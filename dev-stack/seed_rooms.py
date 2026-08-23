#!/usr/bin/env python3
"""Seed the demonstration rooms — the visibility pair, and the room to work in.

    python dev-stack/seed_rooms.py
    python dev-stack/seed_rooms.py --force     # …overwriting the work room too

`mostra` is `visibility: public` and `scavo` is `restricted`. They are the same
study twice, on purpose: the ONLY difference between them is that one word in the
header, so a probe of the two answers the question "what does publishing change?"
without any other variable moving.

`basilica-demo` is a third room, and a different KIND of thing: a small
stratigraphy for two EMStudio clients to meet in (walkthrough Tappa 4). Which is
why it is **preserved and not overwritten** on a re-run — the other two are
fixtures nobody edits, this one is where somebody's editing happens, and a seed
that silently replaced a morning's work would be the worst kind of idempotent.
`--force` overwrites it, deliberately and out loud.

    curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:8000/v1/rooms/mostra/iiif/img-1/manifest   # 200
    curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:8000/v1/rooms/scavo/iiif/img-1/manifest    # 401

**Why this file exists.** These two rooms were written into the volume by hand
during the session that built the visibility rule. Hand-seeded state is state
that dies the first time somebody runs `down -v`, and its loss is discovered as a
smoke test failing for a reason nobody can reconstruct. A seed you can re-run is
the difference between a demo and a fixture.

It writes THROUGH the container (`docker exec`) rather than into a host path,
because the snapshots live on a named volume and the volume is the only place
they exist. Nothing here needs em-server to be answering — only the container to
be up — so it also works while the service is restarting.

The image both rooms point at is the one `smoke_iiif.py` uploads; run that first
(or after — the manifest is what needs it, not the seed).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

#: The image the two rooms annotate — the digest `smoke_iiif.py` produces. It is
#: a CONTENT address, so it is the same on every machine that runs that smoke.
IMAGE_SHA = ("sha256:4239fc67504c2b22c584d4de71b7329e"
             "3c01b2a0b716aaa198ca6c4120b00abe")

CONTAINER = "em-dev-server"
SNAPSHOT_DIR = "/srv/em-data/snapshots"


def document(room_id: str, name: str, visibility: str) -> dict:
    """One room: an image and two annotated regions. Identical but for the word."""
    return {
        "header": {"format": "em.json", "version": "1.0",
                   "visibility": visibility},
        "graphs": {room_id: {
            "graph_id": room_id,
            "name": name,
            "nodes": [
                {"id": "img-1", "node_type": "resource", "name": "Foto di scavo",
                 "data": {"checksum": IMAGE_SHA, "media_type": "image/png",
                          "residency": "reference"}},
                {"id": "reg-a", "node_type": "annotation_region", "name": "muro",
                 "data": {"shape_kind": "rect", "rect": [0.1, 0.1, 0.3, 0.2],
                          "page": 0, "resource_id": "img-1"}},
                {"id": "reg-b", "node_type": "annotation_region", "name": "soglia",
                 "data": {"shape_kind": "rect", "rect": [0.55, 0.6, 0.25, 0.25],
                          "page": 0, "resource_id": "img-1"}},
            ],
            "edges": [
                {"id": "e-a", "source": "reg-a", "target": "img-1",
                 "edge_type": "is_on_resource"},
                {"id": "e-b", "source": "reg-b", "target": "img-1",
                 "edge_type": "is_on_resource"},
            ],
        }},
        "active_graph_id": room_id,
    }


def work_room(room_id: str, name: str) -> dict:
    """A small stratigraphy — the room two clients meet in.

    Not annotated regions this time but **units and a relation**, because what
    Tappa 4 shows is one person editing and another seeing it: that needs a
    graph somebody can rename a node in, and a matrix that redraws when they do.

    Small on purpose. Six units and five relations are enough to see an edit
    arrive; a hundred would only make the screen harder to read.

    Node and edge shapes are literal here — the same idiom as the fixtures above
    — and they are the shapes s3Dgraphy's importer builds (`node_type: "US"`,
    `edge_type: "is_before"`, measured against `StratigraphicUnit`), not a guess.
    """
    units = [
        ("US1", "US 1", "Crollo di tegole sul piano di calpestio."),
        ("US2", "US 2", "Piano in cocciopesto, integro nel settore nord."),
        ("US3", "US 3", "Muro perimetrale est, opera mista."),
        ("US4", "US 4", "Fossa di spoliazione lungo il muro est."),
        ("US5", "US 5", "Riempimento della fossa, terra e frammenti."),
        ("US6", "US 6", "Fondazione del muro perimetrale."),
    ]
    #: `is_before` = the source is EARLIER. Read them as a sequence: the collapse
    #: sits on the floor, the floor abuts the wall, the robbing cuts the wall.
    relations = [("US1", "US2"), ("US2", "US3"), ("US4", "US3"),
                 ("US5", "US4"), ("US3", "US6")]
    return {
        "header": {"format": "em.json", "version": "1.0",
                   "visibility": "restricted",
                   "description": "Dato di dimostrazione, non uno scavo."},
        "graphs": {room_id: {
            "graph_id": room_id,
            "name": name,
            "nodes": [
                {"id": unit, "node_type": "US", "name": label,
                 "description": description}
                for unit, label, description in units
            ],
            "edges": [
                {"id": f"{a}__is_before__{b}", "source": a, "target": b,
                 "edge_type": "is_before"}
                for a, b in relations
            ],
        }},
        "active_graph_id": room_id,
    }


#: `preserve` = written only when the room is not there yet. See the module
#: docstring: a fixture is meant to be rewritten, a room somebody works in is not.
ROOMS = (
    ("mostra", "Mostra (dissemination)", "public", document, False),
    ("scavo", "Scavo (in corso)", "restricted", document, False),
    ("basilica-demo", "Basilica · saggio dimostrativo", None, work_room, True),
)


def exists(room_id: str) -> bool:
    """Is this room already written? Asked of the VOLUME, which is where it is."""
    return subprocess.run(
        ["docker", "exec", CONTAINER, "test", "-f",
         f"{SNAPSHOT_DIR}/{room_id}.em.json"], capture_output=True).returncode == 0


def record(room_id: str, title: str) -> dict:
    """The room's DURABLE record — `rooms.RoomDescriptor`, as JSON.

    Seeded beside the container, because since the register exists a room is a
    place with a name and not just a document somebody happened to open. Thin on
    purpose: no members here — the ACL is the one place that says who may work in
    a room, and a second list in this file would be a second answer.

    `created_by: null` is honest: a seed script is not a person. The first
    authenticated arrival still becomes the owner (`access.claim_owner`), which is
    the behaviour these demo rooms had before and keeps having.
    """
    return {"room_id": room_id, "title": title,
            "container_refs": [room_id], "created_by": None,
            "created_at": "2026-08-23T00:00:00Z", "archived_at": None}


def seed_record(room_id: str, title: str) -> bool:
    """Write `<room>.room.json` next to the snapshot. True when it landed."""
    target = f"{SNAPSHOT_DIR}/{room_id}.room.json"
    payload = json.dumps(record(room_id, title), ensure_ascii=False)
    result = subprocess.run(
        ["docker", "exec", "-i", "-u", "0", CONTAINER, "sh", "-c",
         f"cat > {target} && chown --reference={SNAPSHOT_DIR} {target}"],
        input=payload.encode("utf-8"), capture_output=True)
    if result.returncode != 0:
        print(f"[ FAIL ] {room_id} (record): {result.stderr.decode().strip()}")
        return False
    return True


# ── the part a file cannot seed: roles and an invitation ─────────────────────
#
# The documents and the room records go into the volume directly (above): they are
# state, and writing them is a copy. Roles and invitation links are ACTS — the ACL
# is written through the door that checks who is asking, and an invite's secret
# exists exactly once, in the answer to the request that mints it. So this half
# goes through the API with a real token, and says so when it cannot.

DEMO = "cantiere-demo"


def api_pass() -> bool:
    """Declare the demo room, seat three people in it, and print a link.

    Best-effort by design: no stack, no token, no realm → it says what it skipped
    and the file-seeded rooms above are still there. A seed that failed loudly
    over an optional half would stop somebody from getting the part that worked.
    """
    import ssl
    import urllib.error
    import urllib.request

    base = f"https://{env_domain()}:{env_https_port()}/em/v1"
    tls = ssl.create_default_context()
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE       # the dev stack's own internal CA

    def token(user: str | None) -> str | None:
        args = [str(pathlib.Path(__file__).resolve().parent / "token.sh")]
        if user:
            args += ["--user", user]
        try:
            out = subprocess.run(args, capture_output=True, text=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        lines = [l for l in out.stdout.strip().splitlines() if l.strip()]
        return lines[-1] if lines else None

    def call(method: str, path: str, body: dict | None = None,
             bearer: str | None = None) -> tuple[int, dict]:
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base + path, data=data, headers=headers,
                                         method=method)
        try:
            with urllib.request.urlopen(request, context=tls, timeout=20) as answer:
                return answer.status, json.loads(answer.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"null")
            except ValueError:
                return exc.code, {}
        except urllib.error.URLError as exc:
            print(f"[ SKIP ] the API half — {base} not reachable ({exc.reason})")
            return 0, {}

    def orcid_of(jwt: str) -> str | None:
        import base64
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload))
        except Exception:                                  # noqa: BLE001
            return None
        return (claims.get("orcid") or claims.get("preferred_username")
                or claims.get("sub"))

    owner = token(None)
    if not owner:
        print("[ SKIP ] the API half — no token (is Keycloak up?). The rooms and "
              "their records are seeded; roles and the invite link are not.")
        return False
    guest = token("viewer")
    who_owner, who_guest = orcid_of(owner), (orcid_of(guest) if guest else None)

    # 1 · the demo WORKSPACE: two containers, because a room references 1..N
    status, made = call("POST", "/rooms", {
        "room_id": DEMO, "title": "Cantiere · demo (due container)",
        "container_refs": ["basilica-demo", "scavo"]}, owner)
    if status == 409:
        print(f"[ kept ] {DEMO} — c'era già (i ruoli e il link li rifaccio)")
    elif status == 201:
        print(f"[  ok  ] {DEMO} — record + refs "
              f"{made.get('container_refs')} · owner {who_owner}")
    else:
        print(f"[ FAIL ] {DEMO}: {status} {made.get('detail')}")
        return False

    # 2 · three people, three roles. The viewer is a REAL realm user, so the
    #     read-only face can be tried by logging in as them.
    seated = []
    if who_guest:
        status, _ = call("PUT", f"/rooms/{DEMO}/members/{who_guest}",
                         {"role": "viewer"}, owner)
        seated.append(f"{who_guest} → viewer ({status})")
    # …and a third identity as editor, so a promotion has somewhere to go
    editor = "0000-0003-1415-9265"
    status, _ = call("PUT", f"/rooms/{DEMO}/members/{editor}",
                     {"role": "editor"}, owner)
    seated.append(f"{editor} → editor ({status})")
    print(f"[  ok  ] {DEMO} — ACL: owner {who_owner} · " + " · ".join(seated))

    # 3 · a link, printed. Once — the node keeps a digest, not the link.
    status, invite = call("POST", f"/rooms/{DEMO}/invites",
                          {"role": "editor"}, owner)
    if status == 201 and invite.get("token"):
        print(f"[  ok  ] {DEMO} — invite link (editor), shown ONCE:")
        print(f"           token   {invite['token']}")
        print(f"           EMStudio  <emstudio-url>?join={invite['token']}"
              f"&room={DEMO}")
        print(f"           curl      curl -sk -X POST {base}/join "
              f"-H 'Authorization: Bearer $(./dev-stack/token.sh --user viewer)' "
              f"-H 'Content-Type: application/json' "
              f"-d '{{\"token\":\"{invite['token']}\"}}'")
    else:
        print(f"[ FAIL ] {DEMO} — no invite link: {status} {invite.get('detail')}")
        return False
    return True


def main() -> int:
    force = "--force" in sys.argv
    try:
        subprocess.run(["docker", "inspect", CONTAINER], check=True,
                       capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"the container {CONTAINER} is not there. Bring the stack up "
              f"first:\n  docker-compose --env-file .env.dev "
              f"-f docker-compose.dev.yml up -d")
        return 2

    for room_id, name, visibility, build, preserve in ROOMS:
        if preserve and not force and exists(room_id):
            print(f"[ kept ] {room_id} — c'era già, non l'ho toccata "
                  f"(--force per riscriverla)")
            continue
        payload = json.dumps(
            build(room_id, name, visibility) if visibility is not None
            else build(room_id, name), ensure_ascii=False)
        # `sh -c` with the JSON on stdin: no quoting of a document into an
        # argv, which is where this kind of script usually breaks.
        #
        # `-u 0` and then chown, rather than writing as the app user, because a
        # snapshot that arrived by an earlier `docker cp` is owned by the HOST
        # user and the app user cannot truncate it — measured: "Permission
        # denied" on a file that plainly exists and is plainly world-readable.
        # The chown hands it back to whoever owns the directory, so em-server
        # keeps being able to rewrite it on its own save.
        target = f"{SNAPSHOT_DIR}/{room_id}.em.json"
        result = subprocess.run(
            ["docker", "exec", "-i", "-u", "0", CONTAINER, "sh", "-c",
             f"cat > {target} && chown --reference={SNAPSHOT_DIR} {target}"],
            input=payload.encode("utf-8"), capture_output=True)
        if result.returncode != 0:
            print(f"[ FAIL ] {room_id}: {result.stderr.decode().strip()}")
            return 1
        print(f"[  ok  ] {room_id} — visibility: {visibility or 'restricted'}")
        # …and the room's own record, so the room can be LISTED and named before
        # anybody connects. Same volume as the snapshot and the ACL: an operator
        # who backs one up finds the others.
        if not seed_record(room_id, name):
            return 1
        print(f"[  ok  ] {room_id} — record: «{name}» → [{room_id}]")

    print("\nem-server reads a room's document when the room is first opened, so "
          "a room that was already live in this process keeps what it had.\n"
          "Restart it if you have just changed a seed:\n"
          "  docker-compose --env-file .env.dev -f docker-compose.dev.yml "
          "restart em-server")
    print()
    api_pass()

    print("\nLe stanze ora hanno un RECORD durevole (`<room>.room.json`): "
          "esistono\nanche vuote, si elencano con `GET /v1/rooms`, e "
          "referenziano 1..N container.\nGli inviti (`POST "
          "/v1/rooms/<id>/invites`) vivono accanto agli ACL.")
    print("\nPer Tappa 4 (EMStudio come client), da incollare in "
          "Impostazioni ▸ Live sync:")
    print(f"  URL     https://{env_domain()}:{env_https_port()}/em")
    print(f"  Stanza  basilica-demo")
    print("  Token   ./dev-stack/token.sh   (il campo lo chiede Mode ▸ Hub)")
    return 0


def env_domain() -> str:
    return _env("EM_DEV_DOMAIN", "em.localhost")


def env_https_port() -> str:
    return _env("HTTPS_PORT", "8443")


def _env(name: str, default: str) -> str:
    """Read `.env.dev` without importing anything: this script deliberately has
    no dependencies, so that it works while the stack is half-up."""
    import os
    import pathlib
    if os.environ.get(name):
        return os.environ[name]
    path = pathlib.Path(__file__).resolve().parent / ".env.dev"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == name and value.strip():
                return value.strip()
    return default


if __name__ == "__main__":
    raise SystemExit(main())
