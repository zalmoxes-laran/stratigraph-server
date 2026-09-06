"""La conversazione di una stanza: dura, si ordina, e non si pubblica.

════════════════════════════════════════════════════════════════════════════════
## LE TRE COSE CHE QUESTO FILE PROVA, E PERCHÉ SONO TRE

**1 · Il confine della pubblicazione.** Un messaggio scritto fra due persone che
finisce dentro uno studio pubblicato senza che nessuno l'abbia deciso non è un
difetto tecnico: sono le parole di un collega messe in una pubblicazione. Il
confine esiste già — `contract.document_view()` scarta i nodi volatili e ogni
arco che li tocca — e qui si prova nei due sensi: il messaggio non passa, e se
qualcuno gli togliesse il marcatore passerebbe.

**2 · L'ordine viene dagli orologi, non dal registro.** Il 26 settembre la
difficoltà dichiarata era il riavvio. La prova qui è la stessa cosa in
laboratorio: si dimentica la stanza, si ricostruisce dallo store, e la
conversazione torna nello stesso ordine — con il registro **buttato**, perché
una conversazione non ha la finestra che il registro ha.

**3 · L'autore è quello del token.** Costruito apposta: un client che dichiara
un altro autore, e il grafo che registra l'identità del token.

E una quarta che nessuno aveva chiesto: **la cancellazione**, che è due atti, e
il primo è quello che si dimentica.
"""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402
from s3dgraphy.contract import document_view               # noqa: E402

from app import conversation as chat                       # noqa: E402
from app import keeping                                    # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.main import app                                   # noqa: E402
from app.rooms import RoomRegistry                         # noqa: E402
from app.store import DirectorySnapshotStore               # noqa: E402
from app.wire import WIRE                                  # noqa: E402

ROOM = "scavo"
ANNA = "0000-0002-1825-0097"
BRUNO = "0000-0001-2345-6789"


def _document():
    return {"header": {"format": "em.json", "version": "1.0"},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [{"id": "US12", "node_type": "US",
                                         "name": "US 12", "data": {}}],
                              "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture
def stanza(tmp_path, monkeypatch):
    """Un relay con lo store SU DISCO: è la condizione perché ci sia qualcosa
    da riaprire dopo aver dimenticato la stanza."""
    store = DirectorySnapshotStore(str(tmp_path))
    store.put(ROOM, _document())
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "KEEPER", keeping.Keeper(
        ws_module._keep, after_ops=1, after_quiet=10_000))
    return registry, store


@pytest.fixture
def client():
    return TestClient(app)


def _drain_join(socket):
    socket.receive_json()          # host_info
    socket.receive_json()          # snapshot
    socket.receive_json()          # presence


def _attendi(socket, tipo, quanti=12):
    for _ in range(quanti):
        message = socket.receive_json()
        if message["type"] == tipo:
            return message
    raise AssertionError(f"nessun «{tipo}»")


def _dì(socket, testo, node_id, ts, author=None):
    op = chat.message_op(testo, node_id=node_id, author=author)
    op["ts"] = ts
    socket.send_json({"v": WIRE, "type": "op", "source": "test", "payload": op})
    return _attendi(socket, "op_result")["payload"]


# ═══ 1 · IL CONFINE DELLA PUBBLICAZIONE ══════════════════════════════════════

def _con_un_messaggio():
    doc = _document()
    section = doc["graphs"][ROOM]
    section["nodes"].append({
        "id": "m1", "node_type": chat.NODE_TYPE, "name": "",
        "data": {chat.SAID: "la 12 taglia la 14, guarda la sezione",
                 chat.VOLATILE_KEY: chat.INJECTOR,
                 "created_at": "2026-09-06T10:00:00Z", "created_by": ANNA}})
    section["edges"].append({"id": "e1", "edge_type": "has_paradata",
                             "source": "m1", "target": "US12"})
    return doc, section


def test_UN_MESSAGGIO_NON_ATTRAVERSA_LA_PUBBLICAZIONE():
    """E nemmeno l'arco che lo lega all'unità di cui parla."""
    _doc, section = _con_un_messaggio()
    pubblicato = document_view(section)
    assert [n["id"] for n in pubblicato["nodes"]] == ["US12"]
    assert pubblicato["edges"] == [], "l'arco incidente non passa nemmeno lui"


def test_E_SE_QUALCUNO_TOGLIESSE_IL_MARCATORE_passerebbe():
    """La prova che il confine è quel marcatore e non un'intenzione.

    È anche la ragione per cui la promozione a paradato NON è `bake`: smarcare
    lascia un `UnknownNode` con dentro una frase, e un documento pubblicato lo
    conterrebbe così.
    """
    _doc, section = _con_un_messaggio()
    del section["nodes"][1]["data"][chat.VOLATILE_KEY]
    pubblicato = document_view(section)
    assert [n["id"] for n in pubblicato["nodes"]] == ["US12", "m1"]
    passato = pubblicato["nodes"][1]
    assert passato["node_type"] == "UnknownNode"
    assert chat.SAID in passato["data"], \
        "…e la frase viaggerebbe dentro un nodo senza tipo"


# ═══ 2 · L'ORDINE SOPRAVVIVE A UN RIAVVIO ════════════════════════════════════

def test_LA_CONVERSAZIONE_SOPRAVVIVE_a_una_stanza_dimenticata(client, stanza):
    """Scrivi, dimentica la stanza, riaprila dallo store: stesso ordine.

    `registry.forget()` è ciò che un riavvio fa a questo processo — la stanza
    esce dalla memoria e la successiva `get()` la ricostruisce dal disco. E il
    registro delle operazioni viene **buttato** prima di rileggere, perché
    l'ordine non deve venire da lì: una conversazione non ha la finestra di
    diecimila operazioni che il registro ha.
    """
    registry, _store = stanza
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as socket:
        _drain_join(socket)
        assert _dì(socket, "la 12 taglia la 14", "m1",
                   "2026-09-06T10:00:00Z")["applied"] is True
        assert _dì(socket, "no, guarda la sezione", "m2",
                   "2026-09-06T10:01:00Z")["applied"] is True
        assert _dì(socket, "hai ragione", "m3",
                   "2026-09-06T10:02:00Z")["applied"] is True

    viva = registry.peek(ROOM)
    if viva is not None and viva.journal is not None:
        viva.journal.forget()                    # il registro, buttato apposta
    registry.forget(ROOM)
    assert registry.peek(ROOM) is None, "la stanza è uscita dalla memoria"

    import asyncio
    riaperta = asyncio.run(registry.get(ROOM))
    detti = chat.conversation(riaperta.document)
    assert [m["said"] for m in detti] == [
        "la 12 taglia la 14", "no, guarda la sezione", "hai ragione"]
    assert [m["at"] for m in detti] == [
        "2026-09-06T10:00:00Z", "2026-09-06T10:01:00Z", "2026-09-06T10:02:00Z"]


def test_E_L_ORDINE_VIENE_DAGLI_OROLOGI_non_dalla_lettura():
    """La prova che l'ordinamento è un fatto e non l'ordine del file.

    I nodi stanno nella sezione al contrario di come sono stati detti — che è
    quello che succede dopo una fusione — e la conversazione si legge lo stesso.
    """
    doc = _document()
    section = doc["graphs"][ROOM]
    for node_id, testo, at in (("m3", "terzo", "2026-09-06T10:02:00Z"),
                               ("m1", "primo", "2026-09-06T10:00:00Z"),
                               ("m2", "secondo", "2026-09-06T10:01:00Z")):
        section["nodes"].append({
            "id": node_id, "node_type": chat.NODE_TYPE, "name": "",
            "data": {chat.SAID: testo, chat.VOLATILE_KEY: chat.INJECTOR,
                     "created_at": at, "created_by": ANNA}})
    assert [m["said"] for m in chat.conversation(doc)] == [
        "primo", "secondo", "terzo"]


# ═══ 3 · L'AUTORE È QUELLO DEL TOKEN ═════════════════════════════════════════

def test_CHI_DICHIARA_UN_ALTRO_AUTORE_non_ci_riesce(monkeypatch, client, stanza):
    """Costruito apposta: il client dice di essere Bruno, il token dice Anna."""
    class Enforcing:
        enforcing = True

    monkeypatch.setattr(ws_module.authenticator, "settings", Enforcing())
    monkeypatch.setattr(ws_module.authenticator, "verify",
                        lambda token: {"orcid": ANNA, "name": "Anna"})
    registry, _store = stanza
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=whatever") as socket:
        _drain_join(socket)
        assert _dì(socket, "questa la firmo io", "m1",
                   "2026-09-06T10:00:00Z", author=BRUNO)["applied"] is True

    detti = chat.conversation(registry.peek(ROOM).document)
    assert detti[0]["by"] == ANNA, "il grafo registra l'identità del token"
    assert BRUNO not in str(registry.peek(ROOM).document), \
        "e il nome dichiarato non resta scritto da nessuna parte"


def test_E_LA_FUNZIONE_CHE_COSTRUISCE_UN_MESSAGGIO_non_offre_il_posto():
    """La seconda metà della stessa regola: non c'è dove scriverlo.

    `author` esiste nella firma solo per i test, che non hanno un relay — ed è
    la sola cosa che il relay butta prima di guardare il resto (`op.pop`, non
    un `op["author"] = …` che sovrascrive: in modo sviluppo non c'è identità e
    «sovrascrivi quando ce l'hai» lasciava passare un autore dichiarato
    proprio lì).
    """
    op = chat.message_op("una frase", node_id="m1")
    assert "author" not in op
    assert "ts" not in op, "e nemmeno l'istante: lo mette chi riceve"


# ═══ 4 · LA CANCELLAZIONE, CHE È DUE ATTI ════════════════════════════════════

def _detto_una_frase():
    from s3dgraphy import crdt as em
    doc = _document()
    section = doc["graphs"][ROOM]
    op = chat.message_op("una frase di cui mi pento", node_id="m1", author=ANNA)
    op["ts"] = "2026-09-06T10:00:00Z"
    em.apply_op_to_section(section, op)
    return doc, section


def test_SOLO_remove_node_lascia_la_frase_nello_snapshot():
    """Quello che sembra bastare, e non basta — misurato.

    Il nodo sparisce dalle viste e la frase resta scritta sul disco. È il caso
    che fa scattare la regola: chi si pente di una frase non vuole che sparisca
    dalla vista, vuole che smetta di esserci.
    """
    from s3dgraphy import crdt as em
    doc, section = _detto_una_frase()
    em.apply_op_to_section(section, {"op": "remove_node", "node_id": "m1",
                                     "author": ANNA,
                                     "ts": "2026-09-06T10:05:00Z"})
    nodo = section["nodes"][1]
    assert em.is_removed(nodo) is True
    assert nodo["data"][chat.SAID] == "una frase di cui mi pento"


def test_I_DUE_ATTI_INSIEME_la_tolgono():
    from s3dgraphy import crdt as em
    doc, section = _detto_una_frase()
    for op in chat.retract_ops("m1"):
        op.update(author=ANNA, ts="2026-09-06T10:05:00Z")
        assert em.apply_op_to_section(section, op).applied is True
    nodo = section["nodes"][1]
    assert nodo["data"][chat.SAID] == ""
    assert em.is_removed(nodo) is True
    detti = chat.conversation(doc)
    assert detti == [{"id": "m1", "at": "2026-09-06T10:00:00Z", "by": ANNA,
                      "said": "", "retracted": True, "graph_id": ROOM}]


def test_UN_MESSAGGIO_RITRATTATO_RESTA_NELL_ELENCO_senza_il_testo():
    """Sparire senza dirlo lascerebbe un buco che sembra un errore di lettura."""
    doc, section = _detto_una_frase()
    from s3dgraphy import crdt as em
    for op in chat.retract_ops("m1"):
        op.update(author=ANNA, ts="2026-09-06T10:05:00Z")
        em.apply_op_to_section(section, op)
    assert len(chat.conversation(doc)) == 1
    assert chat.conversation(doc, include_retracted=False) == []


# ═══ 5 · LA STANZA LO SA DIRE DI SÉ ══════════════════════════════════════════

def test_LA_ROTTA_LEGGE_E_BASTA(client, stanza):
    registry, _store = stanza
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as socket:
        _drain_join(socket)
        _dì(socket, "la 12 taglia la 14", "m1", "2026-09-06T10:00:00Z")
    letto = client.get(f"/v1/rooms/{ROOM}/chat").json()
    assert letto["total"] == 1
    assert letto["messages"][0]["said"] == "la 12 taglia la 14"
    assert letto["retracted"] == 0
    #: e non esiste la gemella che scrive: `test_write_paths` tiene le vie a due
    assert client.post(f"/v1/rooms/{ROOM}/chat", json={}).status_code == 405


def test_HEALTH_DICE_QUANTE_CONVERSAZIONI_E_IL_LORO_CONFINE(client, stanza):
    registry, _store = stanza
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as socket:
        _drain_join(socket)
        _dì(socket, "prima", "m1", "2026-09-06T10:00:00Z")
        _dì(socket, "seconda", "m2", "2026-09-06T10:01:00Z")
    salute = client.get("/health").json()["conversations"]
    assert salute["rooms_with_a_conversation"] == 1
    assert salute["messages"] == 2
    #: IL CONFINE, GUARDATO invece che promesso: zero per costruzione, e il
    #: giorno che non lo fosse si vede qui e non in un commento.
    assert salute["in_documents"] == 0
