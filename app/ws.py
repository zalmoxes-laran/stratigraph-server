"""The relay: `/v1/rooms/{id}/ws` — StratiGraph Server as "just another host".

It speaks **the wire that already exists** (ADR-002: `snapshot`, `op`,
`host_info`, `select`, `command`). That is the whole trick of this step: when
EMStudio points at an StratiGraph Server it does not need a new protocol, because the
relay is a host that happens to have several clients instead of one.

**WIRE 2 · the body travels nested.** The envelope is `{v, type, source,
graph_id?}` and everything type-specific is inside `payload` — see `wire.py` for
the bug that taught us why. The consequence here is the important one: for an
`op` the relay treats the payload as **opaque**. It stamps the author, it dates
it, it hands it to the library and it forwards it verbatim. It never reaches
into it for a field, so no word of the body can ever collide with a word of the
wire again (an edge's `source` used to be eaten by the envelope's).

What the relay does with an operation is **apply it through s3Dgraphy and pass it
on**. It does not transform, order or reconcile anything: the CRDT of P4.1
converges whatever the order, which is precisely why a *relay* is enough and an
operational-transform server is not needed. If this file ever grows a merge rule,
the rule is in the wrong repository.

Authentication: the connection carries a token, verified with the same
`app/auth.py` the HTTP routes use. The **author of every operation is the token's
identity**, never what the client wrote in the message — an author a client can
declare is an author anybody can borrow, and P4.1b made the stamp the thing the
merge trusts.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from . import access
from .access import (Acl, Groups, Role, acl_store_from_env,
                     group_store_from_env)
from .auth import authenticator
from . import keeping
from .rooms import RoomRegistry, now_iso
from .store import store_from_env
from .wire import WIRE, WireError, envelope, read

#: This process's rooms and the store behind them. Built at import so a
#: misconfigured store fails when the process starts, not at the first join.
SNAPSHOT_STORE = store_from_env()
ROOMS = RoomRegistry(SNAPSHOT_STORE)

#: Who may do what, per room. A separate store from the snapshots on purpose:
#: the access list is operational and the study is scientific, and putting the
#: ACL inside the em.json would ship an access-control list with the record of
#: what was found (see `access.py`).
ACL_STORE = acl_store_from_env()

#: …and the registry of WHO IS IN WHICH GROUP. Separate from the ACLs because it
#: is instance-wide: a group is a set of people, and the rooms grant roles to the
#: name rather than to six ORCIDs each.
GROUP_STORE = group_store_from_env()


def groups() -> Groups:
    """The group registry, resolved when asked (see `main.rooms()` for why the
    import-time binding is a trap this codebase has already fallen into)."""
    return Groups(GROUP_STORE)

#: The wire version lives in `wire.py` now — one definition for every speaker in
#: this process, so a bump cannot be half-applied.

#: What this host calls itself in `host_info` — a client shows it in its footer.
HOST_TOOL = "StratiGraph Server (relay)"

#: The verbs that CHANGE something and therefore need `editor` or better.
#: `select` and `ack` are not here and must not be: awareness is not writing,
#: and a viewer whose cursor nobody could see would be a ghost in the room.
#: `request_snapshot` is a read — asking for the document again is what a
#: viewer does when it loses its place.
_WRITING_VERBS = frozenset({"op", "request_save", "command"})


# ── LA RETE, E CHI LA STENDE ─────────────────────────────────────────────────
#
# `room.snapshot` si chiama da questo modulo e da nessun altro
# (`tests/test_write_paths.py`), quindi l'ATTO sta qui e la POLITICA sta in
# `app/keeping.py`. Il temporizzatore chiede; questa funzione sa dove si scrive.


async def _keep(room, why: str, *, asked: bool = False):
    """Scrivere la stanza, sotto il suo lock, e dirlo a chi c'è dentro.

    **Il lock non è una precauzione generica**: `apply_from_connector` tiene
    quello stesso lock per un lotto intero, perché «uno snapshot preso fra due
    operazioni di un delta sarebbe un grafo che nessuno ha scritto». Prendendolo
    anche qui, il salvataggio differito non può cadere in mezzo a un lotto: al
    massimo aspetta che finisca.

    `asked=True` quando è un `request_save`, e cambia due cose — **trovato
    rompendo `test_6c`, che è il motivo per cui il parametro esiste invece di
    una condizione sola**:

    * si scrive **anche se non c'è niente di nuovo**. Chi chiede vuole il file
      sul disco adesso, non un giudizio del server su quanto sia utile;
    * si risponde **sempre**. Il differito che salta un giro può tacere, perché
      nessuno lo stava aspettando; un client che ha chiesto sta aspettando, e il
      silenzio è la peggiore delle risposte possibili.
    """
    async with room.lock:
        if not room.unsaved and not asked:
            # Qualcun altro ha salvato mentre questo compito aspettava il lock.
            # Non è una corsa persa: è la corsa vinta da chi doveva vincerla.
            return None
        # ── DURARE E COMPATTARE SONO DUE COSE, E LA RETE NE FA UNA SOLA ──────
        #
        # `gc=False` quando non l'ha chiesto nessuno, e la ragione è misurata.
        # `gc_watermark()` è il minimo dei watermark dei connessi, e quei
        # watermark **li muove `_fanout` quando SPEDISCE**, non l'`ack` quando
        # il client conferma (ws.py, in fondo: `member.watermark = body["ts"]`
        # dentro il ciclo di invio). Quindi il watermark misura la CONSEGNA,
        # non l'applicazione.
        #
        # Finché si compattava solo quando un client lo chiedeva, era una sua
        # decisione. Una rete che scatta ogni 64 operazioni o ogni due secondi
        # renderebbe quella compattazione continua, e la finestra in cui il
        # bookkeeping di un client lento viene buttato mentre lui non l'ha
        # ancora applicato si allargherebbe di parecchio — **senza che nessuno
        # l'abbia chiesto**, che è esattamente il difetto che questa notte sta
        # riparando dall'altro lato.
        #
        # Quindi la rete tiene, e basta. Compattare resta un atto di chi sa
        # cosa sta facendo: `request_save`.
        info = room.snapshot(SNAPSHOT_STORE, gc=asked)
    info["why"] = why
    await _fanout(room, envelope("snapshot_written", info, source="em-server"))
    return info


#: Uno per processo, come le stanze.
KEEPER = keeping.Keeper(_keep)


def _keeping_info(room) -> dict:
    """Cosa il relay dice di sé a chi entra: se tiene lui la stanza, e con quali
    soglie. **I numeri viaggiano** perché un client che li conosce può decidere
    con cognizione — e perché una soglia che nessuno può leggere è una politica
    segreta."""
    return {"host_keeps": KEEPER.covers(room),
            "after_ops": KEEPER.after_ops,
            "after_quiet_seconds": KEEPER.after_quiet,
            **room.keeping()}

ws_router = APIRouter(prefix="/v1")


def _identity(claims: Dict[str, Any]) -> Optional[str]:
    """The author to stamp operations with, out of the token's claims.

    ORCID first, because in this ecosystem the ORCID iD **is** the identity
    (AUDIT1/ORCID batch); then the realm's preferred username, then the subject.
    In dev mode there is no token and therefore no author — and the honest
    answer is None, which the stamp treats as "unknown" rather than inventing
    somebody.
    """
    if claims.get("em_dev_mode"):
        return None
    for key in ("orcid", "ORCID", "preferred_username", "sub"):
        value = claims.get(key)
        if value:
            return str(value)
    return None


def load_acl(room_id: str) -> Acl:
    """This room's access list, read from the store every time it is asked.

    Not cached: a grant or a revocation must take effect at the next door, and a
    cache would need an invalidation channel between REST and the relay for no
    gain — an ACL is read once per join and once per management call.
    """
    return Acl.from_dict(ACL_STORE.get(room_id))


def save_acl(room_id: str, acl: Acl) -> None:
    ACL_STORE.put(room_id, acl.as_dict())


def authorize(room, author: Optional[str], *, dev_mode: bool = False
              ) -> Optional[Role]:
    """The role this person has in this room, or None for "not a member".

    **Dev mode is `owner`, and that is not a loophole — it is the truth.** When
    no OIDC is configured there are no identities: every connection is the same
    anonymous nobody, `/v1/health` says `dev-no-auth`, and the door is already
    open by construction. Resolving roles against an identity that does not
    exist would be theatre — a lock drawn on a door with no wall. So a laptop
    run gets the role that lets it do everything, exactly as before this module
    existed, and authorisation begins the moment authentication does.

    Also the place the **owner bootstrap** happens, and it happens once: a room
    whose study names nobody has no one who can grant access to it, so the first
    signed-in person through the door becomes the owner. It is written **in the
    study's header** (that is where an owner belongs — it travels with the file)
    *and* in the ACL (that is what this server reads at the next join), because
    a bootstrap recorded in only one of the two is a bootstrap that disappears
    the first time the other is restored.
    """
    if dev_mode:
        return Role.OWNER
    acl = load_acl(room.room_id)
    if acl.owner is None:
        declared = access.owner_from_document(room.document)
        if declared:
            acl.owner = declared
            save_acl(room.room_id, acl)
        elif author:
            # nobody owns this room yet: the first authenticated arrival does
            if access.claim_owner(room.document, author):
                acl.owner = access.owner_from_document(room.document)
                save_acl(room.room_id, acl)
    # …and the group grants are expanded here: `role_of` takes the maximum of
    # the individual grant and whatever the person's groups hold.
    return access.role_of(acl, author, room.visibility, embargo=room.embargo,
                          groups_of=groups().expander())


async def _deny(websocket: WebSocket, member, verb: str, reason: str) -> None:
    """Say no, out loud. A dropped message is indistinguishable from a network
    fault, and the person on the other end deserves the difference."""
    await _send(websocket, envelope("denied", {
        "verb": verb, "reason": reason,
        "role": getattr(member.role, "value", None),
        "can_write": bool(member.role and member.role.can_write),
    }, source="em-server"))


async def _authenticate(websocket: WebSocket, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Verify the handshake, or close the socket with a reason.

    The token may arrive in `Authorization` (a native client can set headers) or
    as `?token=` (a browser cannot). Both are accepted deliberately: refusing the
    query parameter would mean no browser could ever connect, and inventing a
    second auth mechanism for browsers would be worse than a URL a TLS
    connection already protects.
    """
    if not authenticator.settings.enforcing:
        # dev / no-auth: the same door the HTTP routes leave open on a laptop,
        # and `/v1/health` says so out loud rather than letting anyone assume.
        return {"sub": "anonymous", "em_dev_mode": True}
    header = websocket.headers.get("authorization") or ""
    scheme, _, from_header = header.partition(" ")
    raw = from_header.strip() if scheme.lower() == "bearer" else (token or "")
    if not raw:
        await websocket.close(code=4401, reason="missing bearer token")
        return None
    try:
        return authenticator.verify(raw)
    except Exception as exc:  # HTTPException from the verifier, or anything else
        detail = getattr(exc, "detail", None) or str(exc)
        await websocket.close(code=4401, reason=f"token refused: {detail}"[:120])
        return None


@ws_router.websocket("/rooms/{room_id}/ws")
async def room_socket(websocket: WebSocket, room_id: str,
                      token: Optional[str] = Query(default=None),
                      since: Optional[str] = Query(default=None)) -> None:
    """Join a room, receive its snapshot, then live on the op stream."""
    await websocket.accept()
    claims = await _authenticate(websocket, token)
    if claims is None:
        return
    author = _identity(claims)

    room = await ROOMS.get(room_id)

    # ── the door ────────────────────────────────────────────────────────────
    # Authentication said WHO; this says WHETHER, and they are different
    # questions. A valid token used to be enough to enter any room and write to
    # it — the gap this closes.
    role = authorize(room, author, dev_mode=bool(claims.get("em_dev_mode")))
    if role is None:
        code = access.refusal_code(author)
        await websocket.close(
            code=code,
            reason=("not a member of this room" if code == 4403
                    else "this room needs a signed-in member"))
        return

    connection_id = uuid.uuid4().hex[:12]
    member = room.join(connection_id, websocket, author,
                       display=str(claims.get("name") or author or "anon"),
                       role=role)

    # ── the join: who you are, what the room is, what you missed ─────────────
    await _send(websocket, envelope("host_info", {
                            "tool": HOST_TOOL, "file": room_id,
                            "room": room_id, "connection_id": connection_id,
                            "author": author,
                            # WHAT YOU MAY DO, said at the door. A client that
                            # has to discover it by being refused shows an
                            # editing UI that does not work, which reads as a
                            # broken app rather than as a room you may only read.
                            "role": role.value,
                            "can_write": role.can_write,
                            # P4.3 · the compaction point this room has passed.
                            # A client whose own base is OLDER than this cannot
                            # safely replay its history — what it would re-assert
                            # has already been settled and forgotten here — so it
                            # is told the number and decides to re-sync instead.
                            # Announcing it is the difference between a gap that
                            # is handled and one that is discovered.
                            "gc_watermark": room.compacted_upto,
                            # COSA FARÀ IL SERVER CON QUESTA STANZA, detto alla
                            # porta. Un client che sa di essere coperto non ha
                            # bisogno di inventarsi un `request_save` a
                            # intervalli, e uno che vuole occuparsene da sé sa
                            # cosa deve dichiarare per farlo (`client_info`).
                            "keeping": _keeping_info(room),
                            "accepts_commands": False}, source="em-server"))
    await _send(websocket, envelope("snapshot", {
                            "doc": room.document,
                            "gc_watermark": room.compacted_upto,
                            "host": {"tool": HOST_TOOL, "file": room_id}},
                            source="em-server"))
    # presence closes the JOIN — three frames, always the same three, so a client
    # knows when it has arrived without counting
    await _broadcast_presence(room)
    # …and only then the replay: what a late arrival missed comes as the stream
    # it would have received had it been here, not as part of the handshake
    for op in room.replay_since(since):
        # wrapped like any other op frame: what a client missed must arrive in
        # the SAME shape it would have had live, or a replay needs its own reader
        await _send(websocket, envelope("op", op, source="em-server"))
    member.watermark = room.last_op_at or now_iso()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            try:
                await _handle(room, member, websocket, message, author)
            except WireError as exc:
                # A speaker from another protocol version is TOLD, not
                # half-understood. There are no external clients to migrate, but
                # the day an old build connects it gets a sentence instead of an
                # edge with no ends.
                await _send(websocket, envelope(
                    "error", {"detail": str(exc), "wire": WIRE},
                    source="em-server"))
            except Exception as exc:      # noqa: BLE001
                # A relay that dies on one bad message takes the room's other
                # clients down with it. The connection survives and the sender is
                # told — a silent drop would look exactly like a network problem.
                await _send(websocket, envelope(
                    "error", {"detail": f"{type(exc).__name__}: {exc}"},
                    source="em-server"))
    except WebSocketDisconnect:
        pass
    finally:
        could_write = bool(member.role and member.role.can_write)
        room.leave(connection_id)
        # presence is ephemeral: leaving removes it, and nothing is written down
        await _broadcast_presence(room)
        # …MA IL DOCUMENTO NON È EFFIMERO. Se se n'è andato l'ultimo che sapeva
        # scrivere, questo è l'istante in cui nessuno chiederà più niente — ed è
        # esattamente la forma in cui è sparita la scheda del 25 settembre: il
        # client che avrebbe dovuto chiedere `request_save` se n'era già andato.
        if could_write and not any(
                m.role is not None and getattr(m.role, "can_write", False)
                for m in room.members.values()):
            await KEEPER.emptied(room)
        elif not room.members:
            KEEPER.forget(room_id)


async def _handle(room, member, websocket: WebSocket, message: Dict[str, Any],
                  author: Optional[str]) -> None:
    kind, payload = read(message)          # …and a wrong version raises WireError

    # ── the write gate ───────────────────────────────────────────────────────
    # A viewer reads. Everything that CHANGES something — an operation, or the
    # request that writes a snapshot — needs editor or better, and a refusal is
    # a frame with a reason rather than a silence: a client that saw its edits
    # vanish without a word would report a lost connection, and the room would
    # get blamed for a rule it applied correctly.
    if kind in _WRITING_VERBS and not (member.role and member.role.can_write):
        await _deny(websocket, member, kind,
                    "this room is read-only for your role")
        return

    if kind == "op":
        # THE AUTHOR IS THE TOKEN'S, always. A client that names somebody else is
        # not lying to the relay, it is lying to everyone downstream — the stamp
        # is what the merge trusts (P4.1b), so it cannot be self-declared.
        #
        # The payload is otherwise **opaque**: copied, stamped, dated, forwarded.
        # That is the whole cure — there is no longer any envelope word to strip
        # out of it, so an edge's `source`/`target` cannot be mistaken for the
        # wire's "who sent this" (WIRE 2; the per-verb exception this replaces
        # was a symptom fix).
        op = dict(payload)
        # The client's own `author`, if it wrote one, is DROPPED before anything
        # else — not merely overwritten. In dev mode there is no token identity,
        # and "overwrite when we have one" quietly let a self-declared author
        # through exactly there. An author nobody verified is not an author.
        op.pop("author", None)
        if author:
            op["author"] = author
        op.setdefault("ts", now_iso())
        graph_id = message.get("graph_id")
        async with room.lock:
            result = room.apply(op, graph_id)
            if not result.get("applied"):
                # stale / idempotent / refused: it is NOT news, and re-broadcasting
                # it would hand the other clients a regression to re-apply
                await _send(websocket, envelope(
                    "op_result",
                    {"applied": False, "reason": result.get("reason", ""), "op": op},
                    source="em-server"))
                return
            room.record(op)
            outbound = envelope("op", op, source="em-server", graph_id=graph_id)
        await _fanout(room, outbound, skip=member.connection_id)
        await _send(websocket, envelope(
            "op_result",
            {"applied": True, "reason": result.get("reason", ""), "op": op},
            source="em-server"))
        # LA RETE, DOPO AVER RISPOSTO. Ultima riga e fuori dal lock: chi ha
        # scritto ha già il suo `op_result` e gli altri hanno già l'operazione,
        # quindi un salvataggio che dura 26 ms sulla stanza più grande non è 26
        # ms di attesa per nessuno. E se scatta, `_keep` riprende il lock da
        # solo.
        await KEEPER.kept(room)
        return

    if kind == "select":
        # awareness, soft and never a lock (design P4 §6)
        ids = payload.get("node_ids") or ([payload["node_id"]]
                                          if payload.get("node_id") else [])
        member.selection = [str(i) for i in ids]
        await _fanout(room, envelope("select", {
            "connection_id": member.connection_id,
            "author": author,
            "node_id": payload.get("node_id"),
            "node_ids": member.selection}, source="em-server"),
                      skip=member.connection_id)
        # NO presence broadcast here: the `select` frame IS the awareness
        # message, and sending the roster after every click would be noise the
        # clients have to filter. The selection travels with the next presence.
        return

    if kind == "request_snapshot":
        await _send(websocket, envelope("snapshot", {
            "doc": room.document,
            "gc_watermark": room.compacted_upto,
            "host": {"tool": HOST_TOOL, "file": room.room_id}},
            source="em-server"))
        return

    if kind == "request_save":
        # Il client chiede: per un relay, quello È lo snapshot. Passa da `_keep`
        # come tutto il resto, così `why` esiste sempre e un registro distingue
        # «l'ha chiesto qualcuno» da «è scattata la rete».
        #
        # E RESTA, adesso che c'è il differito, con il senso giusto: non è più
        # il rattoppo che sostituisce la rete, è l'INTENTO — una scheda è un
        # atto compiuto, e un atto compiuto si tiene subito invece che fra due
        # secondi.
        await _keep(room, keeping.BY_REQUEST, asked=True)
        return

    if kind == "client_info":
        # §2.3 · IL CLIENT DICHIARA, E CHI NON DICHIARA RICEVE LA RETE.
        #
        # Non c'è niente da rifiutare qui: l'assenza di questa frame è
        # l'impostazione sicura, quindi un client vecchio — che non sa nemmeno
        # che esista la domanda — non si rompe e non riceve un errore. Riceve
        # il differito, che è ciò che gli serviva senza saperlo.
        #
        # È una dichiarazione sulla DIVISIONE DEL LAVORO. Non dice chi è
        # seduto: quello si vede da una cosa sola, che la sessione è tenuta
        # aperta, e lo racconta la presenza.
        member.saves_itself = bool(payload.get("saves_itself"))
        await _send(websocket, envelope("host_info", {
            "tool": HOST_TOOL, "file": room.room_id, "room": room.room_id,
            "connection_id": member.connection_id,
            "keeping": _keeping_info(room)}, source="em-server"))
        if not member.saves_itself:
            # Un client che si è appena tolto la responsabilità può avere
            # lasciato del lavoro non tenuto: la rete riparte da adesso.
            await KEEPER.kept(room)
        return

    if kind == "ack":
        # "I have applied everything up to here" — the watermark that makes
        # compaction safe. A client that never acks simply holds the GC back,
        # which is the failure direction we want.
        member.watermark = str(payload.get("ts") or member.watermark or now_iso())
        return


# ── THE OTHER TRANSPORT · apply, KEEP, and ANNOUNCE, in one place ────────────
#
# `room.apply` had two callers and only one of them carried the rest of the
# contract. The socket, above, applies under `room.lock`, records the operation
# and fans it out — and persistence is the client's to ask for (`request_save`),
# which is right for a socket: a relay that wrote the room on every keystroke
# would rewrite a study while somebody is typing in it.
#
# `POST /v1/photogrammetry` was the second caller, from a `threading.Thread`, and
# it had NONE of the rest. Measured on 9 September 2026 with a store behind the
# room: a job reporting `done` with `ops_applied: N`, a snapshot store holding
# NOTHING for that room, and zero operations announced. The model, its genesis
# event and its placement lived in a dictionary that dies with the process, and
# anybody in EMStudio had to reopen the room — with no way to know they should.
#
# WHY THE FIX CANNOT BE A `snapshot()` CALL IN THE THREAD: `room.lock` is an
# `asyncio.Lock`, and an async lock does not exclude a thread. Saving from there
# would buy persistence at the price of making the race likelier AND more
# destructive — `em.compact` walks the same nested structures `em.apply_op` is
# mutating. So the thread produces a DELTA and this coroutine does the rest, on
# the event loop, where it already happens for the socket.
#
# AND IT IS ONE FUNCTION rather than a recipe repeated at the second door,
# because that is the property that survives a third: a connector written next
# month gets the lock, the save and the announcement by calling this, and
# `test_write_paths.py` refuses a caller of `room.apply` from anywhere else.
async def apply_from_connector(room, ops: List[Dict[str, Any]], *,
                               source: str,
                               graph_id: Optional[str] = None,
                               author: Optional[str] = None) -> Dict[str, Any]:
    """Apply a batch that came from something which is not a socket.

    Returns what landed, so the caller can put it on a job record. RAISES if the
    save fails — the caller is expected to report which half landed rather than a
    clean failure, and `photogrammetry.py` already does exactly that.

    THE ORDER IS THE CONTRACT: apply and keep under one lock (a snapshot taken
    between two operations of one delta would be a graph nobody wrote), announce
    afterwards, outside it — the same order the socket uses, for the same reason.
    """
    applied: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    async with room.lock:
        for op in ops:
            entry = dict(op)
            # THE AUTHOR IS THE CALLER'S, never the payload's — the same rule the
            # socket enforces by popping it: an author nobody verified is not an
            # author, and a connector's op is attributed to whoever asked for the
            # run.
            entry.pop("author", None)
            if author:
                entry["author"] = author
            entry.setdefault("ts", now_iso())
            outcome = room.apply(entry, graph_id)
            if outcome.get("applied"):
                room.record(entry)
                applied.append(entry)
            else:
                refused.append({"op": entry.get("op"), "id": entry.get("id"),
                                "reason": outcome.get("reason", "")})
        # KEPT, and inside the lock: this is the one place in the process that
        # writes a room, and it stays the one place.
        info = room.snapshot(SNAPSHOT_STORE) if applied else None

    # ANNOUNCED, outside the lock and to everybody: there is no origin to skip —
    # a job is not a member of the room (the roster is for people), so nobody in
    # it has already seen these.
    for op in applied:
        await _fanout(room, envelope("op", op, source=source, graph_id=graph_id))
    if info is not None:
        await _fanout(room, envelope("snapshot_written", info, source=source))
    return {"applied": len(applied), "refused": refused, "kept": info}


async def _send(websocket: WebSocket, payload: Dict[str, Any]) -> None:
    try:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:      # a socket that died mid-write is a disconnect
        pass


async def _fanout(room, message: Dict[str, Any], *, skip: Optional[str] = None) -> None:
    """Send to everybody but the origin — the echo suppression the bridge already
    does, for the same reason: a client must not have to recognise its own work
    coming back."""
    body = message.get("payload") or {}
    for connection_id, socket in list(room.sockets.items()):
        if connection_id == skip:
            continue
        await _send(socket, message)
        member = room.members.get(connection_id)
        if member is not None and message.get("type") == "op":
            # the timestamp is a field OF THE OP, and the op lives in the payload
            member.watermark = str(body.get("ts") or member.watermark or "")


async def _broadcast_presence(room) -> None:
    message = envelope("presence", {"room": room.room_id,
                                    "members": room.presence()},
                       source="em-server")
    for socket in list(room.sockets.values()):
        await _send(socket, message)
