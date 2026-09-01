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
        assert set(offer) == {"name", "label", "state", "url", "detail",
                              "capabilities"}
    assert "stratigraph-catalog:8000" not in str(payload), \
        "an internal hostname reached a public answer"
    # `capabilities` is the neighbour's own declaration, carried through — and it
    # is a reduction too: `node_health._reduce_capabilities` keeps objects with
    # string (or list-of-string) values and drops any shape it cannot vouch for.
    for offer in payload["offers"]:
        for capability in offer["capabilities"]:
            assert isinstance(capability, dict)
            for value in capability.values():
                assert isinstance(value, (str, list, bool)) or value is None


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
    nobody would see it move.

    `/admin/health` USED TO BE on this list and has come off it, deliberately.
    The rule this test defends is «no NEIGHBOUR's address», and this server's own
    API paths were never neighbours: the page has always called `/rooms`,
    `/node`, `/whoami` on itself. The node map (`loadNodeMap`) asks
    `/v1/admin/health` for the same reason — it is THIS node describing itself,
    which is the opposite of writing a neighbour's address down. The distinction
    is kept by the assertions below: every neighbour still has to arrive as data.
    """
    for stranger in ("/catalog", "/chat", "/iiif", "localhost"):
        assert stranger not in CODE, f"{stranger!r} is written into the page"
    assert 'o.name === "stratigraph-catalog"' in CODE, \
        "the catalogue's address must come from /v1/node"


def test_the_node_map_owns_no_address_either():
    """The operator's zone composes; it does not know.

    Every row of it comes from `/v1/admin/health` — the node describing itself —
    so a deployment that moves Keycloak moves the map with it and this file is
    not touched. That is the measure the design note calls «the one that counts».
    """
    at = CODE.index("async function loadNodeMap")
    body = CODE[at:CODE.index("function mapRow")]
    # it reads the report, and every field it draws comes out of it
    assert '"/admin/health"' in body, "the map asks the node for the map"
    assert "report.entrances" in body and "report.checks" in body
    for field in ("check.browser", "check.probe", "check.state", "check.detail"):
        assert field in body, f"{field} comes from the node, not from here"
    # …and it never invents one
    assert "http://" not in body and "https://" not in body, \
        "the node map must not contain a literal address"
    # the operator gate is ASKED, and it is the endpoint that answers without 403
    assert '"/admin/whoami"' in body
    assert "who.operator !== true" in body, \
        "a non-operator sees no zone — not an error, and not an empty one"


def test_the_node_map_writes_nothing():
    """Read-only, so the zone can go to the institutional node as it is.

    No re-run, no seed, no reset. The only button is «copy curl», which formats
    a URL the node already gave us — formatting, not a verb.
    """
    at = CODE.index("async function loadNodeMap")
    body = CODE[at:CODE.index("function paintStrings")]
    for verb in ('request("POST"', 'request("PUT"', 'request("DELETE"',
                 'request("PATCH"'):
        assert verb not in body, f"the node map performs {verb} — it must not"


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
    # the three verbs are KEYS now, and they must stay three DIFFERENT words in
    # every language: they are the only thing telling three look-alike cards
    # apart (see `stratigraph-brand/GLOSSARY.md`).
    for verb in ('t("rooms.verb")', 't("studies.verb")', 't("hdt.verb")'):
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


# ── what a page must never print, and what it must never hide ───────────────
#
# Both of these were invisible until real data arrived on 4 September 2026: four
# case studies imported from the EM_CaseStudies archive, 37 studies in the
# catalogue, and the door showed three monument cards titled `[object Object]`
# over a list that was quietly eight-of-thirty-seven. Fake data hides exactly
# this class of bug, which is why the checks belong here rather than in a
# fixture.

def test_no_title_on_this_page_can_be_an_OBJECT():
    """`[object Object]` is `||` doing its job with an object as last operand.

    The repair is on the catalogue's side (it emits `label` now — see
    `stratigraph-catalog/app/index.py::group_label`), and the guard is here:
    everything this page puts in a text node goes through `entityText`, which
    cannot return an object. Without the guard the failure comes back the moment
    somebody adds one more fallback to the chain — which is how it would.
    """
    assert "function entityText(" in CODE
    # the chain that printed the object: `|| group.hc2` with nothing after it
    assert "|| group.hc2 ||" not in CODE
    # every entity a card shows goes through the guard
    for reached in ("entityText(group.hc2)", "entityText(group.hc1)"):
        assert reached in CODE, reached


def test_a_LIST_THAT_TRUNCATES_says_so_and_counts():
    """Villa di Aiano was in the catalogue and not on the door, and the page said
    nothing. A visible debt is a debt somebody pays."""
    assert "const SHOWN = 8;" in CODE, "one cap, named once"
    # …and BOTH lists that truncate say it, with the two numbers
    for key in ('t("studies.someOf"', 't("hdt.someOf"'):
        assert key in CODE, key
    assert "shown:" in CODE and "total:" in CODE, \
        "the sentence carries both numbers, not just «and more»"
    # the cap is not written twice
    assert CODE.count("slice(0, 8)") == 0, \
        "no literal 8 beside the named one: two zones truncating at two counts " \
        "would be a difference nobody decided"


def test_a_STUDY_S_DESKTOP_DOOR_says_when_nothing_opened():
    """The room's door said it after 1.8 s; the study's said nothing at all.

    Same page, same gesture, two degrees of honesty — which is worse than
    either. One mechanism now, and this asserts there is only one.
    """
    assert "function followScheme(" in CODE
    assert CODE.count('t("door.nothingOpened"') == 1, \
        "ONE implementation of «nothing opened», or the next door forgets it"
    # …and both doors go through it
    assert "followScheme(targets.scheme" in CODE, "the room's door"
    assert "followScheme(target.scheme" in CODE, "the study's door"

