"""The processing connector, exposed — HTTP around a decision made elsewhere.

Everything this module knows about photogrammetry it learned from
``s3dgraphy.photogrammetry``: which refusals apply, what a delta looks like, what
the two modes mean. What lives HERE is the three things only a server can do:

1. **stage** — turn the graph's record of a photo cluster into actual bytes, by
   reading each resource's digest out of the room's document and fetching it
   from this node's store;
2. **run it out of band** — a reconstruction is minutes, and an HTTP request
   that waits minutes is a request something between here and the client will
   drop. So the endpoint answers a job id and the work continues on a thread;
3. **land it** — publish the produced bytes into the store and apply the delta
   to the room, as ordinary CRDT operations, so what appears is exactly what
   would have appeared had a person drawn it.

**The declared limit of the job registry.** It is in memory. A restart forgets
that a job existed — and the run itself does NOT stop: it is on the engine, it
has a uuid, and the uuid is in the record and in the log. That is a worse answer
than a durable queue and a much better one than pretending: a job store in the
room's document would put scheduling noise into a scientific record, and a real
queue is a service this node does not have yet. Stated, not hidden.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import posixpath
import threading
import time
import uuid as _uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("stratigraph.photogrammetry")

#: where the engine is. Unset = this node has no engine, and the endpoint says
#: so with a 503 instead of timing out against nothing.
NODEODM_URL = (os.environ.get("NODEODM_URL") or "").strip()

#: how many finished jobs to remember. Small: the record is a courtesy for a
#: client that polled late, not an archive — the ARCHIVE is the graph.
JOB_HISTORY = int(os.environ.get("EM_PHOTOGRAMMETRY_HISTORY", "50"))

#: the ceiling on one run, in seconds. A US is minutes (design §3); an hour is
#: where "slow" becomes "something is wrong".
JOB_DEADLINE = float(os.environ.get("EM_PHOTOGRAMMETRY_DEADLINE", "3600"))


def configured() -> bool:
    return bool(NODEODM_URL)


def describe() -> str:
    return (f"nodeodm at {NODEODM_URL}" if NODEODM_URL
            else "no photogrammetric engine configured (NODEODM_URL unset)")


# ── the job ──────────────────────────────────────────────────────────────────

@dataclass
class Job:
    """One reconstruction, as a client polls it."""

    job_id: str
    room_id: str
    status: str = "queued"          # queued · staging · running · done · failed
    author: Optional[str] = None
    mode: str = "local"
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    #: the engine's own id. Present as soon as the task is accepted, and it is
    #: what somebody uses to find a run this process has forgotten.
    task_uuid: Optional[str] = None
    progress: float = 0.0
    image_count: int = 0
    detail: str = ""
    error: Optional[str] = None
    result: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"job_id": self.job_id, "room_id": self.room_id,
                "status": self.status, "author": self.author, "mode": self.mode,
                "created_at": self.created_at, "finished_at": self.finished_at,
                "task_uuid": self.task_uuid, "progress": self.progress,
                "image_count": self.image_count, "detail": self.detail,
                "error": self.error, "result": dict(self.result)}


class JobRegistry:
    """The jobs this process knows about. Bounded, and honest about it."""

    def __init__(self, history: int = JOB_HISTORY) -> None:
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._lock = threading.Lock()
        self._history = max(1, history)

    def new(self, room_id: str, *, author: Optional[str], mode: str) -> Job:
        job = Job(job_id=_uuid.uuid4().hex, room_id=room_id, author=author,
                  mode=mode)
        with self._lock:
            self._jobs[job.job_id] = job
            self._trim()
        return job

    def trim(self) -> None:
        """Drop the oldest FINISHED jobs past the history. Called on every new
        job; exposed so a caller (and a test) can force it without one."""
        with self._lock:
            self._trim()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, room_id: Optional[str] = None) -> List[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if room_id:
            jobs = [j for j in jobs if j.room_id == room_id]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def _trim(self) -> None:
        # `is not None`, not truthiness: a job that finished at epoch 0 is a job
        # that finished, and the falsy read kept them all for ever (measured).
        finished = [j for j in self._jobs.values() if j.finished_at is not None]
        while len(finished) > self._history:
            oldest = min(finished, key=lambda j: j.finished_at or 0)
            self._jobs.pop(oldest.job_id, None)
            finished.remove(oldest)


JOBS = JobRegistry()


# ── staging: the graph's record of a cluster becomes bytes ───────────────────

class StagingError(LookupError):
    """The cluster could not be turned into photographs. Says which one."""


def stage_cluster(document: Dict[str, Any], cluster_ref: str, *,
                  fetch: Callable[[str], Optional[bytes]],
                  limit: int = 500) -> Tuple[List[Tuple[str, bytes]], List[str], str]:
    """Read a cluster out of a room document and fetch its bytes.

    ``cluster_ref`` is either a ``crmdig:D12`` acquisition (then its members are
    the photographs) or a single resource id (then it is the only one). Both are
    accepted because both are true records — the field assistant's ingest makes
    an acquisition, and a hand-attached photograph does not.

    Returns ``(images, resource_ids, acquisition_id)``. The FILENAMES are the
    resources' names: a control file refers to photographs by name, so renaming
    them here would silently destroy the caller's control — the same refusal the
    engine client makes, honoured at the point the names are chosen.

    Every absence is named. A resource whose bytes are not in this node's store
    is a :class:`StagingError` and not a silently shorter cluster: a
    reconstruction from 8 of the 10 photographs somebody sent is a different
    reconstruction, and nobody asked for it.
    """
    nodes, edges = _live(document)
    node_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    target = node_by_id.get(cluster_ref)
    if target is None:
        raise StagingError(f"no node '{cluster_ref}' in this room")

    kind = str(target.get("node_type") or target.get("type") or "")
    acquisition_id = ""
    if kind in ("dtc_acquisition", "dtc_process"):
        acquisition_id = cluster_ref
        members = [e.get("target") for e in edges
                   if e.get("source") == cluster_ref
                   and e.get("edge_type") == "dtc_had_output"]
    elif kind == "resource":
        members = [cluster_ref]
    else:
        # a stratigraphic unit: the photographs hanging off it
        members = [e.get("target") for e in edges
                   if e.get("source") == cluster_ref
                   and e.get("edge_type") == "has_linked_resource"]
        if not members:
            raise StagingError(
                f"'{cluster_ref}' is a {kind or 'node'} with no linked "
                f"resources: name an acquisition, a resource, or a unit that "
                f"has photographs on it")

    images: List[Tuple[str, bytes]] = []
    resource_ids: List[str] = []
    missing: List[str] = []
    for ref in members:
        node = node_by_id.get(ref)
        if node is None or str(node.get("node_type") or node.get("type")) != "resource":
            continue
        data = node.get("data") or {}
        media = str(data.get("media_type") or "")
        if media and not media.startswith("image/"):
            continue                       # a cluster is photographs, not the PDF beside them
        digest = str(data.get("checksum") or "")
        if not digest:
            missing.append(f"{ref} (no checksum recorded)")
            continue
        blob = fetch(digest)
        if blob is None:
            missing.append(f"{ref} ({digest})")
            continue
        images.append((_filename(node, ref, len(images)), blob))
        resource_ids.append(str(ref))
        if len(images) >= limit:
            break

    if missing:
        raise StagingError(
            f"{len(missing)} photograph(s) of '{cluster_ref}' are not in this "
            f"node's store: " + ", ".join(missing[:4])
            + ("…" if len(missing) > 4 else "")
            + ". A reconstruction from a subset is a different reconstruction.")
    if not images:
        raise StagingError(
            f"'{cluster_ref}' names no photographs this node holds")
    return images, resource_ids, acquisition_id


def _filename(node: Dict[str, Any], ref: str, index: int) -> str:
    raw = str((node.get("data") or {}).get("filename")
              or node.get("name") or ref).strip()
    if "." not in raw.rsplit("/", 1)[-1]:
        raw = f"{raw}.jpg"
    # a name with a slash in it would become a path inside the engine's upload
    return raw.replace("/", "_").replace("\\", "_") or f"image_{index:04d}.jpg"


def _live(document: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Every live node and edge of every graph in the container.

    Through s3Dgraphy's reader, so a tombstoned photograph is not staged: a
    resource somebody deleted must not come back inside a model.
    """
    from s3dgraphy.crdt import live_edges, live_nodes

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for section in (document.get("graphs") or {}).values():
        if not isinstance(section, dict):
            continue
        try:
            nodes.extend(live_nodes(section))
            edges.extend(live_edges(section))
        except Exception:                    # a member that is not a graph
            continue
    return nodes, edges


# ── running it ───────────────────────────────────────────────────────────────

def start(job: Job, *, document: Dict[str, Any], cluster_ref: str,
          subject_id: Optional[str], gcps_payload: Optional[Dict[str, Any]],
          fetch: Callable[[str], Optional[bytes]],
          publish: Callable[[bytes, str], Dict[str, Any]],
          apply_delta: Callable[[Job, Dict[str, Any]], None],
          options: Optional[Dict[str, Any]] = None,
          engine_version: Optional[str] = None,
          client: Any = None) -> Job:
    """Stage, reconstruct, publish, apply — on a daemon thread.

    The refusals that can be made WITHOUT the engine are made here, on the
    request's own thread, so a caller that got a job id knows the job was at
    least possible. Everything after the first engine call is out of band.
    """
    import s3dgraphy.api as em

    from .nodeodm_client import DEFAULT_OPTIONS, ENGINE_NAME, NodeODMClient

    gcps = None
    if gcps_payload:
        # raises on a point that controls nothing — before a job id is handed out
        gcps = em.gcp_set(str(gcps_payload.get("id") or f"gcp.{job.job_id[:8]}"),
                          gcps_payload.get("points") or [],
                          crs=gcps_payload.get("crs"),
                          name=str(gcps_payload.get("name")
                                   or "Ground control points"))
    job.status = "staging"
    images, resource_ids, acquisition_id = stage_cluster(
        document, cluster_ref, fetch=fetch)
    job.image_count = len(images)
    engine = client or NodeODMClient(NODEODM_URL)

    gcp_list = gcps.gcp_list() if gcps is not None else None

    def _work() -> None:
        job.status = "running"
        notes: List[str] = []
        # ── the engine. THIS is the part that knows what NodeODM is ──────────
        try:
            task, archive = engine.run(
                images, options=options, gcp_list=gcp_list, name=cluster_ref,
                poll=5.0, deadline=JOB_DEADLINE,
                on_progress=lambda t: _progress(job, t))
        except Exception as exc:                     # the engine, or the network
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            log.warning("photogrammetry job %s failed: %s", job.job_id, exc)
            return
        job.task_uuid = task.uuid

        found = read_archive(archive)
        notes.extend(found["warnings"])
        if found["model"] is None:
            job.status = "failed"
            job.error = (f"task {task.uuid} finished but the archive holds no "
                         f"model in a form we read "
                         f"({', '.join(MODEL_CANDIDATES)}): the run is on the "
                         f"node, nothing was written to the graph")
            job.finished_at = time.time()
            return

        # ── the bytes, published BEFORE the graph points at them ─────────────
        outputs: List[Dict[str, Any]] = []
        stored = _publish_one(publish, "model", found["model"], outputs)
        if found["point_cloud"] is not None:
            _publish_one(publish, "point_cloud", found["point_cloud"], outputs)
        # the whole archive too: a run you cannot re-open is a run nobody can check
        _publish_one(publish, "archive", ("all.zip", archive), outputs)

        # ── the MEANING, which is s3Dgraphy's and not ours ───────────────────
        rms = rms_from_report(found.get("report"))
        if gcps is not None and rms is None:
            notes.append("registered, but the RMS of the fit could not be read")
        transform = None
        if rms is not None:
            # only when we actually measured something: the builder's own
            # canonical transform is the honest one otherwise
            transform = em.registration_transform(
                crs=gcps.crs if gcps is not None else None, rms=rms,
                name=("Registration (absolute)" if gcps is not None
                      else "Registration (site frame)"))
        run = em.build_photogrammetry_delta(
            input_resources=resource_ids,
            output_model=em.produced_model(
                stored["sha256"], url=stored.get("url"),
                media_type=stored.get("media_type"),
                residency=stored.get("residency") or "resident",
                name=f"{cluster_ref} · {ENGINE_NAME} model"),
            transform=transform, gcp_set=gcps,
            author=job.author, mode=job.mode,
            acquisition=acquisition_id or None, subject=subject_id,
            tool={"name": ENGINE_NAME, "version": engine_version,
                  "task_uuid": task.uuid,
                  "options": {**DEFAULT_OPTIONS, **(options or {})}},
            image_count=len(images), at=_now(), warnings=notes)

        if not run.get("ok"):
            # the semantic refusal, surfaced with the library's own words
            job.status, job.error = "failed", run.get("message")
            job.finished_at = time.time()
            return

        job.detail = run.get("message") or ""
        job.result = {k: run.get(k) for k in
                      ("model_id", "transform_id", "gcp_set_id", "process_id")}
        job.result["outputs"] = outputs
        job.result["warnings"] = run.get("warnings") or []
        try:
            # AFTER the result is on the job: the applier adds to it, and an
            # assignment afterwards would drop what it wrote (measured).
            apply_delta(job, run)
        except Exception as exc:
            # the bytes ARE in the store and the run IS on the engine: say which
            # half landed rather than reporting a clean failure
            job.status = "failed"
            job.error = (f"the model was produced and stored but the graph was "
                         f"not written: {type(exc).__name__}: {exc}")
            job.finished_at = time.time()
            return
        job.progress = 100.0
        job.status = "done"
        job.finished_at = time.time()

    threading.Thread(target=_work, name=f"photogrammetry-{job.job_id[:8]}",
                     daemon=True).start()
    return job


# ── the archive: where THIS engine puts what ─────────────────────────────────
#
# Engine-specific by nature, which is exactly why it is here and not in the
# semantic library: these paths are NodeODM's layout, and a second engine has its
# own. Everything is optional and every absence is REPORTED — a version that moves
# a file must not make this return a model of zero bytes.

#: Ordered: the first match wins, and the order is the preference — glTF is what
#: the ecosystem consumes (design §1.5).
MODEL_CANDIDATES = (
    "odm_texturing/odm_textured_model_geo.glb",
    "odm_texturing/odm_textured_model.glb",
    "odm_texturing/odm_textured_model_geo.obj",
    "odm_texturing/odm_textured_model.obj",
)
POINTCLOUD_CANDIDATES = (
    "odm_georeferencing/odm_georeferenced_model.laz",
    "odm_georeferencing/odm_georeferenced_model.las",
    "odm_filterpoints/point_cloud.ply",
    "odm_georeferencing/odm_georeferenced_model.ply",
)
#: read when present, declared when not — never invented
REPORT_CANDIDATES = (
    "odm_georeferencing/odm_georeferencing_model_geo.txt",
    "odm_report/stats.json",
)

MEDIA_TYPES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
               ".obj": "model/obj", ".laz": "application/vnd.laszip",
               ".las": "application/vnd.las", ".ply": "application/x-ply",
               ".zip": "application/zip"}


def read_archive(blob: bytes) -> Dict[str, Any]:
    """Pull the model, the point cloud and (if legible) the residuals out."""
    out: Dict[str, Any] = {"model": None, "point_cloud": None,
                           "report": None, "warnings": []}
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        out["warnings"].append("the engine's answer is not a zip archive")
        return out
    names = set(archive.namelist())
    for candidate in MODEL_CANDIDATES:
        if candidate in names:
            out["model"] = (posixpath.basename(candidate), archive.read(candidate))
            break
    for candidate in POINTCLOUD_CANDIDATES:
        if candidate in names:
            out["point_cloud"] = (posixpath.basename(candidate),
                                  archive.read(candidate))
            break
    if out["point_cloud"] is None:
        out["warnings"].append(
            "no point cloud in the archive: the model was published, the cloud "
            "was not (looked for " + ", ".join(POINTCLOUD_CANDIDATES) + ")")
    for candidate in REPORT_CANDIDATES:
        if candidate in names:
            try:
                out["report"] = archive.read(candidate).decode("utf-8", "replace")
            except OSError:                       # a truncated member
                continue
            break
    if out["report"] is None:
        out["warnings"].append(
            "no georeferencing report in the archive: the transform carries the "
            "CRS the caller stated and NO residuals — the fit's error is unknown, "
            "not zero")
    return out


def rms_from_report(report: Optional[str]) -> Optional[float]:
    """The one shape we read: a line naming an RMS and a number on it.

    Deliberately narrow. Guessing at a format across engine versions is how a
    number that means something else ends up in a record somebody cites.
    """
    if not report:
        return None
    for line in report.splitlines():
        if "rms" not in line.lower():
            continue
        for token in line.replace(",", " ").replace("=", " ").split():
            try:
                return float(token)
            except ValueError:
                continue
    return None


def _publish_one(publish: Callable[[bytes, str], Dict[str, Any]], role: str,
                 item: Tuple[str, bytes],
                 outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bytes into the store, and the record of where they went.

    The store speaks `ref`; the semantic layer speaks `sha256` and means the FULL
    reference (the thing a client verifies against). Translated once, here.
    """
    filename, blob = item
    media_type = MEDIA_TYPES.get(posixpath.splitext(filename)[1].lower(),
                                 "application/octet-stream")
    stored = publish(blob, media_type) or {}
    ref = stored.get("ref") or stored.get("sha256") or (
        "sha256:" + hashlib.sha256(blob).hexdigest())
    record = {"sha256": ref, "url": f"asset/{ref}", "media_type": media_type,
              "residency": "resident", "created": stored.get("created")}
    outputs.append({"role": role, "filename": filename, "bytes": len(blob),
                    **{k: v for k, v in record.items()
                       if k in ("sha256", "url", "media_type", "created",
                                "residency")}})
    return record


# ── the connector, DECLARED ──────────────────────────────────────────────────
#
# The descriptor moved here with the driver it names. s3Dgraphy keeps the generic
# contract (`contract/`) and the vocabulary; naming an engine is a fact about THIS
# node, and a node that runs COLMAP instead declares a different one.
#
# No new capability was invented: the set in `contract.connector` is closed on
# purpose, and this connector needs three that already exist — it reads the graph
# to find the cluster, writes the provenance, and attaches the bytes it produced.

def photogrammetry_descriptor(handler=None, *, version: Optional[str] = None):
    """The connector, as a promise. Declaring it buys the core's four refusals —
    and the one that matters for a machine act is the third: a write with no
    author is refused."""
    from s3dgraphy.contract.connector import ConnectorDescriptor
    from s3dgraphy.contract.core import Slot

    from .nodeodm_client import ENGINE_NAME

    return ConnectorDescriptor(
        name="photogrammetry",
        intents=["photogrammetry", "build-model", "reconstruct",
                 "costruisci il modello"],
        input_schema=[
            Slot("cluster", "string", True,
                 "the acquisition (or the resources) the photographs arrived as"),
            Slot("subject", "string", False,
                 "what is being reconstructed — a stratigraphic unit, usually"),
            Slot("mode", "string", False,
                 "'local' (scaled, site frame) or 'absolute' (registered by GCPs)"),
            Slot("gcps", "object", False,
                 "the control set: points, observations, CRS"),
        ],
        output="graph-delta",
        handler=handler,
        description=(f"A cluster of photographs becomes a placed 3D model: "
                     f"{ENGINE_NAME} reconstructs it, the graph records the "
                     f"genesis (crmdig:D7), the placement and, when there are "
                     f"ground control points, the evidence it was registered from."),
        service="rest",
        writes=True,
        host="app-side",
        transport=["direct", "lan"],
        capabilities=["read-graph", "write-graph", "attach-asset"],
        provenance="derivation",
        vendor={"engine": ENGINE_NAME, "engine_version": version},
    )


def _progress(job: Job, task: Any) -> None:
    job.task_uuid = getattr(task, "uuid", None) or job.task_uuid
    job.progress = float(getattr(task, "progress", 0.0) or 0.0)
    job.detail = getattr(task, "status_name", "") or job.detail


def _now() -> str:
    from s3dgraphy.editorial import now_iso
    return now_iso()


def delta_to_ops(delta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The delta as the CRDT operations the room already speaks.

    Not a second write path: these are the same ``add_node`` / ``add_edge``
    operations EMStudio and the field assistant send, so a model that appears
    this way is indistinguishable from one somebody drew — including for the
    merge, the tombstones and the field clocks.
    """
    ops: List[Dict[str, Any]] = []
    nodes = list(delta.get("nodes") or [])
    process = delta.get("process")
    if process:
        nodes.append(process)
    for node in nodes:
        ops.append({"op": "add_node", "id": node["id"], "node": node})
    for edge in delta.get("edges") or []:
        ops.append({"op": "add_edge", "id": edge.get("id"),
                    "source": edge["source"], "target": edge["target"],
                    "edge_type": edge.get("edge_type")})
    return ops
