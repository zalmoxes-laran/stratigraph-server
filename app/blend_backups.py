"""The `.blend` SAFETY ARCHIVE: opaque snapshots of a working file.

## Why this is not the versioning we already have

The **shared** data is content-addressed and stays that way: em.json, the glTF
of record, the DTC resources are named by their `sha256`, so *the version IS the
hash* and *the history IS the DTC* — a derivation records which digest it used.
Nothing here touches that, and there is deliberately **no bucket versioning**:
S3 key-versioning on content-addressed objects versions nothing, because a
different content is already a different key.

A `.blend` is a different animal. It is **not the medium** — what gets shared is
em.json plus the glTF — it is the workshop: heavy linked survey models, light
proxy work, a reconstruction phase that gets heavy again. Its day-to-day
versioning belongs on the modeller's disk. What a modeller does *not* have is a
second copy somewhere that survives the laptop.

So: **on demand, opaque, deliberate.** A person decides "keep this one", the
bytes go into the room's store under their own digest, and a record says who
kept it, when, and what they called it. Not a hook on save — a save hook would
turn a safety net into a quota.

## What "opaque" buys, and what it costs

An archived `.blend` is **not a publishable asset**. It lives in its own
namespace (`blend-backups/…`, see `BACKUP_PREFIX`), never in the asset bucket's
flat digest namespace, so nothing can cite it, no manifest can point at it, and
the rights gate never has to have an opinion about it. The cost is that it
carries no scientific meaning at all: it is a pile of bytes with a label. That
is the correct amount of meaning for a backup.

## Dedup, for free and on purpose

The object key is the digest, so re-archiving unchanged bytes writes nothing and
the answer says `created: false`. This is content-addressing again — but
**explicit and on demand**, which is the whole difference from the automatic kind
the invariant refuses.

## The `backup` note, and where it does NOT go

Each archive gets a DTC-shaped note (`kind: "backup"`) — who, when, on which
digest, with which label. It is kept in the **backup register**, and *not* as a
node in the resident DTC corpus. That is a decision worth writing down: the
corpus is the register of documentation that em-server *enforces rights from*,
and its `ResourceNode`s are exactly the publishable things. Registering an
opaque working file there would manufacture the one thing this module refuses to
make — a citable asset — and would make the asset gate answer questions about a
file nobody may read. The note keeps the DTC vocabulary so it can become a real
act the day s3Dgraphy has an activity for "archived a working file"; today it is
a record, honestly labelled.

## Declared, and not tonight

* **retention / GC.** Backups accumulate: an unreferenced-asset sweep
  (keep-if-referenced) is an em-server-wide concern, and backups are precisely
  the objects a naive sweep would eat. Nothing here deletes anything.
* **replicas.** The directory register is local, exactly like the directory
  snapshot store, and says so in `describe()`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import threading
from typing import Any, Dict, List, Optional, Protocol

#: The namespace inside the store. Backups are NOT in the publishable assets'
#: flat digest namespace — that separation is what "opaque" means operationally.
BACKUP_PREFIX = "blend-backups/"

#: The media type we store them as. Deliberately generic: an opaque blob.
BLEND_MEDIA_TYPE = "application/x-blender"


def digest_of(data: bytes) -> str:
    """`<hex>` — the name these bytes will have. No `sha256:` prefix: that
    belongs to a *reference* to a publishable asset, and a backup is not one."""
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ── the bytes ────────────────────────────────────────────────────────────────

class BackupBlobs(Protocol):
    """Where the snapshots' bytes live. Keyed by digest, nothing else."""

    def put(self, data: bytes) -> Dict[str, Any]: ...

    def get(self, sha256: str) -> Optional[bytes]: ...

    def head(self, sha256: str) -> Optional[Dict[str, Any]]: ...


class InMemoryBackupBlobs:
    """Tests, and a node that has not been given anywhere to put them."""

    def __init__(self) -> None:
        self._blobs: Dict[str, bytes] = {}

    def put(self, data: bytes) -> Dict[str, Any]:
        key = digest_of(data)
        created = key not in self._blobs
        self._blobs[key] = bytes(data)
        return {"sha256": key, "size": len(data), "created": created}

    def get(self, sha256: str) -> Optional[bytes]:
        return self._blobs.get(str(sha256).lower())

    def head(self, sha256: str) -> Optional[Dict[str, Any]]:
        data = self._blobs.get(str(sha256).lower())
        return None if data is None else {"sha256": sha256, "size": len(data)}

    def count(self) -> int:
        return len(self._blobs)


class DirectoryBackupBlobs:
    """A directory, one file per digest. What a single-machine node uses."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha256: str) -> pathlib.Path:
        safe = "".join(c for c in str(sha256).lower() if c in "0123456789abcdef")
        if len(safe) != 64:
            raise ValueError(f"not a sha256: {sha256!r}")
        return self.root / safe

    def put(self, data: bytes) -> Dict[str, Any]:
        key = digest_of(data)
        path = self._path(key)
        created = not path.exists()
        if created:
            # write-then-rename: a half-written backup is worse than no backup
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        return {"sha256": key, "size": len(data), "created": created}

    def get(self, sha256: str) -> Optional[bytes]:
        try:
            return self._path(sha256).read_bytes()
        except (FileNotFoundError, ValueError):
            return None

    def head(self, sha256: str) -> Optional[Dict[str, Any]]:
        try:
            stat = self._path(sha256).stat()
        except (FileNotFoundError, ValueError):
            return None
        return {"sha256": str(sha256).lower(), "size": stat.st_size}


class MinioBackupBlobs:
    """MinIO, in its own **prefix** inside the node's bucket.

    A prefix rather than a second bucket, and the reason is operational: a bucket
    is a thing somebody has to create, give a policy and remember; a prefix
    arrives with the code. It is still a separate namespace — nothing that walks
    the assets' flat digest keys sees `blend-backups/…`, which is what the
    invariant asks for. A deployment that wants a whole bucket sets
    `EM_BACKUP_BUCKET`.
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str,
                 bucket: str, secure: bool = True,
                 prefix: str = BACKUP_PREFIX) -> None:
        try:
            from minio import Minio  # type: ignore
        except ImportError as exc:   # pragma: no cover — depends on the build
            raise RuntimeError(
                "the backup store needs the `minio` client, which this build "
                "does not have: install em-server[s3], or unset the MinIO "
                "environment and use EM_BACKUP_DIR") from exc
        self.bucket = bucket
        self.endpoint = endpoint
        self.prefix = prefix
        host = endpoint.split("://", 1)[-1].rstrip("/")
        self._client = Minio(host, access_key=access_key, secret_key=secret_key,
                             secure=secure)

    def _key(self, sha256: str) -> str:
        safe = "".join(c for c in str(sha256).lower() if c in "0123456789abcdef")
        if len(safe) != 64:
            raise ValueError(f"not a sha256: {sha256!r}")
        return f"{self.prefix}{safe}"

    def put(self, data: bytes) -> Dict[str, Any]:
        import io

        from minio.error import S3Error  # type: ignore

        key = self._key(digest_of(data))
        existed = False
        try:
            self._client.stat_object(self.bucket, key)
            existed = True
        except S3Error as exc:
            if exc.code not in ("NoSuchKey", "NoSuchObject", "NotFound"):
                raise
        if not existed:
            self._client.put_object(self.bucket, key, io.BytesIO(data), len(data),
                                    content_type=BLEND_MEDIA_TYPE)
        return {"sha256": digest_of(data), "size": len(data),
                "created": not existed}

    def get(self, sha256: str) -> Optional[bytes]:
        from minio.error import S3Error  # type: ignore

        try:
            answer = self._client.get_object(self.bucket, self._key(sha256))
        except (S3Error, ValueError):
            return None
        try:
            return answer.read()
        finally:
            answer.close()
            answer.release_conn()

    def head(self, sha256: str) -> Optional[Dict[str, Any]]:
        from minio.error import S3Error  # type: ignore

        try:
            stat = self._client.stat_object(self.bucket, self._key(sha256))
        except (S3Error, ValueError):
            return None
        return {"sha256": str(sha256).lower(), "size": int(stat.size or 0)}


# ── the register ─────────────────────────────────────────────────────────────

class BackupRegister(Protocol):
    """What was kept, per room. Small records, never the bytes."""

    def records(self, room_id: str) -> List[Dict[str, Any]]: ...

    def put(self, room_id: str, records: List[Dict[str, Any]]) -> None: ...

    def rooms(self) -> List[str]: ...


class InMemoryBackupRegister:
    def __init__(self) -> None:
        self._by_room: Dict[str, List[Dict[str, Any]]] = {}

    def records(self, room_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._by_room.get(room_id, [])]

    def put(self, room_id: str, records: List[Dict[str, Any]]) -> None:
        self._by_room[room_id] = [dict(r) for r in records]

    def rooms(self) -> List[str]:
        return sorted(self._by_room)


class DirectoryBackupRegister:
    """One JSON file per room, written atomically.

    Local to this machine — the same caveat the directory snapshot store carries,
    and `describe()` says it out loud rather than letting a two-replica
    deployment discover it."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, room_id: str) -> pathlib.Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in room_id)
        return self.root / f"{safe}.json"

    def records(self, room_id: str) -> List[Dict[str, Any]]:
        try:
            raw = json.loads(self._path(room_id).read_text("utf-8"))
        except FileNotFoundError:
            return []
        except ValueError:
            return []
        return list(raw) if isinstance(raw, list) else []

    def put(self, room_id: str, records: List[Dict[str, Any]]) -> None:
        path = self._path(room_id)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)

    def rooms(self) -> List[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))


def blobs_from_env(environ: Optional[Dict[str, str]] = None) -> BackupBlobs:
    """MinIO when the node has one, a directory otherwise, memory as the last
    resort — the same ladder the asset store climbs, and for the same reason: a
    node must not silently store a person's safety copy in RAM."""
    env = dict(environ if environ is not None else os.environ)
    from .assets import _minio_settings

    settings = _minio_settings(env)
    if settings:
        bucket = env.get("EM_BACKUP_BUCKET") or settings["bucket"]
        return MinioBackupBlobs(settings["endpoint"], settings["access_key"],
                                settings["secret_key"], bucket,
                                secure=settings["secure"])
    directory = env.get("EM_BACKUP_DIR")
    if directory:
        return DirectoryBackupBlobs(directory)
    # …otherwise beside the snapshots, which is where an operator already looks
    # for this node's durable state (same convention as the ACL and invite
    # stores). Memory only when the node has been given nowhere at all.
    beside = env.get("EM_SNAPSHOT_DIR")
    if beside:
        return DirectoryBackupBlobs(pathlib.Path(beside) / "blend-backups")
    return InMemoryBackupBlobs()


def register_from_env(environ: Optional[Dict[str, str]] = None) -> BackupRegister:
    env = dict(environ if environ is not None else os.environ)
    root = env.get("EM_BACKUP_REGISTER_DIR")
    if not root and env.get("EM_SNAPSHOT_DIR"):
        root = str(pathlib.Path(env["EM_SNAPSHOT_DIR"]) / "blend-backup-register")
    return DirectoryBackupRegister(root) if root else InMemoryBackupRegister()


def describe(blobs: BackupBlobs, register: BackupRegister) -> str:
    where = getattr(blobs, "endpoint", None) or getattr(blobs, "root", None)
    kind = type(blobs).__name__.replace("BackupBlobs", "").lower() or "memory"
    if isinstance(blobs, MinioBackupBlobs):
        where = f"{blobs.endpoint}, bucket {blobs.bucket}, prefix {blobs.prefix}"
    note = ""
    if isinstance(register, DirectoryBackupRegister):
        note = " · register: directory (local only — not for replicas)"
    elif isinstance(register, InMemoryBackupRegister):
        note = " · register: memory (lost on restart)"
    return f"{kind} ({where}){note}"


# ── the service ──────────────────────────────────────────────────────────────

class BlendBackups:
    """Archive, list, restore. Thin on purpose — there is nothing to compute
    about an opaque blob beyond hashing it, and the hashing is the store's."""

    def __init__(self, blobs: BackupBlobs, register: BackupRegister) -> None:
        self.blobs = blobs
        self.register = register
        self._lock = threading.Lock()

    def archive(self, room_id: str, data: bytes, *, orcid: Optional[str],
                label: str = "", filename: str = "") -> Dict[str, Any]:
        """Keep these bytes. Returns the record, with `created` telling the
        caller whether anything new was stored.

        Re-archiving the same `.blend` is **not** a new snapshot: same bytes,
        same digest, one object. The record's label and time are the FIRST ones —
        re-labelling a snapshot by uploading it again would rewrite history, and
        a backup whose date moves is not a backup. What does change is
        `last_seen`, which is a fact and not a rewrite.

        Two different questions, two flags, because conflating them was a
        measured bug: `created` says whether a **snapshot** was taken here, and
        `stored_bytes` whether **bytes** were written. The same `.blend` kept in
        two rooms is two snapshots of one object.
        """
        if not data:
            raise ValueError("empty body: there is no snapshot in zero bytes")
        stored = self.blobs.put(data)
        sha = stored["sha256"]
        # `created` below is about the RECORD, not about the object, and the
        # difference was measured: the same bytes archived into a *different*
        # room found the object already in the store (dedup is node-wide, the
        # store is shared) and the answer said `created: false` for what was, to
        # the person doing it, a snapshot they had just taken. Whether bytes were
        # actually written is a separate fact and is reported as `stored_bytes`.
        with self._lock:
            records = self.register.records(room_id)
            for record in records:
                if record.get("sha256") == sha and record.get("orcid") == orcid:
                    record["last_seen"] = now()
                    record["seen"] = int(record.get("seen") or 1) + 1
                    self.register.put(room_id, records)
                    return {**record, "created": False,
                            "stored_bytes": bool(stored.get("created")),
                            "detail": "these exact bytes were already kept — "
                                      "one snapshot, not two"}
            record = {
                "sha256": sha,
                "size": int(stored["size"]),
                "label": str(label or "")[:200],
                "filename": str(filename or "")[:200],
                "orcid": orcid,
                "created_at": now(),
                "last_seen": now(),
                "seen": 1,
                "room_id": room_id,
                # The DTC-shaped note. A distinct EVENT — «somebody kept a copy
                # of a working file» — and explicitly not a derivation producing
                # a scientific product. It lives here and not in the resident
                # corpus; see the module docstring for why.
                "dtc": {"kind": "backup", "act": "archive_working_file",
                        "about": f"sha256:{sha}", "by": orcid, "at": now(),
                        "opaque": True, "publishable": False},
            }
            records.append(record)
            self.register.put(room_id, records)
        return {**record, "created": True,
                # False when the store already held these bytes for somebody
                # else, or in another room: one object, two records, which is
                # what content-addressing is for.
                "stored_bytes": bool(stored.get("created", True))}

    def mine(self, room_id: str, *, orcid: Optional[str],
             all_authors: bool = False) -> List[Dict[str, Any]]:
        """The snapshots this person kept, newest first.

        **Only their own**, and that is the rule rather than an omission: a
        safety copy of somebody's working file is not documentation of the study,
        so being an editor of the room does not make it yours to read. Room
        membership is what lets you archive; authorship is what lets you list and
        restore.

        `all_authors` exists for dev mode, where there is no identity to compare
        against — and it says so in its name instead of pretending.
        """
        records = self.register.records(room_id)
        if not all_authors:
            records = [r for r in records if r.get("orcid") == orcid]
        return sorted(records, key=lambda r: str(r.get("created_at") or ""),
                      reverse=True)

    def fetch(self, room_id: str, sha256: str, *, orcid: Optional[str],
              all_authors: bool = False) -> Optional[bytes]:
        """The exact bytes back, or None when this person has no such snapshot.

        **None, not a 403 with a reason**, and the difference is deliberate: the
        register is per-author, so "you did not keep this" and "nobody kept this"
        are the same answer to the person asking — and telling a stranger which
        digests exist would turn a listing they may not read into a listing they
        can probe.
        """
        wanted = str(sha256).lower()
        if not any(r.get("sha256") == wanted
                   for r in self.mine(room_id, orcid=orcid,
                                      all_authors=all_authors)):
            return None
        return self.blobs.get(wanted)
