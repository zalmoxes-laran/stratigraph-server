"""`POST /v1/photogrammetry` — the connector's HTTP face, with a fake engine.

What is measured here is the SERVER's half and only that: the gate, the staging,
the job's lifecycle, and that what lands in the room is ordinary CRDT operations.
The engine is faked — the reconstruction logic has its own suite in s3Dgraphy,
and a test that needs a 2 GB container running is a test that stops being run.

The one thing this cannot prove is stated in the end-of report: that a REAL
NodeODM 3.5.6 answers those three endpoints the way the fake does.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys
import time
import zipfile

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient  # noqa: E402

from app import photogrammetry as pg  # noqa: E402
from app.assets import ASSET_STORE  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def live_loop():
    """One event loop for the whole test, the way a server has one per process.

    MEASURED, and it is a fact about the harness rather than about the code: a
    bare `TestClient` runs EACH request on its own loop and closes it afterwards
    (two calls, two loop ids); entered as a context manager it keeps one for the
    block. Since 9 September the photogrammetry thread hands its delta back to
    the loop that started the job — which is right for uvicorn, where there is
    one loop for the process, and impossible against a loop that was closed when
    the POST returned.

    Without this fixture the job failed with the sentence it is supposed to give
    when the graph really cannot be written («the model was produced and stored
    but the graph was not written: RuntimeError: Event loop is closed») — an
    honest report of a harness artefact, which is the worst kind of red: correct
    code, correct message, wrong conclusion.
    """
    with client:
        yield


# ── a room with a photo cluster in it ────────────────────────────────────────

def _photo(name: str, blob: bytes) -> dict:
    info = ASSET_STORE.put(blob, "image/jpeg")
    return {"id": f"res.{name}", "node_type": "resource", "name": name,
            "data": {"checksum": info["ref"], "media_type": "image/jpeg",
                     "url": f"asset/{info['ref']}"}}


def _document(count: int = 4) -> dict:
    nodes = [{"id": "US12", "node_type": "US", "name": "US 12"},
             {"id": "acq.march", "node_type": "dtc_acquisition",
              "name": "March cluster", "data": {"dtc_kind": "ingest"}}]
    edges = []
    for index in range(count):
        name = f"IMG_{index:04d}.JPG"
        nodes.append(_photo(name, f"pixels-{index}".encode()))
        edges.append({"id": f"acq__out__{index}", "source": "acq.march",
                      "target": f"res.{name}", "edge_type": "dtc_had_output"})
        edges.append({"id": f"us__link__{index}", "source": "US12",
                      "target": f"res.{name}", "edge_type": "has_linked_resource"})
    return {"graphs": {"main": {"nodes": nodes, "edges": edges,
                                "name": "test", "id": "main"}},
            "active_graph_id": "main"}


def _archive(model=b"GLB", cloud=b"LAZ") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if model is not None:
            zf.writestr("odm_texturing/odm_textured_model_geo.glb", model)
        if cloud is not None:
            zf.writestr("odm_georeferencing/odm_georeferenced_model.laz", cloud)
    return buffer.getvalue()


class FakeEngineTransport:
    def __init__(self, archive=None):
        self.archive = archive if archive is not None else _archive()

    def __call__(self, method, url, body, headers, timeout):
        if url.split("?")[0].endswith("/task/new"):
            return 200, json.dumps({"uuid": "task-http"}).encode()
        if "/info" in url:
            return 200, json.dumps({"uuid": "task-http", "progress": 100.0,
                                    "status": {"code": 40}}).encode()
        if "all.zip" in url:
            return 200, self.archive
        return 404, b'{"error":"no"}'


@pytest.fixture
def engine(monkeypatch):
    """A configured node whose engine lives in this process."""
    from app.nodeodm_client import NodeODMClient

    monkeypatch.setattr(pg, "NODEODM_URL", "http://nodeodm:3000")
    made = NodeODMClient("http://nodeodm:3000",
                         transport=FakeEngineTransport(), sleep=lambda _s: None)
    real_start = pg.start

    def start(job, **kwargs):
        kwargs["client"] = made
        return real_start(job, **kwargs)

    monkeypatch.setattr(pg, "start", start)
    return made


@pytest.fixture
def room(monkeypatch):
    """A live room holding the cluster, without touching a snapshot store."""
    from app import main as m
    from app.rooms import Room

    the_room = Room("photogrammetry-room", _document())
    original = m.rooms

    class Registry:
        async def get(self, room_id):
            return the_room

        def rooms(self):
            return {the_room.room_id: the_room}

    monkeypatch.setattr(m, "rooms", lambda: Registry())
    monkeypatch.setattr(m, "load_acl", lambda room_id: original().__class__ and
                        __import__("app.access", fromlist=["Acl"]).Acl())
    yield the_room


def _await(job_id, timeout=5.0):
    """Poll the job endpoint until it is terminal. The thread is real."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        answer = client.get(f"/v1/photogrammetry/{job_id}")
        assert answer.status_code == 200, answer.text
        body = answer.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished: {body}")


# ── 1 · the ordinary run ─────────────────────────────────────────────────────

def test_a_local_run_lands_a_model_a_genesis_and_a_placement(engine, room):
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march",
                               "subject": "US12", "mode": "local"})
    assert answer.status_code == 202, answer.text
    job = answer.json()
    assert job["status"] in ("queued", "staging", "running")
    assert job["image_count"] == 4

    done = _await(job["job_id"])
    assert done["status"] == "done", done
    assert done["task_uuid"] == "task-http"
    assert done["result"]["ops_applied"] == done["result"]["ops"]

    section = room.document["graphs"]["main"]
    by_id = {n["id"]: n for n in section["nodes"]}
    model_id = done["result"]["model_id"]
    assert by_id[model_id]["node_type"] == "resource"
    assert by_id[model_id]["data"]["dtc_kind"] == "mesh"
    assert by_id[done["result"]["transform_id"]]["node_type"] == "registration_transform"
    assert by_id[done["result"]["process_id"]]["node_type"] == "dtc_process"

    kinds = {(e["source"], e["edge_type"], e["target"]) for e in section["edges"]}
    assert (model_id, "has_registration_transform", done["result"]["transform_id"]) in kinds
    assert (done["result"]["process_id"], "dtc_had_input", "acq.march") in kinds
    assert ("US12", "has_linked_resource", model_id) in kinds


def test_the_produced_bytes_are_in_this_nodes_store_addressed_by_digest(engine, room):
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march"})
    done = _await(answer.json()["job_id"])
    roles = {o["role"]: o for o in done["result"]["outputs"]}
    assert set(roles) == {"model", "point_cloud", "archive"}
    for output in roles.values():
        assert ASSET_STORE.get(output["sha256"]) is not None
    # …and the digest is verifiable: the reference IS the content
    assert roles["model"]["sha256"] == "sha256:" + hashlib.sha256(b"GLB").hexdigest()


def test_an_absolute_run_records_the_control_set_it_was_solved_from(engine, room):
    gcps = {"crs": "EPSG:32633", "id": "gcp.site",
            "points": [{"id": f"gcp{n}", "world": [n, n, 0.0],
                        "observations": [{"image": f"IMG_{n:04d}.JPG",
                                          "pixel": [10.0, 20.0]}]}
                       for n in range(3)]}
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march",
                               "mode": "absolute", "gcps": gcps})
    assert answer.status_code == 202, answer.text
    done = _await(answer.json()["job_id"])
    assert done["status"] == "done", done
    section = room.document["graphs"]["main"]
    by_id = {n["id"]: n for n in section["nodes"]}
    transform = by_id[done["result"]["transform_id"]]
    assert transform["data"]["crs"] == "EPSG:32633"
    assert by_id[done["result"]["gcp_set_id"]]["node_type"] == "gcp_set"
    kinds = {(e["source"], e["edge_type"], e["target"]) for e in section["edges"]}
    assert (done["result"]["transform_id"], "has_gcp_set",
            done["result"]["gcp_set_id"]) in kinds


# ── 2 · the refusals happen BEFORE a job id ──────────────────────────────────

def test_no_engine_is_503_not_a_timeout(room, monkeypatch):
    monkeypatch.setattr(pg, "NODEODM_URL", "")
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march"})
    assert answer.status_code == 503
    assert "NODEODM_URL" in answer.json()["detail"]


def test_a_cluster_this_node_cannot_fully_stage_is_404(engine, room):
    section = room.document["graphs"]["main"]
    section["nodes"].append({"id": "res.GHOST.JPG", "node_type": "resource",
                             "name": "GHOST.JPG",
                             "data": {"checksum": "sha256:" + "0" * 64,
                                      "media_type": "image/jpeg"}})
    section["edges"].append({"id": "acq__out__ghost", "source": "acq.march",
                             "target": "res.GHOST.JPG",
                             "edge_type": "dtc_had_output"})
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march"})
    assert answer.status_code == 404
    detail = answer.json()["detail"]
    assert "not in this node's store" in detail
    assert "a different reconstruction" in detail


def test_an_unknown_cluster_is_404(engine, room):
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "nope"})
    assert answer.status_code == 404
    assert "no node 'nope'" in answer.json()["detail"]


def test_a_control_point_that_controls_nothing_is_400(engine, room):
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march",
                               "mode": "absolute",
                               "gcps": {"crs": "EPSG:32633",
                                        "points": [{"id": "a", "world": []}]}})
    assert answer.status_code == 400
    assert "world coordinate" in answer.json()["detail"]


def test_absolute_without_control_points_fails_the_job_not_the_request(engine, room):
    """s3Dgraphy's refusal, surfaced on the job — the engine is never called."""
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march",
                               "mode": "absolute"})
    assert answer.status_code == 202
    done = _await(answer.json()["job_id"])
    assert done["status"] == "failed"
    assert "needs ground control points" in done["error"]
    # and nothing was written
    assert not any(n["node_type"] == "registration_transform"
                   for n in room.document["graphs"]["main"]["nodes"])


def test_an_archive_with_no_model_writes_nothing_and_keeps_the_uuid(room, monkeypatch):
    from app.nodeodm_client import NodeODMClient

    monkeypatch.setattr(pg, "NODEODM_URL", "http://nodeodm:3000")
    empty = NodeODMClient("http://nodeodm:3000",
                          transport=FakeEngineTransport(_archive(model=None)),
                          sleep=lambda _s: None)
    real_start = pg.start
    monkeypatch.setattr(pg, "start",
                        lambda job, **kw: real_start(job, **{**kw, "client": empty}))
    before = len(room.document["graphs"]["main"]["nodes"])
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": room.room_id, "cluster": "acq.march"})
    done = _await(answer.json()["job_id"])
    assert done["status"] == "failed"
    assert "holds no model" in done["error"]
    assert done["task_uuid"] == "task-http"       # the run is still findable
    assert len(room.document["graphs"]["main"]["nodes"]) == before


# ── 3 · the job registry, and its declared limit ─────────────────────────────

def test_a_forgotten_job_is_404_and_says_the_run_may_be_alive():
    answer = client.get("/v1/photogrammetry/" + "f" * 32)
    assert answer.status_code == 404
    assert "still be on the engine" in answer.json()["detail"]


def test_the_registry_is_bounded():
    registry = pg.JobRegistry(history=3)
    for index in range(6):
        job = registry.new("r", author="x", mode="local")
        job.finished_at = float(index)       # 0.0 is a real instant, not "unfinished"
    registry.trim()
    assert len(registry.list()) == 3


# ── 4 · what lands is ORDINARY operations, not a second write path ───────────

def test_the_delta_becomes_add_node_and_add_edge_only():
    ops = pg.delta_to_ops({
        "nodes": [{"id": "a", "node_type": "resource"}],
        "edges": [{"id": "e", "source": "a", "target": "b",
                   "edge_type": "has_registration_transform"}],
        "process": {"id": "p", "node_type": "dtc_process"}})
    assert {o["op"] for o in ops} == {"add_node", "add_edge"}
    assert [o["id"] for o in ops if o["op"] == "add_node"] == ["a", "p"]


# ── 5 · staging reads the graph the way every other reader does ──────────────

def test_a_removed_photograph_is_not_staged():
    document = _document(3)
    section = document["graphs"]["main"]
    for node in section["nodes"]:
        if node["id"] == "res.IMG_0001.JPG":
            # the real tombstone: a clock under `data.removed` (s3dgraphy.crdt)
            node.setdefault("data", {})["removed"] = {"ts": "2026-08-29T09:00:00Z",
                                                      "by": "0000-0001-5109-3700"}
    images, ids, acq = pg.stage_cluster(document, "acq.march",
                                        fetch=ASSET_STORE.get)
    assert len(images) == 2
    assert "res.IMG_0001.JPG" not in ids
    assert acq == "acq.march"


def test_a_unit_with_photographs_on_it_is_a_cluster_too():
    images, _ids, acq = pg.stage_cluster(_document(2), "US12",
                                         fetch=ASSET_STORE.get)
    assert len(images) == 2
    assert acq == ""            # no acquisition: the inputs will be the files


def test_the_filenames_are_the_resources_names_because_the_control_file_uses_them():
    images, _ids, _acq = pg.stage_cluster(_document(2), "acq.march",
                                          fetch=ASSET_STORE.get)
    assert [name for name, _b in images] == ["IMG_0000.JPG", "IMG_0001.JPG"]


# ── 6 · THE SECOND DOOR WRITES INTO THE GRAPH AND MUST KEEP IT ───────────────
#
# The fixtures above give the room «without touching a snapshot store», which is
# exactly why nothing here ever measured the half that was missing: the ops land
# in `room.document` (memory) and the test looks there.
#
# These two hold the other half. Written BEFORE the repair, so the red is ours.

@pytest.fixture
def stored_room(monkeypatch):
    """The same live room, but with a REAL snapshot store behind it — which is
    what a deployed node has, and what the fixture above deliberately omits."""
    from app import main as m
    from app import ws as ws_mod
    from app.rooms import Room
    from app.store import InMemorySnapshotStore

    store = InMemorySnapshotStore()
    the_room = Room("kept-room", _document())

    class Registry:
        async def get(self, room_id):
            return the_room

        def rooms(self):
            return {the_room.room_id: the_room}

    monkeypatch.setattr(m, "rooms", lambda: Registry())
    monkeypatch.setattr(m, "load_acl",
                        lambda room_id: __import__("app.access",
                                                   fromlist=["Acl"]).Acl())
    monkeypatch.setattr(ws_mod, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(m, "snapshot_store", lambda: store)
    yield the_room, store


def test_a_finished_run_is_ON_DISK_and_not_only_in_memory(engine, stored_room):
    """«`applied: true` does not mean KEPT» — found on 6 September from the other
    door, and true here too until it was repaired. A job that says `done` with
    `ops_applied: N` while the store holds nothing is the failure that looks like
    a success, with a number confirming it."""
    the_room, store = stored_room
    answer = client.post("/v1/photogrammetry",
                         json={"room_id": the_room.room_id,
                               "cluster": "acq.march", "mode": "local"})
    assert answer.status_code == 202, answer.text
    done = _await(answer.json()["job_id"])
    assert done["status"] == "done", done
    model_id = done["result"]["model_id"]

    kept = store.get(the_room.room_id)
    assert kept is not None, (
        "the run finished and the snapshot store holds NOTHING for this room: "
        "the model, its genesis event and its placement live in a dictionary "
        "that dies with the process")
    ids = {n["id"] for section in (kept.get("graphs") or {}).values()
           for n in section.get("nodes", [])}
    assert model_id in ids, (
        f"the store has a document for this room but not the model {model_id!r} "
        "the job says it applied")


def test_a_finished_run_was_ANNOUNCED_to_the_room(engine, stored_room):
    """Whoever is in EMStudio in that room must see the model appear, without
    reopening — and without a way to know they should. So the operations go out
    on the wire like every other operation, from the place that already does it
    for the socket."""
    the_room, _store = stored_room
    seen = []

    async def spy(room, message, skip=None):
        seen.append(message)

    from app import ws as ws_mod
    original = ws_mod._fanout
    ws_mod._fanout = spy
    try:
        answer = client.post("/v1/photogrammetry",
                             json={"room_id": the_room.room_id,
                                   "cluster": "acq.march", "mode": "local"})
        done = _await(answer.json()["job_id"])
    finally:
        ws_mod._fanout = original
    assert done["status"] == "done", done

    ops = [m for m in seen if m.get("type") == "op"]
    assert ops, ("the run applied operations and announced none: anybody in the "
                 "room has to reopen it, and has no way to know they should")
    announced = {(m.get("payload") or {}).get("id") for m in ops}
    assert done["result"]["model_id"] in announced
