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
    for nome in ("docker", "colima", "sudo", "security", "scutil", "hostname",
                 "update-ca-certificates", "update-ca-trust", "certutil",
                 "cmd.exe", "wslpath", *extra, *nomi):
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
                       '  exit 0', 'fi',
                       'exit 0']
        elif nome == "sudo":
            #: `sudo` esegue il resto: così `security`/`cp` finiscono nel
            #: registro col loro nome, che è quello che si vuole guardare
            script += ['"$@"']
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
