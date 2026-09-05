"""La rete: una stanza si tiene senza dipendere dalla buona educazione di chi ci scrive.

════════════════════════════════════════════════════════════════════════════════
## IL FATTO

`ws.py` dichiarava che la persistenza è del client (`request_save`). EMStudio lo
chiede; il `RoomWriter` di StratiField non lo chiedeva mai — apriva una
connessione per consegna e la chiudeva, quindi quando qualcuno avrebbe dovuto
chiedere non c'era più nessuno.

Misurato sulla dev-stack il 25 e rimisurato il 26 prima di toccare niente:
**217 stanze dichiarate, 14 documenti su disco**. E una scheda di ventisei campi
dettata in aereo, riaccodata, confermata con l'ORCID di chi l'aveva dettata, che
non è mai esistita fuori dalla memoria di un processo — mentre la stanza
rispondeva `applied: True` a ogni operazione.

## LA REGOLA DI CASA, APPLICATA QUI

Il primo test di questo file **spegne la rete** e rimisura la perdita, poi la
riaccende sullo stesso giro. È il cancello che verifica l'effetto della rottura,
non che una sostituzione sia avvenuta: se un domani `KEEPER.kept` smettesse di
essere chiamato, quel test tornerebbe verde con la rete spenta e rosso con quella
accesa — cioè direbbe esattamente cosa è successo.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402

from app import keeping                                    # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.main import app                                   # noqa: E402
from app.rooms import RoomRegistry                         # noqa: E402
from app.store import InMemorySnapshotStore                # noqa: E402
from app.wire import WIRE                                  # noqa: E402

ROOM = "rete"
ANNA = "0000-0002-1825-0097"
#: quando il nodo di partenza è stato creato. Ogni operazione dei test è
#: successiva: un `update_field` con lo STESSO istante del timbro di nodo perde
#: contro di lui — misurato sbagliando, ed è il comportamento giusto del CRDT.
T1 = "2026-09-26T09:00:00Z"
T2 = "2026-09-26T10:00:00Z"


def _document():
    return {"header": {"format": "em.json", "version": "1.0"},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [{"id": "US1", "node_type": "US",
                                         "name": "US1",
                                         "data": {"created_at": T1,
                                                  "created_by": ANNA}}],
                              "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture
def relay(monkeypatch):
    store = InMemorySnapshotStore()
    store.put(ROOM, _document())
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    return registry


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def net(monkeypatch):
    """Sostituisce la rete con una a soglie scelte dal test.

    `on=False` la spegne davvero: il temporizzatore gira e non salva niente. È
    così che si rimisura la perdita senza smontare il codice."""
    def make(*, on=True, after_ops=64, after_quiet=2.0):
        if on:
            keeper = keeping.Keeper(ws_module._keep, after_ops=after_ops,
                                    after_quiet=after_quiet)
        else:
            async def nothing(room, why):
                return None
            keeper = keeping.Keeper(nothing, after_ops=after_ops,
                                    after_quiet=after_quiet)
        monkeypatch.setattr(ws_module, "KEEPER", keeper)
        return keeper
    return make


def _drain_join(socket):
    host = socket.receive_json()
    socket.receive_json()
    socket.receive_json()
    return host["payload"]


def _op(value, ts=T2, node_id="US1", field="description"):
    return {"v": WIRE, "type": "op", "source": "test",
            "payload": {"op": "update_field", "node_id": node_id,
                        "field": field, "value": value, "ts": ts}}


def _stored(relay):
    return json.dumps(relay.store.get(ROOM), ensure_ascii=False)


# ═══ 1 · l'effetto della rottura, misurato spegnendo la rete ════════════════

def test_con_la_rete_spenta_il_lavoro_si_perde(client, relay, net):
    """LA PERDITA, RIMISURATA. Nessuno chiede `request_save`, come StratiField
    non chiedeva: l'operazione è applicata, la stanza la mostra, e alla fine
    non c'è."""
    net(on=False)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("muro in opus"))
        assert a.receive_json()["payload"]["applied"] is True
        # la stanza la mostra: è questo che rendeva il difetto invisibile
        assert "muro in opus" in json.dumps(
            ws_module.ROOMS.peek(ROOM).document, ensure_ascii=False)

    assert "muro in opus" not in _stored(relay)
    relay.forget(ROOM)                          # ← il riavvio
    assert "muro in opus" not in json.dumps(
        asyncio.run(relay.get(ROOM)).document, ensure_ascii=False)


def test_con_la_rete_accesa_lo_stesso_giro_tiene(client, relay, net):
    """Identico al precedente riga per riga, tranne la prima."""
    net(on=True)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("muro in opus"))
        assert a.receive_json()["payload"]["applied"] is True

    assert "muro in opus" in _stored(relay)
    relay.forget(ROOM)
    assert "muro in opus" in json.dumps(
        asyncio.run(relay.get(ROOM)).document, ensure_ascii=False)


# ═══ 2 · i tre momenti in cui la rete scatta ════════════════════════════════

def test_lultimo_che_sa_scrivere_esce_e_la_stanza_tiene(client, relay, net):
    """Il terzo momento, e quello che ha salvato il test qui sopra: nessuna
    soglia raggiunta, nessuna quiete attesa — semplicemente non c'è più
    nessuno da cui aspettarsi un `request_save`."""
    net(on=True, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("l'ultimo esce"))
        a.receive_json()
        assert "l'ultimo esce" not in _stored(relay), "non ancora"
    assert "l'ultimo esce" in _stored(relay)
    assert ws_module.ROOMS.peek(ROOM).unsaved == 0


def test_la_soglia_a_conteggio(client, relay, net):
    """Sessantaquattro è la soglia vera; qui è tre, perché un test che ne manda
    sessantaquattro misurerebbe la pazienza e non la regola."""
    net(on=True, after_ops=3, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        for i in range(2):
            a.send_json(_op(f"v{i}", ts=f"2026-09-26T10:0{i}:00Z"))
            assert a.receive_json()["payload"]["applied"] is True
        assert ws_module.ROOMS.peek(ROOM).unsaved == 2
        assert "v1" not in _stored(relay), "sotto soglia non si scrive"

        a.send_json(_op("v2", ts="2026-09-26T10:02:00Z"))
        assert a.receive_json()["payload"]["applied"] is True
        scritto = a.receive_json()          # …e la terza fa scattare
        assert scritto["type"] == "snapshot_written"
        assert scritto["payload"]["why"] == keeping.BY_COUNT
        assert "v2" in _stored(relay)
        assert ws_module.ROOMS.peek(ROOM).unsaved == 0


def test_la_soglia_a_quiete(client, relay, net):
    """Due secondi è la soglia vera; qui è un decimo."""
    net(on=True, after_ops=10_000, after_quiet=0.1)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("dopo la quiete"))
        assert a.receive_json()["payload"]["applied"] is True
        assert "dopo la quiete" not in _stored(relay), "non subito"
        scritto = a.receive_json()          # arriva quando la quiete scade
        assert scritto["type"] == "snapshot_written"
        assert scritto["payload"]["why"] == keeping.BY_QUIET
        assert "dopo la quiete" in _stored(relay)


def test_una_raffica_produce_UN_salvataggio(client, relay, net):
    """Il numero che governa la scelta di N e T: una scheda è **27 operazioni
    misurate** (un nodo e ventisei campi), e deve produrre un salvataggio, non
    ventisette. Qui si contano le scritture nello store."""
    net(on=True, after_ops=64, after_quiet=0.1)
    scritture = []
    vera = relay.store.put
    relay.store.put = lambda rid, doc: (scritture.append(rid), vera(rid, doc))[1]

    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        for i in range(27):
            a.send_json(_op(f"c{i}", ts=f"2026-09-26T11:{i:02d}:00Z",
                            field=f"data.c{i}"))
            assert a.receive_json()["payload"]["applied"] is True
        assert scritture == [], "durante la raffica non si scrive"
        assert a.receive_json()["type"] == "snapshot_written"
    assert len(scritture) == 1, f"27 operazioni → {len(scritture)} scritture"


# ═══ 3 · il lock, e la compattazione durante una raffica ════════════════════

def test_un_lotto_di_connettore_non_si_vede_mai_a_meta(client, relay, net):
    """LA GARANZIA CHE IL DIFFERITO NON DEVE ROMPERE.

    `apply_from_connector` tiene `room.lock` per il lotto intero, «perché uno
    snapshot preso fra due operazioni di un delta sarebbe un grafo che nessuno
    ha scritto». Il salvataggio differito prende lo stesso lock, quindi non può
    cadere in mezzo: al massimo aspetta.

    Il cancello guarda **cosa c'era dentro a ogni scrittura**, non dove sta il
    lock: se un domani `_keep` smettesse di prenderlo, qui comparirebbe una
    scrittura con tre unità su cinque."""
    net(on=True, after_ops=1, after_quiet=0.01)   # la rete più aggressiva possibile
    visto = []
    vera = relay.store.put

    def spiando(rid, doc):
        nodi = [n["id"] for g in (doc.get("graphs") or {}).values()
                for n in (g.get("nodes") or [])]
        visto.append(sum(1 for i in nodi if i.startswith("L")))
        return vera(rid, doc)
    relay.store.put = spiando

    ops = [{"op": "add_node", "id": f"L{i}", "ts": f"2026-09-26T12:0{i}:00Z",
            "node": {"node_type": "US", "name": f"L{i}"}} for i in range(5)]
    stanza = asyncio.run(relay.get(ROOM))
    esito = asyncio.run(ws_module.apply_from_connector(stanza, ops, author=ANNA,
                                                       source="test"))
    assert esito["applied"] == 5
    assert visto, "il lotto è stato scritto"
    for quante in visto:
        assert quante in (0, 5), f"lotto visto a metà: {quante} unità su 5"


def test_la_compattazione_non_cambia_lo_stato_osservabile(client, relay, net,
                                                          monkeypatch):
    """§2.1 · la compattazione durante una raffica è innocua, **con la prova**.

    Si fa lo stesso lavoro due volte: una con la rete che salva a ogni
    operazione (quindi compattando in mezzo) e una senza salvataggi intermedi.
    Lo stato osservabile — nodi, nomi, valori — deve essere identico.

    Innocua NON vuol dire che non fa niente: `em.compact` butta gli orologi di
    campo più vecchi del watermark. Vuol dire che quello che butta è
    bookkeeping che ogni connesso ha già visto passare, ed è la ragione per cui
    il watermark è un MINIMO."""
    def vista(document):
        return sorted(
            (n["id"], n.get("name"), n.get("description"),
             tuple(sorted((k, str(v)) for k, v in (n.get("data") or {}).items()
                          if k not in ("field_clocks", "modified_at",
                                       "created_at"))))
            for g in (document.get("graphs") or {}).values()
            for n in (g.get("nodes") or []))

    risultati = {}
    for etichetta, soglia in (("compattando", 1), ("senza", 10_000)):
        store = InMemorySnapshotStore()
        store.put(ROOM, _document())
        registry = RoomRegistry(store)
        # `monkeypatch`, non un'assegnazione: un relay lasciato sporco fa
        # fallire i test dopo con un messaggio che parla d'altro — misurato.
        monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
        monkeypatch.setattr(ws_module, "ROOMS", registry)
        monkeypatch.setattr(ws_module, "KEEPER", keeping.Keeper(
            ws_module._keep, after_ops=soglia, after_quiet=10_000))
        with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
            _drain_join(a)
            for i in range(6):
                a.send_json(_op(f"x{i}", ts=f"2026-09-26T13:0{i}:00Z",
                                field=f"data.k{i}"))
                assert a.receive_json()["payload"]["applied"] is True
                # l'ack è ciò che muove il watermark, cioè ciò che AUTORIZZA la
                # compattazione: senza, `gc_watermark()` non compatta niente
                a.send_json({"v": WIRE, "type": "ack", "source": "test",
                             "payload": {"ts": f"2026-09-26T13:0{i}:00Z"}})
                while soglia == 1:
                    frame = a.receive_json()
                    if frame["type"] == "snapshot_written":
                        break
        risultati[etichetta] = vista(registry.peek(ROOM).document)

    assert risultati["compattando"] == risultati["senza"]


def test_la_rete_tiene_ma_non_compatta(client, relay, net):
    """**DURARE E COMPATTARE SONO DUE COSE**, e la rete ne fa una sola.

    Il salvataggio differito scrive il documento e lascia `compacted_upto`
    dov'era. Un `request_save` — che qualcuno ha chiesto — compatta.

    Il perché è nel test qui sotto: il watermark su cui la compattazione si
    appoggia misura la CONSEGNA, non l'applicazione. Finché a compattare era un
    client che lo chiedeva, era una sua decisione; una rete che scatta ogni 64
    operazioni la renderebbe continua, e nessuno l'ha chiesta."""
    net(on=True, after_ops=1, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json({"v": WIRE, "type": "ack", "source": "test",
                     "payload": {"ts": "2026-09-26T14:00:00Z"}})
        a.send_json(_op("dalla rete", ts="2026-09-26T15:00:00Z"))
        assert a.receive_json()["payload"]["applied"] is True
        scritto = a.receive_json()
        assert scritto["type"] == "snapshot_written"
        assert "dalla rete" in _stored(relay), "il documento è tenuto"
        assert ws_module.ROOMS.peek(ROOM).compacted_upto is None, (
            "la rete ha compattato senza che nessuno lo chiedesse")

        # …e chi lo chiede, compatta
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        assert a.receive_json()["type"] == "snapshot_written"
        assert ws_module.ROOMS.peek(ROOM).compacted_upto is not None


def test_il_watermark_misura_la_consegna_non_lapplicazione(client, relay, net):
    """LA RAGIONE DELLA RIGA SOPRA, misurata invece che assunta.

    Il commento di `ack` dice: «un client che non manda mai un ack trattiene
    semplicemente la GC, che è la direzione in cui vogliamo sbagliare». Non è
    quello che succede. `_fanout`, alla riga in cui spedisce, fa anche:

        member.watermark = str(body.get("ts") or member.watermark or "")

    cioè **spedire a qualcuno lo dichiara aggiornato**. Qui B conferma le 13:00
    e non legge mai più niente; A manda un'operazione delle 15:00; il watermark
    di B diventa le 15:00 senza che B abbia letto un byte.

    Non è una perdita di dati — chi rientra con una base più vecchia del
    `gc_watermark` viene avvisato al join e si risincronizza invece di
    riprodurre. Ma è **risincronizzare invece di riprodurre**, e la differenza
    la paga chi ha la rete lenta. Con la rete che non compatta, la finestra
    resta quella di prima; se un domani si volesse compattare anche lì, questa
    è la riga da cambiare per prima."""
    net(on=True, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a, \
         client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as b:
        _drain_join(a)
        _drain_join(b)
        a.receive_json()                        # A è avvisata che B è entrata
        # tutti e due confermano, altrimenti il minimo è l'istante di ingresso
        # di chi non ha detto niente — che è vero e non è quello che si misura qui
        a.send_json({"v": WIRE, "type": "ack", "source": "test",
                     "payload": {"ts": "2026-09-26T14:00:00Z"}})
        b.send_json({"v": WIRE, "type": "ack", "source": "test",
                     "payload": {"ts": "2026-09-26T13:00:00Z"}})
        b.send_json({"v": WIRE, "type": "request_snapshot", "source": "test",
                     "payload": {}})
        assert b.receive_json()["type"] == "snapshot"   # l'ack è passato

        room = ws_module.ROOMS.peek(ROOM)
        assert room.gc_watermark() == "2026-09-26T13:00:00Z"

        a.send_json(_op("che B non leggerà", ts="2026-09-26T15:00:00Z"))
        assert a.receive_json()["payload"]["applied"] is True

        # B NON HA LETTO NIENTE, e risulta aggiornato alle 15:00. A invece
        # resta al suo ack: `_fanout` salta l'origine, quindi chi scrive non si
        # dichiara aggiornato da solo — è l'unico membro che dice la verità qui.
        per_membro = {m.watermark for m in room.members.values()}
        assert per_membro == {"2026-09-26T14:00:00Z", "2026-09-26T15:00:00Z"}
        assert room.gc_watermark() == "2026-09-26T14:00:00Z", (
            "il minimo è l'ack di A, non la consegna a B — che è più avanti")


def test_un_membro_nasce_con_un_watermark(client, relay, net):
    """L'altra metà, che rende la riga sopra meno grave di come suona: un
    membro non parte da `None`. Al join il server fa
    `member.watermark = room.last_op_at or now_iso()`, quindi entrare in una
    stanza vecchia non autorizza a compattare la sua storia."""
    net(on=True, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        membro = next(iter(ws_module.ROOMS.peek(ROOM).members.values()))
        assert membro.watermark, "un membro nasce con un watermark"


def test_la_rete_non_accorcia_la_memoria_della_stanza(client, relay, net):
    """§3 · QUANTO LONTANO PUÒ GUARDARE INDIETRO UNA STANZA, adesso.

    `snapshot()` troncava il registro ogni volta che salvava. Con una rete che
    salva ogni 64 operazioni o ogni due secondi, quel troncamento sarebbe
    diventato continuo e la finestra di riproduzione si sarebbe chiusa quasi
    subito — cioè la rete avrebbe pagato la durata con la memoria.

    Non succede, perché troncare è legato al compattare (`if gc and before`) e
    la rete non compatta. Il registro resta quello che era: **in memoria, fino a
    `OPLOG_LIMIT = 512`, e perso al riavvio**.

    Il che lascia la domanda dove il prompt la vuole: «cosa è cambiato da quando
    non guardavo» ha bisogno di un registro che duri, **oppure** di essere
    ridefinita come un confronto fra due documenti. La seconda è più economica e
    non è una decisione mia."""
    net(on=True, after_ops=2, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json({"v": WIRE, "type": "ack", "source": "test",
                     "payload": {"ts": "2026-09-26T20:00:00Z"}})
        for i in range(4):
            a.send_json(_op(f"m{i}", ts=f"2026-09-26T17:0{i}:00Z",
                            field=f"data.m{i}"))
            assert a.receive_json()["payload"]["applied"] is True
            if (i + 1) % 2 == 0:
                assert a.receive_json()["type"] == "snapshot_written"
        # due salvataggi differiti sono passati e il registro è tutto lì
        assert len(ws_module.ROOMS.peek(ROOM).oplog) == 4

        # …mentre un `request_save` lo taglia, come ha sempre fatto
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        assert a.receive_json()["type"] == "snapshot_written"
        assert ws_module.ROOMS.peek(ROOM).oplog == []


# ═══ 4 · il client dichiara, e chi non dichiara riceve la rete ══════════════

def test_un_client_vecchio_non_sa_che_esista_la_domanda(client, relay, net):
    """La retrocompatibilità: non manda `client_info`, non riceve un errore, e
    prende la rete d'ufficio. È l'impostazione SICURA, non un ripiego."""
    net(on=True, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        host = _drain_join(a)
        assert host["keeping"]["host_keeps"] is True
        a.send_json(_op("da un client vecchio"))
        a.receive_json()
    assert "da un client vecchio" in _stored(relay)


def test_chi_dichiara_di_salvarsi_da_solo_non_riceve_la_rete(client, relay, net):
    """L'EFFETTO della dichiarazione: stesso giro, esito opposto."""
    net(on=True, after_ops=1, after_quiet=0.05)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json({"v": WIRE, "type": "client_info", "source": "emstudio",
                     "payload": {"saves_itself": True}})
        risposta = a.receive_json()
        assert risposta["type"] == "host_info"
        assert risposta["payload"]["keeping"]["host_keeps"] is False

        a.send_json(_op("me la tengo io"))
        assert a.receive_json()["payload"]["applied"] is True
        assert "me la tengo io" not in _stored(relay)

        # …e quando lo chiede, si scrive
        a.send_json({"v": WIRE, "type": "request_save", "source": "emstudio",
                     "payload": {}})
        assert a.receive_json()["type"] == "snapshot_written"
        assert "me la tengo io" in _stored(relay)


def test_ma_uscendo_senza_aver_salvato_la_stanza_si_tiene_lo_stesso(
        client, relay, net):
    """La dichiarazione vale finché c'è qualcuno a mantenerla.

    Un client che diceva «salvo io» e se n'è andato senza farlo ha lasciato per
    terra lo stesso lavoro di uno che non aveva dichiarato niente — quindi
    `emptied` non guarda la dichiarazione. È il caso del telefono che si chiude
    a metà."""
    net(on=True, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json({"v": WIRE, "type": "client_info", "source": "emstudio",
                     "payload": {"saves_itself": True}})
        a.receive_json()
        a.send_json(_op("e poi se n'è andato"))
        a.receive_json()
        assert "e poi se n'è andato" not in _stored(relay)
    assert "e poi se n'è andato" in _stored(relay)


def test_basta_un_presente_che_non_dichiara_perche_la_rete_torni(
        client, relay, net):
    """Uno snapshot scrive il documento INTERO: il lavoro di chi non salva
    viaggia dentro il salvataggio di chi salva, ma solo quando quello lo fa.
    Quindi un client che entra senza dichiarare niente riaccende la rete per
    tutta la stanza."""
    net(on=True, after_ops=1, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as studio:
        _drain_join(studio)
        studio.send_json({"v": WIRE, "type": "client_info", "source": "emstudio",
                          "payload": {"saves_itself": True}})
        studio.receive_json()
        with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as campo:
            _drain_join(campo)                  # non dichiara niente
            studio.receive_json()               # presenza
            campo.send_json(_op("dal campo"))
            assert campo.receive_json()["payload"]["applied"] is True
            campo.receive_json()                # snapshot_written: la rete è tornata
            assert "dal campo" in _stored(relay)


def test_request_save_risponde_sempre_anche_se_non_ce_niente(client, relay, net):
    """Trovato rompendo `test_6c`. Il differito che salta un giro può tacere —
    nessuno lo aspettava; un client che ha CHIESTO sta aspettando, e il
    silenzio è la peggiore risposta possibile."""
    net(on=True)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        assert ws_module.ROOMS.peek(ROOM).unsaved == 0
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        risposta = a.receive_json()
        assert risposta["type"] == "snapshot_written"
        assert risposta["payload"]["why"] == keeping.BY_REQUEST


# ═══ 5 · il debito si vede ══════════════════════════════════════════════════

def test_il_debito_compare_in_cosa_aspetta_me(client, relay, net):
    net(on=True, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        for i in range(3):
            a.send_json(_op(f"d{i}", ts=f"2026-09-26T16:0{i}:00Z",
                            field=f"data.d{i}"))
            a.receive_json()
        answer = client.get(f"/v1/rooms/{ROOM}/waiting").json()["unsaved"]
        assert answer["unsaved_ops"] == 3
        assert answer["writers_present"] == 1
        # con qualcuno dentro non è «a rischio»: è il funzionamento normale
        assert answer["at_risk"] is False

    dopo = client.get(f"/v1/rooms/{ROOM}/waiting").json()["unsaved"]
    assert dopo["unsaved_ops"] == 0, "l'ultimo che esce ha tenuto"


def test_una_stanza_abbandonata_con_lavoro_dentro_e_a_rischio(client, relay, net):
    """IL CASO CHE LO FA SCATTARE, ed è quello vero: la rete spenta e nessuno
    dentro. È la forma esatta in cui è sparita la scheda del 25 settembre."""
    net(on=False, after_ops=10_000, after_quiet=10_000)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a:
        _drain_join(a)
        a.send_json(_op("nessuno la terrà"))
        a.receive_json()

    stato = client.get(f"/v1/rooms/{ROOM}/waiting").json()["unsaved"]
    assert stato["unsaved_ops"] == 1
    assert stato["writers_present"] == 0
    assert stato["at_risk"] is True

    salute = client.get("/health").json()["keeping"]
    assert ROOM in salute["rooms_at_risk"]
    assert salute["worst"]["room"] == ROOM
    assert salute["worst"]["unsaved_ops"] == 1


def test_la_salute_dichiara_la_politica(client):
    """Una soglia che nessuno può leggere è una politica segreta."""
    salute = client.get("/health").json()["keeping"]
    assert salute["policy"]["after_ops"] == keeping.SAVE_AFTER_OPS
    assert salute["policy"]["after_quiet_seconds"] == keeping.SAVE_AFTER_QUIET


# ═══ 6 · il recinto ═════════════════════════════════════════════════════════

def test_la_politica_non_sa_scrivere():
    """`room.snapshot` si chiama da `ws.py` e da nessun altro posto
    (`test_write_paths.py`). `keeping.py` è un temporizzatore: chiede."""
    import pathlib
    source = pathlib.Path(keeping.__file__).read_text(encoding="utf-8")
    codice = "\n".join(l for l in source.splitlines()
                       if not l.lstrip().startswith(("#", "*")))
    for vietato in ("room.snapshot", "SNAPSHOT_STORE", "store.put"):
        assert vietato not in codice, f"keeping.py scrive: {vietato}"
