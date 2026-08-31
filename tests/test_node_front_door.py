"""The node's front door — a composition, and never a fifth place to keep aligned.

The room browser grew a head: which node this is, who you are on it, what it
runs, and the three scopes — monuments, studies, rooms — which are one object at
three distances and therefore ONE card with three verbs.

What is worth a test here is not the layout. It is the property the page was
built around and that nothing in a browser would reveal: **it owns nothing.** No
list of services, no address of a neighbour, no rule about who may see what. The
day it knew one of those, it would have become the fifth thing to keep aligned —
the mistake `/v1/auth-config` and the handoff grammar exist to avoid.

`tests/test_handoff.py` already forbids the other half (a hand-built
`stratigraph://`); this file is about the addresses and the lists.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

_REPO = pathlib.Path(__file__).resolve().parent.parent
_UI = _REPO / "app" / "rooms_ui"
PAGE = (_UI / "index.html").read_text(encoding="utf-8")
SCRIPT = (_UI / "rooms.js").read_text(encoding="utf-8")
STYLE = (_UI / "rooms.css").read_text(encoding="utf-8")


def _code(source: str) -> str:
    """The script without its prose. The comments EXPLAIN the addresses that are
    not written down, so matching them would be matching the explanation."""
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)


CODE = _code(SCRIPT)


@pytest.fixture()
def client():
    from app import main

    return TestClient(main.app)


# ── /v1/node — what this node offers, and where ──────────────────────────────

def test_the_node_says_what_it_offers(client):
    answer = client.get("/v1/node")
    assert answer.status_code == 200
    payload = answer.json()
    assert payload["service"] == "stratigraph-server"
    named = {offer["name"] for offer in payload["offers"]}
    assert named == {"stratigraph-catalog", "iiif", "stratigraph-chatbot", "nodeodm"}


def test_a_face_nobody_configured_is_ABSENT_and_has_no_link(client, monkeypatch):
    """Not an empty tile and not a guessed path: `not configured` plus the name
    of the variable that would configure it — the habit `auth.py` set."""
    for var in ("EM_CATALOG_INTERNAL", "EM_IIIF_INTERNAL", "EM_CHATBOT_INTERNAL",
                "NODEODM_URL", "EM_CATALOG_PUBLIC", "EM_FIELD_ASSISTANT_URL"):
        monkeypatch.delenv(var, raising=False)
    for offer in client.get("/v1/node").json()["offers"]:
        assert offer["state"] == "not configured", offer
        assert offer["url"] == "", offer
        assert offer["detail"], "an absent service must say what would name it"


def test_the_public_answer_carries_NO_internal_map(client, monkeypatch):
    """`/v1/admin/health` is operator-scoped because it names internal hosts and
    latencies. This one is the reduction, and the reduction has to be real."""
    monkeypatch.setenv("EM_CATALOG_INTERNAL", "http://stratigraph-catalog:8000")
    payload = client.get("/v1/node").json()
    for offer in payload["offers"]:
        assert set(offer) == {"name", "label", "state", "url", "detail"}
    assert "stratigraph-catalog:8000" not in str(payload), \
        "an internal hostname reached a public answer"


def test_the_tools_you_install_are_a_DIFFERENT_half(client):
    """The node runs the first half and cannot know anything about the second —
    so the second carries no state at all, only where to get it."""
    tools = client.get("/v1/node").json()["tools"]
    assert {t["name"] for t in tools} == {"emstudio", "blender"}
    for tool in tools:
        assert "state" not in tool
        assert tool["download"] and tool["manual"]


def test_where_the_tools_come_from_is_overridable(client, monkeypatch):
    """A node on a dig with no route out must be able to point at its own mirror
    rather than at a domain nobody there can reach."""
    monkeypatch.setenv("EM_TOOLS_EMSTUDIO_DOWNLOAD", "http://node.local/emstudio")
    tools = {t["name"]: t for t in client.get("/v1/node").json()["tools"]}
    assert tools["emstudio"]["download"] == "http://node.local/emstudio"


# ── the page owns nothing ────────────────────────────────────────────────────

def test_the_page_writes_down_NO_neighbour_address():
    """Where the catalogue lives is a fact about the deployment. A page carrying
    `/catalog` would be right here and wrong the day somebody moves it — and
    nobody would see it move."""
    for stranger in ("/catalog", "/chat", "/iiif", "/admin/health", "localhost"):
        assert stranger not in CODE, f"{stranger!r} is written into the page"
    assert 'o.name === "stratigraph-catalog"' in CODE, \
        "the catalogue's address must come from /v1/node"


def test_the_page_holds_no_visibility_rule():
    """`GET /v1/rooms` is already ACL-filtered. A second implementation of the
    rule, in a place that is not the one that decides, is a rule with two
    answers — and the second one is the one nobody tested."""
    assert not re.search(r"your_role\s*===|\brole\s*===", CODE)
    # the only filters left are emptiness, archival and «is this a monument»
    filters = re.findall(r"\.filter\(\(?(\w+)\)?\s*=>\s*([^)]*)\)", CODE)
    for _name, body in filters:
        assert "visibility" not in body and "role" not in body, body


def test_the_doors_are_ASKED_and_never_assembled():
    """There is a test in another repository that forbids the hand-built
    `stratigraph://`; this is the same rule from the other side."""
    assert '/open`' in CODE
    assert "stratigraph://" not in CODE


def test_the_api_base_survives_the_prefix_AND_the_bare_port():
    """`/em/rooms/` behind the node's Caddy, `/rooms/` on a bare uvicorn. Taken
    from the document's DIRECTORY, which is what makes `index.html` and the bare
    directory the same case."""
    assert 'new URL(".", window.location.href).pathname' in CODE
    from urllib.parse import urljoin, urlsplit
    for href, expected in [("https://h/em/rooms/", "/em/v1"),
                           ("https://h/rooms/", "/v1"),
                           ("https://h/em/rooms/index.html", "/em/v1")]:
        directory = urlsplit(urljoin(href, ".")).path
        base = re.sub(r"/$", "", re.sub(r"rooms/$", "", directory)) + "/v1"
        assert base == expected, href


# ── one card, three verbs ────────────────────────────────────────────────────

def test_there_is_exactly_ONE_card_component():
    """A monument, a study and a room are the same object at three distances. A
    second card would be the road that multiplies."""
    assert len(re.findall(r"^function card\(", CODE, re.M)) == 1
    for verb in ('"enter"', '"read"', '"explore"'):
        assert verb in CODE, verb


def test_the_rooms_zone_starts_HIDDEN_in_the_markup():
    """Not signed, no empty list. «There is nothing here» is a lie when the truth
    is «I do not know who you are» — the mute gate in a more elegant form, which
    makes it worse. Hidden in the MARKUP, so it is not on screen for the instant
    before the script runs."""
    assert re.search(r'<section id="zone-rooms"[^>]*\bhidden\b', PAGE)
    assert re.search(r'<section id="zone-studies"[^>]*\bhidden\b', PAGE)
    assert re.search(r'<section id="zone-hdt"[^>]*\bhidden\b', PAGE)


def test_the_page_uses_the_THEME_for_hidden_and_keeps_no_copy():
    """`[hidden]` entered the vendored theme because all four faces need it. A
    local `.hidden` class doing the same job would be a second answer."""
    theme = (_REPO / "app" / "brand" / "stratigraph-theme.css").read_text(
        encoding="utf-8")
    assert "[hidden] { display: none !important; }" in theme
    assert ".hidden {" not in STYLE
    assert "classList.add(\"hidden\")" not in CODE
