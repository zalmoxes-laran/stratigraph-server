# Cheat-sheet — run the stack & connect EMtools to MinIO

Quick reference. The full explanation is in [`README-DEV.md`](README-DEV.md); this
is the "what do I type" version, plus the one thing it does not cover: pointing
**EMtools (Blender)** at the local object store.

## 1 · Launch every service at once

```bash
brew install colima docker docker-compose      # once
cd stratigraph-server/dev-stack
cp .env.dev.example .env.dev                    # once
./fcn-up.sh                                      # ← the whole stack, in one go
```

`fcn-up.sh` starts **Colima** by itself, then brings up, behind Caddy on
`https://em.localhost:8443`:

| service | where | notes |
|---|---|---|
| **MinIO** | API `:9000`, console `:9001` | bucket `em-assets` auto-created |
| **Keycloak** | `:8085` | realm `em-dev` |
| **Cantaloupe / IIIF** | `:8182` | reads the SAME bucket, key = asset sha256 |
| **StratiGraph Server** | via Caddy `/em/v1` | the room API |
| **StratiGraph Catalog** | `:8010` | the register (published studies) |
| **NodeODM** | `:3010` (its own dashboard) | the photogrammetric engine — StratiGraph Server dials it at `nodeodm:3000`, never this port |

Health: `https://em.localhost:8443/em/v1/health`. First run takes a few minutes
(image build); after that a full `up` is **~15 s**.

> **Disk, and it bit us (measured 2026-08-29).** NodeODM is **2.62 GB on disk**
> and its working volume grows by a GB or two per run — it prunes nothing, not
> even failed runs. On a Colima VM with a 19 GB root already holding ~16 GB of
> other projects' images, the stack and the engine did **not** both fit: the pull
> and then the server's own rebuild died on `no space left on device`. If
> `./fcn-up.sh` fails that way:
>
> ```bash
> colima stop && colima start --disk 120     # grow the VM; the volumes survive
> ```
>
> …or reclaim (`docker image prune -a`), or run without the engine
> (`docker compose stop nodeodm && docker volume rm em-dev_nodeodm_data` — the
> `/v1/photogrammetry` endpoint then reports the engine as unreachable, which is
> a true sentence about that node).

- **Edit s3Dgraphy live:** `./fcn-up.sh --local-s3d` mounts `../../s3Dgraphy/src`;
  edit, then `docker-compose -f docker-compose.dev.yml -f docker-compose.local-s3d.yml restart stratigraph-server stratigraph-catalog`.
- **Cert rejected by the browser?** `./fcn-trust-ca.sh` (once; and after every `--wipe`).
- **Down:** `./fcn-down.sh` — data (studies, rooms, bucket, realm, CA) **persists**;
  only `./fcn-down.sh --wipe` erases it.

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
