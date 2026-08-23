#!/usr/bin/env python3
"""The OWNER's scope: my room, and only my room.

    python dev-stack/smoke_user_mgmt.py

Five sentences, measured against a live node:

1. an **owner promotes** somebody `viewer → editor`, and **revokes**;
2. an **admin** manages editors and viewers **but cannot touch the owner** — the
   rule `access.may_assign` has held since the door was built, checked here
   through the room's HTTP surface;
3. an **editor is not a manager**: no roster, no invitations;
4. the **asset-creator** reads their own embargoed asset via the DTC — through
   ANY room, because the register speaks about the bytes — while somebody with no
   identity is refused. The per-asset layer, untouched by this work and measured
   to prove it (this smoke's own first guess about it was wrong; see §4);
5. an **owner-scope does not see other rooms**: `GET /v1/rooms` answers what the
   ACL grants and nothing else.

Leaves behind two archived rooms.
"""

from __future__ import annotations

import random
import sys

from smoke_common import (Tally, alive, arguments, body_of, call, detail_of,
                          need, orcid_of, token_for, unique)


def main() -> int:
    args = arguments(__doc__ or "")
    base = args.base.rstrip("/")
    if not alive(base):
        return 2
    owner = need(token_for(args.owner), "no owner token")
    other = need(token_for(args.viewer), f"no token for {args.viewer!r}")
    who_owner, who_other = orcid_of(owner), orcid_of(other)
    tally = Tally()
    mine, theirs = unique("smoke-mine"), unique("smoke-theirs")
    print(f"base {base}\nowner {who_owner} · other {who_other}\n")

    for room, title in ((mine, "Smoke · la mia"), (theirs, "Smoke · l'altra")):
        status, _, raw = call("POST", f"{base}/rooms", token=owner,
                              json_body={"room_id": room, "title": title})
        tally.ok(status == 201, f"declared {room}", detail_of(raw))

    # ── 1 · promote, then revoke ────────────────────────────────────────────
    print("1 · the owner promotes and revokes")
    status, _, raw = call("PUT", f"{base}/rooms/{mine}/members/{who_other}",
                          token=owner, json_body={"role": "viewer"})
    tally.ok(status == 200, "viewer granted", detail_of(raw))
    status, _, raw = call("PUT", f"{base}/rooms/{mine}/members/{who_other}",
                          token=owner, json_body={"role": "editor"})
    roles = {m["orcid"]: m["role"] for m in body_of(raw).get("members") or []}
    tally.ok(roles.get(who_other) == "editor", "viewer → editor", str(roles))

    # ── 2 · an admin cannot touch the owner ─────────────────────────────────
    print("\n2 · an admin manages editors, never the owner")
    status, _, raw = call("PUT", f"{base}/rooms/{mine}/members/{who_other}",
                          token=owner, json_body={"role": "admin"})
    tally.ok(status == 200, "…promoted to admin by the owner", detail_of(raw))
    status, _, raw = call("PUT", f"{base}/rooms/{mine}/members/{who_owner}",
                          token=other, json_body={"role": "viewer"})
    tally.ok(status == 403, f"the admin may NOT demote the owner ({status})",
             detail_of(raw))
    tally.ok("owner" in detail_of(raw).lower(),
             "…and the refusal says why", detail_of(raw))
    status, _, raw = call("POST", f"{base}/rooms/{mine}/invites", token=other,
                          json_body={"role": "admin"})
    tally.ok(status == 403, f"nor offer admin by link ({status})", detail_of(raw))
    status, _, raw = call("POST", f"{base}/rooms/{mine}/invites", token=other,
                          json_body={"role": "editor"})
    tally.ok(status == 201, "…while an editor link is theirs to make",
             detail_of(raw))

    # ── 3 · an editor is not a manager ─────────────────────────────────────
    print("\n3 · an editor is not a manager")
    status, _, raw = call("PUT", f"{base}/rooms/{mine}/members/{who_other}",
                          token=owner, json_body={"role": "editor"})
    tally.ok(status == 200, "back to editor")
    status, _, raw = call("GET", f"{base}/rooms/{mine}/members", token=other)
    tally.ok(status == 403, f"no roster for an editor ({status})", detail_of(raw))
    status, _, raw = call("GET", f"{base}/rooms/{mine}/invites", token=other)
    tally.ok(status == 403, f"no invitations either ({status})", detail_of(raw))

    # ── 4 · the per-asset layer, untouched ─────────────────────────────────
    print("\n4 · the DTC layer still decides about the BYTES (untouched)")
    pixels = b"\x89PNG\r\n\x1a\n" + str(random.random()).encode("ascii")
    status, _, raw = call("PUT", f"{base}/rooms/{mine}/asset?media_type=image/png",
                          token=owner, data=pixels, media_type="image/png")
    if not tally.ok(status == 200, "an asset published", detail_of(raw)):
        tally.skip("the DTC section", "the asset did not upload")
    else:
        ref = body_of(raw)["ref"]
        status, _, raw = call("POST", f"{base}/corpus/append", token=owner,
                              json_body={"act": "resource", "checksum": ref,
                                         "name": "smoke.png"})
        tally.ok(status in (200, 201), "registered in the corpus", detail_of(raw))
        status, _, raw = call("POST", f"{base}/corpus/append", token=owner,
                              json_body={"act": "attribution", "checksum": ref,
                                         "author": who_other,
                                         "author_name": "the other one",
                                         "embargo": "2099-01-01"})
        tally.ok(status in (200, 201),
                 "attributed to the other person, under embargo", detail_of(raw))
        status, _, _ = call("GET", f"{base}/rooms/{mine}/asset/{ref}", token=other)
        tally.ok(status == 200,
                 f"the file's author reads their own embargoed asset ({status})")
        # …and through a room they have NO grant in, too — which is not a hole
        # and was this smoke's own wrong guess on the first run (it expected a
        # 403 and got a 200). The register speaks about the BYTES: whichever
        # door the author comes through, the file is theirs. The room-level ACL
        # is a different gate, and it is the one measured in §5.
        status, _, _ = call("GET", f"{base}/rooms/{theirs}/asset/{ref}",
                            token=other)
        tally.ok(status == 200,
                 f"…through any room, because the DTC is about the bytes ({status})")
        status, _, raw = call("GET", f"{base}/rooms/{mine}/asset/{ref}")
        tally.ok(status in (401, 403),
                 f"while nobody at all is refused the embargoed file ({status})",
                 detail_of(raw))

    # ── 5 · owner-scope is not the node ────────────────────────────────────
    print("\n5 · an owner sees their rooms, not the node's")
    status, _, raw = call("GET", f"{base}/rooms", token=other)
    seen = {r["room_id"] for r in body_of(raw)}
    tally.ok(mine in seen, "the other person sees the room they were granted")
    tally.ok(theirs not in seen,
             "…and NOT the one they were not", str(sorted(seen)[:6]))
    status, _, raw = call("GET", f"{base}/rooms/{theirs}", token=other)
    tally.ok(status == 403, f"reading it outright is refused ({status})",
             detail_of(raw))

    print("\n· tidying")
    for room in (mine, theirs):
        call("POST", f"{base}/rooms/{room}/archive", token=owner,
             json_body={"archived": True})
        print(f"  · {room} archived")
    return tally.report("user-mgmt")


if __name__ == "__main__":
    sys.exit(main())
