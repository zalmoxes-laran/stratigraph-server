"""Una connessione non è una presenza, e un'assenza non è un'uscita.

════════════════════════════════════════════════════════════════════════════════
## COSA È STATO MISURATO PRIMA DI SCRIVERE UNA RIGA DI CODICE

Il 30 settembre 2026, contro un uvicorn vero — non il `TestClient`, che non ha né
TCP né keepalive e misurarlo lì sarebbe misurare niente — e con un proxy che
scarta i pacchetti senza chiudere niente (la galleria):

    A · client vivo che risponde ai PONG
        PING a t=20,0 · 40,0 · 60,0 s → periodo 20,0 s esatti, ancora aperto
    B · client che riceve i PING e NON risponde
        PING a t=20,0 s → CLOSE a t=40,0 s, «keepalive ping timeout»
    C · LA GALLERIA (pacchetti scartati, socket aperti da tutti e due i capi)
        il server chiude il suo capo a t=40,0 s, il client non lo sa mai,
        e `/who` smette di dirlo seduto a **t=40,2 s**

**Il livello sotto il keepalive ce l'ha e funziona.** La premessa «la rete
sceglie al posto nostro, dodici minuti» non regge su questa configurazione, e
quello che segue non esiste per riparare quella.

Esiste per la prova A, che è il punto. Quel client sono quaranta righe di socket
grezzo che non fanno **nient'altro** che rispondere ai PONG: nessun EMStudio,
nessuna applicazione, nessuna persona. Per il relay è indistinguibile da Elisa
che sta lavorando.

> Un PONG di protocollo dimostra uno stack TCP. Lo manda il browser, o il
> sistema operativo, e lo manda anche per una scheda congelata che non disegna
> più niente da dieci minuti.

════════════════════════════════════════════════════════════════════════════════
## E LA SECONDA MISURA, CHE HA ROVESCIATO IL RESTO

L'attesa era: «un silenzioso resta nella lista e trattiene la compattazione».
Misurato prima di toccare niente, su due client e tre operazioni:

    watermark di chi TACE   2026-09-26T10:00:02Z   ← il più NUOVO di tutti
    watermark di chi PARLA  2026-09-05T19:34:43Z   ← è LUI a trattenere il GC

Il contrario. E il perché è peggio del difetto atteso: `_fanout` muoveva il
watermark **quando spediva**, e `_send` inghiottiva l'errore. Un socket rotto
veniva quindi accreditato di consegne mai avvenute — misurato su un socket che
esplode a ogni scrittura: un tentativo, zero byte, watermark avanzato lo stesso.

Il watermark è **l'argomento di sicurezza della compattazione**, e diceva una
bugia proprio su chi non stava ricevendo niente.

════════════════════════════════════════════════════════════════════════════════
## LA REGOLA DI CASA, APPLICATA QUI

Ogni guardia è dimostrata su un caso che la fa scattare, e il cancello verifica
**l'effetto della rottura**: i test del §3 spengono il silenzio (soglia
irraggiungibile) e rimisurano il danno sullo stesso giro. Se un domani
`presence.is_quiet` smettesse di essere consultato, quei test tornerebbero verdi
col silenzio spento e rossi con quello acceso — cioè direbbero esattamente
cos'è successo.
"""

from __future__ import annotations

import json
import pathlib
import re
import time

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402

from app import keeping                                    # noqa: E402
from app import main as main_module                        # noqa: E402
from app import presence                                   # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.access import Acl, InMemoryAclStore, Role         # noqa: E402
from app.rooms import Room, RoomRegistry                   # noqa: E402
from app.store import InMemorySnapshotStore                # noqa: E402
from app.wire import WIRE, envelope                        # noqa: E402

ROOM = "cantiere"
ANNA = "0000-0002-1825-0097"       # proprietaria
ELISA = "0000-0001-5109-3700"      # editor — quella che va in galleria
CARLO = "0000-0003-1415-9265"      # editor — quello che resta a scavare

T1 = "2026-09-26T09:00:00Z"


# ═══ gli attrezzi ═══════════════════════════════════════════════════════════

def _document():
    return {"header": {"format": "em.json", "version": "1.0", "owner": ANNA},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": [{"id": "US1", "node_type": "US",
                                         "name": "US1",
                                         "data": {"created_at": T1,
                                                  "created_by": ANNA}}],
                              "edges": []}},
            "active_graph_id": ROOM}


@pytest.fixture
def acls():
    store = InMemoryAclStore()
    store.put(ROOM, Acl(owner=ANNA,
                        members={ELISA: "editor", CARLO: "editor"}).as_dict())
    return store


@pytest.fixture
def relay(monkeypatch, acls):
    store = InMemorySnapshotStore()
    store.put(ROOM, _document())
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "ACL_STORE", acls)
    monkeypatch.setattr(main_module, "ROOMS", registry, raising=False)
    return registry


@pytest.fixture
def whoever(monkeypatch):
    """Chi sta parlando adesso. Stessa forma di `test_cosa_sa_dire_una_stanza`."""
    class Enforcing:
        enforcing = True

        def describe(self):
            return "keycloak"

    monkeypatch.setattr(ws_module.authenticator, "settings", Enforcing())
    monkeypatch.setattr(main_module.authenticator, "settings", Enforcing())

    def be(orcid):
        claims = {"orcid": orcid} if orcid else {}
        monkeypatch.setattr(ws_module.authenticator, "verify",
                            lambda token: claims)
        monkeypatch.setattr(ws_module.authenticator, "require_token",
                            lambda request: claims)
        monkeypatch.setattr(main_module.authenticator, "require_token",
                            lambda request: claims)
    return be


@pytest.fixture
def client():
    return TestClient(app=main_module.app)


@pytest.fixture
def silenzio(monkeypatch):
    """Accende o SPEGNE il silenzio, per misurare l'effetto della rottura.

    `on=False` non smonta niente: alza la soglia a un valore irraggiungibile,
    che è esattamente com'era il mondo prima di stanotte. È così che si
    rimisura il danno sullo stesso giro."""
    def make(*, on=True, after=30.0):
        monkeypatch.setattr(presence, "QUIET_AFTER",
                            after if on else 10 ** 9)
    return make


def _tace_da(member, secondi: float) -> None:
    """Fa **davvero** tacere un membro, spostando indietro il suo orologio.

    Indietro il suo e non avanti quello della stanza: l'orologio è monotòno e
    non si può spostare, e aspettare trenta secondi veri in una suite che ne
    dura dieci sarebbe pagare tre volte il suo costo per una cosa già misurata
    dal vivo (il referto della notte, `/who` a t=40,2 s)."""
    member.last_seen_mono -= secondi


def _drain_join(socket):
    """host_info, snapshot, presence — i tre frame che chiudono l'ingresso."""
    host = socket.receive_json()
    socket.receive_json()
    socket.receive_json()
    return host["payload"]


#: Il frame che chiude ogni attesa di questo file. Ne esiste uno perché
#: `receive_json()` **blocca**: un test che aspetta un frame che una rottura fa
#: sparire si pianta invece di diventare rosso, e un cancello che si pianta non
#: dice niente a nessuno — scoperto rompendo apposta la scopa per vedere il
#: rosso, e poi di nuovo rompendo la rivalutazione del ruolo.
_SENTINELLA = {"v": WIRE, "type": "request_snapshot", "source": "test",
               "payload": {}}


def _fino_a(socket, tipo, limite=8):
    """Legge finché non arriva un frame di quel tipo, **o la sentinella**.

    Serve perché un cambio di ruolo ne produce tre: `access_changed` a chi è
    cambiato, il roster a tutti (chi c'è e chi può scrivere sono una domanda
    sola per chi guarda la lista), e poi il rifiuto. Contare i frame a mano
    renderebbe questi test fragili rispetto a una riga di presenza in più.

    E la sentinella è la garanzia che l'attesa finisca: si manda una domanda a
    cui il server risponde SEMPRE, e se la sua risposta arriva prima di quello
    che si aspettava, quello che si aspettava non arriverà mai."""
    socket.send_json(_SENTINELLA)
    for _ in range(limite):
        frame = socket.receive_json()
        if frame["type"] == tipo:
            return frame
        if frame["type"] == "snapshot" and tipo != "snapshot":
            raise AssertionError(
                f"la risposta alla sentinella è arrivata prima di un frame "
                f"{tipo!r}: quel frame non è stato mandato")
    raise AssertionError(f"nessun frame {tipo!r} in {limite} letture")


def _op(value, ts, node_id="US1", field="description"):
    return {"v": WIRE, "type": "op", "source": "test",
            "payload": {"op": "update_field", "node_id": node_id,
                        "field": field, "value": value, "ts": ts}}


class SocketRotto:
    """La galleria dopo che il buffer si è riempito: ogni scrittura esplode."""

    def __init__(self):
        self.tentativi = 0

    async def send_text(self, _payload):
        self.tentativi += 1
        raise ConnectionResetError("broken pipe")


class SocketVivo:
    def __init__(self):
        self.consegnati = 0

    async def send_text(self, _payload):
        self.consegnati += 1


# ═══ §0 · il livello sotto, e il vincolo fra i due numeri ═══════════════════

def test_il_keepalive_del_trasporto_e_SCRITTO_non_ereditato():
    """I venti secondi del PING non sono più il default di una dipendenza.

    Erano in vigore e non li aveva scelti nessuno. Il problema non era il
    numero — quaranta secondi vanno benissimo — ma che un aggiornamento di
    uvicorn o un `--ws-ping-interval 0` in un playbook lo cambiasse in
    silenzio."""
    dockerfile = (pathlib.Path(__file__).resolve().parent.parent
                  / "Dockerfile").read_text(encoding="utf-8")
    cmd = dockerfile[dockerfile.rindex('CMD ["uvicorn"'):]
    interval = re.search(r'"--ws-ping-interval",\s*"(\d+)"', cmd)
    timeout = re.search(r'"--ws-ping-timeout",\s*"(\d+)"', cmd)
    assert interval and timeout, (
        "il comando dell'immagine non dichiara il keepalive: se torna a essere "
        "il default di uvicorn, torna a poter cambiare senza che nessuno lo sappia")
    assert int(interval.group(1)) == 20
    assert int(timeout.group(1)) == 20


def test_il_silenzio_sta_DENTRO_i_secondi_del_trasporto():
    """IL VINCOLO CHE TIENE INSIEME I TRE STATI, verificato invece che commentato.

    Il trasporto toglie un socket morto in `interval + timeout` = 40 s
    (misurato: CLOSE a t=40,0 s, `/who` a t=40,2 s). Se la soglia del silenzio
    fosse **sopra** quel numero, una rete staccata passerebbe da «dentro» a
    «uscito» senza mai attraversare «silenzioso», e i tre stati sarebbero due —
    senza che nessun test se ne accorgesse."""
    dockerfile = (pathlib.Path(__file__).resolve().parent.parent
                  / "Dockerfile").read_text(encoding="utf-8")
    cmd = dockerfile[dockerfile.rindex('CMD ["uvicorn"'):]
    trasporto = (int(re.search(r'"--ws-ping-interval",\s*"(\d+)"', cmd).group(1))
                 + int(re.search(r'"--ws-ping-timeout",\s*"(\d+)"', cmd).group(1)))
    assert presence.QUIET_AFTER < trasporto, (
        f"il silenzio scatta a {presence.QUIET_AFTER}s e il trasporto chiude a "
        f"{trasporto}s: così «uscito» arriverebbe prima di «silenzioso»")
    # …e il battito dev'esserci dentro almeno due volte, o una soglia di tre
    # battiti mancati non sarebbe tre battiti mancati.
    assert presence.BEAT_SECONDS * 2 <= presence.QUIET_AFTER


# ═══ §1 · il battito: il nodo dichiara ══════════════════════════════════════

def test_il_battito_e_una_DICHIARAZIONE_e_la_stanza_lo_annuncia(relay, whoever,
                                                                client):
    """Alla porta si sa a che ritmo battere. Una soglia che nessuno può leggere
    è una politica segreta — la stessa ragione per cui viaggiano quelle della
    rete dei salvataggi."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        host = _drain_join(elisa)
        assert host["presence"] == {"every_seconds": presence.BEAT_SECONDS,
                                    "quiet_after_seconds": presence.QUIET_AFTER,
                                    "verb": "still_here"}


def test_QUALUNQUE_frame_e_un_segno_di_vita_non_solo_il_battito(relay, whoever,
                                                                client):
    """Chi lavora non deve battere: sta già dicendo di esserci a ogni
    operazione, e chiedergli di dirlo due volte sarebbe traffico per un fatto
    già noto."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        member = next(iter(ws_module.ROOMS.peek(ROOM).members.values()))
        _tace_da(member, 300)
        assert presence.is_quiet(member)

        elisa.send_json(_op("muro in opus", "2026-09-26T10:00:00Z"))
        elisa.receive_json()                       # il roster tornato «dentro»
        assert not presence.is_quiet(member)


def test_il_battito_non_risponde_niente(relay, whoever, client):
    """Un `ack` a ogni battito raddoppierebbe il costo del meccanismo più
    economico della stanza. Il segno di vita è già stato preso."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        member = next(iter(ws_module.ROOMS.peek(ROOM).members.values()))
        prima = member.last_seen_mono
        elisa.send_json({"v": WIRE, "type": "still_here", "source": "emstudio",
                         "payload": {}})
        # nessuna risposta: lo si dimostra facendo una domanda VERA e
        # verificando che la prima risposta sia quella e non un eco del battito
        elisa.send_json({"v": WIRE, "type": "request_snapshot", "source": "t",
                         "payload": {}})
        assert elisa.receive_json()["type"] == "snapshot"
        assert member.last_seen_mono >= prima


def test_la_presenza_si_diffonde_SOLO_quando_cambia(relay, whoever, client):
    """IL CONTO CHE SCEGLIE IL DISEGNO.

    Il battito è ~90 byte; un roster è ~1 KB. Rimandare il roster a ogni
    battito costerebbe, su dieci presenti, 60 × 10 × 1 KB = **600 KB/min** per
    non dire niente di nuovo — cento volte il battito che dovrebbe risparmiare.

    Quindi la presenza parte solo quando lo stato di qualcuno cambia, e questo
    test conta i frame per dimostrarlo."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        room = ws_module.ROOMS.peek(ROOM)
        socket_finto = SocketVivo()
        room.sockets[next(iter(room.members))] = socket_finto

        for _ in range(20):
            elisa.send_json({"v": WIRE, "type": "still_here",
                             "source": "emstudio", "payload": {}})
        elisa.send_json({"v": WIRE, "type": "request_snapshot", "source": "t",
                         "payload": {}})
        assert elisa.receive_json()["type"] == "snapshot"

        assert socket_finto.consegnati == 0, (
            f"venti battiti hanno prodotto {socket_finto.consegnati} frame di "
            f"presenza: il roster sta viaggiando per non dire niente di nuovo")


# ═══ §2 · i tre stati ═══════════════════════════════════════════════════════

def test_dentro_silenzioso_uscito_sono_TRE_e_si_leggono_in_who(relay, whoever,
                                                               client, silenzio):
    """I TRE STATI, con gli istanti, letti da `/who`.

    «C'è» e «non c'è mai stata» sono due bugie diverse: la prima la dice un
    roster su chi è in galleria, la seconda la dice su chi se n'è andato. Qui
    si legge la terza cosa, che è la verità."""
    silenzio(on=True, after=30.0)
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        room = ws_module.ROOMS.peek(ROOM)
        member = next(iter(room.members.values()))

        # ── DENTRO ──────────────────────────────────────────────────────────
        risposta = client.get(f"/v1/rooms/{ROOM}/who",
                              headers={"Authorization": "Bearer t"}).json()
        assert risposta["counts"] == {"seated": 1, "in": 1, "quiet": 0,
                                      "left": 0, "wrote_recently": 0}
        assert risposta["seated"][0]["state"] == "in"
        assert "quiet_since" not in risposta["seated"][0]

        # ── SILENZIOSO DA 00:03 ─────────────────────────────────────────────
        _tace_da(member, 187)                       # tre minuti e sette secondi
        atteso = member.last_seen
        risposta = client.get(f"/v1/rooms/{ROOM}/who",
                              headers={"Authorization": "Bearer t"}).json()
        assert risposta["counts"]["in"] == 0
        assert risposta["counts"]["quiet"] == 1
        voce = risposta["seated"][0]
        assert voce["state"] == "quiet"
        assert voce["silent_for"] == "00:03"        # «silenziosa da tre minuti»
        assert voce["quiet_since"] == atteso        # DA QUANDO, non da quando l'ho visto
        assert voce["silent_for_seconds"] >= 187
        # e il criterio viaggia con il giudizio, o il giudizio non si contesta
        assert risposta["beat"]["quiet_after_seconds"] == 30.0

    # ── USCITO ─────────────────────────────────────────────────────────────
    risposta = client.get(f"/v1/rooms/{ROOM}/who",
                          headers={"Authorization": "Bearer t"}).json()
    assert risposta["counts"] == {"seated": 0, "in": 0, "quiet": 0,
                                  "left": 1, "wrote_recently": 0}
    uscita = risposta["left"][0]
    assert uscita["author"] == ELISA
    assert uscita["left_at"]
    # «ha chiuso» e «la rete ha ceduto» sono due uscite diverse, e questa è la
    # seconda: se n'è andata già silenziosa.
    assert uscita["was_quiet"] is True


def test_chi_esce_parlando_e_chi_esce_tacendo_sono_due_uscite(relay, whoever,
                                                              client, silenzio):
    """Il caso simmetrico del precedente, che lo rende un fatto e non un caso."""
    silenzio(on=True, after=30.0)
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
    risposta = client.get(f"/v1/rooms/{ROOM}/who",
                          headers={"Authorization": "Bearer t"}).json()
    assert risposta["left"][0]["was_quiet"] is False


def test_chi_torna_non_e_anche_uscito(relay, whoever, client):
    """Una riga «uscito» accanto alla stessa persona presente sarebbe la stanza
    che dice le due cose insieme."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
    assert ws_module.ROOMS.peek(ROOM).departures()
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        risposta = client.get(f"/v1/rooms/{ROOM}/who",
                              headers={"Authorization": "Bearer t"}).json()
        assert risposta["counts"]["seated"] == 1
        assert risposta["left"] == []


def test_le_uscite_ricordate_sono_LIMITATE(relay):
    """Una lista «effimera» che cresce senza fine è uno stato durevole per
    sbaglio. Il caso che lo fa scattare: più uscite del tetto."""
    room = Room(ROOM, _document())
    for n in range(presence.DEPARTED_KEPT + 12):
        room.join(f"c{n}", SocketVivo(), f"0000-0000-0000-{n:04d}")
        room.leave(f"c{n}")
    assert len(room.departed) == presence.DEPARTED_KEPT


# ═══ §3 · le tre cose che il silenzio tocca ════════════════════════════════
#
# Il lavoro vero. Un membro che resta nella lista non è inerte.

def test_un_socket_ROTTO_non_e_piu_accreditato_di_consegne_mai_avvenute(relay):
    """LA MISURA CHE HA ROVESCIATO IL RESTO, e adesso è una guardia.

    `_send` inghiottiva l'eccezione e `_fanout` avanzava il watermark lo
    stesso. Inghiottire l'errore va bene — un relay che muore perché un client
    è caduto porta giù la stanza — ma **inghiottirlo e poi contarlo come
    riuscito** faceva del watermark una bugia proprio su chi non riceveva
    niente. E il watermark è l'argomento di sicurezza della compattazione."""
    import asyncio

    room = Room(ROOM, _document())
    vivo, rotto = SocketVivo(), SocketRotto()
    room.join("A", vivo, CARLO, role=Role.EDITOR)
    room.join("B", rotto, ELISA, role=Role.EDITOR)
    room.members["A"].watermark = room.members["B"].watermark = T1

    asyncio.run(ws_module._fanout(room, envelope(
        "op", {"ts": "2026-09-30T12:00:00Z"}, source="em-server")))

    assert rotto.tentativi == 1, "il tentativo si fa comunque"
    assert room.members["A"].watermark == "2026-09-30T12:00:00Z"
    assert room.members["B"].watermark == T1, (
        "un socket che ha esploso in scrittura è stato contato come consegnato")


def test_UN_SILENZIOSO_NON_TRATTIENE_LA_COMPATTAZIONE(relay, silenzio):
    """IL CASO COSTRUITO APPOSTA, e il numero che si muove.

    Elisa è in galleria: il suo socket esplode in scrittura, quindi — riparato
    il credito falso — il suo watermark resta fermo all'ultima consegna vera.
    Carlo scava e conferma quello che riceve.

    **L'effetto della rottura si misura sullo stesso giro**: prima col silenzio
    spento (la soglia irraggiungibile, cioè il mondo di ieri), poi acceso.

    PERCHÉ È SICURO compattare oltre il punto di Elisa: l'argomento del minimo
    protegge chi *sta ancora arrivando*, e un silenzioso non sta arrivando — o
    non riceve, o non applica. Se torna con una base più vecchia,
    `_replay_plan` gli nega la riproduzione (`unsafe`) e gli dà lo snapshot
    intero. Questa riparazione si appoggia alla guardia di ieri notte."""
    import asyncio

    def stanza():
        room = Room(ROOM, _document())
        room.join("carlo", SocketVivo(), CARLO, role=Role.EDITOR)
        room.join("elisa", SocketRotto(), ELISA, role=Role.EDITOR)
        room.members["carlo"].watermark = room.members["elisa"].watermark = T1
        # tre operazioni di Carlo: lui le riceve, Elisa no
        for k in range(3):
            asyncio.run(ws_module._fanout(room, envelope(
                "op", {"ts": f"2026-09-30T12:00:0{k}Z"}, source="em-server")))
        _tace_da(room.members["elisa"], 300)
        return room

    # ── il silenzio SPENTO: com'era ieri ───────────────────────────────────
    #
    # I valori si leggono ADESSO e si tengono da parte: `gc_watermark()` è
    # derivato, quindi richiamarlo alla fine risponderebbe con la soglia di
    # allora — misurato sbagliando, ed è il comportamento giusto di uno stato
    # che non si memorizza.
    silenzio(on=False)
    prima = stanza()
    assert presence.is_quiet(prima.members["elisa"]) is False
    prima_gc = prima.gc_watermark()
    assert prima_gc == T1, (
        "col silenzio spento la compattazione resta piantata sul watermark di "
        "chi non riceve più niente")

    # ── il silenzio ACCESO: stesso giro, riga per riga ─────────────────────
    silenzio(on=True, after=30.0)
    dopo = stanza()
    assert presence.is_quiet(dopo.members["elisa"]) is True
    dopo_gc = dopo.gc_watermark()
    assert dopo_gc == "2026-09-30T12:00:02Z", (
        "un silenzioso trattiene ancora la compattazione")

    # IL NUMERO CHE SI MUOVE, detto per intero
    assert prima_gc < dopo_gc


def test_se_TACCIONO_TUTTI_non_si_compatta(relay, silenzio):
    """Il caso al bordo, e la risposta giusta è «non si sa».

    Nessun watermark affidabile è la stessa condizione di una stanza vuota: non
    c'è niente da proteggere, ma nemmeno niente da promettere."""
    silenzio(on=True, after=30.0)
    room = Room(ROOM, _document())
    room.join("elisa", SocketRotto(), ELISA, role=Role.EDITOR)
    room.members["elisa"].watermark = T1
    _tace_da(room.members["elisa"], 300)
    assert room.gc_watermark() is None


def test_UN_SILENZIOSO_NON_E_UNO_SCRITTORE_PRESENTE(relay, silenzio):
    """LA SECONDA DELLE TRE COSE, e la decisione: **non conta**.

    Il caso che la impone è preciso. Un client che ha dichiarato `saves_itself`
    si congela: il trasporto non lo toglierà — una scheda del browser congelata
    risponde ai PONG lo stesso, misurato — quindi `covers()` resterebbe falso
    per sempre, la rete resterebbe tirata via per sempre, e la sua scheda
    resterebbe non salvata **per sempre**. È la forma del 25 settembre con un
    socket ancora aperto sopra.

    Sbagliare da questa parte costa un salvataggio (0,6–26 ms misurati);
    sbagliare dall'altra costa una scheda."""
    keeper = keeping.Keeper(ws_module._keep)
    room = Room(ROOM, _document())
    room.join("elisa", SocketVivo(), ELISA, role=Role.EDITOR)
    room.members["elisa"].saves_itself = True       # «me ne occupo io»
    room.unsaved = 7                                # …e non se n'è occupato

    # ── il silenzio SPENTO: la rete resta tirata via ───────────────────────
    silenzio(on=False)
    _tace_da(room.members["elisa"], 300)
    assert keeper.covers(room) is False, (
        "col silenzio spento la promessa di un congelato vale ancora")
    assert room.keeping()["writers_present"] == 1
    assert room.keeping()["at_risk"] is False       # …e la stanza dice di star bene

    # ── il silenzio ACCESO: la rete torna ─────────────────────────────────
    silenzio(on=True, after=30.0)
    assert keeper.covers(room) is True
    keep = room.keeping()
    assert keep["writers_present"] == 0
    # …e i due numeri insieme spiegano PERCHÉ, che è la domanda che segue
    assert keep["writers_seated"] == 1
    assert keep["at_risk"] is True


def test_la_stanza_si_TIENE_quando_lultimo_scrittore_tace(relay, whoever,
                                                          client, silenzio,
                                                          monkeypatch):
    """LA PROVA SUL CASO CHE LO FA SCATTARE: il lavoro arriva sul disco.

    Non «`covers` torna True», che è un giudizio: il documento. Con le soglie
    della rete irraggiungibili, l'unica cosa che può salvare questa stanza è
    l'aver smesso di considerare presente chi tace."""
    silenzio(on=True, after=30.0)
    keeper = keeping.Keeper(ws_module._keep, after_ops=10_000,
                            after_quiet=10_000)
    monkeypatch.setattr(ws_module, "KEEPER", keeper)
    whoever(ELISA)

    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        elisa.send_json(_op("muro in opus", "2026-09-26T10:00:00Z"))
        assert elisa.receive_json()["payload"]["applied"] is True

        room = ws_module.ROOMS.peek(ROOM)
        assert "muro in opus" not in json.dumps(
            ws_module.SNAPSHOT_STORE.get(ROOM), ensure_ascii=False)

        # Elisa si congela. Carlo entra, e il suo ingresso è il frame che fa
        # accorgere la stanza — la scopa del temporizzatore fa lo stesso lavoro
        # quando non entra nessuno (`presence.Watcher`).
        _tace_da(next(iter(room.members.values())), 300)
        whoever(CARLO)
        with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as carlo:
            _drain_join(carlo)
            carlo.send_json({"v": WIRE, "type": "still_here", "source": "t",
                             "payload": {}})
            carlo.send_json({"v": WIRE, "type": "request_snapshot",
                             "source": "t", "payload": {}})
            for _ in range(6):
                if carlo.receive_json()["type"] == "snapshot":
                    break

        assert "muro in opus" in json.dumps(
            ws_module.SNAPSHOT_STORE.get(ROOM), ensure_ascii=False), (
            "l'unico che sapeva scrivere si è congelato e il suo lavoro è "
            "rimasto solo in memoria")


def _stanza_con_registro(tmp_path=None):
    """Una stanza con o senza registro durevole, e per il resto identica."""
    def make(journal):
        room = Room(ROOM, _document(), journal=journal)
        room.join("carlo", SocketVivo(), CARLO, role=Role.EDITOR)
        room.join("elisa", SocketVivo(), ELISA, role=Role.EDITOR)
        room.members["carlo"].watermark = "2026-09-30T12:00:02Z"
        room.members["elisa"].watermark = T1        # fermo: è in galleria
        for k in range(3):
            room.record({"op": "update_field", "id": "US1",
                         "ts": f"2026-09-30T12:00:0{k}Z"})
        _tace_da(room.members["elisa"], 300)
        return room
    return make


def test_LO_SCRITTORE_SOLO_CHE_SI_CONGELA_e_la_stanza_si_salva_da_sola(
        relay, whoever, client, silenzio, monkeypatch):
    """IL CASO PER CUI ESISTE IL TEMPORIZZATORE, e l'unico che nient'altro copre.

    L'ultimo che sa scrivere si congela **mentre è solo in stanza**. Nessuno
    manda più niente, quindi nessun frame in arrivo può far accorgere la stanza;
    e il trasporto non lo toglierà, perché una scheda del browser congelata
    risponde ai PONG lo stesso (prova A del referto). Senza la scopa, la sua
    scheda resta in memoria finché non muore il processo.

    Le soglie della rete dei salvataggi sono irraggiungibili di proposito:
    l'unica cosa che può salvare questa stanza è essersi accorti del silenzio.
    """
    silenzio(on=True, after=0.05)
    keeper = keeping.Keeper(ws_module._keep, after_ops=10_000,
                            after_quiet=10_000)
    watcher = presence.Watcher(ws_module._sweep_presence, quiet_after=0.05)
    monkeypatch.setattr(ws_module, "KEEPER", keeper)
    monkeypatch.setattr(ws_module, "WATCHER", watcher)
    whoever(ELISA)

    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        elisa.send_json(_op("muro in opus", "2026-09-26T10:00:00Z"))
        assert elisa.receive_json()["payload"]["applied"] is True
        assert "muro in opus" not in json.dumps(
            ws_module.SNAPSHOT_STORE.get(ROOM), ensure_ascii=False), (
            "la rete dei salvataggi non è spenta: il test non dimostrerebbe niente")

        # …e adesso Elisa si congela. Non chiude, non parla: si ferma. Il socket
        # resta aperto, ed è precisamente il punto.
        #
        # SI ASPETTA CON UN LIMITE, e non su `receive_json()`. Un test che sta
        # in ascolto di un frame che la rottura fa sparire **si pianta invece di
        # diventare rosso**, e un cancello che si pianta non dice niente a
        # nessuno: l'ho scoperto rompendo la scopa apposta per vedere il rosso.
        for _ in range(200):
            if "muro in opus" in json.dumps(ws_module.SNAPSHOT_STORE.get(ROOM),
                                            ensure_ascii=False):
                break
            time.sleep(0.01)

    assert "muro in opus" in json.dumps(
        ws_module.SNAPSHOT_STORE.get(ROOM), ensure_ascii=False), (
        "l'unico che sapeva scrivere si è congelato da solo in stanza e il suo "
        "lavoro non è arrivato sul disco")


def test_la_scopa_non_si_lascia_RIMANDARE_da_chi_parla(relay):
    """IL DIFETTO TROVATO RIARMANDO, e la riga che lo chiude.

    Riarmare a ogni frame sembrava naturale — è quello che fa
    `keeping.Keeper`, dove la quiete è della STANZA. Qui il silenzio è di UNA
    PERSONA, e riarmare avrebbe voluto dire che ogni operazione di Carlo
    spostava più in là il momento di accorgersi che **Elisa** tace: il colpo di
    scopa non sarebbe mai arrivato proprio nella stanza dove serviva."""
    armati = []
    watcher = presence.Watcher(lambda room: None, quiet_after=999)
    watcher._arm = lambda room: (armati.append(room.room_id),
                                 watcher._timers.__setitem__(room.room_id,
                                                             object()))[0]
    room = Room(ROOM, _document())
    for _ in range(20):
        watcher.heard(room)
    assert armati == [ROOM], (
        f"venti frame hanno armato la scopa {len(armati)} volte: chi parla sta "
        f"rimandando il momento in cui ci si accorge che un altro tace")


def test_IL_REGISTRO_e_toccato_dal_silenzio_e_NON_come_previsto(relay, silenzio):
    """LA TERZA DELLE TRE COSE — e la scoperta della serata.

    ════════════════════════════════════════════════════════════════════════
    ## L'IPOTESI, E COSA HA DETTO LA MISURA

    L'ipotesi era: `replayable_from` si sposta (perché è
    `max(reaches, compacted_upto)` e `compacted_upto` adesso va più avanti),
    `readable_from` no (perché è la ritenzione del registro, che non guarda i
    membri).

    **La prima metà è vera. La seconda dipende da una cosa che non era nel
    conto: se la stanza ha un registro durevole.**

    `Room.snapshot` tronca la finestra in memoria fino al punto di
    compattazione (`self.oplog = [op for op in self.oplog if ts > before]`).
    Su una stanza **senza** journal — uno store in memoria, un portatile —
    quella finestra è l'unica memoria che esiste, quindi compattare più avanti
    **butta via più registro**, e `readable_from` si sposta con lui: da
    `12:00:00Z` a `None`, cioè la stanza smette del tutto di saper dire chi ha
    fatto cosa.

    E `readable_from` non è contabilità: `_two_memories` lo dice bene, per un
    dato archeologico è **il verbale di un disaccordo interpretativo**.

    ## PERCHÉ NON È UN DIFETTO DA RIPARARE STANOTTE

    Perché su una stanza che ha un registro durevole — cioè ogni installazione
    vera, dove `journal_for` restituisce un `Journal` accanto agli snapshot —
    `readable_from` viene da `journal.oldest()`, che è una politica di
    ritenzione (`keep` operazioni) e la compattazione non la tocca. È il caso
    misurato nel secondo test qui sotto.

    Quindi il difetto vive **solo dove il registro già dichiarava di non
    durare**, e allargarlo lì è coerente con quella dichiarazione. Scritto,
    perché una cosa scoperta e non scritta è una cosa che si riscopre.
    """
    stanza = _stanza_con_registro()

    silenzio(on=False)
    prima = stanza(journal=None)
    prima.snapshot(InMemorySnapshotStore(), gc=True)
    prima_reaches, prima_upto = prima.replay_reaches(), prima.compacted_upto

    silenzio(on=True, after=30.0)
    dopo = stanza(journal=None)
    dopo.snapshot(InMemorySnapshotStore(), gc=True)
    dopo_reaches, dopo_upto = dopo.replay_reaches(), dopo.compacted_upto

    # ── LA PARTE RIGIOCABILE si sposta in avanti: era l'ipotesi, ed è vera.
    assert prima_upto == T1
    assert dopo_upto == "2026-09-30T12:00:02Z"

    # ── LA PARTE LEGGIBILE si sposta anche lei, e questo NON era nell'ipotesi.
    assert prima_reaches == "2026-09-30T12:00:00Z"
    assert dopo_reaches is None, (
        "senza registro durevole la finestra in memoria è l'unica memoria, e "
        "compattare più avanti la butta: la stanza smette di saper dire chi ha "
        "fatto cosa")


def test_con_un_registro_DUREVOLE_la_parte_leggibile_NON_si_muove(relay,
                                                                  silenzio,
                                                                  tmp_path):
    """L'altra metà della scoperta, e quella che la rende innocua dove conta.

    Con un `Journal` accanto agli snapshot, `readable_from` viene dalla
    ritenzione del registro e non dalla finestra in memoria. La compattazione
    può andare avanti quanto vuole: **quello che si può raccontare resta lì**.

    È esattamente la ragione per cui `_two_memories` porta due nomi invece di
    uno — una si può applicare, l'altra si può solo raccontare — e qui i due si
    comportano diversamente sotto la stessa pressione, che è la prova che i due
    nomi servivano."""
    from app.oplog import Journal

    stanza = _stanza_con_registro()

    silenzio(on=False)
    prima = stanza(journal=Journal(str(tmp_path / "a.oplog.jsonl")))
    prima.snapshot(InMemorySnapshotStore(), gc=True)
    prima_reaches = prima.replay_reaches()

    silenzio(on=True, after=30.0)
    dopo = stanza(journal=Journal(str(tmp_path / "b.oplog.jsonl")))
    dopo.snapshot(InMemorySnapshotStore(), gc=True)
    dopo_reaches = dopo.replay_reaches()

    assert prima_reaches == dopo_reaches == "2026-09-30T12:00:00Z"
    # …mentre il punto di compattazione, sotto, si è mosso lo stesso
    assert dopo.compacted_upto > prima.compacted_upto


# ═══ §4 · la revoca ════════════════════════════════════════════════════════
#
# Buttare fuori un disconnesso e togliere il diritto di scrivere non c'entrano
# niente: il primo rientra quando vuole, il secondo non deve poter rientrare.

def test_LA_REVOCA_HA_EFFETTO_ALLA_PROSSIMA_SCRITTURA(relay, whoever, client,
                                                      acls):
    """`US911: DOPO la revoca`, chiuso nella parte che fa danno.

    `_authenticate` sta prima del ciclo e il ruolo si congelava su
    `member.role`: una sessione sopravviveva alla revoca del proprio accesso
    finché non scadeva il token. Adesso l'ACL si rilegge a ogni scrittura."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        host = _drain_join(elisa)
        assert host["can_write"] is True

        elisa.send_json(_op("prima della revoca", "2026-09-26T10:00:00Z"))
        assert elisa.receive_json()["payload"]["applied"] is True

        # ── LA REVOCA, davvero: Elisa esce dall'ACL ────────────────────────
        acls.put(ROOM, Acl(owner=ANNA, members={CARLO: "editor"}).as_dict())

        elisa.send_json(_op("dopo la revoca", "2026-09-26T11:00:00Z"))
        cambio = _fino_a(elisa, "access_changed")
        rifiuto = _fino_a(elisa, "denied")

    assert cambio["type"] == "access_changed"
    assert cambio["payload"]["change"] == "revoked"
    assert cambio["payload"]["was"] == "editor"
    assert cambio["payload"]["role"] is None
    assert rifiuto["type"] == "denied"

    # …E IL LAVORO NON È PASSATO. Il frame è la cortesia; questa è la guardia.
    documento = json.dumps(ws_module.ROOMS.peek(ROOM).document,
                           ensure_ascii=False)
    assert "prima della revoca" in documento
    assert "dopo la revoca" not in documento


def test_CHI_E_STATO_REVOCATO_VEDE_UNA_FRASE_non_una_rete_che_manca(relay,
                                                                    whoever,
                                                                    client,
                                                                    acls):
    """Il difetto già riparato una volta in StratiField con `SessionRefused`,
    dall'altro lato.

    Un rifiuto che non si distingue da una rete che manca è la cosa peggiore
    che si possa mandare a qualcuno: il client segnala «connessione persa», la
    persona ricarica, e la stanza si prende la colpa di una regola applicata
    correttamente. Quindi due frame e non un silenzio, e ognuno dice una cosa
    che l'altro non dice."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        acls.put(ROOM, Acl(owner=ANNA, members={CARLO: "editor"}).as_dict())
        elisa.send_json(_op("x", "2026-09-26T11:00:00Z"))
        cambio = _fino_a(elisa, "access_changed")["payload"]
        rifiuto = _fino_a(elisa, "denied")["payload"]

        # 1 · È CAMBIATO IL MONDO — non «hai sbagliato una mossa». Un client lo
        #     riceve e aggiorna l'interfaccia PRIMA di riprovare a scrivere.
        assert cambio["change"] == "revoked"
        assert cambio["can_write"] is False
        # 2 · …E COSA RESTA APERTO, detto invece che lasciato credere chiuso.
        assert cambio["still_connected"] is True

        # 3 · IL RIFIUTO nomina la revoca, e non si confonde con una stanza in
        #     sola lettura: sono due situazioni diverse per chi le legge.
        assert "withdrawn" in rifiuto["reason"]
        assert rifiuto["can_write"] is False

        # 4 · E LA CONNESSIONE È VIVA — la prova che il rifiuto NON è una rete
        #     che manca è che sullo stesso socket si continua a parlare.
        elisa.send_json({"v": WIRE, "type": "request_snapshot", "source": "t",
                         "payload": {}})
        assert _fino_a(elisa, "snapshot")


def test_un_DECLASSAMENTO_non_si_legge_come_una_revoca(relay, whoever, client,
                                                       acls):
    """Editor → viewer e editor → niente si gestiscono diversamente: il primo
    lascia la stanza aperta in lettura, il secondo la chiude. Una parola sola
    per le due obbligherebbe ogni client a indovinare."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        acls.put(ROOM, Acl(owner=ANNA,
                           members={ELISA: "viewer", CARLO: "editor"}).as_dict())
        elisa.send_json(_op("x", "2026-09-26T11:00:00Z"))
        cambio = _fino_a(elisa, "access_changed")["payload"]
        rifiuto = _fino_a(elisa, "denied")["payload"]

    assert cambio["change"] == "changed"
    assert cambio["role"] == "viewer"
    assert "read-only" in rifiuto["reason"]


def test_LA_LETTURA_DOPO_LA_REVOCA_RESTA_APERTA_ed_e_dichiarato(relay, whoever,
                                                                client, acls):
    """**COSA NON È CHIUSO**, scritto come test invece che come nota.

    La riparazione più piccola chiude la parte che fa danno — scrivere — e non
    tocca il resto: chi è dentro resta connesso e continua a **leggere** finché
    non se ne va. Un giorno qualcuno vorrà chiudere anche quello; finché non
    succede, questo test tiene la cosa detta in chiaro invece che lasciata
    credere risolta."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        acls.put(ROOM, Acl(owner=ANNA, members={CARLO: "editor"}).as_dict())
        elisa.send_json(_op("x", "2026-09-26T11:00:00Z"))
        _fino_a(elisa, "denied")

        # Revocata, e legge ancora TUTTO il documento.
        elisa.send_json({"v": WIRE, "type": "request_snapshot", "source": "t",
                         "payload": {}})
        snapshot = _fino_a(elisa, "snapshot")
        assert "US1" in json.dumps(snapshot["payload"]["doc"])


def test_il_ruolo_NON_si_rilegge_per_una_LETTURA(relay, whoever, client,
                                                 monkeypatch):
    """Il costo si paga dove serve. `request_snapshot` e `select` non toccano
    l'ACL: rileggerlo per un click di selezione sarebbe pagare la revoca su un
    verbo che non può fare danno."""
    whoever(ELISA)
    letture = []
    originale = ws_module.load_acl
    monkeypatch.setattr(ws_module, "load_acl",
                        lambda rid: (letture.append(rid), originale(rid))[1])
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        letture.clear()
        for _ in range(10):
            elisa.send_json({"v": WIRE, "type": "select", "source": "t",
                             "payload": {"node_id": "US1"}})
        elisa.send_json({"v": WIRE, "type": "request_snapshot", "source": "t",
                         "payload": {}})
        assert elisa.receive_json()["type"] == "snapshot"
    assert letture == []


def test_in_modo_SVILUPPO_la_revoca_non_rilegge_niente(relay, client,
                                                       monkeypatch):
    """Senza OIDC non ci sono identità: risolvere un ruolo contro un'identità
    che non esiste sarebbe teatro, e leggere un ACL per farlo sarebbe teatro
    che costa."""
    letture = []
    originale = ws_module.load_acl
    monkeypatch.setattr(ws_module, "load_acl",
                        lambda rid: (letture.append(rid), originale(rid))[1])
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws") as chiunque:
        _drain_join(chiunque)
        letture.clear()
        chiunque.send_json(_op("x", "2026-09-26T10:00:00Z"))
        assert chiunque.receive_json()["payload"]["applied"] is True
    assert letture == []


def test_QUANTO_COSTA_rileggere_lACL_su_una_raffica_di_112(relay, whoever,
                                                           client, capsys):
    """IL COSTO, MISURATO — perché se non fosse trascurabile cambierebbe la
    decisione, e allora sarebbe un fatto da scrivere invece che da forzare.

    Centododici operazioni: la forma di quattro schede ICCD da ventisei campi
    più le correzioni. Il numero stampato finisce nel referto."""
    whoever(ELISA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as elisa:
        _drain_join(elisa)
        inizio = time.perf_counter()
        for k in range(112):
            elisa.send_json(_op(f"campo {k}", "2026-09-26T10:00:00Z",
                                field=f"f{k}"))
            elisa.receive_json()
        raffica = time.perf_counter() - inizio

    # …e lo stesso conto senza la rilettura, per avere un termine di paragone
    room = ws_module.ROOMS.peek(ROOM)
    membro = type("M", (), {"dev_mode": False, "author": ELISA, "role": None})()
    inizio = time.perf_counter()
    for _ in range(112):
        ws_module._role_now(room, membro)
    solo_acl = time.perf_counter() - inizio

    # ── E LO STORE VERO, che non è quello di questo test ───────────────────
    #
    # «L'ACL è già in memoria, è una lettura di dizionario» vale per
    # `InMemoryAclStore`, cioè per un portatile. Un'installazione usa
    # `DirectoryAclStore`, che a ogni `get` **legge un file e lo analizza**. È
    # quello il costo da conoscere, e misurarlo solo in memoria vorrebbe dire
    # rispondere alla domanda facile.
    import tempfile
    from app.access import DirectoryAclStore

    grande = Acl(owner=ANNA,
                 members={f"0000-0002-{n:04d}-000X":
                          ("editor" if n % 3 else "viewer")
                          for n in range(24)}).as_dict()
    disco = DirectoryAclStore(tempfile.mkdtemp())
    disco.put(ROOM, grande)
    for _ in range(200):                      # scalda la cache del filesystem
        disco.get(ROOM)
    inizio = time.perf_counter()
    for _ in range(112):
        Acl.from_dict(disco.get(ROOM))
    su_disco = time.perf_counter() - inizio

    print(f"\n  112 operazioni complete .......... {raffica * 1000:7.1f} ms"
          f"   ({raffica / 112 * 1000:.3f} ms/op)"
          f"\n  di cui rilettura dell'ACL ....... {solo_acl * 1000:7.1f} ms"
          f"   ({solo_acl / 112 * 1000:.3f} ms/op)"
          f"\n  quota della rilettura ........... {solo_acl / raffica * 100:7.1f} %"
          f"\n  …e con l'ACL SU DISCO (24 membri) {su_disco * 1000:6.1f} ms"
          f"   ({su_disco / 112 * 1000:.3f} ms/op)"
          f"\n  per confronto, UNO snapshot di sarmizegetusa: 26,1 ms")

    assert su_disco / 112 < 0.005, (
        f"con l'ACL su disco la rilettura costa {su_disco / 112 * 1000:.3f} ms "
        f"per operazione: la decisione del §4 va ripresa")

    # La soglia è larga di proposito: questo non è un test di prestazione, è la
    # guardia contro un cambio di ORDINE DI GRANDEZZA — uno store di ACL su
    # rete al posto di uno in memoria, per esempio, e allora la decisione
    # andrebbe ripresa.
    assert solo_acl / 112 < 0.005, (
        f"rileggere l'ACL costa {solo_acl / 112 * 1000:.3f} ms per operazione: "
        f"non è più trascurabile, e la decisione del §4 va ripresa")
