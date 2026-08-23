#!/usr/bin/env python3
"""The OPERATOR's scope: the node, and a capability nobody can give themselves.

    python dev-stack/smoke_node_admin.py

The dev stack has no OIDC operator role configured by default, so this smoke
**configures the question it asks**: it reads `EM_OPERATORS` out of the running
container and, if the owner is not in it, says exactly which line to add and what
it then measures. Nothing here grants anybody anything.

What it measures:

1. `/v1/admin/whoami` answers **without** a 403 — a console has to be able to ask
   "am I an operator" before it draws anything;
2. an operator lists **every** room on the node, including the ones nobody
   declared (`implicit`) and the archived ones;
3. an operator reads **storage**: per-room asset counts, missing containers,
   orphan digests;
4. an operator **archives** a dangling room — with the room named back
   (`confirm_room_id`), and a wrong name is refused;
5. …and there is **no delete**;
6. an owner who is NOT an operator gets **403 with the remedy** — fail-closed, and
   the refusal says who grants the capability.
7. the console itself is **served** (`GET /admin/`), shell + two modules.
"""

from __future__ import annotations

import subprocess
import sys

from smoke_common import (Tally, alive, arguments, body_of, call, detail_of,
                          need, orcid_of, token_for, unique)

CONTAINER = "em-dev-server"


def env_of_container(name: str) -> dict:
    """What the running node has in its environment. Asked of the container,
    because the answer to "is this ORCID an operator" is a deployment fact and
    not something this script can decide."""
    out = subprocess.run(["docker", "exec", CONTAINER, "env"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    return dict(line.split("=", 1) for line in out.stdout.splitlines()
                if "=" in line)


def main() -> int:
    args = arguments(__doc__ or "")
    base = args.base.rstrip("/")
    if not alive(base):
        return 2
    owner = need(token_for(args.owner), "no owner token")
    who_owner = orcid_of(owner)
    operator_token = token_for(args.operator) if args.operator else owner
    who_operator = orcid_of(operator_token)
    tally = Tally()
    print(f"base {base}\nowner {who_owner} · operator candidate {who_operator}\n")

    # ── 1 · whoami answers, whoever asks ────────────────────────────────────
    print("1 · «am I an operator?» is answerable without being refused")
    status, _, raw = call("GET", f"{base}/admin/whoami", token=owner)
    me = body_of(raw)
    tally.ok(status == 200, f"whoami answers ({status})", detail_of(raw))
    tally.ok("operator" in me and "capability" in me,
             "…and says what the capability IS", str(me.get("capability")))
    node_env = env_of_container(CONTAINER)
    configured = [o.strip() for o in (node_env.get("EM_OPERATORS") or "").split(",")
                  if o.strip()]
    print(f"    node: EM_OPERATORS={configured or '(empty)'} · "
          f"auth={me.get('auth')}")

    if not me.get("operator"):
        # This is the fail-closed half, and it is a MEASUREMENT, not a failure:
        # an owner with no capability is refused, with the remedy in the sentence.
        print("\n2 · fail-closed: this identity is NOT an operator")
        for path in ("/admin/rooms", "/admin/storage"):
            status, _, raw = call("GET", f"{base}{path}", token=owner)
            tally.ok(status == 403, f"{path} → 403 ({status})")
            tally.ok("EM_OPERATORS" in detail_of(raw)
                     or "operator" in detail_of(raw).lower(),
                     "…and the refusal names the capability", detail_of(raw))
        tally.ok("Owning a room does not grant it" in detail_of(raw)
                 or "does not grant" in detail_of(raw),
                 "…and says an owner cannot grant it to themselves",
                 detail_of(raw))
        print("\nTo measure the operator half, add this identity to the node and "
              "restart it:\n"
              f"  in dev-stack/docker-compose.dev.yml, under em-server → "
              f"environment:\n      EM_OPERATORS: \"{who_owner}\"\n"
              "  docker-compose --env-file .env.dev -f docker-compose.dev.yml "
              "up -d em-server\n"
              "…then run this smoke again. (Or grant the realm role "
              f"«{node_env.get('EM_OPERATOR_ROLE') or 'em-operator'}» in "
              "Keycloak.)")
        tally.skip("the operator half", "this identity has no capability yet")
        return tally.report("node-admin")

    # ── 2 · the node's reach ────────────────────────────────────────────────
    print("\n2 · an operator sees the whole node")
    room = unique("smoke-node")
    status, _, raw = call("POST", f"{base}/rooms", token=owner, json_body={
        "room_id": room, "title": "Smoke · node admin",
        "container_refs": ["non-esiste-questo-container"]})
    tally.ok(status == 201, f"a dangling room to look at ({room})", detail_of(raw))

    status, _, raw = call("GET", f"{base}/admin/rooms", token=operator_token)
    rooms = body_of(raw)
    tally.ok(status == 200, f"the node lists its rooms ({status})", detail_of(raw))
    ids = {r["room_id"] for r in rooms}
    tally.ok(room in ids, "…including the one just declared")
    tally.ok(any(r.get("implicit") for r in rooms),
             "…and the ones nobody ever declared (implicit)",
             str(sum(1 for r in rooms if r.get("implicit"))) + " implicit")

    status, _, raw = call("GET", f"{base}/rooms", token=owner)
    owned = {r["room_id"] for r in body_of(raw)}
    tally.ok(len(ids) >= len(owned),
             f"the node's reach is not the owner's ({len(ids)} vs {len(owned)})")

    # ── 3 · storage ─────────────────────────────────────────────────────────
    print("\n3 · storage oversight")
    status, _, raw = call("GET", f"{base}/admin/storage", token=operator_token)
    report = body_of(raw)
    tally.ok(status == 200, f"the storage report answers ({status})",
             detail_of(raw))
    tally.ok(all(k in report for k in ("asset_store", "snapshot_store",
                                       "room_store", "rooms", "orphan_assets")),
             "…with the three stores, the rooms and the orphans",
             f"assets={report.get('asset_store')}")
    mine = next((r for r in report.get("rooms") or []
                 if r["room_id"] == room), None)
    tally.ok(bool(mine) and mine.get("missing_refs")
             == ["non-esiste-questo-container"],
             "…and it NAMES the container that is not there",
             str(mine and mine.get("missing_refs")))

    # ── 4 · lifecycle, with the name typed back ─────────────────────────────
    print("\n4 · archiving an orphan: named, confirmed, never deleted")
    status, _, raw = call("POST", f"{base}/admin/rooms/{room}/archive",
                          token=operator_token,
                          json_body={"archived": True,
                                     "confirm_room_id": "qualcos-altro"})
    tally.ok(status == 400, f"a wrong confirmation is refused ({status})",
             detail_of(raw))
    status, _, raw = call("POST", f"{base}/admin/rooms/{room}/archive",
                          token=operator_token,
                          json_body={"archived": True, "confirm_room_id": room})
    tally.ok(status == 200 and body_of(raw).get("archived_at"),
             f"…and with the right one it is archived ({status})",
             str(body_of(raw).get("archived_at")))
    status, _, raw = call("GET", f"{base}/admin/rooms", token=operator_token)
    still = {r["room_id"]: r for r in body_of(raw)}
    tally.ok(room in still and still[room].get("archived_at"),
             "the room is STILL listed, marked — nothing was removed")
    status, _, _ = call("DELETE", f"{base}/admin/rooms/{room}",
                        token=operator_token)
    tally.ok(status in (404, 405), f"and there is no delete ({status})")

    # ── 5 · the console is served ───────────────────────────────────────────
    print("\n5 · the console itself")
    root = base.rsplit("/v1", 1)[0]
    status, headers, raw = call("GET", f"{root}/admin/")
    tally.ok(status == 200, f"GET /admin/ serves the shell ({status})")
    page = raw.decode("utf-8", "replace")
    tally.ok("node console" in page and "boot.js" in page,
             "…the shell, loaded through the file that orders the modules")
    for module in ("users-rooms.js", "storage.js"):
        status, _, _ = call("GET", f"{root}/admin/modules/{module}")
        tally.ok(status == 200, f"module served: {module} ({status})")
    # the dependency points ONE way. Asserted here too because the alternative
    # was measured in a browser and it is silent: a circular import where the
    # shell awaits a module deadlocks, and the page just says "Loading…".
    status, _, shell = call("GET", f"{root}/admin/console.js")
    tally.ok(status == 200 and "./modules/" not in shell.decode("utf-8", "replace"),
             "…and the shell does not import its modules (no cycle)")

    return tally.report("node-admin")


if __name__ == "__main__":
    sys.exit(main())
