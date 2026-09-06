"""Cosa sa dire una stanza di sé — le domande di chi si siede.

Una stanza non è un database di siti: è un tavolo di lavoro. Le schermate che
contano un patrimonio («Total Sites 2 · Total US 54») rispondono bene a una
domanda che chi entra in una stanza non sta facendo.

Le domande vere sono quattro, e questo modulo le risponde:

* **chi c'è dentro adesso** — `who_is_here`
* **cosa aspetta me** — `waiting_for`
* **com'è fatto quello che abbiamo** — `statistics`
* **chi ha scritto qui** — `operators`

* **cosa è cambiato da quando non guardavo** — `changes_since`

La quinta è arrivata il 3 ottobre, e per tre settimane **non era qui** con una
ragione che vale la pena tenere scritta: il registro delle operazioni viveva
solo in memoria, tagliato a 512, e una stanza dimenticata e ricostruita — cioè
un riavvio — lo perdeva. Costruire quella risposta su quel registro avrebbe
promesso una memoria che non c'era.

Dal 28 settembre il registro **dura** (`app/oplog.py`, diecimila operazioni per
stanza, su disco accanto allo snapshot) e la domanda si può fare. Con il suo
confine detto: `readable_from` è fin dove la stanza sa guardare indietro, e una
risposta che non ci arriva **lo dichiara** invece di sembrare completa.

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
        "holes": _holes(all_nodes, units, connected, dated, bool(epochs)),
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


def _holes(all_nodes: List[Dict[str, Any]], units: List[Dict[str, Any]],
           connected: set, dated: set, any_epoch: bool) -> Dict[str, Any]:
    """I lavori da fare. Ogni buco porta **gli id**, non solo il numero: un
    conteggio senza i nomi è una colpa senza un'azione."""
    alone = [str(n.get("id")) for n in units
             if str(n.get("id")) not in connected]
    undated = [str(n.get("id")) for n in units
               if str(n.get("id")) not in dated]
    # IL BUCO DI `by_author`, e sta QUI e non nella pagina che lo mostra. La
    # legge di questa risposta è che il numero e il suo buco viaggiano insieme;
    # un cruscotto che calcolasse «nodi meno la somma degli autori» starebbe
    # inventando un numero che nessuno può ricondurre a un conteggio sul grafo.
    #
    # Contato su TUTTI i nodi e non solo sulle unità, perché è la stessa base su
    # cui è contato `by_author`: due numeri che si guardano devono guardare la
    # stessa cosa, sennò la sottrazione a mano non torna.
    unsigned = [str(n.get("id")) for n in all_nodes
                if not _data(n).get("created_by")]
    return {
        "nodes_without_author": {"count": len(unsigned), "ids": unsigned[:50]},
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


# ── §2.6 · cosa è cambiato da quando non guardavo ───────────────────────────

#: I verbi che il registro può contenere, con la parola che una persona legge.
#: Dichiarati invece che dedotti dal nome: `update_field` letto così com'è dice
#: a un programmatore cosa è successo e a chi scava non dice niente.
VERBS = {
    "add_node": "creata",
    "update_field": "corretta",
    "add_edge": "collegata",
    "remove_edge": "scollegata",
    "remove_node": "rimossa",
}


def _touched(op: Dict[str, Any]) -> Optional[str]:
    """Su quale nodo ha agito questa operazione.

    Tre grafie perché tre sono i verbi: `add_node` porta `id`, `update_field`
    porta `node_id`, un arco porta `source` e `target` e quello che conta per
    chi legge è **da dove parte** — «la 12 è stata collegata» è la frase che
    qualcuno rilegge, non «esiste un arco 12→7».
    """
    for key in ("node_id", "id", "source"):
        value = op.get(key)
        if value:
            return str(value)
    return None


def changes_since(room: Any, since: Optional[str], *,
                  limit: int = 200) -> Dict[str, Any]:
    """Cosa è successo in questa stanza dopo `since`, **letto e non rigiocato**.

    ════════════════════════════════════════════════════════════════════════════
    ## LA DOMANDA CHE FINO A TRE NOTTI FA NON SI POTEVA FARE

    La testa di questo modulo diceva che questa quinta domanda non stava qui, e
    la ragione era giusta: il registro viveva in memoria, tagliato a 512, e si
    azzerava a ogni riavvio. Costruirci sopra un cursore avrebbe promesso una
    memoria che non c'era.

    Dal 28 settembre il registro **dura** (`app/oplog.py`): diecimila operazioni
    per stanza, su disco, accanto allo snapshot. Quindi la domanda si può fare —
    e si fa **sul registro** e non sul documento, che è l'altra metà della
    ragione: il documento dice com'è adesso, non cosa è successo.

    ## E IL LIMITE SI MOSTRA, NON SI NASCONDE

    `readable_from` è il `ts` più vecchio che il registro tiene ancora. Un
    cursore più vecchio di quello **non si può servire per intero**, e la
    risposta lo dice invece di dare un elenco corto che sembra completo:

        complete: false      →  «oltre questo punto non so più»

    Una dashboard che mostrasse «tre unità nuove» senza dire che oltre quel
    punto non sa più starebbe dichiarando una memoria che non ha, e sarebbe la
    stessa famiglia di difetti di `ws.py:387`.

    ## OGNI NUMERO SI PUÒ RIFARE A MANO

    `operations` è `len(read_since(since))`; `by_author` e `by_verb` sono due
    `Counter` sulle stesse righe; `units` raggruppa per nodo toccato. Chi vuole
    controllare apre il registro della stanza e conta le righe con `ts > since`
    — non c'è nessun indice, nessuna cache e nessun numero che venga da
    un'altra parte.
    """
    reaches = room.replay_reaches()
    compacted = getattr(room, "compacted_upto", None)
    replayable = reaches
    if compacted and (not replayable or compacted > replayable):
        replayable = compacted

    fuori: Dict[str, Any] = {
        "since": since or None,
        # LE DUE PAROLE, le stesse del filo: una si può applicare, l'altra si
        # può solo raccontare, e questa funzione racconta.
        "readable_from": reaches,
        "replayable_from": replayable,
        "durable": room.journal is not None,
        "operations": 0,
        "by_author": {},
        "by_verb": {},
        "units": [],
        "oldest": None,
        "newest": None,
        "complete": True,
        #: PERCHÉ non è completa: `pruned` (buttate) o `began` (mai avute).
        "horizon": None,
        "truncated": False,
    }
    if not since:
        # Nessun cursore: non è una domanda malformata, è la prima volta che
        # qualcuno guarda. Si risponde con i confini, che è l'unica cosa vera —
        # «tutto» sarebbe il documento, e il documento non è una notizia.
        fuori["complete"] = False
        return fuori

    # IL CONFINE, PRIMA DI CONTARE. Se il cursore è più vecchio di quel che il
    # registro tiene, quello che si conta è un pezzo — e dirlo dopo aver dato i
    # numeri sarebbe darli e poi ritirarli.
    #
    # E **due ragioni diverse**, perché sono due frasi diverse per chi legge:
    #
    #   `pruned`  il registro è al suo tetto: righe più vecchie ci sono state e
    #             sono state buttate. «Non te lo posso più dire.»
    #   `began`   il registro comincia lì ed è sotto il tetto: non ha buttato
    #             niente. Prima di quel punto questo server **non sa** se sia
    #             successo qualcosa — la stanza può essere più vecchia del suo
    #             registro (i registri esistono dal 28 settembre).
    #
    # Appiattirle in un «incompleto» direbbe «ho dimenticato» anche quando la
    # verità è «non ho mai saputo», e sono due cose che si riparano in modi
    # diversi.
    if reaches and since < reaches:
        fuori["complete"] = False
        pieno = (room.journal is not None
                 and len(room.journal) >= room.journal.keep)
        fuori["horizon"] = "pruned" if pieno else "began"

    righe = room.read_since(since)
    autori = collections.Counter()
    verbi = collections.Counter()
    per_nodo: Dict[str, Dict[str, Any]] = {}
    for op in righe:
        ts = str(op.get("ts") or "")
        if ts:
            if fuori["oldest"] is None or ts < fuori["oldest"]:
                fuori["oldest"] = ts
            if fuori["newest"] is None or ts > fuori["newest"]:
                fuori["newest"] = ts
        chi = str(op.get("author") or "")
        if chi:
            autori[chi] += 1
        verbo = str(op.get("op") or "?")
        verbi[verbo] += 1
        nodo = _touched(op)
        if not nodo:
            continue
        riga = per_nodo.setdefault(nodo, {
            "id": nodo, "operations": 0, "verbs": collections.Counter(),
            "authors": set(), "first_at": ts or None, "last_at": ts or None})
        riga["operations"] += 1
        riga["verbs"][verbo] += 1
        if chi:
            riga["authors"].add(chi)
        if ts:
            if not riga["first_at"] or ts < riga["first_at"]:
                riga["first_at"] = ts
            if not riga["last_at"] or ts > riga["last_at"]:
                riga["last_at"] = ts

    fuori["operations"] = len(righe)
    fuori["by_author"] = dict(autori.most_common())
    fuori["by_verb"] = dict(verbi.most_common())

    unita = sorted(per_nodo.values(), key=lambda r: r["last_at"] or "",
                   reverse=True)
    fuori["truncated"] = len(unita) > limit
    fuori["units"] = [{
        "id": riga["id"],
        "operations": riga["operations"],
        # LE PAROLE E NON I VERBI DEL PROTOCOLLO: «creata, corretta ×3» è quello
        # che qualcuno rilegge; `add_node, update_field` è quello che qualcuno
        # deve tradurre. Un verbo che questo elenco non conosce passa com'è —
        # inventargli un nome sarebbe peggio del gergo.
        "what": [{"verb": v, "said": VERBS.get(v, v), "times": n}
                 for v, n in riga["verbs"].most_common()],
        "authors": sorted(riga["authors"]),
        "first_at": riga["first_at"],
        "last_at": riga["last_at"],
    } for riga in unita[:limit]]
    return fuori
