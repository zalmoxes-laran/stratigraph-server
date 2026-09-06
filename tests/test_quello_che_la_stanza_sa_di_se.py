"""Il cruscotto della stanza, e il ricontrollo dell'ACL nelle due direzioni.

════════════════════════════════════════════════════════════════════════════════
## COSA C'ERA GIÀ, MISURATO PRIMA DI SCRIVERE UNA RIGA

Il prompt del 3 ottobre descrive `_authenticate` «una volta sola, prima del
ciclo» e il ruolo «congelato su `member.role`». **Non è più così**: la
rivalutazione a ogni scrittura è arrivata il 30 settembre ed è committata
(`51bc4ca`), con il costo misurato su una raffica di 112 e la distinzione fra
revoca e declassamento. Tre dei cinque cancelli erano già verdi.

Questo file chiude i due che mancavano — **la presenza di un revocato** e **la
direzione opposta** — e aggiunge quello che il cruscotto ha bisogno che sia
vero.

## E LA COSA CHE LA MISURA HA TROVATO

Il roster diceva, nel proprio commento, che «chi c'è e chi può scrivere sono una
domanda sola». Misurato: la riga di una revocata era

    {"author": …, "state": "in", "role": null}

— e `role: null` è indistinguibile da «il ruolo non lo so». Chi legge doveva
tenersi in casa la tabella dei ruoli, cioè una copia di `Role.can_write` che si
allinea oggi e diverge il giorno che ne arriva un quinto. Adesso il roster
risponde.
"""

from __future__ import annotations

import json
import pathlib
import re
import time

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from app import main as main_module                        # noqa: E402
from app import roomview                                   # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.access import Acl, Role                            # noqa: E402
from app.wire import WIRE                                   # noqa: E402

from tests import sorgenti                                  # noqa: E402

from tests.test_chi_ce_e_chi_non_ce_piu import (            # noqa: E402,F401
    ANNA, CARLO, ELISA, ROOM, T1, _document, _drain_join, _fino_a, _op,
    acls, client, relay, silenzio, whoever)

_REPO = pathlib.Path(__file__).resolve().parent.parent
SENTINELLA = {"v": WIRE, "type": "request_snapshot", "source": "test",
              "payload": {}}


def _tutto(socket, limite=12):
    """Ogni frame in coda, fino alla risposta della sentinella esclusa.

    `_fino_a` legge fino a UN frame e lascia in coda quello che viene dopo —
    compresa la risposta alla propria sentinella, che poi fa inciampare la
    lettura successiva. Quando le domande su un giro sono più d'una, si prendono
    tutti i frame e si guarda l'insieme."""
    socket.send_json(SENTINELLA)
    fuori = []
    for _ in range(limite):
        frame = socket.receive_json()
        if frame["type"] == "snapshot":
            return fuori
        fuori.append(frame)
    return fuori


def _di_tipo(frames, tipo):
    return [f["payload"] for f in frames if f["type"] == tipo]


# ═══ PARTE UNO · §3.4 — la presenza di un revocato ═══════════════════════════

def test_UN_REVOCATO_E_ANCORA_DENTRO_e_il_roster_dice_che_NON_SCRIVE(
        relay, whoever, client, acls):
    """Cosa fa oggi, e cosa deve fare — misurato e poi deciso.

    **Resta `in`, ed è giusto**: è ancora connesso e sta ancora leggendo, e
    scriverlo `out` direbbe una cosa falsa a chi guarda la lista — quella
    persona *è* nella stanza. Buttare fuori un revocato è una domanda diversa
    (e la risposta è nel referto: la lettura non passa dal cancello, quindi
    chiuderla vorrebbe dire chiudere il socket, che è un'altra decisione).

    Quello che il roster **deve** dire, e prima di stanotte non diceva, è che
    quella persona non può più scrivere.
    """
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        acls.put(ROOM, Acl(owner=ANNA, members={CARLO: "editor"}).as_dict())
        elisa.send_json(_op("dopo la revoca", "2026-09-26T11:00:00Z"))
        frames = _tutto(elisa)

    [roster] = _di_tipo(frames, "presence")
    [riga] = [m for m in roster["members"] if m["author"] == ELISA]
    assert riga["state"] == "in", "è ancora nella stanza, e sta leggendo"
    assert riga["role"] is None
    assert riga["can_write"] is False, (
        "il roster diceva solo `role: null`, che è indistinguibile da «il "
        "ruolo non lo so»")


def test_E_SENZA_can_write_IL_ROSTER_NON_SA_RISPONDERE(relay, whoever, client,
                                                       acls):
    """La gemella della rottura, e misura **l'effetto**: senza quel campo, per
    sapere chi può scrivere bisogna tenersi in casa la tabella dei ruoli."""
    whoever(CARLO)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as carlo:
        _drain_join(carlo)
        stanza = ws_module.ROOMS.peek(ROOM)
        [membro] = list(stanza.members.values())
        riga = membro.as_presence()
    senza = {k: v for k, v in riga.items() if k != "can_write"}
    # con il campo: una domanda sola. Senza: due, e la seconda la deve
    # rispondere chi legge, con una copia di `Role.can_write`.
    assert riga["can_write"] is True
    assert "editor" == senza["role"]
    assert not any(k for k in senza if "write" in k), (
        "questa è la riga di prima: dice il nome del ruolo e non la risposta")


def test_ANCHE_CHI_E_USCITO_porta_la_risposta(relay, whoever, client):
    """Il terzo stato la porta anche lui: «chi era e cosa poteva fare» è la
    stessa domanda su una riga che non si può più interrogare."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
    uscite = ws_module.ROOMS.peek(ROOM).departures()
    assert uscite and uscite[0]["can_write"] is True


# ═══ PARTE UNO · §3.5 — la direzione opposta ═════════════════════════════════

def test_CHI_RICEVE_DIRITTI_MENTRE_E_DENTRO_puo_scrivere_SUBITO(
        relay, whoever, client, acls):
    """Un ricontrollo che funziona in una direzione sola è un ricontrollo a
    metà. Misurato: `viewer` alla porta, rifiutata, poi `editor`, e scrive —
    senza riconnettersi e senza rifirmare."""
    acls.put(ROOM, Acl(owner=ANNA, members={ELISA: "viewer"}).as_dict())
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        porta = _drain_join(elisa)
        assert porta["can_write"] is False and porta["role"] == "viewer"

        elisa.send_json(_op("da viewer", "2026-09-26T10:00:00Z"))
        primo = _tutto(elisa)
        [rifiuto] = _di_tipo(primo, "denied")
        assert "read-only" in rifiuto["reason"]
        assert not _di_tipo(primo, "access_changed"), (
            "niente è cambiato: annunciarlo sarebbe rumore")

        acls.put(ROOM, Acl(owner=ANNA, members={ELISA: "editor"}).as_dict())
        elisa.send_json(_op("da editor", "2026-09-26T11:00:00Z"))
        secondo = _tutto(elisa)

    [cambio] = _di_tipo(secondo, "access_changed")
    assert cambio["change"] == "changed"
    assert (cambio["was"], cambio["role"]) == ("viewer", "editor")
    assert cambio["can_write"] is True
    [esito] = _di_tipo(secondo, "op_result")
    assert esito["applied"] is True, "e il lavoro passa, non solo l'annuncio"
    documento = json.dumps(ws_module.ROOMS.peek(ROOM).document,
                           ensure_ascii=False)
    assert "da editor" in documento and "da viewer" not in documento


def test_UN_PROMOSSO_APPARE_come_scrittore_nel_roster(relay, whoever, client,
                                                      acls):
    """…e chi guarda la lista lo vede senza doverglielo chiedere."""
    acls.put(ROOM, Acl(owner=ANNA, members={ELISA: "viewer"}).as_dict())
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        acls.put(ROOM, Acl(owner=ANNA, members={ELISA: "editor"}).as_dict())
        elisa.send_json(_op("adesso sì", "2026-09-26T11:00:00Z"))
        frames = _tutto(elisa)
    [roster] = _di_tipo(frames, "presence")
    [riga] = [m for m in roster["members"] if m["author"] == ELISA]
    assert (riga["role"], riga["can_write"]) == ("editor", True)


# ═══ PARTE UNO · §3.2 — il costo, sulle DUE forme ════════════════════════════

def test_IL_COSTO_DELLE_DUE_FORME_per_operazione_e_per_lotto(relay, whoever,
                                                             client, capsys):
    """Quando ricontrollare: a ogni operazione, o all'inizio di un lotto?

    **Le due forme non sono due opzioni della stessa cosa**, ed è la ragione per
    cui la risposta è diversa nelle due:

    * sul **socket** ogni `op` è un atto suo, arrivato in un momento suo, e fra
      due frame l'ACL può cambiare davvero. Si ricontrolla a ogni operazione.
    * in un **lotto** (`apply_from_connector`) le 112 operazioni sono UN atto —
      il delta di un lavoro — applicate sotto un lock solo. Ricontrollare a metà
      vorrebbe dire applicarne mezzo, e «uno snapshot preso fra due operazioni
      di un delta sarebbe un grafo che nessuno ha scritto» è la frase che quella
      funzione ha già scritta sopra. Si autorizza **l'atto**, alla porta REST.

    Il costo dice che la prima non è cara; l'argomento dice che la seconda non
    sarebbe più sicura.
    """
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        stanza = ws_module.ROOMS.peek(ROOM)
        [membro] = list(stanza.members.values())

        t0 = time.perf_counter()
        for _ in range(112):
            ws_module._role_now(stanza, membro)
        per_operazione = time.perf_counter() - t0

        t0 = time.perf_counter()
        ws_module._role_now(stanza, membro)
        per_lotto = time.perf_counter() - t0

    print(f"\n  112 controlli, uno per operazione ... "
          f"{per_operazione * 1000:7.3f} ms  "
          f"({per_operazione / 112 * 1000:.4f} ms/op)")
    print(f"  1 controllo, uno per lotto ......... "
          f"{per_lotto * 1000:7.3f} ms  "
          f"(112 operazioni sotto un lock solo)")
    print(f"  differenza ......................... "
          f"{(per_operazione - per_lotto) * 1000:7.3f} ms")

    assert per_operazione / 112 < 0.005, (
        f"il ricontrollo per operazione costa "
        f"{per_operazione / 112 * 1000:.3f} ms: non è più trascurabile, ed è "
        f"un fatto da scrivere invece che da forzare")


def test_IL_LOTTO_SI_AUTORIZZA_ALLA_PORTA_e_non_dentro_il_lock():
    """La forma per lotto, letta nel codice: la porta REST risolve il ruolo una
    volta, e `apply_from_connector` non ne sa niente — di proposito."""
    porta = (_REPO / "app" / "main.py").read_text(encoding="utf-8")
    blocco = porta[porta.index("async def apply_ops("):]
    blocco = blocco[:blocco.index("class ArchiveIn")]
    assert "_acting_role(room_id, request)" in blocco
    assert "role is None or not role.can_write" in blocco

    relay_src = (_REPO / "app" / "ws.py").read_text(encoding="utf-8")
    lotto = relay_src[relay_src.index("async def apply_from_connector("):]
    lotto = lotto[:lotto.index("async def _send(")]
    codice = "\n".join(riga for riga in lotto.split("\n")
                       if not riga.strip().startswith("#"))
    assert "_role_now" not in codice and "authorize(" not in codice, (
        "il lotto ha cominciato a ricontrollare dentro il lock: applicherebbe "
        "mezzo delta, che è un grafo che nessuno ha scritto")


# ═══ PARTE DUE · cosa è cambiato, e il suo confine ═══════════════════════════

def _stanza_con_registro(tmp_path):
    """Una stanza con un registro DUREVOLE, che è la condizione di §4."""
    from app.oplog import Journal
    from app.rooms import Room

    stanza = Room(ROOM, _document(),
                  journal=Journal(str(tmp_path / "registro.jsonl")))
    return stanza


def test_LE_DUE_PAROLE_VIAGGIANO_anche_quando_la_risposta_e_completa(tmp_path):
    """`readable_from` e `replayable_from` ci sono sempre: chi guarda deve poter
    sapere fin dove la stanza sa guardare indietro **senza doverlo dedurre da un
    elenco corto**."""
    stanza = _stanza_con_registro(tmp_path)
    for k in range(3):
        op = {"op": "update_field", "node_id": "US1", "field": "description",
              "value": f"v{k}", "ts": f"2026-09-26T10:0{k}:00Z",
              "author": ANNA}
        stanza.apply(op, None)
        stanza.record(op)

    # …dall'interno della finestra: completa
    fuori = roomview.changes_since(stanza, "2026-09-26T10:00:30Z")
    assert fuori["durable"] is True
    assert fuori["readable_from"] == "2026-09-26T10:00:00Z"
    assert fuori["replayable_from"] == "2026-09-26T10:00:00Z"
    assert fuori["complete"] is True and fuori["horizon"] is None
    assert fuori["operations"] == 2


def test_UNA_STANZA_CHE_NON_ARRIVA_COSI_INDIETRO_LO_DICE(tmp_path):
    """Il caso che il prompt chiede per nome: cosa legge chi guarda più indietro
    di quanto la stanza sappia.

    **Non un elenco vuoto e non un elenco corto che sembra completo.** Un
    cruscotto che mostrasse «tre unità nuove» senza dire che oltre quel punto
    non sa più starebbe dichiarando una memoria che non ha."""
    stanza = _stanza_con_registro(tmp_path)
    for k in range(3):
        op = {"op": "update_field", "node_id": "US1", "field": "description",
              "value": f"v{k}", "ts": f"2026-09-26T10:0{k}:00Z", "author": ANNA}
        stanza.apply(op, None)
        stanza.record(op)

    # …si chiede da PRIMA di quello che il registro tiene
    fuori = roomview.changes_since(stanza, "2020-01-01T00:00:00Z")
    assert fuori["complete"] is False, "il confine si dichiara"
    assert fuori["readable_from"] == "2026-09-26T10:00:00Z"
    assert fuori["operations"] == 3, "e quello che sa, lo dà lo stesso"
    # …e con la RAGIONE: questo registro non ha buttato niente, comincia lì.
    # «Ho dimenticato» e «non ho mai saputo» sono due frasi diverse.
    assert fuori["horizon"] == "began"


def test_UN_REGISTRO_AL_SUO_TETTO_dice_di_AVER_BUTTATO(tmp_path):
    """L'altra ragione, e si distingue: qui delle righe ci sono state."""
    from app.oplog import Journal
    from app.rooms import Room

    stanza = Room(ROOM, _document(),
                  journal=Journal(str(tmp_path / "r.jsonl"), keep=4, slack=0))
    for k in range(9):
        stanza.record({"op": "update_field", "node_id": "US1",
                       "field": "description", "value": str(k),
                       "ts": f"2026-09-26T10:{k:02d}:00Z", "author": ANNA})
    fuori = roomview.changes_since(stanza, "2020-01-01T00:00:00Z")
    assert fuori["complete"] is False
    assert fuori["horizon"] == "pruned", (
        "il registro è al suo tetto: righe più vecchie ci sono state e sono "
        "state buttate")


def test_SENZA_CURSORE_non_si_finge_di_sapere_tutto(tmp_path):
    """«Tutto» sarebbe il documento, e il documento non è una notizia."""
    stanza = _stanza_con_registro(tmp_path)
    fuori = roomview.changes_since(stanza, None)
    assert fuori["operations"] == 0
    assert fuori["complete"] is False
    assert "readable_from" in fuori


def test_UNA_STANZA_SENZA_REGISTRO_lo_dichiara(tmp_path):
    """Non «non è cambiato niente»: **non lo so**. Sono due frasi diverse e la
    prima è una bugia."""
    from app.rooms import Room

    stanza = Room(ROOM, _document())          # nessun journal
    fuori = roomview.changes_since(stanza, "2020-01-01T00:00:00Z")
    assert fuori["durable"] is False
    assert fuori["readable_from"] is None


def test_LEGGERE_NON_E_RIGIOCARE_e_le_due_finestre_sono_DIVERSE(tmp_path):
    """Il confine che `_two_memories` nomina, misurato: dopo una compattazione
    la parte più vecchia **non si rigioca** e **si legge**."""
    stanza = _stanza_con_registro(tmp_path)
    for k in range(4):
        op = {"op": "update_field", "node_id": "US1", "field": "description",
              "value": f"v{k}", "ts": f"2026-09-26T10:0{k}:00Z", "author": ANNA}
        stanza.apply(op, None)
        stanza.record(op)
    stanza.compacted_upto = "2026-09-26T10:02:00Z"

    fuori = roomview.changes_since(stanza, "2026-09-26T09:00:00Z")
    assert fuori["readable_from"] == "2026-09-26T10:00:00Z"
    assert fuori["replayable_from"] == "2026-09-26T10:02:00Z"
    assert fuori["readable_from"] < fuori["replayable_from"], (
        "le due parole sono due cose: una si può applicare, l'altra si può "
        "solo raccontare")
    assert fuori["operations"] == 4, "e il racconto arriva più indietro"


def test_OGNI_NUMERO_SI_RIFA_A_MANO(tmp_path):
    """§9 del prompt: «un conteggio che nessuno può rifare a mano è un'opinione
    con un carattere monospaziato».

    Qui il conto si rifà: si aprono le righe del registro con `ts > since` e si
    contano. Nessun indice, nessuna cache, nessun numero che venga da un'altra
    parte."""
    stanza = _stanza_con_registro(tmp_path)
    atti = [("US1", ANNA, "10:00"), ("US1", ANNA, "10:01"),
            ("US2", CARLO, "10:02"), ("US2", CARLO, "10:03"),
            ("US2", ANNA, "10:04")]
    for node_id, chi, quando in atti:
        op = {"op": "update_field", "node_id": node_id, "field": "description",
              "value": quando, "ts": f"2026-09-26T{quando}:00Z", "author": chi}
        stanza.apply(op, None) if node_id == "US1" else None
        stanza.record(op)

    since = "2026-09-26T10:01:30Z"
    fuori = roomview.changes_since(stanza, since)

    # …e adesso lo stesso conto, a mano, sul file
    righe = [op for op in stanza.journal.read()
             if str(op.get("ts") or "") > since]
    assert fuori["operations"] == len(righe) == 3
    assert fuori["by_author"] == {CARLO: 2, ANNA: 1}
    assert [u["id"] for u in fuori["units"]] == ["US2"]
    assert fuori["units"][0]["operations"] == 3


def test_I_VERBI_SI_LEGGONO_in_una_lingua_che_qualcuno_rilegge(tmp_path):
    """«creata, corretta ×3» è quello che qualcuno rilegge; `add_node,
    update_field` è quello che qualcuno deve tradurre."""
    stanza = _stanza_con_registro(tmp_path)
    stanza.record({"op": "add_node", "id": "US9", "ts": "2026-09-26T10:00:00Z",
                   "author": ANNA})
    for k in range(3):
        stanza.record({"op": "update_field", "node_id": "US9",
                       "field": "description", "value": str(k),
                       "ts": f"2026-09-26T10:1{k}:00Z", "author": ANNA})
    fuori = roomview.changes_since(stanza, "2026-09-26T09:00:00Z")
    [unita] = fuori["units"]
    detto = {w["said"]: w["times"] for w in unita["what"]}
    assert detto == {"corretta": 3, "creata": 1}


def test_UN_VERBO_SCONOSCIUTO_passa_come_e(tmp_path):
    """Inventargli un nome sarebbe peggio del gergo."""
    stanza = _stanza_con_registro(tmp_path)
    stanza.record({"op": "un_verbo_nuovo", "id": "US9",
                   "ts": "2026-09-26T10:00:00Z", "author": ANNA})
    fuori = roomview.changes_since(stanza, "2026-09-26T09:00:00Z")
    assert fuori["units"][0]["what"][0]["said"] == "un_verbo_nuovo"


def test_CHANGES_NON_SCRIVE_NIENTE(tmp_path):
    """`tests/test_write_paths.py` tiene le vie di scrittura a due, e questa non
    è una terza. Qui la prova sul comportamento: il documento e il registro sono
    identici prima e dopo."""
    stanza = _stanza_con_registro(tmp_path)
    for k in range(3):
        stanza.record({"op": "update_field", "node_id": "US1",
                       "field": "description", "value": str(k),
                       "ts": f"2026-09-26T10:0{k}:00Z", "author": ANNA})
    prima_doc = json.dumps(stanza.document, sort_keys=True)
    prima_reg = pathlib.Path(stanza.journal.path).read_bytes()

    roomview.changes_since(stanza, "2026-09-26T09:00:00Z")

    assert json.dumps(stanza.document, sort_keys=True) == prima_doc
    assert pathlib.Path(stanza.journal.path).read_bytes() == prima_reg


# ═══ PARTE DUE · il numero e il suo buco ═════════════════════════════════════

def test_OGNI_NUMERO_HA_IL_SUO_BUCO_NELLA_STESSA_RISPOSTA():
    """La legge di questa dashboard, e il cancello di accettazione della notte.

    Il buco sta nella **stessa risposta** dei conteggi perché «un numero senza
    il suo buco accanto invita a guardare il numero»."""
    documento = {"graphs": {"g": {"graph_id": "g", "nodes": [
        {"id": "US1", "node_type": "US", "name": "US1",
         "data": {"created_by": ANNA}},
        {"id": "US2", "node_type": "US", "name": "US2", "data": {}},
        {"id": "E1", "node_type": "EpochNode", "name": "Fase 1", "data": {}},
    ], "edges": [
        {"edge_type": "has_first_epoch", "source": "US1", "target": "E1"},
    ]}}}
    stats = roomview.statistics(documento)
    buchi = stats["holes"]

    # numero            → buco, nella stessa risposta
    assert stats["units"] == 2
    assert buchi["units_without_epoch"]["count"] == 1        # US2
    assert buchi["units_without_relations"]["count"] == 1    # US2
    assert stats["nodes"] == 3
    assert buchi["nodes_without_author"]["count"] == 2       # US2, E1
    # …e il buco porta GLI ID, non solo il numero: un conteggio senza i nomi è
    # una colpa senza un'azione.
    assert buchi["nodes_without_author"]["ids"] == ["US2", "E1"]
    # …e quello che NON si può contare è dichiarato invece che taciuto
    assert buchi["cannot_count"], "il buco che non si conta si dice"


def test_IL_BUCO_DI_by_author_SI_RIFA_A_MANO():
    """`nodes - somma(by_author)` deve tornare, sennò i due numeri guardano cose
    diverse e la sottrazione a mano non torna — che è il modo in cui un
    cruscotto smette di essere controllabile."""
    documento = {"graphs": {"g": {"graph_id": "g", "nodes": [
        {"id": f"US{k}", "node_type": "US", "name": f"US{k}",
         "data": ({"created_by": ANNA} if k % 2 else {})} for k in range(6)
    ], "edges": []}}}
    stats = roomview.statistics(documento)
    firmati = sum(stats["by_author"].values())
    assert stats["nodes"] - firmati == \
        stats["holes"]["nodes_without_author"]["count"]


# ═══ PARTE DUE · la superficie ═══════════════════════════════════════════════

WORK = (_REPO / "app" / "rooms_ui" / "work" / "index.html").read_text(
    encoding="utf-8")
SCRIPT = (_REPO / "app" / "rooms_ui" / "rooms.js").read_text(encoding="utf-8")
CSS = (_REPO / "app" / "rooms_ui" / "rooms.css").read_text(encoding="utf-8")
I18N = (_REPO / "app" / "node_admin" / "i18n.js").read_text(encoding="utf-8")


def _board_block() -> str:
    inizio = SCRIPT.index("// ── IL CRUSCOTTO DELLA STANZA")
    return SCRIPT[inizio:SCRIPT.index("// ── CONDIVIDI · un luogo solo")]


def test_il_cruscotto_esiste_e_disegna_le_cinque_cose():
    """Un recinto attorno a niente passerebbe per sempre."""
    assert 'id="zone-board"' in WORK
    for host in ("board-who", "board-waiting", "board-changes", "board-people",
                 "board-numbers", "board-horizon"):
        assert f'id="{host}"' in WORK, host
    assert "async function loadBoard(" in SCRIPT


def test_LA_PAGINA_COMPONE_E_NON_CALCOLA():
    """«Un conteggio che nessuno può rifare a mano è un'opinione con un
    carattere monospaziato.» Nessun numero nasce qui: arrivano tutti da una
    rotta che li ha contati sul grafo o sul registro."""
    codice = _board_block()
    for rotta in ("/who", "/waiting?subject=me", "/statistics", "/operators",
                  "/changes"):
        assert rotta in codice, rotta
    # …e nessuna aritmetica sui conteggi: niente somme, niente percentuali
    aritmetica = re.findall(r"(?:count|operations|units|nodes|edges)\s*[-+/*]\s*\w",
                            re.sub(r"//[^\n]*", "", codice))
    assert not aritmetica, f"il cruscotto sta calcolando: {aritmetica}"


def test_IL_LIMITE_STA_IN_CIMA_e_non_in_una_nota_a_pie_di_pagina():
    """`readable_from` è la condizione di tutto quello che segue."""
    zona = WORK[WORK.index('id="zone-board"'):]
    zona = zona[:zona.index("</section>")]
    assert zona.index('id="board-horizon"') < zona.index('id="board-who"')
    assert "horizonLine" in _board_block()
    assert "board.horizon.short" in _board_block(), (
        "il caso di chi chiede da più indietro di quanto la stanza sappia")


#: Come si riconosce un grafico, e **non è una parola**.
#:
#: La prima versione cercava `canvas`, `chart`, `<svg`, `donut`, `sparkline`,
#: `d3.` nel testo, ed è scattata su **`pie` dentro «ripiego»** — nona volta in
#: questo ecosistema. Togliere `pie` non chiudeva niente: `chart` sta dentro
#: `charter`, `canvas` dentro un URL, `d3.` dentro un numero di versione.
#:
#: Adesso si guarda **cosa il codice fa**: crea un elemento, istanzia una
#: libreria, la importa, o scrive un `<svg` in una stringa. Sono quattro forme
#: che una parola dentro un'altra parola non può assumere.
DISEGNA = (
    ("crea un <canvas>", lambda c: sorgenti.crea_elemento(c, "canvas")),
    ("crea un <svg>", lambda c: sorgenti.crea_elemento(c, "svg")),
    ("scrive un <svg> in una stringa",
     lambda c: sorgenti.markup_letterale(c, "svg")),
    ("istanzia Chart", lambda c: sorgenti.costruisce(c, "Chart")),
    ("istanzia ApexCharts", lambda c: sorgenti.costruisce(c, "ApexCharts")),
    ("importa una libreria di grafici",
     lambda c: any(sorgenti.importa_js(c, n)
                   for n in ("chart", "d3", "plotly", "echarts", "apexcharts"))),
    ("usa d3", lambda c: sorgenti.usa_oggetto(c, "d3")),
)


def test_NESSUN_GRAFICO():
    """Il controesempio misurato: dieci tessere di cui otto a zero, una torta
    divisa a metà fra due regioni (n=2), un istogramma di due barre alte 1. Con
    n=2 un grafico è arredamento, e queste stanze hanno spesso n=2.

    **Cerca nel programma e non nel testo** — vedi `DISEGNA` qui sopra.
    """
    codice = sorgenti.senza_prosa(_board_block())
    disegna = [nome for nome, guarda in DISEGNA if guarda(codice)]
    assert not disegna, f"il cruscotto {', '.join(disegna)}"


def test_LA_GUARDIA_DEI_GRAFICI_MORDE_ANCORA():
    """Prova 1 di 2: il caso vero, quello per cui era stata scritta."""
    for finto, atteso in (
            ('const c = document.createElement("canvas");', "crea un <canvas>"),
            ('host.innerHTML = "<svg viewBox=\'0 0 8 8\'></svg>";',
             "scrive un <svg> in una stringa"),
            ('const g = new Chart(ctx, {type: "pie"});', "istanzia Chart"),
            ('import * as d3 from "d3";', "importa una libreria di grafici"),
            ('d3.select("#x").append("g");', "usa d3"),
    ):
        disegna = [nome for nome, guarda in DISEGNA if guarda(finto)]
        assert atteso in disegna, f"non morde su: {finto}"


def test_LA_GUARDIA_DEI_GRAFICI_NON_MORDE_PIU_LA_PROSA():
    """Prova 2 di 2: il falso positivo che prima la faceva scattare.

    `ripiego` è il caso vero — è la parola che l'ha fatta scattare il 3 ottobre,
    in una riga che spiegava perché una comodità non era stata comprata — e gli
    altri tre sono la stessa classe con parole diverse.
    """
    onesti = [
        "// non è un ripiego: il server già lo sa",
        "const NOTA = 'la charter del progetto dice di no';",
        'const LOGO = "https://x.example/canvas-logo.png";',
        "// visto con d3.js e scartato, vedi il referto",
        "const versione = 'libreria 1.d3.0';",
    ]
    for riga in onesti:
        codice = sorgenti.senza_prosa(riga)
        disegna = [nome for nome, guarda in DISEGNA if guarda(codice)]
        assert not disegna, f"morde ancora su una riga onesta: {riga} → {disegna}"


def test_E_RIPIEGO_STA_DAVVERO_NEL_CODICE_e_la_guardia_tace():
    """Il caso che ha aperto il micro, sul file vero e non su un finto: la
    parola `ripiego` è nel cruscotto, in una riga di prosa che spiega una
    decisione, e la guardia non dice niente."""
    assert "ripiego" in _board_block(), (
        "la riga che ha aperto questo micro è stata tolta: rimettila o togli "
        "questo test, ma non lasciarlo verde su un caso che non esiste più")
    codice = sorgenti.senza_prosa(_board_block())
    assert not [nome for nome, guarda in DISEGNA if guarda(codice)]


def test_IL_TELEFONO_ha_la_sua_regola_e_non_nasconde_niente():
    """«La superficie si adatta, il substrato no.» Nascondere un buco su uno
    schermo piccolo sarebbe esattamente il difetto che questo cruscotto esiste
    per non avere."""
    # ── IL BLOCCO, CONTATO A GRAFFE ────────────────────────────────────────
    #
    # Era `CSS[CSS.index("@media …"):]`, cioè **fino alla fine del file**: una
    # regola scritta dopo quel blocco, o un commento che nomina `display: none`,
    # finivano dentro una guardia che credeva di guardare un blocco solo. E il
    # blocco è l'ultimo del file, quindi passava per posizione.
    #
    # E `dichiara` cerca la DICHIARAZIONE, non le due parole: `display:none`,
    # `display : none` e `display:  none !important` sono la stessa cosa, e
    # «niente display: none qui» in un commento non lo è.
    stretto = sorgenti.blocco_css(CSS, "@media (max-width: 40rem)")
    assert stretto, "il blocco del telefono non c'è più"
    assert not sorgenti.dichiara(stretto, "display", "none"), \
        "una riga sparisce sul telefono"


def test_LA_GUARDIA_DEL_TELEFONO_MORDE_ANCORA():
    """Prova 1 di 2: il caso vero — una riga nascosta sotto i 40rem."""
    finto = ("@media (max-width: 40rem) {\n"
             "  .share-note { display: none; }\n}\n")
    dentro = sorgenti.blocco_css(finto, "@media (max-width: 40rem)")
    assert sorgenti.dichiara(dentro, "display", "none")
    for grafia in ("display:none", "display : none", "display:  none !important"):
        assert sorgenti.dichiara(f".x {{ {grafia}; }}", "display", "none"), grafia


def test_LA_GUARDIA_DEL_TELEFONO_NON_GUARDA_PIU_TUTTO_IL_FILE():
    """Prova 2 di 2: i due falsi positivi.

    Un commento dentro il blocco, e una regola **dopo** il blocco — che con la
    fetta fino a fine file era indistinguibile da una regola dentro."""
    finto = ("@media (max-width: 40rem) {\n"
             "  /* qui non si usa display: none: nascondere un buco sarebbe */\n"
             "  .share-row { flex-direction: column; }\n"
             "}\n"
             "@media print { .bar { display: none; } }\n")
    dentro = sorgenti.blocco_css(finto, "@media (max-width: 40rem)")
    assert "flex-direction" in dentro, "il blocco giusto"
    assert "print" not in dentro, "e si ferma alla sua graffa"
    assert not sorgenti.dichiara(dentro, "display", "none")


def test_ZERO_COLORI_LETTERALI():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS)
    assert "var(--sg-warn)" in CSS


def test_LE_STRINGHE_DEL_CRUSCOTTO_esistono_in_en_e_in_it():
    trovate = {}
    for locale in re.finditer(r"^  (\w+): \{(.*?)^  \}", I18N, re.S | re.M):
        code = locale.group(1)
        for key, _v in re.findall(r'"(board\.[^"]+)":\s*"((?:[^"\\]|\\.)*)"',
                                  locale.group(2)):
            trovate.setdefault(code, set()).add(key)
    assert len(trovate.get("en", ())) > 25, trovate.get("en")
    assert trovate.get("en") == trovate.get("it"), (
        "en e it sono le due complete: una chiave in una sola è una frase che "
        "manca a metà delle persone")


def test_LA_DIRECTORY_DEGLI_OPERATORI_dice_da_dove_viene():
    """«Da noi l'autore lo timbra il relay dal token, non lo digita una
    persona.» È la ragione per cui questa vista è più forte di quella di
    pyarchinit, e una riga che non lo dice si legge come una tabella di
    utenti."""
    assert "board.people.whence" in _board_block()
    assert "token" in I18N[I18N.index('"board.people.whence"'):][:300].lower()
