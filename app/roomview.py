"""Cosa sa dire una stanza di sé — le domande di chi si siede.

Una stanza non è un database di siti: è un tavolo di lavoro. Le schermate che
contano un patrimonio («Total Sites 2 · Total US 54») rispondono bene a una
domanda che chi entra in una stanza non sta facendo.

Le domande vere sono quattro, e questo modulo le risponde:

* **chi c'è dentro adesso** — `who_is_here`
* **cosa aspetta me** — `waiting_for`
* **com'è fatto quello che abbiamo** — `statistics`
* **chi ha scritto qui** — `operators`

La quinta, «cosa è cambiato da quando non guardavo», **non è qui**, e la ragione
è misurata in `tests/test_il_registro_della_stanza.py`: il registro delle
operazioni della stanza vive solo in memoria, un'operazione arrivata dal socket
non viene nemmeno persistita, e una stanza dimenticata e ricostruita — cioè un
riavvio — perde entrambi. Costruire quella risposta su questo registro
significherebbe promettere una memoria che non c'è.

════════════════════════════════════════════════════════════════════════════════
## COSA QUESTO MODULO NON FA

**Non scrive.** Nessuna funzione qui tocca il documento, il registro o lo store.
`tests/test_write_paths.py` tiene le vie di scrittura a due, e questa non è una
terza.

**Non tiene un indice.** Una stanza è piccola e la verità è nel grafo: si conta
lì, a ogni richiesta. Un indice che diverge è l'errore che questo progetto ha già
pagato quattro volte, e il costo di ricontare è misurato nel referto invece di
essere evitato con una cache.

**Non sa cosa sia obbligatorio.** «Campi obbligatori vuoti» sarebbe il terzo buco
utile, e non si può contare da qui: `required` vive nella definizione della
scheda (`stratigraph-templates`), che **non viaggia col grafo**. `statistics` lo
dichiara invece di tacerlo — vedi `holes.cannot_count`.

════════════════════════════════════════════════════════════════════════════════
## UNA CONVENZIONE LETTA, CHE QUESTO SERVER NON POSSIEDE

L'autorialità **per campo** — quella che distingue un campo composto da un
modello da uno scritto da una persona — non è nella libreria. È una convenzione
di StratiField (`stratigraph-chatbot/app/authorship.py`), scritta dentro `data`
con chiavi appiattite dal CRDT:

    data["authorship.<campo>"] = {"by": "human"|"ai",
                                  "model": …,            # solo se by == "ai"
                                  "validated_by": ORCID, # ASSENTE finché nessuno
                                  "validated_at": …}     # l'ha confermato

Misurata su una stanza vera il 2026-09-25 (`US44` in
`probe-stratifield-02-aereo`): 26 chiavi `authorship.*`, tutte `{"by": "human"}`.

Qui si LEGGE, e la costante sta in un posto solo. **Da chiedere:** se questa
convenzione deve reggere anche per EMStudio e per un import da Tropy, il suo
posto è `s3dgraphy`, accanto a `editorial.py` che già timbra chi e quando. Non
stanotte: stanotte si legge.

L'autorialità **per campo verificata** invece è della libreria e c'è già:
`data["field_clocks"]["data.<campo>"] = {"ts": …, "by": ORCID}` — e quell'ORCID
viene dal token, non da quello che il client ha dichiarato.
"""

from __future__ import annotations

import collections
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import presence

#: Dove StratiField scrive l'autorialità di un campo, dentro `data`.
AUTHORSHIP_PREFIX = "authorship."
#: I due soli autori che quella convenzione ammette.
AI = "ai"
HUMAN = "human"

#: Il tipo di nodo che è un'epoca. **Misurato**, non dedotto dal nome della
#: classe: sulla stanza `aiano` sono 5 nodi con `node_type == "EpochNode"`.
EPOCH_NODE_TYPE = "EpochNode"

#: GLI ARCHI CHE DATANO UN NODO — e questa riga è nata da un errore evitato per
#: un pelo.
#:
#: Avevo scritto `is_in_epoch`, che suona giusto e **non esiste**: nel datamodel
#: delle connessioni 1.6.13 è soltanto la `reverse.name` di `has_sub_epoch`
#: (epoca → fase), cioè un'etichetta di lettura, non un tipo d'arco. È lo stesso
#: inciampo di `is_before` per `is_after`, in un altro repository.
#:
#: I due veri, misurati su `aiano`: `has_first_epoch` (181 archi) — «l'epoca
#: iniziale associata a un nodo» — e `survive_in_epoch` (89) — «continua a
#: esistere in quell'epoca». Un'unità è DATATA se ne ha almeno uno: chiedere il
#: primo soltanto direbbe «senza epoca» di una che sopravvive e basta.
EPOCH_EDGES = ("has_first_epoch", "survive_in_epoch")

#: I prefissi dei tipi di nodo che sono UNITÀ stratigrafiche. Copiati dal
#: chatbot (`app/writer.py::_STRAT_PREFIXES`) perché è la stessa domanda, e
#: **dichiarati qui come copia** invece di importati: quel modulo sta in un
#: altro repository e questo server non ne dipende. Se la lista diverge, il
#: posto giusto è la libreria — è la stessa richiesta della convenzione sopra.
UNIT_PREFIXES = ("US", "USV", "USD", "SF", "VSF", "RSF", "ser", "TSU", "UL",
                 "USN", "BR", "SE")


# ── leggere il documento senza inventarsi una forma ─────────────────────────

def sections(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """I grafi del contenitore. Un `em.json` è SEMPRE `{"graphs": {…}}`."""
    return list((document.get("graphs") or {}).values())


def nodes(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [n for section in sections(document)
            for n in (section.get("nodes") or [])]


def edges(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for section in sections(document)
            for e in (section.get("edges") or [])]


def is_unit(node: Dict[str, Any]) -> bool:
    return str(node.get("node_type") or "").startswith(UNIT_PREFIXES)


def _data(node: Dict[str, Any]) -> Dict[str, Any]:
    return node.get("data") or {}


def _authorship(node: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """`{campo: marchio}` per i campi di cui questo nodo dichiara l'autore."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in _data(node).items():
        if not key.startswith(AUTHORSHIP_PREFIX):
            continue
        if isinstance(value, dict):
            out[key[len(AUTHORSHIP_PREFIX):]] = value
    return out


def _clock(node: Dict[str, Any], field: str) -> Dict[str, Any]:
    """L'orologio di campo, che è dove sta l'identità VERIFICATA."""
    clocks = _data(node).get("field_clocks")
    if not isinstance(clocks, dict):
        return {}
    found = clocks.get(f"data.{field}")
    return found if isinstance(found, dict) else {}


def _label(node: Dict[str, Any]) -> str:
    return str(node.get("name") or node.get("id") or "")


# ── §2.2 · chi c'è dentro adesso ────────────────────────────────────────────

def who_is_here(room: Any, *, recent_after: Optional[str] = None) -> Dict[str, Any]:
    """Due liste, e **non si fondono**.

    ════════════════════════════════════════════════════════════════════════
    UN CORRISPONDENTE NON È SEDUTO, ed è il motivo per cui questa funzione
    torna due cose invece di una.

    `seated` è la presenza del relay: chi ha un socket aperto adesso. È
    effimera per costruzione — uno sguardo non è un fatto sullo studio — e
    sparisce quando la connessione cade.

    `wrote_recently` è tutt'altro: chi ha lasciato un timbro nel grafo dopo un
    certo istante. pyarchinit-mini consegna a raffiche e non tiene un posto;
    StratiField in trincea può essere offline da un'ora e aver scritto dieci
    minuti fa.

    Fonderle produrrebbe una lista che mente in **tutte e due** le direzioni:
    direbbe «presente» di chi ha consegnato e se n'è andato, e «assente» di chi
    è seduto e non ha ancora scritto niente. Quindi due liste, e ogni voce dice
    se compare anche nell'altra — così un client può disegnare l'unione senza
    che sia il server a decidere che sono la stessa cosa.

    `recent_after` è un istante ISO: se manca, `wrote_recently` è **vuota** e
    lo dice. Un default («le ultime 24 ore») sarebbe una politica inventata
    qui, e la finestra la sceglie chi guarda.

    ════════════════════════════════════════════════════════════════════════
    ## E DA STANOTTE `seated` NON È PIÙ UN SÌ O UN NO

    Ogni seduto porta il suo `state`: `in`, oppure `quiet` con `quiet_since` e
    `silent_for` («HH:MM»). Non è cosmesi. Chi scava accanto a Elisa deve
    poter decidere **da solo** se aspettarla, e «silenziosa da tre minuti» è un
    fatto su cui si agisce mentre «c'è» non lo è.

    E `left` è il terzo stato: chi era seduto e non c'è più, con l'ora e con
    `was_quiet` — perché «ha chiuso» e «la rete ha ceduto» sono due uscite
    diverse. Senza questa lista una persona che se ne va si legge come una che
    non è mai passata di qui, che è la seconda delle due bugie.
    """
    seated = list(room.presence())
    seated_authors = {m.get("author") for m in seated if m.get("author")}

    writers: Dict[str, Dict[str, Any]] = {}
    if recent_after:
        for who, at, what in _stamps(room.document):
            if at <= recent_after:
                continue
            entry = writers.setdefault(who, {"author": who, "last_at": at,
                                             "nodes": set()})
            entry["nodes"].add(what)
            if at > entry["last_at"]:
                entry["last_at"] = at

    wrote = sorted(
        ({"author": e["author"], "last_at": e["last_at"],
          "nodes_touched": len(e["nodes"]), "seated": e["author"] in seated_authors}
         for e in writers.values()),
        key=lambda e: e["last_at"], reverse=True)
    wrote_authors = {e["author"] for e in wrote}

    left = list(room.departures())
    quiet = [m for m in seated if m.get("state") == "quiet"]

    return {
        "seated": [dict(m, wrote_recently=(m.get("author") in wrote_authors))
                   for m in seated],
        "wrote_recently": wrote,
        # IL TERZO STATO, e in una lista sua. Fonderlo con `seated` rifarebbe
        # esattamente l'errore che questa funzione evita fra seduti e
        # scriventi: una lista che mente in tutte e due le direzioni.
        "left": left,
        # LA FINESTRA È DEL CHIAMANTE, e la risposta la ripete: un conteggio
        # senza il suo «da quando» è un numero che sembra assoluto.
        "recent_after": recent_after,
        # E LE SOGLIE, perché `quiet` è un giudizio e un giudizio senza il suo
        # criterio non si può contestare.
        "beat": {"every_seconds": presence.BEAT_SECONDS,
                 "quiet_after_seconds": presence.QUIET_AFTER},
        "counts": {"seated": len(seated),
                   # `in` + `quiet` = `seated`. Il conteggio vecchio non cambia
                   # significato — chi lo leggeva continua a leggere «quanti
                   # socket aperti» — e i due nuovi lo spiegano.
                   "in": len(seated) - len(quiet),
                   "quiet": len(quiet),
                   "left": len(left),
                   "wrote_recently": len(wrote)},
    }


def _stamps(document: Dict[str, Any]) -> Iterable[Tuple[str, str, str]]:
    """`(autore, istante, id del nodo)` per ogni timbro leggibile nel grafo.

    Tre fonti, in ordine di precisione: gli orologi di campo (per campo, con
    l'ORCID del token), `modified_by`/`modified_at`, `created_by`/`created_at`.
    """
    for node in nodes(document):
        data = _data(node)
        node_id = str(node.get("id") or "")
        clocks = data.get("field_clocks")
        if isinstance(clocks, dict):
            for mark in clocks.values():
                if isinstance(mark, dict) and mark.get("by") and mark.get("ts"):
                    yield str(mark["by"]), str(mark["ts"]), node_id
        for who_key, at_key in (("modified_by", "modified_at"),
                                ("created_by", "created_at")):
            who, at = data.get(who_key), data.get(at_key)
            if who and at:
                yield str(who), str(at), node_id


# ── §2.3 · cosa aspetta me ──────────────────────────────────────────────────

def waiting_for(document: Dict[str, Any], *,
                subject: Optional[str] = None) -> Dict[str, Any]:
    """I campi composti da un modello che nessuno ha ancora accettato.

    **Un debito che si conta si paga; uno nascosto è una palude.** Un campo
    scritto da una macchina e non validato è esattamente un debito: sta nel
    grafo, si legge come un fatto, e nessuno ci ha messo la faccia.

    Non validato = `validated_by` **assente**. Nasce assente e non `False`,
    perché «nessuno l'ha ancora guardato» e «qualcuno l'ha guardato e
    respinto» sono due cose diverse e la seconda non ha ancora un posto.

    `subject` (un ORCID) restringe alle unità che quella persona ha creato o
    modificata: è la differenza fra «cosa aspetta qualcuno» e «cosa aspetta
    **me**». Senza, si vede tutto il debito della stanza — che è la vista di
    chi la governa.

    ════════════════════════════════════════════════════════════════════════
    LA METÀ CHE MANCA, E NON È NASCOSTA IN UNA DOCSTRING

    Il prompt chiede anche «le operazioni rifiutate per una ragione che non è
    l'idempotenza». **Non sono qui perché non esistono da nessuna parte**: un
    rifiuto torna al chiamante nella risposta e finisce lì — non entra nel
    registro, non entra nel documento, non tocca lo store.

    Non è una supposizione:
    `tests/test_il_registro_della_stanza.py::test_un_rifiuto_non_lascia_traccia`
    lo dimostra su un rifiuto vero (`update_field` su un nodo che non c'è).

    E per questo la risposta **non ha una chiave `refused`**: una lista sempre
    vuota si legge come «nessun rifiuto», che è la cosa falsa da dire.
    """
    units: List[Dict[str, Any]] = []
    total_fields = 0
    oldest: Optional[str] = None
    models: collections.Counter = collections.Counter()

    for node in nodes(document):
        data = _data(node)
        if subject and subject not in (data.get("created_by"),
                                       data.get("modified_by")):
            continue
        pending: List[Dict[str, Any]] = []
        for field, mark in sorted(_authorship(node).items()):
            if str(mark.get("by") or "") != AI:
                continue
            if mark.get("validated_by"):
                continue
            # DA DOVE VIENE IL «DA QUANDO», e non è un dettaglio.
            #
            # Se il campo ha un suo orologio, `since` è l'istante in cui quel
            # campo è stato scritto: preciso. Se non ce l'ha — `add_node`
            # timbra il nodo, non i campi — si ripiega sul timbro del NODO, che
            # si muove ogni volta che qualcuno tocca qualunque cosa lì dentro.
            #
            # Misurato mentre si scriveva questo: validando `colore` alle 08:30,
            # il «da quando» di `definizione` è saltato da 08:00 a 08:30 senza
            # che nessuno l'avesse toccato. Un debito che sembra più giovane di
            # quello che è, è un debito che si rimanda.
            #
            # Quindi la risposta dice DA DOVE viene la data, invece di
            # presentare due cose diverse con lo stesso nome.
            clock = _clock(node, field)
            if clock.get("ts"):
                since, since_from = str(clock["ts"]), "field_clock"
            elif data.get("modified_at") or data.get("created_at"):
                since = str(data.get("modified_at") or data.get("created_at"))
                since_from = "node"
            else:
                since, since_from = "", ""
            model = str(mark.get("model") or "")
            if model:
                models[model] += 1
            pending.append({"field": field, "since": since,
                            "since_from": since_from, "model": model})
            if since and (oldest is None or since < oldest):
                oldest = since
        if pending:
            total_fields += len(pending)
            units.append({"id": node.get("id"), "name": _label(node),
                          "node_type": node.get("node_type"),
                          "fields": pending})

    units.sort(key=lambda u: (u["fields"][0]["since"] or "", str(u["id"])))
    return {"subject": subject,
            "units": units,
            "counts": {"units": len(units), "fields": total_fields},
            # L'ISTANTE PIÙ VECCHIO, con l'avvertenza che vale quanto la sua
            # fonte: se nessuno dei campi ha un orologio suo, questo è il timbro
            # di nodo più vecchio, che è una data più giovane della verità.
            "oldest": oldest,
            "oldest_from_field_clock": any(
                f["since"] == oldest and f["since_from"] == "field_clock"
                for u in units for f in u["fields"]),
            "by_model": dict(models.most_common())}


def unsaved_work(room: Any) -> Dict[str, Any]:
    """Il debito di specie peggiore: lavoro applicato e non ancora tenuto.

    Sta in «cosa aspetta me» accanto ai campi non validati, e non è un accostamento
    disinvolto: un campo composto da un modello e non confermato è un'affermazione
    di cui nessuno risponde ancora, un documento non salvato è un'affermazione che
    **può smettere di esistere**. La seconda è più urgente della prima.

    Il numero è `unsaved_ops`, non `snapshot_at`. Una data dice quando è successa
    una cosa; un conteggio dice cosa c'è da perdere — e il 25 settembre la
    differenza è stata ventisei campi.

    `at_risk` è vero solo quando non c'è più nessuno che sa scrivere nella stanza:
    con qualcuno dentro il lavoro non tenuto è normale e la rete lo coprirà fra
    poco, senza nessuno è lavoro lasciato per terra.
    """
    keeping = room.keeping()
    return {"unsaved_ops": keeping["unsaved_ops"],
            "saved_at": keeping["snapshot_at"],
            "writers_present": keeping["writers_present"],
            "at_risk": keeping["at_risk"]}


# ── §2.4 · com'è fatto quello che abbiamo ───────────────────────────────────

def statistics(document: Dict[str, Any]) -> Dict[str, Any]:
    """Il grafo di QUESTA stanza, contato sul grafo.

    **Le statistiche di una stanza sono un elenco di lavori da fare travestito
    da numeri.** Se quello che esce è solo una torta colorata il progetto è
    sbagliato, quindi `holes` non è un'appendice: è la metà utile, e sta nella
    stessa risposta dei conteggi perché un numero senza il suo buco accanto
    invita a guardare il numero.
    """
    all_nodes = nodes(document)
    all_edges = edges(document)
    units = [n for n in all_nodes if is_unit(n)]

    by_type = collections.Counter(str(n.get("node_type") or "?")
                                  for n in all_nodes)
    by_edge = collections.Counter(str(e.get("edge_type") or "?")
                                  for e in all_edges)

    # LE EPOCHE SONO NODI, non una colonna: `EpochNode` più gli archi che ci
    # portano (vedi `EPOCH_EDGES`, e la nota sull'arco che non esiste).
    # Contate per epoca, perché «12 unità senza epoca» è azione e «3 epoche» è
    # arredamento.
    epochs = {str(n.get("id")): _label(n) for n in all_nodes
              if str(n.get("node_type") or "") == EPOCH_NODE_TYPE}
    in_epoch: Dict[str, Dict[str, set]] = {
        eid: {"first": set(), "surviving": set()} for eid in epochs}
    dated: set = set()
    for edge in all_edges:
        kind = str(edge.get("edge_type") or "")
        if kind not in EPOCH_EDGES:
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if target not in in_epoch:
            continue
        bucket = "first" if kind == "has_first_epoch" else "surviving"
        in_epoch[target][bucket].add(source)
        dated.add(source)

    connected: set = set()
    for edge in all_edges:
        connected.add(str(edge.get("source") or ""))
        connected.add(str(edge.get("target") or ""))

    authors = collections.Counter()
    for node in all_nodes:
        who = _data(node).get("created_by")
        if who:
            authors[str(who)] += 1

    return {
        "nodes": len(all_nodes),
        "edges": len(all_edges),
        "units": len(units),
        "by_node_type": dict(by_type.most_common()),
        "by_edge_type": dict(by_edge.most_common()),
        "by_epoch": [{"id": eid, "name": epochs[eid],
                      "first": len(members["first"]),
                      "surviving": len(members["surviving"])}
                     for eid, members in sorted(in_epoch.items())],
        "by_author": dict(authors.most_common()),
        "validation": _validation(all_nodes),
        "holes": _holes(units, connected, dated, bool(epochs)),
    }


def _validation(all_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Quanti campi dichiarano un autore, e quanti aspettano una firma."""
    counts = collections.Counter()
    for node in all_nodes:
        for field, mark in _authorship(node).items():
            who = str(mark.get("by") or HUMAN)
            counts[who] += 1
            if who == AI:
                counts["ai_validated" if mark.get("validated_by")
                       else "ai_waiting"] += 1
    return {"fields_declaring_an_author": counts[HUMAN] + counts[AI],
            "human": counts[HUMAN], "ai": counts[AI],
            "ai_validated": counts["ai_validated"],
            "ai_waiting": counts["ai_waiting"]}


def _holes(units: List[Dict[str, Any]], connected: set, dated: set,
           any_epoch: bool) -> Dict[str, Any]:
    """I lavori da fare. Ogni buco porta **gli id**, non solo il numero: un
    conteggio senza i nomi è una colpa senza un'azione."""
    alone = [str(n.get("id")) for n in units
             if str(n.get("id")) not in connected]
    undated = [str(n.get("id")) for n in units
               if str(n.get("id")) not in dated]
    return {
        "units_without_relations": {"count": len(alone), "ids": alone[:50]},
        # Se la stanza non ha NESSUNA epoca, «tutte le unità sono senza epoca»
        # è vero e inutile: il lavoro da fare è periodizzare, e si dice così.
        "units_without_epoch": {"count": len(undated), "ids": undated[:50],
                                "no_epochs_at_all": not any_epoch},
        # DICHIARATO, non taciuto: vedi la testa del modulo.
        "cannot_count": [
            "required fields left empty — `required` lives in the sheet "
            "definition (stratigraph-templates), which does not travel with "
            "the graph: this server would have to guess which fields a unit "
            "owes, and a guessed obligation is a fabricated one",
        ],
    }


# ── §2.5 · chi ha scritto qui ───────────────────────────────────────────────

def operators(document: Dict[str, Any]) -> Dict[str, Any]:
    """La directory degli operatori come **vista sull'autorialità**.

    In pyarchinit questa tabella si deduce da una colonna di testo scritta a
    mano (`schedatore`) e non è verificata da niente. Qui `created_by`, i
    timbri e gli orologi di campo ci sono già, e le identità sono ORCID che un
    token ha verificato: la funzione si guadagna senza scrivere un modello.

    Lo **strumento** viene da `data.origin`, se c'è. **Se non c'è si dice**:
    `tools_unknown` conta le unità che non lo dichiarano, invece di attribuirle
    a un attrezzo plausibile. Misurato sulla stanza di pyarchinit: 44 nodi su
    44 con `origin: "pyarchinit-mini"`; su una stanza scritta da StratiField:
    zero, perché il chatbot non lo scrive — ed è una richiesta, non un bug da
    coprire qui.
    """
    people: Dict[str, Dict[str, Any]] = {}

    def who(orcid: str) -> Dict[str, Any]:
        return people.setdefault(str(orcid), {
            "author": str(orcid), "created": 0, "modified": 0,
            "fields": 0, "first_at": None, "last_at": None,
            "tools": collections.Counter(), "tools_unknown": 0})

    def seen(entry: Dict[str, Any], at: Optional[str]) -> None:
        if not at:
            return
        at = str(at)
        if entry["first_at"] is None or at < entry["first_at"]:
            entry["first_at"] = at
        if entry["last_at"] is None or at > entry["last_at"]:
            entry["last_at"] = at

    for node in nodes(document):
        data = _data(node)
        origin = str(data.get("origin") or "")
        creator = data.get("created_by")
        if creator:
            entry = who(creator)
            entry["created"] += 1
            seen(entry, data.get("created_at"))
            if origin:
                entry["tools"][origin] += 1
            else:
                entry["tools_unknown"] += 1
        modifier = data.get("modified_by")
        if modifier:
            entry = who(modifier)
            entry["modified"] += 1
            seen(entry, data.get("modified_at"))
        clocks = data.get("field_clocks")
        if isinstance(clocks, dict):
            for mark in clocks.values():
                if not isinstance(mark, dict) or not mark.get("by"):
                    continue
                entry = who(mark["by"])
                entry["fields"] += 1
                seen(entry, mark.get("ts"))

    listing = []
    for entry in people.values():
        tools = dict(entry.pop("tools").most_common())
        listing.append(dict(entry, tools=tools))
    listing.sort(key=lambda e: (-(e["created"] + e["modified"]), e["author"]))
    return {"operators": listing, "counts": {"people": len(listing)}}
