#!/usr/bin/env bash
# fcn-trust-ca — estrai la CA interna di Caddy dell'FCN e falla fidare a questa
# macchina. Serve UNA volta (e di nuovo dopo ./fcn-down.sh --wipe, che rigenera
# la CA).
#
#   ./fcn-trust-ca.sh                 # fidati della CA su QUESTA macchina
#   ./fcn-trust-ca.sh --export-only   # solo estrai il .crt (per un altro computer)
#
# TRE SISTEMI, e non sono lo stesso comando:
#
#   macOS     il portachiavi di sistema             (sudo security add-trusted-cert)
#   Linux     lo store di sistema + quello di Chrome, che sono DUE
#   Windows   lo store di Windows, e questo script gira in WSL o in Git Bash
#             → esporta e STAMPA il comando da dare come Amministratore
#
# Su un ALTRO computer: copia il file caddy-em-root.crt e lancia lì questo stesso
# script con il .crt già a posto, oppure il comando che stampa per il tuo sistema.
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} /^[[:space:]]*$/{next} {exit}' "$0"; exit 0
fi
set -euo pipefail
cd "$(dirname "$0")"
. ./platform.sh

CONT="em-dev-caddy"
CRT="$HOME/caddy-em-root.crt"
HTTPS_PORT="${HTTPS_PORT:-8443}"
PROVA="https://em.localhost:${HTTPS_PORT}/em/v1/health"

echo "▶ estraggo il root CA dal container ${CONT}…"
docker cp "$CONT:/data/caddy/pki/authorities/local/root.crt" "$CRT"
echo "  salvato in $CRT"

if [ "${1:-}" = "--export-only" ]; then
  echo "✔ solo export. Copialo sull'altra macchina e fidati lì col comando del suo sistema"
  echo "  (rilancia questo script là, oppure guarda i tre rami in testa a questo file)."
  exit 0
fi

case "$(sg_os)" in

macos)
  echo "▶ macOS · lo aggiungo al portachiavi di sistema (serve la password)…"
  sudo security add-trusted-cert -d -r trustRoot \
       -k /Library/Keychains/System.keychain "$CRT"
  echo "✔ CA fidata. RIAVVIA Safari (esci del tutto e riapri) e ricarica:"
  echo "    $PROVA"
  ;;

linux)
  # ── DUE STORE, E QUESTO È IL PUNTO ────────────────────────────────────────
  #
  # `update-ca-certificates` sistema OpenSSL — cioè `curl`, `python`, `git`. Il
  # browser no: Chrome e Chromium su Linux hanno il proprio store NSS in
  # `~/.pki/nssdb`, e un certificato fidato dal sistema **non** è fidato dal
  # browser. È lo stesso genere di trappola dei due store di Windows, sulla
  # stessa macchina invece che fra due sistemi, e nessuno la vede finché non ci
  # sbatte: `curl` funziona, la pagina dà un errore di certificato.
  #
  # Firefox ha un terzo store, per profilo, e si fa dalle sue preferenze:
  # detto e non automatizzato, perché indovinare quale profilo è quello aperto
  # è il modo di scrivere in quello sbagliato.
  echo "▶ Linux · lo store di SISTEMA (serve la password)…"
  if command -v update-ca-certificates >/dev/null 2>&1; then
    # Debian, Ubuntu, e derivate: la directory vuole l'estensione `.crt`
    sudo cp "$CRT" /usr/local/share/ca-certificates/caddy-em-root.crt
    sudo update-ca-certificates
  elif command -v update-ca-trust >/dev/null 2>&1; then
    # Fedora, RHEL, openSUSE: altra directory, altro comando
    sudo cp "$CRT" /etc/pki/ca-trust/source/anchors/caddy-em-root.crt
    sudo update-ca-trust
  else
    echo "✖ non trovo né update-ca-certificates né update-ca-trust." >&2
    echo "  Il .crt è in $CRT: aggiungilo allo store della tua distribuzione." >&2
    exit 1
  fi
  echo "  ✔ store di sistema: fatto (curl, python, git)."

  echo "▶ Linux · e ora il browser, che ha il PROPRIO store…"
  if command -v certutil >/dev/null 2>&1; then
    mkdir -p "$HOME/.pki/nssdb"
    # `-d sql:` è la forma nuova del database NSS; senza `sql:` certutil
    # scrive nel formato vecchio, che Chrome recente non legge.
    certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n "Caddy EM root" \
             -i "$CRT" 2>/dev/null \
      && echo "  ✔ Chrome/Chromium: fatto." \
      || echo "  ⚠ certutil non ha scritto in ~/.pki/nssdb: fallo dalle impostazioni del browser."
  else
    echo "  ⚠ certutil non è installato (pacchetto libnss3-tools)."
    echo "    Senza, curl funziona e IL BROWSER NO. Due strade:"
    echo "      sudo apt install libnss3-tools   # poi rilancia questo script"
    echo "      …oppure importa $CRT dalle impostazioni del browser (Autorità)."
  fi
  echo "  ⚠ Firefox ha un terzo store, per profilo: Impostazioni → Certificati →"
  echo "    Autorità → Importa, e spunta «identificare siti web»."
  echo "✔ Ricarica (riavvia il browser): $PROVA"
  ;;

wsl|windows)
  # ── DUE SISTEMI, DUE STORE, E LO SCRIPT STA NEL POSTO SBAGLIATO ───────────
  #
  # Questo script gira in WSL o in Git Bash. Il browser che deve fidarsi della
  # CA è quello di **Windows**, e Windows ha il proprio store: niente di ciò che
  # si fa da qui lo tocca. È la trappola che il prompt del 7 ottobre nomina, e
  # la risposta onesta è **non fingere**: si esporta dove Windows lo vede e si
  # stampa il comando esatto, che va dato in un prompt da Amministratore.
  #
  # Non si tenta `powershell.exe` da qui: da WSL si può chiamare, e chiederebbe
  # l'elevazione in una finestra che questo terminale non controlla — un
  # comando che a volte funziona è peggio di una riga da copiare.
  DEST=""
  if [ -n "${USERPROFILE:-}" ]; then
    DEST="$USERPROFILE"
  elif command -v wslpath >/dev/null 2>&1 && command -v cmd.exe >/dev/null 2>&1; then
    #: il profilo dell'utente Windows visto da WSL. `tr -d '\r'` perché
    #: `cmd.exe` chiude ogni riga con CRLF e il path risulta inesistente.
    WINHOME="$(cmd.exe /c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')"
    [ -n "$WINHOME" ] && DEST="$(wslpath -u "$WINHOME" 2>/dev/null || true)"
  fi

  if [ -n "$DEST" ] && [ -d "$DEST" ]; then
    cp "$CRT" "$DEST/caddy-em-root.crt"
    echo "  copiato dove Windows lo vede: $DEST/caddy-em-root.crt"
    WINPATH="%USERPROFILE%\\caddy-em-root.crt"
  else
    echo "  ⚠ non ho trovato il profilo utente di Windows da qui."
    echo "    Il .crt è in $CRT: copialo a mano in Windows."
    WINPATH="C:\\percorso\\caddy-em-root.crt"
  fi

  cat <<MSG

▶ ORA IL PASSO CHE VA FATTO DA WINDOWS, e questo script non lo può fare.
  Apri **PowerShell come Amministratore** e dai:

      Import-Certificate -FilePath "$WINPATH" \\
        -CertStoreLocation Cert:\\LocalMachine\\Root

  oppure, in un **Prompt dei comandi come Amministratore**:

      certutil -addstore -f Root "$WINPATH"

  Poi CHIUDI DEL TUTTO il browser e riapri (Chrome ed Edge leggono lo store di
  Windows all'avvio), e ricarica:
      $PROVA

  ⚠ DUE STORE, e la confusione costa un'ora: il comando qui sopra convince il
    BROWSER di Windows. `curl` **dentro WSL** usa lo store di Linux e continuerà
    a rifiutare — se ti serve anche quello, lancia il ramo Linux di questo
    script dentro WSL (\`sudo cp … && sudo update-ca-certificates\`).
    Firefox ha un terzo store, suo, per profilo.
MSG
  ;;

*)
  echo "✖ non riconosco questo sistema ($(uname -s 2>/dev/null))." >&2
  echo "  Il .crt è in $CRT: aggiungilo allo store del tuo sistema e del tuo browser." >&2
  exit 1
  ;;
esac

echo "  (dopo un ./fcn-down.sh --wipe la CA cambia → rilancia questo script.)"
