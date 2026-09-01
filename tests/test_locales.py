"""Six languages on the two faces this server serves, and no locale with a hole.

English is the source language of every StratiGraph surface; beside it, the
languages of the project's case studies (T2.3) — `it` `ro` `el` `es` `pl` —
because those are the languages somebody will actually excavate in.

`en` and `it` are complete. The other four exist with **the same keys and empty
values**, which fall back to English. That is deliberate: **translating is the
partners' work**, each for their own language and their own dig, and a string
invented by us in a language none of us re-reads is worse than the English.

What this file defends is the SLOT:

* every locale carries every key — a hole is invisible, because the fallback
  shows English and nobody learns the string was never translated;
* the placeholders survive translation: a `{n}` lost is a sentence that stops;
* the three VERBS stay three different words, because they are the only thing
  telling a monument, a study and a room apart on screen;
* nothing that is a domain TERM was translated.

One dictionary for both faces, and it lives in `node_admin/` — the arrow this
codebase already draws (`rooms_ui` imports `../admin/auth.js`, not the reverse).
"""

from __future__ import annotations

import pathlib
import re

_UI = pathlib.Path(__file__).resolve().parent.parent / "app"
I18N = (_UI / "node_admin" / "i18n.js").read_text(encoding="utf-8")

EXPECTED = ("en", "it", "ro", "el", "es", "pl")
COMPLETE = ("en", "it")


def locales() -> dict:
    block = re.search(r"const STRINGS = \{(.*?)\n\};", I18N, re.S)
    assert block, "no STRINGS in the dictionary"
    found: dict = {}
    for match in re.finditer(r"^  (\w+): \{(.*?)^  \}", block.group(1), re.S | re.M):
        found[match.group(1)] = {
            key: value for key, value in
            re.findall(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', match.group(2))
        }
    for code in re.findall(r"(\w+): \{\},", block.group(1)):
        found.setdefault(code, {})
    return found


LOCALES = locales()


def test_the_six_locales_are_declared():
    assert tuple(LOCALES) == EXPECTED, tuple(LOCALES)


def test_the_complete_locales_have_every_key_and_no_empty_value():
    keys = set(LOCALES["en"])
    assert len(keys) > 50, f"only {len(keys)} keys — did the parser find them?"
    for code in COMPLETE:
        assert not keys - set(LOCALES[code]), sorted(keys - set(LOCALES[code]))
        empty = [k for k, v in LOCALES[code].items() if not v.strip()]
        assert not empty, f"{code} has empty values: {empty}"


def test_the_empty_locales_are_FILLED_IN_at_load_and_not_left_ragged():
    """The source declares them `{}`; the module normalises them against `en` so
    a translator opening the file sees the whole list and a test can count."""
    assert 'if (!(key in STRINGS[code])) STRINGS[code][key] = "";' in I18N


def test_the_placeholders_survive_translation():
    for key, source in LOCALES["en"].items():
        wanted = set(re.findall(r"\{(\w+)\}", source))
        for code in COMPLETE:
            value = LOCALES[code].get(key, "")
            if value:
                assert set(re.findall(r"\{(\w+)\}", value)) == wanted, f"{code}/{key}"


def test_the_verb_this_SERVER_still_shows_is_the_room_s():
    """There used to be three, and this is the shape of the 6 September cut.

    The three verbs — esplora · leggi · entra — told a monument, a study and a
    room apart on one page that showed all three. The pages then separated by
    verb: consultare went to the CATALOGUE (which has its own dictionary and its
    own test) and this server kept lavorare. So one verb, and asserting three
    here would be asserting two strings nothing renders.

    The verbs are still THREE DIFFERENT WORDS in the catalogue's dictionary; what
    moved is which file has to defend it.
    """
    for code in COMPLETE:
        assert LOCALES[code].get("rooms.verb"), code
    for gone in ("studies.verb", "hdt.verb", "studies.title", "hdt.title"):
        assert gone not in LOCALES["en"], (
            f"{gone} is back in this server's dictionary. If a page here shows "
            "published studies again, that is a decision to make in the design "
            "note first — the door doing four jobs is what the cut was for.")


def test_HDT_is_a_TERM_and_reaches_every_locale_as_one():
    """«Monuments» was the translation of a term, which the dictionary's own rule
    forbids: US, DTC, HDT, ORCID do not translate. So the vestibule's door names
    HDT, and it says HDT in all six languages.

    In four of them it says so BY FALLING BACK to English, which is the honest
    arrangement and not a gap: translating is the partners' work. What matters is
    that no locale can quietly put a translated «monumento» in its place —
    `test_no_domain_term_was_translated` is what stops that, and this is what
    gives it something to bite on.
    """
    source = LOCALES["en"]["go.consult"]
    assert "HDT" in source, source
    assert "onument" not in source.lower(), (
        "«monument» is back on the door: it was the translation of a term")
    for code in EXPECTED:
        shown = LOCALES[code].get("go.consult") or source     # the fallback
        assert "HDT" in shown, f"{code}: {shown}"


def test_no_domain_term_was_translated():
    for code in COMPLETE:
        for key, value in LOCALES[code].items():
            for term in ("DTC", "ORCID", "HDT", "em.json", "IIIF", "CIDOC"):
                if term in LOCALES["en"].get(key, ""):
                    assert term in value, f"{code}/{key} lost the term {term}"


def test_the_language_lives_on_the_DEVICE_and_the_token_does_not():
    """The locale is not a credential and it belongs to the device, not the
    person — like the field assistant's queue, unlike its token."""
    assert "localStorage.setItem(LOCALE_KEY" in I18N
    for name in re.findall(r"localStorage\.setItem\(([^,]+),", I18N):
        assert "token" not in name.lower(), name


def test_both_faces_read_the_SAME_dictionary():
    rooms = (_UI / "rooms_ui" / "rooms.js").read_text(encoding="utf-8")
    console = (_UI / "node_admin" / "console.js").read_text(encoding="utf-8")
    assert 'from "../admin/i18n.js"' in rooms
    assert 'from "./i18n.js"' in console


def test_coverage_report():
    """Not an assertion — the number that goes to the partners."""
    keys = len(LOCALES["en"])
    print(f"\n  locale coverage, room server ({keys} keys)")
    for code in EXPECTED:
        filled = sum(1 for v in LOCALES[code].values() if v.strip())
        print(f"    {code}  {filled:3}/{keys}"
              + ("  complete" if filled == keys else "  ← partners"))
