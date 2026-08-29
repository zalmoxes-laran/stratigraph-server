"""The NodeODM driver — the conversation, measured without the container.

These tests moved here with the driver (2026-08-29): they were in s3Dgraphy while
the driver was, and they left when it did. What they measure is the part that is
this node's and no library's — three endpoints and only three, the multipart body,
the bounded poll, and the refusals that happen before an hour of CPU is spent.

The one thing they cannot prove is stated in the report: that a real
`opendronemap/nodeodm:3.5.6` answers those three endpoints this way. That is what
`dev-stack/smoke_photogrammetry.py` is for, and it has been run.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.nodeodm_client import (STATUS_COMPLETED, STATUS_FAILED,  # noqa: E402
                                STATUS_RUNNING, NodeODMClient, NodeODMError,
                                NodeODMTimeout, odm_options)
from app.photogrammetry import read_archive, rms_from_report  # noqa: E402

IMAGES = [(f"IMG_{n:04d}.JPG", f"bytes-{n}".encode()) for n in range(1, 11)]


def _archive(*, model=b"GLB-BYTES", cloud=b"LAZ-BYTES", report=None,
             model_name="odm_texturing/odm_textured_model_geo.glb") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if model is not None:
            zf.writestr(model_name, model)
        if cloud is not None:
            zf.writestr("odm_georeferencing/odm_georeferenced_model.laz", cloud)
        if report is not None:
            zf.writestr("odm_georeferencing/odm_georeferencing_model_geo.txt",
                        report)
    return buffer.getvalue()


class FakeEngine:
    """The three endpoints, in memory. Records what it was asked."""

    def __init__(self, *, statuses=None, archive=None, fail_first=0):
        self.calls = []
        self.bodies = []
        self.statuses = list(statuses or [STATUS_COMPLETED])
        self.archive = archive if archive is not None else _archive()
        self.fail_first = fail_first
        self._attempts = 0

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append((method, url.split("?")[0]))
        if self.fail_first:
            self._attempts += 1
            if self._attempts <= self.fail_first:
                raise NodeODMError("connection refused (staged)")
        if method == "POST" and url.endswith("/task/new"):
            self.bodies.append(body)
            return 200, json.dumps({"uuid": "task-1"}).encode()
        if "/info" in url:
            code = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            payload = {"uuid": "task-1", "progress": 100.0, "status": {"code": code}}
            if code == STATUS_FAILED:
                payload["status"]["errorMessage"] = "not enough overlap"
            return 200, json.dumps(payload).encode()
        if "/download/all.zip" in url:
            return 200, self.archive
        return 404, b'{"error": "no such route"}'


def _client(engine, **kwargs):
    kwargs.setdefault("sleep", lambda _s: None)
    return NodeODMClient("http://engine:3000", transport=engine, **kwargs)


# ── 1 · three endpoints, and only those ──────────────────────────────────────

def test_the_client_creates_polls_and_downloads():
    engine = FakeEngine(statuses=[STATUS_RUNNING, STATUS_RUNNING, STATUS_COMPLETED])
    task, blob = _client(engine).run(IMAGES, poll=0.0)
    assert task.ok and task.uuid == "task-1"
    assert blob == engine.archive
    routes = [url for _m, url in engine.calls]
    assert routes[0].endswith("/task/new")
    assert routes.count("http://engine:3000/task/task-1/info") == 3
    assert routes[-1].endswith("/task/task-1/download/all.zip")
    # nothing else was called: a surface we do not use cannot break us
    assert {r.rsplit("/", 1)[-1] for r in routes} == {"new", "info", "all.zip"}


def test_the_cluster_and_the_control_file_go_up_together():
    engine = FakeEngine()
    gcp_list = "EPSG:32633\n1 2 3 10 20 IMG_0001.JPG gcp01\n"
    _client(engine).run(IMAGES, gcp_list=gcp_list, poll=0.0)
    body = engine.bodies[0]
    assert b'filename="IMG_0001.JPG"' in body
    assert b'filename="gcp_list.txt"' in body
    assert b"EPSG:32633" in body
    # the options travel as ODM's [{name, value}] and carry the glTF ask
    assert b'"name": "gltf"' in body


def test_the_defaults_are_close_range_not_aerial():
    options = {o["name"]: o["value"] for o in odm_options({})}
    assert options["feature-quality"] == "high"
    assert options["gltf"] is True
    # and a caller's override wins over the default, without losing the rest
    overridden = {o["name"]: o["value"] for o in odm_options({"pc-quality": "ultra"})}
    assert overridden["pc-quality"] == "ultra"
    assert overridden["gltf"] is True


def test_a_control_file_naming_an_absent_photograph_is_refused_before_the_upload():
    engine = FakeEngine()
    stray = "EPSG:32633\n1 2 3 10 20 NOT_IN_THE_CLUSTER.JPG gcp01\n"
    with pytest.raises(NodeODMError) as exc:
        _client(engine).create_task(IMAGES, gcp_list=stray)
    assert "not in this cluster" in str(exc.value)
    assert engine.calls == []          # nothing was sent


def test_an_empty_cluster_never_reaches_the_engine():
    engine = FakeEngine()
    with pytest.raises(NodeODMError) as exc:
        _client(engine).create_task([])
    assert "zero photographs" in str(exc.value)
    assert engine.calls == []


def test_a_failed_task_names_what_the_engine_said():
    engine = FakeEngine(statuses=[STATUS_FAILED])
    with pytest.raises(NodeODMError) as exc:
        _client(engine).run(IMAGES, poll=0.0)
    assert "not enough overlap" in str(exc.value)


def test_the_deadline_is_its_own_exception_because_the_task_is_still_alive():
    engine = FakeEngine(statuses=[STATUS_RUNNING])
    with pytest.raises(NodeODMTimeout) as exc:
        _client(engine).wait("task-1", poll=0.0, deadline=-1.0)
    assert "task-1" in str(exc.value)
    assert "poll it again" in str(exc.value)


def test_the_retry_is_finite():
    engine = FakeEngine(fail_first=2)
    assert _client(engine, retries=2).create_task(IMAGES).uuid == "task-1"
    with pytest.raises(NodeODMError):
        _client(FakeEngine(fail_first=5), retries=2).create_task(IMAGES)


def test_a_non_json_answer_is_a_sentence_not_a_traceback():
    def rubbish(method, url, body, headers, timeout):
        return 200, b"<html>gateway</html>"

    with pytest.raises(NodeODMError) as exc:
        _client(rubbish).create_task(IMAGES)
    assert "not JSON" in str(exc.value)


# ── 2 · the archive: this engine's layout, read here ─────────────────────────

def test_the_model_and_the_cloud_come_out_of_the_archive():
    found = read_archive(_archive())
    assert found["model"] == ("odm_textured_model_geo.glb", b"GLB-BYTES")
    assert found["point_cloud"][0] == "odm_georeferenced_model.laz"


def test_a_missing_point_cloud_is_declared_not_faked():
    found = read_archive(_archive(cloud=None))
    assert found["point_cloud"] is None
    assert any("no point cloud" in w for w in found["warnings"])


def test_a_model_in_no_form_we_read_is_reported_as_absent():
    found = read_archive(_archive(model=None))
    assert found["model"] is None


def test_something_that_is_not_a_zip_is_said_rather_than_raised():
    found = read_archive(b"not a zip at all")
    assert found["model"] is None
    assert any("not a zip" in w for w in found["warnings"])


def test_the_rms_is_read_only_in_the_one_shape_we_know():
    assert rms_from_report("Average RMS error: 0.021 m") == pytest.approx(0.021)
    # …and left unknown otherwise, because a number nobody measured is worse
    # than none
    assert rms_from_report("everything went fine") is None
    assert rms_from_report(None) is None


def test_an_archive_with_no_report_says_the_error_is_unknown_not_zero():
    found = read_archive(_archive(report=None))
    assert found["report"] is None
    assert any("unknown, not zero" in w for w in found["warnings"])


# ── 3 · the connector descriptor came with the driver ────────────────────────

def test_the_descriptor_names_the_engine_and_declares_no_new_capability():
    from app.photogrammetry import photogrammetry_descriptor

    descriptor = photogrammetry_descriptor(version="3.5.6")
    assert descriptor.name == "photogrammetry"
    assert descriptor.writes is True
    assert descriptor.capabilities == ["read-graph", "write-graph", "attach-asset"]
    assert descriptor.vendor == {"engine": "NodeODM", "engine_version": "3.5.6"}
    assert "NodeODM" in descriptor.description
