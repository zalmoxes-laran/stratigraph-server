#!/usr/bin/env bash
# fcn-down — spegni il Field Computing Node.
#
#   ./fcn-down.sh            # spegnimento pulito: rimuove i container, TIENE i dati
#                            #   (studi, stanze, bucket, realm, CA di Caddy) → riparte con ./fcn-up.sh
#   ./fcn-down.sh --stop     # solo pausa: container fermati ma non rimossi (ripartenza in secondi)
#   ./fcn-down.sh --wipe     # ⚠ AZZERA TUTTO: cancella anche i volumi (dati persi)
#   ./fcn-down.sh --colima   # dopo lo spegnimento, ferma anche la VM Colima (libera RAM)
#
# I flag si combinano: es.  ./fcn-down.sh --wipe --colima
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} /^[[:space:]]*$/{next} {exit}' "$0"; exit 0
fi
set -euo pipefail
cd "$(dirname "$0")"                     # stratigraph-server/dev-stack
. ./platform.sh

# `--profile engine` here and NOT in fcn-up: the engine is opt-in to start
# (2.62 GB, see the compose) but "down" has to mean down — otherwise an
# engine somebody turned on by hand outlives the stack and keeps its volume.
COMPOSE=(docker-compose -f docker-compose.dev.yml --profile https --profile engine)
MODE="down"; STOP_COLIMA="no"
for a in "$@"; do
  case "$a" in
    --stop)   MODE="stop" ;;
    --wipe|-v) MODE="wipe" ;;
    --colima) STOP_COLIMA="yes" ;;
    *) echo "flag sconosciuto: $a (usa --stop | --wipe | --colima)"; exit 2 ;;
  esac
done

case "$MODE" in
  stop)
    echo "⏸  fermo i container (li tengo, ripartenza in secondi)…"
    "${COMPOSE[@]}" stop
    ;;
  down)
    echo "▶ spegnimento pulito: rimuovo i container, TENGO i dati…"
    # `--remove-orphans`, e non è profilassi: quando un servizio ESCE dal compose
    # il suo container resta addosso alla macchina, perché porta ancora la
    # `com.docker.compose.project=em-dev` e nessuno lo va a cercare. Misurato il
    # 7 ottobre con pyarchinit-mini: `docker-compose ps --all` lo elencava
    # ancora, come servizio `pyarchinit-mini`, ore dopo che il blocco era
    # uscito dal file. Un `down` che lo lascia in piedi vuol dire che il
    # prossimo `up` gira accanto a un fantasma che serve ancora la sua rotta.
    "${COMPOSE[@]}" down --remove-orphans
    echo "   i volumi (studi/stanze/bucket/realm/CA) restano; ./fcn-up.sh riparte da lì."
    ;;
  wipe)
    echo "⚠  --wipe: sto per CANCELLARE tutti i dati dell'FCN (studi, foto, utenti, e la CA di Caddy)."
    echo "   Dopo un wipe, l'altro computer dovrà RI-FIDARSI della nuova CA."
    read -r -p "   Scrivi 'wipe' per confermare: " ok
    [ "$ok" = "wipe" ] || { echo "annullato."; exit 1; }
    "${COMPOSE[@]}" down -v --remove-orphans
    echo "   tutto azzerato; il prossimo ./fcn-up.sh ri-semina bucket e realm da capo."
    ;;
esac

if [ "$STOP_COLIMA" = "yes" ]; then
  #: SOLO dove Colima esiste. `--colima` su Linux o su Windows chiedeva una
  #: cosa che non c'è, e `|| true` la rendeva un silenzio invece di una frase.
  if [ "$(sg_os)" = "macos" ] && command -v colima >/dev/null 2>&1; then
    echo "▶ fermo Colima…"
    colima stop || true
  else
    echo "· niente Colima su questo sistema ($(sg_os)): non c'è nulla da fermare."
  fi
fi

echo "✔ FCN spento (${MODE})."
