"""Il watermark: due implementazioni contraddittorie, e chi paga se se ne toglie una.

════════════════════════════════════════════════════════════════════════════════
## IL FATTO

`ws.py` dichiara la semantica che vuole, nel gestore di `ack`:

> *«"I have applied everything up to here" — the watermark that makes compaction
> safe. A client that never acks simply holds the GC back, which is the failure
> direction we want.»*

E due righe più in là, dentro `_fanout`, ne implementa un'altra:

```python
member.watermark = str(body.get("ts") or member.watermark or "")
```

**Spedire a qualcuno lo dichiara aggiornato.** Il watermark misura la consegna,
non l'applicazione — e la stessa bugia è detta al join
(`member.watermark = room.last_op_at or now_iso()`).

Dal 26 settembre l'innesco è diventato regolare: StratiField chiede un
`request_save` a ogni scheda, e `request_save` è l'unica cosa che compatta.

## LA DOMANDA CHE VIENE PRIMA DELLA RIPARAZIONE: CHI MANDA `ack`?

Contato su tutto l'ecosistema, cercando il verbo sul filo:

```
EM-blender-tools   sync_manager/room_session.py:191   → SÌ, e con la semantica
                   chiamato da operators.py:582          giusta: `SESSION.ack()`
                                                         DOPO aver applicato,
                                                         con `last_applied`
EMStudio           frontend/src/*.ts                  → nessuna occorrenza
StratiField        app/session.py, app/writer.py      → nessuna occorrenza
stratigraph-server                                    → solo il gestore e i test
```

**Un client su tre.** Quindi togliere la riga da `_fanout` senza altro sposta il
difetto da «compatta troppo» a «non compatta quasi mai», in silenzio, in
un'altra funzione.

Questo file **misura** quella conseguenza invece di ragionarla, e resta come la
prova su cui si appoggia la decisione. La riparazione non è di stanotte: vuole
l'`ack` in due client, cioè due repository, cioè un prompt suo.
"""

from __future__ import annotations

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
from app.wire import WIRE, envelope                        # noqa: E402

ROOM = "watermark"
T0 = "2026-09-27T09:00:00Z"


def _document():
    return {"header": {"format": "em.json", "version": "1.0"},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [{"id": "US1", "node_type": "US",
                                         "name": "US1",
                                         "data": {"created_at": T0}}],
                              "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture
def relay(monkeypatch):
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


@pytest.fixture
def senza_watermark_dal_fanout(monkeypatch):
    """`_fanout` senza la riga che muove il watermark — cioè la riparazione,
    applicata da sola, per misurarne la conseguenza."""
    async def _fanout(room, message, *, skip=None):
        for connection_id, socket in list(room.sockets.items()):
            if connection_id == skip:
                continue
            await ws_module._send(socket, message)
    monkeypatch.setattr(ws_module, "_fanout", _fanout)


def _drain_join(socket):
    for _ in range(3):
        socket.receive_json()


def _op(value, ts):
    return {"v": WIRE, "type": "op", "source": "test",
            "payload": {"op": "update_field", "node_id": "US1",
                        "field": "description", "value": value, "ts": ts}}


def _attendi(socket, tipo):
    """Il primo frame di quel tipo, saltando le notizie degli altri."""
    for _ in range(20):
        message = socket.receive_json()
        if message["type"] == tipo:
            return message
    raise AssertionError(f"nessun «{tipo}» in venti frame")


def _lavora_e_salva(client):
    """Due membri che scrivono a turno e **nessuno dei due conferma niente** —
    come EMStudio e StratiField oggi.

    A turno, e non uno solo: `_fanout` salta l'origine, quindi chi scrive non si
    dichiara aggiornato da solo. Con un mittente unico il minimo resterebbe il
    suo ingresso, e non si vedrebbe la cosa che si vuole misurare.
    """
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a, \
         client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as b:
        _drain_join(a)
        _drain_join(b)
        a.receive_json()                     # A sa che B è entrata
        for i, (chi, altro) in enumerate([(a, b), (b, a), (a, b)]):
            chi.send_json(_op(f"v{i}", f"2026-09-27T1{i}:00:00Z"))
            assert _attendi(chi, "op_result")["payload"]["applied"] is True
            _attendi(altro, "op")            # la riceve, e non conferma niente
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        _attendi(a, "snapshot_written")
        room = ws_module.ROOMS.peek(ROOM)
        # gli ingressi si prendono QUI: uscendo dal `with` i membri se ne vanno
        # e il roster si svuota — la presenza è effimera per costruzione.
        return room, {m.joined_at for m in room.members.values()}


ISTANTI = {"2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z",
           "2026-09-27T12:00:00Z"}


# ── la misura ───────────────────────────────────────────────────────────────

def test_oggi_la_compattazione_passa_oltre_chi_non_ha_applicato(client, relay):
    """COM'È ADESSO. Nessuno dei due ha confermato niente, e la compattazione
    arriva fino a un'operazione **spedita** — cioè il watermark si è mosso da
    solo, perché `_fanout` lo muove quando manda."""
    room, _ingressi = _lavora_e_salva(client)
    assert room.compacted_upto in ISTANTI, (
        f"atteso l'istante di un'operazione, trovato {room.compacted_upto}")


def test_togliendo_la_riga_la_compattazione_NON_GIRA_PIU(
        client, relay, senza_watermark_dal_fanout):
    """LA CONSEGUENZA, MISURATA. Stessa scena, con la sola riparazione del
    watermark: `compacted_upto` resta all'istante in cui i membri sono ENTRATI,
    perché è l'unico watermark che hanno mai avuto.

    Su una sessione che dura giorni — cioè quella che StratiField ha da ieri —
    vuol dire che la compattazione non avanza mai più. È la direzione di
    fallimento giusta (meglio non compattare che compattare troppo) ed è una
    regressione silenziosa in un'altra funzione: nessuno la vedrebbe finché il
    documento non diventa grande."""
    room, ingressi = _lavora_e_salva(client)
    assert room.compacted_upto not in ISTANTI, (
        "il watermark si è mosso lo stesso")
    assert room.compacted_upto in ingressi, (
        f"{room.compacted_upto} non è l'ingresso di nessuno: {ingressi}")


def test_con_UN_ack_solo_non_basta(client, relay, senza_watermark_dal_fanout):
    """E la forma della riparazione completa, provata su un client solo.

    Con l'`ack` il minimo si muove **solo per chi lo manda**, e si ferma dove il
    più lento ha davvero applicato — che è la semantica dichiarata. Qui B lo
    manda e A no: vince l'ingresso di A, e la compattazione resta ferma.

    È la ragione per cui la riparazione vuole l'`ack` in DUE client, cioè due
    repository, cioè un prompt suo."""
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as a, \
         client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as b:
        _drain_join(a)
        _drain_join(b)
        a.receive_json()
        for i in range(3):
            a.send_json(_op(f"v{i}", f"2026-09-27T1{i}:00:00Z"))
            _attendi(a, "op_result")
        b.send_json({"v": WIRE, "type": "ack", "source": "test",
                     "payload": {"ts": "2026-09-27T11:00:00Z"}})
        b.send_json({"v": WIRE, "type": "request_snapshot", "source": "test",
                     "payload": {}})
        _attendi(b, "snapshot")              # l'ack di B è passato
        a.send_json({"v": WIRE, "type": "request_save", "source": "test",
                     "payload": {}})
        _attendi(a, "snapshot_written")
        room = ws_module.ROOMS.peek(ROOM)
        ingresso_di_a = next(m.joined_at for m in room.members.values()
                             if m.watermark not in ISTANTI)
        assert room.compacted_upto == ingresso_di_a, (
            "un ack solo ha spostato il minimo: non è quello che deve fare")


# ── il conteggio, come cancello ─────────────────────────────────────────────

def test_chi_manda_ack_e_UNO_SOLO_in_tutto_lecosistema():
    """Il conteggio è un cancello, non una nota: il giorno che EMStudio o
    StratiField imparano a mandarlo, questo test diventa rosso e la riparazione
    del watermark si può fare. **È il segnale che aspettiamo.**

    Sui repository che ci sono accanto; se non ci sono, si salta e lo dice.
    """
    import pathlib

    vicini = pathlib.Path(__file__).resolve().parents[2]
    parlanti = {
        "EM-blender-tools": vicini / "EM-blender-tools" / "sync_manager",
        "EMStudio": vicini / "EMStudio" / "frontend" / "src",
        "stratigraph-chatbot": vicini / "stratigraph-chatbot" / "app",
    }
    mancanti = [n for n, p in parlanti.items() if not p.is_dir()]
    if mancanti:
        pytest.skip(f"non accanto: {mancanti}")

    manda = set()
    for nome, radice in parlanti.items():
        for py in list(radice.rglob("*.py")) + list(radice.rglob("*.ts")):
            testo = py.read_text(encoding="utf-8", errors="replace")
            codice = "\n".join(l for l in testo.splitlines()
                               if not l.lstrip().startswith(("#", "//", "*")))
            if '"ack"' in codice or "'ack'" in codice:
                manda.add(nome)
    assert manda == {"EM-blender-tools"}, (
        f"chi manda `ack` è cambiato: {sorted(manda)}. Se sono diventati tutti, "
        f"la riga del watermark in `_fanout` si può togliere — è la scoperta "
        f"che questo test aspetta.")
