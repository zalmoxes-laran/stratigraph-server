"""The `.blend` safety archive: opaque, deliberate, and nobody else's business.

Four things this must not become, and one test each:

* **a second versioning system.** The shared data is content-addressed and the
  history is the DTC. Nothing here touches em.json, the glTF or the resources,
  and there is no key-versioning: a different content is already a different key;
* **an accidental publishable asset.** A snapshot lives in its own namespace, is
  cited by nothing, and the rights gate never sees it. If a backup ever showed up
  among the assets, everything the corpus says about licences would suddenly have
  an opinion about somebody's work in progress;
* **a save hook.** Deliberate means a person decided. Re-archiving the same bytes
  writes nothing and does NOT move the date — a backup whose date drifts is not a
  backup;
* **a shared folder.** The register is per-author: room membership is what lets
  you keep a snapshot, authorship is what lets you read it back.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app import main as main_module                            # noqa: E402
from app import ws as ws_module                                # noqa: E402
from app.access import InMemoryAclStore, Role                  # noqa: E402
from app.blend_backups import (BlendBackups, DirectoryBackupBlobs,
                               DirectoryBackupRegister, InMemoryBackupBlobs,
                               InMemoryBackupRegister, blobs_from_env,
                               digest_of)
from app.main import app                                       # noqa: E402
from app.rooms import RoomRegistry                             # noqa: E402
from app.store import (InMemoryRoomStore,                      # noqa: E402
                       InMemorySnapshotStore)

ANNA = "0000-0002-1825-0097"
BRUNO = "0000-0001-5109-3700"
AUTH = {"Authorization": "Bearer t"}
ROOM = "scavo"

# Not a real .blend, and it does not need to be: the whole point is that the
# store never looks inside. That IS the invariant.
BLEND = b"BLENDER-v405RENDH" + b"\x00opaque bytes, nobody parses these\x00" * 40
CHANGED = BLEND + b"one more object"


def container() -> dict:
    return {"header": {"format": "em.json", "version": "1.0", "owner": ANNA},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [], "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture
def instance(monkeypatch, tmp_path):
    snapshots = InMemorySnapshotStore()
    snapshots.put(ROOM, container())
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", snapshots)
    monkeypatch.setattr(ws_module, "ROOMS",
                        RoomRegistry(snapshots, InMemoryRoomStore()))
    acls = InMemoryAclStore()
    acls.put(ROOM, {"owner": ANNA, "members": {BRUNO: Role.EDITOR.value}})
    monkeypatch.setattr(ws_module, "ACL_STORE", acls)
    backups = BlendBackups(DirectoryBackupBlobs(tmp_path / "blobs"),
                           DirectoryBackupRegister(tmp_path / "register"))
    monkeypatch.setattr(main_module, "BACKUPS", backups)
    return backups


@pytest.fixture
def enforcing(monkeypatch):
    class Enforcing:
        enforcing = True

        def describe(self):
            return "keycloak"

    for module in (ws_module, main_module):
        monkeypatch.setattr(module.authenticator, "settings", Enforcing())

    def be(orcid):
        for module in (ws_module, main_module):
            monkeypatch.setattr(module.authenticator, "verify",
                                lambda token: {"orcid": orcid})
    return be


@pytest.fixture
def client():
    return TestClient(app)


# ── the store itself ─────────────────────────────────────────────────────────

def test_the_key_is_the_content_so_dedup_is_not_a_feature(tmp_path):
    """Re-archiving unchanged bytes stores nothing. Not because a comparison ran
    — because the name of an object IS its content."""
    blobs = DirectoryBackupBlobs(tmp_path)
    first = blobs.put(BLEND)
    second = blobs.put(BLEND)
    assert first["sha256"] == hashlib.sha256(BLEND).hexdigest()
    assert first["created"] is True and second["created"] is False
    assert len(list(tmp_path.iterdir())) == 1
    assert blobs.put(CHANGED)["created"] is True
    assert len(list(tmp_path.iterdir())) == 2


def test_the_bytes_come_back_exactly(tmp_path):
    blobs = DirectoryBackupBlobs(tmp_path)
    sha = blobs.put(BLEND)["sha256"]
    restored = blobs.get(sha)
    assert restored == BLEND
    assert hashlib.sha256(restored).hexdigest() == sha


def test_a_snapshot_is_not_in_the_assets_namespace(monkeypatch, tmp_path):
    """The separation that makes «opaque» operational: a backup must not be
    reachable as `sha256:<hex>` through the asset route, or the rights gate would
    have to have an opinion about somebody's work in progress."""
    from app import assets

    store = assets.DirectoryAssetStore(tmp_path / "assets")
    blobs = DirectoryBackupBlobs(tmp_path / "backups")
    sha = blobs.put(BLEND)["sha256"]
    assert store.get(f"sha256:{sha}") is None, "a backup leaked into the assets"
    assert store.head(f"sha256:{sha}") is None


def test_minio_backups_get_their_own_prefix(monkeypatch):
    """A prefix, not the flat digest namespace the assets use. Checked on the KEY
    rather than by uploading, because the property is about where it would go."""
    monkeypatch.setattr("app.blend_backups.MinioBackupBlobs.__init__",
                        lambda self, *a, **k: None)
    from app.blend_backups import BACKUP_PREFIX, MinioBackupBlobs

    store = MinioBackupBlobs.__new__(MinioBackupBlobs)
    store.prefix = BACKUP_PREFIX
    key = store._key(digest_of(BLEND))
    assert key.startswith("blend-backups/")
    assert key != digest_of(BLEND), "same key as an asset = same namespace"


def test_a_node_with_nowhere_to_put_them_does_not_pretend(monkeypatch):
    """Memory is the last resort and it is reported (see `/v1/health`) — a safety
    copy that dies with the process must not be a surprise."""
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    assert isinstance(blobs_from_env({}), InMemoryBackupBlobs)
    assert isinstance(blobs_from_env({"EM_SNAPSHOT_DIR": "/tmp/em-x"}),
                      DirectoryBackupBlobs)


# ── the register: the date does not move, and it is per-author ───────────────

def test_re_archiving_does_not_rewrite_the_first_record():
    service = BlendBackups(InMemoryBackupBlobs(), InMemoryBackupRegister())
    first = service.archive(ROOM, BLEND, orcid=ANNA, label="before the cut",
                            filename="scavo.blend")
    again = service.archive(ROOM, BLEND, orcid=ANNA, label="a different label")
    assert again["created"] is False
    assert again["created_at"] == first["created_at"], "the date moved"
    assert again["label"] == "before the cut", "the label was rewritten"
    assert again["seen"] == 2, "…but being kept again is a fact worth recording"
    assert len(service.mine(ROOM, orcid=ANNA)) == 1


def test_a_snapshot_in_another_room_is_a_snapshot_even_if_the_bytes_are_there():
    """The distinction, measured live before it was written: the object store is
    node-wide, so the same `.blend` kept in two rooms finds the bytes already
    there — and it is still a snapshot the person just took. `created` is about
    the record, `stored_bytes` about the object."""
    service = BlendBackups(InMemoryBackupBlobs(), InMemoryBackupRegister())
    first = service.archive("scavo", BLEND, orcid=ANNA, label="here")
    assert first["created"] is True and first["stored_bytes"] is True
    elsewhere = service.archive("mostra", BLEND, orcid=ANNA, label="and there")
    assert elsewhere["created"] is True, "a snapshot in another room is a snapshot"
    assert elsewhere["stored_bytes"] is False, "…of the same object"
    assert len(service.mine("mostra", orcid=ANNA)) == 1


def test_a_changed_blend_is_a_new_snapshot():
    service = BlendBackups(InMemoryBackupBlobs(), InMemoryBackupRegister())
    service.archive(ROOM, BLEND, orcid=ANNA, label="one")
    service.archive(ROOM, CHANGED, orcid=ANNA, label="two")
    kept = service.mine(ROOM, orcid=ANNA)
    assert len(kept) == 2
    assert {r["sha256"] for r in kept} == {digest_of(BLEND), digest_of(CHANGED)}


def test_the_note_is_a_backup_and_says_it_is_not_publishable():
    service = BlendBackups(InMemoryBackupBlobs(), InMemoryBackupRegister())
    record = service.archive(ROOM, BLEND, orcid=ANNA, label="x")
    note = record["dtc"]
    assert note["kind"] == "backup"
    assert note["by"] == ANNA and note["at"]
    assert note["about"] == f"sha256:{digest_of(BLEND)}"
    assert note["publishable"] is False and note["opaque"] is True


def test_one_persons_snapshot_is_not_anothers():
    """The rule, not an omission: being an editor of the room is what let you
    archive. It does not make somebody else's working file yours to read."""
    service = BlendBackups(InMemoryBackupBlobs(), InMemoryBackupRegister())
    service.archive(ROOM, BLEND, orcid=ANNA, label="anna's")
    assert service.mine(ROOM, orcid=BRUNO) == []
    assert service.fetch(ROOM, digest_of(BLEND), orcid=BRUNO) is None
    assert service.fetch(ROOM, digest_of(BLEND), orcid=ANNA) == BLEND


# ── the door ─────────────────────────────────────────────────────────────────

def test_archive_list_restore_over_http(client, instance, enforcing):
    enforcing(ANNA)
    kept = client.put(f"/v1/rooms/{ROOM}/blend-backup?label=prima%20del%20taglio"
                      f"&filename=scavo.blend", content=BLEND, headers=AUTH)
    assert kept.status_code == 200, kept.text
    body = kept.json()
    assert body["sha256"] == digest_of(BLEND)
    assert body["created"] is True and body["orcid"] == ANNA
    assert body["label"] == "prima del taglio"
    assert body["dtc"]["kind"] == "backup"

    # the same bytes again: one object, one record, and it SAYS so
    twice = client.put(f"/v1/rooms/{ROOM}/blend-backup", content=BLEND,
                       headers=AUTH).json()
    assert twice["created"] is False
    assert "already kept" in twice["detail"]

    listing = client.get(f"/v1/rooms/{ROOM}/blend-backups", headers=AUTH).json()
    assert [r["sha256"] for r in listing] == [digest_of(BLEND)]
    assert listing[0]["size"] == len(BLEND)

    back = client.get(f"/v1/rooms/{ROOM}/blend-backup/{digest_of(BLEND)}",
                      headers=AUTH)
    assert back.status_code == 200
    assert back.content == BLEND, "the exact bytes"
    assert hashlib.sha256(back.content).hexdigest() == digest_of(BLEND)
    assert back.headers["x-em-opaque-backup"] == "true"


def test_an_empty_body_is_not_a_snapshot(client, instance, enforcing):
    enforcing(ANNA)
    answer = client.put(f"/v1/rooms/{ROOM}/blend-backup", content=b"",
                        headers=AUTH)
    assert answer.status_code == 400
    assert "zero bytes" in answer.json()["detail"]


def test_the_door_is_fail_closed(client, instance, enforcing):
    """No token, and a token with no role here — both refused, and the second one
    is refused even though the room exists and the caller is somebody."""
    assert client.put(f"/v1/rooms/{ROOM}/blend-backup",
                      content=BLEND).status_code == 401
    assert client.get(f"/v1/rooms/{ROOM}/blend-backups").status_code == 401
    enforcing("0000-0003-1415-9265")             # a stranger to this room
    for call in (client.put(f"/v1/rooms/{ROOM}/blend-backup", content=BLEND,
                            headers=AUTH),
                 client.get(f"/v1/rooms/{ROOM}/blend-backups", headers=AUTH),
                 client.get(f"/v1/rooms/{ROOM}/blend-backup/{digest_of(BLEND)}",
                            headers=AUTH)):
        assert call.status_code == 403, call.text
        assert "editor" in call.json()["detail"]


def test_a_viewer_may_not_keep_their_working_file_here(client, instance,
                                                       enforcing, monkeypatch):
    """A room you may only read is not a room you may store files in."""
    acls = ws_module.ACL_STORE
    acls.put(ROOM, {"owner": ANNA, "members": {BRUNO: Role.VIEWER.value}})
    enforcing(BRUNO)
    assert client.put(f"/v1/rooms/{ROOM}/blend-backup", content=BLEND,
                      headers=AUTH).status_code == 403


def test_a_stranger_does_not_list_or_probe_somebody_elses(client, instance,
                                                          enforcing):
    """BRUNO is an editor here — he may keep his own snapshots and still must not
    see ANNA's, nor learn that a digest exists by asking for it."""
    enforcing(ANNA)
    client.put(f"/v1/rooms/{ROOM}/blend-backup?label=anna", content=BLEND,
               headers=AUTH)
    enforcing(BRUNO)
    assert client.get(f"/v1/rooms/{ROOM}/blend-backups", headers=AUTH).json() == []
    probe = client.get(f"/v1/rooms/{ROOM}/blend-backup/{digest_of(BLEND)}",
                       headers=AUTH)
    assert probe.status_code == 404, "a 403 here would confirm the digest exists"


def test_the_shared_data_is_untouched(client, instance, enforcing):
    """The invariant, asserted rather than asserted-in-prose: archiving a `.blend`
    adds nothing to the room's document and nothing to the asset store."""
    from app.assets import ASSET_STORE

    before = ws_module.SNAPSHOT_STORE.get(ROOM)
    count_before = getattr(ASSET_STORE, "count", lambda: 0)()
    enforcing(ANNA)
    client.put(f"/v1/rooms/{ROOM}/blend-backup", content=BLEND, headers=AUTH)
    assert ws_module.SNAPSHOT_STORE.get(ROOM) == before
    assert getattr(ASSET_STORE, "count", lambda: 0)() == count_before
    assert ASSET_STORE.get(f"sha256:{digest_of(BLEND)}") is None


def test_health_says_where_the_snapshots_go(client):
    payload = client.get("/health").json()
    assert "blend_backup_store" in payload
