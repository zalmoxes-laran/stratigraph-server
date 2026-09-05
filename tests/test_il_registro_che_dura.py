"""Il registro dura, e un cursore troppo vecchio viene RIFIUTATO.

════════════════════════════════════════════════════════════════════════════════
## COSA C'ERA GIÀ

Il join accetta `?since=`, `replay_since()` è corretta, `host_info` annuncia
`compacted_upto` con la regola scritta accanto. Mancava una cosa sola: il
registro moriva col processo. Misurato sulla dev-stack **prima** di scrivere
una riga:

    scritte 6 operazioni · cursore 2026-09-28T10:05:00Z
    rientro con since=…10:02:00Z  → 3 operazioni riprodotte
    docker restart em-dev-server
    LO STESSO rientro             → 0 operazioni riprodotte

## E COSA IL REGISTRO PORTA CON SÉ

Un registro che dura è anche un'arma. `em.compact` butta gli archi rimossi il
cui tombstone è più vecchio del watermark: **un vecchio `add_edge` rigiocato
dopo quella pulizia resuscita un arco cancellato**, perché il tombstone che
l'avrebbe rifiutato non c'è più. Il grafo tornerebbe a dire che due unità sono
in rapporto quando qualcuno aveva stabilito che non lo sono — e non è lavoro
perso, è un'affermazione falsa in un dato archeologico.

La regola che lo impedisce **esisteva già ed era annunciata**. Annunciata, e il
client decideva. Da stanotte il server **rifiuta**, ed è la differenza fra un
contratto e una speranza — la stessa lezione di `ws.py:387`, incontrata stavolta
*prima* che costasse qualcosa.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402

from app import keeping                                    # noqa: E402
from app import oplog                                      # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.main import app                                   # noqa: E402
from app.rooms import RoomRegistry                         # noqa: E402
from app.store import DirectorySnapshotStore, InMemorySnapshotStore  # noqa: E402
from app.wire import WIRE                                  # noqa: E402

ROOM = "registro"
ANNA = "0000-0002-1825-0097"
T0 = "2026-09-28T09:00:00Z"
T1 = "2026-09-28T10:00:00Z"
T2 = "2026-09-28T11:00:00Z"
T3 = "2026-09-28T12:00:00Z"


def _document():
    return {"header": {"format": "em.json", "version": "1.0"},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [{"id": "US1", "node_type": "US",
                                         "name": "US1",
                                         "data": {"created_at": T0}},
                                        {"id": "US2", "node_type": "US",
                                         "name": "US2",
                                         "data": {"created_at": T0}}],
                              "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture
def durevole(tmp_path, monkeypatch):
    """Un relay con lo store SU DISCO, che è la condizione perché il registro
    esista: `journal_for` non ne fa uno quando lo store è in memoria."""
    store = DirectorySnapshotStore(str(tmp_path))
    store.put(ROOM, _document())
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "KEEPER", keeping.Keeper(
        ws_module._keep, after_ops=10_000, after_quiet=10_000))
    return registry


@pytest.fixture
def volatile(monkeypatch):
    """Lo stesso relay con lo store in memoria — cioè senza registro."""
    store = InMemorySnapshotStore()
    store.put(ROOM, _document())
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "KEEPER", keeping.Keeper(
        ws_module._keep, after_ops=10_000, after_quiet=10_000))
    return registry


@pytest.fixture
def client():
    return TestClient(app)


def _join(client, since=None):
    percorso = f"/v1/rooms/{ROOM}/ws" + (f"?since={since}" if since else "")
    return client.websocket_connect(percorso)


def _drain_join(socket):
    host = socket.receive_json()
    socket.receive_json()                      # snapshot
    socket.receive_json()                      # presence
    return host["payload"]


def _attendi(socket, tipo, quanti=12):
    for _ in range(quanti):
        message = socket.receive_json()
        if message["type"] == tipo:
            return message
    raise AssertionError(f"nessun «{tipo}»")


def _riprodotte(socket, quante):
    """Le operazioni che arrivano dopo il join. `quante` è quante aspettarne:
    zero significa «e nient'altro», e per saperlo si manda una domanda che ha
    una risposta e si guarda cosa arriva prima."""
    ricevute = []
    for _ in range(quante):
        ricevute.append(_attendi(socket, "op")["payload"])
    socket.send_json({"v": WIRE, "type": "request_snapshot", "source": "test",
                      "payload": {}})
    while True:
        message = socket.receive_json()
        if message["type"] == "snapshot":
            return ricevute
        if message["type"] == "op":
            ricevute.append(message["payload"])


def _scrivi(socket, quante, primo=600):
    for i in range(quante):
        socket.send_json({"v": WIRE, "type": "op", "source": "test", "payload": {
            "op": "add_node", "id": f"US{primo+i}", "ts": f"2026-09-28T10:{i:02d}:00Z",
            "node": {"node_type": "US", "name": f"US {primo+i}"}}})
        assert _attendi(socket, "op_result")["payload"]["applied"] is True


# ═══ 1 · il registro dura ════════════════════════════════════════════════════

def test_senza_registro_un_riavvio_porta_via_la_riproduzione(client, volatile):
    """L'EFFETTO DELLA ROTTURA, rimisurato: store in memoria, nessun registro.

    `forget` è quello che fa un riavvio — lo dice la docstring del metodo: «the
    next join rebuilds it from the store»."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 4)
    with _join(client, since="2026-09-28T10:01:00Z") as b:
        assert len(_riprodotte(b, 2)) == 2, "prima del riavvio si riproduce"

    volatile.forget(ROOM)                      # ← il riavvio
    with _join(client, since="2026-09-28T10:01:00Z") as c:
        piano = _drain_join(c)["replay"]
        assert _riprodotte(c, 0) == [], "il registro è morto col processo"
        assert piano["granted"] is True, (
            "e nessuno lo ha rifiutato: semplicemente non c'era niente")


def test_col_registro_lo_stesso_giro_riproduce(client, durevole):
    """Identico riga per riga, tranne lo store."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 4)

    durevole.forget(ROOM)                      # ← il riavvio
    with _join(client, since="2026-09-28T10:01:00Z") as c:
        piano = _drain_join(c)["replay"]
        assert piano["granted"] is True
        arrivate = _riprodotte(c, 2)
        assert [op["id"] for op in arrivate] == ["US602", "US603"]


def test_il_registro_e_un_file_ACCANTO_e_non_dentro(tmp_path, client, durevole):
    """Non dentro l'`em.json`: quello è un formato che l'ecosistema intero sa
    leggere, e infilarci una coda di operazioni ne farebbe un formato nostro."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 2)
    registro = tmp_path / f"{ROOM}.oplog.jsonl"
    assert registro.is_file()
    documento = json.loads((tmp_path / f"{ROOM}.em.json").read_text(encoding="utf-8"))
    assert "oplog" not in json.dumps(documento)
    righe = [json.loads(l) for l in registro.read_text(encoding="utf-8").splitlines()]
    assert all(set(r) == {"at", "op"} for r in righe)
    # `at` è l'istante del SERVER, `ts` quello del client: due orologi, due usi
    assert righe[0]["at"] != righe[0]["op"]["ts"]


def test_una_morte_sporca_lascia_un_registro_leggibile(tmp_path, client, durevole):
    """Ammazzato a metà riga: quello che resta si legge, e la riga monca si
    conta invece di portare via il resto."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 3)
    registro = tmp_path / f"{ROOM}.oplog.jsonl"
    with open(registro, "a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-09-28T10:9')      # il nodo muore qui

    diario = oplog.Journal(str(registro))
    letto = diario.read()
    assert [op["id"] for op in letto] == ["US600", "US601", "US602"]
    assert diario.unreadable == 1, "la riga monca è contata, non nascosta"


# ═══ 2 · il rifiuto, e la resurrezione ═══════════════════════════════════════

def _arco_creato_e_rimosso(client, durevole):
    """Costruisce il caso: un arco creato a T1, rimosso a T2, la compattazione
    che ne butta il tombstone. Torna il cursore da cui un replay lo
    resusciterebbe."""
    with _join(client) as a:
        _drain_join(a)
        a.send_json({"v": WIRE, "type": "op", "source": "test", "payload": {
            "op": "add_edge", "id": "US2__is_after__US1", "edge_type": "is_after",
            "source": "US2", "target": "US1", "ts": T1}})
        assert _attendi(a, "op_result")["payload"]["applied"] is True
        a.send_json({"v": WIRE, "type": "op", "source": "test", "payload": {
            "op": "remove_edge", "id": "US2__is_after__US1", "ts": T2}})
        assert _attendi(a, "op_result")["payload"]["applied"] is True
        # …e la compattazione, che butta il tombstone: serve un watermark, cioè
        # un ack, e poi il salvataggio che compatta
        a.send_json({"v": WIRE, "type": "ack", "source": "test",
                     "payload": {"ts": T3}})
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        _attendi(a, "snapshot_written")

    room = durevole.peek(ROOM)
    assert room.compacted_upto == T3
    archi = room.document["graphs"][ROOM]["edges"]
    assert archi == [], "il tombstone è stato buttato dalla compattazione"
    return room


def test_IL_CANCELLO_un_vecchio_add_edge_non_resuscita_un_arco(client, durevole):
    """IL CANCELLO PIÙ IMPORTANTE DELLA NOTTE.

    L'arco è stato creato, rimosso, e la compattazione ha buttato il tombstone.
    Nel registro l'`add_edge` di T1 c'è ancora. Un client che rientra con un
    cursore anteriore a T1 lo rigiocherebbe — e senza tombstone l'arco
    tornerebbe.

    Il server **rifiuta**, e dice perché."""
    _arco_creato_e_rimosso(client, durevole)
    durevole.forget(ROOM)                       # il riavvio, per buona misura

    with _join(client, since=T0) as b:
        piano = _drain_join(b)["replay"]
        assert piano["granted"] is False
        assert piano["reason"] == ws_module.REPLAY_UNSAFE
        assert piano["compacted_upto"] == T3
        assert "already settled" in piano["detail"]
        assert _riprodotte(b, 0) == [], "nessuna operazione rigiocata"

    room = durevole.peek(ROOM)
    assert room.document["graphs"][ROOM]["edges"] == [], "l'arco NON è tornato"


def test_e_senza_il_rifiuto_larco_RISORGE(client, durevole, monkeypatch):
    """L'EFFETTO DELLA ROTTURA, sullo stesso identico giro.

    Si rimette il comportamento di prima — annunciare e lasciar decidere il
    client — e si guarda il grafo: l'arco che qualcuno aveva cancellato torna a
    dire che due unità sono in rapporto.

    Se un domani il rifiuto sparisse, il test qui sopra diventerebbe rosso;
    questo mostra **cosa** diventerebbe vero al suo posto."""
    _arco_creato_e_rimosso(client, durevole)
    durevole.forget(ROOM)

    monkeypatch.setattr(ws_module, "_replay_plan",
                        lambda room, since: {"granted": bool(since),
                                             "since": since})
    with _join(client, since=T0) as b:
        _drain_join(b)
        rigiocate = _riprodotte(b, 2)
        assert any(op.get("op") == "add_edge" for op in rigiocate)
        # il client rigioca ciò che ha ricevuto: qui lo fa il relay per lui
        b.send_json({"v": WIRE, "type": "op", "source": "test", "payload": {
            "op": "add_edge", "id": "US2__is_after__US1", "edge_type": "is_after",
            "source": "US2", "target": "US1", "ts": T1}})
        assert _attendi(b, "op_result")["payload"]["applied"] is True

    archi = durevole.peek(ROOM).document["graphs"][ROOM]["edges"]
    assert [e["id"] for e in archi] == ["US2__is_after__US1"], (
        "senza il rifiuto l'arco cancellato è tornato — ed è il punto")


def test_un_client_maleducato_non_ottiene_il_replay(client, durevole):
    """§3 · il rifiuto è del SERVER, non del client.

    Questo client legge `compacted_upto` in `host_info`, lo ignora, e chiede lo
    stesso. Non ottiene niente di diverso: è tutta la differenza fra una regola
    e una speranza."""
    _arco_creato_e_rimosso(client, durevole)
    with _join(client, since=T0) as maleducato:
        annuncio = _drain_join(maleducato)
        assert annuncio["gc_watermark"] == T3, "il server lo aveva detto"
        assert _riprodotte(maleducato, 0) == [], "e lo applica lui"


def test_un_cursore_piu_vecchio_del_registro_riceve_una_ragione(client, durevole,
                                                                monkeypatch):
    """L'altro rifiuto: il registro non arriva così indietro.

    Una riproduzione parziale sarebbe **peggio del niente** — il client
    crederebbe di essere allineato. Qui il registro viene potato a due
    operazioni, e un cursore precedente non le riceve."""
    monkeypatch.setattr(oplog, "KEEP_OPS", 2)
    monkeypatch.setattr(oplog, "SLACK", 0)
    durevole.forget(ROOM)
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 5)

    with _join(client, since=T0) as b:
        piano = _drain_join(b)["replay"]
        assert piano["granted"] is False
        assert piano["reason"] == ws_module.REPLAY_INCOMPLETE
        assert piano["reaches_back_to"] > T0
        assert "believing you are caught up" in piano["detail"]
        assert _riprodotte(b, 0) == []


def test_senza_cursore_non_e_un_rifiuto(client, durevole):
    """Non c'è niente da riprodurre e non c'è niente di cui lamentarsi: lo
    snapshot appena mandato È il documento."""
    with _join(client) as a:
        piano = _drain_join(a)["replay"]
        assert piano["granted"] is False and piano["reason"] == "no cursor"
        assert "whole document" in piano["detail"]


# ═══ 3 · la guardia sopravvive con il registro ═══════════════════════════════

def test_il_punto_di_compattazione_sopravvive_al_riavvio(client, durevole,
                                                         tmp_path):
    """SENZA QUESTO LA GUARDIA EVAPORA PROPRIO QUANDO SERVE.

    `Room.compacted_upto` vive in memoria e ripartirebbe da `None`: dopo un
    riavvio il documento sul disco è già compattato, e il rifiuto non
    scatterebbe. Il punto sta nel registro, che è il file la cui vita coincide
    con quella della riproduzione."""
    _arco_creato_e_rimosso(client, durevole)
    durevole.forget(ROOM)                       # ← il riavvio

    room = asyncio.run(durevole.get(ROOM))
    assert room.compacted_upto == T3, "la guardia è ripartita da zero"
    assert oplog.Journal(str(tmp_path / f"{ROOM}.oplog.jsonl")).compacted_upto() == T3


def test_la_potatura_non_pota_il_segno_di_compattazione(tmp_path):
    """Un segno che cade fuori dalla finestra sarebbe una guardia che sparisce
    da sola, in silenzio, su una stanza attiva."""
    diario = oplog.Journal(str(tmp_path / "reg.jsonl"), keep=3, slack=0)
    diario.mark_compacted(T1, at=T1)
    for i in range(10):
        diario.append({"op": "add_node", "id": f"U{i}",
                       "ts": f"2026-09-28T10:{i:02d}:00Z"}, at=T2)
    assert diario.compacted_upto() == T1, "il segno è stato potato via"
    assert len(diario.read()) <= 3


# ═══ 4 · la ritenzione non dipende dal watermark ═════════════════════════════

def test_il_registro_tiene_anche_dove_nessuno_manda_ack(client, durevole,
                                                        tmp_path):
    """`gc_watermark()` misura la consegna e non l'applicazione, e **un client
    su tre manda `ack`** — misurato il 27 settembre. Un registro la cui durata
    dipendesse da quel numero si accorcerebbe da solo per ragioni che nessuno
    controlla.

    Qui nessuno conferma niente. La stanza compatta lo stesso — fino
    all'**istante di ingresso** dei membri, perché al join il server gliene
    assegna uno (`member.watermark = room.last_op_at or now_iso()`, misurato il
    26 settembre) — e il registro tiene comunque tutto, perché la sua durata
    non guarda quel numero.

    Scritto così dopo che la prima versione ha asserito «senza ack non si
    compatta» ed è diventata rossa: non è vero, e il test lo diceva."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 6)
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        _attendi(a, "snapshot_written")

    room = durevole.peek(ROOM)
    assert room.compacted_upto is not None, (
        "compatta comunque, fino all'istante di ingresso")
    assert room.compacted_upto not in {f"2026-09-28T10:{i:02d}:00Z"
                                       for i in range(6)}, (
        "e non fino a un'operazione: nessuno ha confermato niente")
    diario = oplog.Journal(str(tmp_path / f"{ROOM}.oplog.jsonl"))
    assert len(diario.read()) == 6, "il registro tiene tutto lo stesso"


def test_la_ritenzione_e_una_politica_di_oplog_e_di_nessun_altro():
    """Il cancello sul sorgente: se un domani la potatura guardasse il
    watermark, questo diventa rosso.

    Sul CODICE e non sulla prosa — `_without_prose` di `test_relay.py`, la
    stessa che stanotte è servita a `test_7b`. Questo file *spiega* nel proprio
    docstring perché non guarda il watermark, e un controllo sul testo grezzo
    avrebbe scambiato la spiegazione per la cosa spiegata: è la terza volta in
    una notte, e la quinta in questo ecosistema."""
    from tests.test_relay import _without_prose

    codice = _without_prose(pathlib.Path(oplog.__file__))
    for vietato in ("gc_watermark", "watermark"):
        assert vietato not in codice, (
            f"la ritenzione guarda {vietato}: è un numero che nessuno controlla")


# ═══ 4bis · le due parole ════════════════════════════════════════════════════

def test_rigiocabile_e_leggibile_sono_due_cose(client, durevole):
    """§8 · la ritenzione è più lunga della finestra di compattazione, di
    proposito: dipendono da cose diverse. La parte di registro più vecchia di
    `compacted_upto` **non serve a rigiocare** — rigiocarla è ciò che
    resusciterebbe un arco — e serve a **leggere**: chi ha fatto cosa, quando,
    in che ordine.

    Due nomi perché sono due cose. Chiamarle allo stesso modo sarebbe l'invito
    a rigiocare la seconda."""
    _arco_creato_e_rimosso(client, durevole)
    with _join(client, since=T3) as a:
        piano = _drain_join(a)["replay"]
        assert piano["readable_from"] == T1, (
            "il registro si legge da quando comincia")
        assert piano["replayable_from"] == T3, (
            "…ma si rigioca solo da dopo la compattazione")
        assert piano["readable_from"] < piano["replayable_from"], (
            "se coincidessero non ci sarebbero due parole da usare")


def test_le_due_parole_ci_sono_anche_nel_rifiuto(client, durevole):
    """Chi viene rifiutato è **proprio** chi ha bisogno di sapere fin dove può
    leggere: gli si nega di rigiocare, non di guardare."""
    _arco_creato_e_rimosso(client, durevole)
    with _join(client, since=T0) as a:
        piano = _drain_join(a)["replay"]
        assert piano["granted"] is False
        assert piano["readable_from"] and piano["replayable_from"]


# ═══ 5 · la salute lo dice ═══════════════════════════════════════════════════

def test_health_dice_se_la_storia_dura(client, durevole):
    """Un operatore deve poter chiedere «quello che questo nodo tiene, lo tiene
    davvero?» senza leggere il deployment."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 3)
    salute = client.get("/health").json()["oplog"]
    assert salute["durable"] is True
    assert salute["keep_per_room"] == oplog.KEEP_OPS
    assert salute["rooms_with_a_log"] == 1 and salute["entries"] == 3
    assert salute["shortest_memory"]["room"] == ROOM
    assert salute["shortest_memory"]["reaches_back_to"] == "2026-09-28T10:00:00Z"


def test_health_lo_dice_anche_quando_NON_dura(client, volatile):
    """Assente per configurazione non è un guasto: è una funzione spenta."""
    with _join(client) as a:
        _drain_join(a)
        _scrivi(a, 2)
    salute = client.get("/health").json()["oplog"]
    assert salute["durable"] is False
    assert "dies with the process" in salute["describe"]
    assert salute["rooms_with_a_log"] == 0


# ═══ 6 · e quando non c'è un posto dove tenerlo, lo dice ═════════════════════

def test_senza_una_directory_non_ce_registro_e_si_dice():
    """Un registro che finge di durare sarebbe peggio di nessun registro."""
    assert oplog.journal_for(InMemorySnapshotStore(), ROOM) is None
    assert "memory only" in oplog.describe(None)
    assert "dies with the process" in oplog.describe(None)
