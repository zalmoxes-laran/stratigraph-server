"""Is this node well? — the probes behind the console's Health panel.

`/v1/health` answers *«is StratiGraph Server up, and what can this build do»*: a public
probe, for an orchestrator. This module answers a different question, and it is
the operator's: **are the things StratiGraph Server depends on answering, and how much are
they holding?** MinIO, Keycloak, the IIIF server, the Catalog — a node is not
healthy because its own process is.

Four states, and the fourth is the one a two-state health check gets wrong:

    ok             it answered, and the answer made sense
    degraded       it answered, and the answer did not (a 500, a JWKS with no keys)
    unreachable    it did not answer inside the deadline
    not configured nobody asked for it on this node

**`not configured` is not a failure.** A deployment without a Catalog is not a
broken deployment, and painting it red teaches an operator to ignore red. What is
never allowed is the reverse: a service that could not be reached must never read
as `ok`.

**Bounded, with a wall clock.** Every probe runs in a worker with a *deadline*,
and that is deliberate rather than belt-and-braces: `urlopen(timeout=N)` bounds
one connection ATTEMPT, and a host with several addresses (or a black-holed IPv6
half) multiplies it — measured on this very machine while hardening EMStudio's
release gate, where a "ten-second" timeout took over two minutes. A health page
that can hang is worse than no health page: it is a health page that says nothing
while looking like it is about to.

**No secrets.** The probes report a URL's HOST, a status and a latency. Not a
key, not a token, not a bucket policy. This endpoint is operator-scoped, and it
is still not a place to print a credential.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

#: The wall-clock deadline for ONE probe. Small on purpose: this is a page an
#: operator refreshes, and the answer "it did not answer in two seconds" is more
#: useful than a spinner. Overridable for a slow link.
PROBE_DEADLINE = float(os.environ.get("EM_HEALTH_DEADLINE", "2.5"))

#: …and the socket timeout inside it. Lower than the deadline so a well-behaved
#: failure reports itself rather than being killed by the outer bound.
SOCKET_TIMEOUT = float(os.environ.get("EM_HEALTH_TIMEOUT", "2.0"))

#: How many objects the storage probe will count before it says "at least". A
#: listing of a shared bucket is an expensive question, and an operator opening a
#: page has not agreed to pay for it — so the count is capped and the cap is SAID
#: (`truncated: true`), which is the difference between a partial answer and a
#: wrong one.
COUNT_CAP = int(os.environ.get("EM_HEALTH_COUNT_CAP", "5000"))

OK, DEGRADED, UNREACHABLE, ABSENT = "ok", "degraded", "unreachable", "not configured"


@dataclass
class Check:
    """One service, as the operator sees it."""

    name: str
    state: str = ABSENT
    #: what was asked, host only — never a URL with credentials in it
    target: Optional[str] = None
    latency_ms: Optional[int] = None
    #: one sentence: what answered, or what did not
    detail: str = ""
    #: anything the probe learned that is worth showing (a bucket's size, a
    #: realm's key count). Never a secret.
    facts: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "state": self.state, "target": self.target,
                "latency_ms": self.latency_ms, "detail": self.detail,
                "facts": self.facts}


def _host_of(url: str) -> str:
    """The host and scheme, which is all an operator needs to recognise a service
    — and all that is safe to print: a MinIO endpoint can carry credentials in
    its userinfo, and a health page is a screenshot waiting to happen."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" + (
        f":{parts.port}" if parts.port else "")


def _fetch(url: str, *, expect_json: bool = False) -> tuple:
    """(status, payload_or_none, error_or_none) — never raises."""
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=SOCKET_TIMEOUT) as answer:
            raw = answer.read(65536)
            payload = None
            if expect_json:
                try:
                    payload = json.loads(raw or b"null")
                except ValueError:
                    payload = None
            return answer.status, payload, None
    except urllib.error.HTTPError as exc:
        # An HTTP status IS an answer: 404 from a service that is up is not the
        # same fact as a connection that never happened, and collapsing the two
        # is how a health page cries wolf.
        return exc.code, None, None
    except Exception as exc:                       # noqa: BLE001
        return 0, None, f"{type(exc).__name__}: {exc}"


def _timed(probe: Callable[[], Check], name: str) -> Check:
    """Run one probe under the wall-clock deadline. A probe that overruns is
    `unreachable` — the thread may still be waiting on a socket, and that is
    acceptable: what must not wait is the page.

    A DAEMON thread, not a `ThreadPoolExecutor`, and both halves of that were
    measured rather than guessed:

    * `with ThreadPoolExecutor(...)` exits through `shutdown(wait=True)`, so
      returning on timeout would block until the hung probe finished: the bound
      would look right in the code and the page would still hang;
    * an executor's worker threads are non-daemon and joined at interpreter
      exit — measured, a report that came back in 1.01 s left the *process*
      taking 30 s to die, i.e. a restart would wait for the black hole.

    So: one daemon thread per probe, abandoned if it overruns, gone when the
    process goes. Worst case one abandoned thread per unreachable service per
    page load, each ending at its own `SOCKET_TIMEOUT` — declared, not hidden.
    """
    started = time.monotonic()
    box: Dict[str, Any] = {}

    def run() -> None:
        try:
            box["check"] = probe()
        except Exception as exc:                   # noqa: BLE001
            box["error"] = exc

    worker = threading.Thread(target=run, name=f"health-{name}", daemon=True)
    worker.start()
    worker.join(timeout=PROBE_DEADLINE)

    if worker.is_alive():
        return Check(name=name, state=UNREACHABLE,
                     latency_ms=int(PROBE_DEADLINE * 1000),
                     detail=f"no answer within {PROBE_DEADLINE:g}s — the probe "
                            f"was stopped rather than left to hang the page")
    if "error" in box:
        return Check(name=name, state=UNREACHABLE,
                     detail=f"the probe itself failed: "
                            f"{type(box['error']).__name__}: {box['error']}")
    check: Check = box["check"]
    if check.latency_ms is None:
        check.latency_ms = int((time.monotonic() - started) * 1000)
    return check


# ── the probes ───────────────────────────────────────────────────────────────

def _probe_self(version: str, s3dgraphy: Optional[str],
                started_at: float) -> Check:
    """StratiGraph Server itself. It answered — that is what a request reaching this line
    means — so the useful part is what it IS: the version, the library it speaks,
    and how long it has been up."""
    return Check(name="stratigraph-server", state=OK, target="self", latency_ms=0,
                 detail=f"this process answered (up "
                        f"{int(time.time() - started_at)}s)",
                 facts={"version": version, "s3dgraphy": s3dgraphy,
                        "uptime_s": int(time.time() - started_at),
                        "host": socket.gethostname()})


def _probe_minio(store: Any) -> Check:
    """The object store, asked TWO questions: is it alive, and what is it holding.

    `/minio/health/live` is MinIO's own probe. The holding half is a bounded
    listing (see `COUNT_CAP`) through the client the asset store already has —
    not a second connection, and not a bucket policy read.
    """
    endpoint = getattr(store, "endpoint", None)
    bucket = getattr(store, "bucket", None)
    if not endpoint or not bucket:
        return Check(name="minio", state=ABSENT,
                     detail="this node stores assets locally (no MinIO "
                            "configured) — see /health for which store is in use")
    target = _host_of(endpoint)
    status, _, error = _fetch(endpoint.rstrip("/") + "/minio/health/live")
    if error:
        return Check(name="minio", state=UNREACHABLE, target=target,
                     detail=f"the object store did not answer — {error}")
    check = Check(name="minio", target=target,
                  state=OK if status in (200, 204) else DEGRADED,
                  detail=f"health/live answered {status}",
                  facts={"bucket": bucket})
    # …and what is in it. Failures here do not make the store unreachable: it
    # answered, we simply could not count — said as a fact, not as a state.
    client = getattr(store, "_client", None)
    if client is not None:
        try:
            objects = 0
            total = 0
            truncated = False
            for item in client.list_objects(bucket, recursive=True):
                objects += 1
                total += int(getattr(item, "size", 0) or 0)
                if objects >= COUNT_CAP:
                    truncated = True
                    break
            check.facts.update({"objects": objects, "bytes": total,
                                "truncated": truncated,
                                "count_cap": COUNT_CAP})
        except Exception as exc:                   # noqa: BLE001
            check.facts["listing"] = f"could not count: {type(exc).__name__}"
    return check


def _probe_keycloak(jwks_uri: Optional[str], issuer: Optional[str]) -> Check:
    """The realm. Probed at its JWKS, because that is the URL StratiGraph Server actually
    depends on: a realm that answers but publishes no keys cannot verify a single
    token, and it is `degraded` rather than `ok` — the distinction a two-state
    check cannot make."""
    if not jwks_uri:
        return Check(name="keycloak", state=ABSENT,
                     detail="no OIDC configured on this node — every /v1 route "
                            "is open (see /health: auth)")
    status, payload, error = _fetch(jwks_uri, expect_json=True)
    target = _host_of(jwks_uri)
    if error:
        return Check(name="keycloak", state=UNREACHABLE, target=target,
                     detail=f"the realm's keys are unreachable — {error}. Tokens "
                            f"cannot be verified while this is true")
    keys = len((payload or {}).get("keys") or []) if isinstance(payload, dict) else 0
    if status == 200 and keys:
        return Check(name="keycloak", state=OK, target=target,
                     detail=f"the realm published {keys} signing key(s)",
                     facts={"keys": keys, "issuer": issuer})
    return Check(name="keycloak", state=DEGRADED, target=target,
                 detail=f"the keys endpoint answered {status} with {keys} key(s) "
                        f"— token verification will fail",
                 facts={"keys": keys, "issuer": issuer})


def _probe_iiif(base: Optional[str]) -> Check:
    """The image server. There is no identifier this probe may assume exists, so
    it asks the base URL and reads the ANSWER rather than the body: any HTTP
    status means the service is there, a 5xx means it is unwell."""
    if not base:
        return Check(name="iiif", state=ABSENT,
                     detail="no IIIF image service configured (EM_IIIF_INTERNAL)")
    status, _, error = _fetch(base.rstrip("/") + "/")
    target = _host_of(base)
    if error:
        return Check(name="iiif", state=UNREACHABLE, target=target,
                     detail=f"the image server did not answer — {error}")
    if status >= 500:
        return Check(name="iiif", state=DEGRADED, target=target,
                     detail=f"the image server answered {status}")
    return Check(name="iiif", state=OK, target=target,
                 detail=f"the image server answered {status} "
                        f"(any status means it is there)")


def _probe_catalog(base: Optional[str]) -> Check:
    """The Catalog, which is a separate service and NOT one StratiGraph Server talks to in
    normal operation — so it is only probed when a deployment names it
    (`EM_CATALOG_INTERNAL`). Absent means absent."""
    if not base:
        return Check(name="stratigraph-catalog", state=ABSENT,
                     detail="no Catalog named on this node "
                            "(set EM_CATALOG_INTERNAL to watch one)")
    status, payload, error = _fetch(base.rstrip("/") + "/health", expect_json=True)
    target = _host_of(base)
    if error:
        return Check(name="stratigraph-catalog", state=UNREACHABLE, target=target,
                     detail=f"the Catalog did not answer — {error}")
    if status == 200:
        facts = {}
        if isinstance(payload, dict):
            for key in ("version", "studies", "index"):
                if key in payload:
                    facts[key] = payload[key]
        return Check(name="stratigraph-catalog", state=OK, target=target,
                     detail="the Catalog answered its health probe", facts=facts)
    return Check(name="stratigraph-catalog", state=DEGRADED, target=target,
                 detail=f"the Catalog answered {status}")


# ── the report ───────────────────────────────────────────────────────────────

_STARTED_AT = time.time()


def node_health(*, version: str, s3dgraphy: Optional[str], asset_store: Any,
                environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Every probe, run under its own deadline, plus the versions in view.

    Sequential rather than concurrent, and that is a choice worth naming: five
    probes with a 2.5 s deadline is 12.5 s in the worst case, which nobody will
    see because a service that is down answers in milliseconds (connection
    refused) — while a thread pool of pools is a way to make a health endpoint
    the thing that falls over. If a deployment ever has five slow services, the
    deadline is one environment variable away.
    """
    env = environ if environ is not None else os.environ
    checks: List[Check] = [
        _timed(lambda: _probe_self(version, s3dgraphy, _STARTED_AT), "stratigraph-server"),
        _timed(lambda: _probe_minio(asset_store), "minio"),
        _timed(lambda: _probe_keycloak(env.get("OIDC_JWKS_URI"),
                                       env.get("OIDC_ISSUER")), "keycloak"),
        _timed(lambda: _probe_iiif(env.get("EM_IIIF_INTERNAL")
                                   or env.get("EM_IIIF_INTERNAL_BASE")), "iiif"),
        _timed(lambda: _probe_catalog(env.get("EM_CATALOG_INTERNAL")), "stratigraph-catalog"),
    ]

    # The node's verdict, and the rule is the pessimistic one: anything the node
    # NEEDS being unreachable makes the node unhealthy, whatever else is fine.
    needed = {"stratigraph-server", "minio", "keycloak"}
    states = {c.name: c.state for c in checks}
    if any(states.get(name) == UNREACHABLE for name in needed):
        verdict = UNREACHABLE
    elif any(c.state == DEGRADED for c in checks) or \
            any(states.get(name) == UNREACHABLE for name in states):
        verdict = DEGRADED
    else:
        verdict = OK

    return {
        "verdict": verdict,
        "deadline_s": PROBE_DEADLINE,
        "checks": [c.as_dict() for c in checks],
        # The anteroom of the future drift module: the versions in one place, so
        # that panel becomes a comparison rather than a discovery.
        "versions": _versions(version, s3dgraphy),
    }


def _versions(version: str, s3dgraphy: Optional[str]) -> Dict[str, Any]:
    """What this node speaks. Read, never restated — the datamodel version comes
    from the JSON s3Dgraphy ships (ADR-001), which is the same file every consumer
    reads, so a drift panel later compares like with like."""
    out: Dict[str, Any] = {"em_server": version, "s3dgraphy": s3dgraphy}
    try:
        from s3dgraphy.tools.consumer_drift import CONNECTIONS_PATH, VERSION_KEY
        out["connections_datamodel"] = json.loads(
            CONNECTIONS_PATH.read_text(encoding="utf-8")).get(VERSION_KEY)
    except Exception:                              # noqa: BLE001
        out["connections_datamodel"] = None
    try:
        from s3dgraphy.exporter.emjson_exporter import SCHEMA_VERSION
        out["emjson_schema"] = str(SCHEMA_VERSION)
    except Exception:                              # noqa: BLE001
        out["emjson_schema"] = None
    return out
