#!/usr/bin/env python3
"""The link is the invitation, the ORCID is the identity, the ACL is the role.

    python dev-stack/smoke_invite_join.py

Each of the three does one job, and the failure of any one of them is a refusal
rather than a hole. Measured, in order:

1. an **editor link** minted by the owner (the token is shown **once**);
2. the **viewer user** follows it → in, as editor, and the ACL says so;
3. **no identity** → 401. A link opens a door; it does not answer for you;
4. **revoked** → 403 with the state, and nothing was granted;
5. **wrong secret** and **unknown token** → the *same* refusal, so a guessed id
   teaches nobody anything;
6. **used up** → a one-use link stops being a way in after one person;
7. and a link **never demotes**: an editor who follows a viewer link stays an
   editor.

Leaves behind one archived room.
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
    owner = need(token_for(args.owner), "no owner token")
    guest = need(token_for(args.viewer), f"no token for {args.viewer!r}")
    who_owner, who_guest = orcid_of(owner), orcid_of(guest)
    tally = Tally()
    room = unique("smoke-invite")
    print(f"base {base}\nowner {who_owner} · guest {who_guest}\nroom {room}\n")

    status, _, raw = call("POST", f"{base}/rooms", token=owner,
                          json_body={"room_id": room, "title": "Smoke · inviti"})
    if not tally.ok(status == 201, "room declared", detail_of(raw)):
        return tally.report("invite-join")

    # ── 1 · the link ────────────────────────────────────────────────────────
    print("1 · a link that offers `editor`")
    status, _, raw = call("POST", f"{base}/rooms/{room}/invites", token=owner,
                          json_body={"role": "editor"})
    invite = body_of(raw)
    tally.ok(status == 201, "minted", detail_of(raw) if status != 201 else "")
    token = invite.get("token")
    tally.ok(bool(token), "the token is in the answer — once")
    tally.ok(invite.get("state") == "live", "and it is live")

    status, _, raw = call("GET", f"{base}/rooms/{room}/invites", token=owner)
    listed = body_of(raw)
    tally.ok(all(entry.get("token") in (None, "") for entry in listed),
             "listing NEVER shows the token again (the node keeps a digest)")
    tally.ok(all("secret_digest" not in entry for entry in listed),
             "…and not even the digest travels to a client")

    # ── 2 · the guest walks through it ──────────────────────────────────────
    print("\n2 · the guest follows the link, with their own ORCID")
    status, _, raw = call("POST", f"{base}/join", token=guest,
                          json_body={"token": token})
    joined = body_of(raw)
    tally.ok(status == 200, f"in ({status})", detail_of(raw))
    tally.ok(joined.get("role") == "editor", "…as editor, the role the link offered")
    tally.ok(joined.get("already_had") is False, "…and it was a new grant")

    status, _, raw = call("GET", f"{base}/rooms/{room}/members", token=owner)
    members = {m["orcid"]: m["role"] for m in body_of(raw).get("members") or []}
    tally.ok(members.get(who_guest) == "editor",
             "THE ACL is what holds the role now", str(members))

    # ── 3 · no identity ─────────────────────────────────────────────────────
    print("\n3 · a link is not a credential")
    status, _, raw = call("POST", f"{base}/join", json_body={"token": token})
    tally.ok(status == 401, f"without a token: 401 ({status})", detail_of(raw))

    # ── 4/5 · what a refusal looks like ─────────────────────────────────────
    print("\n4 · wrong secret and unknown token are the SAME refusal")
    token_id = invite.get("token_id")
    _, _, wrong = call("POST", f"{base}/join", token=guest,
                       json_body={"token": f"{token_id}.sbagliato"})
    _, _, unknown = call("POST", f"{base}/join", token=guest,
                         json_body={"token": "deadbeefdeadbeef.sbagliato"})
    tally.ok(detail_of(wrong) == detail_of(unknown),
             "identical sentences", detail_of(wrong))

    print("\n5 · revoked, and it stops being a way in")
    status, _, raw = call("DELETE", f"{base}/rooms/{room}/invites/{token_id}",
                          token=owner)
    tally.ok(status == 200 and body_of(raw).get("state") == "revoked",
             "revoked, and the record survives it")
    tally.ok(body_of(raw).get("uses") == 1 and who_guest in
             (body_of(raw).get("accepted_by") or []),
             "…still saying who it let in", str(body_of(raw).get("accepted_by")))
    status, _, raw = call("POST", f"{base}/join", token=guest,
                          json_body={"token": token})
    tally.ok(status == 403 and "revoked" in detail_of(raw),
             f"and the link is refused with its state ({status})", detail_of(raw))

    # ── 6 · one use ─────────────────────────────────────────────────────────
    print("\n6 · a one-use link is used once")
    status, _, raw = call("POST", f"{base}/rooms/{room}/invites", token=owner,
                          json_body={"role": "viewer", "max_uses": 1})
    once = body_of(raw).get("token")
    status, _, raw = call("POST", f"{base}/join", token=guest,
                          json_body={"token": once})
    tally.ok(status == 200, "the guest may follow it")
    status, _, raw = call("POST", f"{base}/join", token=guest,
                          json_body={"token": once})
    tally.ok(status == 403 and "used up" in detail_of(raw),
             f"…and then it is used up ({status})", detail_of(raw))

    # ── 7 · never demotes ───────────────────────────────────────────────────
    print("\n7 · a link never takes rights away")
    status, _, raw = call("POST", f"{base}/rooms/{room}/invites", token=owner,
                          json_body={"role": "viewer"})
    lesser = body_of(raw).get("token")
    status, _, raw = call("POST", f"{base}/join", token=guest,
                          json_body={"token": lesser})
    answer = body_of(raw)
    tally.ok(answer.get("role") == "editor" and answer.get("already_had") is True,
             "the editor who follows a viewer link stays an editor", str(answer))
    status, _, raw = call("GET", f"{base}/rooms/{room}/members", token=owner)
    members = {m["orcid"]: m["role"] for m in body_of(raw).get("members") or []}
    tally.ok(members.get(who_guest) == "editor", "…and the ACL agrees", str(members))

    print("\n· tidying")
    call("POST", f"{base}/rooms/{room}/archive", token=owner,
         json_body={"archived": True})
    print(f"  · {room} archived")
    return tally.report("invite-join")


if __name__ == "__main__":
    sys.exit(main())
