#!/usr/bin/env python3
"""Quali indirizzi resi nominano una macchina che non è questo nodo.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ ESISTE, E IL MOTIVO È DI SEI ORE PRIMA

L'8 settembre 2026, sul Raspberry, dopo `./fcn-up.sh fcn.local`:

    docker exec em-dev-catalog python3 -c "… deeplink.open_targets(…)"
      web → http://localhost:5173/?study=studio-x&emjson=x

cioè la macchina di chi clicca. La riga colpevole era stata commentata in
`.env.dev.example` la notte prima, e l'export che la mascherava era stato tolto
la notte prima — entrambe le cose giuste. Ma il Pi aveva un `.env.dev` **già
esistente**, germogliato da quell'esempio quando la riga era attiva.

**Correggere il seme non tocca la pianta.** La regola sugli indirizzi pubblici
esisteva, era scritta una volta, ed era provata — ma sull'ambiente che il
repository si ASPETTA. Mancava applicata all'ambiente che su quella macchina
ESISTE, e il momento per farlo è l'avvio.

## I TRE VINCOLI, CHE SONO LA PARTE IMPORTANTE

**DICE, NON CORREGGE.** Un file di configurazione è di chi l'ha scritto. Il
giorno che uno script riscrive `.env.dev` da solo, nessuno si fida più di quello
che c'è dentro. Questo programma non apre `.env.dev` in scrittura, mai — lo
legge solo per dire a quale RIGA sta il valore che ha nominato.

**NON BLOCCA.** Ritorna sempre 0. Questi valori sono spesso legittimi su una
macchina di sviluppo: è per quello che il file esiste. Un avvertimento che
impedisce di lavorare viene disattivato entro una settimana.

**SA TACERE.** Su un avvio pulito non stampa niente e non stampa nemmeno
«tutto bene». Un avviso che compare sempre non lo legge nessuno, ed è la ragione
per cui questo difetto è arrivato fino a stamattina.

## UNA LISTA SOLA

Le esenzioni non sono qui: stanno in `x-public-addresses.exempt` dentro
`docker-compose.dev.yml`, accanto alla regola che le spiega. Le legge questo
programma e le legge la prova generale — che importa questo file invece di
reimplementarne i predicati. Due lettori, nessuna copia.

## E NIENTE PYYAML

`compose config --format json` e la libreria standard. Misurato: il `python3` di
sistema di Debian 12 non ha `yaml`, e su questi nodi non si installa niente con
pip — la regola di `platform.sh`.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

#: Uno schema che un browser aprirebbe.
_ASSOLUTO = ("http://", "https://")

#: Percorsi che NON sono indirizzi di un browser ma di un filesystem dentro il
#: container. Riconosciuti per prefisso, non per intuizione: `/chat/` sì,
#: `/srv/chatbot-data/scavo.em.json` no.
_NON_INDIRIZZI = ("/srv/", "/opt/", "/var/", "/tmp/", "/etc/", "/data",
                  "/usr/", "/home/", "/mnt/", "/run/")

#: I nomi che tradiscono un indirizzo interno alla rete di container. Il
#: suffisso è la convenzione già scritta nel codice (`app/node_health.py:645`,
#: «one is a machine we dial, the other is a name we hand out»); i tre nomi
#: espliciti sono quelli che il codice usa così senza portare il suffisso.
_INTERNI_PER_NOME = ("OIDC_JWKS_URI", "NODEODM_URL", "EM_TRANSFORMER_URL")

#: Le macchine che non sono questo nodo, scritte come compaiono.
ALTRUI = ("em.localhost", "localhost", "127.0.0.1", "0.0.0.0", "::1",
          "host.docker.internal")


def e_indirizzo(valore: str) -> bool:
    """`valore` è un indirizzo che un browser aprirebbe."""
    v = (valore or "").strip()
    if v.startswith(_ASSOLUTO):
        return True
    if v.startswith("/") and not v.startswith(_NON_INDIRIZZI):
        return True
    return False


def host_di(valore: str) -> str:
    """L'host di un URL assoluto, senza porta. "" per tutto il resto."""
    v = (valore or "").strip()
    if not v.startswith(_ASSOLUTO):
        return ""
    resto = v.split("//", 1)[1].split("/", 1)[0]
    #: IPv6 fra parentesi quadre, che ha i due punti dentro
    if resto.startswith("["):
        return resto.split("]", 1)[0].lstrip("[")
    return resto.split(":", 1)[0]


def e_interno(nome: str, valore: str, servizi) -> bool:
    """Un indirizzo della rete di container, non uno consegnato a un browser.

    Due modi di riconoscerlo, e nessuno dei due è un elenco a mano:

      · il NOME lo dichiara (`*_INTERNAL`, più i tre che il codice usa così);
      · l'HOST è un servizio di QUESTO compose — `http://minio:9000`,
        `http://keycloak:8080`. I nomi dei servizi vengono dal reso, quindi un
        servizio nuovo è coperto da sé.

    Il secondo modo è stato aggiunto eseguendo: la prima versione guardava solo
    il suffisso e inciampava su `CANTALOUPE_S3SOURCE_ENDPOINT` e
    `MINIO_ENDPOINT`, che nominano container e non macchine.
    """
    if (nome.endswith("_INTERNAL") or nome.endswith("_INTERNAL_BASE")
            or nome in _INTERNI_PER_NOME):
        return True
    return host_di(valore) in servizi


def leggi_reso(sorgente: Optional[str] = None,
               compose: Optional[List[str]] = None,
               file_compose: str = "docker-compose.dev.yml",
               profilo: str = "https") -> Dict[str, Any]:
    """Il reso, da un file/stdin già in JSON o chiedendolo al compose."""
    if sorgente == "-":
        return json.loads(sys.stdin.read())
    if sorgente:
        return json.loads(pathlib.Path(sorgente).read_text())
    cmd = list(compose or ["docker", "compose"]) + [
        "-f", file_compose, "--profile", profilo, "config", "--format", "json"]
    fuori = subprocess.run(cmd, capture_output=True, text=True)
    if fuori.returncode != 0 or not fuori.stdout.strip():
        raise RuntimeError(
            f"`{' '.join(cmd)}` non ha reso niente: {fuori.stderr.strip()[:200]}")
    return json.loads(fuori.stdout)


def esenzioni(reso: Dict[str, Any]) -> Dict[str, str]:
    """Le esenzioni dichiarate, dal compose. Una lista sola, e sta là."""
    blocco = reso.get("x-public-addresses") or {}
    voci = blocco.get("exempt") or {}
    return {str(k): str(v or "").strip() for k, v in voci.items()}


def ambiente(reso: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """Ogni variabile resa → (valore, primo servizio che la porta)."""
    fuori: Dict[str, Tuple[str, str]] = {}
    for servizio, sv in (reso.get("services") or {}).items():
        for k, v in ((sv or {}).get("environment") or {}).items():
            if v is None:
                continue
            fuori.setdefault(str(k), (str(v), servizio))
    return fuori


def provenienza(nome: str, env_file: Optional[pathlib.Path]) -> str:
    """Da dove viene quel valore: `.env.dev` riga N, o il default del compose.

    Legge SOLO. Il file non si apre in scrittura da nessuna parte di questo
    programma, e non è un dettaglio implementativo: è il primo dei tre vincoli.
    """
    if env_file and env_file.is_file():
        pat = re.compile(rf"^\s*(?:export\s+)?{re.escape(nome)}\s*=")
        try:
            for n, riga in enumerate(
                    env_file.read_text(errors="replace").splitlines(), 1):
                if pat.match(riga):
                    return f"{env_file.name} riga {n}"
        except OSError:
            pass
    #: Anche l'ambiente di shell batte l'env-file, ed è il caso in cui un export
    #: di `fcn-up.sh` sta mascherando (o causando) il valore. Dirlo cambia il
    #: rimedio: si guarda lo script, non il file.
    if nome in os.environ:
        return "l'ambiente di shell (un export di fcn-up.sh?)"
    return "il default del compose"


def sospetti(reso: Dict[str, Any], primario: str,
             env_file: Optional[pathlib.Path] = None) -> List[Dict[str, str]]:
    """I valori resi che nominano una macchina che non è `primario`.

    Un valore va bene quando è RELATIVO (segue l'host per costruzione) oppure
    quando è assoluto e nomina il primario. Tutto il resto si dice.
    """
    servizi = set(reso.get("services") or {})
    esenti = esenzioni(reso)
    trovati: List[Dict[str, str]] = []
    for nome, (valore, servizio) in sorted(ambiente(reso).items()):
        if nome in esenti:
            continue
        if not e_indirizzo(valore) or e_interno(nome, valore, servizi):
            continue
        if valore.startswith("/"):
            continue                       #: relativo: segue l'host
        host = host_di(valore)
        if host == primario:
            continue
        #: si dice solo se nomina una macchina RICONOSCIBILMENTE altrui, o un
        #: host che non è il primario. Il secondo caso include un dominio vero
        #: messo a mano, che può essere del tutto legittimo — per questo si
        #: DICE e non si blocca.
        trovati.append({
            "nome": nome, "valore": valore, "host": host,
            "servizio": servizio,
            "da": provenienza(nome, env_file),
            "altrui": "si" if host in ALTRUI else "no",
        })
    return trovati


def racconta(trovati: List[Dict[str, str]], primario: str) -> List[str]:
    """Le righe da stampare. Vuoto quando non c'è niente da dire."""
    if not trovati:
        return []                          #: IL TERZO VINCOLO: sa tacere
    righe = [
        f"⚠ {len(trovati)} indirizzo/i reso/i nomina una macchina che non è "
        f"`{primario}`:"]
    for t in trovati:
        nota = "" if t["altrui"] == "no" else "  ← la macchina di chi guarda"
        righe.append(f"    {t['nome']} = {t['valore']}{nota}")
        righe.append(f"        da: {t['da']}   (servizio {t['servizio']})")
    righe += [
        "  Su una macchina di sviluppo può essere giusto: è per questo che quei",
        "  valori esistono, e per questo NON blocco l'avvio e non tocco nessun",
        "  file. Su un nodo servito ad altri, quell'indirizzo apre la macchina di",
        "  chi clicca — misurato l'8 settembre 2026, `localhost:5173`.",
        "  Il rimedio è commentare la riga: il default del compose è relativo e",
        "  segue il nome del nodo da sé.",
    ]
    return righe


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Dice quali indirizzi resi nominano un'altra macchina.")
    ap.add_argument("--primario", required=True,
                    help="l'host primario, come passato a fcn-up.sh")
    ap.add_argument("--reso", default=None,
                    help="JSON del `compose config` (`-` per stdin); "
                         "senza, lo chiede al compose")
    ap.add_argument("--compose", default=None,
                    help="il comando compose, es. 'docker compose'")
    ap.add_argument("--env-file", default=".env.dev")
    ap.add_argument("--file", default="docker-compose.dev.yml")
    a = ap.parse_args(argv)

    try:
        reso = leggi_reso(a.reso,
                          compose=(a.compose.split() if a.compose else None),
                          file_compose=a.file)
    except Exception as e:                 # noqa: BLE001
        #: NON blocca nemmeno qui: se non si riesce a leggere il reso, il nodo
        #: deve salire comunque. Si dice, su stderr, e si ritorna 0.
        print(f"· non ho potuto controllare gli indirizzi resi ({e})",
              file=sys.stderr)
        return 0

    env = pathlib.Path(a.env_file) if a.env_file else None
    for riga in racconta(sospetti(reso, a.primario, env), a.primario):
        print(riga)
    return 0                               #: IL SECONDO VINCOLO: mai bloccare


if __name__ == "__main__":
    raise SystemExit(main())
