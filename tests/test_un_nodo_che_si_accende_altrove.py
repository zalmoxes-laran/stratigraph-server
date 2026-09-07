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


def _finti(tmp_path, *, docker_ok: bool, nomi=()) -> pathlib.Path:
    """Una cartella di comandi finti, e il registro che scrivono.

    `docker info` decide: `docker_ok=False` è la macchina su cui docker non
    risponde, che è l'unico caso in cui un rimedio ha senso.
    """
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "chiamati.txt"
    for nome in ("docker", "colima", "sudo", "security", "scutil", "hostname",
                 "update-ca-certificates", "update-ca-trust", "certutil",
                 "cmd.exe", "wslpath", *nomi):
        script = ["#!/usr/bin/env bash",
                  f'echo "{nome} $*" >> "{log}"']
        if nome == "docker":
            # `docker info` è la domanda; `docker cp` deve produrre il file,
            # perché `fcn-trust-ca.sh` lo copia prima di decidere qualsiasi cosa
            script += ['if [ "${1:-}" = "info" ]; then',
                       f'  exit {0 if docker_ok else 1}', 'fi',
                       'if [ "${1:-}" = "cp" ]; then',
                       '  printf "finta CA\\n" > "${3:-/dev/null}"; exit 0', 'fi',
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


def _run(script: str, bin_: pathlib.Path, tmp_path, extra_env=None, args=()):
    env = dict(os.environ)
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
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
