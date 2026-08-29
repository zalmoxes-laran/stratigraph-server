"""A driver for NodeODM — the engine, spoken to over its REST surface.

**Why this lives here and not in s3Dgraphy.** s3Dgraphy is the semantic library:
Extended Matrix, the property graph, the CIDOC mapping. Driving a REST API,
polling a task queue and unzipping an archive are none of those things — they are
node-side plumbing, and the node is this service. What the library owns is what
the act MEANS (`s3dgraphy.api.build_photogrammetry_delta`), and this module calls
it with the result. A second engine — COLMAP, MicMac, Aïoli — is a second file
beside this one, calling the same builder.

**Pinned, deliberately.** Three endpoints and no more:

===========================  ============================================
``POST /task/new``           images + options + ``gcp_list.txt`` → a uuid
``GET  /task/{uuid}/info``   status and progress, polled
``GET  /task/{uuid}/download/all.zip``  everything the run produced
===========================  ============================================

NodeODM has a larger API (``/task/new/init`` chunked uploads, ``/task/cancel``,
``/options``). Those are not used, and the reason is the same reason the image tag
is pinned: a surface we do not call is a surface that cannot break us. The one
place a fourth endpoint would earn itself is named in ``create_task``.

**Bounded, everywhere.** A field node is not a laptop somebody is watching: a run
that hangs must end in a refusal with a sentence, not in a thread nobody is
waiting on. Every call has a timeout, the poll has a deadline, and the retry count
is finite and small — a network that has failed three times is not a network that
succeeds on the tenth.

The transport is injectable, so the suite measures the whole conversation without
a 2.6 GB container: `tests/test_photogrammetry_endpoint.py`.
"""

from __future__ import annotations

import io
import json
import os
import time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: Where the engine lives. An ENDPOINT, not a host: today the `nodeodm` service
#: beside us, tomorrow a ClusterODM broker in front of a CPU worker and a GPU one
#: — the design's §7-bis routing — and NOTHING here changes for that, because a
#: broker speaks the same three endpoints. The routing itself is deliberately NOT
#: implemented (declared out of scope).
DEFAULT_NODEODM_URL = "http://nodeodm:3000"

#: the engine, named the way the DTC will record it. The ONLY place this string
#: exists: it travels to the graph as `tool.name`, through the agnostic builder.
ENGINE_NAME = "NodeODM"

#: statuses NodeODM reports. 10=queued 20=running 30=failed 40=completed 50=canceled
STATUS_QUEUED, STATUS_RUNNING, STATUS_FAILED, STATUS_COMPLETED, STATUS_CANCELED = (
    10, 20, 30, 40, 50)

_TERMINAL = (STATUS_FAILED, STATUS_COMPLETED, STATUS_CANCELED)

STATUS_NAMES = {STATUS_QUEUED: "queued", STATUS_RUNNING: "running",
                STATUS_FAILED: "failed", STATUS_COMPLETED: "completed",
                STATUS_CANCELED: "canceled"}


class NodeODMError(RuntimeError):
    """The engine could not be reached, or said no. Carries what it said."""


class NodeODMTimeout(NodeODMError):
    """The deadline passed with the task still running. NOT a failure of the
    task — it may still finish — which is why it is its own exception: a caller
    can come back and poll the same uuid."""


@dataclass
class Task:
    """A run, as the engine sees it."""

    uuid: str
    status: int = STATUS_QUEUED
    progress: float = 0.0
    #: whatever the engine put in `processingTime`, `output`, `error`…
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in _TERMINAL

    @property
    def ok(self) -> bool:
        return self.status == STATUS_COMPLETED

    @property
    def status_name(self) -> str:
        return STATUS_NAMES.get(self.status, f"unknown({self.status})")


# ── the transport seam ───────────────────────────────────────────────────────
#
# One function, so a test does not have to fake a socket and this module does not
# have to grow a dependency. `(method, url, body, headers, timeout) -> (status,
# bytes)`. The default implementation is stdlib urllib — the same choice the
# Blender room client made, and for the same reason: an addon cannot pip-install.

Transport = Callable[[str, str, Optional[bytes], Dict[str, str], float],
                     Tuple[int, bytes]]


def urllib_transport(method: str, url: str, body: Optional[bytes],
                     headers: Dict[str, str], timeout: float) -> Tuple[int, bytes]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, method=method, data=body,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as exc:              # an answer, just not 2xx
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise NodeODMError(f"cannot reach the engine at {url}: {exc}") from exc


def _multipart(fields: Dict[str, str],
               files: Sequence[Tuple[str, str, bytes]]) -> Tuple[bytes, str]:
    """Build a multipart/form-data body. Hand-rolled and it earns itself: the
    alternative is a dependency, and this is thirty lines that never change."""
    boundary = f"----stratigraph{_uuid.uuid4().hex}"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(f"{value}\r\n".encode())
    for field_name, filename, blob in files:
        out.write(f"--{boundary}\r\n".encode())
        out.write((f'Content-Disposition: form-data; name="{field_name}"; '
                   f'filename="{filename}"\r\n').encode())
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(blob)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class NodeODMClient:
    """The three endpoints, bounded, with the engine's answers turned into
    sentences a caller can act on.

    ``token`` is NodeODM's own optional query token — not an OIDC bearer. It is
    sent as a query parameter because that is what NodeODM reads; a node that
    sets one should not also expose the engine's port outside its compose
    network, and the deployment does not.
    """

    def __init__(self, base_url: Optional[str] = None, *,
                 token: Optional[str] = None,
                 transport: Optional[Transport] = None,
                 timeout: float = 60.0,
                 retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.base_url = (base_url or os.environ.get("NODEODM_URL")
                         or DEFAULT_NODEODM_URL).rstrip("/")
        self.token = token or os.environ.get("NODEODM_TOKEN") or None
        self.transport: Transport = transport or urllib_transport
        self.timeout = float(timeout)
        #: total ATTEMPTS is retries + 1, and it is small on purpose
        self.retries = max(0, int(retries))
        self._sleep = sleep

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        if self.token:
            url += ("&" if "?" in url else "?") + f"token={self.token}"
        return url

    def _call(self, method: str, path: str, *, body: Optional[bytes] = None,
              headers: Optional[Dict[str, str]] = None,
              timeout: Optional[float] = None) -> Tuple[int, bytes]:
        url = self._url(path)
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                return self.transport(method, url, body, dict(headers or {}),
                                      timeout if timeout is not None else self.timeout)
            except NodeODMError as exc:
                last = exc
                if attempt < self.retries:
                    # linear, not exponential: the deadline above is the real
                    # bound, and a doubling backoff would blow past it silently
                    self._sleep(1.0 + attempt)
        raise NodeODMError(str(last))

    @staticmethod
    def _json(status: int, blob: bytes, what: str) -> Dict[str, Any]:
        try:
            payload = json.loads(blob.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise NodeODMError(
                f"{what}: the engine answered {status} with something that is "
                f"not JSON ({blob[:120]!r})") from None
        if status >= 400 or (isinstance(payload, dict) and payload.get("error")):
            detail = (payload.get("error") if isinstance(payload, dict)
                      else None) or f"HTTP {status}"
            raise NodeODMError(f"{what}: {detail}")
        return payload if isinstance(payload, dict) else {"value": payload}

    # ── the three endpoints ──────────────────────────────────────────────────

    def create_task(self, images: Sequence[Tuple[str, bytes]], *,
                    options: Optional[Dict[str, Any]] = None,
                    gcp_list: Optional[str] = None,
                    name: Optional[str] = None) -> Task:
        """POST /task/new — the cluster goes up, a uuid comes back.

        ``images`` is ``[(filename, bytes), …]``. The filenames MATTER and are
        not cosmetic: a ``gcp_list.txt`` refers to photographs by name, so a
        caller that renames images while staging them silently destroys its own
        control. Refused here rather than debugged later.

        The whole cluster goes in one request. That is the limit of this client
        and it is stated: NodeODM has a chunked ``/task/new/init`` flow for very
        large sets, and the day a cluster is too big for one POST is the day that
        fourth endpoint earns itself.
        """
        if not images:
            raise NodeODMError("no images: there is no reconstruction in zero photographs")
        files: List[Tuple[str, str, bytes]] = [
            ("images", filename, blob) for filename, blob in images]
        if gcp_list:
            missing = self._unnamed_images(gcp_list, [n for n, _ in images])
            if missing:
                raise NodeODMError(
                    f"the control file names {len(missing)} photograph(s) that "
                    f"are not in this cluster ({', '.join(sorted(missing)[:3])}"
                    f"{'…' if len(missing) > 3 else ''}): the engine would "
                    f"silently ignore those points and report a registration "
                    f"solved from fewer than you gave it")
            files.append(("images", "gcp_list.txt", gcp_list.encode("utf-8")))
        fields = {"options": json.dumps(odm_options(options or {}))}
        if name:
            fields["name"] = name
        body, content_type = _multipart(fields, files)
        status, blob = self._call("POST", "/task/new", body=body,
                                  headers={"Content-Type": content_type})
        payload = self._json(status, blob, "creating the task")
        task_uuid = str(payload.get("uuid") or "").strip()
        if not task_uuid:
            raise NodeODMError(f"the engine accepted the task but named no uuid: {payload}")
        return Task(uuid=task_uuid, raw=payload)

    @staticmethod
    def _unnamed_images(gcp_list: str, names: Sequence[str]) -> set:
        known = set(names)
        wanted = set()
        for line in gcp_list.splitlines()[1:]:          # line 0 is the CRS
            parts = line.split()
            if len(parts) >= 6:
                wanted.add(parts[5])
        return wanted - known

    def task_info(self, task_uuid: str) -> Task:
        """GET /task/{uuid}/info — where the run is now."""
        status, blob = self._call("GET", f"/task/{task_uuid}/info")
        payload = self._json(status, blob, f"asking about task {task_uuid}")
        return Task(uuid=task_uuid,
                    status=int(payload.get("status", {}).get("code")
                               if isinstance(payload.get("status"), dict)
                               else payload.get("status") or STATUS_QUEUED),
                    progress=float(payload.get("progress") or 0.0),
                    raw=payload)

    def wait(self, task_uuid: str, *, poll: float = 5.0,
             deadline: float = 3600.0,
             on_progress: Optional[Callable[[Task], None]] = None) -> Task:
        """Poll until the task is terminal, or until the deadline says stop.

        The deadline is an hour by default because a US is minutes (the design's
        §3) and an hour is the point at which something is wrong rather than
        slow. Reaching it raises :class:`NodeODMTimeout` and NOT a failure: the
        task keeps running on the engine, and its uuid is in the exception's
        message so somebody can come back to it.
        """
        started = time.monotonic()
        last: Optional[Task] = None
        while True:
            last = self.task_info(task_uuid)
            if on_progress:
                on_progress(last)
            if last.done:
                return last
            if time.monotonic() - started >= deadline:
                raise NodeODMTimeout(
                    f"task {task_uuid} is still {last.status_name} after "
                    f"{int(deadline)}s: it has not failed, it is not finished — "
                    f"poll it again rather than starting a second run")
            self._sleep(poll)

    def download_all(self, task_uuid: str, *,
                     timeout: Optional[float] = None) -> bytes:
        """GET /task/{uuid}/download/all.zip — the whole output, as bytes.

        Not streamed to a path, because the caller is the one that knows where
        the bytes belong (a content-addressed store, in our case) and a
        temporary file in between would be a third copy nobody deletes.
        """
        status, blob = self._call("GET", f"/task/{task_uuid}/download/all.zip",
                                  timeout=timeout if timeout is not None
                                  else max(self.timeout, 300.0))
        if status >= 400:
            # a failure here answers JSON, not a zip
            self._json(status, blob, f"downloading task {task_uuid}")
        if not blob:
            raise NodeODMError(f"task {task_uuid} produced an empty archive")
        return blob

    # ── one call, for the ordinary case ──────────────────────────────────────

    def run(self, images: Sequence[Tuple[str, bytes]], *,
            options: Optional[Dict[str, Any]] = None,
            gcp_list: Optional[str] = None,
            name: Optional[str] = None,
            poll: float = 5.0, deadline: float = 3600.0,
            on_progress: Optional[Callable[[Task], None]] = None
            ) -> Tuple[Task, bytes]:
        """create → wait → download, with the failure named rather than swallowed."""
        task = self.create_task(images, options=options, gcp_list=gcp_list,
                                name=name)
        finished = self.wait(task.uuid, poll=poll, deadline=deadline,
                             on_progress=on_progress)
        if not finished.ok:
            detail = ((finished.raw.get("status") or {}).get("errorMessage")
                      if isinstance(finished.raw.get("status"), dict) else None)
            raise NodeODMError(
                f"task {finished.uuid} {finished.status_name}"
                + (f": {detail}" if detail else ""))
        return finished, self.download_all(finished.uuid)


#: What we ask the engine for, and why. A US is a small object photographed from
#: close range, not a drone strip — so the defaults that matter are the ones that
#: stop ODM from assuming altitude. Overridable per run; NOT silently tuned.
DEFAULT_OPTIONS: Dict[str, Any] = {
    # a textured mesh is what Heriverse consumes; the point cloud comes anyway
    "mesh-size": 200000,
    # the design's §8.2 is open: texture in the field, yes — a model nobody can
    # look at is not a model anybody checks
    "texturing-single-material": True,
    # close-range, not aerial
    "feature-quality": "high",
    "pc-quality": "medium",
    # glTF is the FAIR output the ecosystem reads (design §1.5)
    "gltf": True,
}


def odm_options(overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    """ODM wants ``[{"name": …, "value": …}, …]``, not a mapping."""
    merged = dict(DEFAULT_OPTIONS)
    merged.update(overrides or {})
    return [{"name": key, "value": value} for key, value in merged.items()]
