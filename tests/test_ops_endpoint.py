"""`POST /v1/rooms/{room_id}/ops` — the REST door for graph operations.

The second caller of `ws.apply_from_connector`. What is measured here is the
door and only the door: the gate, the author, the cap, and — the point of the
whole thing — that **a refusal is a 200**.

That last one is the design decision worth defending in a test rather than in a
comment. A 4xx tells a client to change something and retry; a CRDT client that
retries a refused operation gets refused again for ever, because the refusal is
the convergent answer. So the status code answers «did the room hear you» and
the body answers «what did it do about it».

What these tests do NOT cover, stated rather than implied: that a real EMStudio
sitting in the room sees the fanout. `_fanout` walks `room.sockets`, which is
empty here — that half is measured against the live dev-stack and reported in
the end-of.

## TWO THINGS MEASURED HERE THAT CONTRADICT THE OBVIOUS EXPECTATION

Both were written into this file the wrong way first, and `s3dgraphy/crdt.py`
corrected them.

**1. Re-sending a batch is PARTLY applied and partly refused, per verb.**
`apply_op_to_section` (crdt.py:726) makes `add_node` idempotent by MERGING: an
id that is already there is the same node, so a second arrival returns
`OpResult(True, "merged")` — `applied`, not refused. `add_edge` and
`update_field` do answer `applied=False` on a repeat, with `"idempotent"`.
Measured against the live server, the same three-operation batch sent twice:

    first  → {"applied": 3, "refused": []}
    second → {"applied": 2, "refused": [{"op": "add_edge", …, "idempotent"}]}

So «send it twice and everything comes back refused» is false, and the counts
alone do not tell a caller whether anything changed. The property that IS true,
and the one asserted here, is that **the graph does not move**. `refused` is
populated by a repeated edge, by a stale or repeated field write, by an
operation naming a node that is not there, by an unaddressable field, and by an
unknown verb.

**2. `add_node`'s payload lives in `node` (or `data`), not at the top level.**
crdt.py:727 reads `dict(op.get("node") or op.get("data") or {})`. An operation
that puts `node_type` and `name` beside `op` and `id` lands a node with neither
— measured: `{"id": "US1", "data": {…stamps…}}`, silently. Every batch in this
file therefore uses the shape the code reads, and a test at the bottom pins it
so the next reader does not have to find out the same way.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402

from app import main as main_module                        # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.access import Acl, InMemoryAclStore               # noqa: E402
from app.main import app                                   # noqa: E402
from app.rooms import RoomRegistry                         # noqa: E402
from app.store import InMemorySnapshotStore                # noqa: E402

ANNA = "0000-0002-1825-0097"     # owner
CARLA = "0000-0003-1415-9265"    # editor — the one this door is for
BRUNO = "0000-0001-5109-3700"    # viewer

ROOM = "scavo"
HEAD = {"Authorization": "Bearer t"}


def document() -> dict:
    """An empty section, so what arrives is all there is to see."""
    return {"header": {"format": "em.json", "version": "1.0",
                       "visibility": "restricted", "owner": ANNA},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [], "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture()
def store(monkeypatch):
    """A real registry over an in-memory store, so «kept» means something.

    `apply_from_connector` writes a snapshot inside the lock, and a test that
    stubbed the store out would pass while the operations lived in a dictionary
    that dies with the process — which is exactly the bug that made that
    function exist (photogrammetry, 9 September).
    """
    kept = InMemorySnapshotStore()
    kept.put(ROOM, document())
    acls = InMemoryAclStore()
    acls.put(ROOM, Acl(owner=ANNA,
                       members={CARLA: "editor", BRUNO: "viewer"}).as_dict())
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", kept)
    monkeypatch.setattr(ws_module, "ROOMS", RoomRegistry(kept))
    monkeypatch.setattr(ws_module, "ACL_STORE", acls)
    return kept


@pytest.fixture()
def whoever(monkeypatch):
    """Tokens are enforced, and we choose whose they are.

    The same shape `test_asset_rights.py` uses. Enforced rather than dev mode
    because dev mode is `owner` for everybody by design, so a gate cannot be
    measured through it.
    """
    class Enforcing:
        enforcing = True

        def describe(self):
            return "keycloak"

    monkeypatch.setattr(ws_module.authenticator, "settings", Enforcing())
    monkeypatch.setattr(main_module.authenticator, "settings", Enforcing())

    def be(orcid):
        claims = {"orcid": orcid} if orcid else {}
        monkeypatch.setattr(ws_module.authenticator, "verify",
                            lambda token: claims)
        monkeypatch.setattr(ws_module.authenticator, "require_token",
                            lambda request: claims)
        monkeypatch.setattr(main_module.authenticator, "require_token",
                            lambda request: claims)
    return be


@pytest.fixture()
def client():
    return TestClient(app)


def _units(*names, at="2026-09-13T10:00:00Z"):
    """A small stratigraphic delivery, in the shape `apply_op_to_section` READS.

    The payload goes in `node`. Putting `node_type` and `name` at the top level
    beside `op` and `id` is accepted and lands a node with neither — see the
    module docstring.
    """
    return [{"op": "add_node", "id": name,
             "node": {"node_type": "US", "name": f"US {name}"},
             "ts": at} for name in names]


def _rename(node_id, value, at):
    """A field write — the ONE verb that can come back refused."""
    return [{"op": "update_field", "node_id": node_id, "field": "name",
             "value": value, "ts": at}]


def _nodes(kept) -> dict:
    section = kept.get(ROOM)["graphs"][ROOM]
    return {n["id"]: n for n in section["nodes"]}


# ── 1 · the ordinary delivery ────────────────────────────────────────────────

def test_an_editors_batch_lands_and_is_kept(client, store, whoever):
    whoever(CARLA)
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": _units("US1", "US2", "US3"),
                               "graph_id": ROOM},
                         headers=HEAD)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["applied"] == 3
    assert body["refused"] == []
    assert body["kept"] is not None, (
        "the door answered without keeping: `applied` that is not on disk is "
        "the failure that looks like a success")

    landed = _nodes(store)
    assert set(landed) == {"US1", "US2", "US3"}
    assert landed["US2"]["node_type"] == "US", (
        "the node landed without its type: the payload has to be in `node`, "
        "which is what crdt.py reads")
    assert landed["US2"]["name"] == "US US2"
    # …and the editorial stamps are the server's, written where the graph keeps
    # them rather than by the caller.
    assert landed["US2"]["data"]["created_by"] == CARLA


def test_the_author_is_the_callers_identity_and_never_the_payloads(
        client, store, whoever):
    """An author nobody verified is not an author.

    The payload asks to be attributed to somebody else; the room records the
    person holding the token. `apply_from_connector` pops it — this test is here
    so that a future door cannot quietly stop calling that function.
    """
    whoever(CARLA)
    forged = _units("US9")
    forged[0]["author"] = ANNA          # …not the caller
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": forged, "graph_id": ROOM}, headers=HEAD)
    assert answer.status_code == 200, answer.text

    # Asserted on the GRAPH rather than on the registry's internals: the
    # editorial stamp is where the attribution ends up, so it is the thing a
    # reader of the study will see — and it does not depend on how the registry
    # happens to expose its rooms today.
    landed = _nodes(store)["US9"]
    assert landed["data"]["created_by"] == CARLA, (
        f"the node was attributed to {landed['data'].get('created_by')!r}, "
        f"which is what the payload asked for rather than who was holding the "
        f"token")
    assert landed["data"]["created_by"] != ANNA


# ── 2 · a refusal is not an error ────────────────────────────────────────────

def test_the_same_batch_twice_leaves_the_graph_IDENTICAL(client, store,
                                                          whoever):
    """Idempotence, from THIS door, proved the way it is actually true.

    A connector that re-sends after a timeout it never saw the answer to is
    doing the right thing, and this is what it gets: a 200, and a graph that
    has not moved.

    **NOT «everything refused».** `add_node` is idempotent by merging, so the
    second delivery comes back `applied: 2` with the internal reason `merged`
    — measured, and it is the opposite of what the endpoint's plan expected.
    The invariant that matters is the state, and the state is what is asserted.

    THE LIMITATION THIS EXPOSES, asserted so it cannot be forgotten: the door's
    body cannot tell the caller «this changed nothing». `apply_from_connector`
    returns a COUNT of applied operations, not their reasons, so a first
    delivery and a repeat are indistinguishable from outside. Reported rather
    than repaired: the return shape belongs to that function, and changing it
    is an architecture decision.
    """
    whoever(CARLA)
    batch = {"ops": _units("US1", "US2"), "graph_id": ROOM}

    first = client.post(f"/v1/rooms/{ROOM}/ops", json=batch, headers=HEAD)
    assert first.status_code == 200 and first.json()["applied"] == 2
    before = _nodes(store)

    second = client.post(f"/v1/rooms/{ROOM}/ops", json=batch, headers=HEAD)
    assert second.status_code == 200, (
        "the second delivery was an HTTP error, which tells the caller to "
        "retry — and a retried refusal is refused for ever")
    body = second.json()
    assert _nodes(store) == before, (
        "the graph moved on a repeated delivery: that is the property this "
        "whole door depends on")
    assert body["applied"] == 2 and body["refused"] == [], (
        f"if this now reports refusals, `add_node`'s merge semantics changed "
        f"in s3dgraphy and this door's documentation is out of date: {body}")


def test_a_repeated_edge_IS_refused_while_a_repeated_node_is_merged(
        client, store, whoever):
    """The per-verb asymmetry, pinned because it is surprising.

    Measured on the live server before it was written here: the same batch of
    two nodes and one edge comes back `applied: 3` the first time and
    `applied: 2, refused: [add_edge … idempotent]` the second. A caller written
    against «a repeat is all refused» would read that as a partial failure.
    """
    whoever(CARLA)
    batch = {"ops": _units("US1", "US2") + [
        {"op": "add_edge", "id": "US1__is_before__US2", "source": "US1",
         "target": "US2", "edge_type": "is_before",
         "ts": "2026-09-13T10:00:02Z"}], "graph_id": ROOM}

    first = client.post(f"/v1/rooms/{ROOM}/ops", json=batch, headers=HEAD).json()
    assert first["applied"] == 3 and first["refused"] == [], first

    second = client.post(f"/v1/rooms/{ROOM}/ops", json=batch, headers=HEAD).json()
    assert second["applied"] == 2, second
    assert [r["op"] for r in second["refused"]] == ["add_edge"], second
    assert second["refused"][0]["reason"] == "idempotent"


def test_a_stale_field_write_inside_a_good_batch_is_named(client, store,
                                                          whoever):
    """Partial is normal, and the caller is told which half.

    `update_field` is the verb that can be stale: an older clock on a field
    that already carries a newer value loses, and losing is `applied=False`
    with the reason `"stale"`. So the batch is not rejected — the one operation
    is, by name, which is what lets a caller tell «already known» from
    «malformed».
    """
    whoever(CARLA)
    client.post(f"/v1/rooms/{ROOM}/ops",
                json={"ops": _units("US1"), "graph_id": ROOM}, headers=HEAD)
    client.post(f"/v1/rooms/{ROOM}/ops",
                json={"ops": _rename("US1", "il nome nuovo",
                                     "2026-09-13T12:00:00Z"),
                      "graph_id": ROOM}, headers=HEAD)

    mixed = (_rename("US1", "un nome vecchio", "2026-09-13T08:00:00Z")
             + _units("US2", at="2026-09-13T12:00:00Z"))
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": mixed, "graph_id": ROOM}, headers=HEAD)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["applied"] == 1, body
    assert len(body["refused"]) == 1, body["refused"]
    assert body["refused"][0]["reason"] == "stale", (
        f"the reason travels to the caller, and it is the difference between "
        f"«already known» and «your operation is wrong»: {body['refused'][0]}")

    # A DEFECT, asserted as it is rather than repaired here — the function that
    # builds this record is shared with the photogrammetry door and is not this
    # change's to rewrite.
    #
    # `apply_from_connector` reports a refusal as
    # `{"op": …, "id": entry.get("id"), "reason": …}`, and an `update_field`
    # operation identifies its node with `node_id`, not `id`. So every refused
    # field write comes back naming NO node, and a caller cannot act on it.
    # The one-line fix — `entry.get("id") or entry.get("node_id")` — is proposed
    # in the end-of. Until it is taken, this is the honest shape:
    assert body["refused"][0]["id"] is None, (
        "the refusal now names the node, which means `apply_from_connector` "
        "was fixed — good: tighten this assertion to == 'US1' and delete this "
        "comment")
    assert "US2" in _nodes(store)
    assert _nodes(store)["US1"]["name"] == "il nome nuovo", \
        "the stale write won"


def test_the_refusals_a_caller_can_actually_get(client, store, whoever):
    """The four shapes of `refused`, so a client can be written against them.

    Measured against `apply_op_to_section` rather than assumed, because «a
    refusal» is not one thing: two of these are normal traffic and two are bugs
    at the caller's end, and a connector needs to tell them apart.
    """
    whoever(CARLA)
    client.post(f"/v1/rooms/{ROOM}/ops",
                json={"ops": _units("US1"), "graph_id": ROOM}, headers=HEAD)

    probes = (_rename("MAI-ESISTITO", "x", "2026-09-13T12:00:00Z")
              + [{"op": "update_field", "node_id": "US1",
                  "field": "node_type", "value": "USV",
                  "ts": "2026-09-13T12:00:00Z"}]
              + [{"op": "un-verbo-inventato", "id": "US1",
                  "ts": "2026-09-13T12:00:00Z"}])
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": probes, "graph_id": ROOM}, headers=HEAD)
    assert answer.status_code == 200, answer.text
    reasons = [r["reason"] for r in answer.json()["refused"]]
    assert len(reasons) == 3, answer.json()
    assert "is not here" in reasons[0]
    assert "not an addressable field" in reasons[1], (
        "only `name`, `description` and `data.*` are addressable — `node_type` "
        "has to travel in `add_node`'s payload")
    assert "unknown operation" in reasons[2]


# ── 3 · the gate ─────────────────────────────────────────────────────────────

def test_a_viewer_may_not_write(client, store, whoever):
    whoever(BRUNO)
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": _units("US1"), "graph_id": ROOM},
                         headers=HEAD)
    assert answer.status_code == 403, answer.text
    assert "editor" in answer.json()["detail"], (
        "the refusal must say what role is needed: «no» with a reason is "
        "actionable, «no» is not")
    assert _nodes(store) == {}


def test_nobody_may_not_write(client, store, whoever):
    """No identity, no write — and the graph is untouched.

    Two checks stand behind this one 403: the role resolution (a stranger is
    not a member) and the belt-and-braces refusal of an unattributed write. The
    test asserts the outcome, not which of the two fired.

    NOTE what this does NOT measure: a request with NO `Authorization` header at
    all never reaches those checks — `require_token` refuses it first, and the
    live server answers **401**. Measured against the dev-stack. Here the header
    is present and the claims are empty, which is the case these two checks are
    for.
    """
    whoever(None)
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": _units("US1"), "graph_id": ROOM},
                         headers=HEAD)
    assert answer.status_code == 403, answer.text
    assert _nodes(store) == {}


# ── 4 · the cap, and the empty delivery ──────────────────────────────────────

def test_a_batch_over_the_cap_is_a_413_that_says_why(client, store, whoever,
                                                     monkeypatch):
    """413, and the sentence names the lock rather than the bytes.

    The cap is patched down instead of building a thousand operations: what is
    being tested is that the limit is enforced and explained, not the constant's
    value — and a test that had to construct 1001 nodes would be measuring
    pytest.
    """
    monkeypatch.setattr(main_module, "OPS_BATCH_MAX", 2)
    whoever(CARLA)
    answer = client.post(f"/v1/rooms/{ROOM}/ops",
                         json={"ops": _units("US1", "US2", "US3"),
                               "graph_id": ROOM}, headers=HEAD)
    assert answer.status_code == 413, answer.text
    detail = answer.json()["detail"]
    assert "3 operations" in detail and "2" in detail
    assert "lock" in detail, (
        "the refusal should say WHY there is a limit — a caller who is told "
        "«too many» learns nothing about how to split the work")
    assert _nodes(store) == {}, "a refused batch applied part of itself"


def test_an_empty_delivery_is_fine(client, store, whoever):
    """A site with no units is a correct answer from an adapter, not an error.

    So the door does not make every client special-case it.
    """
    whoever(CARLA)
    answer = client.post(f"/v1/rooms/{ROOM}/ops", json={"ops": []},
                         headers=HEAD)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"applied": 0, "refused": [], "kept": None}


# ── 5 · the door does not invent its own provenance ──────────────────────────

def test_the_wire_source_names_the_door_and_is_not_the_photogrammetry_one():
    """`source` is what a listener filters by.

    Read off the module rather than restated: the REST door must not label a
    stratigraphic delivery as a reconstruction, and it must not let the caller
    choose the label — a caller-chosen provenance is the same class of statement
    as a caller-chosen author.
    """
    import inspect
    import re

    source = inspect.getsource(main_module.apply_ops)
    without_prose = re.sub(r'"""[\s\S]*?"""', '""', source)
    without_prose = re.sub(r"#[^\n]*", "", without_prose)

    assert "source=REST_OPS_SOURCE" in without_prose.replace(" ", ""), (
        "the door must pass its own named source to apply_from_connector")
    assert "CONNECTOR_SOURCE" not in without_prose, (
        "that constant is the string 'photogrammetry'")
    assert main_module.REST_OPS_SOURCE != main_module.CONNECTOR_SOURCE
    assert "body.source" not in without_prose and "ask.source" not in without_prose, (
        "the source must not come from the request")
