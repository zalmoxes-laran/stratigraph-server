"""The NODE's scope, and the capability that opens it.

A room's owner manages their room; an **operator** looks after the node. These
tests are the description of the second one, and each names a failure that would
be invisible from outside:

* a **capability an owner can grant themselves** is not a second scope, it is the
  first one with a longer name. There is no endpoint that grants it, and the tests
  below check the door rather than trusting the absence of a route;
* an **empty allow-list read as "everybody"** would open a cross-room console
  because a variable was unset. Nothing configured means nobody;
* a **console that cannot say why it is empty** teaches people to reload, so
  `whoami` answers without a 403 and names the capability;
* and a **one-click archive on a list of other people's rooms** is how the wrong
  row gets clicked: the API asks for the room's name back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module                      # noqa: E402
from app import operators as ops                        # noqa: E402
from app import ws as ws_module                          # noqa: E402
from app.access import InMemoryAclStore, Role            # noqa: E402
from app.invites import InMemoryInviteStore              # noqa: E402
from app.main import app                                 # noqa: E402
from app.rooms import RoomRegistry                       # noqa: E402
from app.store import InMemoryRoomStore, InMemorySnapshotStore  # noqa: E402

ANNA = "0000-0002-1825-0097"     # owns a room
BRUNO = "0000-0001-5109-3700"    # the node's operator, in these tests
AUTH = {"Authorization": "Bearer t"}


def container(name: str) -> dict:
    return {"header": {"format": "em.json", "version": "1.0", "owner": ANNA},
            "graphs": {name: {"graph_id": name, "name": name,
                              "nodes": [], "edges": []}},
            "active_graph_id": name}


@pytest.fixture(autouse=True)
def instance(monkeypatch):
    snapshots = InMemorySnapshotStore()
    snapshots.put("vecchia", container("vecchia"))     # a room nobody declared
    registry = RoomRegistry(snapshots, InMemoryRoomStore())
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", snapshots)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "ACL_STORE", InMemoryAclStore())
    monkeypatch.setattr(main_module, "INVITE_STORE", InMemoryInviteStore())
    return registry


@pytest.fixture
def enforcing(monkeypatch):
    """Tokens are checked, and we choose whose they are — and BOTH authenticators
    are patched, for the reason `test_rooms_register.py` documents at length."""
    class Enforcing:
        enforcing = True

        def describe(self):
            return "keycloak"

    for module in (ws_module, main_module):
        monkeypatch.setattr(module.authenticator, "settings", Enforcing())

    def be(orcid, **extra):
        claims = ({"orcid": orcid} if orcid else {})
        claims.update(extra)
        for module in (ws_module, main_module):
            monkeypatch.setattr(module.authenticator, "verify",
                                lambda token: dict(claims))
    return be


@pytest.fixture
def operator(monkeypatch):
    """Name an operator the way a deployment does: from outside every room."""
    def be(orcid):
        monkeypatch.setenv("EM_OPERATORS", orcid)
    return be


@pytest.fixture
def client():
    return TestClient(app)


# ── the capability itself ───────────────────────────────────────────────────

def test_nothing_configured_means_nobody():
    """Fail-closed. An empty allow-list is not «everybody» — it is «nobody», and a
    node that opened its cross-room console because a variable was unset would be
    the worst kind of default."""
    assert ops.is_operator({"orcid": ANNA}, environ={}) is False
    assert ops.is_operator({}, environ={}) is False
    assert ops.is_operator(None, environ={}) is False
    assert "fail-closed" in ops.describe({})


def test_the_capability_comes_from_outside_every_room():
    """Two ways in, and neither is anything a room's admin can write: a realm role
    the realm grants, or an allow-list the deployment sets."""
    assert ops.is_operator({"orcid": ANNA}, environ={"EM_OPERATORS": ANNA})
    assert not ops.is_operator({"orcid": BRUNO}, environ={"EM_OPERATORS": ANNA})
    # a realm role, in either of the two places Keycloak puts it
    assert ops.is_operator({"realm_access": {"roles": ["em-operator"]}}, environ={})
    assert ops.is_operator(
        {"resource_access": {"em-server": {"roles": ["em-operator"]}}}, environ={})
    # …renamable, because a shared realm may already have a convention
    assert ops.is_operator({"realm_access": {"roles": ["nodo"]}},
                           environ={"EM_OPERATOR_ROLE": "nodo"})
    assert not ops.is_operator({"realm_access": {"roles": ["em-operator"]}},
                               environ={"EM_OPERATOR_ROLE": "nodo"})


def test_an_orcid_url_and_a_bare_id_are_the_same_person():
    """`access._norm`'s rule, reused — an allow-list that granted to one spelling
    and refused the other would be a capability that depends on how somebody
    typed it."""
    assert ops.is_operator({"orcid": f"https://orcid.org/{ANNA}"},
                           environ={"EM_OPERATORS": ANNA})
    assert ops.is_operator({"orcid": ANNA},
                           environ={"EM_OPERATORS": f"https://orcid.org/{ANNA}"})


def test_dev_mode_is_an_operator_and_says_why():
    """With no OIDC there are no identities at all, and everything is already open
    by construction (`ws.authorize` makes dev mode owner). A lock drawn on a door
    with no wall would be theatre."""
    assert ops.is_operator({"em_dev_mode": True}, environ={})


def test_the_refusal_names_the_capability_and_who_grants_it():
    words = ops.refusal({})
    assert "em-operator" in words and "EM_OPERATORS" in words
    assert "Owning a room does not grant it" in words
    assert "ask whoever runs this node" in words


def test_describe_never_prints_the_names():
    """`/v1/health` is open enough to be a probe, which makes it open enough to be
    a screenshot. A list of the people who administer the node is not for it."""
    said = ops.describe({"EM_OPERATORS": f"{ANNA},{BRUNO}"})
    assert ANNA not in said and BRUNO not in said
    assert "2 allow-listed" in said


# ── the door ────────────────────────────────────────────────────────────────

def test_an_owner_is_not_an_operator(client, enforcing):
    """The whole split, through HTTP: Anna owns a room and is refused the node."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "mia"})
    assert client.get("/v1/rooms/mia", headers=AUTH).status_code == 200
    for path in ("/v1/admin/rooms", "/v1/admin/storage"):
        refused = client.get(path, headers=AUTH)
        assert refused.status_code == 403, path
        assert "OPERATOR" in refused.json()["detail"]
        assert "does not grant it" in refused.json()["detail"]


def test_whoami_answers_without_refusing(client, enforcing):
    """A console asks this BEFORE it draws anything. Answering 403 here would give
    it an error where it could give a sentence."""
    enforcing(ANNA)
    answer = client.get("/v1/admin/whoami", headers=AUTH)
    assert answer.status_code == 200
    body = answer.json()
    assert body["operator"] is False and body["orcid"] == ANNA
    assert "em-operator" in body["capability"]


def test_an_operator_sees_every_room_including_the_undeclared(
        client, enforcing, operator):
    enforcing(BRUNO)
    operator(BRUNO)
    # Anna's room, declared by Anna
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "mia",
                                                 "title": "La mia"})
    enforcing(BRUNO)
    rooms = client.get("/v1/admin/rooms", headers=AUTH)
    assert rooms.status_code == 200
    seen = {r["room_id"]: r for r in rooms.json()}
    assert "mia" in seen and seen["mia"]["title"] == "La mia"
    # …and the room that only exists as a snapshot: exactly what an operator
    # needs to see, since nobody has titled or claimed it
    assert "vecchia" in seen and seen["vecchia"]["implicit"] is True

    # while the owner's own listing is still only hers
    enforcing(ANNA)
    assert {r["room_id"] for r in client.get("/v1/rooms", headers=AUTH).json()} \
        == {"mia"}


def test_the_storage_report_names_what_does_not_line_up(client, enforcing,
                                                        operator):
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH,
                json={"room_id": "rotta", "container_refs": ["spostato"]})
    enforcing(BRUNO)
    operator(BRUNO)
    report = client.get("/v1/admin/storage", headers=AUTH)
    assert report.status_code == 200
    body = report.json()
    assert body["asset_store"] and body["snapshot_store"] and body["room_store"]
    rooms = {r["room_id"]: r for r in body["rooms"]}
    assert rooms["rotta"]["missing_refs"] == ["spostato"]
    assert rooms["rotta"]["declared"] is True
    assert rooms["vecchia"]["declared"] is False


def test_archiving_from_the_node_asks_for_the_name_back(client, enforcing,
                                                        operator):
    """On a console that lists other people's rooms, a single click is not
    consent."""
    enforcing(ANNA)
    client.post("/v1/rooms", headers=AUTH, json={"room_id": "mia"})
    enforcing(BRUNO)
    operator(BRUNO)
    wrong = client.post("/v1/admin/rooms/mia/archive", headers=AUTH,
                        json={"archived": True, "confirm_room_id": "altra"})
    assert wrong.status_code == 400
    assert "confirm the room by name" in wrong.json()["detail"]

    right = client.post("/v1/admin/rooms/mia/archive", headers=AUTH,
                        json={"archived": True, "confirm_room_id": "mia"})
    assert right.status_code == 200 and right.json()["archived_at"]
    # …still listed, and nothing removed
    listed = {r["room_id"]: r for r in
              client.get("/v1/admin/rooms", headers=AUTH).json()}
    assert listed["mia"]["archived_at"]
    assert client.delete("/v1/admin/rooms/mia").status_code in (404, 405)


def test_an_undeclared_room_can_be_archived_without_inventing_a_title(
        client, enforcing, operator):
    """The operator's list includes rooms nobody declared, so archiving one has to
    work — and it must not put a title on somebody's study that nobody chose."""
    enforcing(BRUNO)
    operator(BRUNO)
    archived = client.post("/v1/admin/rooms/vecchia/archive", headers=AUTH,
                           json={"archived": True, "confirm_room_id": "vecchia"})
    assert archived.status_code == 200
    assert archived.json()["archived_at"]
    assert archived.json()["title"] == "vecchia"      # its own name, not a guess
    assert archived.json()["container_refs"] == ["vecchia"]


def test_health_says_how_an_operator_is_recognised(client):
    payload = client.get("/health").json()
    assert "operators" in payload
    assert "em-operator" in payload["operators"]


# ── the console is served, and it is a shell with modules ──────────────────

def test_the_console_is_served_and_is_a_shell_with_modules(client):
    """It is mounted OUTSIDE `/v1` and NOT behind the router's auth: a 401 on the
    HTML would be a blank page with a status code. The page loads and then asks
    the node who it is — the data is behind the capability, the shell is not."""
    page = client.get("/admin/")
    assert page.status_code == 200
    assert "node console" in page.text
    assert "boot.js" in page.text, "the page loads the ordering file"
    boot = client.get("/admin/boot.js")
    assert boot.status_code == 200
    # the dependency points ONE way: modules import the shell, the shell does not
    # import them (a circular import with a top-level await deadlocks — measured
    # in a browser: the page said "Loading…" for ever and logged nothing)
    shell = client.get("/admin/console.js").text
    assert "./modules/" not in shell
    for module in ("users-rooms.js", "storage.js"):
        answer = client.get(f"/admin/modules/{module}")
        assert answer.status_code == 200, module
        assert 'from "../console.js"' in answer.text
        assert "register({" in answer.text, "a module registers itself"


# ── module 3: Node Health, and the door in front of it ──────────────────────

def test_the_health_report_is_operator_scoped(client, enforcing, operator):
    """The same door as the rest of the node scope, tested from both sides: no
    token is 401, a room owner who is not an operator is 403. A health page tells
    a reader which services this node talks to and where they are — that is a map
    of the deployment, and a map is not public."""
    assert client.get("/v1/admin/health").status_code == 401
    operator(BRUNO)
    enforcing(ANNA)                                  # owns a room, runs nothing
    refused = client.get("/v1/admin/health", headers=AUTH)
    assert refused.status_code == 403
    assert "em-operator" in refused.json()["detail"]
    enforcing(BRUNO)
    assert client.get("/v1/admin/health", headers=AUTH).status_code == 200


def test_the_report_answers_about_this_node(client, enforcing, operator):
    """em-server is asked about itself too — the probe that cannot fail is the one
    that proves the page is showing this node and not a cached one."""
    operator(BRUNO)
    enforcing(BRUNO)
    report = client.get("/v1/admin/health", headers=AUTH).json()
    names = [check["name"] for check in report["checks"]]
    assert names[0] == "em-server"
    assert {"minio", "keycloak", "iiif", "em-catalog"} <= set(names)
    assert report["checks"][0]["state"] == "ok"
    assert report["deadline_s"] > 0, "the page says what bound it ran under"
    assert report["versions"]["em_server"]


# ── the PATTERN, asserted rather than described ─────────────────────────────

def test_a_module_is_admitted_without_the_shell_knowing_its_name(client):
    """The property the console is built around: a panel is a file in `modules/`
    plus one line in `boot.js`, and `console.js` never learns it exists.

    This is checked by reading the files rather than by trusting the prose: the
    shell must not name any module, and every module present must be registered
    in the ordering file (a module nobody imports is a panel that silently is not
    there — the failure this test exists for)."""
    import pathlib

    root = pathlib.Path(main_module.__file__).parent / "node_admin"
    shell = (root / "console.js").read_text()
    boot = (root / "boot.js").read_text()
    modules = sorted(p.name for p in (root / "modules").glob("*.js"))
    assert modules, "the console has modules"
    for name in modules:
        assert name not in shell, f"the shell names {name} — the seam leaked"
        assert f'"./modules/{name}"' in boot, f"{name} is not imported by boot.js"
        source = (root / "modules" / name).read_text()
        assert 'from "../console.js"' in source
        assert "register({" in source


def test_the_next_panels_are_declared_and_not_half_built(client):
    """Four more panels are wanted (corpus · IIIF · catalog · drift). They are
    named in `boot.js` with the endpoint each will fetch, and they do NOT exist:
    a nav entry that opens an empty panel is worse than one that is not there."""
    import pathlib

    root = pathlib.Path(main_module.__file__).parent / "node_admin"
    boot = (root / "boot.js").read_text()
    for planned in ("corpus", "iiif", "catalog", "drift"):
        assert f"modules/{planned}.js" in boot, f"{planned} is not declared"
        assert f"/v1/admin/{planned}" in boot, f"{planned}'s endpoint is not named"
        assert not (root / "modules" / f"{planned}.js").exists(), \
            f"{planned} is half-built: declared as a seam and shipped as a file"
        assert client.get(f"/v1/admin/{planned}", headers=AUTH).status_code == 404
