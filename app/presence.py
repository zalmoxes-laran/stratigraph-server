"""Chi c'è, chi tace, e chi non c'è più.

════════════════════════════════════════════════════════════════════════════════
## IL FATTO, MISURATO PRIMA DI SCRIVERE UNA RIGA

`ws.py` non ha né `ping` né `pong`, ed è vero. Ma il livello sotto ce li ha, e
la prima cosa da fare era **misurare se erano attivi** invece di supporlo.

Contro un uvicorn vero (non il `TestClient`, che non ha né TCP né keepalive e
misurarlo lì sarebbe misurare niente), il 30 settembre 2026:

    A · client vivo che risponde ai PONG
        PING a t=20.0s, 40.0s, 60.0s — periodo 20,0 s esatti, ancora aperto
    B · client che riceve i PING e NON risponde
        PING a t=20.0s → CLOSE a t=40.0s, «keepalive ping timeout»
    C · LA GALLERIA (un proxy che scarta i pacchetti senza chiudere niente)
        il server chiude il suo capo a t=40.0s; il client non lo sa mai
        e `/who` smette di dirlo seduto a **t=40.2s**

Quindi: **il trasporto un socket morto lo toglie, e ci mette quaranta secondi.**
La premessa «la rete sceglie al posto nostro, dodici minuti» non regge su questa
configurazione — e questo modulo non esiste per riparare quello.

## ESISTE PER L'ALTRA META', CHE IL TRASPORTO NON PUÒ VEDERE

La prova A è il punto. Quel client sono quaranta righe di socket grezzo che non
fanno **nient'altro** che rispondere ai PONG: nessun EMStudio, nessuna
applicazione, nessuna persona. Per il relay è indistinguibile da Elisa che sta
lavorando.

> **Un PONG di protocollo dimostra uno stack TCP, non un'applicazione viva e
> tantomeno qualcuno seduto davanti.** Lo manda il browser, o il sistema
> operativo, e lo manda anche per una scheda congelata che non disegna più
> niente da dieci minuti.

Il trasporto misura il **socket**. Questo modulo misura il **client**. Sono due
domande diverse e la prima non risponde alla seconda.

════════════════════════════════════════════════════════════════════════════════
## IL BATTITO: IL NODO DICHIARA

Il progetto ha una grammatica — `client_info` dichiara, `ack` dichiara, `select`
dichiara — e regge anche qui: il battito è **una dichiarazione del client**
(`still_here`), non una domanda del server.

Non è solo coerenza. Una domanda del server misurerebbe di nuovo il socket: chi
risponde a una domanda automatica è di nuovo il livello che risponde ai PONG.
Una dichiarazione la manda **il codice dell'applicazione**, e se
l'applicazione è ferma non parte. È l'unica delle due che misura la cosa giusta.

E costa zero a chi lavora: **qualunque frame in arrivo è un segno di vita**
(un'operazione, un `select`, un `ack`). Il battito serve solo a chi è presente e
non sta scrivendo — che è precisamente il caso che il silenzio non deve
confondere con l'assenza.

## I NUMERI, E IL CONTO CHE LI SCEGLIE

* **`BEAT_SECONDS = 10`.** Non è un numero estetico: dev'essere abbastanza
  fitto che la soglia del silenzio ci stia **dentro i quaranta secondi del
  trasporto**, altrimenti un cavo staccato passerebbe da «dentro» a «uscito»
  senza mai attraversare «silenzioso» — e i tre stati sarebbero due.

* **`QUIET_AFTER = 30` s, cioè tre battiti mancati.** Uno può essere una pausa
  del garbage collector o un disegno lento; tre è un fatto. E trenta sta sotto
  i quaranta del trasporto, quindi una rete staccata si legge per intero:
  dentro → silenzioso a 30 s → uscito a 40 s.

* **Il costo su dieci presenti.** Il frame è l'inviluppo minimo, ~90 byte con
  l'incorniciatura WebSocket. Sei al minuto per presente, dieci presenti:
  **60 frame/min ≈ 5,4 KB/min in ingresso**, e **zero in uscita** — perché la
  presenza si diffonde **solo quando uno stato cambia**, non a ogni battito.
  Quella è la trappola vera: un roster è ~1 KB e rimandarlo a ogni battito
  costerebbe 60 × 10 × 1 KB = **600 KB/min**, cento volte il battito stesso,
  per non dire niente di nuovo.

════════════════════════════════════════════════════════════════════════════════
## COSA QUESTO FILE NON FA

**Non scrive niente su disco.** Il recinto 3 dice che la presenza è effimera, e
resta vero: gli stati si *derivano* da quando si è visto l'ultimo frame, e la
lista di chi è uscito vive in memoria e muore col processo.

**Non decide quando salvare** — quello è `keeping.py`, che però adesso chiede a
questo modulo *chi conta come scrittore presente*, perché uno scrittore
silenzioso non è qualcuno da cui aspettarsi un `request_save`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional

log = logging.getLogger("stratigraph.presence")


def _number(name: str, fallback: float) -> float:
    """Una soglia dall'ambiente, o quella scelta qui. Un valore illeggibile NON
    spegne il battito: torna il default e lo dice — la stessa regola di
    `keeping.py`, per la stessa ragione (una variabile scritta male non deve
    avere lo stesso effetto di una politica disattivata)."""
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except (TypeError, ValueError):
        log.warning("%s=%r is not a number: keeping the chosen default %s",
                    name, raw, fallback)
        return fallback


#: Ogni quanto un client che non ha altro da dire dichiara di esserci.
BEAT_SECONDS = _number("EM_PRESENCE_BEAT_SECONDS", 10.0)

#: Dopo quanto silenzio la stanza smette di dire «c'è» e comincia a dire «tace
#: da». Tre battiti mancati, e sotto i 40 s del trasporto — vedi il conto in
#: cima: se fosse sopra, i tre stati sarebbero due.
QUIET_AFTER = _number("EM_PRESENCE_QUIET_AFTER_SECONDS", 30.0)

#: I due stati che una persona può avere *nel roster*. Il terzo — «uscito» — non
#: è uno stato di chi è dentro: è l'uscita dal roster, e si racconta a parte
#: (`Room.departed`), perché scriverlo qui vorrebbe dire tenere per sempre una
#: riga per ogni socket che è passato di qui.
IN = "in"
QUIET = "quiet"

#: Quante uscite recenti la stanza si ricorda. Effimere, in memoria, e limitate:
#: senza un tetto una stanza con un client che si riconnette in continuazione
#: crescerebbe senza fine — che è il modo in cui una lista «effimera» diventa
#: uno stato durevole per sbaglio.
DEPARTED_KEPT = 32


def monotonic() -> float:
    """L'orologio delle DURATE, e non è quello dei timbri.

    `now_iso()` è l'ora del muro: torna indietro quando NTP corregge, e un
    «silenzioso da -4 minuti» è un difetto che si vede solo in produzione. Una
    durata si misura con un orologio che non torna mai indietro; l'istante che
    viaggia sul filo resta ISO, perché quello lo legge una persona.
    """
    return time.monotonic()


def silent_for(member: Any, *, now: Optional[float] = None) -> float:
    """Da quanti secondi non si sente questo membro."""
    seen = getattr(member, "last_seen_mono", None)
    if seen is None:
        return 0.0
    return max(0.0, (monotonic() if now is None else now) - seen)


def state_of(member: Any, *, now: Optional[float] = None) -> str:
    """`in` o `quiet`, derivato — non memorizzato.

    Derivato e non memorizzato di proposito: uno stato scritto da qualche parte
    avrebbe bisogno di qualcuno che lo aggiorni, e quel qualcuno sarebbe un
    compito in più che può restare indietro. Così la risposta è vera
    nell'istante in cui la si chiede, sempre.
    """
    return QUIET if silent_for(member, now=now) >= QUIET_AFTER else IN


def is_quiet(member: Any, *, now: Optional[float] = None) -> bool:
    return state_of(member, now=now) == QUIET


def hhmm(seconds: float) -> str:
    """`HH:MM` di silenzio, per chi legge. Sotto il minuto è `00:00`, che si
    legge «adesso» e non «da un tempo che non so dire»."""
    total = int(max(0.0, seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"


class Watcher:
    """Il temporizzatore che si accorge di un silenzio quando NESSUNO parla.

    ════════════════════════════════════════════════════════════════════════════
    ## PERCHÉ SERVE, VISTO CHE LO STATO È DERIVATO

    Uno stato derivato è vero quando lo si chiede — e `/who` lo chiede, quindi
    la lista non mente mai a chi guarda. Ma la stanza deve anche **dirlo** a chi
    è dentro, e un frame di presenza parte solo se qualcosa lo fa partire.

    Finché in stanza c'è qualcuno che parla, basta lui: ogni frame in arrivo è
    l'occasione per ri-derivare tutti gli stati e diffondere se qualcosa è
    cambiato. Il caso che quel meccanismo **non copre** è preciso e non è
    teorico: *l'ultimo che sa scrivere si congela mentre è solo in stanza.*
    Nessuno manda più niente, quindi nessuno ri-deriva, e — questa è la parte
    che fa danno — la sua scheda non salvata resta lì. Il trasporto non lo
    toglierà: una scheda del browser congelata **risponde ai PONG lo stesso**.

    Quindi un temporizzatore, con la forma che questa casa usa già
    (`keeping.Keeper`): uno per stanza, al massimo uno, riarmato a ogni frame.
    Non è un ciclo di fondo che gira sempre — quando la stanza è vuota non c'è
    niente di armato.
    """

    def __init__(self, on_sweep: Callable[[Any], Awaitable[None]], *,
                 quiet_after: Optional[float] = None) -> None:
        #: `async on_sweep(room)` — chi sa ri-derivare e diffondere. Iniettato e
        #: non importato: questo modulo non deve poter toccare un socket.
        self._sweep = on_sweep
        self.quiet_after = QUIET_AFTER if quiet_after is None else quiet_after
        self._timers: Dict[str, Any] = {}

    def heard(self, room: Any) -> None:
        """Qualcuno ha parlato: da adesso questa stanza va guardata.

        **Non riarma se è già armato**, ed è la riga che rende il meccanismo
        vero invece che apparente. Riarmare a ogni frame sembrava naturale — è
        quello che fa `keeping.Keeper`, dove la quiete È della stanza — e qui
        sarebbe stato il difetto: finché Carlo scava e manda un'operazione ogni
        cinque secondi, ogni sua operazione avrebbe spostato più in là il
        momento di accorgersi che **Elisa** tace. Cioè il colpo di scopa non
        sarebbe mai arrivato proprio nella stanza dove serviva.

        Il silenzio è di UNA PERSONA, la scopa è DELLA STANZA: sono due cose, e
        il temporizzatore deve seguire la seconda.
        """
        if not self._timers.get(room.room_id):
            self._arm(room)

    def forget(self, room_id: str) -> None:
        self._disarm(room_id)

    # ── il temporizzatore ───────────────────────────────────────────────────

    def _arm(self, room: Any) -> None:
        import asyncio
        self._disarm(room.room_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Nessun ciclo: siamo in un test sincrono o in un thread. Non è un
            # errore ed è giusto che non lo sia — lo stato resta derivabile a
            # richiesta, che è la parte che non dipende da questo compito.
            return
        self._timers[room.room_id] = loop.create_task(self._after(room))

    def _disarm(self, room_id: str) -> None:
        task = self._timers.pop(room_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _after(self, room: Any) -> None:
        import asyncio
        try:
            await asyncio.sleep(self.quiet_after)
        except asyncio.CancelledError:
            return
        self._timers.pop(room.room_id, None)
        try:
            await self._sweep(room)
        except Exception as exc:      # noqa: BLE001
            # Un colpo di scopa che fallisce non deve uccidere il compito in
            # silenzio: la lista resta comunque derivabile a richiesta, e il
            # difetto si legge invece di sparire.
            log.warning("presence: the sweep of %s failed: %s",
                        room.room_id, exc)
        # …E SI RIARMA FINCHÉ C'È QUALCUNO. Un solo colpo non basta: chi si è
        # congelato resta congelato, e il giro dopo può essersi congelato un
        # altro. Quando la stanza si svuota non resta niente di armato, che è
        # la differenza fra un temporizzatore e un ciclo di fondo.
        if getattr(room, "members", None):
            self._arm(room)
