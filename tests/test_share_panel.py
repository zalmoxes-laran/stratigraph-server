"""«Condividi» stays small — and the test carries the reason, so whoever changes
it has to read why first.

The design note `EM_design_condividi-e-firma.md` (7 September 2026) separated two
jobs that had been one panel:

    1 · Presence     who may be at the table, and with what power   ← this panel
    2 · Property     whose the live copy is                         ← this panel
    3 · Contribution who did what, and how much                     ← NOT A DATUM
    4 · Authorship   the names on a published version, and order    ← the VERSION

The first two are operational and live on the node, beside the ACL and the owner.
The fourth is scientific and lives on the published version, in the catalogue,
agreed when publishing. **The third is not a field**: counting nodes would reward
whoever imports a GraphML of two hundred elements in one gesture and erase
whoever spends a week correcting forty — not a coarse approximation of merit but
ANOTHER THING, which resembles merit closely enough to be believed.

So the sharing surface must never name an author, an order of names, or a
scientific direction. Three consequences from the note, in its own words:

* being present is not having done — an author list populated from the ACL would
  be that claim, made by a default;
* an author need not be in the room: a director who never touched the graph is
  the normal case, so the publishing surface cannot validate names against the
  ACL and must not even suggest it;
* transferring a room transfers the operational job, not the signature.

A panel that asked «who signs?» would be asking somebody who is only adding a
colleague to the table.

WHAT THIS FILE IS NOT: a ban on the word «author» in this repository. The
narrative, the RDF projection and the catalogue all legitimately carry authors.
It is a fence around ONE surface.
"""

from __future__ import annotations

import pathlib
import re

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"
WORK = (_APP / "rooms_ui" / "work" / "index.html").read_text(encoding="utf-8")
SCRIPT = (_APP / "rooms_ui" / "rooms.js").read_text(encoding="utf-8")
I18N = (_APP / "node_admin" / "i18n.js").read_text(encoding="utf-8")

from tests import sorgenti  # noqa: E402

#: The words that would mean layer 3 or 4 had leaked into layer 1. Both
#: languages the dictionary completes, plus the ones a translator would reach
#: for — the four empty locales fall back to English, so English is the gate.
AUTHORSHIP = ("author", "autore", "autori", "authorship", "autorialità",
              "byline", "firma il", "signs", "credit", "crediti",
              "contributor", "contributo", "director", "direttore",
              "direzione scientifica", "first author", "primo autore",
              "corresponding")


def _share_strings() -> dict:
    """Every `share.*` string, in every locale the dictionary declares."""
    found = {}
    for locale in re.finditer(r"^  (\w+): \{(.*?)^  \}", I18N, re.S | re.M):
        code = locale.group(1)
        for key, value in re.findall(r'"(share\.[^"]+)":\s*"((?:[^"\\]|\\.)*)"',
                                     locale.group(2)):
            found[f"{code}/{key}"] = value
    return found


def _share_block() -> str:
    """The panel's own code, from `loadShare` to the end of its verbs."""
    start = SCRIPT.index("function askedRoom()")
    end = SCRIPT.index("// ── zone 4 · THE NODE MAP")
    return SCRIPT[start:end]


def test_the_panel_exists_at_all():
    """A fence around nothing would pass forever."""
    assert 'id="zone-share"' in WORK
    assert "async function loadShare(" in SCRIPT
    strings = _share_strings()
    assert len([k for k in strings if k.startswith("en/")]) > 20, len(strings)


def test_NO_STRING_ON_THIS_SURFACE_NAMES_AUTHORSHIP():
    """Layer 4 is agreed when publishing, on the version. A field here would ask
    it of somebody who is adding a colleague to a table."""
    offenders = []
    for key, value in _share_strings().items():
        low = value.lower()
        for word in AUTHORSHIP:
            if word in low:
                offenders.append(f"{key}: …{word}…")
    assert not offenders, (
        "the sharing panel names authorship: " + " · ".join(offenders)
        + ". Authors belong to the PUBLISHED VERSION and are agreed when "
        "publishing — see EM_design_condividi-e-firma.md, Regola III. If a "
        "publishing surface is what you are building, it is a different one.")


def test_NO_MARKUP_OR_CODE_ON_THIS_SURFACE_NAMES_AUTHORSHIP():
    """…and not in an id, a placeholder or a comment either: a field named
    `share-authors` would be the decision made in a variable name."""
    # ── AL CONFINE DI PAROLA, e il perché ci si ferma lì ───────────────────
    #
    # Questa guardia guarda **le parole**, ed è giusto che lo faccia: quello che
    # deve impedire è che questa superficie NOMINI l'autorialità — in una
    # etichetta, in un id, in un nome di variabile. Non c'è una struttura da
    # guardare, perché il difetto è precisamente il nome.
    #
    # Ma cercava una sottostringa, e in questo stesso file `Authorization` sta a
    # tre righe dal blocco recintato (`rooms.js:97`, l'intestazione del bearer).
    # Passava per FORTUNA: la fetta comincia dopo. Un giorno la fetta si sposta
    # di dieci righe e la suite dice che il pannello firma le pubblicazioni.
    #
    # `\bauthor\b` non tocca `Authorization`, `\bcredit\b` non tocca
    # `accreditato`. È il minimo sindacale — non distingue un identificatore da
    # una parola in una stringa — e per questa guardia il minimo è la cosa
    # giusta, perché una parola in una stringa È il difetto.
    panel = WORK[WORK.index('id="zone-share"'):]
    panel = panel[:panel.index("</section>")]
    for word in AUTHORSHIP:
        assert not sorgenti.parola(word).search(panel), \
            f"the markup names «{word}»"
    code = sorgenti.senza_prosa(_share_block())
    for word in ("author", "autore", "byline", "credit", "contributor"):
        assert not sorgenti.parola(word).search(code), \
            f"the panel's code names «{word}»"


def test_LA_GUARDIA_DELLAUTORIALITA_MORDE_ANCORA():
    """Prova 1 di 2: il caso vero — un nome che decide, in un id o in una
    variabile."""
    for finto in ('<div id="share-authors"></div>',
                  "const authors = roster.members.map(m => m.orcid);",
                  '<label>Autore principale</label>',
                  "// chi firma: byline",
                  "const credit = 'direzione scientifica';"):
        morde = [w for w in ("author", "autore", "byline", "credit",
                             "contributor")
                 if sorgenti.parola(w).search(finto)]
        assert morde, f"non morde su: {finto}"


def test_LA_GUARDIA_DELLAUTORIALITA_NON_MORDE_PIU_UNA_PAROLA_DENTRO_UNALTRA():
    """Prova 2 di 2: il falso positivo, ed è nel file vero a tre righe dal
    recinto."""
    onesti = ("headers: { Authorization: `Bearer ${token}` }",
              "authConfig.authorization_endpoint",
              "const chi = 'utente accreditato';",
              "await oidc.authorize(cfg);")
    for finto in onesti:
        morde = [w for w in ("author", "autore", "byline", "credit",
                             "contributor")
                 if sorgenti.parola(w).search(finto)]
        assert not morde, f"morde ancora su: {finto} → {morde}"


def test_the_panel_does_not_read_the_ACL_TO_SUGGEST_ANYTHING_BUT_ACCESS():
    """«Chi è fra gli autori non deve essere nella stanza» — so nothing here may
    turn the roster into a list of names for anything else. What it does with the
    roster is draw roles and offer revocation, and that is the whole of it."""
    code = _share_block()
    assert "roster.members" in code and "roster.groups" in code
    # the only things a row offers are access verbs
    verbs = set(re.findall(r't\("share\.(add|addTeam|remove|revoke|transfer|'
                           r'makeLink|archiveDo|restoreDo)"\)', code))
    assert verbs, "the panel offers no verbs at all?"
    assert not re.search(r'share\.(publish|sign|author|order)', code)


def test_the_panel_covers_LAYERS_ONE_AND_TWO_completely():
    """A fence is only worth having around something whole. Presence AND
    property, or the panel is not the one place the note asks for and somebody
    will build the missing half somewhere else."""
    code = _share_block()
    for what, needle in (
            ("who is here", "renderWho("),
            ("teams, not only people", "renderTeams("),
            ("invite by link", "renderInvites("),
            ("whose the live copy is", "renderLive("),
            ("archive, which is a mark and not a deletion", "renderArchive("),
            ("the transfer", "async function transfer(")):
        assert needle in code, f"the panel does not cover {what}"


def test_the_panel_has_a_STABLE_PER_ROOM_ADDRESS():
    """Regola IV: one place, and a link from every app. Another program must be
    able to write that URL into a menu and know it will work — so the address is
    derived in ONE function and the room travels in the query string."""
    code = _share_block()
    assert "function shareUrl(roomId)" in code
    assert "function askedRoom()" in code
    assert 'get("room")' in code
    # …and the room card links to it rather than opening a second copy
    assert "shareUrl(room.room_id)" in SCRIPT


def test_the_panel_OWNS_NO_RULE_AND_NO_ADDRESS():
    """The discipline of this whole face, applied to the newest zone: the node
    decides and the page composes. Measured the hard way — the first version of
    `renderLive` gated the transfer on `your_role === "owner"`, and
    `test_the_page_holds_no_visibility_rule` refused it."""
    # COMMENTS STRIPPED, and it is not pedantry: the comment that explains why
    # the removed line was removed contains the removed line, so a test reading
    # the prose fails on the explanation. Third time this class of self-trip has
    # come up in this repository — hence the stripper, here as elsewhere.
    code = re.sub(r"//[^\n]*", "", _share_block())
    assert not re.search(r"your_role\s*===", code), \
        "a permission rule written in the page is a rule with two answers"
    # the invitation's door comes from the node, never spelled here
    assert "/open`" in code, "the handoff URL is asked for"
    assert "stratigraph://" not in code, "…and never assembled"
    assert "/em/studio" not in code, "…nor is any tool's address written down"


def test_the_REFUSALS_are_the_NODE_S_OWN_WORDS():
    """Every one of these refusals explains a rule — «a group cannot be the
    owner», «only the owner assigns owner or admin», «this room cannot come back
    yet». Re-writing them here would be a second explanation, and the second one
    is the one that goes stale."""
    code = _share_block()
    for handler in ("share-group-note", "share-add-note", "share-note"):
        assert f'note($("{handler}"), error.message, true)' in code, handler
