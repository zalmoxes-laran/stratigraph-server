"""Cosa conserva davvero una stanza — misurato prima di costruirci sopra.

Il prompt del 25 settembre chiede quattro risposte, e la prima («cosa è
cambiato da quando non guardavo») dovrebbe leggere il registro delle operazioni
che la stanza già tiene: `room.record(op)`, chiamata da tutte e due le vie di
scrittura.

**Misura prima di progettare**, dice il prompt, e la misura ha cambiato il
progetto. Questo file è quella misura. Non è un test di regressione su una
funzione nuova: è il verbale di cosa c'è, scritto in modo che resti vero.

Le quattro cose che dice, in ordine di gravità:

1. il registro vive **solo in memoria**: nessun byte ne finisce nello store;
2. un'operazione arrivata dal **WebSocket non viene persistita** — il documento
   si scrive solo se qualcuno chiede `request_save`, o se l'operazione è
   arrivata dalla via REST dei connettori;
3. quindi una stanza dimenticata e ricostruita (`forget`, che è ciò che fa un
   riavvio) perde **sia il registro sia le operazioni non salvate**;
4. e i **rifiuti** non sono registrati da nessuna parte: tornano al chiamante e
   finiscono lì.

`forget` e un riavvio sono la stessa cosa per una stanza, e non è un'analogia
mia: lo dice la docstring del metodo — «the next join rebuilds it from the store
— which is the property that makes this state a cache and not a home».
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402

from app import ws as ws_module                            # noqa: E402
from app.main import app                                   # noqa: E402
from app.rooms import OPLOG_LIMIT, RoomRegistry            # noqa: E402
from app.store import InMemorySnapshotStore                # noqa: E402
from app.wire import WIRE                                  # noqa: E402

ROOM = "registro"
ANNA = "0000-0002-1825-0097"
T1 = "2026-09-25T10:00:00Z"
T2 = "2026-09-25T11:00:00Z"
T3 = "2026-09-25T12:00:00Z"


@pytest.fixture(autouse=True)
def relay(monkeypatch):
    store = InMemorySnapshotStore()
    store.put(ROOM, {
        "header": {"format": "em.json", "version": "1.0"},
        "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                          "nodes": [{"id": "US1", "node_type": "US",
                                     "name": "US1",
                                     "data": {"created_at": T1,
                                              "created_by": ANNA}}],
                          "edges": []}},
        "active_graph_id": ROOM,
    })
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    return registry


@pytest.fixture
def client():
    return TestClient(app)


def _room(registry, room_id=ROOM):
    """La copia di lavoro viva. `peek` è il lettore sincrono; `get` è una
    coroutine e ricostruisce, che è quello che serve DOPO un `forget`."""
    import asyncio
    return registry.peek(room_id) or asyncio.run(registry.get(room_id))


def _drain_join(socket):
    for _ in range(3):
        socket.receive_json()


def _op(value, ts, field="description"):
    return {"v": WIRE, "type": "op", "source": "test",
            "payload": {"op": "update_field", "node_id": "US1",
                        "field": field, "value": value, "ts": ts}}


# ── 1 · il registro è in memoria, e basta ───────────────────────────────────

def test_il_registro_non_finisce_mai_nello_store(client, relay):
    """`record` riempie una lista sull'oggetto Room. Nient'altro.

    Il cancello non guarda il codice: guarda il **byte scritto**. Se un giorno
    qualcuno persistesse il registro, questo test diventa rosso ed è giusto —
    va riscritto, con la prova che adesso sopravvive."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("muro in opus", T2))
        assert a.receive_json()["payload"]["applied"] is True
        room = _room(relay)
        assert len(room.oplog) == 1, "in memoria c'è"

    scritto = json.dumps(relay.store.get(ROOM), ensure_ascii=False)
    assert "oplog" not in scritto
    assert "muro in opus" not in scritto, (
        "l'operazione dal socket non è stata scritta da nessuna parte")


def test_una_operazione_dal_socket_non_viene_persistita(client, relay):
    """E questa è più grave del registro: è il DOCUMENTO.

    `apply_from_connector` (la via REST) chiama `room.snapshot` dentro il lock.
    Il gestore del frame `op` no: persistere è una richiesta del client
    (`request_save`), e il chatbot di StratiField non la fa mai.

    Misurato sulla dev-stack lo stesso giorno: la stanza
    `probe-stratifield-02-aereo` conteneva US44 con 26 campi, letta dallo
    snapshot WS, e in `/srv/em-data/snapshots/` il suo `.em.json`
    **non esisteva**. Solo `.room.json` e `.acl.json`."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("scritta dal socket", T2))
        assert a.receive_json()["payload"]["applied"] is True

    su_disco = json.dumps(relay.store.get(ROOM), ensure_ascii=False)
    assert "scritta dal socket" not in su_disco


def test_chiedere_di_salvare_scrive_il_documento_ma_non_il_registro(client, relay):
    """Il mezzo rimedio che esiste, e il mezzo che manca."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("salvata su richiesta", T2))
        assert a.receive_json()["payload"]["applied"] is True
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        assert a.receive_json()["type"] == "snapshot_written"

    su_disco = json.dumps(relay.store.get(ROOM), ensure_ascii=False)
    assert "salvata su richiesta" in su_disco, "il documento sì"
    assert "oplog" not in su_disco, "il registro no"


# ── 2 · l'effetto della rottura: una stanza dimenticata ─────────────────────

def test_dimenticare_la_stanza_perde_registro_e_operazioni(client, relay):
    """L'EFFETTO, non la sostituzione.

    `forget` è quello che fa un riavvio: la copia di lavoro sparisce e la
    prossima entrata la ricostruisce dallo store. Dopo, il registro è vuoto e
    l'operazione non è più nel documento — cioè le due cose su cui «cosa è
    cambiato da quando non guardavo» dovrebbe poggiare."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("prima del riavvio", T2))
        assert a.receive_json()["payload"]["applied"] is True

    prima = _room(relay)
    assert len(prima.oplog) == 1
    assert prima.replay_since(T1), "c'era qualcosa da riprodurre"

    relay.forget(ROOM)                       # ← il riavvio, in una riga

    dopo = _room(relay)
    assert dopo.oplog == [], "il registro è sparito"
    assert dopo.replay_since(T1) == [], "e con lui la risposta alla domanda"
    nodi = dopo.document["graphs"][ROOM]["nodes"]
    assert nodi[0].get("description") is None
    assert "prima del riavvio" not in json.dumps(dopo.document,
                                                 ensure_ascii=False)


def test_dimenticare_non_perde_quello_che_era_stato_salvato(client, relay):
    """La metà che regge, perché un referto che dice solo «si perde tutto»
    sarebbe falso: quello che è passato da `request_save` resta."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("questa resta", T2))
        a.receive_json()
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        a.receive_json()

    relay.forget(ROOM)
    dopo = _room(relay)
    assert "questa resta" in json.dumps(dopo.document, ensure_ascii=False)
    assert dopo.oplog == [], "il documento sì, il registro comunque no"


# ── 3 · e anche in memoria è una finestra, non un registro ──────────────────

def test_il_registro_e_una_finestra_di_512(client, relay):
    """`OPLOG_LIMIT` taglia dalla testa. Anche senza riavvii, la 513esima
    operazione fa sparire la prima — quindi «cosa è cambiato da quando non
    guardavo» avrebbe comunque un orizzonte, e non lo direbbe."""
    room = _room(relay)
    for i in range(OPLOG_LIMIT + 1):
        room.record({"op": "update_field", "node_id": "US1", "field": "name",
                     "value": str(i), "ts": f"2026-09-25T10:{i // 60:02d}:{i % 60:02d}Z"})
    assert len(room.oplog) == OPLOG_LIMIT
    assert room.oplog[0]["value"] == "1", "la prima è stata buttata"


# ── 4 · i rifiuti non sono registrati da nessuna parte ──────────────────────

def test_un_rifiuto_non_lascia_traccia(client, relay):
    """«Le operazioni rifiutate per una ragione che non è l'idempotenza» sono
    metà di «cosa aspetta me». Oggi il rifiuto torna al chiamante e finisce lì:
    non è nel registro, non è nel documento, non è nello store.

    Qui il rifiuto è vero e non simulato: `update_field` su un nodo che non
    esiste."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json({"v": WIRE, "type": "op", "source": "test",
                     "payload": {"op": "update_field", "node_id": "NON-CE",
                                 "field": "name", "value": "x", "ts": T3}})
        esito = a.receive_json()["payload"]
        assert esito["applied"] is False
        assert "not here" in esito.get("reason", ""), esito

        room = _room(relay)
        assert room.oplog == [], "un rifiuto non entra nel registro"

    assert "NON-CE" not in json.dumps(relay.store.get(ROOM), ensure_ascii=False)
