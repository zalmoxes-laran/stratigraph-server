"""Le quattro domande di chi si siede a un tavolo di lavoro.

*chi c'è dentro adesso · cosa aspetta me · com'è fatto quello che abbiamo ·
chi ha scritto qui.*

La quinta — «cosa è cambiato da quando non guardavo» — non è qui, e il perché è
misurato in `tests/test_il_registro_della_stanza.py`.

Ogni guardia è dimostrata su un caso che la fa scattare, e dove il difetto
avrebbe una forma precisa il test costruisce **quella forma** invece di una
generica: la prova dell'epoca, sotto, monta un grafo con l'arco che *sembra*
giusto e non esiste, e verifica che le unità risultino non datate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")
pytest.importorskip("s3dgraphy", reason="s3dgraphy not importable")

from fastapi.testclient import TestClient                  # noqa: E402

from app import main as main_module                        # noqa: E402
from app import roomview                                   # noqa: E402
from app import ws as ws_module                            # noqa: E402
from app.access import Acl, InMemoryAclStore               # noqa: E402
from app.main import app                                   # noqa: E402
from app.rooms import RoomRegistry                         # noqa: E402
from app.store import InMemorySnapshotStore                # noqa: E402

ROOM = "tavolo"
ANNA = "0000-0002-1825-0097"     # owner, e ha scritto
CARLA = "0000-0003-1415-9265"    # editor, seduta e muta
BRUNO = "0000-0001-5109-3700"    # editor, ha scritto e se n'è andato
HEAD = {"Authorization": "Bearer t"}

T_OLD = "2026-09-20T09:00:00Z"
T_NEW = "2026-09-25T09:00:00Z"


def _unit(node_id, *, by=ANNA, at=T_NEW, origin=None, authorship=None,
          clocks=None, node_type="US"):
    data = {"created_by": by, "created_at": at,
            "modified_by": by, "modified_at": at}
    if origin:
        data["origin"] = origin
    for field, mark in (authorship or {}).items():
        data[f"authorship.{field}"] = mark
    if clocks:
        data["field_clocks"] = clocks
    return {"id": node_id, "node_type": node_type, "name": node_id,
            "data": data}


def _document(nodes, edges=()):
    return {"header": {"format": "em.json", "version": "1.0", "owner": ANNA},
            "graphs": {ROOM: {"graph_id": ROOM, "name": ROOM,
                              "nodes": list(nodes), "edges": list(edges)}},
            "active_graph_id": ROOM}


@pytest.fixture
def relay(monkeypatch):
    store = InMemorySnapshotStore()
    acls = InMemoryAclStore()
    acls.put(ROOM, Acl(owner=ANNA,
                       members={CARLA: "editor", BRUNO: "editor"}).as_dict())
    registry = RoomRegistry(store)
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", store)
    monkeypatch.setattr(ws_module, "ROOMS", registry)
    monkeypatch.setattr(ws_module, "ACL_STORE", acls)

    def seed(document):
        store.put(ROOM, document)
        return registry
    return seed


@pytest.fixture
def whoever(monkeypatch):
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
    return TestClient(app)


# ═══ chi c'è dentro adesso ═══════════════════════════════════════════════════

def test_seduto_e_ha_scritto_di_recente_sono_due_liste(relay, whoever, client):
    """IL CASO CHE LO FA SCATTARE: le due liste sono diverse in **tutte e due**
    le direzioni contemporaneamente.

    * CARLA è seduta e non ha scritto niente;
    * BRUNO ha scritto due minuti fa e non è in nessun socket.

    Una lista sola direbbe «presente» di Bruno e non direbbe niente di Carla, o
    il contrario. Sono due domande."""
    relay(_document([_unit("US1", by=BRUNO, at=T_NEW)]))
    whoever(CARLA)
    with client.websocket_connect(f"/v1/rooms/{ROOM}/ws?token=t") as carla:
        for _ in range(3):
            carla.receive_json()
        answer = client.get(f"/v1/rooms/{ROOM}/who",
                            params={"since": T_OLD}, headers=HEAD).json()

    seduti = [m["author"] for m in answer["seated"]]
    scriventi = [m["author"] for m in answer["wrote_recently"]]
    assert seduti == [CARLA]
    assert scriventi == [BRUNO]
    # …e ognuna dice se l'altra la contiene, così un client può disegnare
    # l'unione senza che sia il server a decidere che sono la stessa cosa
    assert answer["seated"][0]["wrote_recently"] is False
    assert answer["wrote_recently"][0]["seated"] is False
    assert answer["counts"] == {"seated": 1, "wrote_recently": 1}


def test_senza_una_finestra_non_si_inventa_un_recente(relay, whoever, client):
    """«Le ultime 24 ore» sarebbe una politica inventata dal server. Senza
    `since` la lista è vuota e `recent_after` è `null`, che si legge «non
    l'hai chiesto» e non «nessuno ha scritto»."""
    relay(_document([_unit("US1", by=BRUNO, at=T_NEW)]))
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/who", headers=HEAD).json()
    assert answer["wrote_recently"] == []
    assert answer["recent_after"] is None


def test_la_finestra_taglia_davvero(relay, whoever, client):
    """La guardia sul caso che la fa scattare: lo stesso grafo, due finestre."""
    relay(_document([_unit("US1", by=BRUNO, at=T_OLD)]))
    whoever(ANNA)
    dentro = client.get(f"/v1/rooms/{ROOM}/who",
                        params={"since": "2026-09-19T00:00:00Z"},
                        headers=HEAD).json()
    fuori = client.get(f"/v1/rooms/{ROOM}/who",
                       params={"since": "2026-09-24T00:00:00Z"},
                       headers=HEAD).json()
    assert [m["author"] for m in dentro["wrote_recently"]] == [BRUNO]
    assert fuori["wrote_recently"] == []


# ═══ cosa aspetta me ═════════════════════════════════════════════════════════

AI_ATTESA = {"definizione": {"by": "ai", "model": "prova-1"}}
AI_VALIDATA = {"definizione": {"by": "ai", "model": "prova-1",
                               "validated_by": ANNA,
                               "validated_at": T_NEW}}
UMANO = {"definizione": {"by": "human"}}


def test_un_campo_ai_non_validato_e_un_debito_che_si_conta(relay, whoever, client):
    relay(_document([_unit("US1", authorship=AI_ATTESA,
                           clocks={"data.definizione": {"ts": T_OLD, "by": ANNA}})]))
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/waiting", headers=HEAD).json()
    assert answer["counts"] == {"units": 1, "fields": 1}
    assert answer["units"][0]["fields"][0] == {"field": "definizione",
                                               "since": T_OLD,
                                               "since_from": "field_clock",
                                               "model": "prova-1"}
    assert answer["oldest"] == T_OLD
    assert answer["oldest_from_field_clock"] is True
    assert answer["by_model"] == {"prova-1": 1}


def test_validarlo_lo_toglie_dal_conto(relay, whoever, client):
    """L'EFFETTO. Lo stesso campo, con `validated_by`: il debito è pagato e
    sparisce. Se un giorno il filtro guardasse `validated` invece di
    `validated_by`, questo diventa rosso — ed è la differenza fra «nessuno
    l'ha guardato» e «qualcuno l'ha confermato»."""
    relay(_document([_unit("US1", authorship=AI_VALIDATA)]))
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/waiting", headers=HEAD).json()
    assert answer["counts"] == {"units": 0, "fields": 0}
    assert answer["oldest"] is None


def test_da_dove_viene_il_da_quando(relay, whoever, client):
    """IL CASO CHE LO FA SCATTARE, ed è stato trovato scrivendolo.

    `add_node` timbra il NODO, non i campi. Un campo senza orologio suo eredita
    `modified_at`, che si muove ogni volta che qualcuno tocca qualunque cosa in
    quel nodo: validando un campo alle 08:30, il «da quando» di un ALTRO campo
    è saltato da 08:00 a 08:30 senza che nessuno l'avesse toccato.

    Un debito che ringiovanisce è un debito che si rimanda, quindi la risposta
    dice da dove viene la data invece di presentarne due specie con lo stesso
    nome."""
    relay(_document([_unit("US1", at=T_NEW, authorship=AI_ATTESA)]))   # niente clocks
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/waiting", headers=HEAD).json()
    campo = answer["units"][0]["fields"][0]
    assert campo["since"] == T_NEW and campo["since_from"] == "node"
    assert answer["oldest_from_field_clock"] is False


def test_un_campo_umano_non_aspetta_nessuno(relay, whoever, client):
    relay(_document([_unit("US1", authorship=UMANO)]))
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/waiting", headers=HEAD).json()
    assert answer["counts"]["fields"] == 0


def test_mine_restringe_a_chi_chiede(relay, whoever, client):
    relay(_document([_unit("US1", by=ANNA, authorship=AI_ATTESA),
                     _unit("US2", by=BRUNO, authorship=AI_ATTESA)]))
    whoever(ANNA)
    tutto = client.get(f"/v1/rooms/{ROOM}/waiting", headers=HEAD).json()
    mio = client.get(f"/v1/rooms/{ROOM}/waiting", params={"mine": "true"},
                     headers=HEAD).json()
    assert tutto["counts"]["units"] == 2 and tutto["subject"] is None
    assert mio["counts"]["units"] == 1 and mio["subject"] == ANNA
    assert mio["units"][0]["id"] == "US1"


def test_non_ce_una_chiave_refused_perche_sarebbe_una_bugia(relay, whoever, client):
    """I rifiuti non sono registrati da nessuna parte
    (`test_il_registro_della_stanza.py::test_un_rifiuto_non_lascia_traccia`).
    Una lista sempre vuota si leggerebbe «nessun rifiuto»: meglio l'assenza."""
    relay(_document([_unit("US1")]))
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/waiting", headers=HEAD).json()
    assert "refused" not in answer


# ═══ com'è fatto quello che abbiamo ══════════════════════════════════════════

def _edge(source, target, kind):
    return {"id": f"{source}__{kind}__{target}", "edge_type": kind,
            "source": source, "target": target}


def test_unita_senza_rapporti_e_un_buco_con_i_nomi(relay, whoever, client):
    relay(_document([_unit("US1"), _unit("US2"), _unit("US3")],
                    [_edge("US1", "US2", "is_after")]))
    whoever(ANNA)
    holes = client.get(f"/v1/rooms/{ROOM}/statistics",
                       headers=HEAD).json()["holes"]
    assert holes["units_without_relations"] == {"count": 1, "ids": ["US3"]}


def test_dare_un_rapporto_chiude_il_buco(relay, whoever, client):
    """L'effetto, sullo stesso grafo più un arco."""
    relay(_document([_unit("US1"), _unit("US2"), _unit("US3")],
                    [_edge("US1", "US2", "is_after"),
                     _edge("US3", "US2", "cuts")]))
    whoever(ANNA)
    holes = client.get(f"/v1/rooms/{ROOM}/statistics",
                       headers=HEAD).json()["holes"]
    assert holes["units_without_relations"]["count"] == 0


EPOCA = {"id": "EP1", "node_type": "EpochNode", "name": "Fase 1", "data": {}}


def test_le_epoche_si_contano_con_gli_archi_che_esistono(relay, whoever, client):
    relay(_document([_unit("US1"), _unit("US2"), EPOCA],
                    [_edge("US1", "EP1", "has_first_epoch"),
                     _edge("US2", "EP1", "survive_in_epoch")]))
    whoever(ANNA)
    stats = client.get(f"/v1/rooms/{ROOM}/statistics", headers=HEAD).json()
    assert stats["by_epoch"] == [{"id": "EP1", "name": "Fase 1",
                                  "first": 1, "surviving": 1}]
    assert stats["holes"]["units_without_epoch"]["count"] == 0


def test_larco_che_sembra_giusto_e_non_esiste(relay, whoever, client):
    """IL CANCELLO CHE MISURA LA ROTTURA, sulla forma esatta dell'errore.

    Scrivendo questo modulo avevo messo `is_in_epoch`, che suona come l'arco
    che data un'unità. Nel datamodel delle connessioni **1.6.13** non è un tipo
    d'arco: è la `reverse.name` di `has_sub_epoch`, cioè un'etichetta di
    lettura fra un'epoca e una sua fase. Stesso inciampo di `is_before` per
    `is_after`, in un altro repository.

    Se qualcuno lo rimettesse, ogni stanza vera direbbe «zero unità datate» con
    un numero pulito accanto. Qui il grafo è cablato **con quell'arco**, e le
    due unità devono risultare NON datate."""
    relay(_document([_unit("US1"), _unit("US2"), EPOCA],
                    [_edge("US1", "EP1", "is_in_epoch"),
                     _edge("US2", "EP1", "is_in_epoch")]))
    whoever(ANNA)
    stats = client.get(f"/v1/rooms/{ROOM}/statistics", headers=HEAD).json()
    assert stats["by_epoch"] == [{"id": "EP1", "name": "Fase 1",
                                  "first": 0, "surviving": 0}]
    assert stats["holes"]["units_without_epoch"]["count"] == 2
    assert stats["holes"]["units_without_epoch"]["no_epochs_at_all"] is False


def test_una_stanza_senza_nemmeno_unepoca_lo_dice(relay, whoever, client):
    """«Tutte le unità sono senza epoca» è vero e inutile se epoche non ce ne
    sono: il lavoro da fare è periodizzare, e si dice così."""
    relay(_document([_unit("US1"), _unit("US2")]))
    whoever(ANNA)
    hole = client.get(f"/v1/rooms/{ROOM}/statistics",
                      headers=HEAD).json()["holes"]["units_without_epoch"]
    assert hole["count"] == 2 and hole["no_epochs_at_all"] is True


def test_le_statistiche_dichiarano_cosa_non_sanno_contare(relay, whoever, client):
    """«Campi obbligatori vuoti» sarebbe il terzo buco utile e non si può
    contare da qui: `required` vive nella definizione della scheda, che non
    viaggia col grafo. Dichiarato, non taciuto."""
    relay(_document([_unit("US1")]))
    whoever(ANNA)
    holes = client.get(f"/v1/rooms/{ROOM}/statistics",
                       headers=HEAD).json()["holes"]
    assert holes["cannot_count"], "un buco che non si sa contare va detto"
    assert "required" in holes["cannot_count"][0]


def test_non_conta_i_siti(relay, whoever, client):
    """Una stanza non è un database di siti. Se un giorno comparisse un
    `sites`, è il segno che il cruscotto sbagliato è tornato dalla finestra."""
    relay(_document([_unit("US1")]))
    whoever(ANNA)
    stats = client.get(f"/v1/rooms/{ROOM}/statistics", headers=HEAD).json()
    assert "sites" not in stats
    assert set(stats["holes"]) >= {"units_without_relations",
                                   "units_without_epoch", "cannot_count"}


# ═══ chi ha scritto qui ══════════════════════════════════════════════════════

def test_gli_operatori_con_due_identita_diverse(relay, whoever, client):
    relay(_document([
        _unit("US1", by=ANNA, origin="pyarchinit-mini"),
        _unit("US2", by=ANNA, origin="pyarchinit-mini"),
        _unit("US3", by=BRUNO, at=T_OLD,
              clocks={"data.definizione": {"ts": T_OLD, "by": BRUNO},
                      "data.colore": {"ts": T_OLD, "by": BRUNO}}),
    ]))
    whoever(ANNA)
    answer = client.get(f"/v1/rooms/{ROOM}/operators", headers=HEAD).json()
    assert answer["counts"] == {"people": 2}
    anna, bruno = answer["operators"]
    assert anna["author"] == ANNA and anna["created"] == 2
    assert anna["tools"] == {"pyarchinit-mini": 2} and anna["tools_unknown"] == 0
    assert bruno["author"] == BRUNO and bruno["created"] == 1
    # I CAMPI, che è ciò che pyarchinit non può sapere: vengono dagli orologi
    # di campo, e l'identità lì dentro è quella del token.
    assert bruno["fields"] == 2
    assert bruno["first_at"] == T_OLD and bruno["last_at"] == T_OLD


def test_uno_strumento_che_non_si_dichiara_non_si_indovina(relay, whoever, client):
    """`data.origin` assente → contato come ignoto, non attribuito a un
    attrezzo plausibile. È la differenza fra questa vista e la colonna
    `schedatore` da cui pyarchinit deduce la sua."""
    relay(_document([_unit("US1", by=ANNA),
                     _unit("US2", by=ANNA, origin="emstudio")]))
    whoever(ANNA)
    anna = client.get(f"/v1/rooms/{ROOM}/operators",
                      headers=HEAD).json()["operators"][0]
    assert anna["tools"] == {"emstudio": 1}
    assert anna["tools_unknown"] == 1


# ═══ la porta ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("route", ["who", "waiting", "statistics", "operators"])
def test_chi_non_e_della_stanza_non_legge(relay, whoever, client, route):
    relay(_document([_unit("US1")]))
    whoever("0000-0009-9999-9999")      # nessun ruolo qui dentro
    answer = client.get(f"/v1/rooms/{ROOM}/{route}", headers=HEAD)
    assert answer.status_code == 403


# ═══ e nessuna di queste scrive ══════════════════════════════════════════════

def test_leggere_non_muove_il_documento(relay, whoever, client):
    """La regola del recinto, verificata sull'effetto: il digest del documento
    dopo tutte e quattro le letture è quello di prima."""
    from s3dgraphy import api as em

    registry = relay(_document([_unit("US1", authorship=AI_ATTESA)]))
    whoever(ANNA)
    prima = em.content_digest(registry.store.get(ROOM))
    for route in ("who", "waiting", "statistics", "operators"):
        assert client.get(f"/v1/rooms/{ROOM}/{route}",
                          headers=HEAD).status_code == 200
    assert em.content_digest(registry.store.get(ROOM)) == prima


def test_il_modulo_non_contiene_una_via_di_scrittura():
    """`tests/test_write_paths.py` tiene le vie a due. Questa non è una terza,
    e il cancello legge il sorgente perché è ciò che resta vero domani."""
    import pathlib
    source = pathlib.Path(roomview.__file__).read_text(encoding="utf-8")
    codice = "\n".join(l for l in source.splitlines()
                       if not l.lstrip().startswith(("#", '"', "*")))
    for vietato in ("store.put", ".apply(", ".record(", ".snapshot(",
                    "= document[", "document.update"):
        assert vietato not in codice, f"roomview scrive: {vietato}"
