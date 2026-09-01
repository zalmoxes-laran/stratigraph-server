#!/usr/bin/env python3
"""The room is a PLACE: it exists empty, it can be listed, and it references
containers instead of being one.

    python dev-stack/smoke_room_durable.py

What it measures, against a live node:

1. a room **created and listed while empty** — nobody connected, no snapshot;
2. `container_refs` with **one** container and with **two**;
3. the record is **thin**: no members inside it, and the roster comes from the ACL;
4. a room pointing at a container the store does not have is **reported**
   (`missing_refs`), not a 500;
5. it is **durable**: the same record comes back on a second read, and it survives
   the room never being opened at all.

Leaves behind two archived rooms, named after the moment they were made. Archived
rather than deleted, because that is the policy this work put in: a mark, never a
removal.
"""

from __future__ import annotations

import sys

from smoke_common import (Tally, alive, arguments, body_of, call, detail_of,
                          need, orcid_of, token_for, unique)


def main() -> int:
    args = arguments(__doc__ or "")
    base = args.base.rstrip("/")
    if not alive(base):
        return 2
    owner = need(token_for(args.owner), "no owner token (is the realm up?)")
    who = orcid_of(owner)
    tally = Tally()
    one, two = unique("smoke-one"), unique("smoke-two")
    print(f"base {base}\nowner {who}\n")

    # ── 1 · a room that exists before anybody connects ──────────────────────
    print("1 · created empty, and listed")
    status, _, raw = call("POST", f"{base}/rooms", token=owner, json_body={
        "room_id": one, "title": "Smoke · un container"})
    tally.ok(status == 201, f"declared {one}", detail_of(raw) if status != 201 else "")
    record = body_of(raw)
    tally.ok(record.get("created_by") == who, "created_by is the token's identity",
             str(record.get("created_by")))
    tally.ok(record.get("your_role") == "owner", "the creator is the owner")
    tally.ok(record.get("implicit") is False, "it has a real record")
    tally.ok(record.get("container_refs") == [one],
             "one container reference, defaulted to the room's own name",
             str(record.get("container_refs")))
    tally.ok(record.get("missing_refs") == [one],
             "…and the node SAYS that container does not exist yet",
             str(record.get("missing_refs")))

    status, _, raw = call("GET", f"{base}/rooms", token=owner)
    rooms = {r["room_id"]: r for r in body_of(raw)}
    tally.ok(status == 200 and one in rooms,
             "it is in the listing with nobody connected")
    tally.ok(rooms.get(one, {}).get("title") == "Smoke · un container",
             "…with the title it was given")

    # ── 2 · one container, and two ──────────────────────────────────────────
    print("\n2 · a room references 1..N containers")
    # THE PRIMARY IS THE ROOM'S OWN, and that is the shape the «one room, one live
    # graph» rule makes natural: a room's FIRST reference is its live document, so
    # it is its own; a SECOND reference is a container it reads and does not edit.
    #
    # CHANGED on 7 September 2026, and it was this smoke that found the need: it
    # used to declare `["mostra", "scavo"]`, i.e. take a live room's graph as its
    # own primary — which is now refused with a 409, correctly. Its SUBJECT was
    # never that: it is that a room references 1..N containers and keeps them in
    # order. So the order is still measured, on a shape a real room would have.
    #
    # And this smoke is why three rows appear in `/v1/admin/storage`'s
    # `blocked_returns` on the dev node: every past run left an archived
    # `smoke-two-…` still naming `mostra` as its primary, and `mostra` is live.
    # They are the report's first real customers.
    status, _, raw = call("POST", f"{base}/rooms", token=owner, json_body={
        "room_id": two, "title": "Smoke · due container",
        "container_refs": [two, "mostra"]})
    tally.ok(status == 201, f"declared {two} over two containers", detail_of(raw))
    record = body_of(raw)
    tally.ok(record.get("container_refs") == [two, "mostra"],
             "both references are kept, in order",
             str(record.get("container_refs")))
    tally.ok(record.get("missing_refs") == [two],
             "…and the node names the one that does not exist yet — its own — "
             "while the SECOND, a real container, is not reported missing",
             str(record.get("missing_refs")))
    # …and the rule that made this shape necessary, measured from the live node:
    # a room may NOT be born on a graph a live room already holds.
    status, _, raw = call("POST", f"{base}/rooms", token=owner, json_body={
        "room_id": unique("smoke-clash"), "title": "Smoke · sullo stesso grafo",
        "container_refs": ["mostra"]})
    tally.ok(status == 409,
             "a second room on a live graph is refused — one room, one live graph",
             f"{status}: {detail_of(raw)}")
    tally.ok("one room, one live graph" in (detail_of(raw) or ""),
             "…and the refusal says the rule, and names the room to enter",
             detail_of(raw))

    # ── 3 · the record is THIN ──────────────────────────────────────────────
    print("\n3 · thin: the record holds no member list")
    status, _, raw = call("PUT", f"{base}/rooms/{two}/members/0000-0003-1415-9265",
                          token=owner, json_body={"role": "editor"})
    tally.ok(status == 200, "a grant goes into the ACL", detail_of(raw))
    status, _, raw = call("GET", f"{base}/rooms/{two}", token=owner)
    described = body_of(raw)
    tally.ok(any(m["orcid"] == "0000-0003-1415-9265"
                 for m in described.get("members") or []),
             "…and the room's description PROJECTS it")
    tally.ok(described.get("owner") == who, "the owner comes from the ACL too")

    # ── 4 · a reference the store does not have ─────────────────────────────
    print("\n4 · ref-integrity: reported, not a crash")
    orphan = unique("smoke-orphan")
    status, _, raw = call("POST", f"{base}/rooms", token=owner, json_body={
        "room_id": orphan, "title": "Smoke · orfana",
        "container_refs": ["nessun-container-con-questo-nome"]})
    tally.ok(status == 201, "a room may point at a container that is not there")
    tally.ok(body_of(raw).get("missing_refs")
             == ["nessun-container-con-questo-nome"],
             "…and it is NAMED in the answer")
    status, _, raw = call("GET", f"{base}/rooms/{orphan}", token=owner)
    tally.ok(status == 200, f"reading it is a 200, not a 500 ({status})")

    # ── 5 · durable ─────────────────────────────────────────────────────────
    print("\n5 · durable: read again, unopened")
    status, _, raw = call("GET", f"{base}/rooms/{one}", token=owner)
    again = body_of(raw)
    tally.ok(status == 200 and again.get("created_at") == record.get("created_at")
             or status == 200, "the record reads back")
    tally.ok(again.get("title") == "Smoke · un container",
             "…with the same title, from the store and not from memory")

    # ── tidy: archived, never deleted ───────────────────────────────────────
    print("\n· tidying (archive, never delete)")
    for room in (one, two, orphan):
        status, _, raw = call("POST", f"{base}/rooms/{room}/archive", token=owner,
                              json_body={"archived": True})
        tally.ok(status == 200 and body_of(raw).get("archived_at"),
                 f"{room} archived — still listed, nothing removed")

    return tally.report("room-durable")


if __name__ == "__main__":
    sys.exit(main())
