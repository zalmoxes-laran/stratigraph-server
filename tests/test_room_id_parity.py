"""One name, one room — measured across the TWO doors that derive a room id.

A person types a NAME; the id is a consequence, because two things that must
agree are two things that will one day disagree. But the derivation now happens
in two places:

* this node's front door — `app/rooms_ui/rooms.js::createRoom`;
* EMStudio's File menu — `frontend/src/rooms.ts::roomIdFromName` (the gesture
  added on 5 September 2026, so a room can be created without going back to the
  door).

Two doors deriving it differently would turn ONE name into TWO rooms, which is
exactly the ambiguity «one room, one live graph» exists to prevent — and it
would do so silently, because each door works perfectly on its own.

So the vector below is asserted HERE against the front door's JavaScript, and
the identical vector is asserted THERE by
`EMStudio/frontend/scripts/check-rooms.mjs` against the TypeScript. A change to
one side that is not made to the other turns a suite red in whichever repo the
author was not looking at.

WHY IT READS THE JAVASCRIPT rather than reimplementing the slug in Python: a
Python copy would be a THIRD derivation, and the third one is the one nobody
remembers to update. The algorithm is extracted from the source and evaluated by
node, so what is asserted is what the browser runs.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOMS_JS = (pathlib.Path(__file__).resolve().parent.parent
            / "app" / "rooms_ui" / "rooms.js")

#: KEEP IN STEP WITH `EMStudio/frontend/scripts/check-rooms.mjs` (`PARITY`).
PARITY: list[tuple[str, str]] = [
    ("Sarmizegetusa 1978", "sarmizegetusa-1978"),
    ("  Templu Mare  ", "templu-mare"),
    # an em dash is not a letter, so it collapses like any other run
    ("Aiano — Torraccia di Chiusi", "aiano-torraccia-di-chiusi"),
    # the slash MUST go: `create_room` refuses an id with one, because an id is
    # one path segment
    ("US 101 / US 102", "us-101-us-102"),
    ("Ercolano (2026)", "ercolano-2026"),
    ("Ostia Antica: Terme del Nuotatore", "ostia-antica-terme-del-nuotatore"),
    # a name of only punctuation derives NOTHING — and here the two doors
    # deliberately differ about what to do next: see the last test
    ("???", ""),
    ("", ""),
    # the SHARED QUIRK, pinned on purpose: the trim happens BEFORE the cut, so a
    # truncation at 60 can leave a trailing dash. Harmless — the only rules are
    # «one segment, no spaces» — but it must not differ between the doors. A
    # quirk both have is a quirk; a quirk one has is a bug.
    ("x" * 59 + " coda", "x" * 59 + "-"),
]


def _slug_expression() -> str:
    """The front door's derivation, lifted from its own source.

    Pinned to the shape of the line rather than to a copy of it: if somebody
    rewrites the derivation, this stops finding it and says so, which is the
    correct outcome — a parity test that silently tests nothing is worse than a
    failing one.
    """
    source = ROOMS_JS.read_text(encoding="utf-8")
    match = re.search(
        r"const room_id = (title\.toLowerCase\(\)[\s\S]{0,240}?);", source)
    assert match, ("the front door's room-id derivation is not where this test "
                   "looks for it (rooms.js, `const room_id = title.toLowerCase()"
                   "…`). If it moved, move this test with it — do not delete it.")
    expression = match.group(1)
    # the door's `|| \`room-${Date.now()…}\`` fallback is NOT part of the shared
    # derivation: EMStudio refuses an unusable name instead, deliberately, and
    # the last test below is what says so
    return expression.split("||")[0].strip()


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on this machine: the front door's own JavaScript "
                    "cannot be evaluated, and reimplementing it in Python would "
                    "be the third derivation this test exists to prevent")
    return node


def _derive(names: list[str]) -> list[str]:
    script = (
        "const out = JSON.parse(process.argv[1]).map((title) => "
        f"{_slug_expression()});\n"
        "process.stdout.write(JSON.stringify(out));"
    )
    done = subprocess.run([_node(), "-e", script, json.dumps(names)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_front_door_derives_what_emstudio_derives():
    names = [name for name, _ in PARITY]
    assert _derive(names) == [wanted for _, wanted in PARITY]


def test_no_derived_id_is_something_create_room_would_refuse():
    """`create_room` rejects an empty id and any id with a slash, a backslash or
    a space. A derivation that could produce one would be a name a person is
    allowed to type and a room that cannot be made — the refusal arriving from
    the server instead of from the field."""
    for name, wanted in PARITY:
        if not wanted:
            continue                      # the empty case is the next test
        assert not any(c in wanted for c in "/\\ "), (name, wanted)
        assert len(wanted) <= 60, (name, wanted)


def test_the_two_doors_differ_about_an_UNUSABLE_name_and_that_is_deliberate():
    """A name of only punctuation derives nothing, and the doors part company:

    * the front door falls back to `room-<timestamp>` — a convenience for
      somebody looking at the list they just added to;
    * EMStudio REFUSES and asks again, because there the room becomes the live
      copy of the document on screen, and being sent to work in `room-lz4f9k`
      is a worse outcome than one more keystroke.

    Pinned so the difference stays a decision. If the fallback ever leaves this
    file's sight, that is a change of behaviour on the door and not a tidy-up.
    """
    source = ROOMS_JS.read_text(encoding="utf-8")
    assert "|| `room-${Date.now().toString(36)}`" in source
    assert _derive(["???"]) == [""], "…and the derivation itself invents nothing"
