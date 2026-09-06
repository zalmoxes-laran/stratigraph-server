"""Cosa ci siamo detti — la conversazione di una stanza, letta e basta.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ UNA CHAT È UNA COSA SERIA

In uno scavo **la conversazione è il posto dove si decide**: che cosa sia quella
US, se il taglio è lo stesso, se la fotografia mostra quello che sembra. La tesi
dell'Extended Matrix è che il ragionamento è dato; una piattaforma che tiene il
grafo e butta la discussione che l'ha prodotto contraddice la propria premessa.

════════════════════════════════════════════════════════════════════════════════
## QUESTO MODULO NON SCRIVE, E NON PER PUDORE

`tests/test_write_paths.py` tiene le vie di scrittura a **due**: `room.apply` e
`room.snapshot` si chiamano da `ws.py` e da nessun altro posto. Una chat con la
propria porta di scrittura sarebbe la terza, e sarebbe nata — come la via della
fotogrammetria del 9 settembre — con l'arbitro giusto e niente del resto: senza
il lucchetto, senza lo snapshot, senza l'annuncio.

**Quindi un messaggio non ha una rotta: è un'operazione `add_node`**, sul socket
che c'è già. Il che non è un ripiego, è il motivo per cui questa cosa costa così
poco:

    l'autore     lo mette il relay dal TOKEN            (`ws.py`: mai il client)
    l'istante    lo mette il relay                      (`created_at`)
    la durata    lo snapshot su disco + il registro     (`oplog.py`)
    l'ordine     gli orologi, non il registro           (§ sotto)
    il confine   `aux_volatile` + `document_view`       (il contratto s3Dgraphy)

Cinque cose che servivano, e zero costruite.

════════════════════════════════════════════════════════════════════════════════
## L'ORDINE VIENE DAGLI OROLOGHI, NON DAL REGISTRO

Il 26 settembre la difficoltà dichiarata era: *«ricostruire l'ordine di una
conversazione dopo un riavvio: l'ordine oggi vive nel registro delle operazioni,
che non dura»*. Dal 28 settembre il registro dura — ma la correzione del 28 resta
valida per un'altra ragione: **il registro ha una finestra e una conversazione
no.** Diecimila operazioni per stanza sono tante per un pomeriggio e poche per un
anno, e una conversazione che comincia a sparire dalla testa non è una
conversazione.

Quindi l'ordine sta **nel dato**: `data.created_at`, che lo snapshot porta con sé
e che nessuna compattazione tocca.

════════════════════════════════════════════════════════════════════════════════
## NESSUN TIPO DI NODO NUOVO, E L'ARGOMENTO

Il datamodel di s3Dgraphy descrive **quello che uno studio dice**. Un messaggio
che non arriva mai in un documento non è parte di quello che lo studio dice:
metterci `MessageNode` farebbe imparare una parola nuova a Blender, a Heriverse
e alla proiezione RDF per una cosa che non vedranno mai.

Quindi `node_type` è **`UnknownNode`**, che nel datamodel è la parola per «la
sorgente non ha saputo dare un tipo a questo» — ed è esattamente vero: la lingua
EM non ha una parola per un messaggio di chat, e `UnknownNode` è la parola per
non averla. Ciò che distingue un messaggio non è il tipo ma **chi ce l'ha
messo**: `data.aux_volatile == "chat"`, il marcatore che il contratto usa già per
dire «questa è una proposta, non un fatto dello studio».

**E la promozione a paradato NON è `bake`.** `contract.bake()` toglie il
marcatore, e un messaggio smarcato resta un `UnknownNode` con dentro una frase:
un documento pubblicato conterrebbe un nodo senza tipo. Promuovere davvero
significa costruire un `PropertyNode` che cita il messaggio, ed è un atto che
**non esiste** — perché costruirlo vuole prima la risposta alla domanda di §2 del
prompt del 5 ottobre, che è di E.D.

════════════════════════════════════════════════════════════════════════════════
## E LA CANCELLAZIONE, CHE È DUE ATTI

«Il CRDT non dimentica» non è una risposta accettabile da dare a un collega.
Misurato, quello che si può fare è:

    remove_node          → il nodo porta un tombstone e sparisce dalle viste,
                           MA `data.said` resta scritto nello snapshot
    update_field + remove → la frase esce dallo stato, il nodo esce dalle viste

`retract_ops` costruisce la coppia, in quest'ordine, e il secondo atto senza il
primo è quello che sembra bastare e non basta.

**Quello che resta, detto:** il registro (`oplog.py`, 10 000 operazioni su disco)
conserva l'operazione originale con dentro la frase, finché non esce dalla
finestra. Quindi la promessa che si può fare a un collega è *«smette di essere
detta»*, non *«non è mai stata detta»* — e chi può ancora leggerla nel registro è
l'insieme delle persone della stanza, cioè quelle che l'avevano già sentita.
Cancellarla anche da lì vuole una compattazione che riscrive il registro, ed è
una decisione di E.D.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from s3dgraphy import crdt as em_crdt

#: Il marcatore di volatilità del contratto s3Dgraphy — IMPORTATO e non
#: ricopiato, perché è la parola su cui poggia il confine della pubblicazione —
#: e il valore che dice «questo ce l'ha messo la conversazione». Non un tipo di
#: nodo: un iniettore.
#:
#: Gli altri tre (`INJECTOR`, `SAID`, `NODE_TYPE`) sono ricopiati nel chatbot
#: (`app/conversazione.py`), perché quel repo non dipende da questo. Tre
#: stringhe, e una prova per parte che le fissa: è una cucitura che può
#: divergere, e la si dichiara invece di fingere che non ci sia.
from s3dgraphy.contract import VOLATILE_KEY   # noqa: E402  «aux_volatile»

INJECTOR = "chat"

#: Dove sta la frase. In `data` e non in `name`, perché `name` è il nome di un
#: nodo e una frase non è un nome — e perché un campo di `data` ha il proprio
#: orologio, che è ciò che rende ritrattabile la frase da sola.
SAID = "said"

#: Il tipo, che è la dichiarazione di non avere un tipo. Vedi la docstring.
NODE_TYPE = "UnknownNode"


def is_message(node: Dict[str, Any]) -> bool:
    """Un nodo è un messaggio se ce l'ha messo la conversazione."""
    data = node.get("data")
    return isinstance(data, dict) and data.get(VOLATILE_KEY) == INJECTOR


def message_op(text: str, *, node_id: str,
               author: Optional[str] = None) -> Dict[str, Any]:
    """L'operazione che dice una frase. **Senza autore e senza istante.**

    I due campi che non stanno qui sono i due che contano: il relay mette
    l'autore dal token e la data alla ricezione (`ws.py`, `pop("author")` +
    `setdefault("ts")`). Un `author` scritto da un client è un autore che
    chiunque può prendere in prestito, e questa funzione non offre il posto dove
    scriverlo — `author` esiste solo per i test, che non hanno un relay.
    """
    op: Dict[str, Any] = {
        "op": "add_node",
        "node": {
            "id": node_id,
            "node_type": NODE_TYPE,
            "name": "",
            "data": {SAID: str(text), VOLATILE_KEY: INJECTOR},
        },
    }
    if author:
        op["author"] = author
    return op


def retract_ops(node_id: str) -> List[Dict[str, Any]]:
    """I DUE atti di una ritrattazione, in ordine.

    Il secondo da solo è quello che sembra bastare: il nodo sparisce dalle viste
    e la frase resta scritta nello snapshot. Il primo la toglie dallo stato.
    """
    return [
        {"op": "update_field", "node_id": node_id,
         "field": f"data.{SAID}", "value": ""},
        {"op": "remove_node", "node_id": node_id},
    ]


def _sections(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((document.get("graphs") or {}).values())


def conversation(document: Dict[str, Any], *,
                 include_retracted: bool = True) -> List[Dict[str, Any]]:
    """Cosa ci siamo detti, in ordine — dagli orologi, non dal registro.

    Un messaggio ritrattato resta nell'elenco **come ritrattazione**: sparire
    senza dirlo lascerebbe una conversazione con un buco che sembra un errore di
    lettura. Il testo però non c'è più, perché il primo dei due atti l'ha tolto.

    L'ordinamento è per `created_at`, e a parità di istante per `id`: due
    messaggi nello stesso millesimo non hanno un ordine vero e uno stabile è
    meglio di uno che cambia a ogni lettura.
    """
    fuori: List[Dict[str, Any]] = []
    for section in _sections(document):
        for node in section.get("nodes") or []:
            if not is_message(node):
                continue
            data = node.get("data") or {}
            ritrattato = em_crdt.is_removed(node)
            if ritrattato and not include_retracted:
                continue
            fuori.append({
                "id": str(node.get("id")),
                "at": data.get("created_at"),
                "by": data.get("created_by"),
                "said": "" if ritrattato else str(data.get(SAID) or ""),
                "retracted": bool(ritrattato),
                "graph_id": section.get("graph_id"),
            })
    fuori.sort(key=lambda m: (m["at"] or "", m["id"]))
    return fuori


def summary(document: Dict[str, Any]) -> Dict[str, Any]:
    """Quello che la stanza sa dire della propria conversazione, in quattro numeri.

    Sta accanto alle altre cose che la stanza sa di sé (`roomview`): una
    conversazione che dura è un'altra cosa che la stanza tiene, e si vede dove si
    vedono le altre.
    """
    detti = conversation(document)
    vivi = [m for m in detti if not m["retracted"]]
    voci = sorted({m["by"] for m in vivi if m["by"]})
    return {
        "messages": len(detti),
        "retracted": len(detti) - len(vivi),
        "voices": voci,
        "first": vivi[0]["at"] if vivi else None,
        "last": vivi[-1]["at"] if vivi else None,
        #: Nessun messaggio attraversa il confine della pubblicazione finché
        #: qualcuno non lo decide, e oggi nessuno può: la promozione a paradato
        #: non esiste (vedi la docstring del modulo). Il numero c'è perché il
        #: giorno che esistesse, questo è il posto dove si vede.
        "in_the_document": sum(1 for s in _sections(document)
                               for n in (s.get("nodes") or [])
                               if (n.get("data") or {}).get(SAID) is not None
                               and not is_message(n)),
    }
