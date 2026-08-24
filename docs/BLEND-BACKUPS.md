# `.blend` safety snapshots

The shared data's versioning is already settled and this is not it. em.json, the
glTF of record and the DTC resources are **content-addressed**: the version *is*
the `sha256`, the history *is* the DTC (a derivation records which digest it
used), retention is keep-if-referenced. There is deliberately **no bucket
versioning** — S3 key-versioning on content-addressed objects versions nothing,
because a different content is already a different key.

A `.blend` is the workshop, not the medium. What gets shared is em.json plus the
glTF; the `.blend` holds heavy linked survey models, light proxy work, and a
reconstruction phase that gets heavy again. Its day-to-day versioning belongs on
the modeller's disk, where it already is. What that disk cannot do is survive
itself.

So one narrow thing: **an opaque snapshot, on demand.**

## The shape

```
PUT  /v1/rooms/{room}/blend-backup?label=…&filename=…     the bytes, raw
GET  /v1/rooms/{room}/blend-backups                       the ones YOU kept
GET  /v1/rooms/{room}/blend-backup/{sha256}               the exact bytes back
```

* **opaque.** Stored in its own namespace (`blend-backups/<sha256>` — see
  `BACKUP_PREFIX` in `app/blend_backups.py`), never in the assets' flat digest
  namespace. Nothing can cite it, no manifest can point at it, and the rights
  gate never has to have an opinion about somebody's work in progress. Measured:
  a snapshot's digest asked for as `…/asset/sha256:<hex>` answers **404**.
* **deliberate.** There is no save hook and there will not be one: a backup on
  every save is a quota, not a safety net.
* **deduplicated by content.** The key is the digest, so re-archiving an
  unchanged `.blend` writes nothing.
* **per-author.** Room membership (editor and above) is what lets you *keep* a
  snapshot; authorship is what lets you *list and restore* it. A room-mate's
  working file is not documentation of the study, and it is not yours to read. A
  snapshot that is not yours answers 404, not 403 — a 403 would confirm the
  digest exists.

### Two flags, not one

`created` says whether a **snapshot** was taken here; `stored_bytes` whether
**bytes** were written. Conflating them was a measured bug: the object store is
node-wide, so the same `.blend` kept in a second room found the object already
there and the answer said "already kept" for a snapshot somebody had *just*
taken.

### The record, and the `backup` note

```json
{"sha256": "715ed187…", "size": 10517, "label": "prima del taglio",
 "filename": "scavo.blend", "orcid": "0000-0002-…", "created_at": "…Z",
 "last_seen": "…Z", "seen": 2,
 "dtc": {"kind": "backup", "act": "archive_working_file",
         "about": "sha256:715ed187…", "by": "0000-0002-…", "at": "…Z",
         "opaque": true, "publishable": false}}
```

Re-archiving the same bytes does **not** move `created_at` and does not rewrite
the label — a backup whose date moves is not a backup. What moves is `last_seen`
and `seen`, which are facts and not rewrites.

The `backup` note is a **distinct event** ("somebody kept a copy of a working
file"), explicitly not a derivation producing a scientific product. It lives in
the backup register and **not** as a node in the resident DTC corpus, and that is
a decision rather than an omission: the corpus is the register em-server enforces
rights from, and its `ResourceNode`s are exactly the publishable things.
Registering an opaque working file there would manufacture the one thing this
feature refuses to make — a citable asset — and would make the asset gate answer
questions about a file nobody may read. The note keeps the DTC vocabulary so it
can become a real act the day s3Dgraphy has an activity for archiving a working
file.

## Configuration

| variable | what it does |
| --- | --- |
| `EM_BACKUP_BUCKET` | a whole bucket for backups instead of a prefix inside the asset bucket |
| `EM_BACKUP_DIR` | a directory, when the node has no MinIO |
| `EM_BACKUP_REGISTER_DIR` | where the per-room records live (defaults beside the snapshots) |

`GET /health` reports `blend_backup_store`, including the honest caveats
(`register: directory (local only — not for replicas)`, `memory (lost on
restart)`) — a safety copy that dies with the process is the one thing that must
not be a surprise.

## From Blender

EMtools' EM tab → **EMStudio Sync** → *Blend backups (safety, opaque)*, visible
when this Blender is in a room. It archives the file **on disk**: a session with
unsaved changes is refused with the reason, and saving first is a checkbox, not
something that happens behind your back. Restore lands **beside** the current
file as `<name>-snapshot-<sha12>.blend` and never on the file in use — a restore
that overwrote the working file would destroy the work the backup was there to
protect, at the exact moment somebody is panicking. Client: `put_blend_backup` /
`list_blend_backups` / `get_blend_backup` in `sync_manager/room.py`, digest
verified on both legs.

## Declared, and not built

* **retention / GC.** Backups accumulate and nothing here deletes anything.
  There is no DELETE endpoint on purpose: an unreferenced-asset sweep
  (keep-if-referenced) is an em-server-wide concern, and backups are precisely
  the objects a naive sweep would eat. It needs a policy somebody writes down.
* **the storage report counts them.** `GET /v1/admin/storage` and the Node Health
  panel walk the bucket, so backup objects appear in the node's totals. That is
  the truth about disk usage; they are simply not assets.
* **collaborative modelling.** Out of scope: one `.blend` holding the proxies,
  survey models linked, is what this is built for.

## Measuring it

```bash
python dev-stack/smoke_blend_backup.py --base http://localhost:8000/v1 --operator viewer
```

17 checks: archive · label/author/date · the `backup` note · dedup · the first
record holding · a changed `.blend` · the listing · restore byte-identical (hash
verified) · not reachable as an asset · 401 · a stranger's 403 · and the last
four through **EMtools' own client**, for real.
