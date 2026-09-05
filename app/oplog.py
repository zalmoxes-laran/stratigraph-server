"""Il registro della stanza, che dura.

════════════════════════════════════════════════════════════════════════════════
## COSA C'ERA GIÀ, E COSA MANCAVA

Quasi tutto c'era. Il join accetta `?since=`, `replay_since()` è corretta,
`host_info` annuncia `compacted_upto` con la regola scritta accanto. Mancava una
cosa sola: **il registro viveva in memoria e moriva col processo**, tagliato a
`OPLOG_LIMIT = 512`.

Misurato prima di scrivere una riga, sulla dev-stack:

    scritte 6 operazioni · cursore 2026-09-28T10:05:00Z
    rientro con since=…10:02:00Z  → 3 operazioni riprodotte  ✓
    docker restart em-dev-server
    LO STESSO rientro               → 0 operazioni riprodotte

## DUE MEMORIE, DUE MESTIERI

`Room.oplog` resta quello che era: una **finestra in memoria** di 512
operazioni, veloce, che serve il caso normale — un client che si riaffaccia dopo
qualche minuto.

Questo file è la **memoria profonda**: un JSONL accanto allo snapshot, che
sopravvive al riavvio e tiene molto più indietro. Si legge solo quando la
finestra in memoria non arriva abbastanza indietro, cioè quasi mai.

Non dentro l'`em.json`: quello è un formato che l'ecosistema intero sa leggere,
e infilarci una coda di operazioni ne farebbe un formato nostro. È la stessa
ragione per cui il ponte di StratiField sta accanto al container e non dentro.

════════════════════════════════════════════════════════════════════════════════
## QUANTO TIENE, E PERCHÉ QUEL NUMERO

**Misurato**, mandando una scheda ICCD intera sul socket di una stanza vera:

    add_node        324 byte (mediana)
    update_field    155 byte (mediana)
    add_edge        176 byte
    ── una scheda ICCD: 29 operazioni, 5019 byte  →  173 byte per operazione

Il caso che deve coprire è **un telefono via due giorni su uno scavo attivo**.
Una squadra che registra trenta unità al giorno — che è molto — fa 60 schede in
due giorni: **~1740 operazioni, ~300 KB**.

`KEEP_OPS = 10_000` copre quel caso **cinque volte e mezzo** e costa, alla
dimensione misurata, **1,7 MB per stanza** — meno del documento più grande di
questo nodo (`sarmizegetusa`, 2,3 MB). Il tetto è `KEEP_OPS × la dimensione di
un'operazione`: una stanza le cui operazioni fossero dieci volte più grandi
(descrizioni lunghissime) farebbe un registro da 17 MB, ed è il caso da
guardare. **Dichiarato, non sorvegliato**: un secondo limite sui byte
sorveglierebbe un caso che non ho visto, e il primo è già verificabile.

## A CONTEGGIO E NON A TEMPO, e la ragione non è la comodità

Il registro è ordinato dal `ts` delle operazioni, che è **l'orologio del
client** — il relay lo onora apposta, perché una nota dettata in trincea alle 10
e sincronizzata alle 18 deve portare le 10. Potare a tempo su quel campo
vorrebbe dire che **un client con l'orologio sbagliato pota il registro della
stanza**: una ritenzione controllata da chi scrive.

Ogni riga porta anche `at`, l'istante in cui il **server** l'ha ricevuta. Non
serve alla riproduzione — quella usa il `ts` — e costa trenta byte: serve a
leggere il registro, che è l'altro mestiere (vedi «rigiocabile e leggibile» in
fondo), e sarebbe la base di una ritenzione a tempo se un giorno la si volesse.

## E NON DIPENDE DAL WATERMARK

`gc_watermark()` misura la **consegna** e non l'applicazione — misurato il 27
settembre — e **un client su tre manda `ack`**. Un registro la cui durata
dipendesse da quel numero si accorcerebbe da solo per ragioni che nessuno
controlla. La ritenzione è una politica di questo file, e di nessun altro.

════════════════════════════════════════════════════════════════════════════════
## LA SCRITTURA: FLUSH SÌ, FSYNC NO — con i numeri

    flush soltanto   mediana 0,005 ms · p95 0,007 ms
    flush + fsync    mediana 0,361 ms · p95 1,549 ms
    (un'operazione sul socket, misurata il 26/09: 0,1 ms)

`fsync` moltiplicherebbe per quattro il costo di un'operazione, e per quindici
al novantacinquesimo percentile. E **non comprerebbe niente**: `store.put`, che
scrive il documento, non fa `fsync`. Un `fsync` qui renderebbe il registro più
durevole del documento che accompagna — e una mancanza di corrente che perde la
coda del registro perde anche quella del documento.

La morte del processo — `docker restart`, che è il caso frequente — non perde
niente in nessuno dei due: le scritture bufferizzate sono del sistema
operativo, non del processo.

**Diverso dal ponte di StratiField, che invece fa `fsync`**, e la differenza è
la ragione: là la coda è **l'unica copia** di quel lavoro; qui il registro
accompagna un documento che lo stesso processo scrive sullo stesso ritmo.

## UN SOLO SCRITTORE, E NESSUN LUCCHETTO FRA PROCESSI

Il ponte usa `flock` perché due processi hanno condiviso quella coda per caso.
Qui no: **una stanza vive in un processo solo** — `RoomRegistry` è la copia di
lavoro di *questo* relay, e due relay sullo stesso snapshot sarebbero già un
problema molto più grande di un registro. E la scrittura è un `append`, che il
sistema operativo serializza da sé quando il file è aperto in `"a"`.

Il lucchetto c'è comunque **fra i thread**, perché la potatura riscrive il file
e non deve incrociare un `append`.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
from typing import Any, Dict, List, Optional

log = logging.getLogger("stratigraph.oplog")

#: Quante operazioni tiene un registro. Il conto sta in cima al file.
KEEP_OPS = int(os.environ.get("EM_OPLOG_KEEP", "10000"))

#: Quanto lo si lascia crescere oltre il tetto prima di potarlo. Potare
#: riscrive il file: farlo a ogni operazione oltre il tetto significherebbe
#: riscrivere 1,7 MB per ogni operazione di una stanza attiva. Con questo
#: margine si riscrive una volta ogni mille.
SLACK = int(os.environ.get("EM_OPLOG_SLACK", "1000"))


class Journal:
    """Un registro durevole per una stanza: JSONL, in append, accanto al documento.

    Non decide niente sulla riproduzione: tiene le righe e le ridà in ordine.
    Chi può rigiocarle, e fino a dove, lo decide `app/ws.py` — perché è lì che
    si sa cosa la stanza ha già compattato.
    """

    def __init__(self, path: str, *, keep: int = KEEP_OPS,
                 slack: int = SLACK) -> None:
        self.path = pathlib.Path(path)
        self.keep = keep
        self.slack = slack
        self._lock = threading.Lock()
        #: quante righe ci sono, senza rileggere il file a ogni append. Conta
        #: dal disco la prima volta e poi si tiene aggiornato.
        self._count: Optional[int] = None
        #: righe illeggibili incontrate leggendo. Contate e non nascoste: un
        #: registro che salta righe in silenzio è peggio di uno che non c'è.
        self.unreadable = 0

    # ── scrivere ────────────────────────────────────────────────────────────

    def mark_compacted(self, upto: str, *, at: str) -> None:
        """Segna fin dove la stanza è stata compattata.

        ════════════════════════════════════════════════════════════════════
        PERCHÉ STA QUI DENTRO E NON ALTROVE

        `Room.compacted_upto` vive in memoria e riparte da `None` a ogni
        riavvio. Finché il registro moriva col processo era coerente: nessun
        registro, nessuna riproduzione, nessun pericolo.

        Con un registro che dura, no. Dopo un riavvio il documento sul disco è
        **già compattato** e `compacted_upto` sarebbe `None`: il rifiuto che
        impedisce la resurrezione di un arco non scatterebbe **proprio quando
        il registro comincia a contare**. La guardia sarebbe evaporata nel
        momento in cui serve.

        Quindi il punto di compattazione sta nel registro, che è il file la cui
        vita coincide con quella della riproduzione: **una guardia non deve
        poter sopravvivere alla prova che la giustifica, né il contrario.**
        """
        riga = json.dumps({"at": at, "compacted_upto": upto}, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(riga + "\n")
                handle.flush()
            if self._count is not None:
                self._count += 1

    def compacted_upto(self) -> Optional[str]:
        """L'ultimo punto di compattazione segnato, o `None`."""
        if not self.path.is_file():
            return None
        trovato = None
        with self._lock:
            for riga in self.path.read_text(encoding="utf-8").splitlines():
                if "compacted_upto" not in riga:
                    continue
                try:
                    voce = json.loads(riga)
                except ValueError:
                    continue
                segno = voce.get("compacted_upto")
                if segno and (trovato is None or str(segno) > trovato):
                    trovato = str(segno)
        return trovato

    def append(self, op: Dict[str, Any], *, at: str) -> None:
        """Una riga. `at` è l'istante del SERVER, e non è il `ts` dell'operazione."""
        riga = json.dumps({"at": at, "op": op}, ensure_ascii=False)
        with self._lock:
            if self._count is None:
                self._count = self._count_lines()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(riga + "\n")
                handle.flush()
            self._count += 1
            if self._count > self.keep + self.slack:
                self._prune()

    def _count_lines(self) -> int:
        if not self.path.is_file():
            return 0
        with open(self.path, "rb") as handle:
            return sum(1 for _ in handle)

    def _prune(self) -> None:
        """Tiene le ultime `keep` righe. Sotto il lucchetto di chi chiama.

        Riscrittura su `.tmp` e `replace`, come lo store: se il nodo muore a
        metà, quello che resta sul disco è il registro di prima — vecchio, e
        leggibile. Un registro troncato a metà riga non lo sarebbe.
        """
        try:
            righe = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:                # noqa: BLE001
            log.warning("oplog: non ho potuto potare %s: %s", self.path, exc)
            return
        resto = righe[-self.keep:]
        # IL SEGNO DI COMPATTAZIONE NON SI POTA. Se cadesse fuori dalla
        # finestra, dopo una potatura il rifiuto smetterebbe di scattare — e
        # sarebbe una guardia che sparisce da sola, in silenzio, su una stanza
        # attiva. Si ripesca il più recente e si rimette in testa.
        segno = None
        for riga in righe:
            if "compacted_upto" in riga:
                try:
                    voce = json.loads(riga)
                except ValueError:
                    continue
                if voce.get("compacted_upto"):
                    segno = riga
        if segno is not None and segno not in resto:
            resto.insert(0, segno)
        temporaneo = self.path.with_suffix(self.path.suffix + ".tmp")
        temporaneo.write_text("\n".join(resto) + "\n", encoding="utf-8")
        temporaneo.replace(self.path)
        self._count = len(resto)
        log.info("oplog: %s potato da %d a %d righe",
                 self.path.name, len(righe), len(resto))

    # ── leggere ─────────────────────────────────────────────────────────────

    def read(self) -> List[Dict[str, Any]]:
        """Le operazioni, in ordine di arrivo.

        Una riga illeggibile si salta e si CONTA. Perdere il resto del registro
        per un byte storto sarebbe il difetto che questo file esiste per
        chiudere, in miniatura — ed è la stessa scelta del ponte.
        """
        if not self.path.is_file():
            return []
        fuori: List[Dict[str, Any]] = []
        rotte = 0
        with self._lock:
            for riga in self.path.read_text(encoding="utf-8").splitlines():
                if not riga.strip():
                    continue
                try:
                    voce = json.loads(riga)
                except ValueError:
                    rotte += 1
                    continue
                if "compacted_upto" in voce:
                    continue          # un segno, non un'operazione
                try:
                    fuori.append(voce["op"])
                except (KeyError, TypeError):
                    rotte += 1
        self.unreadable = rotte
        if rotte:
            log.warning("oplog: %d righe illeggibili in %s, saltate",
                        rotte, self.path)
        return fuori

    def since(self, cursor: str) -> List[Dict[str, Any]]:
        """Le operazioni con un `ts` successivo al cursore.

        Lo stesso confronto di `Room.replay_since`: lessicografico su un ISO in
        UTC, che per quel formato è l'ordine cronologico.
        """
        return [op for op in self.read() if str(op.get("ts") or "") > cursor]

    def oldest(self) -> Optional[str]:
        """Il `ts` più vecchio che questo registro tiene ancora — cioè **fin dove
        si può guardare indietro**. `None` se il registro è vuoto.

        Serve al rifiuto: un cursore più vecchio di questo non può ricevere un
        replay completo, e riceverne uno parziale sarebbe peggio del niente —
        il client crederebbe di essere allineato.
        """
        righe = self.read()
        istanti = [str(op.get("ts") or "") for op in righe if op.get("ts")]
        return min(istanti) if istanti else None

    def __len__(self) -> int:
        with self._lock:
            if self._count is None:
                self._count = self._count_lines()
            return self._count

    def describe(self) -> Dict[str, Any]:
        return {"entries": len(self), "keep": self.keep,
                "path": str(self.path), "unreadable": self.unreadable,
                "compacted_upto": self.compacted_upto()}

    def forget(self) -> None:
        """Butta il registro. Usata solo dai test e da chi dismette una stanza."""
        with self._lock:
            self.path.unlink(missing_ok=True)
            self._count = 0


def journal_for(store: Any, room_id: str) -> Optional[Journal]:
    """Il registro che accompagna una stanza, **se c'è un posto dove metterlo**.

    Accanto agli snapshot quando lo store è una directory; `None` quando è in
    memoria — su un portatile senza `EM_SNAPSHOT_DIR` non c'è niente che
    sopravviva al processo, e un registro che finge di durare sarebbe peggio di
    nessun registro. `/health` dice quale dei due.
    """
    root = getattr(store, "root", None)
    if root is None:
        return None
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in room_id)
    return Journal(str(pathlib.Path(root) / f"{safe}.oplog.jsonl"))


def describe(journal: Optional[Journal]) -> str:
    if journal is None:
        return "memory only (dies with the process — no replay after a restart)"
    return f"durable, keeping {journal.keep} operations per room"
