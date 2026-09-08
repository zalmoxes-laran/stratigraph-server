"""Gli script rilevano invece di assumere — provato con `uname` finto.

════════════════════════════════════════════════════════════════════════════════
## IL MOTIVO NON È TEORICO

Nicolò Paraciani non ha un Mac, e deve potersi fare un'idea dei servizi **come
amministratore**: la console del nodo, la salute, lo storage. Misurato il
7 ottobre 2026, prima di qualunque package manager:

    fcn-up.sh:39        `colima status` chiamato SEMPRE
    fcn-up.sh:47        `scutil --get LocalHostName`, macOS e basta
    fcn-down.sh:53      `colima stop`, idem
    fcn-trust-ca.sh:30  `sudo security add-trusted-cert`, il portachiavi di macOS

Su Linux Docker gira nativo e colima non serve; su Windows non esiste.

## IL CANCELLO CHE IL PROMPT CHIEDE

*«una prova che esegue gli script con `uname` finto e verifica che non chiamino
colima dove non serve»*. È eseguibile, e lo è per una ragione precisa: la
rilevazione vive in `platform.sh`, che è **sourced** e fatto di funzioni, e i
comandi che chiama arrivano dal `PATH`. Quindi si mette davanti al `PATH` una
cartella di finti — `uname`, `docker`, `colima`, `sudo`, `security`,
`update-ca-certificates`, `certutil` — ognuno dei quali scrive il proprio nome
in un registro, e si guarda il registro.

Non è un recinto che cerca testo: i finti vengono **eseguiti**, e ciò che si
asserisce è chi è stato chiamato. Un falso positivo non è costruibile perché non
c'è nessun pagliaio — c'è un elenco di chiamate avvenute.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

DEV = pathlib.Path(__file__).resolve().parent.parent / "dev-stack"

needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="niente bash: questi script non si possono eseguire qui")

#: `uname -s` per ciascun sistema, come lo scrive il sistema vero.
UNAME = {"macos": "Darwin", "linux": "Linux", "wsl": "Linux",
         "windows": "MINGW64_NT-10.0-22631", "unknown": "Plan9"}


def _finti(tmp_path, *, docker_ok: bool, nomi=(),
           plugin: bool = True, autonomo: bool = False) -> pathlib.Path:
    """Una cartella di comandi finti, e il registro che scrivono.

    `docker info` decide: `docker_ok=False` è la macchina su cui docker non
    risponde, che è l'unico caso in cui un rimedio ha senso.

    `plugin` e `autonomo` sono i due compose, e sono **indipendenti** di
    proposito, perché le quattro combinazioni sono quattro macchine vere:

        plugin=True  autonomo=False   una macchina nuova (Docker Engine, Desktop)
        plugin=False autonomo=True    il Mac di E.D., misurato il 9 ottobre 2026
        plugin=True  autonomo=True    una macchina con entrambi (l'ordine conta)
        plugin=False autonomo=False   docker c'è, compose no → si deve fermare

    E `plugin=False` NON si ottiene togliendo il finto `docker`: `docker` c'è e
    risponde, ed è **il sottocomando** che manca. È la ragione per cui la sonda
    non può guardare il binario.
    """
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "chiamati.txt"
    extra = ("docker-compose",) if autonomo else ()
    #: `config` PASSA a compose vero. È l'unica sottocomando che si delega, e
    #: la ragione è che non è un'operazione di rete: legge un file YAML e lo
    #: stampa risolto, offline. Fingerla vorrebbe dire provare il mio finto
    #: invece dell'installer — che dal compose ricava QUALI repository servono.
    #: `up`, `pull` e `build` restano finti e registrati, che è dove le
    #: asserzioni sull'ordine mordono.
    vero = shutil.which("docker-compose") or shutil.which("docker") or ""
    for nome in ("docker", "colima", "sudo", "security", "scutil", "hostname",
                 "update-ca-certificates", "update-ca-trust", "certutil",
                 "cmd.exe", "wslpath", "getent", "ip", "route", *extra,
                 *nomi):
        script = ["#!/usr/bin/env bash",
                  f'echo "{nome} $*" >> "{log}"']
        if nome == "docker":
            # `docker info` è la domanda; `docker cp` deve produrre il file,
            # perché `fcn-trust-ca.sh` lo copia prima di decidere qualsiasi cosa
            script += ['if [ "${1:-}" = "info" ]; then',
                       f'  exit {0 if docker_ok else 1}', 'fi',
                       'if [ "${1:-}" = "cp" ]; then',
                       '  printf "finta CA\\n" > "${3:-/dev/null}"; exit 0', 'fi']
            #: IL SOTTOCOMANDO. `docker` esiste sempre; `docker compose` no —
            #: sul Mac di E.D. risponde «unknown command», misurato. Il finto
            #: riproduce quella macchina, non una in cui manca `docker`.
            script += ['if [ "${1:-}" = "compose" ]; then',
                       *([]  if plugin else
                         ['  echo "docker: unknown command: docker compose" >&2',
                          '  exit 1']),
                       #: `docker compose … config` → il compose vero
                       '  for a in "$@"; do',
                       '    if [ "$a" = "config" ]; then',
                       f'      shift; exec {vero!r} "$@"' if vero else
                       '      exit 0',
                       '    fi',
                       '  done',
                       '  exit 0', 'fi',
                       'exit 0']
        elif nome == "docker-compose":
            script += ['for a in "$@"; do',
                       '  if [ "$a" = "config" ]; then',
                       f'    exec {vero!r} "$@"' if vero else '    exit 0',
                       '  fi',
                       'done',
                       'exit 0']
        elif nome == "sudo":
            #: `sudo` esegue il resto: così `security`/`cp` finiscono nel
            #: registro col loro nome, che è quello che si vuole guardare
            script += ['"$@"']
        elif nome == "ip":
            #: `ip route get 1.1.1.1` → l'indirizzo con cui la macchina
            #: uscirebbe. Come `getent`, è un comando che su questo Mac non
            #: esiste: senza il finto ogni prova con `uname` = linux misurerebbe
            #: l'assenza del comando invece della risposta del kernel.
            script += ['echo "1.1.1.1 via 192.168.1.1 dev eth0 src 192.168.1.77"']
        elif nome == "route":
            #: la forma di macOS, per il ramo `macos` di `sg_lan_ip`
            script += ['echo "   interface: en0"']
        elif nome == "getent":
            #: una macchina Linux SANA: `em.localhost` si risolve. Su questo Mac
            #: `getent` non esiste affatto, quindi senza il finto ogni prova con
            #: `uname` = linux misurerebbe l'assenza del comando invece della
            #: risposta del risolutore. Le prove che vogliono il caso Debian di
            #: fabbrica lo sostituiscono con uno che torna 1.
            script += ['if [ "${1:-}" = "hosts" ]; then',
                       '  case "${2:-}" in',
                       '    *localhost) echo "127.0.0.1 ${2}"; exit 0 ;;',
                       '    *) exit 2 ;;',
                       '  esac', 'fi',
                       'exit 0']
        elif nome == "hostname":
            script += ['echo finta-macchina']
        elif nome == "scutil":
            script += ['echo Finto-Mac']
        elif nome == "cmd.exe":
            script += ['printf "C:\\\\Users\\\\finto\\r\\n"']
        elif nome == "wslpath":
            script += [f'echo "{tmp_path}/winhome"']
        script += ['exit 0']
        target = bin_ / nome
        target.write_text("\n".join(script) + "\n")
        target.chmod(0o755)
    #: e `uname`, che è il finto che conta
    return bin_


def _uname(bin_: pathlib.Path, sistema: str) -> None:
    u = bin_ / "uname"
    u.write_text("#!/usr/bin/env bash\n"
                 f'echo "{UNAME[sistema]}"\n')
    u.chmod(0o755)


#: Il PATH di una macchina appena installata: i finti, e i comandi di sistema.
#: Serve per provare l'ASSENZA di qualcosa — un finto non può rendere invisibile
#: un binario che esiste davvero, e su questo Mac `docker-compose` esiste in
#: `/opt/homebrew/bin`. Dichiarato invece di aggirato: un test che dice «non c'è
#: nessun compose» mentendo su un PATH che ne contiene uno non prova niente.
PATH_MINIMO = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def _run(script: str, bin_: pathlib.Path, tmp_path, extra_env=None, args=(),
         solo_finti: bool = False):
    env = dict(os.environ)
    resto = os.pathsep.join(PATH_MINIMO) if solo_finti else env["PATH"]
    env["PATH"] = f"{bin_}{os.pathsep}{resto}"
    env["HOME"] = str(tmp_path)
    env.pop("WSL_DISTRO_NAME", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(DEV / script), *args],
                          capture_output=True, text=True, env=env, cwd=str(DEV))


def _chiamati(tmp_path) -> str:
    log = tmp_path / "chiamati.txt"
    return log.read_text() if log.exists() else ""


# ═══ 1 · CHE MACCHINA È QUESTA ═══════════════════════════════════════════════

@needs_bash
@pytest.mark.parametrize("sistema", list(UNAME))
def test_SG_OS_RICONOSCE_ogni_sistema(tmp_path, sistema):
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, sistema)
    env = {"WSL_DISTRO_NAME": "Ubuntu"} if sistema == "wsl" else {}
    done = subprocess.run(
        ["bash", "-c", f'cd "{DEV}" && . ./platform.sh && sg_os'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
             **env})
    assert done.stdout.strip() == sistema, done.stderr


# ═══ 2 · IL CANCELLO · COLIMA SOLO DOVE HA SENSO ═════════════════════════════

@needs_bash
@pytest.mark.parametrize("sistema", ["linux", "wsl", "windows", "unknown"])
def test_COLIMA_NON_SI_CHIAMA_dove_non_serve(tmp_path, sistema):
    """IL CANCELLO DI §4, e morde su un caso costruito: docker MUTO.

    È la condizione in cui un rimedio verrebbe tentato — con docker che risponde
    non si chiamerebbe niente comunque, e la prova non proverebbe nulla.
    """
    bin_ = _finti(tmp_path, docker_ok=False)
    _uname(bin_, sistema)
    env = {"WSL_DISTRO_NAME": "Ubuntu"} if sistema == "wsl" else {}
    done = subprocess.run(
        ["bash", "-c", f'cd "{DEV}" && . ./platform.sh && sg_ensure_docker'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
             **env})
    assert done.returncode != 0, "docker muto e ritorna 0: non l'ha chiesto"
    assert "colima" not in _chiamati(tmp_path), (
        f"colima chiamato su {sistema}: su Linux il demone è di sistema, su "
        f"Windows colima non esiste")
    #: …e non tace: dice cosa fare SU QUESTO sistema
    assert done.stderr.strip(), "docker muto e nessuna frase"
    if sistema == "linux":
        assert "systemctl" in done.stderr and "NIENTE colima" in done.stderr
    if sistema in ("wsl", "windows"):
        assert "Docker Desktop" in done.stderr


@needs_bash
def test_E_SU_MACOS_COLIMA_SI_CHIAMA_ancora(tmp_path):
    """L'altra metà: la prova che il cancello misura il SISTEMA e non ha
    semplicemente spento il rimedio."""
    bin_ = _finti(tmp_path, docker_ok=False)
    _uname(bin_, "macos")
    subprocess.run(
        ["bash", "-c", f'cd "{DEV}" && . ./platform.sh && sg_ensure_docker'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"})
    assert "colima start" in _chiamati(tmp_path)


@needs_bash
def test_E_SE_DOCKER_RISPONDE_non_si_avvia_niente(tmp_path):
    """La domanda vera non è «che sistema è questo» ma «docker risponde?».

    Era `colima status` sempre: su un Mac con Docker Desktop già acceso
    chiedeva a colima di uno stato che non lo riguardava.
    """
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, "macos")
    done = subprocess.run(
        ["bash", "-c", f'cd "{DEV}" && . ./platform.sh && sg_ensure_docker'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"})
    assert done.returncode == 0
    assert "colima" not in _chiamati(tmp_path)


@needs_bash
@pytest.mark.parametrize("sistema,atteso", [("macos", "scutil"),
                                            ("linux", "hostname"),
                                            ("wsl", "hostname")])
def test_IL_NOME_DELLA_MACCHINA_si_chiede_a_chi_lo_sa(tmp_path, sistema, atteso):
    """`scutil` è di macOS. Su Linux era una chiamata che tornava vuota — e un
    nome vuoto è un indirizzo in meno che Caddy serve, in silenzio."""
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, sistema)
    env = {"WSL_DISTRO_NAME": "Ubuntu"} if sistema == "wsl" else {}
    done = subprocess.run(
        ["bash", "-c", f'cd "{DEV}" && . ./platform.sh && sg_local_hostname'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
             **env})
    assert done.stdout.strip(), "nessun nome"
    assert atteso in _chiamati(tmp_path)


# ═══ 3 · LA CA · TRE RAMI, TRE COMANDI DIVERSI ═══════════════════════════════

@needs_bash
def test_LA_CA_SU_MACOS_va_nel_portachiavi(tmp_path):
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, "macos")
    done = _run("fcn-trust-ca.sh", bin_, tmp_path)
    assert done.returncode == 0, done.stderr
    chiamati = _chiamati(tmp_path)
    assert "security add-trusted-cert" in chiamati
    assert "update-ca-certificates" not in chiamati


@needs_bash
def test_LA_CA_SU_LINUX_va_in_DUE_store(tmp_path):
    """E questo è il punto del ramo Linux.

    `update-ca-certificates` sistema OpenSSL — curl, python, git. Il browser no:
    Chrome ha il proprio store NSS in `~/.pki/nssdb`. Un certificato fidato dal
    sistema **non** è fidato dal browser, ed è la stessa trappola dei due store
    di Windows sulla stessa macchina: curl funziona e la pagina dà errore.
    """
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, "linux")
    done = _run("fcn-trust-ca.sh", bin_, tmp_path)
    assert done.returncode == 0, done.stderr
    chiamati = _chiamati(tmp_path)
    assert "update-ca-certificates" in chiamati, "lo store di sistema"
    assert "certutil" in chiamati and "nssdb" in chiamati, "quello del browser"
    assert "security" not in chiamati, "il portachiavi di macOS su Linux"
    #: …e il terzo store, quello di Firefox, si DICE e non si indovina
    assert "Firefox" in done.stdout


@needs_bash
@pytest.mark.parametrize("sistema", ["wsl", "windows"])
def test_LA_CA_SU_WINDOWS_non_finge_e_stampa_il_comando(tmp_path, sistema):
    """La trappola che il prompt nomina: lo script gira in WSL o in Git Bash, e
    il browser che deve fidarsi è quello di **Windows**.

    Niente di ciò che si fa da qui tocca quello store. Quindi non si finge: si
    esporta dove Windows lo vede e si stampa il comando esatto — e si DICE che
    `curl` dentro WSL continuerà a rifiutare, perché è l'ora che si perde.
    """
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, sistema)
    (tmp_path / "winhome").mkdir(exist_ok=True)
    env = {"WSL_DISTRO_NAME": "Ubuntu"} if sistema == "wsl" else {}
    done = _run("fcn-trust-ca.sh", bin_, tmp_path, extra_env=env)
    assert done.returncode == 0, done.stderr
    chiamati = _chiamati(tmp_path)
    #: NON tenta niente sullo store: né macOS né Linux
    for mai in ("security add-trusted-cert", "update-ca-certificates",
                "update-ca-trust"):
        assert mai not in chiamati, mai
    #: …e stampa i due comandi da dare da Amministratore
    assert "Import-Certificate" in done.stdout
    assert "certutil -addstore" in done.stdout
    assert "Amministratore" in done.stdout
    #: …e la frase che vale l'ora: DUE STORE
    assert "DUE STORE" in done.stdout


@needs_bash
def test_EXPORT_ONLY_non_tocca_nessuno_store(tmp_path):
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, "macos")
    done = _run("fcn-trust-ca.sh", bin_, tmp_path, args=("--export-only",))
    assert done.returncode == 0, done.stderr
    assert "security" not in _chiamati(tmp_path)
    assert (tmp_path / "caddy-em-root.crt").exists()


@needs_bash
def test_E_UN_SISTEMA_CHE_NON_RICONOSCE_lo_dice_invece_di_provarci(tmp_path):
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, "unknown")
    done = _run("fcn-trust-ca.sh", bin_, tmp_path)
    assert done.returncode != 0
    assert "non riconosco questo sistema" in done.stderr
    assert "security" not in _chiamati(tmp_path)


# ═══ 4 · E FCN-DOWN ══════════════════════════════════════════════════════════

@needs_bash
@pytest.mark.parametrize("sistema", ["linux", "wsl", "windows"])
def test_FERMARE_COLIMA_dove_colima_non_c_e(tmp_path, sistema):
    """`--colima` chiedeva una cosa che su Linux e Windows non esiste, e
    `|| true` la rendeva un silenzio invece di una frase."""
    bin_ = _finti(tmp_path, docker_ok=True)
    _uname(bin_, sistema)
    env = {"WSL_DISTRO_NAME": "Ubuntu"} if sistema == "wsl" else {}
    done = subprocess.run(
        ["bash", "-c",
         f'cd "{DEV}" && . ./platform.sh && '
         'if [ "$(sg_os)" = "macos" ] && command -v colima >/dev/null 2>&1; '
         'then colima stop; else echo "niente Colima ($(sg_os))"; fi'],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
             **env})
    assert "niente Colima" in done.stdout
    assert "colima" not in _chiamati(tmp_path)


# ═══ 5 · IL COMANDO CHE SU QUELLA MACCHINA NON ESISTE ════════════════════════
#
# `fcn-up.sh:75` e `fcn-down.sh:21` costruivano l'array con `docker-compose`
# scritto a mano. È il binario autonomo, e una macchina nuova installa il
# **plugin** — Docker Engine su Linux, Docker Desktop su Windows. Là la PRIMA
# riga risponde `command not found`, e non c'è niente da leggere nei log perché
# la stack non è mai partita.
#
# Misurato sul Mac di E.D. il 9 ottobre 2026, e il risultato è il contrario di
# quello che si aspetterebbe:
#
#     docker compose version  → docker: unknown command: docker compose
#     docker-compose version  → Docker Compose version 5.3.0
#
# Cioè: qui il plugin NON c'è, e il ripiego è un binario autonomo corrente.
# `docker` risponde e il sottocomando no — per questo la sonda chiede al
# sottocomando di presentarsi e non guarda il binario.

def _sg(bin_, frammento: str, extra_env=None, solo_finti: bool = False):
    """Esegue un frammento con `platform.sh` sourced e i finti davanti al PATH."""
    resto = (os.pathsep.join(PATH_MINIMO) if solo_finti
             else os.environ["PATH"])
    env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{resto}"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", f'cd "{DEV}" && . ./platform.sh && {frammento}'],
        capture_output=True, text=True, env=env)


@needs_bash
def test_COMPOSE_col_plugin_presente_usa_il_plugin(tmp_path):
    """`docker compose` prima, perché è quello che una macchina nuova ha."""
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _sg(bin_, "sg_compose")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "docker compose", done.stdout


@needs_bash
def test_COMPOSE_senza_plugin_ripiega_sull_autonomo(tmp_path):
    """E viceversa: il Mac di E.D., misurato.

    `docker` C'È — il finto risponde a `info` e a tutto il resto. Quello che
    manca è il sottocomando. Una sonda che guardasse `command -v docker`
    direbbe «plugin presente» e costruirebbe un comando inesistente.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True)
    _uname(bin_, "macos")
    done = _sg(bin_, "sg_compose")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "docker-compose", done.stdout


@needs_bash
def test_COMPOSE_con_entrambi_vince_il_plugin(tmp_path):
    """L'ordine è una scelta, e va provata: il ripiego è il ripiego."""
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=True)
    _uname(bin_, "linux")
    done = _sg(bin_, "sg_compose")
    assert done.stdout.strip() == "docker compose", (
        "con entrambi presenti ha scelto il ripiego")


@needs_bash
def test_COMPOSE_CON_NESSUNO_DEI_DUE_si_ferma_dicendolo(tmp_path):
    """Il cancello che il prompt chiede: non si prosegue al buio.

    Un `up` lanciato senza compose non fallisce in un modo leggibile: fallisce
    con `command not found` su una riga che nomina un file di configurazione, e
    chi legge cerca il file.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=False)
    _uname(bin_, "linux")
    #: `solo_finti`: il `docker-compose` di Homebrew è su questo Mac e un finto
    #: non lo può nascondere. Vedi PATH_MINIMO.
    done = _sg(bin_, "sg_compose", solo_finti=True)
    assert done.returncode != 0, "nessun compose e ritorna 0"
    assert not done.stdout.strip(), (
        f"ha stampato un comando che non esiste: {done.stdout!r}")
    #: …e NOMINA cosa manca, tutti e due, con il pacchetto da installare
    assert "docker compose" in done.stderr
    assert "docker-compose" in done.stderr
    assert "docker-compose-plugin" in done.stderr, "non dice cosa installare"


@needs_bash
@pytest.mark.parametrize("plugin,autonomo,atteso", [
    (True,  False, ["docker", "compose"]),
    (False, True,  ["docker-compose"]),
])
def test_L_ARRAY_RESTA_UN_ARRAY(tmp_path, plugin, autonomo, atteso):
    """La trappola che rende la sostituzione meno banale di quanto sembri.

        COMPOSE=("$(sg_compose)")     # UN elemento con uno spazio dentro

    cercherebbe un eseguibile chiamato `docker compose` e fallirebbe con un
    messaggio che non aiuta nessuno. Qui si contano gli elementi e si guarda
    ognuno: `docker` e `compose` separati, non `docker compose` insieme.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=plugin, autonomo=autonomo)
    _uname(bin_, "linux")
    done = _sg(bin_, 'sg_compose_array && '
                     'echo "N=${#COMPOSE[@]}" && '
                     'for e in "${COMPOSE[@]}"; do echo "E=$e"; done')
    assert done.returncode == 0, done.stderr
    assert f"N={len(atteso)}" in done.stdout, done.stdout
    assert [l[2:] for l in done.stdout.splitlines()
            if l.startswith("E=")] == atteso, done.stdout


@needs_bash
def test_L_ARRAY_E_ESEGUIBILE_non_solo_ben_contato(tmp_path):
    """E la prova che conta più del conteggio: il comando **gira**.

    Un array con un elemento sbagliato si conta bene e non si esegue. Qui si
    esegue davvero, contro il finto, e si guarda il registro: `docker compose
    up` — non un eseguibile con uno spazio nel nome.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _sg(bin_, 'sg_compose_array && COMPOSE+=(-f qualcosa.yml) && '
                     '"${COMPOSE[@]}" up -d')
    assert done.returncode == 0, done.stderr + done.stdout
    assert "docker compose -f qualcosa.yml up -d" in _chiamati(tmp_path), (
        _chiamati(tmp_path))


@needs_bash
def test_MAPFILE_NON_SI_USA_perche_su_questo_mac_non_esiste(tmp_path):
    """Perché `read -r -a` e non `mapfile`, che sarebbe la risposta ovvia.

    Misurato: la bash che gira questi script sul Mac di E.D. è GNU bash 3.2.57,
    dove `mapfile` è un comando sconosciuto. Una correzione scritta con
    `mapfile` funzionerebbe su Linux e romperebbe la macchina di chi l'ha
    chiesta — che è il modo peggiore di chiudere una mina di portabilità.

    Quindi la prova non è sul testo: si esegue `sg_compose_array` in una bash
    a cui `mapfile` è stato TOLTO, e si guarda se riempie l'array comunque.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _sg(bin_, 'enable -n mapfile 2>/dev/null || true; '
                     'unset -f mapfile 2>/dev/null || true; '
                     'sg_compose_array && echo "N=${#COMPOSE[@]}"')
    assert done.returncode == 0, done.stderr
    assert "N=2" in done.stdout, done.stdout


# ═══ 6 · E GLI SCRIPT LO USANO DAVVERO ═══════════════════════════════════════
#
# Le prove di sopra misurano `platform.sh`. Questa misura la cosa che rompeva:
# quale comando lo SCRIPT chiama.

@needs_bash
@pytest.mark.parametrize("plugin,autonomo,atteso", [
    (True,  False, "docker compose"),
    (False, True,  "docker-compose"),
])
def test_FCN_DOWN_CHIAMA_il_compose_che_c_e(tmp_path, plugin, autonomo, atteso):
    bin_ = _finti(tmp_path, docker_ok=True, plugin=plugin, autonomo=autonomo)
    _uname(bin_, "linux")
    done = _run("fcn-down.sh", bin_, tmp_path, args=("--stop",))
    assert done.returncode == 0, done.stderr + done.stdout
    chiamati = _chiamati(tmp_path)
    assert f"{atteso} -f docker-compose.dev.yml" in chiamati, chiamati
    #: …e NON l'altro
    altro = "docker-compose" if atteso == "docker compose" else "docker compose"
    assert f"{altro} -f" not in chiamati, f"ha chiamato anche {altro}"


@needs_bash
def test_FCN_DOWN_SENZA_COMPOSE_non_prova_a_spegnere(tmp_path):
    """Si ferma prima, e dice cosa manca."""
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=False)
    _uname(bin_, "linux")
    done = _run("fcn-down.sh", bin_, tmp_path, args=("--stop",),
                solo_finti=True)
    assert done.returncode != 0
    assert "Nessun Docker Compose" in done.stderr, done.stderr
    #: e non ha detto «✔ FCN spento», che sarebbe una bugia
    assert "FCN spento" not in done.stdout, done.stdout


@needs_bash
def test_IL_CONSIGLIO_STAMPATO_nomina_il_comando_che_esiste(tmp_path):
    """Le righe che una persona COPIA E INCOLLA.

    `fcn-up.sh` stampava due volte `docker-compose …` come suggerimento — nei
    log da guardare quando il nodo non risponde, e nel restart dopo aver
    editato s3Dgraphy. Su una macchina col solo plugin sono due consigli che
    danno `command not found`, offerti proprio nel momento in cui qualcosa è
    già andato storto.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _run("fcn-up.sh", bin_, tmp_path, args=("--local-s3d",))
    assert done.returncode == 0, done.stderr + done.stdout
    #: il consiglio del restart, quello di `--local-s3d`
    assert "docker compose -f docker-compose.dev.yml -f" in done.stdout, done.stdout
    #: …e da nessuna parte il comando che su questa macchina non c'è
    for riga in done.stdout.splitlines():
        assert "docker-compose -f" not in riga, riga


# ═══ 7 · LA QUARTA MINA · IL FILE CHE CHI CLONA NON HA ═══════════════════════
#
# Non è una delle tre del prompt: trovata cercando cos'altro rompe il primo
# avvio. `.env.dev` è in `.gitignore` (porta i valori riempiti) e
# `.env.dev.example` è committato, ma `fcn-up.sh` passava `--env-file .env.dev`
# senza controllarlo. Su un clone fresco, misurato:
#
#     couldn't find env file: …/dev-stack/.env.dev
#
# Cioè: lo script moriva PRIMA di arrivare alla rilevazione del compose, e
# nessuna delle tre riparazioni di questa notte sarebbe mai stata raggiunta.

@needs_bash
def test_SENZA_ENV_DEV_dice_la_riga_da_incollare(tmp_path, monkeypatch):
    """E lo dice invece di copiarlo.

    `.env.dev` porta credenziali — senza valore, ma credenziali — e crearlo è
    l'atto di una persona: la stessa ragione per cui `fcn-trust-ca.sh` stampa
    il comando `sudo` invece di eseguirlo.

    Il file vero È in `dev-stack/` su questa macchina, quindi la prova gira su
    una COPIA della cartella in cui non c'è: spostare quello vero sarebbe
    misurare rompendo il posto in cui si misura.
    """
    finto_dev = tmp_path / "dev-stack"
    shutil.copytree(DEV, finto_dev,
                    ignore=shutil.ignore_patterns(".env.dev", "__pycache__"))
    assert not (finto_dev / ".env.dev").exists()
    assert (finto_dev / ".env.dev.example").exists(), "il modello deve restare"

    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    env = dict(os.environ)
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
    env["HOME"] = str(tmp_path)
    done = subprocess.run(["bash", str(finto_dev / "fcn-up.sh")],
                          capture_output=True, text=True, env=env,
                          cwd=str(finto_dev))
    assert done.returncode != 0, "manca .env.dev e prosegue"
    assert "cp .env.dev.example .env.dev" in done.stderr, done.stderr
    #: e si è fermato PRIMA di chiedere il compose o di provare un `up`
    assert "up -d" not in _chiamati(tmp_path), _chiamati(tmp_path)


@needs_bash
def test_E_CON_ENV_DEV_non_dice_niente(tmp_path):
    """L'altra metà: il controllo non parla quando non c'è nulla da dire."""
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _run("fcn-up.sh", bin_, tmp_path)
    assert done.returncode == 0, done.stderr
    assert ".env.dev.example" not in done.stderr
    assert "up -d --build" in _chiamati(tmp_path)


# ═══ 8 · UN NODO DA ZERO · I TRE REPOSITORY CHE NESSUNO DICEVA ═══════════════
#
# Misurato sul Raspberry Pi il 7 settembre 2026, primo avvio su una macchina che
# non è un Mac (Debian 12 bookworm arm64):
#
#     unable to prepare context: path "/home/paul/stratigraph-chatbot" not found
#
# `docker-compose.dev.yml` costruisce da `..`, `../../stratigraph-chatbot` e
# `../../stratigraph-catalog`, e chi clona SOLO `stratigraph-server` — cioè
# chiunque segua il README, che parla di questo repository — non ha gli altri
# due.
#
# E il costo non è il messaggio: è QUANDO arriva. Quel messaggio è comparso dopo
# cinque immagini scaricate. Un controllo che costa un `test -d` arrivava dopo
# tutto il lavoro che rendeva inutile.

import json as _json
import re as _re

COMPOSE_YML = DEV / "docker-compose.dev.yml"


def _clone_finto(tmp_path, *, fratelli=()) -> pathlib.Path:
    """Un albero come quello di chi ha clonato solo `stratigraph-server`.

    `fratelli` sono i nomi da creare accanto: `()` è la macchina vergine.
    Il `dev-stack` è una COPIA — le prove non girano nella cartella vera, così
    un realm reso o un clone non finiscono nel checkout di E.D.
    """
    radice = tmp_path / "albero"
    srv = radice / "stratigraph-server"
    srv.mkdir(parents=True)
    shutil.copytree(DEV, srv / "dev-stack",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in fratelli:
        (radice / f).mkdir(parents=True, exist_ok=True)
    return srv / "dev-stack"


def _installa(dev: pathlib.Path, bin_: pathlib.Path, tmp_path, args=(),
              extra_env=None):
    env = dict(os.environ)
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
    env["HOME"] = str(tmp_path)
    env.pop("WSL_DISTRO_NAME", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(dev / "fcn-install.sh"), *args],
                          capture_output=True, text=True, env=env,
                          cwd=str(dev))


# ── 8.1 · GLI INDIRIZZI STANNO IN UN POSTO SOLO ─────────────────────────────

def test_OGNI_CONTEXT_HA_IL_SUO_URL_nello_stesso_file():
    """Il cancello anti-deriva, e è la ragione per cui non c'è una seconda lista.

    Il compose dice QUALI cartelle servono (`context:`) e nello STESSO file dice
    da dove si clonano (`x-sibling-repos`). Aggiungere un servizio che costruisce
    da un fratello nuovo e scordarsi l'indirizzo non è un silenzio: è questa
    prova che diventa rossa.
    """
    import yaml
    doc = yaml.safe_load(COMPOSE_YML.read_text())
    contesti = set()
    for nome, sv in doc["services"].items():
        b = sv.get("build") if isinstance(sv, dict) else None
        if isinstance(b, dict) and b.get("context"):
            #: `..` è stratigraph-server stesso, e il basename di `..` è `..`
            c = str(b["context"])
            contesti.add("stratigraph-server" if c.strip(". /") == ""
                         else pathlib.PurePosixPath(c).name)
    assert contesti, "nessun `context:` nel compose: il parse è sbagliato"
    urls = doc.get("x-sibling-repos") or {}
    assert urls, "manca `x-sibling-repos` nel compose"

    senza = sorted(contesti - set(urls))
    assert not senza, (
        f"questi `context:` non hanno un URL in `x-sibling-repos`: {senza}. "
        f"Aggiungi la riga là, non una lista nell'installer.")
    for nome, u in urls.items():
        assert u.startswith("https://"), f"{nome}: {u!r} non è https"
        assert u.endswith(".git"), f"{nome}: {u!r} non finisce in .git"


def test_L_INSTALLER_NON_CABLA_NESSUN_URL():
    """La proprietà che il prompt chiede: nessuna seconda copia.

    Provata sul testo — e qui è legittimo, perché l'affermazione È sul testo:
    «in questo file non c'è un indirizzo di repository». Il pagliaio si toglie
    prima (i commenti), così una riga di prosa che nomina github non morde.
    """
    testo = (DEV / "fcn-install.sh").read_text()
    codice = "\n".join(r for r in testo.splitlines()
                       if not r.lstrip().startswith("#"))
    assert "github.com" not in codice, (
        "c'è un URL cablato nell'installer: gli indirizzi stanno in "
        "`x-sibling-repos`, nel compose")
    assert "stratigraph-chatbot" not in codice and \
           "stratigraph-catalog" not in codice, (
        "c'è un nome di repository cablato nell'installer: i nomi vengono dai "
        "`context:` del compose")


# ── 8.2 · L'ORDINE, CHE È IL PUNTO ──────────────────────────────────────────

@needs_bash
def test_UN_FRATELLO_ASSENTE_si_dice_PRIMA_di_qualunque_rete(tmp_path):
    """E la prova dimostra l'ORDINE, non solo il messaggio.

    I finti registrano ogni chiamata. Quello che si asserisce è che nel
    registro non c'è **niente** che scarichi: nessun `pull`, nessun `up`,
    nessun `build`. Sul Pi il messaggio è arrivato dopo cinque immagini; qui
    arriva prima di zero.

    Il caso costruito è quello in cui l'installer NON può rimediare da sé — un
    fratello che il compose costruisce e che `x-sibling-repos` non elenca —
    perché è l'unico in cui deve fermarsi invece di clonare.
    """
    dev = _clone_finto(tmp_path)          # vergine: nessun fratello
    #: si toglie TUTTO `x-sibling-repos` NELLA COPIA: così il compose chiede
    #: cartelle di cui l'installer non sa l'indirizzo, e non può rimediare da
    #: sé. È l'unico scenario in cui deve fermarsi invece di clonare — e quindi
    #: l'unico in cui «zero operazioni di rete» è la cosa da asserire.
    y = dev / "docker-compose.dev.yml"
    y.write_text(_re.sub(r"^x-sibling-repos:\n(?:[ \t]+.*\n|\n)*", "",
                         y.read_text(), flags=_re.M))

    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True,
                  nomi=("git",))
    _uname(bin_, "linux")
    done = _installa(dev, bin_, tmp_path)

    assert done.returncode != 0, done.stdout
    for atteso in ("stratigraph-chatbot", "stratigraph-catalog"):
        assert atteso in done.stderr, done.stderr
    assert "x-sibling-repos" in done.stderr, "non dice DOVE si aggiunge"
    assert "Nessun pull" in done.stdout, done.stdout

    #: L'ORDINE, sul registro delle chiamate avvenute — e niente di questo è
    #: una ricerca nel testo: sono le chiamate che ci sono state.
    chiamati = _chiamati(tmp_path)
    for rete in (" pull", "up -d", " build", "clone"):
        assert rete not in chiamati, (
            f"ha fatto {rete.strip()!r} prima di fermarsi:\n{chiamati}")
    #: …e quello che HA fatto è solo leggere: `config` (il parse, offline),
    #: `docker info` (la domanda) e la risoluzione del nome. Nient'altro.
    for riga in chiamati.splitlines():
        assert ("config" in riga or riga.startswith("docker info")
                or riga.startswith("getent") or "version" in riga), (
            f"chiamata inattesa prima di fermarsi: {riga!r}")


@needs_bash
def test_E_FCN_UP_NON_COMINCIA_A_SCARICARE(tmp_path):
    """Lo stesso, sul comando che la gente lancia davvero.

    `fcn-install.sh` è la strada documentata, `fcn-up.sh` è quella che sta nel
    README e nell'aiuto. Un controllo che esiste solo sulla prima protegge solo
    la prima.
    """
    dev = _clone_finto(tmp_path)
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True)
    _uname(bin_, "linux")
    done = subprocess.run(["bash", str(dev / "fcn-up.sh")],
                          capture_output=True, text=True, cwd=str(dev),
                          env={**os.environ, "HOME": str(tmp_path),
                               "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"})
    assert done.returncode != 0
    assert "repository affiancati che non ci sono" in done.stderr, done.stderr
    assert "./fcn-install.sh" in done.stderr, "non dice il rimedio"
    chiamati = _chiamati(tmp_path)
    assert "up -d --build" not in chiamati, chiamati


@needs_bash
def test_UN_FRATELLO_ASSENTE_CON_L_URL_lo_clona(tmp_path):
    """L'altra metà: quando sa l'indirizzo, lo clona invece di lamentarsi.

    `git` è finto e registra: si guarda CHE COSA ha clonato e DOVE, senza
    toccare la rete.
    """
    dev = _clone_finto(tmp_path)
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True,
                  nomi=("git",))
    _uname(bin_, "linux")
    #: il finto `git clone` deve creare la cartella, sennò il passo dopo la
    #: ritrova mancante e il conto dei guai non torna
    g = bin_ / "git"
    g.write_text('#!/usr/bin/env bash\n'
                 f'echo "git $*" >> "{tmp_path}/chiamati.txt"\n'
                 'if [ "${1:-}" = "clone" ]; then mkdir -p "${!#}"; fi\n'
                 'exit 0\n')
    g.chmod(0o755)
    done = _installa(dev, bin_, tmp_path)
    chiamati = _chiamati(tmp_path)
    for atteso in ("stratigraph-chatbot.git", "stratigraph-catalog.git"):
        assert atteso in chiamati, f"non ha clonato {atteso}:\n{chiamati}"
    #: e dagli indirizzi del compose, non da una lista sua
    assert "StratiGraph-ECCCH/stratigraph-chatbot" in chiamati, chiamati


# ── 8.3 · IDEMPOTENTE, E LO DICE ────────────────────────────────────────────

@needs_bash
def test_RILANCIATO_SU_UNA_MACCHINA_A_POSTO_non_cambia_niente(tmp_path):
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True,
                  nomi=("git",))
    _uname(bin_, "linux")
    prima = _installa(dev, bin_, tmp_path)
    assert prima.returncode == 0, prima.stderr + prima.stdout
    assert "non ho cambiato niente" in prima.stdout, prima.stdout
    dopo = _installa(dev, bin_, tmp_path)
    assert dopo.returncode == 0
    assert dopo.stdout == prima.stdout, "due giri, due uscite diverse"
    #: e non ha clonato niente in nessuno dei due
    assert "clone" not in _chiamati(tmp_path)


# ── 8.4 · NIENTE SUDO, PROVATO ESEGUENDO ────────────────────────────────────

@needs_bash
def test_NON_CHIAMA_SUDO_provato_con_un_sudo_che_esplode(tmp_path):
    """Non cercando la stringa: mettendo davanti un `sudo` che FALLISCE.

    Se l'installer lo chiamasse per scrivere `/etc/hosts`, finirebbe male. Se
    non lo chiama, finisce bene — e la differenza è misurabile senza leggere una
    riga di sorgente.
    """
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True,
                  nomi=("git",))
    _uname(bin_, "linux")
    #: un `sudo` che non esegue niente e ritorna 99
    s = bin_ / "sudo"
    s.write_text('#!/usr/bin/env bash\n'
                 f'echo "sudo $*" >> "{tmp_path}/chiamati.txt"\n'
                 'echo "SUDO CHIAMATO" >&2\nexit 99\n')
    s.chmod(0o755)
    done = _installa(dev, bin_, tmp_path)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "SUDO CHIAMATO" not in done.stderr, done.stderr
    assert "sudo" not in _chiamati(tmp_path), _chiamati(tmp_path)
    #: …e nemmeno `tee`, che è l'altra metà di quella riga
    assert "\ntee " not in _chiamati(tmp_path)


# ── 8.5 · em.localhost CHE NON RISOLVE ──────────────────────────────────────

@needs_bash
def test_EM_LOCALHOST_CHE_NON_RISOLVE_produce_la_riga(tmp_path):
    """La predizione del 9 ottobre, confermata sul Pi.

    Il finto `getent` è quello che decide: torna 1, come glibc con la riga
    `hosts: files dns` di fabbrica su Debian 12 — misurato in
    `debian:bookworm-slim` e in `ubuntu:24.04`.
    """
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True,
                  nomi=("git", "getent"))
    _uname(bin_, "linux")
    ge = bin_ / "getent"
    ge.write_text('#!/usr/bin/env bash\n'
                  f'echo "getent $*" >> "{tmp_path}/chiamati.txt"\n'
                  'exit 1\n')          #: non risolve NIENTE
    ge.chmod(0o755)
    done = _installa(dev, bin_, tmp_path)
    assert done.returncode != 0, "non risolve e prosegue"
    assert "127.0.0.1 em.localhost" in done.stderr, done.stderr
    assert "hosts: files dns" in done.stderr, "non spiega la causa"
    #: …e NON è un'installazione a metà: non invita ad accendere
    assert "Adesso:   ./fcn-up.sh" not in done.stdout, done.stdout
    #: …e non ha scritto niente
    assert "sudo" not in _chiamati(tmp_path)


# ═══ 9 · IL REALM CHE IMPARA IL NOME ═════════════════════════════════════════
#
# Misurato sul Pi il 7 settembre 2026: `./fcn-up.sh fcn.local` fa emettere a
# Caddy un certificato per quel nome, la pagina ARRIVA da un'altra macchina —
# TLS a posto, CA fidata, Keycloak che risponde — e il login muore con
#
#     We are sorry…   Invalid parameter: redirect_uri
#
# perché le redirect URI del client `em-console` sono cablate su
# `em.localhost:8443` e su `localhost`. L'argomento «host primario» era
# applicato a METÀ: Caddy lo imparava, il realm no.
#
# `render_realm.py` è una FUNZIONE PURA — dict dentro, dict fuori — e per
# questo si prova per intero senza far ripartire nessun container.

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("render_realm", DEV / "render_realm.py")
RR = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(RR)

REALM = _json.loads((DEV / "keycloak" / "realm-em-dev.json").read_text())


def _uris(realm, client="em-console"):
    for c in realm.get("clients", []):
        if c.get("clientId") == client:
            return c.get("redirectUris") or []
    return []


def test_IL_REALM_COMMITTATO_e_ancora_quello_che_credo():
    """Il presupposto, asserito: se le URI del dev-stack cambiassero forma
    questa prova va guardata prima delle altre."""
    u = _uris(REALM)
    assert len(u) == 28, f"erano 28, adesso {len(u)}"
    canoniche = [x for x in u if x.startswith("https://em.localhost:8443/")]
    assert len(canoniche) == 8, canoniche
    #: e nessun jolly nella parte host, di partenza
    for x in u:
        assert "*" not in x.split("//", 1)[-1].split("/", 1)[0], x


def test_RISPECCHIA_e_non_inventa():
    """I PERCORSI restano quelli già dichiarati; cambia solo l'host.

    È la forma più forte del vincolo «niente jolly»: non c'è nessun percorso
    nuovo da giudicare.
    """
    reso, aggiunte = RR.rendi(REALM, host="fcn.local", porta=8443)
    nuove = aggiunte["em-console"]
    assert len(nuove) == 8, nuove
    percorsi_prima = {u.split(":8443", 1)[1]
                      for u in _uris(REALM)
                      if u.startswith("https://em.localhost:8443/")}
    percorsi_dopo = {u.split(":8443", 1)[1] for u in nuove}
    assert percorsi_dopo == percorsi_prima, "ha inventato o perso un percorso"
    for u in nuove:
        assert u.startswith("https://fcn.local:8443/")
    #: e l'ingresso non è stato mutato
    assert len(_uris(REALM)) == 28


def test_NIENTE_JOLLY_NELLA_PARTE_HOST():
    """Il vincolo non negoziabile: `https://*/em/admin/*` è un open redirect.

    Provato su ogni forma con cui un jolly può arrivare, e su quelle che
    sembrano un host e non lo sono.
    """
    for cattivo in ("*", "*.local", "fcn.*", "*.*",
                    "fcn.local:8443",           # la porta è un altro argomento
                    "https://fcn.local",        # lo schema
                    "fcn.local/em",             # un percorso
                    "", " ", "-fcn", "fcn_local"):
        with pytest.raises(RR.NomeRifiutato):
            RR.rendi(REALM, host=cattivo, porta=8443)


def test_E_IL_JOLLY_NON_ENTRA_NEANCHE_DI_STRAFORO():
    """La guardia DOPO, non solo prima: nessuna URI resa porta un jolly.

    Costruito: un realm il cui insieme canonico contiene già un jolly. Il nome
    passato è pulito, quindi il cancello d'ingresso non morde — e deve mordere
    quello d'uscita.
    """
    sporco = _json.loads(_json.dumps(REALM))
    for c in sporco["clients"]:
        if c.get("clientId") == "em-console":
            c["redirectUris"].append("https://*:8443/em/admin/*")
    with pytest.raises(RR.NomeRifiutato) as e:
        RR.rendi(sporco, host="fcn.local", porta=8443)
    assert "jolly nella parte host" in str(e.value)


def test_IDEMPOTENTE():
    uno, _ = RR.rendi(REALM, host="fcn.local", porta=8443)
    due, aggiunte = RR.rendi(uno, host="fcn.local", porta=8443)
    assert due == uno
    assert not aggiunte, "il secondo giro ha aggiunto qualcosa"


def test_SENZA_L_AUTORITA_CANONICA_si_rifiuta_invece_di_rendere_niente():
    """Se le URI del dev-stack cambiassero, questo script deve DIRLO.

    Rendere un realm identico e chiamarlo «reso» sarebbe la forma peggiore:
    Caddy servirebbe il nome, il realm no, e il file direbbe che è a posto.
    """
    vuoto = {"clients": [{"clientId": "em-console",
                          "redirectUris": ["https://altro.example/x/*"]}]}
    with pytest.raises(RR.NomeRifiutato) as e:
        RR.rendi(vuoto, host="fcn.local", porta=8443)
    assert "autorità canonica" in str(e.value)


def test_IL_NOME_CANONICO_non_e_un_errore_ma_non_e_un_lavoro():
    reso, aggiunte = RR.rendi(REALM, host="em.localhost", porta=8443)
    assert not aggiunte
    assert reso == REALM


@needs_bash
def test_FCN_UP_RENDE_IL_REALM_e_lo_monta(tmp_path):
    """E lo fa PRIMA di `up`, perché il realm è un import all'avvio.

    Una correzione via API di amministrazione dopo `up` funzionerebbe e
    vivrebbe fuori dal file: al `--wipe` successivo il nome smetterebbe di
    funzionare senza che nessuno abbia cambiato niente.
    """
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = subprocess.run(["bash", str(dev / "fcn-up.sh"), "fcn.local"],
                          capture_output=True, text=True, cwd=str(dev),
                          env={**os.environ, "HOME": str(tmp_path),
                               "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"})
    assert done.returncode == 0, done.stderr + done.stdout
    reso = dev / "keycloak" / "realm-em-dev.fcn.local.json"
    assert reso.is_file(), "non ha reso il realm"
    assert len(_uris(_json.loads(reso.read_text()))) == 36, "8 in più"
    #: …e l'ORDINE: reso prima dell'`up`
    chiamati = _chiamati(tmp_path)
    assert "up -d --build" in chiamati
    #: il file esisteva già quando `up` è stato chiamato — provato dal fatto che
    #: `REALM_FILE` era nell'ambiente di quel processo
    ambiente = dev / "keycloak"
    assert reso.stat().st_mtime <= pathlib.Path(
        tmp_path / "chiamati.txt").stat().st_mtime


def test_IL_REALM_RESO_E_IGNORATO_da_git():
    """Il committato resta la verità; il reso è di quella macchina."""
    fuori = subprocess.run(
        ["git", "check-ignore", "-v",
         "dev-stack/keycloak/realm-em-dev.fcn.local.json"],
        capture_output=True, text=True, cwd=str(DEV.parent))
    assert fuori.returncode == 0, "il realm reso NON è ignorato da git"


def test_IL_COMPOSE_MONTA_IL_REALM_per_variabile_col_default_di_prima():
    testo = COMPOSE_YML.read_text()
    assert "${REALM_FILE:-./keycloak/realm-em-dev.json}" in testo, (
        "senza il default, `./fcn-up.sh` senza argomenti cambierebbe "
        "comportamento")


# ═══ 10 · host.docker.internal · UN NOME CHE SU LINUX NON C'È ════════════════

def test_EXTRA_HOSTS_dichiara_host_docker_internal():
    """Misurato sul Pi: `getent hosts host.docker.internal` dentro Caddy non
    risolve. Docker Desktop e Colima quel nome lo forniscono (qui risolve a
    192.168.5.2); Docker Engine nativo no.

    Quindi `/em/studio/*` e `/em/read/*` restavano 502 sul Pi **anche
    accendendo Vite**: la rotta non poteva risolvere il suo destinatario, e il
    502 era la risposta giusta alla domanda sbagliata.
    """
    #: Il compose si LEGGE come YAML e non si cerca una stringa vicina a
    #: un'altra: quello che deve valere è che l'impostazione sta sul servizio
    #: che ha la rotta, e la struttura è la sola cosa che lo dice.
    import yaml
    doc = yaml.safe_load(COMPOSE_YML.read_text())
    caddy = doc["services"]["caddy"]
    assert "host.docker.internal:host-gateway" in (caddy.get("extra_hosts") or [])
    #: …e su nessun altro, perché è la rotta di Caddy e non un'abitudine
    altri = [n for n, sv in doc["services"].items()
             if n != "caddy" and isinstance(sv, dict)
             and any("host.docker.internal" in str(h)
                     for h in (sv.get("extra_hosts") or []))]
    assert not altri, f"anche su {altri}: dichiaralo dove serve"


def test_LE_DUE_ROTTE_DI_SVILUPPO_lo_dicono_nel_502():
    """L'altra metà della decisione: riparare la risoluzione NON rende quelle
    due rotte una promessa del nodo. Su un nodo che non fa girare Vite sono
    502, ed è giusto — quindi il 502 deve dirlo."""
    caddy = (DEV / "Caddyfile.dev").read_text()
    assert "DEVELOPER-ONLY" in caddy
    assert "is not broken" in caddy, "non dice che il nodo non è rotto"
    assert "host.docker.internal" in caddy


# ═══ 11 · UN NOME CHE SI STAMPA E NON ESISTE ═════════════════════════════════
#
# Misurato il 7 settembre 2026: avahi è attivo sul Pi e PUBBLICA, e da un Mac
# sulla stessa LAN `ping fcn.local` risponde `cannot resolve`. La causa non è il
# Pi: è l'access point che non propaga il multicast fra i client, che sugli AP
# di consumo è la norma e non l'eccezione.
#
# Quindi la riga «Per l'ALTRO computer: https://fcn.local:8443/…» era un
# indirizzo che non funziona stampato come se funzionasse — la stessa forma del
# `curl` senza `-f`.

def _con_curl(tmp_path, *, esito: str, plugin=True, autonomo=False):
    """I finti, con un `curl` che risponde come si vuole.

    `esito`: `si` (0), `nome` (6 = couldn't resolve), `muto` (7).
    Il `curl` finto distingue il NOME chiesto: `em.localhost` risponde sempre
    (sennò `fcn-up.sh` stamperebbe anche l'avviso della CA e la prova
    misurerebbe due cose), il nome Bonjour risponde secondo `esito`.
    """
    bin_ = _finti(tmp_path, docker_ok=True, plugin=plugin, autonomo=autonomo,
                  nomi=("curl",))
    rc = {"si": 0, "nome": 6, "muto": 7}[esito]
    c = bin_ / "curl"
    c.write_text('#!/usr/bin/env bash\n'
                 f'echo "curl $*" >> "{tmp_path}/chiamati.txt"\n'
                 'for a in "$@"; do case "$a" in\n'
                 '  *em.localhost*) exit 0 ;;\n'
                 f'  *finta-macchina.local*) exit {rc} ;;\n'
                 'esac; done\n'
                 'exit 0\n')
    c.chmod(0o755)
    return bin_


def _accendi(dev, bin_, tmp_path, args=()):
    return subprocess.run(["bash", str(dev / "fcn-up.sh"), *args],
                          capture_output=True, text=True, cwd=str(dev),
                          env={**os.environ, "HOME": str(tmp_path),
                               "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"})


@needs_bash
def test_IL_NOME_CHE_RISPONDE_lo_dice_e_dichiara_il_LIMITE(tmp_path):
    """Quando il nome risponde, la frase non promette più di quel che sa.

    Il limite è il punto: da qui si misura solo che QUESTA macchina risolve il
    nome. Che lo risolva l'ALTRA è la cosa che serve e che da qui non si vede.
    """
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _con_curl(tmp_path, esito="si")
    _uname(bin_, "linux")
    done = _accendi(dev, bin_, tmp_path)
    assert done.returncode == 0, done.stderr
    assert "finta-macchina.local:8443" in done.stdout
    assert "risponde DA QUI" in done.stdout, done.stdout
    assert "multicast" in done.stdout, "non dichiara il limite della sonda"


@needs_bash
@pytest.mark.parametrize("esito,frase", [
    ("nome", "il nome non si risolve"),
    ("muto", "si risolve ma non risponde"),
])
def test_UN_NOME_CHE_NON_RISPONDE_non_si_stampa_come_buono(tmp_path, esito, frase):
    """Le due frasi sono diverse perché sono due rimedi diversi."""
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _con_curl(tmp_path, esito=esito)
    _uname(bin_, "linux")
    done = _accendi(dev, bin_, tmp_path)
    assert done.returncode == 0, done.stderr
    assert "NON risponde nemmeno da qui" in done.stdout, done.stdout
    assert frase in done.stdout, done.stdout
    #: …e il ripiego è insegnare il NOME all'altra macchina, non usare l'IP
    assert "/etc/hosts" in done.stdout
    assert "non firma per un IP" in done.stdout, (
        "non dice perché l'IP nudo non basta")
    #: la riga di hosts porta un IP e il nome, in quest'ordine
    riga = [r for r in done.stdout.splitlines()
            if "finta-macchina.local" in r and _re.search(r"\d+\.\d+\.\d+\.\d+", r)]
    assert riga, "nessuna riga di /etc/hosts utilizzabile"


@needs_bash
def test_E_NON_SI_STAMPA_DUE_VOLTE_lo_stesso_indirizzo(tmp_path):
    """Il primario e il nome Bonjour possono coincidere: in quel caso la riga
    «per l'altro computer» non ha niente da aggiungere."""
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _con_curl(tmp_path, esito="si")
    _uname(bin_, "linux")
    done = _accendi(dev, bin_, tmp_path, args=("finta-macchina.local",))
    assert done.returncode == 0, done.stderr
    assert "Per l'ALTRO computer" not in done.stdout, done.stdout


# ═══ 12 · IL BIND MOUNT CHE FUNZIONA QUASI ═══════════════════════════════════
#
# `--local-s3d` non costruisce da `s3Dgraphy`: lo MONTA
# (`docker-compose.local-s3d.yml`, `../../s3Dgraphy/src:/s3dgraphy-src:ro`).
# Quindi il controllo dei `context:` non lo vede — non è un contesto.
#
# Misurato l'8 settembre 2026 con la sorgente assente:
#
#     docker run --rm -v "<cartella-che-non-c-e>:/dentro:ro" alpine ls /dentro
#       → riesce, e /dentro è VUOTA
#
# Il container parte sano e `PYTHONPATH=/s3dgraphy-src` ricade sul wheel
# installato: `--local-s3d` non fa niente, in silenzio. Si edita s3Dgraphy, si
# riavvia, e non cambia niente — che è la forma «funziona quasi», la peggiore.

@needs_bash
def test_LOCAL_S3D_SENZA_S3DGRAPHY_si_ferma_invece_di_montare_il_nulla(tmp_path):
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _accendi(dev, bin_, tmp_path, args=("--local-s3d",))
    assert done.returncode != 0, done.stdout
    assert "s3Dgraphy/src" in done.stderr, done.stderr
    assert "VUOTA" in done.stderr, "non dice cosa succederebbe"
    assert "./fcn-install.sh --local-s3d" in done.stderr, "non dice il rimedio"
    #: e non ha acceso niente
    assert "up -d" not in _chiamati(tmp_path), _chiamati(tmp_path)


@needs_bash
def test_E_CON_S3DGRAPHY_PRESENTE_prosegue(tmp_path):
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    (dev.parent.parent / "s3Dgraphy" / "src").mkdir(parents=True)
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False)
    _uname(bin_, "linux")
    done = _accendi(dev, bin_, tmp_path, args=("--local-s3d",))
    assert done.returncode == 0, done.stderr + done.stdout
    assert "s3Dgraphy LOCALE" in done.stdout
    chiamati = _chiamati(tmp_path)
    assert "docker-compose.local-s3d.yml" in chiamati, chiamati
    assert "up -d --build" in chiamati


@needs_bash
def test_L_INSTALLER_COL_FLAG_clona_anche_s3dgraphy(tmp_path):
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=True, autonomo=False,
                  nomi=("git",))
    _uname(bin_, "linux")
    g = bin_ / "git"
    g.write_text('#!/usr/bin/env bash\n'
                 f'echo "git $*" >> "{tmp_path}/chiamati.txt"\n'
                 'if [ "${1:-}" = "clone" ]; then mkdir -p "${!#}"; fi\nexit 0\n')
    g.chmod(0o755)
    done = _installa(dev, bin_, tmp_path, args=("--local-s3d",))
    assert done.returncode == 0, done.stderr + done.stdout
    assert "s3Dgraphy.git" in _chiamati(tmp_path), _chiamati(tmp_path)
    #: …e SENZA il flag non lo clona: è un requisito del modo, non del nodo
    tmp2 = tmp_path / "secondo"
    tmp2.mkdir()
    dev2 = _clone_finto(tmp2,
                        fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev2 / ".env.dev")
    bin2 = _finti(tmp2, docker_ok=True, plugin=True, autonomo=False,
                  nomi=("git",))
    _uname(bin2, "linux")
    _installa(dev2, bin2, tmp2)
    assert "s3Dgraphy" not in _chiamati(tmp2), _chiamati(tmp2)


# ═══ 13 · LE PORTE CHE NOMINANO UN'ALTRA MACCHINA ════════════════════════════
#
# LA PROVA GENERALE, quella che enuncia la regola invece di elencare i casi:
#
#   Dato `PRIMARY = fcn.local`, ogni indirizzo che questa stack consegna a un
#   browser nomina `fcn.local`. Non `em.localhost`, non `localhost`, non un IP.
#
# Origine: primo uso vero del nodo `fcn.local` da un altro computer, 8 settembre
# 2026. Da `https://fcn.local:8443/em/rooms/` il pulsante «StratiField ·
# browser» apriva `https://em.localhost:8443/chat/` — cioè il portatile di chi
# aveva cliccato, non il nodo che stava guardando.
#
# ## PERCHÉ SULL'AMBIENTE RESO E NON SUL SORGENTE
#
# Perché finora si è chiesto al testo. `fcn-up.sh` esportava `EM_IIIF_PUBLIC`
# con cura e la riga del compose la cablava: un lettore del sorgente vedeva
# l'export e concludeva che la variabile seguiva il nome. Le due righe cablate
# esistevano proprio perché nessuno aveva chiesto alla COSA.
#
# Quindi: si esegue `fcn-up.sh fcn.local` per davvero (con i finti delle notti
# precedenti), il finto compose intercetta l'`up` e **ri-chiama il compose vero
# con `config`** usando lo stesso ambiente e gli stessi `-f`, e si guardano i
# valori resi. L'elenco delle variabili esportate NON è ripetuto qui: è
# `fcn-up.sh` a deciderlo, che è la proprietà «un posto solo».

PRIMARIO = "fcn.local"

#: Chi è ESENTE, e ognuna con la sua ragione. Un'esenzione senza ragione è un
#: buco nella regola, quindi stanno qui in un dizionario e non in un `if`.
ESENTI = {
    #: `EM_SITE` è l'ELENCO di host che Caddy serve: contiene `em.localhost`
    #: perché il nodo continua a rispondere anche su quel nome, e deve.
    "EM_SITE": "è la lista dei nomi che Caddy serve, non un indirizzo consegnato",
    #: Categoria 3, dichiarata nel compose: la 9001 è la console di
    #: amministrazione di MinIO, legata a 127.0.0.1 e NON servita da Caddy
    #: (misurato: `/minio/` → 404). `localhost` è giusto perché in un browser
    #: `localhost` è la macchina di chi guarda, e quella è l'unica da cui si
    #: apre. Nominarla `fcn.local:9001` sarebbe una bugia.
    "EM_MINIO_CONSOLE_URL": "console su loopback, non pubblicata né proxata",
}

#: Gli indirizzi INTERNI non sono consegnati a un browser: sono nomi della rete
#: di container. La distinzione è già nel codice (`node_health.py:645`, «one is a
#: machine we dial, the other is a name we hand out»), e questa prova la usa
#: invece di re-inventarla.
def _e_interno(nome: str, valore: str, servizi) -> bool:
    """Un indirizzo della rete di container, non uno consegnato a un browser.

    Due modi di riconoscerlo, e nessuno dei due è un elenco scritto a mano:

      · il NOME lo dichiara (`*_INTERNAL`, e i tre che il codice usa così);
      · l'HOST è un servizio di questo compose — `http://minio:9000`,
        `http://keycloak:8080`. I nomi dei servizi vengono dal reso, quindi un
        servizio nuovo è coperto da sé. Trovato eseguendo: la prima versione
        guardava solo il suffisso e inciampava su
        `CANTALOUPE_S3SOURCE_ENDPOINT` e `MINIO_ENDPOINT`.
    """
    if (nome.endswith("_INTERNAL") or nome.endswith("_INTERNAL_BASE")
            or nome in ("OIDC_JWKS_URI", "NODEODM_URL", "EM_TRANSFORMER_URL")):
        return True
    v = (valore or "").strip()
    if v.startswith(("http://", "https://")):
        host = v.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
        if host in servizi:
            return True
    return False


#: Un valore che un browser aprirebbe: uno schema http(s), o un percorso
#: assoluto. Tutto il resto (una porta, un id, un percorso di filesystem) non è
#: un indirizzo e non riguarda questa regola.
def _e_indirizzo(v: str) -> bool:
    v = (v or "").strip()
    if v.startswith(("http://", "https://")):
        return True
    #: `/chat/` sì; `/srv/chatbot-data/x.json` no — un percorso di filesystem
    #: non arriva in un browser, e si riconosce perché nomina una cartella del
    #: container.
    if v.startswith("/") and not v.startswith(("/srv/", "/opt/", "/var/",
                                               "/tmp/", "/etc/", "/data")):
        return True
    return False


NOMI_SBAGLIATI = ("em.localhost", "localhost", "127.0.0.1", "0.0.0.0")


def _reso_con_primario(tmp_path, primario: str = PRIMARIO):
    """Esegue `fcn-up.sh <primario>` e ritorna l'ambiente che il compose rende.

    Il finto compose, quando vede `up`, ri-chiama quello VERO con `config`: così
    l'ambiente misurato è quello che `fcn-up.sh` ha davvero passato, e l'elenco
    delle variabili non è ripetuto in questa prova.
    """
    import yaml
    dev = _clone_finto(tmp_path,
                       fratelli=("stratigraph-chatbot", "stratigraph-catalog"))
    shutil.copy(DEV / ".env.dev.example", dev / ".env.dev")
    bin_ = _finti(tmp_path, docker_ok=True, plugin=False, autonomo=True)
    _uname(bin_, "linux")
    vero = shutil.which("docker-compose") or shutil.which("docker")
    if not vero:
        pytest.skip("nessun compose: l'ambiente reso non si può misurare qui")
    uscita = tmp_path / "reso.yml"
    #: il finto `docker-compose`: `config` passa, e `up` diventa un `config`
    #: sugli STESSI `-f`, con l'ambiente che fcn-up.sh ha esportato
    fc = bin_ / "docker-compose"
    fc.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "docker-compose $*" >> "{tmp_path}/chiamati.txt"\n'
        #: si tiene TUTTO fino a `up`, e si taglia da lì. `fcn-up.sh` chiama
        #: `… --profile https up -d --build`, quindi `--profile https` sta
        #: prima e va conservato: senza, il servizio `caddy` non entra nel reso
        #: e `EM_SITE` non c'è — che è come ho scoperto questa riga.
        'args=(); for a in "$@"; do\n'
        '  [ "$a" = "up" ] && break\n'
        '  args+=("$a")\n'
        'done\n'
        'for a in "$@"; do\n'
        f'  if [ "$a" = "config" ]; then exec {vero!r} "$@"; fi\n'
        'done\n'
        f'exec {vero!r} "${{args[@]}}" config > "{uscita}" 2>/dev/null\n')
    fc.chmod(0o755)
    done = subprocess.run(["bash", str(dev / "fcn-up.sh"), primario],
                          capture_output=True, text=True, cwd=str(dev),
                          env={**os.environ, "HOME": str(tmp_path),
                               "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"})
    assert done.returncode == 0, done.stderr + done.stdout
    assert uscita.is_file() and uscita.stat().st_size, (
        f"il compose non ha reso niente:\n{done.stdout}\n{done.stderr}")
    doc = yaml.safe_load(uscita.read_text())
    servizi = set((doc.get("services") or {}))
    reso = {}
    for nome, sv in (doc.get("services") or {}).items():
        for k, v in (sv.get("environment") or {}).items():
            if v is None:
                continue
            reso.setdefault(k, str(v))
    return reso, servizi, done


@needs_bash
def test_OGNI_INDIRIZZO_CONSEGNATO_A_UN_BROWSER_nomina_il_nodo(tmp_path):
    """LA PROVA GENERALE. Enuncia la regola, non elenca i casi.

    Prima dell'8 settembre 2026 questa sarebbe stata rossa su quattro variabili:
    `EM_IIIF_PUBLIC` e `EM_KEYCLOAK_CONSOLE_URL` (cablate, nessuna via d'uscita)
    e `EM_CATALOG_PUBLIC` e `EM_FIELD_ASSISTANT_URL` (escape presente, default
    assoluto, nessun esportatore).
    """
    reso, servizi, _ = _reso_con_primario(tmp_path)
    assert len(reso) > 20, f"troppo poche variabili per provare qualcosa: {reso}"

    guardate, colpe = [], []
    for nome, valore in sorted(reso.items()):
        if (nome in ESENTI or _e_interno(nome, valore, servizi)
                or not _e_indirizzo(valore)):
            continue
        guardate.append(nome)
        basso = valore.lower()
        if valore.startswith("/"):
            continue                    #: relativo: segue l'host per costruzione
        if PRIMARIO in basso:
            continue                    #: assoluto e nomina il nodo
        colpe.append(f"{nome} = {valore}")
    assert guardate, "nessuna variabile guardata: il filtro è troppo stretto"
    assert not colpe, (
        "questi indirizzi non nominano il nodo:\n  " + "\n  ".join(colpe))

    #: …e nessuno dei nomi sbagliati compare, in nessuna forma
    for nome in guardate:
        v = reso[nome].lower()
        if v.startswith("/"):
            continue
        for cattivo in NOMI_SBAGLIATI:
            assert cattivo not in v, f"{nome} nomina {cattivo}: {reso[nome]}"


@needs_bash
def test_E_LE_QUATTRO_CHE_SBAGLIAVANO_sono_quelle_che_si_guardano(tmp_path):
    """La prova che la prova generale morde dove serve.

    Una regola che non nomina i suoi casi difficili può passare filtrando via
    proprio quelli. Qui si asserisce che le quattro variabili dell'episodio
    dell'8 settembre sono davvero fra quelle guardate o relative — non escluse.
    """
    reso, _servizi, _ = _reso_con_primario(tmp_path)
    for nome in ("EM_IIIF_PUBLIC", "EM_KEYCLOAK_CONSOLE_URL",
                 "EM_CATALOG_PUBLIC", "EM_FIELD_ASSISTANT_URL"):
        assert nome in reso, f"{nome} non è più resa: la prova non la copre"
        assert nome not in ESENTI, f"{nome} è stata esentata"
        assert _e_indirizzo(reso[nome]), f"{nome} = {reso[nome]!r} non è un indirizzo"
        v = reso[nome]
        assert v.startswith("/") or PRIMARIO in v, f"{nome} = {v}"


@needs_bash
def test_E_IL_PERCORSO_DI_IIIF_e_quello_che_risponde(tmp_path):
    """Questa asserzione esiste perché una mutazione ha dato ZERO rosse.

    Rimettere `/iiif` al posto di `/iiif/3` in `fcn-up.sh` non rompeva niente:
    la prova generale guarda l'HOST e quello restava giusto. Ma il percorso era
    sbagliato, misurato l'8 settembre 2026:

        https://em.localhost:8443/iiif    → 404
        https://em.localhost:8443/iiif/3  → 200

    ed è la forma che `app/main.py:1863` scrive come attesa. Era invisibile
    perché il compose cablava la riga e l'export era morto — leggere la
    variabile senza correggere il percorso avrebbe trasformato un valore morto
    in un valore vivo e sbagliato, che è peggio.
    """
    reso, _servizi, _ = _reso_con_primario(tmp_path)
    v = reso["EM_IIIF_PUBLIC"]
    assert v.endswith("/iiif/3"), (
        f"EM_IIIF_PUBLIC = {v!r}: `/iiif` dà 404, l'Image API 3 di Cantaloupe "
        f"sta su `/iiif/3` (e `app/main.py:1863` scrive quella forma)")
    #: …e la stessa forma la dice il messaggio d'errore del nodo, così le due
    #: non possono divergere in silenzio
    fonte = (DEV.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "/iiif/3" in fonte


@needs_bash
def test_LE_ESENZIONI_sono_ancora_quelle_che_credo(tmp_path):
    """Un'esenzione è un buco nella regola: quando smette di servire va via.

    `EM_SITE` deve continuare a contenere ENTRAMBI i nomi (il nodo risponde
    anche su `em.localhost`, e deve), e `EM_MINIO_CONSOLE_URL` deve continuare a
    essere loopback — se un giorno Caddy servisse la console di MinIO, quella
    esenzione andrebbe togliendo, non tenuta per abitudine.
    """
    reso, _servizi, _ = _reso_con_primario(tmp_path)
    assert PRIMARIO in reso["EM_SITE"] and "em.localhost" in reso["EM_SITE"]
    assert reso["EM_MINIO_CONSOLE_URL"].startswith("http://localhost:")


# ═══ 14 · UNA PORTA PER STRUMENTO, E OGNUNA SU UNA PROVA ═════════════════════
#
# Misurato l'8 settembre 2026 su `fcn.local`: la riga della stanza offriva sei
# porte e tre si comportavano male, in tre modi diversi. La causa era una sola —
# `rooms_ui/rooms.js` disegnava la porta `desktop` per tutti e tre gli strumenti
# senza chiedere niente a nessuno, mentre `addBrowserDoors`, sulla stessa riga
# dello schermo, disegnava quella `browser` solo dalla risposta del server e lo
# diceva in un commento:
#
#     `if (!target.browser) continue;` — no web build: no button, no lie
#
# La regola era enunciata per una porta e ignorata per l'altra.
#
# La tabella misurata, che è quasi l'inverso di quella che la pagina disegnava:
#
#   strumento     desktop (schema registrato)   browser (build web deployato)
#   EMStudio      sì  (bundle Tauri)            solo dove qualcuno l'ha messo
#   EMtools       no  (il link si INCOLLA)      mai: Blender non è un'app web
#   StratiField   no  (non esiste un'app)       sì, ed è la porta vera
#
# Ogni riga ha esattamente UNA porta che funziona su un nodo di campo.

import importlib.util as _ilu2

_spec_h = _ilu2.spec_from_file_location("handoff_mod",
                                        DEV.parent / "app" / "handoff.py")
HO = _ilu2.module_from_spec(_spec_h)
_spec_h.loader.exec_module(HO)

ROOMS_JS = (DEV.parent / "app" / "rooms_ui" / "rooms.js")
TAURI = (DEV.parent.parent / "EMStudio" / "apps" / "desktop" / "src-tauri")


def _porte(env=None):
    """Le porte per strumento, dalla risposta del server."""
    vecchio = dict(os.environ)
    try:
        os.environ.pop("EM_EMSTUDIO_WEB_URL", None)
        os.environ["EM_PUBLIC_BASE"] = "https://fcn.local:8443/em"
        os.environ["EM_FIELD_ASSISTANT_URL"] = "/chat/"
        if env:
            os.environ.update(env)
        d = HO.open_targets("test-casa")
    finally:
        os.environ.clear()
        os.environ.update(vecchio)
    return {n: {k for k in ("scheme", "paste", "browser") if k in v}
            for n, v in d["tools"].items()}


def test_UNA_PORTA_PER_STRUMENTO_su_un_nodo_fresco():
    """Un nodo fresco: nessun build web dichiarato tranne StratiField, che è
    un container di questa stack e c'è sempre."""
    p = _porte()
    assert p == {"emstudio": {"scheme"},
                 "blender": {"paste"},
                 "chatbot": {"browser"}}, p


def test_E_CON_UN_BUILD_WEB_EMSTUDIO_ne_ha_DUE():
    """Sulla macchina di chi sviluppa la porta `browser` torna, e non si perde
    niente: è l'altra metà della decisione di §4."""
    p = _porte({"EM_EMSTUDIO_WEB_URL": "/em/studio/"})
    assert p["emstudio"] == {"scheme", "browser"}, p
    #: …e gli altri due non cambiano
    assert p["blender"] == {"paste"} and p["chatbot"] == {"browser"}


def test_NESSUNO_SCHEMA_PER_CHI_NON_LO_REGISTRA():
    """La porta `scheme` esiste SOLO dove il software la registra.

    E `paste` non è un ripiego per tutti: StratiField non ha un'applicazione
    desktop, quindi non ha nemmeno dove incollare un link. Tre stati, non un
    booleano — un booleano avrebbe dato a StratiField una porta «incolla questo
    da qualche parte» che non porta in nessun posto.
    """
    p = _porte()
    assert "scheme" not in p["blender"] and "scheme" not in p["chatbot"]
    assert "paste" not in p["chatbot"], (
        "StratiField non ha un'app desktop: non c'è niente in cui incollare")
    #: e il link di `paste` è lo STESSO del `scheme`, sotto un nome che dice
    #: cosa farne. `_porte` mette e rimette l'ambiente, quindi la risposta va
    #: chiesta DENTRO quella finestra e non dopo — fuori, `EM_PUBLIC_BASE` non
    #: c'è e il nodo rifiuta di scrivere un link (giustamente).
    vecchio = dict(os.environ)
    try:
        os.environ["EM_PUBLIC_BASE"] = "https://fcn.local:8443/em"
        d = HO.open_targets("test-casa")
    finally:
        os.environ.clear(); os.environ.update(vecchio)
    assert d["tools"]["blender"]["paste"] == d["scheme"]


def test_EMSTUDIO_REGISTRA_LO_SCHEMA_e_lo_dice_il_suo_repo():
    """La prova che il fatto è MISURATO e non ripetuto da una lista.

    `desktop: "registered"` in `CONSUMERS` è vero perché il bundle di EMStudio
    lo dichiara. Questa prova guarda il repository di EMStudio: se quella
    dichiarazione sparisse, la porta `scheme` di EMStudio diventerebbe una
    bugia — e qui diventa rossa.
    """
    if not TAURI.is_dir():
        pytest.skip("EMStudio non è affiancato: la dichiarazione non si misura")
    #: SI PARSA, non si cerca come sottostringa — e la ragione è misurata: la
    #: prima versione di questa prova cercava `"deepLinkProtocols" in testo`, e
    #: la mutazione che rinominava la chiave in `deepLinkProtocolsDISATTIVATO`
    #: la lasciava VERDE, perché la sottostringa c'è ancora. Una prova che una
    #: mutazione non rompe non stava provando niente.
    conf = _json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    protocolli = (conf.get("bundle") or {}).get("deepLinkProtocols") or []
    schemi = {s for voce in protocolli for s in (voce.get("schemes") or [])}
    assert "stratigraph" in schemi, (
        f"il bundle di EMStudio non registra più `stratigraph`: {schemi}")
    #: …e il plugin che lo fa valere a runtime
    assert "tauri-plugin-deep-link" in (TAURI / "Cargo.toml").read_text(
        encoding="utf-8")
    rs = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    assert "tauri_plugin_deep_link::init()" in rs
    assert "register_all()" in rs
    #: E LA DISTINZIONE CHE NON VA PERSA: `register_all()` può fallire a
    #: runtime e il codice si limita a stamparlo, quindi «registrato nel
    #: bundle» non è «registrato su questo computer» — per cui `followScheme`
    #: resta la rete giusta.
    assert "could not register" in rs


def test_E_CONSUMERS_CONCORDA_col_repo_di_EMStudio():
    """I due fatti non devono poter divergere in silenzio."""
    if not TAURI.is_dir():
        pytest.skip("EMStudio non è affiancato")
    conf = _json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    protocolli = (conf.get("bundle") or {}).get("deepLinkProtocols") or []
    dichiara = any("stratigraph" in (v.get("schemes") or []) for v in protocolli)
    registra = HO.CONSUMERS["emstudio"].get("desktop") == "registered"
    assert dichiara == registra, (
        f"EMStudio dichiara lo schema: {dichiara}; CONSUMERS dice: {registra}")


def test_IL_CLIENT_NON_DISEGNA_UNA_PORTA_CHE_IL_SERVER_NON_HA_DATO():
    """§6 mutazione 3: la prova non deve misurare solo il server.

    Il pagliaio si toglie prima — questo file parla degli strumenti nella sua
    prosa.
    """
    page = ROOMS_JS.read_text(encoding="utf-8")
    codice = "\n".join(r for r in page.splitlines()
                       if not r.lstrip().startswith(("*", "/*", "//", "*/")))
    #: nessun nome di strumento nel codice: l'elenco lo dà il server
    for tool in ("emstudio", "blender", "chatbot"):
        assert f'"{tool}"' not in codice, f"{tool} è cablato nel client"
    #: ogni porta è dietro la presenza del suo campo nella risposta
    for door in ("scheme", "paste", "browser"):
        assert f"target.{door}" in codice, f"{door} non viene dalla risposta"
        assert f"if (target.{door})" in codice, (
            f"{door} è disegnata senza chiedere se c'è")
    #: e uno strumento senza porte non lascia un gruppo vuoto
    assert "if (!doors.length) continue" in codice


def test_L_ETICHETTA_E_STRATIFIELD_e_la_CHIAVE_e_chatbot():
    """§5. La chiave tecnica non cambia: è il nome del consumatore e del repo."""
    assert HO.CONSUMERS["chatbot"]["label"] == "StratiField"
    assert "chatbot" in HO.CONSUMERS
    #: …e nessuna etichetta dice più «Field assistant» dove la vede una persona
    salute = (DEV.parent / "app" / "node_health.py").read_text(encoding="utf-8")
    i18n = (DEV.parent / "app" / "node_admin" / "i18n.js").read_text(
        encoding="utf-8")
    assert '"Field assistant"' not in salute
    assert '"Field assistant"' not in i18n
    assert '"Assistente di campo"' not in i18n, (
        "la quinta occorrenza, quella italiana, che una grep inglese non vede")
    assert i18n.count('"service.stratigraph-chatbot": "StratiField"') == 2, \
        "en e it"


@needs_bash
def test_UN_NODO_FRESCO_NON_DICHIARA_un_build_web_di_EMSTUDIO(tmp_path):
    """§4, e questa asserzione esiste perché una mutazione ha dato ZERO rosse.

    Rimettere il default `/em/studio` a `:528` non rompeva niente: la prova
    generale ammette i valori relativi, e `/em/studio` è relativo. Ma la
    proprietà che quel default viola è un'altra — **un nodo fresco non deve
    AFFERMARE che qui c'è un build web di EMStudio**, perché non c'è e non ci
    sarà: EMStudio è un quarto repository con un dev server che si avvia a mano,
    e la porta dava il 502 con la spiegazione lunga.

    È lo stesso difetto che `rooms.js` vieta al client («a client that assumed
    one would offer a button that 404s»), un piano più sotto: qui ad assumere il
    deploy era il deployment.

    Misurato sull'ambiente RESO, con `.env.dev` copiato da `.env.dev.example` —
    cioè la macchina di chi ha appena clonato.
    """
    reso, _servizi, _ = _reso_con_primario(tmp_path)
    for nome in ("EM_EMSTUDIO_WEB_URL", "EM_CATALOG_EMSTUDIO_URL"):
        assert reso.get(nome, "") == "", (
            f"{nome} = {reso.get(nome)!r} su un nodo fresco: è l'affermazione "
            f"«qui c'è un build web di EMStudio», e non c'è. Lo mette "
            f"`.env.dev`, che è il file di chi sa di aver avviato npm run dev.")
    #: …e le due righe di `.env.dev.example` restano COMMENTATE, che è dove la
    #: decisione vive. `EM_CATALOG_EMSTUDIO_URL` era scoperta: nominava
    #: `localhost:5173`, cioè la macchina di chi CLICCA — lo stesso difetto
    #: dell'episodio dell'8 settembre, un piano più sotto.
    esempio = (DEV / ".env.dev.example").read_text(encoding="utf-8")
    for nome in ("EM_EMSTUDIO_WEB_URL", "EM_CATALOG_EMSTUDIO_URL",
                 "EM_FIELD_ASSISTANT_URL"):
        attive = [r for r in esempio.splitlines()
                  if r.strip().startswith(nome + "=")]
        assert not attive, f"{nome} è attiva in .env.dev.example: {attive}"


def test_E_SULLA_MACCHINA_DI_CHI_SVILUPPA_la_porta_browser_torna():
    """L'altra metà, e per questo non si perde niente.

    Chi ha avviato il dev server scommenta la riga, riceve entrambe le porte, e
    continua a ricevere il 502 didattico quando si dimentica di avviarlo.
    """
    p = _porte({"EM_EMSTUDIO_WEB_URL": "/em/studio/"})
    assert p["emstudio"] == {"scheme", "browser"}
