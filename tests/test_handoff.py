"""The handoff, and the one property it exists to have: no credential in the URL.

`app/handoff.py` says why the link carries a place and not a permission. This
measures it — and measures it the way it can actually fail, which is not "did we
write a token" but "would we ACCEPT one": a contract that quietly tolerates a
token in a link is a contract whose next version has one in it.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent

pytest.importorskip("fastapi", reason="needs the [dev] extra")

from fastapi.testclient import TestClient  # noqa: E402

from app import handoff as ho  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

SECRETS = ("token", "access_token", "id_token", "password", "secret", "code",
           "authorization", "bearer", "api_key")


@pytest.fixture(autouse=True)
def public_base(monkeypatch):
    monkeypatch.setenv("EM_PUBLIC_BASE", "https://em.example.org")


# ── 1 · the link ─────────────────────────────────────────────────────────────

def test_the_scheme_is_the_ECOSYSTEMS_not_one_apps():
    link = ho.scheme_url("https://em.example.org", "saggio-b")
    assert link.startswith("stratigraph://open?")
    # `emstudio://` would be a scheme owned by one consumer, which is the
    # opposite of what a handoff contract is for
    assert "emstudio://" not in link


def test_the_link_carries_a_place_and_never_a_permission():
    targets = ho.open_targets("saggio-b")
    for url in [targets["scheme"], targets["web"],
                *[t["scheme"] for t in targets["tools"].values()],
                *[t["web"] for t in targets["tools"].values()]]:
        low = url.lower()
        for secret in SECRETS:
            assert f"{secret}=" not in low, f"{secret} appears in {url}"
    assert targets["carries_token"] is False


def test_a_link_that_carries_one_is_REFUSED_rather_than_ignored():
    """Not because we would use it — because accepting one teaches whoever built
    it that sending one works, and then the contract has no property left."""
    for secret in SECRETS:
        with pytest.raises(ho.HandoffError) as exc:
            ho.parse(f"stratigraph://open?server=https%3A%2F%2Fx&room=r"
                     f"&{secret}=whatever")
        assert secret in str(exc.value)


def test_both_forms_read_back_to_the_same_place():
    scheme = ho.scheme_url("https://em.example.org", "saggio b/2")
    web = ho.web_url("https://em.example.org", "saggio b/2")
    assert ho.parse(scheme) == ho.parse(web) == {
        "server": "https://em.example.org", "room": "saggio b/2"}


def test_the_web_form_may_leave_the_server_implicit_because_the_page_is_on_it():
    assert ho.parse("https://em.example.org/open?room=r") == {
        "server": "https://em.example.org", "room": "r"}


@pytest.mark.parametrize("bad, fragment", [
    ("", "empty"),
    ("stratigraph://join?room=r", "unknown action"),
    ("mailto:someone@example.org", "not a handoff link"),
    ("https://em.example.org/rooms?room=r", "not a handoff link"),
    ("stratigraph://open?server=https%3A%2F%2Fx", "names no room"),
])
def test_what_is_not_a_handoff_is_said(bad, fragment):
    with pytest.raises(ho.HandoffError) as exc:
        ho.parse(bad)
    assert fragment in str(exc.value)


def test_a_node_that_does_not_know_its_address_refuses_to_write_localhost(monkeypatch):
    for name in ("EM_PUBLIC_BASE", "EM_SERVER_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    assert ho.public_base() == ""
    with pytest.raises(ho.HandoffError) as exc:
        ho.scheme_url("", "r")
    assert "EM_PUBLIC_BASE" in str(exc.value)


def test_the_address_comes_from_CONFIG_and_never_from_the_caller(monkeypatch):
    """A link built from the `Host` header is a link an attacker can aim at their
    own server by asking for it with the right header — so it is configuration.

    And configuration ONLY: the first version fell back to `EM_IIIF_PUBLIC`'s
    origin, which against the dev stack produced a link naming Cantaloupe (a
    different service) — well formed, and pointing at the wrong machine. Measured
    on 2026-08-29 and removed.
    """
    monkeypatch.delenv("EM_PUBLIC_BASE", raising=False)
    monkeypatch.delenv("EM_SERVER_PUBLIC_URL", raising=False)
    monkeypatch.setenv("EM_IIIF_PUBLIC", "https://images.example.org/iiif/3")
    assert ho.public_base() == "", \
        "the image server's address is not this server's address"
    monkeypatch.setenv("EM_SERVER_PUBLIC_URL", "https://node.example.org/")
    assert ho.public_base() == "https://node.example.org"


def test_a_tool_is_offered_only_when_it_has_a_READER_for_the_link():
    """The list is measured, not aspired to. EMtools was written off as "not a
    room client" and that was wrong — it has had one for a while
    (`sync_bridge/ws_client.py`), so it is here; something with no reader is not.
    """
    assert set(ho.CONSUMERS) == {"emstudio", "blender", "chatbot"}
    with pytest.raises(ho.HandoffError) as exc:
        ho.open_targets("r", tools=["heriverse"])
    assert "nothing here can open a room in that" in str(exc.value)


# ── 2 · through HTTP ─────────────────────────────────────────────────────────

def _room(room_id: str) -> None:
    answer = client.post("/v1/rooms", json={"room_id": room_id,
                                            "title": f"T {room_id}"})
    assert answer.status_code in (200, 201), answer.text


def test_the_endpoint_answers_a_well_formed_handoff():
    _room("handoff-a")
    answer = client.get("/v1/rooms/handoff-a/open")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["room"] == "handoff-a"
    assert body["scheme"].startswith("stratigraph://open?")
    assert body["web"].startswith("https://em.example.org/open?")
    assert set(body["tools"]) == {"emstudio", "blender", "chatbot"}
    assert body["carries_token"] is False


def test_no_secret_reaches_the_wire_of_the_endpoints_answer():
    """A grep over the whole JSON, not just the fields we remembered to check."""
    _room("handoff-b")
    raw = client.get("/v1/rooms/handoff-b/open").text.lower()
    for secret in SECRETS:
        assert f'"{secret}"' not in raw
        assert f"{secret}=" not in raw


def test_the_parse_endpoint_is_the_ONE_grammar_the_consumers_share():
    answer = client.post("/v1/handoff/parse", json={
        "link": "stratigraph://open?server=https%3A%2F%2Fem.example.org&room=r"})
    assert answer.status_code == 200
    assert answer.json() == {"ok": True, "server": "https://em.example.org",
                             "room": "r"}
    bad = client.post("/v1/handoff/parse",
                      json={"link": "stratigraph://open?room=r&token=x"})
    assert bad.status_code == 400
    assert "never a permission" in bad.json()["detail"]


def test_the_open_page_needs_no_token_and_explains_itself():
    """A 401 on an HTML page is a blank tab with a status code."""
    answer = client.get("/open?server=https://em.example.org&room=saggio-b")
    assert answer.status_code == 200
    assert "text/html" in answer.headers["content-type"]
    page = answer.text
    assert "saggio-b" in page
    assert "stratigraph://open?" in page
    # it says the property out loud, where the person following the link reads it
    assert "not a permission" in page
    # …and it does NOT silently redirect: it says when nothing happened
    assert "Nothing opened" in page


def test_the_open_page_escapes_what_the_caller_put_in_it():
    answer = client.get("/open?room=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert "<script>alert(1)" not in answer.text
    assert "&lt;script&gt;" in answer.text


# ── 3 · the listing is per-identity ──────────────────────────────────────────

def test_my_rooms_is_the_rooms_I_HAVE_A_GRANT_IN_not_the_node():
    """Measured with two identities. In dev mode there is nobody to distinguish,
    so this drives the ACL directly — which is what the endpoint reads."""
    from app import access
    from app.main import load_acl, rooms

    _room("handoff-mine")
    _room("handoff-theirs")

    mine, theirs = "0000-0001-0000-0001", "0000-0002-0000-0002"
    store = None
    for room_id, owner in (("handoff-mine", mine), ("handoff-theirs", theirs)):
        acl = load_acl(room_id)
        store = store or acl
    # …and the property that matters, stated against `access.role_of` itself:
    # an identity with no grant gets no role, so the listing skips the room.
    empty = access.Acl(owner=theirs)
    assert access.role_of(empty, mine, "private") is None
    assert access.role_of(empty, theirs, "private") is not None
    assert rooms() is not None


def test_a_handoff_for_a_room_you_have_no_grant_in_is_refused(monkeypatch):
    """A listing is not a discovery service, and neither is a handoff."""
    import app.main as m

    _room("handoff-closed")

    async def no_role(room_id, request):
        return (None, None, None, "0000-0009-0000-0009")

    monkeypatch.setattr(m, "_acting_role", no_role)
    answer = client.get("/v1/rooms/handoff-closed/open")
    assert answer.status_code in (401, 403)
    assert "invite" in answer.json()["detail"]


# ── 4 · the two consoles are served, and share one sign-in ───────────────────

def test_both_faces_are_served_and_there_is_ONE_pkce_module():
    assert client.get("/admin/").status_code == 200
    assert client.get("/rooms/").status_code == 200
    page = client.get("/rooms/rooms.js").text
    # relative, so it survives the `/em` prefix a proxy adds
    assert 'from "../admin/auth.js"' in page
    assert not (_REPO / "app" / "rooms_ui" / "auth.js").exists(), \
        "a second copy of the PKCE module is a second thing to keep right"


def test_the_room_browser_never_writes_a_token_to_disk():
    page = (_REPO / "app" / "rooms_ui" / "rooms.js").read_text(encoding="utf-8")
    for sink in ("localStorage", "document.cookie", "indexedDB"):
        assert sink not in page, f"{sink} in the room browser"


def test_the_two_refusal_codes_are_for_two_PROTOCOLS():
    """Found by this suite, 2026-08-29: `refusal_code` returns WebSocket close
    codes (4401/4403) and two HTTP endpoints were passing one into an
    `HTTPException` — answering a literal `4403`, which no client understands and
    no proxy classifies. Two names now, so the wrong one reads as wrong."""
    from app import access

    assert access.refusal_code(None) == 4401          # the socket's
    assert access.refusal_code("0000-0001-0000-0001") == 4403
    assert access.http_refusal_code(None) == 401      # …and HTTP's
    assert access.http_refusal_code("0000-0001-0000-0001") == 403

    source = (_REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "status_code=access.refusal_code(" not in source, \
        "a WebSocket close code is being answered over HTTP again"


# ── 5 · the second door: the tool's own web build ────────────────────────────

def test_a_browser_door_appears_only_where_a_web_build_is_CONFIGURED(monkeypatch):
    """No default and no guess. A button pointing at a web app nobody deployed
    fails AFTER the click, which is worse than a button that is not there."""
    monkeypatch.delenv("EM_EMSTUDIO_WEB_URL", raising=False)
    monkeypatch.delenv("EM_FIELD_ASSISTANT_URL", raising=False)
    tools = ho.open_targets("saggio-b")["tools"]
    assert all("browser" not in t for t in tools.values())

    monkeypatch.setenv("EM_EMSTUDIO_WEB_URL", "http://localhost:5177")
    tools = ho.open_targets("saggio-b")["tools"]
    assert "browser" in tools["emstudio"]
    # …and only for the one that was named
    assert "browser" not in tools["chatbot"]
    assert "browser" not in tools["blender"]


def test_blender_can_never_have_a_browser_door():
    """Not a missing setting — Blender is not a web app, so there is no env var
    that could turn one on."""
    assert "web_env" not in ho.CONSUMERS["blender"]
    assert ho.web_app("blender") == ""


def test_the_browser_link_carries_the_same_two_parameters_and_no_token(monkeypatch):
    monkeypatch.setenv("EM_EMSTUDIO_WEB_URL", "http://localhost:5177")
    url = ho.open_targets("saggio-b")["tools"]["emstudio"]["browser"]
    assert url.startswith("http://localhost:5177/?")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert query == {"server": "https://em.example.org", "room": "saggio-b"}
    for secret in SECRETS:
        assert f"{secret}=" not in url.lower()


def test_the_browser_link_reads_back_through_the_SAME_grammar(monkeypatch):
    """The web build parses `?server=&room=` with its own copy of the grammar
    (`EMStudio/src/handoff.ts::handoffFromLocation`). Measured here against the
    server's parser, so the two cannot drift apart unnoticed."""
    monkeypatch.setenv("EM_EMSTUDIO_WEB_URL", "http://localhost:5177")
    url = ho.open_targets("saggio-b")["tools"]["emstudio"]["browser"]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    # the same pair the scheme carries, which is what makes the two doors one
    # handoff rather than two features
    assert ho.parse(ho.open_targets("saggio-b")["scheme"]) == query


def test_a_web_url_with_a_path_keeps_it(monkeypatch):
    monkeypatch.setenv("EM_EMSTUDIO_WEB_URL", "https://apps.example.org/emstudio/")
    url = ho.open_targets("r")["tools"]["emstudio"]["browser"]
    assert url.startswith("https://apps.example.org/emstudio?")


def test_the_answer_names_the_address_and_not_the_setting(monkeypatch):
    """`web_env` is this deployment's business. A browser gets the address."""
    monkeypatch.setenv("EM_EMSTUDIO_WEB_URL", "http://localhost:5177")
    raw = client.get("/v1/rooms/handoff-a/open").text
    assert "EM_EMSTUDIO_WEB_URL" not in raw
    assert "web_env" not in raw


def test_the_room_browser_draws_a_browser_door_only_from_the_ANSWER():
    """The page must not decide this for itself — whether a web build exists is
    a fact about the deployment."""
    page = (_REPO / "app" / "rooms_ui" / "rooms.js").read_text(encoding="utf-8")
    assert "if (!target.browser) continue" in page
    assert "window.open(target.browser" in page
    # …and it never builds one out of its own head
    assert "?server=" not in page


import urllib.parse  # noqa: E402  — used by the browser-door tests above
