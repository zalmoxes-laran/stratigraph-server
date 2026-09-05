"""Quando salvare, quando la stanza non può contare su chi ci scrive.

════════════════════════════════════════════════════════════════════════════════
## IL FATTO PER CUI QUESTO FILE ESISTE

`ws.py` dichiarava, in una riga di commento: *«persistence is the client's to ask
for (`request_save`)»*. EMStudio lo chiede. Il `RoomWriter` di StratiField non lo
chiede mai. Il vocabolario del relay conosce `request_save` — sta in
`_WRITING_VERBS`, accanto a `op` — e **il relay non verifica che qualcuno lo dica
mai**.

Misurato il 25 settembre e rimisurato il 26 prima di toccare niente:

    snapshots/ → 217 file .room.json     (le stanze dichiarate)
               →  14 file .em.json       (i documenti)

Una scheda di ventisei campi, dettata in aereo, riaccodata, confermata nella
stanza con l'ORCID di chi l'aveva dettata, **non è mai esistita fuori dalla
memoria di un processo**. E la stanza rispondeva di sì a tutti: l'operazione
tornava `applied: True`, il fan-out la diffondeva, lo snapshot del join la
mostrava.

> **Una presunzione sul comportamento di chi sta dall'altra parte, mai
> verificata, non è un contratto: è una speranza.**

E l'altra faccia della stessa cosa: il client che avrebbe dovuto chiedere se
n'era già andato. Non ha dimenticato di chiedere — **non c'era più nessuno a
chiedere**, perché apriva una connessione per consegna e la chiudeva.

════════════════════════════════════════════════════════════════════════════════
## LA POLITICA, E I NUMERI CHE LA SCELGONO

Salvare a ogni operazione non si può. `Room.snapshot` compatta, scrive il file e
tronca il registro; misurato su questo nodo, con il documento vero:

    stanza                              nodi/archi     KB      snapshot→disco
    probe-pyarchinit-09-client-stanza      44/71        58        0.6 ms
    aiano                                 454/1212     464        5.7 ms
    sarmizegetusa                        1845/5650    2334       26.1 ms

Una raffica di 27 operazioni sul socket — la forma di una scheda ICCD compilata:
un nodo e ventisei campi — dura **4 ms in tutto**, con 0,1 ms di mediana fra
un'operazione e la successiva. Salvarne una per operazione su `sarmizegetusa`
costerebbe 27 × 26,1 = **705 ms** e riscriverebbe 63 MB per una scheda.

Quindi due soglie, e tutte e due hanno un numero dietro:

* **`SAVE_AFTER_OPS = 64`.** Una scheda è 27 operazioni misurate, quindi una
  scheda intera **non** fa scattare la soglia a conteggio: prende un salvataggio
  solo, quello della quiete. Sessantaquattro lascia margine per una scheda più
  le correzioni, e tiene il costo ammortizzato a 26,1/64 = **0,41 ms per
  operazione** sulla stanza più grande di questo nodo.

* **`SAVE_AFTER_QUIET = 2.0 s`.** Il divario misurato fra due operazioni di una
  raffica è 0,1 ms (massimo osservato 0,2): quattro ordini di grandezza sotto la
  soglia, quindi una raffica produce **un** salvataggio e non ventisette. E due
  secondi è il lavoro massimo che un client può perdere sparendo senza dire
  niente.

E un terzo momento, che non è una soglia ma un evento: **quando l'ultimo che sa
scrivere lascia la stanza.** È esattamente l'istante in cui nessuno chiederà più
niente, ed è la forma in cui è sparita la scheda del 25 settembre.

════════════════════════════════════════════════════════════════════════════════
## COSA QUESTO FILE NON FA

**Non salva.** Decide *quando*, e chiama indietro. `room.snapshot` si invoca da
`app/ws.py` e da nessun altro posto — è il recinto di
`tests/test_write_paths.py`, e questo modulo non ne è un'eccezione: è un
temporizzatore che chiede a quel posto lì di fare il suo mestiere.

**Non decide chi è presente.** Un client può dichiarare che dei salvataggi si
occupa lui (`Member.saves_itself`), e allora la rete si tira via. È una
dichiarazione sulla **divisione del lavoro**, non sull'essere seduti: un client
che dice «non salvo io» non sta dicendo di essere nella stanza, e un client che
dice «salvo io» non è più presente di prima.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

log = logging.getLogger("stratigraph.keeping")


def _number(name: str, fallback: float) -> float:
    """Una soglia dall'ambiente, o quella misurata. Un valore illeggibile NON
    spegne la rete: torna il default e lo dice, perché una variabile scritta
    male non deve avere lo stesso effetto di una rete disattivata."""
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except (TypeError, ValueError):
        log.warning("%s=%r is not a number: keeping the measured default %s",
                    name, raw, fallback)
        return fallback


#: Le due soglie. I numeri hanno la loro ragione in cima al file; l'ambiente può
#: cambiarli su un nodo con un disco lento o una stanza enorme.
SAVE_AFTER_OPS = int(_number("EM_SAVE_AFTER_OPS", 64))
SAVE_AFTER_QUIET = _number("EM_SAVE_AFTER_QUIET_SECONDS", 2.0)

#: Perché è scattato. Viaggia nella frame `snapshot_written`, così chi guarda un
#: registro distingue «l'ha chiesto qualcuno» da «è scattata la rete» — e senza
#: quella parola le due cose sono indistinguibili nei log.
BY_REQUEST = "requested"
BY_COUNT = "ops"
BY_QUIET = "quiet"
BY_LAST_OUT = "last-writer-left"


class Keeper:
    """Il temporizzatore che tiene una stanza, per conto di chi non lo chiede.

    Uno per processo. Tiene un compito per stanza — al massimo uno — e lo
    riarma a ogni operazione: la quiete è quiete di QUESTA stanza, non del nodo.
    """

    def __init__(self, save: Callable[[Any, str], Awaitable[Any]], *,
                 after_ops: Optional[int] = None,
                 after_quiet: Optional[float] = None) -> None:
        #: `async save(room, why)` — chi sa davvero scrivere. Iniettato e non
        #: importato: questo modulo non deve poter chiamare `room.snapshot`.
        self._save = save
        self.after_ops = SAVE_AFTER_OPS if after_ops is None else after_ops
        self.after_quiet = (SAVE_AFTER_QUIET if after_quiet is None
                            else after_quiet)
        self._timers: Dict[str, asyncio.Task] = {}

    # ── la politica ─────────────────────────────────────────────────────────

    def covers(self, room: Any) -> bool:
        """Se questa stanza ha bisogno della rete.

        **Basta un presente che NON dichiara perché la rete torni.** Non è
        prudenza generica: uno snapshot scrive il documento intero, quindi il
        lavoro di chi non salva viaggia dentro il salvataggio di chi salva — ma
        solo se quello salva, e solo quando lo fa. Un client che entra senza
        dichiarare niente è, per quanto ne sa il relay, il `RoomWriter` di ieri.

        **E UN SILENZIOSO NON È UN PRESENTE.** `writers_present()` toglie chi
        tace da più di `QUIET_AFTER`, e la ragione sta nel suo docstring: da un
        client congelato non arriverà nessun `request_save`, per quanto lo abbia
        dichiarato. La dichiarazione vale finché c'è qualcuno a mantenerla — la
        stessa frase che `emptied()` dice qui sotto per chi se n'è andato,
        applicata a chi è rimasto senza esserci.
        """
        writers = room.writers_present()
        if not writers:
            # Nessuno dentro: non c'è nessuno da cui aspettarsi un
            # `request_save`, quindi la rete c'è per definizione.
            return True
        return not all(m.saves_itself for m in writers)

    async def kept(self, room: Any) -> Optional[Dict[str, Any]]:
        """Da chiamare dopo OGNI operazione applicata sul socket.

        Torna il resoconto se ha salvato adesso, `None` se ha solo armato il
        temporizzatore — così chi chiama può annunciarlo senza doverlo dedurre.
        """
        if not room.unsaved:
            return None
        if not self.covers(room):
            self._disarm(room.room_id)
            return None
        if room.unsaved >= self.after_ops:
            return await self._now(room, BY_COUNT)
        self._arm(room)
        return None

    async def emptied(self, room: Any) -> Optional[Dict[str, Any]]:
        """L'ultimo che sa scrivere ha lasciato la stanza.

        Qui NON si guarda `covers`: un client che dichiarava di salvarsi da solo
        e se n'è andato senza farlo ha lasciato lo stesso lavoro per terra di
        uno che non aveva dichiarato niente. La dichiarazione vale finché c'è
        qualcuno a mantenerla.
        """
        self._disarm(room.room_id)
        if not room.unsaved:
            return None
        return await self._now(room, BY_LAST_OUT)

    def forget(self, room_id: str) -> None:
        self._disarm(room_id)

    # ── il temporizzatore ───────────────────────────────────────────────────

    def _arm(self, room: Any) -> None:
        self._disarm(room.room_id)
        self._timers[room.room_id] = asyncio.get_running_loop().create_task(
            self._after_quiet(room))

    def _disarm(self, room_id: str) -> None:
        task = self._timers.pop(room_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _after_quiet(self, room: Any) -> None:
        try:
            await asyncio.sleep(self.after_quiet)
        except asyncio.CancelledError:
            return
        self._timers.pop(room.room_id, None)
        try:
            await self._now(room, BY_QUIET)
        except Exception as exc:      # noqa: BLE001
            # UN SALVATAGGIO CHE FALLISCE NON DEVE UCCIDERE IL COMPITO IN
            # SILENZIO. Il contatore resta dov'è — `snapshot` lo azzera solo
            # dopo `store.put` — quindi la prossima operazione riproverà, e il
            # debito resta visibile in `/health` nel frattempo.
            log.warning("keeping: the deferred save of %s failed: %s",
                        room.room_id, exc)

    async def _now(self, room: Any, why: str) -> Optional[Dict[str, Any]]:
        self._disarm(room.room_id)
        return await self._save(room, why)
