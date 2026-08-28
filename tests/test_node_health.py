"""The probes, and the two ways a health page lies.

A health page earns its place by being believed, so the tests here are about the
two failures that would destroy that:

* **a false ok.** A service that did not answer must never read `ok`, and the
  four states have to stay four: `ok` · `degraded` · `unreachable` ·
  `not configured`. The last one is not a failure — a node with no Catalog is
  not a broken node — and painting it as one teaches an operator to ignore red;
* **a bound that is not a bound.** `urlopen(timeout=N)` bounds one connection
  attempt, not the probe: a black-holed address, or a name with sixteen
  addresses behind it, walks past it. So the deadline is a wall clock, and the
  test below is a stopwatch, not a reading of the code.

Plus the thing a health page must not do: print a credential. A MinIO endpoint
can carry one in its userinfo, and this page is a screenshot waiting to happen.
"""

from __future__ import annotations

import time

from app import node_health as nh


class Blackhole:
    """A store pointed at an address that swallows packets: the SYN goes out and
    nothing ever comes back. This is the case a socket timeout alone mishandles."""

    endpoint = "http://10.255.255.1:9999"
    bucket = "em-assets"

    class _Client:
        def list_objects(self, *a, **k):
            time.sleep(60)                          # never reached, bounded run
            return []

    _client = _Client()


class Credentialed:
    endpoint = "http://minioadmin:sup3rs3cret@127.0.0.1:9/"
    bucket = "em-assets"
    _client = None


def test_the_four_states_and_nothing_else():
    assert (nh.OK, nh.DEGRADED, nh.UNREACHABLE, nh.ABSENT) == (
        "ok", "degraded", "unreachable", "not configured")


def test_a_service_nobody_configured_is_not_a_failure():
    """«not configured» is a fact about the deployment. A node with no IIIF and no
    Catalog is a healthy node, and the verdict has to agree."""
    report = nh.node_health(version="0.0.0", s3dgraphy=None, asset_store=None,
                            environ={})
    states = {c["name"]: c["state"] for c in report["checks"]}
    assert states["iiif"] == nh.ABSENT
    assert states["stratigraph-catalog"] == nh.ABSENT
    assert report["verdict"] == nh.OK
    detail = [c for c in report["checks"] if c["name"] == "stratigraph-catalog"][0]["detail"]
    assert "EM_CATALOG_INTERNAL" in detail, "it says how to watch one"


def test_a_service_that_did_not_answer_never_reads_ok(monkeypatch):
    monkeypatch.setattr(nh, "PROBE_DEADLINE", 1.0)
    monkeypatch.setattr(nh, "SOCKET_TIMEOUT", 0.4)
    report = nh.node_health(
        version="0.0.0", s3dgraphy=None, asset_store=None,
        environ={"OIDC_JWKS_URI": "http://127.0.0.1:9/certs",
                 "EM_IIIF_INTERNAL": "http://127.0.0.1:9/iiif/3",
                 "EM_CATALOG_INTERNAL": "http://127.0.0.1:9"})
    states = {c["name"]: c["state"] for c in report["checks"]}
    assert states["keycloak"] == nh.UNREACHABLE
    assert states["iiif"] == nh.UNREACHABLE
    assert states["stratigraph-catalog"] == nh.UNREACHABLE
    # …and a service the node NEEDS being gone is not a "degraded" node.
    assert report["verdict"] == nh.UNREACHABLE


def test_the_bound_is_a_wall_clock_not_a_socket_option(monkeypatch):
    """The stopwatch test. The socket timeout is set to a minute and the deadline
    to a second: only an outer wall clock can bring this back in a second, and a
    `with ThreadPoolExecutor(...)` would have blocked on shutdown while looking
    correct."""
    monkeypatch.setattr(nh, "PROBE_DEADLINE", 1.0)
    monkeypatch.setattr(nh, "SOCKET_TIMEOUT", 60.0)
    started = time.monotonic()
    check = nh._timed(lambda: nh._probe_minio(Blackhole()), "minio")
    elapsed = time.monotonic() - started
    assert check.state == nh.UNREACHABLE
    assert elapsed < 2.0, f"the page waited {elapsed:.1f}s — the bound did not bite"
    assert "was stopped rather than left to hang" in check.detail


def test_a_probe_that_raises_is_unreachable_not_a_500(monkeypatch):
    """A broken probe must not take the page with it: the operator wants to see
    the other four services."""
    def explode() -> nh.Check:
        raise RuntimeError("the client library changed its mind")
    check = nh._timed(explode, "minio")
    assert check.state == nh.UNREACHABLE
    assert "the probe itself failed" in check.detail


def test_the_page_never_prints_a_credential():
    """`_host_of` is what the report shows, and userinfo is exactly the part of a
    MinIO endpoint that must not leave the process."""
    assert nh._host_of(Credentialed.endpoint) == "http://127.0.0.1:9"
    check = nh._probe_minio(Credentialed())
    printed = f"{check.target} {check.detail} {check.facts}"
    assert "sup3rs3cret" not in printed
    assert "minioadmin" not in printed


def test_an_http_status_is_an_answer_not_a_silence(monkeypatch):
    """A 404 from a service that is up and a connection that never happened are
    different facts. Collapsing them is how a health page cries wolf."""
    monkeypatch.setattr(nh, "_fetch", lambda url, **k: (404, None, None))
    up = nh._probe_iiif("http://iiif.internal:8182/iiif/3")
    assert up.state == nh.OK, up.detail
    monkeypatch.setattr(nh, "_fetch", lambda url, **k: (0, None, "URLError: refused"))
    down = nh._probe_iiif("http://iiif.internal:8182/iiif/3")
    assert down.state == nh.UNREACHABLE


def test_a_realm_that_answers_without_keys_is_degraded(monkeypatch):
    """200 is not enough: a JWKS document with no keys means every token this node
    is handed will be refused, and «ok» would send the operator looking elsewhere."""
    monkeypatch.setattr(nh, "_fetch", lambda url, **k: (200, {"keys": []}, None))
    assert nh._probe_keycloak("http://kc:8080/certs", None).state == nh.DEGRADED
    monkeypatch.setattr(nh, "_fetch",
                        lambda url, **k: (200, {"keys": [{"kid": "a"}]}, None))
    well = nh._probe_keycloak("http://kc:8080/certs", None)
    assert well.state == nh.OK
    assert well.facts["keys"] == 1


def test_a_capped_count_says_that_it_is_capped(monkeypatch):
    """The bucket listing is bounded, and a count that stopped at the cap without
    saying so would be a wrong number wearing a right one's clothes."""
    monkeypatch.setattr(nh, "COUNT_CAP", 3)

    class Client:
        def list_objects(self, *a, **k):
            return [type("O", (), {"size": 10, "object_name": f"o{i}"})()
                    for i in range(50)]

    class Many:
        endpoint = "http://127.0.0.1:9000"
        bucket = "em-assets"
        _client = Client()                # the probe borrows the store's client

    monkeypatch.setattr(nh, "_fetch", lambda url, **k: (200, None, None))
    check = nh._probe_minio(Many())
    assert check.facts["objects"] == 3
    assert check.facts["truncated"] is True
    assert check.facts["count_cap"] == 3
