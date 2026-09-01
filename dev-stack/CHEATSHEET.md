# Cheat-sheet — run the stack & connect EMtools to MinIO

Quick reference. The full explanation is in [`README-DEV.md`](README-DEV.md); this
is the "what do I type" version, plus the one thing it does not cover: pointing
**EMtools (Blender)** at the local object store.

> ## Where you start again: **`/em/rooms/`**
>
> — [`https://em.localhost:8443/em/rooms/`](https://em.localhost:8443/em/rooms/)
>
> That is the node's front door: what it runs, its rooms, its studies, its
> monuments, and the door of each one. **Sign in and it also draws the «Node
> map»** — every face of the node with its live state, the internal address each
> probe asked, and a *copy curl* for putting the same question to a terminal. That
> zone is operator-only and read-only; it is the part of this file that the page
> now carries itself.
>
> On the institutional node `/` belongs to Heriverse
> (`role/templates/Caddyfile.j2` opens with `redir / /a/heriverse`), so the door
> is `/em/rooms/` **there too** — worth knowing rather than discovering. Here `/`
> redirects to it.

## 1 · Launch every service at once

```bash
brew install colima docker docker-compose      # once
cd stratigraph-server/dev-stack
cp .env.dev.example .env.dev                    # once
./fcn-up.sh                                      # ← the whole stack, in one go
./fcn-up.sh --demo                               # …and POPULATE it: images, rooms, studies (idempotent)
```

`fcn-up.sh` starts **Colima** by itself, then brings up, behind Caddy on
`https://em.localhost:8443`:

| service | where | notes |
|---|---|---|
| **MinIO** | API `:9000`, console `:9001` | bucket `em-assets` auto-created |
| **Keycloak** | `:8085` and `…:8443/auth` | realm `em-dev`; ONE public issuer (https, through Caddy) whichever door you use — see `keycloak/README.md` |
| **Cantaloupe / IIIF** | `:8182` | reads the SAME bucket, key = asset sha256 |
| **StratiGraph Server** | via Caddy `/em/v1` | the room API |
| **StratiGraph Catalog** | `:8010` | the register (published studies) |
| **NodeODM** | `:3010` (its own dashboard) | **not started by `fcn-up.sh`** — opt-in, see §1-bis |
| **Field assistant** | via Caddy `/chat` | the PWA — https on the shared origin, which is what gives a **phone** its camera, microphone and GPS |

Health: `https://em.localhost:8443/em/v1/health`. First run takes a few minutes
(image build); after that a full `up` is **~15 s**.

## 1-bis · Turn the photogrammetric engine on

The engine is **opt-in**, like production (`nodeodm_enabled: false` in the Ansible
role). A plain `./fcn-up.sh` leaves it down; a photogrammetry session turns it on:

```bash
docker compose --env-file .env.dev -f docker-compose.dev.yml --profile engine up -d nodeodm
```

…and off again when you are done:

```bash
docker compose --env-file .env.dev -f docker-compose.dev.yml --profile engine stop nodeodm
```

With the engine down, `POST /v1/photogrammetry` reports that it cannot reach it —
which is a true sentence about that node, not a failure.

Then the end-to-end walk, against the real engine:

```bash
python dev-stack/smoke_photogrammetry.py --images ~/photos/US12 --wait 1800
```

> **Why opt-in, measured 2026-08-29.** NodeODM is **2.62 GB on disk**, and by
> default it keeps finished *and failed* tasks for **48 h**. On a Colima VM with a
> 19 GB root already holding ~16 GB of other projects' images, the stack and the
> engine did not both fit — the pull, and then the server's own rebuild, died on
> `no space left on device`. Both halves are now handled: the size is opt-in
> (`profiles: ["engine"]`) and the growth is bounded
> (`--cleanup_tasks_after`, `NODEODM_CLEANUP_AFTER=60` minutes — the engine sweeps
> at boot and hourly). If a node still runs out of room, grow the VM rather than
> shrinking the window — `colima stop && colima start --disk 120` keeps the
> volumes. Note the trade-off of a short window: after it, the engine deletes the
> task, so its `all.zip` is gone and `GET /task/{uuid}/info` 404s. Safe for us —
> the connector publishes the produced bytes to MinIO *before* writing anything to
> the graph — but raise `NODEODM_CLEANUP_AFTER` on a node where somebody
> re-downloads runs from NodeODM's own dashboard.

## 1-ter · Point the field assistant at a room

By default the assistant writes to **its own container** on a volume, not to a
room. That is the honest default rather than a shortcut: it refuses to start with
a room configured and no token (`writer.py`: *the assistant writes as a verified
person or it does not write*), a compose file cannot call `token.sh`, and a JWT
pasted into `.env.dev` would be a credential dressed up as configuration — living
there long after the reason it was issued.

When you want the writes to land in a room, put a **fresh** token in the
environment for that run:

```bash
cd stratigraph-server/dev-stack
EM_CHATBOT_SERVER_URL=http://stratigraph-server:8000 \
EM_CHATBOT_ROOM=cantiere-demo \
EM_CHATBOT_TOKEN="$(./token.sh)" \
  docker compose --env-file .env.dev -f docker-compose.dev.yml \
    up -d --force-recreate stratigraph-chatbot
```

The URL is the **internal** one (`stratigraph-server:8000`): the assistant dials
it from inside the compose network, and routing it back out through Caddy would
make it depend on the proxy to reach a service beside it. `/chat/health` reports
which of the two it is on.

Dev tokens are short-lived. When it stops writing, the token expired — re-run the
command. Persisting a session is a decision of its own and is not made here.

## 1-quater · The dev loop is not lost

The assistant still runs alone, which is the loop worth keeping:

```bash
cd stratigraph-chatbot && .venv/bin/python -m uvicorn app.main:app --port 8020 --reload
```

The page derives its API base and its service-worker scope from **its own
document URL**, so the same file works at `http://localhost:8020/` and at
`https://em.localhost:8443/chat/` with nothing configured. On a phone, though,
only the second one has a camera: `localhost` is a secure context, `http://<lan
ip>:8020` is not.

## 2 · Colima is the intended runtime

The stack is written for **Colima** (`fcn-up.sh` runs `colima start --cpu 4 --memory 8`);
`docker` is only the client — without Colima (or Docker Desktop) there is no daemon.
If `docker ps` fails, run `docker context use colima`. Alternatives exist (Docker
Desktop — mind the org licensing; OrbStack; Podman) but the scripts assume Colima.

## 3 · Connect EMtools (Blender) to the local MinIO

EMtools promotes an asset **in-process via s3Dgraphy** (`promote_resource_to_minio`
→ `api.ingest_minio_resource`), and s3Dgraphy's MinIO backend reads the
**Heriverse-aligned `S3_*` env vars** (`minio_backend.from_env`). So launch Blender
from a shell that carries them, pointing at the dev-stack MinIO:

```bash
export S3_ENDPOINT=http://localhost:9000        # http:// = insecure (dev)
export S3_ACCESS_KEY_ID=minioadmin
export S3_SECRET_ACCESS_KEY=minioadmin
export S3_BUCKET=em-assets                       # the SAME bucket server + Cantaloupe use
# optional: S3_PREFIX, S3_REGION, S3_FORCE_PATH_STYLE (default true)
/Applications/Blender.app/Contents/MacOS/Blender
```

Then in EMtools ▸ **Resources** ▸ "Object store (MinIO)" ▸ **Promote to MinIO**:
the local bytes go up content-addressed, and the LinkNode locator is repointed to
the returned `s3_uri` (one ID space, FS ↔ MinIO). Needs the `minio` extra in
Blender's Python, else `MissingDependency`. Inspect the object at the MinIO console
`http://localhost:9001`.

> **Direct vs gated — read this once.** This `S3_*` path writes **straight to
> MinIO**, which is fine for local single-user work but **bypasses the StratiGraph Server
> gate** (per-room ACL + per-asset DTC). The shared / production path goes through
> StratiGraph Server, which enforces rights before the object store. Use the direct path for
> local dev; do not wire it into a shared node.
