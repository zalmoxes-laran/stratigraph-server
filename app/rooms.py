"""The room: a study, its working copy, its op-log, and who is in it.

P4.2 is where StratiGraph Server stops being stateless, and the honest thing to do is to
say exactly where the state is and why it is allowed. Three fences, and they are
the contract:

1. **The durable truth is outside the process** — `store.py`. What lives here is
   a *working copy*, rebuilt from the snapshot when a cold room is opened. The
   process can die; the study does not.
2. **Convergence lives in s3Dgraphy.** This module calls `s3dgraphy.api` and
   contains no merge, no clock comparison, no tombstone rule. The relay applies
   and re-broadcasts; the CRDT converges regardless of order, which is exactly
   why a relay is enough and a transformer (OT) is not needed.
3. **Presence is ephemeral.** Who is connected lives in RAM, is lost on restart,
   and that is correct: presence is about *now*. Nothing in it is durable, so
   nothing in it needs a store.

4. **The room's own record is DURABLE and THIN** (`RoomDescriptor`, below): a
   room exists before anybody connects, it can be listed, and it *references*
   1..N containers instead of being one. Everything heavy stays in the
   containers, the object store and the DTC corpus — and membership stays in
   `access.py`, projected on read rather than copied in.

Scaling seam, declared and NOT implemented: one instance owns a room (sticky
routing by `room_id`). Several replicas would need the op-log outside the process
— a Redis stream or the object store — and every `broadcast` here would become a
publish. The shape is ready for that; tonight it would be an untested moving part
in a component that just gained state.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from s3dgraphy import api as em

from .store import RoomStore, SnapshotStore, deep_copy, room_store_from_env

#: How many recent operations a room keeps so a late arrival can catch up
#: without a fresh snapshot. Bounded on purpose: an unbounded log is a memory
#: leak with a good excuse.
OPLOG_LIMIT = 512

#: "not looked up yet", distinct from "looked up and there is none".
_UNREAD = object()


def now_iso() -> str:
    """The clock this server stamps with — UTC, seconds, the EM spelling."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Member:
    """One connected client. Ephemeral by construction — see fence 3."""

    connection_id: str
    #: the ORCID (or subject) from the TOKEN, never what the client said it was
    author: Optional[str]
    display: str = ""
    #: what this member may do here (`access.Role`). Resolved at the door and
    #: carried, so the write gate is a comparison and not a second lookup —
    #: re-resolving per message would let a revocation take effect mid-session
    #: for some verbs and not others, which is worse than a rule that holds for
    #: the length of a connection and is re-read when they come back.
    role: Optional[Any] = None
    #: what this member has selected — the awareness channel, soft, no locks
    selection: List[str] = field(default_factory=list)
    #: the instant of the last operation this member has been sent. The minimum
    #: across members is what makes compaction safe (see `gc_watermark`).
    watermark: Optional[str] = None
    joined_at: str = field(default_factory=now_iso)

    def as_presence(self) -> Dict[str, Any]:
        # The role travels with presence too: "who is here" and "who may write"
        # is one question for the person reading the roster, and a client that
        # had to ask separately would draw a room where everybody looks alike.
        return {"id": self.connection_id, "author": self.author,
                "display": self.display, "selection": list(self.selection),
                "role": getattr(self.role, "value", self.role),
                "joined_at": self.joined_at}


class Room:
    """One study, live.

    The working copy is a container document (`{"graphs": {...}}`) — the same
    shape the file on disk has, because the relay must never invent a second
    representation of a project.
    """

    def __init__(self, room_id: str, document: Dict[str, Any]):
        self.room_id = room_id
        self.document = document
        self.oplog: List[Dict[str, Any]] = []
        self.members: Dict[str, Member] = {}
        self.sockets: Dict[str, Any] = {}
        self.lock = asyncio.Lock()
        self.snapshot_at: Optional[str] = None
        self.last_op_at: Optional[str] = None
        #: P4.3 · how far this room has been COMPACTED. Announced to every client
        #: (`host_info`, `snapshot`) because it is the one number a client needs
        #: to know whether its own history is still reconcilable here: below this
        #: point the room no longer holds what a replay would argue with.
        self.compacted_upto: Optional[str] = None
        #: lazily read, see the `embargo` property. `_UNREAD` and not None
        #: because "no embargo" is an answer worth remembering too.
        self._embargo: Any = _UNREAD
        #: Bumped on every write to this room's document. The digest index
        #: (`app/digest_index.py`) watches it, and that is the whole design: an
        #: index that expired on a TIMER would be a *cache of an embargo*, and an
        #: embargo read from a cache is precisely what the asset gate promises
        #: not to do. A revision is not a clock — it changes when the document
        #: changes, which is the only moment the answer can.
        self.revision: int = 0
        #: …and WHICH working copy this is. A room dropped and rebuilt (a
        #: `forget`, a fresh registry, a snapshot restored) is a different
        #: document with the same name and a revision that starts again at zero:
        #: without this, a derived index keyed on (room_id, revision) would hand
        #: the new room the old room's answers. Measured — two tests, same room
        #: id, different embargo, and the second read the first's.
        self.instance: str = uuid.uuid4().hex[:12]

    # ── who may read this study ──────────────────────────────────────────────

    @property
    def visibility(self) -> str:
        """`public` or `restricted` — read from the STUDY, not from a config.

        It belongs in the document's header because it is a fact about the work
        (D2.2 §3.4: dissemination is a *validated* tier, in-progress is not), and
        a study that travels — a file somebody sends, a snapshot restored
        elsewhere — must carry its own answer rather than inherit whatever the
        new server happens to think.

        **Restricted is the default, and unknown reads as restricted.** The
        failure directions are not symmetric: a public study served behind a
        token annoys somebody, an in-progress study served without one publishes
        an interpretation nobody has finished making.
        """
        header = self.document.get("header")
        value = str((header or {}).get("visibility") or "").strip().lower()
        return "public" if value == "public" else "restricted"

    @property
    def is_public(self) -> bool:
        return self.visibility == "public"

    @property
    def embargo(self) -> Optional[str]:
        """The study's embargo, if it declares one — the room's temporal gate.

        `header.embargo` first because it is cheap and it is where a room-level
        embargo would be written; failing that, the graph-scope node the Catalog
        reads (DP-65), through the library, so the door and the listing cannot
        disagree about whether a study is still under embargo.

        Computed once per room instance and remembered: it walks the container,
        and a join is not the place to do that twice. A room that is dropped and
        rebuilt (`forget`) reads it again.
        """
        if self._embargo is not _UNREAD:
            return self._embargo
        header = self.document.get("header") or {}
        declared = header.get("embargo") or header.get("embargo_until")
        if declared:
            self._embargo = str(declared)
            return self._embargo
        try:
            from s3dgraphy.study import study_metadata
            self._embargo = study_metadata(self.document).get("embargo")
        except Exception:      # an unreadable container declares no embargo
            self._embargo = None
        return self._embargo

    # ── membership ───────────────────────────────────────────────────────────

    def join(self, connection_id: str, socket: Any, author: Optional[str],
             display: str = "", role: Any = None) -> Member:
        member = Member(connection_id=connection_id, author=author,
                        display=display or (author or "anon"), role=role)
        self.members[connection_id] = member
        self.sockets[connection_id] = socket
        return member

    def leave(self, connection_id: str) -> None:
        self.members.pop(connection_id, None)
        self.sockets.pop(connection_id, None)

    def presence(self) -> List[Dict[str, Any]]:
        return [m.as_presence() for m in self.members.values()]

    # ── the op-log ───────────────────────────────────────────────────────────

    def record(self, op: Dict[str, Any]) -> None:
        self.oplog.append(op)
        if len(self.oplog) > OPLOG_LIMIT:
            del self.oplog[: len(self.oplog) - OPLOG_LIMIT]
        self.last_op_at = str(op.get("ts") or now_iso())

    def replay_since(self, since: Optional[str]) -> List[Dict[str, Any]]:
        """The operations after `since` — what a late arrival missed.

        With no `since` the caller gets nothing: it is being handed the snapshot,
        which already contains everything, and replaying the log on top would
        only re-apply what is there (harmless, because the ops are idempotent,
        and pointless, which is the better reason not to).
        """
        if not since:
            return []
        return [op for op in self.oplog if str(op.get("ts") or "") > since]

    # ── the operations (the library does the work) ───────────────────────────

    def touch(self) -> None:
        """The document changed. Say so, once, where everything derived can see."""
        self.revision += 1

    def apply(self, op: Dict[str, Any], graph_id: Optional[str] = None) -> Dict[str, Any]:
        """Apply ONE operation to the working copy, through s3Dgraphy.

        Fence 2 in one line: the relay does not decide anything about the
        operation — `em.apply_op` does, with the same code the offline merge and
        EMStudio's own copy run. A stale operation comes back `applied: False`,
        and the relay does not re-broadcast it as if it were news.
        """
        section = self._section(graph_id)
        if section is None:
            return {"applied": False, "reason": "no such graph in this room"}
        result = em.apply_op(section, op)
        if result.get("applied"):
            self.touch()
        return result

    def _section(self, graph_id: Optional[str]) -> Optional[Dict[str, Any]]:
        graphs = self.document.get("graphs") or {}
        if graph_id and graph_id in graphs:
            return graphs[graph_id]
        active = self.document.get("active_graph_id")
        if active and active in graphs:
            return graphs[active]
        return next(iter(graphs.values()), None)

    # ── snapshot + GC ────────────────────────────────────────────────────────

    def gc_watermark(self) -> Optional[str]:
        """The instant every connected member has been brought past.

        The safety argument for compaction, and the reason it is a MINIMUM: a
        member still catching up may yet be sent an operation older than the
        others have seen, and compacting past that point would let a late
        operation win against a fallback instead of against the real clock.

        With nobody connected there is nothing to protect — but nothing to
        promise either: an absent client can come back with an old op-log, which
        this cannot know about. That is the declared limit of GC at this stage.
        """
        marks = [m.watermark for m in self.members.values() if m.watermark]
        if not marks:
            return None
        return min(marks)

    def snapshot(self, store: SnapshotStore, *, gc: bool = True) -> Dict[str, Any]:
        """Write the room to the durable store, compact, and truncate the log.

        The order matters: compaction happens on the working copy BEFORE the
        write, so the snapshot is the compacted one and a restart does not
        resurrect the bookkeeping that was just settled.
        """
        report: Dict[str, Any] = {}
        before = self.gc_watermark()
        if gc and before:
            # fence 2 again: the GC rule is s3Dgraphy's, invoked from here
            report = em.compact(self.document, before_ts=before)
            # …and the point is REMEMBERED, because a client that was away has to
            # be able to ask "is my history still worth anything here?"
            self.compacted_upto = before
        store.put(self.room_id, self.document)
        self.snapshot_at = now_iso()
        if before:
            # the log up to the watermark is now inside the snapshot
            self.oplog = [op for op in self.oplog if str(op.get("ts") or "") > before]
        return {"at": self.snapshot_at, "compaction": report,
                "gc_watermark": self.compacted_upto,
                "oplog": len(self.oplog),
                "stats": em.crdt_stats(self.document)}


# ── the room as a DURABLE, THIN descriptor ───────────────────────────────────
#
# Until now a room WAS a container: `room_id` was the snapshot key, and a room
# that nobody had written did not exist at all. That is enough for a relay and not
# enough for a workspace — you cannot name a room, list it, invite somebody to it,
# or have it hold two studies in progress.
#
# So a room gets a record. Two properties, and they are the whole design:
#
# **DURABLE** — it exists before anybody connects and after everybody leaves. A
# room is a place, and a place that only exists while somebody is standing in it
# is a meeting, not a room.
#
# **THIN** — `{room_id, title, container_refs, created_by, created_at}` and
# nothing else. Every heavy thing stays where it already lives: the graph in the
# em.json containers, the bytes in MinIO, the rights in the DTC corpus. The
# register is a small table, not a second truth. The day it starts carrying a
# copy of something the container already says, it becomes a thing to keep in
# step, and the copy will be the one somebody reads.
#
# **A room REFERENCES containers; it is not one.** `container_refs` is a LIST
# because a workspace holds what is in progress — several containers being worked
# towards one publication — while a *study* is the published, citable unit the
# Catalog lists. Different stages of the same lifecycle, and collapsing them
# would mean either a workspace you cannot cite or a citation that keeps moving.
#
# **Membership is NOT stored here.** Reading a descriptor answers with `members`,
# and that list is PROJECTED from the ACL (`access.py`) every time. Writing it
# into the record would make two places able to disagree about who may write,
# which is the one question that must have a single answer. The record holds what
# only it knows.

#: A room with no descriptor is not an error: every room that existed before this
#: record did is exactly that, and it must keep working. `implicit()` is what it
#: means — one container, named after the room, nothing declared.
@dataclass
class RoomDescriptor:
    """The durable, thin record of one room."""

    room_id: str
    title: str = ""
    #: 1..N containers this room works on. The FIRST that has a snapshot is the
    #: live document (see `RoomRegistry.get`); the rest are declared and carried.
    #: Multi-container editing in one session is a follow-up — the list is the
    #: data model for it, not the implementation of it.
    container_refs: List[str] = field(default_factory=list)
    created_by: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    #: When an operator ARCHIVED it. A room is never deleted by a policy: an
    #: orphan is reported and, at most, marked — a workspace that vanished
    #: because a container was moved is a data loss nobody consented to.
    archived_at: Optional[str] = None
    #: True when this record was invented on the fly for a room that predates the
    #: register. Never written to the store: it says "nobody declared this".
    implicit: bool = False

    @classmethod
    def implicit_for(cls, room_id: str) -> "RoomDescriptor":
        return cls(room_id=room_id, title=room_id, container_refs=[room_id],
                   implicit=True)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "RoomDescriptor":
        refs = raw.get("container_refs") or []
        if isinstance(refs, str):              # a single ref, written by hand
            refs = [refs]
        room_id = str(raw.get("room_id") or "")
        return cls(
            room_id=room_id,
            title=str(raw.get("title") or room_id),
            container_refs=[str(r) for r in refs if str(r)] or [room_id],
            created_by=(str(raw["created_by"])
                        if raw.get("created_by") else None),
            created_at=str(raw.get("created_at") or now_iso()),
            archived_at=(str(raw["archived_at"])
                         if raw.get("archived_at") else None))

    def as_dict(self) -> Dict[str, Any]:
        """What goes to the store — and `members` is deliberately not in it."""
        return {"room_id": self.room_id, "title": self.title,
                "container_refs": list(self.container_refs),
                "created_by": self.created_by, "created_at": self.created_at,
                "archived_at": self.archived_at}

    @property
    def primary_ref(self) -> str:
        """The container the live document comes from. A room with one ref — every
        room that existed before this record — resolves to exactly what it did."""
        return self.container_refs[0] if self.container_refs else self.room_id


# The stores for these records live in `store.py`, with the snapshot store —
# `RoomStore` / `room_store_from_env`, imported above. Not here: this module is
# fenced off from the filesystem on purpose (`ws.py` and `rooms.py` never open a
# file; the relay writes through an interface), and a test says so out loud.


class RoomRegistry:
    """The rooms this instance owns.

    Sticky by design: one instance owns a room. The seam for horizontal scaling
    is `get`/`broadcast` — a multi-replica deployment would resolve a room
    through a shared op-log instead of this dict. Not tonight (see the module
    docstring), and not precluded.
    """

    def __init__(self, store: SnapshotStore,
                 rooms_store: Optional[RoomStore] = None):
        self.store = store
        #: the durable register. Its own store, beside the others, for the reason
        #: `access.py` gives: a room's title and its containers are operational
        #: facts, and they do not belong inside the study.
        self.rooms_store: RoomStore = (rooms_store if rooms_store is not None
                                       else room_store_from_env())
        self._rooms: Dict[str, Room] = {}
        self._lock = asyncio.Lock()

    # ── the durable register ────────────────────────────────────────────────

    def descriptor(self, room_id: str) -> RoomDescriptor:
        """This room's record — or the IMPLICIT one for a room that predates the
        register. Never None: every room this relay can open is describable, and
        a caller that had to handle "no record" would end up inventing one badly.
        Ask `.implicit` when the difference matters."""
        raw = self.rooms_store.get(room_id)
        if not raw:
            return RoomDescriptor.implicit_for(room_id)
        record = RoomDescriptor.from_dict({**raw, "room_id": room_id})
        return record

    def declare(self, descriptor: RoomDescriptor) -> RoomDescriptor:
        """Write a record. The only way a room becomes explicit."""
        descriptor.implicit = False
        self.rooms_store.put(descriptor.room_id, descriptor.as_dict())
        return descriptor

    def create(self, room_id: str, *, title: str = "",
               container_refs: Optional[List[str]] = None,
               created_by: Optional[str] = None) -> RoomDescriptor:
        """Declare a room. It exists from this moment — empty, listable, with no
        snapshot and nobody connected, which is the whole point of the record."""
        refs = [str(r) for r in (container_refs or [room_id]) if str(r)]
        return self.declare(RoomDescriptor(
            room_id=room_id, title=title or room_id,
            container_refs=refs or [room_id], created_by=created_by))

    def declared(self) -> List[RoomDescriptor]:
        """Every DECLARED room, by id — the register, enumerated. Rooms that only
        exist as a snapshot are not in here: they were never declared, and
        inventing entries for them would make the register a guess."""
        return [self.descriptor(room_id) for room_id in self.rooms_store.ids()]

    def archive(self, room_id: str, *, archived: bool = True,
                at: Optional[str] = None) -> RoomDescriptor:
        """Mark a room archived — or bring it back. **Never a deletion.**

        The policy the prompt for this work asked for, and the reason it is a
        flag and not a `del`: a workspace goes quiet for a season (a dig is
        seasonal), and a container gets moved or renamed by somebody tidying up.
        Neither is consent to lose the record of what the room was, who made it
        and what it pointed at. An archived room is still listed, still says its
        title, and still reports its missing references — it is just marked, and
        somebody can un-mark it.

        Deletion, if it is ever wanted, is a separate act with a separate name and
        a confirmation, not the tail end of a garbage collector.
        """
        descriptor = self.descriptor(room_id)
        descriptor.archived_at = (at or now_iso()) if archived else None
        return self.declare(descriptor)

    def orphans(self) -> List[RoomDescriptor]:
        """Declared rooms pointing at containers the store does not have.

        The GC's *report*, which is all a GC gets to be here: it names what is
        dangling and leaves the decision to a person (`archive`). A sweep that
        removed them would be a policy nobody wrote down, executed on a schedule.
        """
        return [d for d in self.declared() if self.missing_refs(d)]

    def missing_refs(self, descriptor: RoomDescriptor) -> List[str]:
        """Which of this room's containers the snapshot store does not have.

        REPORTED, never raised. A room pointing at a container somebody moved is a
        real state — the workspace still exists, its title is still meaningful,
        and the honest answer to "open it" is a sentence about the missing
        reference rather than a 500 or an empty document pretending to be the
        study.
        """
        return [ref for ref in descriptor.container_refs
                if self.store.get(ref) is None]

    async def get(self, room_id: str) -> Room:
        async with self._lock:
            room = self._rooms.get(room_id)
            if room is not None:
                return room
            # The document comes from the room's PRIMARY container reference —
            # which for every room that predates the register is the room id
            # itself, so a one-container room resolves exactly as it always did.
            descriptor = self.descriptor(room_id)
            snapshot = None
            for ref in descriptor.container_refs or [room_id]:
                snapshot = self.store.get(ref)
                if snapshot is not None:
                    break
            document = deep_copy(snapshot) if snapshot else _empty_container(room_id)
            room = Room(room_id, document)
            self._rooms[room_id] = room
            return room

    def peek(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def forget(self, room_id: str) -> None:
        """Drop the working copy. The next join rebuilds it from the store —
        which is the property that makes this state a cache and not a home."""
        self._rooms.pop(room_id, None)

    def rooms(self) -> List[str]:
        return sorted(self._rooms)


def _empty_container(room_id: str) -> Dict[str, Any]:
    """A room nobody has ever written: an empty container-of-one.

    A container, not a bare graph, because that is what an em.json IS since the
    multigraph decision — a relay that invented a different starting shape would
    hand its first client a document the rest of the ecosystem does not read.
    """
    return {
        "header": {"format": "em.json", "version": "1.0"},
        "graphs": {room_id: {"graph_id": room_id, "name": room_id,
                             "nodes": [], "edges": []}},
        "active_graph_id": room_id,
    }
