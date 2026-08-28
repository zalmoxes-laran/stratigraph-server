#!/usr/bin/env python3
"""The `.blend` safety archive, end to end against the LIVE node.

Not a unit test: this drives **EMtools' own client** (`sync_manager/room.py`,
loaded headless from the sibling checkout) against a real StratiGraph Server with a real
MinIO behind it. What it measures is the chain nobody can measure in isolation —
that the bytes leave Blender's side, land in the backup namespace and not among
the publishable assets, dedup on content, and come back byte-identical.

If the EMtools checkout is not beside this one it says so and measures the
server's half only, rather than pretending it ran.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from smoke_common import (Tally, alive, arguments, body_of, call,  # noqa: E402
                          detail_of, need, orcid_of, token_for, unique)

BLEND = b"BLENDER-v405RENDH" + b"\x00opaque bytes, nobody parses these\x00" * 300
CHANGED = BLEND + b"one more object after the cut"


def emtools_room():
    """EMtools' room client, loaded by path (its package `__init__` needs bpy)."""
    here = pathlib.Path(__file__).resolve().parent.parent.parent
    source = here / "EM-blender-tools" / "sync_manager" / "room.py"
    if not source.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_emtools_room", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    args = arguments(__doc__ or "")
    base = args.base.rstrip("/")
    if not alive(base):
        return 2
    token = need(token_for(args.owner), "no owner token")
    who = orcid_of(token)
    tally = Tally()
    room_id = unique("smoke-blend")
    print(f"base {base}\nas {who} · room {room_id}\n")

    # ── 0 · a room to keep them in ──────────────────────────────────────────
    status, _, raw = call("POST", f"{base}/rooms", token=token,
                          json_body={"room_id": room_id,
                                     "title": "blend backup smoke"})
    tally.ok(status in (200, 201), f"a room to work in ({status})",
             detail_of(raw))

    # ── 1 · the server's half ───────────────────────────────────────────────
    print("\n1 · the node: archive · dedup · list · restore")
    status, _, raw = call("PUT",
                          f"{base}/rooms/{room_id}/blend-backup"
                          f"?label=prima%20del%20taglio&filename=scavo.blend",
                          token=token, data=BLEND,
                          media_type="application/x-blender")
    kept = body_of(raw) or {}
    sha = hashlib.sha256(BLEND).hexdigest()
    tally.ok(status == 200 and kept.get("sha256") == sha,
             f"a snapshot is kept, addressed by its own sha256 ({status})",
             f"{str(kept.get('sha256'))[:16]}… · {kept.get('size')} bytes")
    tally.ok(kept.get("created") is True, "…and it is new")
    tally.ok(kept.get("label") == "prima del taglio"
             and kept.get("orcid") == who and kept.get("created_at"),
             "…with the label, the author and the date",
             f"{kept.get('label')} · {kept.get('orcid')} · {kept.get('created_at')}")
    note = kept.get("dtc") or {}
    tally.ok(note.get("kind") == "backup" and note.get("publishable") is False,
             "…and a DTC note of kind «backup», declared unpublishable",
             str(note))

    status, _, raw = call("PUT", f"{base}/rooms/{room_id}/blend-backup?label=again",
                          token=token, data=BLEND,
                          media_type="application/x-blender")
    twice = body_of(raw) or {}
    tally.ok(status == 200 and twice.get("created") is False,
             "the SAME bytes again → no new object (dedup)",
             str(twice.get("detail"))[:70])
    tally.ok(twice.get("created_at") == kept.get("created_at")
             and twice.get("label") == kept.get("label"),
             "…and the first record is not rewritten (date and label hold)")

    status, _, raw = call("PUT", f"{base}/rooms/{room_id}/blend-backup?label=dopo",
                          token=token, data=CHANGED,
                          media_type="application/x-blender")
    changed = body_of(raw) or {}
    tally.ok(changed.get("created") is True
             and changed.get("sha256") == hashlib.sha256(CHANGED).hexdigest(),
             "a CHANGED .blend → a new snapshot")

    status, _, raw = call("GET", f"{base}/rooms/{room_id}/blend-backups",
                          token=token)
    listing = body_of(raw) or []
    tally.ok(status == 200 and len(listing) == 2,
             f"the list shows both snapshots ({len(listing)})",
             " · ".join(f"{r.get('label')}/{str(r.get('sha256'))[:8]}"
                        for r in listing))

    status, _, raw = call("GET", f"{base}/rooms/{room_id}/blend-backup/{sha}",
                          token=token)
    tally.ok(status == 200 and raw == BLEND,
             f"restore returns the EXACT bytes ({len(raw or b'')} B)")
    tally.ok(hashlib.sha256(raw or b"").hexdigest() == sha,
             "…verified by hashing what came back")

    # ── 2 · the shared data is untouched ────────────────────────────────────
    print("\n2 · nothing happened to the publishable half")
    status, _, raw = call("GET", f"{base}/rooms/{room_id}/asset/sha256:{sha}",
                          token=token)
    tally.ok(status == 404,
             f"the snapshot is NOT reachable as a publishable asset ({status})",
             "a backup in the assets' namespace would put the rights gate in "
             "charge of somebody's work in progress")

    # ── 3 · the door ────────────────────────────────────────────────────────
    print("\n3 · fail-closed")
    status, _, _ = call("GET", f"{base}/rooms/{room_id}/blend-backups")
    tally.ok(status == 401, f"no token → no listing ({status})")
    stranger = token_for(args.operator) if args.operator else None
    if stranger and orcid_of(stranger) != who:
        status, _, raw = call("GET", f"{base}/rooms/{room_id}/blend-backups",
                              token=stranger)
        tally.ok(status == 403, f"a stranger to the room is refused ({status})",
                 detail_of(raw))
    else:
        print("  · (no second identity given: pass --operator to measure the "
              "stranger's refusal)")

    # ── 4 · EMtools' own client, against this node ──────────────────────────
    print("\n4 · EMtools' client (sync_manager/room.py), for real")
    emtools = emtools_room()
    if emtools is None:
        print("  · EM-blender-tools is not beside this checkout — the client "
              "half was NOT measured")
    else:
        emtools.set_room(base.rsplit("/v1", 1)[0], room_id, token=token)
        record = emtools.put_blend_backup(BLEND, label="from blender",
                                          filename="scavo.blend")
        tally.ok(record.get("sha256") == sha and record.get("created") is False,
                 "EMtools archives the same bytes → recognised, not duplicated",
                 str(record.get("detail"))[:60])
        back = emtools.get_blend_backup(sha)
        tally.ok(back == BLEND,
                 "EMtools restores byte-identical (digest verified client-side)")
        mine = emtools.list_blend_backups()
        tally.ok(len(mine) == 2, f"EMtools lists {len(mine)} snapshot(s)")
        emtools.set_room(None, None)
        emtools.forget_token()

    # leave the room archived, like the other smokes
    call("POST", f"{base}/admin/rooms/{room_id}/archive", token=token,
         json_body={"archived": True, "confirm_room_id": room_id})
    return tally.report("blend-backup")


if __name__ == "__main__":
    sys.exit(main())
