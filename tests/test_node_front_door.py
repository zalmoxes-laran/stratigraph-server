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
#: THE PAGES SEPARATED BY VERB on 6 September 2026 (design note
#: `EM_design_aprire-entrare-creare.md`). `index.html` is the VESTIBULE — who you
#: are, how the node is, where to go — and two verbs are pages of their own. The
#: assertions about a zone follow the zone: a test that kept asserting on the
#: vestibule what moved to `/work/` would go green by looking at the wrong file.
WORK = (_UI / "work" / "index.html").read_text(encoding="utf-8")
TOOLS = (_UI / "tools" / "index.html").read_text(encoding="utf-8")
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


def test_the_curl_is_offered_only_for_a_question_ACTUALLY_asked():
    """A row that says «off by choice» must not offer to curl the thing.

    Measured on the map: the engine read «off by choice» — the node had
    deliberately not dialled it — and the row still offered `copy curl`, i.e.
    offered to demonstrate a failure that is not a fault, on a service we had just
    said nobody is running. The address is still SHOWN (an operator wants to see
    which one it would use); the button follows the probe.
    """
    assert "curlable: Boolean(check.probe)" in CODE, \
        "a neighbour's curl is offered when a probe actually asked something"
    assert "if (curlable &&" in CODE, "…and the button is gated on it"


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
    """A monument, a study and a room were the same object at three distances, and
    one card drew all three. Two of the distances now live in the catalogue, so
    this server draws ONE — and the component stays single, because the reason a
    second card is the road that multiplies did not change.
    """
    assert len(re.findall(r"^function card\(", CODE, re.M)) == 1
    assert 't("rooms.verb")' in CODE
    for gone in ('t("studies.verb")', 't("hdt.verb")'):
        assert gone not in CODE, (
            f"{gone} is back on this server's face. Published studies belong to "
            "the catalogue: the door doing four jobs at once is what the cut of "
            "6 September was for, and undoing it is a decision for the note.")


def test_the_rooms_zone_starts_HIDDEN_in_the_markup():
    """Not signed, no empty list. «There is nothing here» is a lie when the truth
    is «I do not know who you are» — the mute gate in a more elegant form, which
    makes it worse. Hidden in the MARKUP, so it is not on screen for the instant
    before the script runs."""
    # …and it is asserted on the page that HOLDS it: the zone moved to `/work/`
    # with the verb, and asserting it on the vestibule would be a test looking at
    # a file where the thing cannot be.
    assert re.search(r'<section id="zone-rooms"[^>]*\bhidden\b', WORK)
    assert 'id="zone-rooms"' not in PAGE, \
        "the vestibule does not own a list — it says where the lists are"
    # the vestibule's own content cannot be a list either: destinations are drawn
    # from `/v1/node` and the operator answer, and there is no roster to leak
    assert 'id="destinations"' in PAGE


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

    RETIRED AS A GUARD HERE, and the retirement is declared rather than silent:
    `entityText` existed for the study and monument cards, and both went to the
    catalogue on 6 September. A room's title is `room.title || room.room_id` —
    two strings, no object to print — so keeping the helper would have been dead
    code, and a test asserting a dead helper is a test that measures nothing.

    THE DEBT, named because it left this repo and did not arrive anywhere: the
    catalogue's UI has no equivalent guard. Its suite covers the HDT view at the
    index and API level and its page for brand and locales, not the chain that
    puts an entity in a text node. That is a `stratigraph-catalog` task, and it
    is written down here because this is where somebody would come looking.

    What this test still holds is the shape of the failure, so a title chain that
    can end on an object cannot come back on THIS face unnoticed.
    """
    assert "function entityText(" not in CODE, \
        "entityText is back: if a card here shows an entity again, restore the " \
        "guard AND its assertions, do not leave the helper unmeasured"
    # a room's two title sources, and neither can be an object
    assert "room.title || room.room_id" in CODE or "room.title" in CODE
    for chain in ("|| group.hc2", "|| study.hc2"):
        assert chain not in CODE, chain


def test_NOTHING_ON_THIS_FACE_TRUNCATES_SILENTLY():
    """Villa di Aiano was in the catalogue and not on the door, and the page said
    nothing. A visible debt is a debt somebody pays.

    Measured after the cut, and it corrected a sentence I had written: only the
    two zones that went to the catalogue ever truncated. The rooms list does not
    — it filters the archived and draws the rest — so `SHOWN` was left without a
    caller and went with them.

    So the property this test defends is now the stronger one: this face has NO
    cap at all, and therefore cannot hide anything behind one. The day a list
    here needs a cap, it must arrive together with the sentence carrying both
    numbers — which is what the assertion below is for.
    """
    assert "const SHOWN" not in CODE, "a cap came back"
    # SCOPED TO THE FUNCTION THAT DRAWS THE LIST, and the first version was not:
    # it matched `.slice(0, 60)` in the room-id derivation — a name being cut to
    # a length, which is not a list hiding rows. A test that fires on the wrong
    # `slice` teaches people to widen it until it fires on nothing.
    body = CODE[CODE.index("function renderRooms("):]
    body = body[:body.index("\nfunction ")]
    assert not re.search(r"\.slice\(0,\s*\d+\)", body), (
        "the rooms list truncates. That is allowed, but not silently: bring back "
        "the «showing {shown} of {total}» sentence with both numbers, and assert "
        "it — «and more» without the count is the invisible debt this test is "
        "named after.")


def test_a_STUDY_S_DESKTOP_DOOR_says_when_nothing_opened():
    """The room's door said it after 1.8 s; the study's said nothing at all.

    Same page, same gesture, two degrees of honesty — which is worse than
    either. One mechanism now, and this asserts there is only one.
    """
    assert "function followScheme(" in CODE
    assert CODE.count('t("door.nothingOpened"') == 1, \
        "ONE implementation of «nothing opened», or the next door forgets it"
    # …and the door still on this face goes through it. The study's door left with
    # the studies zone; the MECHANISM is what mattered and it is still single, so
    # the next door added here inherits the honesty instead of re-deciding it.
    assert "followScheme(targets.scheme" in CODE, "the room's door"
    assert CODE.count("followScheme(") == 2, (
        "one definition and one caller. A second caller is welcome — a second "
        "IMPLEMENTATION is what this test exists to refuse.")



# ── THE PAGES SEPARATE BY VERB (6 September 2026) ────────────────────────────
#
# The door did four jobs at once — what runs on this node, the rooms, the
# studies, the monuments — so whoever arrived for one walked through three. The
# cut, from the design note `EM_design_aprire-entrare-creare.md`:
#
#     consultare    the CATALOGUE (`/catalog/ui/`) — measured before removing
#                   anything: it already lists the published studies and already
#                   has the by-HDT view
#     lavorare      `/work/`
#     attrezzarsi   `/tools/`
#     amministrare  `/admin/`, and the map on the vestibule
#
# …and the door stays ONE: four equal pages would lose the property it had won,
# which is that you arrive and understand where everything is.

def test_each_zone_lives_on_EXACTLY_ONE_page():
    """A zone on two pages is a zone that will diverge on one of them."""
    zones = ("zone-rooms", "create-row", "services", "tools-install",
             "destinations", "zone-map")
    pages = {"vestibule": PAGE, "work": WORK, "tools": TOOLS}
    for zone in zones:
        holders = [name for name, page in pages.items() if f'id="{zone}"' in page]
        assert len(holders) == 1, f"{zone} is on {holders or 'no page at all'}"


def test_the_VESTIBULE_owns_no_list():
    """Its whole job is to say who you are, how the node is, and where to go. A
    roster on it would be the fifth place to keep aligned — and the thing the
    other three pages are for."""
    for owned_elsewhere in ("zone-rooms", "services", "tools-install"):
        assert f'id="{owned_elsewhere}"' not in PAGE, owned_elsewhere
    assert 'id="destinations"' in PAGE
    assert 'id="node-line"' in PAGE and 'id="gate"' in PAGE


def test_the_THREE_DOORS_share_ONE_script_and_ONE_stylesheet():
    """Three scripts would be three copies of the session, the token refresh and
    the language — three places for the same bug. Each zone draws only where its
    host element exists."""
    assert 'src="./rooms.js"' in PAGE
    for name, page in (("work", WORK), ("tools", TOOLS)):
        assert 'src="../rooms/rooms.js"' in page, name
        assert 'href="../rooms/rooms.css"' in page, name
        # the console's sheet FIRST, because it is the one importing the theme —
        # the same chain the vestibule uses, so the faces cannot drift apart
        assert 'href="../admin/console.css"' in page, name
        assert page.index('"../admin/console.css"') < page.index('"../rooms/rooms.css"')


def test_the_API_ADDRESS_is_derived_from_WHICHEVER_DOOR_you_stand_at():
    """The trap this file already carried a comment about, one spelling later: a
    strip that only knew `rooms/` derived `/em/work/v1` from the new page, and
    every call 404s behind a "Loading…"."""
    assert "const DOORS = " in CODE
    match = re.search(r"const DOORS = /(.+)/;", CODE)
    assert match, "the doors are not a named pattern any more"
    for verb in ("rooms", "work", "tools"):
        assert verb in match.group(1), verb


def test_the_two_child_pages_have_NO_GATE_they_do_not_need():
    """`/tools/` needs no session at all — the node describes itself to anybody —
    so «sign in to see the rooms you work in» there would be an obstacle in front
    of an open door. `/work/` does need one, and keeps it."""
    assert 'id="gate"' in WORK, "the work page needs the gate: a listing needs a session"
    assert 'id="gate"' not in TOOLS
    assert 'id="node-line"' in TOOLS, "…but which node this is belongs on every door"


def test_the_operator_door_and_the_MAP_come_from_ONE_answer():
    """Two gates asking the same question twice are two gates that can disagree
    about who is an operator."""
    assert CODE.count('request("GET", "/admin/whoami")') == 2, (
        "one call per page-shape (map / destinations-only) and no third: the "
        "answer sets `operator` and nothing else decides it")
    assert "let operator = false;" in CODE
    assert "operator = true;" in CODE
    # …and it is not asked where it would gate nothing
    assert 'if (!$("destinations")) return;' in CODE, (
        "asking `/admin/whoami` on a page with neither a map nor a destination "
        "to reveal is a request made for its side effect — and it answers 401 "
        "without a session, which is a red line in a console on a page that "
        "needs no session at all")


def test_all_three_doors_are_actually_SERVED(client):
    """A page that exists in the repo and is not mounted is a page that stops
    existing in silence — and the two new ones are mounted by a LOOP, so a typo in
    the tuple takes both away without a line of the file looking wrong.

    Each door must also serve ITS OWN page: two mounts pointing at one directory
    would answer 200 for both and show the same thing, which is the failure that
    looks like a success.
    """
    for path, marker in (("/rooms/", 'id="destinations"'),
                         ("/work/", 'id="zone-rooms"'),
                         ("/tools/", 'id="tools-install"')):
        answer = client.get(path)
        assert answer.status_code == 200, (path, answer.status_code)
        assert marker in answer.text, f"{path} served somebody else's page"
    # …and the script and stylesheet the two child pages reach for
    for asset in ("/rooms/rooms.js", "/rooms/rooms.css", "/admin/console.css"):
        assert client.get(asset).status_code == 200, asset


def test_the_new_doors_are_REGISTERED_WITH_THE_REALM():
    """Found by clicking «Sign in» on `/em/work/` in Chrome: the realm answered
    «Invalid parameter: redirect_uri». A door the identity provider has never
    heard of cannot be signed in from, and neither the page nor the server says
    so — the failure is a Keycloak error page one redirect away.

    So every door this server mounts must appear in the dev realm's `em-console`
    client, in the same spellings its siblings use: the bare mount on :8000 and
    the two https hostnames behind Caddy. A door registered in three of the four
    is a door that works until somebody runs the stack the other way.

    THIS DEFENDS THE FIXTURE ONLY, and says so: a deployed realm is not in this
    repo — it is configured wherever that Keycloak lives — so the end-of names
    that as a deploy step instead of pretending a test covers it.
    """
    import json
    realm = json.loads((_REPO / "dev-stack" / "keycloak"
                        / "realm-em-dev.json").read_text(encoding="utf-8"))
    console = next(c for c in realm["clients"] if c["clientId"] == "em-console")
    uris = set(console["redirectUris"])
    for verb in ("rooms", "work", "tools"):
        for wanted in (f"http://localhost:8000/{verb}/*",
                       f"http://127.0.0.1:8000/{verb}/*",
                       f"https://em.localhost:8443/em/{verb}/*",
                       f"https://localhost:8443/em/{verb}/*"):
            assert wanted in uris, wanted


def test_a_door_tries_the_realm_SILENTLY_once_and_then_stops():
    """The split cost this and it had to be paid: the face used to be one page, so
    signing in was one click; with three doors and a token that lives in memory
    (the right place — a token in web storage outlives the person at the
    keyboard), walking between them asked again each time.

    `prompt=none` is the realm saying «you already have a session» without showing
    anybody anything. Measured in Chrome: with a session `/em/rooms/` signs in
    with ZERO clicks; without one the marker is set once, the gate says its own
    sentence, and no error is shown — `login_required` went exactly as designed.
    """
    auth = (_UI.parent / "node_admin" / "auth.js").read_text(encoding="utf-8")
    assert 'url.searchParams.set("prompt", "none")' in auth
    assert "silent: Boolean(saved && saved.silent)" in auth, (
        "the ANSWER must say whether the round was silent, or a caller cannot "
        "tell «no session, as expected» from «the sign-in broke»")

    assert "silentTried()" in CODE and "markSilentTry()" in CODE
    assert "forgetSilentTry()" in CODE, (
        "a marker belongs to the identity that failed: a SUCCESS must clear it, "
        "or the second door of the next session is refused without even trying")
    # …and the marker is per DOOR, because each door is its own redirect_uri
    assert "sg.silenttry:${window.location.pathname}" in SCRIPT
    # …and a silent failure is not reported as one
    assert "result.silent" in CODE


def test_WHO_YOU_ARE_belongs_to_every_door_and_not_to_the_one_with_a_list():
    """Measured in Chrome with a live session, and it is a defect the split
    introduced: the vestibule went on saying «Sign in to see the rooms you work
    in», with no name and no way out, WHILE the node map and the «amministrare»
    door were on the same screen — both of which only appear for a signed-in
    operator. A page contradicting itself about whether you are signed in is worse
    than a page that does not know.

    The cause: the vestibule stopped asking for the rooms listing (it has no list
    to draw) and the gate-closing, the name and the sign-out all hung off that
    request. The note says where they belong — «il vestibolo non possiede niente:
    compone /v1/node e /v1/whoami» — so the identity comes from `/v1/whoami` and
    the listing stays on the page that shows a listing.
    """
    assert "async function showWhoYouAre()" in CODE
    body = CODE[CODE.index("async function showWhoYouAre()"):]
    body = body[:body.index("\nasync function enter(")]
    assert 'request("GET", "/whoami")' in body, "the note's own endpoint"
    for element in ('$("gate")', '$("who")', '$("btn-signout")', '$("btn-signin")'):
        assert element in body, f"{element} is set from the identity answer"
    # …and the WAY IN is revealed by the same function that hides the gate: they
    # are two halves of one statement, and splitting them is how one got forgotten
    assert 'hidden = known ||' in body, (
        "the sign-in button used to hang off `showGate()`, which hangs off the "
        "rooms listing this door no longer asks for — so the unsigned vestibule "
        "named what was behind the gate and not how to pass it")
    # every door calls it: the vestibule directly, the work page through `enter`
    assert CODE.count("showWhoYouAre()") >= 3
