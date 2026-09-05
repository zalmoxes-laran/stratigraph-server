"""Bussare a ciò che si dichiara.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ QUESTO FILE ESISTE

`/health` elencava le sue dipendenze così:

    "asset_store": "minio (http://minio:9000, bucket em-assets)",
    "snapshot_store": "directory (local only — not for replicas)",

Ogni riga è una **descrizione della configurazione**. Nessuna di quelle stringhe
ha mai toccato niente. Un endpoint di salute che riporta ciò che gli è stato
detto sta rileggendo all'operatore il suo stesso file di configurazione, con
l'aria di aver verificato qualcosa.

Non è un'astrazione: il 24 settembre il chatbot è stato puntato a
`http://em-server:8000` — un nome che su quella rete non esiste — ha ripiegato
in silenzio sul suo container locale, e il suo `/health` ha continuato a
dichiarare «writes_to: room … at http://em-server:8000» per tutta la notte. Le
schede di un'intera prova sono finite nel posto sbagliato con tutti i semafori
verdi.

Ed è la **quarta volta in cinque giorni** che incontriamo la stessa specie di
difetto — una risposta che riporta l'intenzione invece del fatto:

* `colima list` che legge la configurazione invece del montaggio;
* il fallback di Caddy che rispondeva 200 a un percorso non instradato;
* le etichette i18n che pescavano una parola invece di rifiutare;
* e questo.

════════════════════════════════════════════════════════════════════════════════
## LE TRE RISPOSTE, E LA TERZA È LA PIÙ IMPORTANTE

* **`reached`** — ho bussato e ha risposto. Con quanto ci ha messo.
* **`unreachable`** — ho bussato e non ha risposto, **con la ragione**. Un
  guasto senza la sua frase è una spia rossa che costringe a indovinare.
* **`off`** — **assente per configurazione, e non è un guasto.** È la regola di
  casa: uno store in memoria su un portatile non è un errore, è una funzione
  spenta, e dipingerla di rosso insegnerebbe a ignorare il rosso.

════════════════════════════════════════════════════════════════════════════════
## ALL'AVVIO E SU RICHIESTA, MAI IN UN CICLO

Bussare costa: una connessione a MinIO, una GET al realm. Farlo a ogni `/health`
significherebbe che l'healthcheck di Docker — ogni pochi secondi, per sempre —
martella l'identity provider di ogni nodo dell'ecosistema.

Quindi si bussa **una volta all'avvio** e **quando qualcuno lo chiede**, e il
risultato porta il suo istante. Un'osservazione datata non è una cache: una
cache dice «è così», questa dice «alle 06:52 era così», che è una frase vera
anche un'ora dopo. La differenza è tutta nel campo `at`, ed è il motivo per cui
non è mai omesso.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

REACHED = "reached"
UNREACHABLE = "unreachable"
OFF = "off"


def _now() -> str:
    from .rooms import now_iso
    return now_iso()


@dataclass(frozen=True)
class Knock:
    """Una dipendenza, e cosa è successo quando le si è bussato."""
    name: str
    #: la stringa che `/health` dichiarava — tenuta accanto al fatto, perché
    #: «dichiarata X · raggiunta» e «dichiarata Y · raggiunta» non sono la
    #: stessa notizia e senza la X non si distinguono.
    declared: str
    state: str
    at: str
    detail: str = ""
    ms: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def knock(name: str, declared: str, probe: Optional[Callable[[], str]]) -> Knock:
    """Esegue una prova e la racconta. `probe=None` significa **spenta**.

    La prova torna una frase su cosa ha trovato («bucket em-assets, 3 oggetti»)
    e solleva se non ci arriva. Non torna un booleano: un `True` non si può
    leggere in un referto.
    """
    if probe is None:
        return Knock(name=name, declared=declared, state=OFF, at=_now(),
                     detail="absent by configuration — a function that is off, "
                            "not a fault")
    started = time.perf_counter()
    try:
        detail = probe() or ""
    except Exception as exc:                      # noqa: BLE001 — è il punto
        return Knock(name=name, declared=declared, state=UNREACHABLE,
                     at=_now(), detail=f"{type(exc).__name__}: {exc}"[:300],
                     ms=round((time.perf_counter() - started) * 1000, 1))
    return Knock(name=name, declared=declared, state=REACHED, at=_now(),
                 detail=detail[:300],
                 ms=round((time.perf_counter() - started) * 1000, 1))


# ── le prove, una per specie di dipendenza ──────────────────────────────────
#
# Ognuna fa la cosa PIÙ PICCOLA che dimostri di esserci arrivata, e nessuna
# scrive. Una prova che scrivesse sarebbe una quinta via di scrittura aperta
# dall'healthcheck.

def probe_directory(path: str) -> Callable[[], str]:
    def go() -> str:
        import pathlib
        root = pathlib.Path(path)
        if not root.is_dir():
            raise FileNotFoundError(f"{path} is not a directory")
        # UN LISTATO, non una scrittura: leggere dimostra il montaggio, che è
        # ciò che manca quando un volume non c'è.
        count = sum(1 for _ in root.iterdir())
        return f"{path} · {count} entries"
    return go


def probe_minio(store: Any) -> Callable[[], str]:
    def go() -> str:
        client = getattr(store, "_client")
        bucket = getattr(store, "bucket")
        if not client.bucket_exists(bucket):
            raise RuntimeError(f"bucket {bucket!r} is not there")
        return f"{getattr(store, 'endpoint', '?')} · bucket {bucket}"
    return go


def probe_jwks(uri: str) -> Callable[[], str]:
    def go() -> str:
        import json
        import urllib.request
        # UN TIMEOUT CORTO, perché questa prova sta dentro una richiesta: un
        # realm che non risponde deve dire «non risponde», non tenere aperta la
        # rotta della salute finché qualcuno se ne accorge.
        with urllib.request.urlopen(uri, timeout=3) as answer:
            document = json.loads(answer.read())
        keys = document.get("keys") or []
        if not keys:
            raise RuntimeError("the JWKS answered with no keys in it")
        return f"{len(keys)} signing key(s)"
    return go


# ── il registro dell'ultima bussata ─────────────────────────────────────────

@dataclass
class Reachability:
    """L'ultima volta che si è bussato, e cosa si è sentito.

    Vive nel processo perché è un'osservazione del processo: due repliche dietro
    lo stesso nome hanno due risposte diverse a «tu ci arrivi?», e fonderle in
    un posto condiviso trasformerebbe una misura in una media.
    """
    at: Optional[str] = None
    #: cosa ha innescato la bussata — `startup` o `request`. Un operatore che
    #: legge «alle 06:52» vuole sapere se è l'avvio di stanotte o la sua GET.
    why: str = ""
    knocks: List[Knock] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        if self.at is None:
            # MAI UNA LISTA VUOTA che si legge come «tutto a posto». Non aver
            # bussato è uno stato, e si dice.
            return {"at": None, "why": "never knocked",
                    "summary": {}, "dependencies": []}
        summary: Dict[str, int] = {}
        for one in self.knocks:
            summary[one.state] = summary.get(one.state, 0) + 1
        return {"at": self.at, "why": self.why, "summary": summary,
                "dependencies": [k.as_dict() for k in self.knocks]}

    @property
    def all_reached(self) -> bool:
        """Nessuna dipendenza dichiarata e irraggiungibile. Le spente non
        contano: sono spente per volontà di chi ha configurato."""
        return not any(k.state == UNREACHABLE for k in self.knocks)
