"""Togliere — il quarto verbo, e il rifiuto che lo rende sicuro.

════════════════════════════════════════════════════════════════════════════════
## L'ASIMMETRIA, MISURATA PRIMA DI SCRIVERE UNA RIGA

    app/assets.py:48   class AssetStore(Protocol)   put · get · head
                                                    nessun delete
    tre implementazioni                             InMemory · Directory · Minio

Aggiungere si poteva quasi ovunque; togliere da nessuna parte, e l'unico posto
dove si poteva era quello dove non si dovrebbe — dentro MinIO, a mano. Il costo
non era l'ordine: era dover dire a qualcuno, la cui fotografia non doveva essere
caricata, che il nodo non sa dimenticare.

## E IL VERBO STA NEL PROTOCOLLO, NON IN UN BACKEND

MinIO potrebbe non essere più supportato per la comunità e PSNC potrebbe
proporre un proprio store. Una cancellazione scritta contro MinIO sarebbe un
debito da pagare due volte. Quindi:

* `delete` entra in `AssetStore`, e tre implementazioni lo rispondono;
* questo file **scopre le implementazioni DAL protocollo** (`runtime_checkable`
  + `issubclass`) invece di elencarle: un backend che nessuno ha ancora scritto
  è sotto il cancello prima di esistere.

## LE DUE PROPRIETÀ, che sono proprietà e non preferenze

* **idempotente** — cancellare due volte non è un errore; una pulizia che
  fallisce sul già-pulito è una pulizia che nessuno rieseguirà;
* **dice cosa ha fatto** — `removed: True` è «c'era e l'ho tolto», `False` è
  «non c'era». Due referti diversi per l'operatore, e `None` non sarebbe
  nessuno dei due.

## IL PAGLIAIO, DICHIARATO (§5)

**Nessuna prova qui legge un sorgente.** Si eseguono gli oggetti veri e si
guardano le risposte HTTP vere, quindi non c'è nessun falso positivo da
costruire: non c'è testo in cui cercare.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

pytest.importorskip("fastapi", reason="needs the [dev] extra")

from fastapi.testclient import TestClient                     # noqa: E402

from app import assets as assets_module                       # noqa: E402
from app import ws as ws_module                               # noqa: E402
from app.access import Acl, InMemoryAclStore                  # noqa: E402
from app.assets import (AssetStore, DirectoryAssetStore,       # noqa: E402
                        InMemoryAssetStore)
from app.corpus import InMemoryCorpusStore, ResidentCorpus     # noqa: E402
from app.main import app                                      # noqa: E402
from app.rooms import RoomRegistry                            # noqa: E402
from app.store import InMemorySnapshotStore                   # noqa: E402

ANNA = "0000-0002-1825-0097"
PIXELS = b"\x89PNG\r\n\x1a\n" + b"la faccia di qualcuno che non doveva esserci"
HEAD = {"Authorization": "Bearer t"}


# ═══ 1 · IL PROTOCOLLO E LE IMPLEMENTAZIONI NON DIVERGONO ════════════════════

def _implementazioni():
    """Chi implementa `AssetStore`, chiesto AL PROTOCOLLO.

    Non un elenco: `runtime_checkable` fa rispondere a `issubclass` sulla
    presenza dei metodi, che è esattamente ciò che «implementa questa
    interfaccia» significa qui. Un backend nuovo in `app/assets.py` entra da
    solo — è il cancello che varrà il giorno che arriva quello di PSNC.
    """
    return [c for _n, c in inspect.getmembers(assets_module, inspect.isclass)
            if c is not AssetStore and issubclass(c, AssetStore)]


def test_IL_PROTOCOLLO_TROVA_LE_SUE_IMPLEMENTAZIONI():
    nomi = sorted(c.__name__ for c in _implementazioni())
    assert nomi == ["DirectoryAssetStore", "InMemoryAssetStore",
                    "MinioAssetStore"], nomi
    #: …e tutte e tre rispondono a tutti e quattro i verbi
    for classe in _implementazioni():
        for verbo in ("put", "get", "head", "delete"):
            assert callable(getattr(classe, verbo, None)), (classe.__name__, verbo)


def test_E_UNO_SENZA_delete_NON_E_UN_AssetStore():
    """IL CANCELLO MORDE, su un caso costruito.

    Se `delete` non fosse nel protocollo, `issubclass` direbbe sì a uno store a
    tre verbi — e la scoperta qui sopra sarebbe una lista con un altro nome.
    """
    class MezzoStore:
        def put(self, data, media_type): ...
        def get(self, ref): ...
        def head(self, ref): ...

    assert not issubclass(MezzoStore, AssetStore)

    class StoreIntero(MezzoStore):
        def delete(self, ref): ...

    assert issubclass(StoreIntero, AssetStore)


#: I due che si possono costruire senza rete. `MinioAssetStore` non entra in
#: questa lista e non è una dimenticanza: il suo `__init__` apre una
#: connessione, e un test che la aprisse misurerebbe la rete. Il suo `delete` è
#: sotto il cancello dei metodi qui sopra, e il suo comportamento è provato
#: dallo smoke della dev-stack contro un MinIO vero.
def _costruibili(tmp_path):
    return [InMemoryAssetStore(), DirectoryAssetStore(str(tmp_path / "blob"))]


@pytest.mark.parametrize("indice", [0, 1], ids=["in-memory", "directory"])
def test_delete_E_IDEMPOTENTE_e_dice_cosa_ha_fatto(tmp_path, indice):
    store = _costruibili(tmp_path)[indice]
    ref = store.put(PIXELS, "image/png")["ref"]
    assert store.head(ref) is not None

    primo = store.delete(ref)
    assert primo == {"ref": ref, "removed": True}
    assert store.head(ref) is None
    assert store.get(ref) is None

    #: la seconda volta NON solleva, e lo dice
    secondo = store.delete(ref)
    assert secondo == {"ref": ref, "removed": False}

    #: e su qualcosa che non c'è mai stato, uguale
    mai = "sha256:" + "a" * 64
    assert store.delete(mai) == {"ref": mai, "removed": False}


@pytest.mark.parametrize("indice", [0, 1], ids=["in-memory", "directory"])
def test_E_QUELLO_CHE_RESTA_puo_essere_rimesso(tmp_path, indice):
    """Content-addressed: gli stessi byte tornano con lo stesso riferimento, e
    `created` torna True — cioè il posto è davvero libero, non solo nascosto."""
    store = _costruibili(tmp_path)[indice]
    ref = store.put(PIXELS, "image/png")["ref"]
    store.delete(ref)
    di_nuovo = store.put(PIXELS, "image/png")
    assert di_nuovo["ref"] == ref
    assert di_nuovo["created"] is True


def test_LA_DIRECTORY_NON_LASCIA_IL_SIDECAR(tmp_path):
    """`put` scrive due file: il blob e il suo `.type`.

    Un `delete` che ne togliesse uno lascerebbe uno store in cui `head` non dice
    niente e un `<digest>.type` resta per sempre. Misurato scrivendo il test
    prima: la prima versione lo lasciava.
    """
    root = tmp_path / "blob"
    store = DirectoryAssetStore(str(root))
    ref = store.put(PIXELS, "image/png")["ref"]
    prima = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert len(prima) == 2, prima

    store.delete(ref)
    dopo = [p for p in root.rglob("*") if p.is_file()]
    assert dopo == [], dopo
    #: …e nemmeno le cartelle del fan-out, che esistono per tenere una
    #: directory elencabile e non per accumularsi vuote
    assert [p for p in root.rglob("*")] == []


def test_MA_NON_TOGLIE_LA_CARTELLA_DI_UN_ALTRO(tmp_path):
    """`rmdir` e non `rmtree`: rifiuta quando c'è ancora qualcosa dentro, che è
    il controllo che si vuole e non un rischio da correre."""
    root = tmp_path / "blob"
    store = DirectoryAssetStore(str(root))
    uno = store.put(b"primo", "text/plain")["ref"]
    due = store.put(b"secondo", "text/plain")["ref"]
    store.delete(uno)
    assert store.head(due) is not None, "il secondo digest è sparito con il primo"


# ═══ 1bis · E GLI ORFANI SI CONTAVANO A ZERO ═════════════════════════════════

def test_I_DUE_STORE_LOCALI_SANNO_ELENCARE(tmp_path):
    """MISURATO L'8 OTTOBRE, prima di dare un pulsante a un orfano.

    `main._stored_digests()` chiede a uno store `refs`, `digests` o `keys` e
    ricade su un dizionario `_data`. **Nessuna implementazione aveva nessuno dei
    quattro**, quindi `orphan_assets` è stato `[]` da quando il referto esiste —
    su OGNI backend, non solo su MinIO. La console dava la colpa a MinIO, che
    era vero e incompleto.

    Contare una cosa che non si può toccare è il modo più sicuro per insegnare a
    non guardare quel numero; contarla SEMPRE ZERO è peggio, perché sembra una
    buona notizia.
    """
    for store in _costruibili(tmp_path):
        assert callable(getattr(store, "refs", None)), type(store).__name__
        uno = store.put(b"uno", "text/plain")["ref"]
        due = store.put(b"due", "text/plain")["ref"]
        assert store.refs() == sorted([uno, due]), type(store).__name__
        store.delete(uno)
        assert store.refs() == [due], type(store).__name__


def test_E_MINIO_NON_ELENCA_e_questo_e_deliberato():
    """Non un'omissione: il client saprebbe listare un bucket, questa classe non
    lo offre. Un elenco di ogni oggetto di un bucket condiviso al caricamento di
    una pagina è una domanda cara, e la regola di `storage.js` — il browser non
    indirizza lo store — resta comunque."""
    from app.assets import MinioAssetStore

    for verbo in ("refs", "digests", "keys"):
        assert not callable(getattr(MinioAssetStore, verbo, None)), verbo


def test_E_UN_SIDECAR_NON_E_UN_ASSET(tmp_path):
    """`put` scrive `<digest>` e `<digest>.type`. L'elenco salta il secondo per
    la FORMA del nome — 64 caratteri esadecimali — e non per il suffisso: è
    quello che impedisce a un file qualunque nell'albero di essere riportato
    come un asset che lo store possiede."""
    store = DirectoryAssetStore(str(tmp_path / "blob"))
    ref = store.put(PIXELS, "image/png")["ref"]
    (tmp_path / "blob" / "un-appunto.txt").write_text("non sono un digest")
    assert store.refs() == [ref]


def test_IL_REFERTO_DELLO_STORAGE_ADESSO_LI_TROVA(nodo, client):
    """L'orfano nel referto, e non una lista vuota che sembra una buona notizia."""
    orfano = nodo["store"].put(b"nessuno mi riferisce", "text/plain")["ref"]
    report = client.get("/v1/admin/storage", headers=HEAD).json()
    assert orfano in report["orphan_assets"]
    #: …e quello che le due stanze riferiscono NON è un orfano
    assert nodo["ref"] not in report["orphan_assets"]


# ═══ 2 · CANCELLARE NON È DIMENTICARE PER SBAGLIO ════════════════════════════

def _stanza(nodes):
    return {"header": {"format": "em.json", "version": "1.0"},
            "graphs": {"g": {"graph_id": "g", "nodes": nodes, "edges": []}},
            "active_graph_id": "g"}


def _risorsa(digest, name="IMG_0001.png"):
    return {"id": name.replace(".", "_"), "node_type": "resource", "name": name,
            "data": {"checksum": digest, "residency": "resident"}}


@pytest.fixture()
def nodo(tmp_path, monkeypatch):
    """Un nodo con uno store vero, due stanze, e un corpus vuoto."""
    from app import main as main_module

    store = DirectoryAssetStore(str(tmp_path / "blob"))
    ref = store.put(PIXELS, "image/png")["ref"]
    monkeypatch.setattr(main_module, "ASSET_STORE", store)

    snapshots = InMemorySnapshotStore()
    snapshots.put("scavo", _stanza([_risorsa(ref)]))
    snapshots.put("mostra", _stanza([_risorsa(ref)]))
    snapshots.put("vuota", _stanza([]))
    acls = InMemoryAclStore()
    for room in ("scavo", "mostra", "vuota"):
        acls.put(room, Acl(owner=ANNA).as_dict())
    monkeypatch.setattr(ws_module, "SNAPSHOT_STORE", snapshots)
    monkeypatch.setattr(ws_module, "ROOMS", RoomRegistry(snapshots))
    monkeypatch.setattr(ws_module, "ACL_STORE", acls)

    corpus = ResidentCorpus(InMemoryCorpusStore())
    monkeypatch.setattr(main_module, "RESIDENT", corpus)
    monkeypatch.setattr(main_module, "CORPUS_STORE", corpus.store)
    return {"store": store, "ref": ref, "snapshots": snapshots,
            "corpus": corpus}


@pytest.fixture()
def client():
    return TestClient(app)


def test_UN_DIGEST_RIFERITO_DA_DUE_STANZE_e_RIFIUTATO(nodo, client):
    """IL GUASTO PEGGIORE CHE QUESTA NOTTE POTEVA PRODURRE, reso impossibile.

    Un asset è indirizzato dal CONTENUTO: lo stesso digest lo riferiscono più
    nodi, più stanze, più studi. Toglierlo perché una scheda non lo vuole più
    significa toglierlo a tutti gli altri, in silenzio.
    """
    answer = client.delete(f"/v1/admin/assets/{nodo['ref']}", headers=HEAD)
    assert answer.status_code == 409
    detail = answer.json()["detail"]
    #: …e DICE PERCHÉ, nominando dove: il rifiuto è anche il rimedio
    assert "scavo" in detail and "mostra" in detail
    assert "still referenced" in detail
    assert "Detach it where it is used" in detail
    #: …e i byte non si sono mossi
    assert nodo["store"].head(nodo["ref"]) is not None


def test_E_QUANDO_NESSUNO_LO_RIFERISCE_si_toglie(nodo, client):
    """Le due stanze smettono di puntarci — che è l'atto DIVERSO, sul grafo — e
    solo allora il verbo dell'operatore passa."""
    for room in ("scavo", "mostra"):
        nodo["snapshots"].put(room, _stanza([]))
    answer = client.delete(f"/v1/admin/assets/{nodo['ref']}", headers=HEAD)
    assert answer.status_code == 200, answer.text
    detto = answer.json()
    assert detto == {"ref": nodo["ref"], "removed": True,
                     "rooms": [], "corpus": False}
    assert nodo["store"].head(nodo["ref"]) is None


def test_E_IL_CORPUS_CONTA(nodo, client):
    """IL BUCO CHE SERVIVA CHIUDERE.

    Il registro DTC residente è indirizzato dal contenuto e parla dei BYTE, non
    di una stanza — `_corpus_gate` ci si appoggia già. Un digest documentato lì
    e citato da nessuna stanza **non** è un orfano, e chiedere solo alle stanze
    avrebbe fatto sembrare completo un rifiuto lasciandogli esattamente una via
    d'uscita.
    """
    for room in ("scavo", "mostra"):
        nodo["snapshots"].put(room, _stanza([]))
    nodo["corpus"].store.put({"graph_id": "corpus", "nodes": [_risorsa(nodo["ref"])],
                              "edges": []})
    answer = client.delete(f"/v1/admin/assets/{nodo['ref']}", headers=HEAD)
    assert answer.status_code == 409
    assert "resident corpus" in answer.json()["detail"]
    assert nodo["store"].head(nodo["ref"]) is not None


def test_E_UN_CORPUS_CHE_NON_SI_LEGGE_dice_NO(nodo, client, monkeypatch):
    """FAIL CLOSED, come ogni altro lettore di quel registro: «non riesco a
    leggere la documentazione» non deve poter rispondere «non lo usa nessuno»."""
    from app import main as main_module

    class Rotto:
        def read(self):
            raise RuntimeError("il registro non risponde")

    for room in ("scavo", "mostra"):
        nodo["snapshots"].put(room, _stanza([]))
    monkeypatch.setattr(main_module, "RESIDENT", Rotto())
    answer = client.delete(f"/v1/admin/assets/{nodo['ref']}", headers=HEAD)
    assert answer.status_code == 409
    assert nodo["store"].head(nodo["ref"]) is not None


def test_UN_RIFERIMENTO_STORTO_non_arriva_allo_store(nodo, client):
    """Controllato prima di toccare lo store: un riferimento arriva da una URL,
    e una implementazione su directory che prendesse una stringa qualunque
    sarebbe un `../` dalla lettura del file di qualcun altro."""
    for storto in ("pippo", "sha256:zz", "sha256:" + "0" * 63, "../etc/passwd"):
        answer = client.delete(f"/v1/admin/assets/{storto}", headers=HEAD)
        assert answer.status_code in (400, 404), (storto, answer.status_code)


def test_SOLO_UN_OPERATORE_puo_chiederlo(nodo, client, monkeypatch):
    """La stessa porta di `/v1/admin/storage`, e non una seconda."""
    from app import main as main_module
    from app import operators as ops_module

    monkeypatch.setattr(ops_module, "is_operator", lambda principal: False)
    monkeypatch.setattr(main_module.authenticator, "require_token",
                        lambda request: {"orcid": ANNA})
    for room in ("scavo", "mostra"):
        nodo["snapshots"].put(room, _stanza([]))
    answer = client.delete(f"/v1/admin/assets/{nodo['ref']}", headers=HEAD)
    assert answer.status_code == 403
    assert nodo["store"].head(nodo["ref"]) is not None


def test_E_NON_C_E_UNA_RACCOLTA_AUTOMATICA(nodo, client):
    """Un digest per volta, nominato da una persona.

    Contare gli orfani è del nodo, toglierli è di qualcuno — la stessa ragione
    per cui `api.compact` si chiede e non scatta (`gc=asked`). Quindi non esiste
    nessuna rotta che ne tolga più di uno.
    """
    for verbo, percorso in (("post", "/v1/admin/assets/forget-all"),
                            ("delete", "/v1/admin/assets"),
                            ("post", "/v1/admin/storage/sweep")):
        answer = getattr(client, verbo)(percorso, headers=HEAD)
        assert answer.status_code in (404, 405), (percorso, answer.status_code)
