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
import logging
from typing import Any, Dict, List, Optional, Set

from s3dgraphy import api as em

from . import presence
from .oplog import journal_for
from .store import RoomStore, SnapshotStore, deep_copy, room_store_from_env

#: How many recent operations a room keeps so a late arrival can catch up
#: without a fresh snapshot. Bounded on purpose: an unbounded log is a memory
#: leak with a good excuse.
log = logging.getLogger("stratigraph.rooms")

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
    #: §2.3 · IL CLIENT DICHIARA se dei salvataggi si occupa lui.
    #:
    #: `False` è l'assenza di dichiarazione, ed è l'impostazione SICURA: un
    #: client vecchio — che non sa nemmeno che esista la domanda — riceve la
    #: rete d'ufficio invece di un errore. EMStudio chiede `request_save` e
    #: oggi non dichiara niente: prende la rete in più, che è la direzione
    #: giusta in cui sbagliare.
    #:
    #: **È UNA DICHIARAZIONE SULLA DIVISIONE DEL LAVORO, NON UNA POSTURA.**
    #: Dice chi scrive il file, non chi è seduto in questa stanza. Sedersi si
    #: vede da una cosa sola — che la sessione è tenuta aperta — e la presenza
    #: la racconta `as_presence`, non questo campo.
    saves_itself: bool = False
    #: SE QUESTA CONNESSIONE È IN MODO SVILUPPO. Serve a rivalutare il ruolo a
    #: ogni scrittura senza rifare il giro dell'autenticazione: in dev non ci
    #: sono identità, quindi non c'è niente da rivalutare e la risposta è
    #: `owner` come alla porta.
    dev_mode: bool = False
    #: L'ULTIMO ISTANTE IN CUI SI È SENTITO QUESTO CLIENT, in due orologi.
    #:
    #: `last_seen` è ISO perché viaggia e lo legge una persona; `last_seen_mono`
    #: è monotòno perché le DURATE si misurano con un orologio che non torna
    #: indietro. Con l'ora del muro, una correzione NTP produce «silenzioso da
    #: -4 minuti», che è un difetto che si vede solo in produzione.
    #:
    #: **Qualunque frame in arrivo li muove**, non solo il battito: chi lavora
    #: sta già dicendo di esserci a ogni operazione, e farglielo dire due volte
    #: sarebbe traffico per un fatto già noto.
    last_seen: str = field(default_factory=now_iso)
    last_seen_mono: float = field(default_factory=presence.monotonic)

    def heard(self) -> None:
        """Un segno di vita. Una riga sola, chiamata da un posto solo."""
        self.last_seen = now_iso()
        self.last_seen_mono = presence.monotonic()

    def as_presence(self) -> Dict[str, Any]:
        # The role travels with presence too: "who is here" and "who may write"
        # is one question for the person reading the roster, and a client that
        # had to ask separately would draw a room where everybody looks alike.
        #
        # E DA STANOTTE ANCHE LO STATO. `state` è `in` o `quiet`, DERIVATO qui e
        # non memorizzato da nessuna parte: uno stato scritto avrebbe bisogno di
        # qualcuno che lo aggiorni, e quel qualcuno può restare indietro. Così è
        # vero nell'istante in cui lo si chiede.
        state = presence.state_of(self)
        silent = presence.silent_for(self)
        entry = {"id": self.connection_id, "author": self.author,
                 "display": self.display, "selection": list(self.selection),
                 "role": getattr(self.role, "value", self.role),
                 "joined_at": self.joined_at,
                 "state": state,
                 "last_seen": self.last_seen}
        if state == presence.QUIET:
            # DA QUANDO, non «da quando me ne sono accorto». Chi scava accanto a
            # Elisa decide se aspettarla, e la differenza fra «tace da tre
            # minuti» e «tace da dieci secondi» è tutta la decisione.
            entry["quiet_since"] = self.last_seen
            entry["silent_for_seconds"] = int(silent)
            entry["silent_for"] = presence.hhmm(silent)
        return entry


class Room:
    """One study, live.

    The working copy is a container document (`{"graphs": {...}}`) — the same
    shape the file on disk has, because the relay must never invent a second
    representation of a project.
    """

    def __init__(self, room_id: str, document: Dict[str, Any],
                 journal: Optional[Any] = None):
        self.room_id = room_id
        self.document = document
        #: LA FINESTRA IN MEMORIA, veloce, 512 operazioni. Serve il caso normale
        #: — un client che si riaffaccia dopo qualche minuto — e muore col
        #: processo, come è sempre stato.
        self.oplog: List[Dict[str, Any]] = []
        #: LA MEMORIA PROFONDA (`app/oplog.py`), che invece dura. Si legge solo
        #: quando la finestra non arriva abbastanza indietro, cioè quasi mai.
        #: `None` quando lo store è in memoria: là non c'è niente che
        #: sopravviva al processo, e un registro che finge di durare sarebbe
        #: peggio di nessun registro.
        self.journal = journal
        self.members: Dict[str, Member] = {}
        self.sockets: Dict[str, Any] = {}
        #: CHI È USCITO, di recente. In memoria, limitata (`DEPARTED_KEPT`), e
        #: mai scritta in nessuno store — vedi `_remember_departure` per perché
        #: questo non viola il recinto 3.
        self.departed: Dict[str, Dict[str, Any]] = {}
        #: L'IMPRONTA DELL'ULTIMO ROSTER DIFFUSO. Serve a `ws.py` per diffondere
        #: la presenza **solo quando cambia**: senza, un battito ogni dieci
        #: secondi per dieci presenti costerebbe cento volte se stesso in roster
        #: rimandati per non dire niente di nuovo.
        self.roster_shape: Optional[tuple] = None
        self.lock = asyncio.Lock()
        self.snapshot_at: Optional[str] = None
        self.last_op_at: Optional[str] = None
        #: QUANTE OPERAZIONI SONO STATE APPLICATE DOPO L'ULTIMO SALVATAGGIO.
        #:
        #: Non «quando ho salvato»: quella è una data, questa è un rischio. Una
        #: stanza che ha applicato centoquaranta operazioni e non è mai stata
        #: salvata aveva esattamente lo stesso aspetto di una salvata a ogni
        #: giro, e il 25 settembre una scheda di 26 campi è sparita così.
        self.unsaved: int = 0
        #: P4.3 · how far this room has been COMPACTED. Announced to every client
        #: (`host_info`, `snapshot`) because it is the one number a client needs
        #: to know whether its own history is still reconcilable here: below this
        #: point the room no longer holds what a replay would argue with.
        #: RIPRESO DAL REGISTRO, se ce n'è uno. Vive in memoria come prima, ma
        #: adesso riparte da dove era: dopo un riavvio il documento sul disco è
        #: già compattato, e un `None` qui lascerebbe rigiocare operazioni che
        #: la compattazione ha reso pericolose. Vedi `Journal.mark_compacted`.
        self.compacted_upto: Optional[str] = (journal.compacted_upto()
                                              if journal is not None else None)
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
             display: str = "", role: Any = None,
             dev_mode: bool = False) -> Member:
        member = Member(connection_id=connection_id, author=author,
                        display=display or (author or "anon"), role=role,
                        dev_mode=dev_mode)
        self.members[connection_id] = member
        self.sockets[connection_id] = socket
        # È TORNATO: la riga «uscito» non deve restare accanto a lui presente,
        # o la stanza direbbe le due cose insieme.
        self.departed.pop(self._departure_key(member), None)
        return member

    def leave(self, connection_id: str) -> None:
        member = self.members.pop(connection_id, None)
        self.sockets.pop(connection_id, None)
        if member is not None:
            self._remember_departure(member)

    def presence(self) -> List[Dict[str, Any]]:
        return [m.as_presence() for m in self.members.values()]

    # ── il terzo stato ───────────────────────────────────────────────────────
    #
    # «C'è» e «non c'è mai stata» sono due bugie diverse, e finché `leave()`
    # cancellava e basta la stanza sapeva dire solo la seconda. Chi era seduto e
    # se n'è andato deve leggersi come **uscito alle 14:32**, non come qualcuno
    # che non è mai passato di qui.
    #
    # E IL RECINTO 3 REGGE LO STESSO. «La presenza è effimera: uscire la toglie e
    # non si scrive niente» parla dello STORE — che uno sguardo non è un fatto
    # sullo studio, e non deve finire nell'em.json. Queste righe vivono in
    # memoria, muoiono col processo e non arrivano a nessuno store: sono
    # esattamente effimere quanto il roster che stanno spiegando.

    def _departure_key(self, member: Member) -> str:
        """Per ORCID quando c'è, per connessione quando no.

        Per persona e non per socket: un client che si riconnette tre volte in
        un minuto è **una** persona che ha avuto problemi di rete, e tre righe
        direbbero di tre uscite che non ci sono state."""
        return member.author or f"conn:{member.connection_id}"

    def _remember_departure(self, member: Member) -> None:
        was_quiet = presence.is_quiet(member)
        self.departed[self._departure_key(member)] = {
            "author": member.author,
            "display": member.display,
            "role": getattr(member.role, "value", member.role),
            "left_at": now_iso(),
            # SE SE N'È ANDATO PARLANDO O TACENDO. Sono due uscite diverse: la
            # prima è una persona che ha chiuso, la seconda è una rete che ha
            # ceduto — e chi legge il roster agisce diversamente sulle due.
            "was_quiet": was_quiet,
            "last_seen": member.last_seen,
        }
        while len(self.departed) > presence.DEPARTED_KEPT:
            self.departed.pop(next(iter(self.departed)))

    def departures(self) -> List[Dict[str, Any]]:
        """Chi è uscito, il più recente per primo."""
        return sorted(self.departed.values(),
                      key=lambda e: e.get("left_at") or "", reverse=True)

    # ── the op-log ───────────────────────────────────────────────────────────

    def record(self, op: Dict[str, Any]) -> None:
        # UN SOLO POSTO CONTA, ed è questo perché è l'unico che tutte e due le
        # vie di scrittura attraversano: il socket (`ws.py`) e la porta dei
        # connettori. Contare nei due chiamanti sarebbe stato due contatori che
        # si allontanano il giorno che ne arriva un terzo.
        self.unsaved += 1
        self.oplog.append(op)
        if len(self.oplog) > OPLOG_LIMIT:
            del self.oplog[: len(self.oplog) - OPLOG_LIMIT]
        self.last_op_at = str(op.get("ts") or now_iso())
        # …E SUL DISCO, che è la sola metà che sopravvive a un riavvio.
        #
        # `at` è l'istante del SERVER e non il `ts` dell'operazione: il secondo
        # è l'orologio del client, onorato apposta perché una nota dettata alle
        # 10 e sincronizzata alle 18 porta le 10. Servono tutti e due, e per due
        # cose diverse — vedi `app/oplog.py`.
        #
        # Un registro che non scrive NON deve far fallire un'operazione che la
        # stanza ha già applicato: si annota e si continua, e chi legge
        # `/health` vede che quella stanza non sta tenendo la sua storia.
        if self.journal is not None:
            try:
                self.journal.append(op, at=now_iso())
            except Exception as exc:      # noqa: BLE001
                log.warning("the room %s could not journal an operation: %s",
                            self.room_id, exc)

    def replay_since(self, since: Optional[str]) -> List[Dict[str, Any]]:
        """The operations after `since` — what a late arrival missed.

        With no `since` the caller gets nothing: it is being handed the snapshot,
        which already contains everything, and replaying the log on top would
        only re-apply what is there (harmless, because the ops are idempotent,
        and pointless, which is the better reason not to).

        ── DUE MEMORIE, E SI SCEGLIE DA SOLA ───────────────────────────────

        La finestra in memoria copre il caso normale ed è immediata. Quando il
        cursore è più vecchio di quello che la finestra tiene — o quando la
        finestra è vuota perché il processo è appena ripartito — si legge il
        registro sul disco.

        Il confronto che decide è sul `ts` della PRIMA operazione in memoria:
        se il cursore è già dentro quella finestra, il disco non aggiungerebbe
        niente.
        """
        if not since:
            return []
        in_memoria = [op for op in self.oplog if str(op.get("ts") or "") > since]
        if self.journal is None:
            return in_memoria
        primo = str(self.oplog[0].get("ts") or "") if self.oplog else ""
        if primo and since >= primo:
            # il cursore cade dentro la finestra: il disco direbbe la stessa cosa
            return in_memoria
        return self.journal.since(since)

    def replay_reaches(self) -> Optional[str]:
        """Fin dove indietro questa stanza sa guardare. `None` = non si sa.

        È il `ts` più vecchio che il registro tiene ancora, e serve al rifiuto:
        un cursore più vecchio non può ricevere un replay COMPLETO, e riceverne
        uno parziale sarebbe peggio del niente — il client crederebbe di essere
        allineato.
        """
        if self.journal is not None:
            piu_vecchio = self.journal.oldest()
            if piu_vecchio:
                return piu_vecchio
        return str(self.oplog[0].get("ts")) if self.oplog else None

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

    def keeping(self) -> Dict[str, Any]:
        """Se questa stanza sta accumulando lavoro non tenuto, e quanto.

        Il numero che conta è `unsaved`, non `snapshot_at`: una data dice
        quand'è successa una cosa, un conteggio dice cosa c'è da perdere.

        `writers_present` distingue i due modi in cui una stanza può avere
        lavoro non salvato: qualcuno la sta scrivendo adesso (normale, e il
        differito la coprirà), oppure **non c'è più nessuno** e quel lavoro sta
        lì da solo — che è la forma in cui è sparita la scheda del 25 settembre.

        **E da stanotte un silenzioso non conta.** Vedi `writers_present` per
        la decisione e per il caso che la impone.
        """
        writers = self.writers_present()
        return {"unsaved_ops": self.unsaved,
                "snapshot_at": self.snapshot_at,
                "last_op_at": self.last_op_at,
                "writers_present": len(writers),
                # …e quanti ce n'erano prima di togliere i silenziosi. Il
                # confronto fra i due numeri È la risposta alla domanda «perché
                # questa stanza si sta salvando da sola se c'è ancora gente
                # dentro», e senza il secondo numero non si può dare.
                "writers_seated": sum(
                    1 for m in self.members.values()
                    if m.role is not None and getattr(m.role, "can_write", False)),
                # I client che si sono presi la responsabilità. Se sono TUTTI i
                # presenti che sanno scrivere, la rete si tira via; se ne arriva
                # uno che non dichiara, torna.
                "writers_saving_themselves": sum(1 for m in writers
                                                 if m.saves_itself),
                "at_risk": bool(self.unsaved) and not writers}

    def writers_present(self) -> List[Member]:
        """Chi sa scrivere **e si sente**.

        ════════════════════════════════════════════════════════════════════════
        LA DECISIONE, E IL CASO CHE LA IMPONE

        `KEEPER.emptied` scatta quando se ne va l'ultimo che sa scrivere, e
        `Keeper.covers` tira via la rete quando tutti i presenti che sanno
        scrivere dichiarano di salvarsi da soli. Tutte e due chiedono **chi c'è**,
        e finché la risposta comprendeva i silenziosi la stanza rispondeva di sì
        a una domanda che voleva dire un'altra cosa: non «quanti socket sono
        aperti», ma **da quanti mi posso aspettare un `request_save`**.

        Da un silenzioso non ci si aspetta niente, e il caso in cui la
        differenza fa danno è preciso: un client che ha dichiarato
        `saves_itself` e si congela. Il trasporto non lo toglierà — una scheda
        del browser congelata risponde ai PONG lo stesso, misurato — quindi
        `covers()` resterebbe falso per sempre, la rete resterebbe tirata via
        per sempre, e la sua scheda resterebbe non salvata **per sempre**. È la
        forma del 25 settembre, con un socket ancora aperto sopra.

        Sbagliare da questa parte costa un salvataggio in più (0,6–26 ms
        misurati); sbagliare dall'altra costa una scheda.
        """
        return [m for m in self.members.values()
                if m.role is not None and getattr(m.role, "can_write", False)
                and not presence.is_quiet(m)]

    def gc_watermark(self) -> Optional[str]:
        """The instant every connected member has been brought past.

        The safety argument for compaction, and the reason it is a MINIMUM: a
        member still catching up may yet be sent an operation older than the
        others have seen, and compacting past that point would let a late
        operation win against a fallback instead of against the real clock.

        With nobody connected there is nothing to protect — but nothing to
        promise either: an absent client can come back with an old op-log, which
        this cannot know about. That is the declared limit of GC at this stage.

        ════════════════════════════════════════════════════════════════════════
        E I SILENZIOSI NON CONTANO — ma la ragione non è quella che sembrava.

        Il difetto atteso era «un silenzioso resta nella lista e trattiene la
        compattazione finché tace». **Misurato prima di toccare niente, era
        falso**, e il perché è peggio: `_fanout` muoveva il watermark quando
        SPEDIVA e `_send` inghiottiva l'errore, quindi un socket rotto veniva
        **accreditato di consegne mai avvenute** — e chi taceva finiva col
        watermark più nuovo di tutti, mentre a trattenere il GC era l'autore
        che non manda `ack` (misura del 30 settembre: chi tace 10:00:02Z, chi
        parla 19:34:43Z).

        Riparato il credito falso in `ws.py::_fanout`, il difetto atteso diventa
        reale: un socket che non riceve più tiene il suo watermark fermo
        all'ultima consegna vera, ed è lì che la compattazione si pianta. Questa
        riga lo toglie.

        **PERCHÉ È SICURO.** L'argomento del minimo protegge chi *sta ancora
        arrivando*. Un silenzioso non sta arrivando: o è in galleria e non
        riceve niente, o è congelato e non applica niente. Compattare oltre il
        suo punto è sicuro perché la guardia del rientro esiste già — se torna
        con una base più vecchia di `compacted_upto`, `_replay_plan` gli nega la
        riproduzione (`unsafe`) e gli dà lo snapshot intero. La compattazione si
        appoggia alla guardia scritta ieri notte, e le due cose si compongono
        invece di annullarsi.
        """
        marks = [m.watermark for m in self.members.values()
                 if m.watermark and not presence.is_quiet(m)]
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
            # …e ricordato SUL DISCO, perché da stanotte il registro sopravvive
            # al riavvio e la guardia che lo protegge deve sopravvivere con lui.
            if self.journal is not None:
                try:
                    self.journal.mark_compacted(before, at=now_iso())
                except Exception as exc:      # noqa: BLE001
                    log.warning("the room %s could not journal its compaction "
                                "point: %s", self.room_id, exc)
        store.put(self.room_id, self.document)
        self.snapshot_at = now_iso()
        # …e il debito è pagato. Azzerato DOPO `store.put`: se la scrittura
        # solleva, il contatore deve continuare a dire la verità.
        self.unsaved = 0
        if gc and before:
            # the log up to the watermark is now inside the snapshot
            #
            # `gc and`, dal 2026-09-26, e non solo `before`: TAGLIARE SENZA
            # COMPATTARE È PERDERE PER NIENTE. Il registro è il buffer di
            # riproduzione per chi rientra; troncarlo fino a un punto a cui il
            # documento non si è ancora assestato butta la storia senza
            # incassare lo spazio. Le due cose sono un atto solo, e adesso lo
            # sono anche nel codice.
            #
            # Serve al salvataggio differito, che passa `gc=False`: quello è
            # DURATA, non compattazione (vedi `app/keeping.py`).
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


class RoomGraphTaken(Exception):
    """A room cannot come back onto a graph that found another home meanwhile.

    Raised by `RoomRegistry.archive(..., archived=False)`. It is a DOMAIN error
    and not an HTTP one on purpose — this module does not know FastAPI, and the
    one place that translates it into a 409 lives in `main.py`.

    WHY IT LIVES IN `archive` AND NOT IN THE ENDPOINTS: two doors call archive
    today (a room's own owner, and an operator on the node console) and a third
    is a plausible afternoon's work. A check written in both is a check the third
    one will not have; a check written HERE is one the third one inherits without
    knowing it exists.
    """

    def __init__(self, room_id: str, graph: str, taken_by: str) -> None:
        self.room_id = room_id
        self.graph = graph
        self.taken_by = taken_by
        super().__init__(f"{room_id!r} cannot return to {graph!r}: "
                         f"{taken_by!r} is live on it")


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

    def room_already_on(self, primary: str,
                        *, except_room: str = "") -> Optional[str]:
        """Which DECLARED room already has `primary` as its live graph, if any.

        **«Una stanza, un grafo vivo; un grafo vivo, una stanza»** — the design
        note of 5 September 2026 puts it among the rules that do not negotiate,
        and until now nothing enforced it. Measured on the dev stack, 6 September:

            POST /v1/rooms {room_id: amb-a}                        → 201
            POST /v1/rooms {room_id: amb-b, container_refs:[amb-a]} → 201

        …and the consequence, measured rather than reasoned about: room `amb-b`
        OPENED `amb-a`'s graph, both rooms accepted their own operations, both
        saved, and the store ended with two files — `amb-a.em.json` and
        `amb-b.em.json` — each declaring to hold the graph **`amb-a`**, with
        divergent contents and nothing saying which one is it. Not «two tables,
        one file» but a FORK THAT NEVER DECLARED ITSELF, which is the worse
        shape: a fork is legal when it says so, and the note says exactly that.

        THE PRIMARY REFERENCE is what this tests, because it is what becomes the
        room's live document (`primary_ref`). A second reference is a pointer the
        session does not edit, and forbidding those would forbid the multi-container
        record this class exists for.

        WHAT IT DOES NOT CATCH, declared rather than papered over: a container
        that no room DECLARED but that is openable under its own name (every
        pre-register room is exactly that — implicit, named after itself). A new
        room whose primary is such a container still makes a second table on it.
        Closing that means deciding whether `POST /v1/rooms {container_refs: [X]}`
        is ever legal for an existing X, which is the «fork» verb the note leaves
        to its own design note — so it is named here, not guessed at.
        """
        for room in self.declared():
            if room.room_id == except_room or room.archived_at:
                continue
            if room.primary_ref == primary:
                return room.room_id
        return None

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
        # ── AND THE QUESTION BELONGS TO THE RETURN, NOT ONLY TO THE DEPARTURE ──
        #
        # `room_already_on` is asked when a room is BORN and never when a room
        # COMES BACK — and coming back is the entire reason archiving is a mark
        # and not a deletion. Measured on the dev stack, five legal calls, no
        # forcing:
        #
        #     POST /v1/rooms {probe-c1}                        → 201
        #     POST /v1/rooms {probe-c3, refs:[probe-c1]}        → 409  ✓
        #     POST /v1/rooms/probe-c1/archive {archived:true}   → 200
        #     POST /v1/rooms {probe-c3, refs:[probe-c1]}        → 201  ← through
        #     POST /v1/rooms/probe-c1/archive {archived:false}  → 200
        #
        # …and two live rooms on one live graph, which is exactly the shape the
        # 409 exists to refuse. Reproduced again from the other end: an ARCHIVED
        # room pointed at a live room's graph came back with a 200, and the store
        # then had `['probe-c3', 'probe-c1']` both live on `probe-c1`.
        #
        # THE SKIP ON `archived_at` STAYS. Removing it would make the archive a
        # permanent lock on a graph, and the archive exists to let a season end —
        # «a workspace goes quiet for a season (a dig is seasonal)», says the
        # docstring above, which is the same sentence that explains why a
        # sleeping room's graph must not be reassigned under it. The exception and
        # its refutation were already in this class.
        if not archived and descriptor.archived_at:
            taken = self.room_already_on(descriptor.primary_ref,
                                         except_room=room_id)
            if taken:
                raise RoomGraphTaken(room_id, descriptor.primary_ref, taken)
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
            # IL REGISTRO ACCOMPAGNA IL DOCUMENTO: sta accanto agli snapshot, e
            # non c'è quando lo store è in memoria (vedi `oplog.journal_for`).
            room = Room(room_id, document, journal=journal_for(self.store, room_id))
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
