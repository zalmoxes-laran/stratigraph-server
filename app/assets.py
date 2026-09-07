"""The asset store: a room provides a graph AND the bytes it refers to.

P4.2 gave a room its graph (the snapshot store). This is the other half: a study
is not only a set of assertions, it is also the models and the photographs those
assertions point at, and a room that could hold the first without the second
would push every collaborator back to sending files by hand.

Same shape as `store.py`, and for the same reason: **the durable truth is not on
the process's disk.** The interface is three methods, the implementation for
tests keeps bytes in memory, and the production one is MinIO — a line of
configuration, not a rewrite.

**Content-addressed.** The reference IS the digest of the content
(`sha256:<hex>`), which buys three things at once and no cleverness:

* **dedup is free** — the same bytes put twice are one object, because they have
  the same name. Two people who promote the same model do not fill a bucket.
* **the reference is verifiable** — a client that fetches an asset can check what
  it got, without asking anybody. That is what makes `checksum` on a ResourceNode
  a fact rather than a hope.
* **immutability** — an object cannot be replaced under a reference that still
  points at it. A citation stays a citation.

The digest travels WITH its algorithm (`sha256:…`), the same rule the shelf's
checksum follows: a bare hex string is unreadable in two years.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import threading
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


#: What a room's assets are addressed by. The prefix is not decoration: it is
#: what lets a reader know how to verify, and what lets us change algorithm one
#: day without every old reference becoming ambiguous.
DIGEST_PREFIX = "sha256"


def content_id(data: bytes) -> str:
    """`sha256:<hex>` of the bytes — the name an asset has for ever."""
    return f"{DIGEST_PREFIX}:{hashlib.sha256(data).hexdigest()}"


#: `runtime_checkable` so a TEST can ask «who implements this?» instead of
#: keeping a list of the backends beside the backends. Added 2026-10-08 with
#: `delete`: it is what lets `tests/test_il_verbo_che_manca.py` discover the
#: implementations FROM the protocol, so a store PSNC has not written yet is
#: under the gate before it exists. It checks method PRESENCE and nothing else,
#: which is exactly what «implements this interface» means here.
@runtime_checkable
class AssetStore(Protocol):
    """Put bytes, get bytes, ask about bytes, take bytes out. Nothing else."""

    def put(self, data: bytes, media_type: str) -> Dict[str, Any]:
        """Store `data`; return `{ref, sha256, media_type, size, created}`.

        `created` is False when the object was already there — a caller that
        wants to report "dedup" can, and one that does not can ignore it.
        """

    def get(self, ref: str) -> Optional[bytes]:
        """The bytes behind a reference, or None."""

    def head(self, ref: str) -> Optional[Dict[str, Any]]:
        """`{ref, sha256, media_type, size}` without moving the bytes."""

    def delete(self, ref: str) -> Dict[str, Any]:
        """Remove the bytes behind `ref`. `{ref, removed}` — never `None`.

        ════════════════════════════════════════════════════════════════════════
        THE FOURTH VERB, added 2026-10-08, and it goes HERE and not in a bucket.

        Until now this interface could put, get and ask. The only way to take
        something out was inside MinIO, by hand — an asymmetry whose cost is
        paid by whoever has to explain, to somebody whose photograph should not
        have been uploaded, that the node cannot forget.

        **It is in the protocol rather than in a backend on purpose.** MinIO may
        stop being supported for the community and PSNC may propose a store of
        their own; a deletion written against MinIO would be a debt paid twice.
        Three implementations already exercise these three methods, which is the
        evidence that a fourth will fit — and `tests/test_il_verbo_che_manca.py`
        discovers the implementations FROM THIS PROTOCOL rather than listing
        them, so a store nobody has written yet is already under the gate.

        Two properties, and they are properties rather than preferences:

        * **IDEMPOTENT.** Deleting twice is not an error. The second call says
          «it was not there» and does not raise: a cleanup that fails on the
          already-clean is a cleanup nobody runs a second time.
        * **IT SAYS WHAT IT DID.** `removed: True` means it was there and is
          gone; `removed: False` means there was nothing to remove. Two
          different reports for an operator, and `None` would be neither.

        What this method does NOT decide is WHETHER the bytes may go. Nothing
        here knows how many rooms point at a digest — that is
        `POST /v1/admin/assets/forget`'s question, and answering it in two
        places would be two answers.
        """


class InMemoryAssetStore:
    """For tests and a single-process laptop run — and it says so.

    Not the deployment target: it dies with the process, which is exactly the
    property the MinIO implementation exists to remove.
    """

    def __init__(self) -> None:
        self._blobs: Dict[str, bytes] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, data: bytes, media_type: str) -> Dict[str, Any]:
        ref = content_id(data)
        with self._lock:
            existed = ref in self._blobs
            if not existed:
                self._blobs[ref] = bytes(data)
                self._meta[ref] = {"ref": ref, "sha256": ref.split(":", 1)[1],
                                   "media_type": media_type, "size": len(data)}
        info = dict(self._meta[ref])
        info["created"] = not existed
        return info

    def get(self, ref: str) -> Optional[bytes]:
        with self._lock:
            return self._blobs.get(ref)

    def head(self, ref: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            meta = self._meta.get(ref)
        return dict(meta) if meta else None

    def delete(self, ref: str) -> Dict[str, Any]:
        with self._lock:
            existed = ref in self._blobs
            self._blobs.pop(ref, None)
            self._meta.pop(ref, None)
        return {"ref": ref, "removed": existed}

    def refs(self) -> List[str]:
        """Every reference this store holds.

        ════════════════════════════════════════════════════════════════════════
        WHY THIS EXISTS, AND WHY IT DID NOT (measured 2026-10-08)

        `main._stored_digests()` asks a store for `refs`, `digests` or `keys` and
        falls back to a `_data` dict. **No implementation had any of the four**,
        so `orphan_assets` in `/v1/admin/storage` has been `[]` since the report
        was written — on every backend, not only on MinIO. The console said
        «None reported. Note that a store which cannot enumerate (MinIO, on
        purpose…)», and the parenthetical made a general emptiness look like one
        store's policy.

        So the two LOCAL stores answer now, and MinIO deliberately still does
        not: listing a shared bucket on a page load is an expensive question,
        and `storage.js`'s rule — the browser never addresses the store — is
        untouched either way. A report that is silently partial is worse than
        one that says so; a report that is silently EMPTY is worse than both.
        """
        with self._lock:
            return sorted(self._blobs)

    def count(self) -> int:
        """How many distinct objects — the number a dedup test measures."""
        with self._lock:
            return len(self._blobs)


class DirectoryAssetStore:
    """A directory of content-addressed files. Local runs and tests that want to
    see the bytes; explicitly not the answer for replicas."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> pathlib.Path:
        digest = ref.split(":", 1)[-1]
        safe = "".join(c for c in digest if c.isalnum())
        # two levels of fan-out: a directory with a hundred thousand entries in
        # it is a directory nothing can list
        return self.root / safe[:2] / safe[2:4] / safe

    def put(self, data: bytes, media_type: str) -> Dict[str, Any]:
        ref = content_id(data)
        path = self._path(ref)
        existed = path.is_file()
        if not existed:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)                      # a reader never sees half a file
            path.with_suffix(".type").write_text(media_type, encoding="utf-8")
        return {"ref": ref, "sha256": ref.split(":", 1)[1], "media_type": media_type,
                "size": len(data), "created": not existed}

    def get(self, ref: str) -> Optional[bytes]:
        path = self._path(ref)
        return path.read_bytes() if path.is_file() else None

    def head(self, ref: str) -> Optional[Dict[str, Any]]:
        path = self._path(ref)
        if not path.is_file():
            return None
        type_file = path.with_suffix(".type")
        media = type_file.read_text(encoding="utf-8").strip() if type_file.is_file() \
            else "application/octet-stream"
        return {"ref": ref, "sha256": ref.split(":", 1)[1], "media_type": media,
                "size": path.stat().st_size}

    def delete(self, ref: str) -> Dict[str, Any]:
        """The blob and its `.type` sidecar, and the fan-out directories that
        are left empty.

        The sidecar matters: `put` writes two files and a `delete` that removed
        one would leave a store where `head` says nothing and a stray
        `<digest>.type` stays for ever. Measured by writing the test first —
        the first version left it behind.

        The empty directories are swept because the fan-out exists to keep a
        directory listable, and two levels of empty folders per forgotten
        object is the same problem arriving from the other side. `rmdir` and
        not `rmtree`: it refuses when something is still in there, which is
        exactly the check we want and not a risk to take.
        """
        path = self._path(ref)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        path.with_suffix(".type").unlink(missing_ok=True)
        for parent in (path.parent, path.parent.parent):
            try:
                if parent != self.root:
                    parent.rmdir()
            except OSError:
                break              # not empty: somebody else's digest lives here
        return {"ref": ref, "removed": existed}

    def refs(self) -> List[str]:
        """Every reference on disk — walked, not indexed.

        See `InMemoryAssetStore.refs` for why this was missing and what it
        costs. Cheap here for the reason the fan-out exists: two levels of
        directories, so this is a walk over a tree and never a listing of one
        enormous folder.

        The `.type` sidecars are skipped by NAME LENGTH and not by suffix: a
        digest is 64 hex characters, and `<digest>.type` is not. Checking the
        shape is what keeps a stray file in the tree from being reported as an
        asset the store holds.
        """
        found = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if len(name) == 64 and all(c in _HEX for c in name.lower()):
                found.append(f"{DIGEST_PREFIX}:{name}")
        return sorted(found)


class MinioAssetStore:
    """The deployment implementation: one bucket, one object per digest.

    **This is the production store**, not a development double. A local FCN and
    an institutional node are the same service with different addresses, so they
    run the same class; `dev-stack/` only puts a MinIO next to it on a laptop.

    The object KEY is the digest itself (`<hex>`, without the `sha256:` prefix —
    the prefix belongs to the reference, not to the bucket's namespace). That is
    what buys the three properties the interface promises, and it buys them from
    S3 rather than from code we would have to keep correct:

    * **dedup** — the same bytes have the same key, so a second upload overwrites
      an identical object instead of making a second one;
    * **immutability in practice** — an object cannot change under a reference
      that still points at it, because a different content is a different key;
    * **verifiability** — anybody can hash what they fetched and check.

    The `minio` client is an OPTIONAL dependency (`pip install StratiGraph Server[s3]`).
    A build without it fails **here**, at construction, with a sentence — not at
    the first upload with a stack trace from inside a request.
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str,
                 bucket: str, secure: bool = True) -> None:
        try:
            from minio import Minio  # type: ignore
        except ImportError as exc:   # pragma: no cover — depends on the build
            raise RuntimeError(
                "the MinIO asset store needs the `minio` client, which this "
                "build does not have: install StratiGraph Server[s3] (or unset the MinIO "
                "environment and use EM_ASSET_DIR)") from exc

        self.bucket = bucket
        self.endpoint = endpoint
        # the client wants host:port WITHOUT a scheme, and takes the scheme as a
        # flag — passing the URL whole is the first thing that goes wrong here
        host = endpoint.split("://", 1)[-1].rstrip("/")
        self._client = Minio(host, access_key=access_key, secret_key=secret_key,
                             secure=secure)
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """The bucket must exist before the first upload, and saying WHY it does
        not is the whole value of checking at start-up.

        Created when missing, because a room that cannot store its first asset is
        not a room. If creation fails the process refuses to start with the two
        causes named — a bucket that is not there and credentials that may not be
        allowed to make one look identical from a stack trace.
        """
        from minio.error import S3Error  # type: ignore

        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
        except S3Error as exc:
            raise RuntimeError(
                f"the asset bucket '{self.bucket}' is not usable at "
                f"{self.endpoint}: {exc.code}. Either it does not exist and these "
                f"credentials may not create it, or they may not read it — "
                f"create the bucket in MinIO, or give the key access to it") from exc
        except Exception as exc:      # network, DNS, TLS: not an S3 answer at all
            raise RuntimeError(
                f"the object store at {self.endpoint} did not answer: {exc}. "
                f"StratiGraph Server will not start without the store it is configured "
                f"to write to") from exc

    # ── the interface ────────────────────────────────────────────────────────

    def _key(self, ref: str) -> str:
        return ref.split(":", 1)[-1]

    def put(self, data: bytes, media_type: str) -> Dict[str, Any]:
        import io

        from minio.error import S3Error  # type: ignore

        ref = content_id(data)
        key = self._key(ref)
        existed = False
        try:
            self._client.stat_object(self.bucket, key)
            existed = True
        except S3Error as exc:
            if exc.code not in ("NoSuchKey", "NoSuchObject", "NotFound"):
                raise
        if not existed:
            self._client.put_object(self.bucket, key, io.BytesIO(data), len(data),
                                    content_type=media_type)
        return {"ref": ref, "sha256": key, "media_type": media_type,
                "size": len(data), "created": not existed}

    def get(self, ref: str) -> Optional[bytes]:
        from minio.error import S3Error  # type: ignore

        response = None
        try:
            response = self._client.get_object(self.bucket, self._key(ref))
            return response.read()
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return None
            raise
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def head(self, ref: str) -> Optional[Dict[str, Any]]:
        """Size and type **without moving the bytes** — a `HEAD`, not a `GET`.

        Worth its own method precisely because an asset can be a hundred
        megabytes: asking "is it there, how big is it" must not cost a download.
        """
        from minio.error import S3Error  # type: ignore

        try:
            stat = self._client.stat_object(self.bucket, self._key(ref))
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return None
            raise
        return {"ref": ref, "sha256": self._key(ref),
                "media_type": stat.content_type or "application/octet-stream",
                "size": stat.size}

    #: NO `refs()` HERE, and it is a decision rather than an omission. The
    #: client can list a bucket; this class does not offer it, so
    #: `main._stored_digests()` gets nothing and the orphan report says it
    #: cannot enumerate instead of returning a partial list. Listing a shared
    #: bucket on a page load is an expensive question, and the two local stores
    #: answer it for the runs where it is cheap. Read `InMemoryAssetStore.refs`
    #: for the measurement that put those two there.

    def delete(self, ref: str) -> Dict[str, Any]:
        """`remove_object`, and the `stat` before it is what makes the answer
        honest.

        S3 deletion is idempotent by design: removing a key that is not there
        succeeds. That is the right behaviour and the wrong REPORT — an operator
        told «removed» about an object that was never in the bucket learns
        nothing. So it asks first, and the two answers stay distinguishable.

        The race is named and accepted: between the `stat` and the `remove`
        somebody could upload the same bytes, and then `removed: True` would be
        about an object that came back. It is a content-addressed store, so what
        came back IS the same bytes — the report is imprecise about the instant
        and correct about the state, which is the trade a lock across a network
        would not improve.
        """
        from minio.error import S3Error  # type: ignore

        key = self._key(ref)
        existed = True
        try:
            self._client.stat_object(self.bucket, key)
        except S3Error as exc:
            if exc.code not in ("NoSuchKey", "NoSuchObject", "NotFound"):
                raise
            existed = False
        if existed:
            self._client.remove_object(self.bucket, key)
        return {"ref": ref, "removed": existed}


#: The MinIO settings, in the two spellings that exist in the wild: the client's
#: own names (`MINIO_*`, what the dev-stack and MinIO's own docs use) and the
#: StratiGraph Server-prefixed ones the Ansible role writes. Read in this order, like
#: `auth.py` reads the issuer — one setting, two names, and a precedence, rather
#: than two settings that will one day disagree.
_MINIO_KEYS = {
    "endpoint": ("MINIO_ENDPOINT", "EM_ASSET_S3_ENDPOINT"),
    "access_key": ("MINIO_ACCESS_KEY", "EM_ASSET_S3_ACCESS_KEY"),
    "secret_key": ("MINIO_SECRET_KEY", "EM_ASSET_S3_SECRET_KEY"),
    "bucket": ("MINIO_BUCKET", "EM_ASSET_S3_BUCKET"),
}


def _minio_settings(env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The MinIO configuration, or None when there is none at all.

    Raises when it is HALF there. That is the whole point of this function: a
    deployment that believes it writes to the shared bucket and quietly writes
    to a local directory instead is a data-loss story, and it is silent until it
    is expensive — the same rule `auth.py` applies to a half-configured realm.
    """
    found = {field: next((env[name].strip() for name in names
                          if (env.get(name) or "").strip()), "")
             for field, names in _MINIO_KEYS.items()}
    given = [field for field, value in found.items() if value]
    if not given:
        return None
    missing = [field for field, value in found.items() if not value]
    if missing:
        raise RuntimeError(
            "the MinIO asset store is half configured: "
            f"{', '.join(sorted(given))} given, {', '.join(sorted(missing))} "
            "missing. StratiGraph Server will not fall back to a local directory the "
            "operator did not ask for — set all four "
            "(MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / "
            "MINIO_BUCKET) or none of them.")
    secure_raw = (env.get("MINIO_SECURE") or "").strip().lower()
    if secure_raw:
        secure = secure_raw in ("1", "true", "yes", "on")
    else:
        # not stated: believe the endpoint. `http://` means somebody chose plain
        # HTTP (a laptop, a private network); anything else defaults to TLS,
        # because guessing wrong in that direction is the safe way to be wrong.
        secure = not found["endpoint"].startswith("http://")
    found["secure"] = secure
    return found


def asset_store_from_env(environ: Optional[Dict[str, str]] = None) -> AssetStore:
    """The asset store this process should use, chosen by configuration.

    Precedence, and each step is a deliberate answer to "what did the operator
    actually ask for?":

    1. **MinIO** when its settings are there — the deployment store, durable and
       outside this process (rule #2). Half-configured **refuses to start**.
    2. `EM_ASSET_DIR` — a directory. Honest for one instance, wrong for replicas.
    3. nothing — in-memory, which dies with the process. Loudly wrong for a
       deployment, which is why `/v1/health` reports which one is in use.
    """
    env = environ if environ is not None else os.environ
    minio = _minio_settings(env)
    if minio:
        return MinioAssetStore(endpoint=minio["endpoint"],
                               access_key=minio["access_key"],
                               secret_key=minio["secret_key"],
                               bucket=minio["bucket"], secure=minio["secure"])
    directory = env.get("EM_ASSET_DIR")
    if directory:
        return DirectoryAssetStore(directory)
    return InMemoryAssetStore()


#: This process's asset store. Built at import, like the snapshot one, so a
#: misconfiguration fails when the process starts rather than at the first upload.
ASSET_STORE: AssetStore = asset_store_from_env()

_HEX = set("0123456789abcdef")


def asset_ref_valid(ref: str) -> bool:
    """Is this a reference this store could ever have minted?

    Checked before touching the store: a reference comes from a URL, and a
    directory-backed implementation that took an arbitrary string would be one
    `../` away from reading somebody else's file. Content-addressing makes the
    check trivial — a valid reference has exactly one shape.
    """
    prefix, _, digest = str(ref).partition(":")
    return (prefix == DIGEST_PREFIX and len(digest) == 64
            and all(c in _HEX for c in digest.lower()))


def describe(store: AssetStore) -> str:
    if isinstance(store, MinioAssetStore):
        # the bucket and the host, never the key: `/v1/health` is public enough
        # that a credential printed there is a credential in a screenshot
        return f"minio ({store.endpoint}, bucket {store.bucket})"
    return {
        "InMemoryAssetStore": "memory (not durable — dies with the process)",
        "DirectoryAssetStore": "directory (local only — not for replicas)",
    }.get(type(store).__name__, type(store).__name__)
