"""The room as a place: a durable record, an invitation link, and who may what.

Until this, a room existed only while somebody was standing in it — you could not
name one, list one, or invite anybody. These tests are the description of the
record that replaced that, and each names a failure that is invisible from
outside:

* a **room that does not exist until somebody connects** cannot be prepared for
  people, and cannot be found by them;
* a **record that keeps its own member list** is a second answer to "who may
  write here", and the two will disagree on the day it matters;
* a **link that grants access by itself** is a password that gets forwarded;
* a **link that demotes** takes rights away from whoever it reaches;
* a **room pointing at a container somebody moved** must be a sentence, not a
  500 — the workspace still exists;
* and an **admin who may touch the owner** is an admin who may take the room
  (that rule already existed; this file checks the new doors obey it too).

Driven through the real `TestClient` with the authenticator in enforcing mode and
`verify` returning the claims we choose — everything but the signature check is
the production path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module                      # noqa: E402
from app import ws as ws_module                          # noqa: E402
from app.access import InMemoryAclStore, Role            # noqa: E402
from app.invites import InMemoryInviteStore, offerable  # noqa: E402
from app.main import app                                 # noqa: E402
from app.rooms import RoomDescriptor, RoomRegistry       # noqa: E402
from app.store import (InMemoryRoomStore, InMemorySnapshotStore,  # noqa: E402
                       room_store_from_env)

ANNA = "0000-0002-1825-0097"     # owner
BRUNO = "0000-0001-5109-3700"    # invited
CARLA = "0000-0003-1415-9265"    # nobody, until she is


def container(name: str) -> dict:
    return {"header": {"format": "em.json", "version": "1.0", "owner": ANNA},
            "graphs": {name: {"graph_id": name, "name": name,
                              "nodes": [], "edges": []}},
            "active_graph_id": name}


@pytest.fixture(autouse=True)
def instance(monkeypatch):
    """A clean instance: its own snapshots, ACLs, room register and invites."""
    snapshots = InMemorySnapshotStore()
    snapshots.put("scavo-a", container("scavo-a"))
    snapshots.put("scavo-b", container("scavo-b"))
    acls = InMemoryAclStore()
    registry = RoomRegistry(snapshots, InMemoryRoomStore())
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", snapshots)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "ACL_STORE", acls)
    monkeypatch.setattr(main_module, "INVITE_STORE", InMemoryInviteStore())
    return registry


@pytest.fixture
def enforcing(monkeypatch):
    """Tokens are checked, and we choose whose they are.

    BOTH authenticators are patched, and that is not belt-and-braces — it is a
    hazard this file measured. `test_auth.py::test_the_real_app_protects_v1…`
    does `importlib.reload(app.auth)` + `importlib.reload(app.main)`, and a
    reload rebinds the name inside the reloaded module while leaving `app.ws`
    holding the ORIGINAL object. Measured, in a full-suite run:

        ws.authenticator      4504400768
        main.authenticator    4509296896      ← the HTTP routes use this one

    So patching only `ws.authenticator` (which is what the older tests do) leaves
    the REST door in dev mode, and every route here would answer as if there were
    no identities. The existing tests survive it by alphabet — `test_access.py`
    runs before `test_auth.py` — which is not a property anybody chose. Declared
    rather than fixed here: repairing it means changing that reload, and this file
    does not rewrite somebody else's test.
    """
    class Enforcing:
        enforcing = True

        def describe(self):
            return "keycloak"

    for module in (ws_module, main_module):
        monkeypatch.setattr(module.authenticator, "settings", Enforcing())

    def be(orcid):
        for module in (ws_module, main_module):
            monkeypatch.setattr(module.authenticator, "verify",
                                lambda token: ({"orcid": orcid} if orcid else {}))
    return be


@pytest.fixture
def client():
    return TestClient(app)


AUTH = {"Authorization": "Bearer t"}


# ── the register ────────────────────────────────────────────────────────────

def test_a_room_exists_before_anybody_connects_and_can_be_listed(client, enforcing):
    """The property the whole record is for: a place you can prepare."""
    enforcing(ANNA)
    made = client.post("/v1/rooms", headers=AUTH,
                       json={"room_id": "cantiere", "title": "Cantiere 2026"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["room_id"] == "cantiere" and body["title"] == "Cantiere 2026"
    assert body["created_by"] == ANNA and body["created_at"]
    assert body["your_role"] == "owner"
    assert body["implicit"] is False
    # nobody has connected, no snapshot exists — and it is there
    assert ws_module.ROOMS.peek("cantiere") is None
    listed = client.get("/v1/rooms", headers=AUTH).json()
    assert [r["room_id"] for r in listed] == ["cantiere"]
    # …and the container it points at does not exist yet, which is SAID
    assert listed[0]["missing_refs"] == ["cantiere"]


def test_the_record_is_thin_and_keeps_no_member_list(client, enforcing):
    """`members` in a response is PROJECTED from the ACL. If the record stored
    it, two places could answer "who may write here" — and one of them would be
    the one somebody read."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    stored = ws_module.ROOMS.rooms_store.get("cantiere")
    assert set(stored) == {"room_id", "title", "container_refs", "created_by",
                           "created_at", "archived_at"}
    assert "members" not in stored and "owner" not in stored

    # the ACL is where a grant goes, and the projection follows it
    client.put(f"/v1/rooms/cantiere/members/{BRUNO}", headers=AUTH,
               json={"role": "editor"})
    described = client.get("/v1/rooms/cantiere", headers=AUTH).json()
    assert described["owner"] == ANNA
    assert {(m["orcid"], m["role"]) for m in described["members"]} == \
        {(BRUNO, "editor")}
    assert "members" not in (ws_module.ROOMS.rooms_store.get("cantiere") or {})


def test_a_room_references_one_container_or_two(client, enforcing):
    """A room is a WORKSPACE that references containers; a study is the published
    unit. So `container_refs` is a list — and a room with one behaves exactly as
    every room did before the record existed."""
    enforcing(ANNA)
    one = client.post("/v1/rooms", headers=AUTH,
                      json={"room_id": "uno", "container_refs": ["scavo-a"]})
    assert one.json()["container_refs"] == ["scavo-a"]
    assert one.json()["missing_refs"] == []

    two = client.post("/v1/rooms", headers=AUTH,
                      json={"room_id": "due", "title": "Due cantieri",
                            "container_refs": ["scavo-a", "scavo-b"]})
    assert two.status_code == 201
    assert two.json()["container_refs"] == ["scavo-a", "scavo-b"]
    assert two.json()["missing_refs"] == []

    # the live document comes from the PRIMARY reference — the one-container case
    # is unchanged, and the two-container case opens the first (multi-container
    # editing in one session is declared as a follow-up, not pretended here)
    import asyncio
    room = asyncio.run(ws_module.ROOMS.get("due"))
    assert set(room.document["graphs"]) == {"scavo-a"}


def test_a_room_nobody_declared_still_works(instance):
    """Every room that predates the register is exactly this: no record, one
    container, named after itself. It must keep opening."""
    implicit = instance.descriptor("scavo-a")
    assert implicit.implicit is True
    assert implicit.container_refs == ["scavo-a"]
    assert instance.missing_refs(implicit) == []
    # …and it is NOT in the register: inventing entries would make the list a
    # guess about what somebody meant
    assert instance.declared() == []


def test_a_room_pointing_at_a_moved_container_is_reported_not_a_crash(
        client, enforcing, instance):
    """Ref-integrity: the workspace still exists, its title is still meaningful,
    and the honest answer names what is missing."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH,
                json={"room_id": "orfana", "title": "Orfana",
                      "container_refs": ["scavo-a", "spostato"]})
    described = client.get("/v1/rooms/orfana", headers=AUTH)
    assert described.status_code == 200
    assert described.json()["missing_refs"] == ["spostato"]

    # and opening it does not 500: the primary reference still resolves
    joined = client.get("/v1/rooms/orfana/members", headers=AUTH)
    assert joined.status_code == 200


def test_creating_a_room_twice_is_a_conflict_not_a_silent_overwrite(client, enforcing):
    enforcing(ANNA)
    assert client.post("/v1/rooms", headers=AUTH,
                       json={"room_id": "cantiere"}).status_code == 201
    again = client.post("/v1/rooms", headers=AUTH,
                        json={"room_id": "cantiere", "title": "altro"})
    assert again.status_code == 409
    assert "already declared" in again.json()["detail"]


def test_a_listing_is_not_a_discovery_service(client, enforcing):
    """What comes back is what the ACL grants. Somebody with no role sees
    nothing — a public study is readable by whoever has its name, which is not
    the same statement as "here is everything this server holds"."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    enforcing(CARLA)
    assert client.get("/v1/rooms", headers=AUTH).json() == []
    assert client.get("/v1/rooms/cantiere", headers=AUTH).status_code == 403


def test_an_orphan_is_reported_and_archived_never_deleted(client, enforcing,
                                                          instance):
    """The lifecycle rule: a room whose container moved is a *report*, and the
    strongest thing that happens to it is a mark somebody can remove."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH,
                json={"room_id": "stagione-2025", "title": "Stagione 2025",
                      "container_refs": ["spostato"]})
    # the GC's whole job: naming what dangles
    assert [d.room_id for d in instance.orphans()] == ["stagione-2025"]

    filed = client.post("/v1/rooms/stagione-2025/archive", headers=AUTH,
                        json={"archived": True})
    assert filed.status_code == 200 and filed.json()["archived_at"]
    # …and it is STILL THERE: title, creator, references, and what is missing
    listed = {r["room_id"]: r for r in client.get("/v1/rooms", headers=AUTH).json()}
    assert listed["stagione-2025"]["title"] == "Stagione 2025"
    assert listed["stagione-2025"]["missing_refs"] == ["spostato"]
    assert listed["stagione-2025"]["archived_at"]

    back = client.post("/v1/rooms/stagione-2025/archive", headers=AUTH,
                       json={"archived": False})
    assert back.json()["archived_at"] is None

    # there is no delete, and that is on purpose
    assert client.delete("/v1/rooms/stagione-2025",
                         headers=AUTH).status_code in (404, 405)


def test_a_room_without_a_record_cannot_be_archived(client, enforcing):
    """An implicit room has nothing to mark. Said as a 404 with the remedy rather
    than by inventing a record somebody never declared."""
    enforcing(ANNA)
    refused = client.post("/v1/rooms/scavo-a/archive", headers=AUTH,
                          json={"archived": True})
    assert refused.status_code == 404
    assert "predates the register" in refused.json()["detail"]


# ── the invitation link ─────────────────────────────────────────────────────

def _invite(client, room="cantiere", role="editor", **body):
    return client.post(f"/v1/rooms/{room}/invites", headers=AUTH,
                       json={"role": role, **body})


def test_a_link_opens_the_door_and_the_orcid_walks_through(client, enforcing):
    """The three jobs: the link says which room and which role, the ORCID says
    who, the ACL ends up holding the grant."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    made = _invite(client)
    assert made.status_code == 201, made.text
    token = made.json()["token"]
    assert token and made.json()["role"] == "editor"
    assert made.json()["state"] == "live"

    # the SECRET is not kept: the record holds a digest, and listing never shows
    # the token again
    listed = client.get("/v1/rooms/cantiere/invites", headers=AUTH).json()
    assert listed[0]["token"] is None and "secret_digest" not in listed[0]

    enforcing(BRUNO)
    joined = client.post("/v1/join", headers=AUTH, json={"token": token})
    assert joined.status_code == 200, joined.text
    assert joined.json() == {"room_id": "cantiere", "title": "cantiere",
                             "role": "editor", "already_had": False}

    # …and the ROLE IS IN THE ACL now. That is the point: from here on nobody
    # reads the invitation to know what Bruno may do.
    enforcing(ANNA)
    members = client.get("/v1/rooms/cantiere/members", headers=AUTH).json()
    assert (BRUNO, "editor") in {(m["orcid"], m["role"]) for m in members["members"]}


def test_a_link_without_an_orcid_is_401_not_a_way_in(client, enforcing):
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    token = _invite(client).json()["token"]
    enforcing(None)                            # a token with no identity in it
    refused = client.post("/v1/join", headers=AUTH, json={"token": token})
    assert refused.status_code == 401
    assert "who you are" in refused.json()["detail"]


def test_a_revoked_link_is_refused_with_its_state(client, enforcing):
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    made = _invite(client).json()
    token, token_id = made["token"], made["token_id"]

    gone = client.delete(f"/v1/rooms/cantiere/invites/{token_id}", headers=AUTH)
    assert gone.status_code == 200 and gone.json()["state"] == "revoked"
    # the record SURVIVES the revocation: that the link was once live is a fact
    assert client.get("/v1/rooms/cantiere/invites",
                      headers=AUTH).json()[0]["token_id"] == token_id

    enforcing(BRUNO)
    refused = client.post("/v1/join", headers=AUTH, json={"token": token})
    assert refused.status_code == 403
    assert "revoked" in refused.json()["detail"]
    # …and nothing was granted
    enforcing(ANNA)
    members = client.get("/v1/rooms/cantiere/members", headers=AUTH).json()
    assert BRUNO not in {m["orcid"] for m in members["members"]}


def test_a_wrong_secret_and_an_unknown_token_are_the_same_refusal(client, enforcing):
    """Telling them apart would let somebody with a token id learn they had
    guessed it."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    token_id = _invite(client).json()["token_id"]
    enforcing(BRUNO)
    wrong = client.post("/v1/join", headers=AUTH,
                        json={"token": f"{token_id}.nope"})
    unknown = client.post("/v1/join", headers=AUTH,
                          json={"token": "deadbeefdeadbeef.nope"})
    assert wrong.status_code == unknown.status_code == 403
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_a_link_never_demotes(client, enforcing):
    """Somebody who is already an editor keeps editing after following a viewer
    link. An invitation is an offer, not a statement about what somebody's access
    should be reduced to."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    client.put(f"/v1/rooms/cantiere/members/{BRUNO}", headers=AUTH,
               json={"role": "editor"})
    token = _invite(client, role="viewer").json()["token"]
    enforcing(BRUNO)
    joined = client.post("/v1/join", headers=AUTH, json={"token": token}).json()
    assert joined == {"room_id": "cantiere", "title": "cantiere",
                      "role": "editor", "already_had": True}
    enforcing(ANNA)
    members = client.get("/v1/rooms/cantiere/members", headers=AUTH).json()
    assert (BRUNO, "editor") in {(m["orcid"], m["role"]) for m in members["members"]}


def test_a_link_cannot_offer_admin_or_owner(client, enforcing):
    """A URL is not a person. Owner and admin are handed over by somebody who
    identified who they are handing them to."""
    enforcing(ANNA)                            # the owner, who may assign anything
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    for role in ("admin", "owner"):
        refused = _invite(client, role=role)
        assert refused.status_code == 403, role
        assert "not by a URL" in refused.json()["detail"]
    # the same rule as a function, both halves
    assert offerable(Role.OWNER, Role.EDITOR) is None
    assert offerable(Role.EDITOR, Role.VIEWER) is not None   # not a manager


def test_only_a_manager_mints_or_reads_invitations(client, enforcing):
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    client.put(f"/v1/rooms/cantiere/members/{BRUNO}", headers=AUTH,
               json={"role": "editor"})
    enforcing(BRUNO)                           # an editor is not a manager
    assert _invite(client).status_code == 403
    assert client.get("/v1/rooms/cantiere/invites", headers=AUTH).status_code == 403


def test_a_used_up_link_stops_working(client, enforcing):
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    token = _invite(client, max_uses=1).json()["token"]
    enforcing(BRUNO)
    assert client.post("/v1/join", headers=AUTH,
                       json={"token": token}).status_code == 200
    enforcing(CARLA)
    refused = client.post("/v1/join", headers=AUTH, json={"token": token})
    assert refused.status_code == 403 and "used up" in refused.json()["detail"]


def test_an_expired_link_stops_working(client, enforcing, monkeypatch):
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    token = _invite(client, ttl_seconds=1).json()["token"]
    # the clock, not a sleep: a test that waits is a test somebody disables
    monkeypatch.setattr("app.invites._now", lambda: 9_999_999_999)
    enforcing(BRUNO)
    refused = client.post("/v1/join", headers=AUTH, json={"token": token})
    assert refused.status_code == 403 and "expired" in refused.json()["detail"]


# ── the management rules the new doors must obey ────────────────────────────

def test_an_admin_promotes_a_viewer_and_cannot_touch_the_owner(client, enforcing):
    """`may_assign`, through the room routes: the rule is not re-implemented by
    anything added tonight."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "cantiere"})
    client.put(f"/v1/rooms/cantiere/members/{BRUNO}", headers=AUTH,
               json={"role": "admin"})
    client.put(f"/v1/rooms/cantiere/members/{CARLA}", headers=AUTH,
               json={"role": "viewer"})

    enforcing(BRUNO)                           # the admin
    promoted = client.put(f"/v1/rooms/cantiere/members/{CARLA}", headers=AUTH,
                          json={"role": "editor"})
    assert promoted.status_code == 200
    assert (CARLA, "editor") in {(m["orcid"], m["role"])
                                 for m in promoted.json()["members"]}

    refused = client.put(f"/v1/rooms/cantiere/members/{ANNA}", headers=AUTH,
                         json={"role": "viewer"})
    assert refused.status_code == 403
    assert "owner" in refused.json()["detail"]

    # …and the admin cannot offer admin by link either — one rule, two doors
    assert _invite(client, role="admin").status_code == 403


def test_health_says_where_the_new_state_lives(client):
    """An operator must be able to ask whether their room titles and invitations
    survive a restart, without reading the deployment."""
    payload = client.get("/health").json()
    assert payload["room_store"].startswith(("memory", "directory"))
    assert payload["invite_store"].startswith(("memory", "directory"))


# ── the store itself ────────────────────────────────────────────────────────

def test_the_record_survives_the_process(tmp_path):
    """A directory store, and the bytes are there to look at — the property the
    in-memory one exists to remove."""
    from app.store import DirectoryRoomStore
    store = DirectoryRoomStore(str(tmp_path))
    registry = RoomRegistry(InMemorySnapshotStore(), store)
    registry.create("cantiere", title="Cantiere", created_by=ANNA,
                    container_refs=["scavo-a", "scavo-b"])
    # a NEW registry, as a restart would build it
    again = RoomRegistry(InMemorySnapshotStore(), DirectoryRoomStore(str(tmp_path)))
    record = again.descriptor("cantiere")
    assert record.implicit is False
    assert record.title == "Cantiere"
    assert record.container_refs == ["scavo-a", "scavo-b"]
    assert record.created_by == ANNA
    assert [r.room_id for r in again.declared()] == ["cantiere"]
    assert (tmp_path / "cantiere.room.json").is_file()


def test_the_room_store_goes_beside_the_snapshots(tmp_path):
    """Same discipline as the ACL store: an operator who backs one up should not
    find the others somewhere else."""
    chosen = room_store_from_env({"EM_SNAPSHOT_DIR": str(tmp_path)})
    assert type(chosen).__name__ == "DirectoryRoomStore"
    assert room_store_from_env({}).__class__.__name__ == "InMemoryRoomStore"
    picked = room_store_from_env({"EM_ROOM_DIR": str(tmp_path / "elsewhere"),
                                  "EM_SNAPSHOT_DIR": str(tmp_path)})
    assert str(picked.root).endswith("elsewhere")


def test_a_descriptor_is_read_back_as_it_was_written():
    record = RoomDescriptor(room_id="x", title="X", container_refs=["a", "b"],
                            created_by=ANNA)
    assert RoomDescriptor.from_dict(record.as_dict()).as_dict() == record.as_dict()
    # a single ref written by hand is read as a list of one
    assert RoomDescriptor.from_dict({"room_id": "y",
                                     "container_refs": "solo"}).container_refs == ["solo"]
    # …and a record with no refs falls back to the room's own name
    assert RoomDescriptor.from_dict({"room_id": "z"}).container_refs == ["z"]
