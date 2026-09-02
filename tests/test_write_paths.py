"""One arbiter AND one save, whatever the transport.

## The fact this file exists for

`room.apply()` — the arbiter, the one that hands an operation to `em.apply_op` —
had **two** callers and only one of them carried the rest of the contract:

    app/ws.py    the socket, inside `async with room.lock`
    app/main.py  POST /v1/photogrammetry, inside a thread, without the lock

The second door was born with the right arbiter and none of the rest.
`app/photogrammetry.py` contained no `snapshot`, no `request_save`, no
`SNAPSHOT_STORE`, no `fanout` — zero occurrences of each — and after applying its
delta the job set `progress = 100.0` and `status = "done"`.

Measured on 9 September 2026, with a snapshot store behind the room (the existing
fixture deliberately had none, which is why nothing had ever noticed):

* the store held **nothing** for that room — the model, its genesis event and its
  placement lived in a dictionary that dies with the process;
* **zero** operations were announced — whoever was in EMStudio had to reopen the
  room, with no way to know they should;
* the job said `done` with `ops_applied: N`. The failure that looks like a
  success, with a number confirming it.

And a third, which is why the obvious repair was forbidden: `room.lock` is an
`asyncio.Lock` and that code ran in a `threading.Thread`. An async lock does not
exclude a thread, so adding `snapshot()` there would have bought persistence at
the price of making the race likelier and more destructive — `em.compact` walks
the same nested structures `em.apply_op` mutates.

## What is gated here, and how

**Every path that writes into a graph goes through the one place that applies,
keeps and announces.** That place is `app/ws.py`: it owns `room.lock`, it owns
`SNAPSHOT_STORE`, and it owns `_fanout`. So the rule is checkable as a location:
`room.apply` and `room.snapshot` are called from `ws.py` and from nowhere else.

The callers are FOUND IN THE CODE, not listed here — the same shape this
repository already uses twice (`test_room_id_parity.py` reads the front door's
JavaScript; `test_every_archive_endpoint_goes_THROUGH_the_registry` reads
`main.py`). A list in this file would be the second list, and the second one is
the one that goes stale in the direction of passing.

## WHAT THIS DOES NOT CLAIM

The socket does **not** persist every operation, and that is deliberate rather
than a hole: a relay that wrote the room on every keystroke would rewrite a study
while somebody is typing in it. Its route to durability is `request_save`, which
the client asks for and EMStudio does ask for. What the connector door needed is
different because there is no client to ask: the job finishes and nobody is left
to say «keep it».

So the assertions below are about REACHING the contract, not about saving on
every op. A future transport that writes and neither keeps nor announces is what
this file refuses.
"""

from __future__ import annotations

import pathlib
import re
from typing import Dict

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _module(name: str) -> str:
    return (_APP / name).read_text(encoding="utf-8")


def _code(source: str) -> str:
    """Source without comments or docstrings — because every one of these
    assertions is about what RUNS, and this file's own prose names the very
    symbols it forbids (the fourth self-trip of the week: a test that reads the
    explanation fails on it)."""
    without_prose = re.sub(r'"""[\s\S]*?"""', '""', source)
    return re.sub(r"#[^\n]*", "", without_prose)


def _callers(symbol: str) -> Dict[str, int]:
    """Which module calls `room.<symbol>(`, and how often. Derived, not listed."""
    found: Dict[str, int] = {}
    for path in sorted(_APP.glob("*.py")):
        hits = len(re.findall(rf"\b\w+\.{symbol}\(", _code(path.read_text(encoding="utf-8"))))
        if hits:
            found[path.name] = hits
    return found


def test_the_ARBITER_is_called_from_one_module_only():
    """`room.apply` is where an operation becomes part of a graph. It lives beside
    the lock that protects it, the store that keeps it and the fanout that tells
    the room — so it is called from there, or the contract is optional."""
    callers = _callers("apply")
    assert "ws.py" in callers, "the socket's own call is gone?"
    stray = {name: n for name, n in callers.items() if name != "ws.py"}
    assert not stray, (
        f"{stray} calls the arbiter from outside `ws.py`. That is how the "
        "photogrammetry door came to apply operations without keeping or "
        "announcing them, for as long as it took somebody to notice by reading. "
        "Use `ws.apply_from_connector(room, ops, source=…)`: it takes the lock, "
        "applies, keeps and announces, and a transport written next month gets "
        "all four by calling it.")


def test_the_SAVE_is_called_from_one_module_only():
    """One save, whatever the transport. `room.snapshot` compacts and writes; two
    call sites is two policies about when a study is rewritten, and the second
    one is the one nobody measured."""
    callers = _callers("snapshot")
    stray = {name: n for name, n in callers.items() if name != "ws.py"}
    assert not stray, (
        f"{stray} writes a room from outside `ws.py`. `snapshot` COMPACTS before "
        "it writes, so a call from a thread races `em.apply_op` on the event "
        "loop over the same nested structures — the obvious repair that was "
        "forbidden on 9 September, and the reason `apply_from_connector` exists.")
    assert callers.get("ws.py", 0) >= 1


def test_the_one_place_APPLIES_KEEPS_and_ANNOUNCES():
    """A fence around a function that only did one of the three would pass while
    the defect stayed. So the function is read, and all three have to be in it."""
    ws = _module("ws.py")
    assert "async def apply_from_connector(" in ws
    start = ws.index("async def apply_from_connector(")
    body = ws[start:ws.index("\nasync def ", start + 10)]
    code = _code(body)
    assert "async with room.lock" in code, "the lock, taken on the loop"
    assert "room.apply(" in code, "the arbiter"
    assert "room.record(" in code, "…and the op recorded, so a re-sync replays it"
    assert "room.snapshot(SNAPSHOT_STORE)" in code, "KEPT"
    assert "_fanout(" in code, "ANNOUNCED"
    # …and in that order: a snapshot taken between two operations of one delta
    # would be a graph nobody wrote
    assert code.index("room.snapshot(") > code.index("room.apply("), \
        "kept after applied, inside one lock"
    assert code.index("_fanout(") > code.index("room.snapshot("), \
        "announced after kept — a client told about an op that was not written " \
        "would be a client ahead of the node"


def test_the_CONNECTOR_hands_its_delta_to_the_LOOP_and_waits():
    """The thread must not touch the room, and must not report `done` before the
    loop has said the delta landed. Both halves are the repair."""
    main = _code(_module("main.py"))
    assert "asyncio.run_coroutine_threadsafe(" in main, \
        "the delta is handed to the loop, not applied in the thread"
    assert "asyncio.get_running_loop()" in main, \
        "…and the loop is captured while the endpoint is ON it: a thread cannot " \
        "find a running loop by asking, because there is none on that thread"
    assert re.search(r"future\.result\(timeout=\w+\)", main), \
        "…and the thread WAITS, bounded: a job that reports `done` before the " \
        "save is the defect this repair is about, and a thread blocked forever " \
        "on a dead loop is a job that never reports either way"


def test_the_JOB_does_not_say_DONE_when_only_half_landed():
    """The code already knew how to say this — it is the branch that reports the
    bytes being in the store and the graph not written. What changed is that the
    branch can now be REACHED, because the applier raises instead of quietly
    finishing."""
    pg = _module("photogrammetry.py")
    assert "the model was produced and stored but the graph was " in pg
    body = pg[pg.index("            apply_delta(job, run)"):]
    branch = body[:body.index("job.status = \"done\"")]
    assert 'job.status = "failed"' in branch, \
        "an exception from the applier must fail the job, not be swallowed"
    assert branch.index("except Exception") < branch.index('job.status = "failed"')
    # …and `done` comes AFTER the applier, so nothing can report success first
    assert pg.index("apply_delta(job, run)") < pg.index('job.status = "done"')


def test_a_JOB_IS_NOT_A_PERSON_and_does_not_enter_the_roster():
    """Out of scope for the repair and worth pinning while nearby: presence is
    for people. A connector that appeared in the roster would put a machine in a
    list somebody reads to know who they are working with."""
    ws = _code(_module("ws.py"))
    start = ws.index("async def apply_from_connector(")
    body = ws[start:ws.index("\nasync def ", start + 10)]
    assert "presence" not in body and "room.members" not in body, \
        "the connector touches the roster"
    assert "skip=" not in body, \
        "there is no origin to skip: a job is not a member, so nobody in the " \
        "room has already seen these operations"
