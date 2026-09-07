# platform.sh — che macchina è questa, e cosa serve davvero accendere.
#
# Sourced, non eseguito:  . "$(dirname "$0")/platform.sh"
#
# ════════════════════════════════════════════════════════════════════════════
# ## PERCHÉ ESISTE, E IL MOTIVO NON È TEORICO
#
# Nicolò Paraciani non ha un Mac, e deve potersi fare un'idea dei servizi **come
# amministratore** — la console del nodo, la salute, lo storage. Misurato il
# 7 ottobre 2026, prima di qualunque package manager:
#
#     fcn-up.sh:39      `colima status` chiamato SEMPRE, senza chiedersi dove gira
#     fcn-up.sh:47      `scutil --get LocalHostName`, macOS e basta
#     fcn-down.sh:53    `colima stop`, idem
#     fcn-trust-ca.sh:30  `sudo security add-trusted-cert`, il portachiavi di macOS
#
# Su Linux Docker gira nativo e colima non serve. Su Windows colima non esiste, e
# lo script gira in WSL o in Git Bash — ma il browser che deve fidarsi della CA
# è quello di **Windows**: due negozi di certificati diversi, ed è la trappola
# che nessuno vede finché non ci sbatte.
#
# ## LA REGOLA: RILEVARE, NON ASSUMERE
#
# E per Docker la domanda vera non è «che sistema è questo» ma **«docker
# risponde?»**. `sg_docker_ready` la fa; colima resta il RIMEDIO su macOS quando
# la risposta è no, non un passo obbligatorio prima di averla chiesta.
#
# ## NESSUN PACKAGE MANAGER
#
# Non brew, non chocolatey: chiedono diritti di amministratore su un portatile
# istituzionale, ed è il punto in cui un IT dice di no. Sull'host servono Docker
# e una shell; tutto il resto è già nei container.

#: Che sistema è questo — `macos` | `linux` | `wsl` | `windows` | `unknown`.
#:
#: `wsl` è separato da `linux` di proposito: `uname -s` dice `Linux` e i comandi
#: sono quelli di Linux, ma il BROWSER è di Windows e la CA va aggiunta dall'altro
#: lato. Chiamarlo `linux` sarebbe la risposta giusta a una domanda diversa.
sg_os() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin) echo macos ;;
    Linux)
      #: `/proc/version` porta «microsoft» sotto WSL1 e WSL2. `WSL_DISTRO_NAME`
      #: è più leggibile ma non c'è quando lo script è lanciato da un servizio.
      if [ -n "${WSL_DISTRO_NAME:-}" ] || \
         grep -qiE "microsoft|wsl" /proc/version 2>/dev/null; then
        echo wsl
      else
        echo linux
      fi
      ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *) echo unknown ;;
  esac
}

#: Docker risponde? La domanda che decide, e l'unica che non dipende dal sistema.
sg_docker_ready() { docker info >/dev/null 2>&1; }

#: Il nome Bonjour di questa macchina, o "" — su macOS `scutil`, altrove il
#: nome che l'host dichiara. Mai un IP: la CA interna di Caddy non fa
#: certificati per un IP nudo.
sg_local_hostname() {
  case "$(sg_os)" in
    macos) scutil --get LocalHostName 2>/dev/null || true ;;
    linux|wsl)
      #: `hostname -s` c'è su ogni distribuzione; `hostnamectl` no (systemd).
      hostname -s 2>/dev/null || true
      ;;
    *) echo "" ;;
  esac
}

#: Assicura che Docker risponda, con il rimedio giusto per QUESTA macchina.
#: Ritorna 0 quando risponde, 1 quando non c'è niente da fare da qui.
sg_ensure_docker() {
  if sg_docker_ready; then return 0; fi
  case "$(sg_os)" in
    macos)
      #: colima SOLO qui, e solo perché la risposta è stata no. Se non è
      #: installato lo si dice invece di far fallire un `command not found`
      #: dentro un `if`.
      if command -v colima >/dev/null 2>&1; then
        echo "▶ Docker non risponde: avvio Colima…"
        colima start --cpu 4 --memory 8 --network-address
        docker context use colima >/dev/null 2>&1 || true
        sg_docker_ready && return 0
      fi
      echo "✖ Docker non risponde su questo Mac." >&2
      echo "  Avvia Colima (\`colima start\`) o Docker Desktop, poi rilancia." >&2
      return 1
      ;;
    linux)
      echo "✖ Docker non risponde." >&2
      echo "  Su Linux il demone è di sistema: \`sudo systemctl start docker\`," >&2
      echo "  e l'utente deve stare nel gruppo \`docker\` (\`sudo usermod -aG docker \$USER\`," >&2
      echo "  poi riapri la sessione). NIENTE colima: qui non serve." >&2
      return 1
      ;;
    wsl)
      echo "✖ Docker non risponde dentro WSL." >&2
      echo "  Due strade, e sono diverse:" >&2
      echo "   · Docker Desktop su Windows, con l'integrazione WSL accesa per" >&2
      echo "     questa distribuzione (Settings → Resources → WSL integration);" >&2
      echo "   · oppure il demone dentro la distribuzione:" >&2
      echo "     \`sudo service docker start\`." >&2
      return 1
      ;;
    windows)
      echo "✖ Docker non risponde." >&2
      echo "  Questa shell è Git Bash/MSYS: serve Docker Desktop avviato su" >&2
      echo "  Windows. NIENTE colima: su Windows non esiste." >&2
      return 1
      ;;
    *)
      echo "✖ Docker non risponde, e non riconosco questo sistema" \
           "($(uname -s 2>/dev/null))." >&2
      return 1
      ;;
  esac
}
