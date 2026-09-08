#!/usr/bin/env python3
"""Il realm impara il nome che si è passato allo script.

════════════════════════════════════════════════════════════════════════════════
## IL GUASTO, MISURATO IL 7 SETTEMBRE 2026 SUL PI

`./fcn-up.sh fcn.local` fa emettere a Caddy un certificato per quel nome, e da
un'altra macchina la pagina ARRIVA: TLS a posto, CA fidata, Keycloak che
risponde. Poi il login muore così:

    We are sorry…   Invalid parameter: redirect_uri

Perché il client `em-console` ha 28 redirect URI **tutte cablate** su
`em.localhost:8443`, `localhost:8443`, `localhost:8000`, `localhost:8020` e
`localhost:5173`. Di `fcn.local` non c'è traccia, e non potrebbe essercene: quel
nome lo sceglie chi lancia lo script.

L'argomento «host primario» era applicato a metà — Caddy lo imparava, il realm
no — e `fcn-up.sh --help` lo offriva dicendo «per l'altro computer», cioè
promettendo esattamente la cosa che poi non funzionava.

## COSA FA, E COSA NON FA

**Rispecchia**, non inventa. Prende le URI che esistono già per l'autorità
canonica `em.localhost:<porta>` e ne fa una copia per la nuova autorità. I
PERCORSI restano quelli già dichiarati e rivisti — `/em/admin/*`, `/chat/*`,
`/catalog/ui/*`… — e cambia solo la parte host.

È il modo più forte di rispettare il vincolo «niente jolly nella parte host»:
non c'è nessun percorso nuovo da giudicare, e l'host è un letterale.

**Non tocca `webOrigins`.** Misurato: vale `["+"]`, che in Keycloak significa
«le origini delle redirect URI». Aggiungere una URI estende il CORS da sé, e
non c'è nessun jolly da scrivere.

**Funzione pura.** Legge un dict, ritorna un dict. Nessun Docker, nessuna rete,
nessun Keycloak — quindi si prova per intero senza far ripartire niente, che è
la ragione per cui è scritta così e non come un passo di amministrazione dopo
`up`.

**Idempotente.** Renderla due volte dà lo stesso file: le URI che già ci sono
non si duplicano.

## PERCHÉ PRIMA DI `up`, E NON DOPO

Il realm è un file importato all'avvio del container (`--import-realm`). Una
correzione via API di amministrazione dopo `up` funzionerebbe, ma vivrebbe fuori
dal file: al `--wipe` successivo il realm torna quello committato e il nome
smette di funzionare senza che nessuno abbia cambiato niente. Il file reso è
invece la stessa verità del file committato, con un'autorità in più.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Tuple

#: L'autorità canonica del dev-stack: è quella che il realm committato porta, ed
#: è quella da cui si rispecchia. Non è un default da indovinare — è scritta nel
#: file, e se un giorno non ci fosse più questo script deve DIRLO invece di
#: rendere un realm senza le URI nuove.
CANONICA = "em.localhost"

#: Un nome host valido per questo scopo: lettere, cifre, `-` e `.`, e nient'altro.
#: Deliberatamente stretto. Quello che esclude, in ordine di gravità:
#:   `*`   un jolly nella parte host è un open redirect, cioè una vulnerabilità
#:   `/`   un percorso: cambierebbe la parte che NON si vuole cambiare
#:   `:`   una porta: la porta è un argomento separato, sennò si può scrivere
#:         un'autorità che non corrisponde a quella su cui Caddy ascolta
#:   ``    vuoto
NOME_VALIDO = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")


class NomeRifiutato(ValueError):
    """Il nome passato non si può mettere in una redirect URI."""


def valida_nome(nome: str) -> str:
    """Il cancello, e morde prima di qualunque scrittura."""
    if not nome:
        raise NomeRifiutato("nome host vuoto")
    if "*" in nome:
        raise NomeRifiutato(
            f"jolly nella parte host: {nome!r}. Una redirect URI come "
            f"`https://*/em/admin/*` è un open redirect — una vulnerabilità "
            f"vera, non una scorciatoia di sviluppo. Il nome va dichiarato.")
    if "/" in nome or ":" in nome:
        raise NomeRifiutato(
            f"{nome!r} non è un nome host: togli lo schema, la porta e il "
            f"percorso (la porta è l'argomento --porta).")
    if not NOME_VALIDO.match(nome):
        raise NomeRifiutato(
            f"{nome!r} non è un nome host valido (lettere, cifre, `-`, `.`).")
    return nome


def rispecchia(uris: List[str], *, da: str, a: str) -> Tuple[List[str], List[str]]:
    """Le URI di `da` copiate per l'autorità `a`. Ritorna (tutte, aggiunte).

    L'ordine è stabile — le nuove vanno in coda, nell'ordine in cui le
    originali comparivano — perché un diff fra due realm resi deve essere
    leggibile.
    """
    prefisso_da = f"https://{da}"
    esistenti = set(uris)
    aggiunte: List[str] = []
    for u in uris:
        if not u.startswith(prefisso_da + "/"):
            continue
        nuova = f"https://{a}" + u[len(prefisso_da):]
        if nuova in esistenti or nuova in aggiunte:
            continue            #: idempotenza: già c'è
        aggiunte.append(nuova)
    return uris + aggiunte, aggiunte


def rendi(realm: Dict[str, Any], *, host: str, porta: int,
          canonica: str = CANONICA) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Il realm con le URI per `host:porta`. Non muta l'ingresso.

    Ritorna (realm, {clientId: [uri aggiunte]}). Il secondo valore è quello che
    si stampa: un'aggiunta silenziosa a un file di sicurezza non va bene.
    """
    valida_nome(host)
    if not (1 <= int(porta) <= 65535):
        raise NomeRifiutato(f"porta fuori intervallo: {porta}")

    da = f"{canonica}:{porta}"
    a = f"{host}:{porta}"
    fuori = copy.deepcopy(realm)
    aggiunte: Dict[str, List[str]] = {}

    if a == da:
        #: il nome canonico passato esplicitamente: non c'è niente da fare, e
        #: dirlo è meglio che rendere un file identico e far credere il contrario
        return fuori, aggiunte

    visto_canonica = False
    for c in fuori.get("clients", []):
        uris = c.get("redirectUris")
        if not uris:
            continue
        if any(u.startswith(f"https://{da}/") for u in uris):
            visto_canonica = True
        tutte, nuove = rispecchia(uris, da=da, a=a)
        if nuove:
            c["redirectUris"] = tutte
            aggiunte[c.get("clientId", "?")] = nuove

    if not visto_canonica:
        raise NomeRifiutato(
            f"nel realm non c'è nessuna redirect URI per l'autorità canonica "
            f"`https://{da}/`, quindi non c'è niente da cui rispecchiare. "
            f"Se le URI del dev-stack sono cambiate, questo script va "
            f"aggiornato invece di rendere un realm che sembra a posto.")

    #: E la guardia dopo, non solo prima: nessun jolly è ENTRATO nella parte
    #: host di nulla. Costa un giro sulla lista e chiude la classe di errore
    #: invece di una sua istanza.
    for c in fuori.get("clients", []):
        for u in c.get("redirectUris") or []:
            autorita = u.split("//", 1)[-1].split("/", 1)[0]
            if "*" in autorita:
                raise NomeRifiutato(
                    f"jolly nella parte host di {u!r} — il realm reso non si "
                    f"scrive")
    return fuori, aggiunte


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Rende il realm del dev-stack per un host primario.")
    ap.add_argument("--host", required=True,
                    help="il nome primario, come passato a fcn-up.sh (es. fcn.local)")
    ap.add_argument("--porta", type=int, default=8443)
    ap.add_argument("--in", dest="ingresso", default="keycloak/realm-em-dev.json")
    ap.add_argument("--out", dest="uscita", default=None,
                    help="dove scrivere; senza, stampa il percorso che userebbe")
    ap.add_argument("--zitto", action="store_true")
    a = ap.parse_args(argv)

    sorgente = pathlib.Path(a.ingresso)
    if not sorgente.is_file():
        print(f"✖ non trovo il realm: {sorgente}", file=sys.stderr)
        return 1
    realm = json.loads(sorgente.read_text())

    try:
        reso, aggiunte = rendi(realm, host=a.host, porta=a.porta)
    except NomeRifiutato as e:
        print(f"✖ {e}", file=sys.stderr)
        return 2

    if not aggiunte:
        if not a.zitto:
            print(f"· il realm già copre {a.host}:{a.porta}: niente da rendere.")
        #: 3 e non 0: chi chiama deve poter distinguere «reso» da «non serviva»
        #: senza rileggere il file.
        return 3

    destinazione = pathlib.Path(
        a.uscita or sorgente.with_suffix(f".{a.host}.json"))
    destinazione.write_text(json.dumps(reso, indent=2) + "\n")
    if not a.zitto:
        n = sum(len(v) for v in aggiunte.values())
        print(f"✔ {destinazione}")
        for client, uris in aggiunte.items():
            print(f"  client {client}: {len(uris)} redirect URI nuove")
            for u in uris:
                print(f"    + {u}")
        print(f"  ({n} in tutto; i PERCORSI sono quelli già dichiarati, "
              f"cambia solo l'host.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
