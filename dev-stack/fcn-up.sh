#!/usr/bin/env bash
# fcn-up — accendi il Field Computing Node: Caddy+https su hostname (mai IP nudo),
# e tira su l'intera stack StratiGraph.
#
#   ./fcn-up.sh                 # locale: https://em.localhost:8443 (+ serve anche il nome Bonjour .local)
#   ./fcn-up.sh mac.local       # host PRIMARIO = un hostname risolvibile (per l'altro computer)
#   ./fcn-up.sh --demo          # …e POPOLA: studi, stanze, immagini IIIF. Idempotente.
#   ./fcn-up.sh --local-s3d     # s3Dgraphy dal CHECKOUT LOCALE (editi e testi live)
#   ./fcn-up.sh mac.local --local-s3d
#
# NB: la CA interna di Caddy NON fa certificati per un IP nudo → per l'altro computer
# serve un HOSTNAME (il nome Bonjour `<mac>.local`, o /etc/hosts, o un dominio vero),
# non 172.x.x.x. E la rete deve vedersi (hotspot che isola i client → travel-router/Tailscale).
#
# DATI: i volumi (studi, stanze, bucket asset+corpus, realm, CA di Caddy) PERSISTONO fra i
# riavvii — `./fcn-down.sh` poi `./fcn-up.sh` (o un restart dei container) ritrova tutto.
# Solo `./fcn-down.sh --wipe` cancella i volumi (dati persi; dopo serve ./fcn-trust-ca.sh).
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} /^[[:space:]]*$/{next} {exit}' "$0"; exit 0
fi
set -euo pipefail
cd "$(dirname "$0")"                     # stratigraph-server/dev-stack

HTTPS_PORT="${HTTPS_PORT:-8443}"
DEV_REALM="${DEV_REALM:-em-dev}"

# ── argomenti: hostname PRIMARIO opzionale + --local-s3d ─────────────────────
LOCAL_S3D="no"; DEMO="no"; ARG_HOST=""
for a in "$@"; do
  case "$a" in
    --local-s3d) LOCAL_S3D="yes" ;;
    --demo)      DEMO="yes" ;;
    -*) echo "flag sconosciuto: $a (usa --local-s3d | --demo)"; exit 2 ;;
    *) ARG_HOST="$a" ;;
  esac
done

# ── 1 · Colima su ────────────────────────────────────────────────────────────
if ! colima status >/dev/null 2>&1; then
  echo "▶ avvio Colima…"
  colima start --cpu 4 --memory 8 --network-address
fi
docker context use colima >/dev/null 2>&1 || true

# ── 2 · host primario (browser) + gli indirizzi che Caddy serve ──────────────
PRIMARY="${ARG_HOST:-em.localhost}"                 # dove punta il browser (URL pubblici)
BONJOUR="$(scutil --get LocalHostName 2>/dev/null || true)"; [ -n "$BONJOUR" ] && BONJOUR="${BONJOUR}.local"
# Caddy serve em.localhost SEMPRE, + il primario e il nome Bonjour se diversi (hostname, mai IP)
addrs="https://em.localhost"
[ "$PRIMARY" != "em.localhost" ] && addrs="$addrs, https://$PRIMARY"
[ -n "$BONJOUR" ] && [ "$BONJOUR" != "em.localhost" ] && [ "$BONJOUR" != "$PRIMARY" ] && addrs="$addrs, https://$BONJOUR"
export EM_SITE="$addrs"
export EM_DEV_DOMAIN="$PRIMARY"

# ── 3 · gli URL PUBBLICI puntano all'host primario ───────────────────────────
export EM_IIIF_PUBLIC="https://${PRIMARY}:${HTTPS_PORT}/iiif"
export EM_CATALOG_EMSTUDIO_URL="https://${PRIMARY}:${HTTPS_PORT}"
# ⚠ VERIFICA: l'issuer OIDC dipende da come Caddy espone Keycloak nel Caddyfile.dev
export OIDC_ISSUER="https://${PRIMARY}:${HTTPS_PORT}/auth/realms/${DEV_REALM}"
# …and the origin Keycloak stamps into every token, so `iss` is ONE string
# whichever door it came through (the proxy, or the direct port).
export OIDC_PUBLIC_ORIGIN="https://${PRIMARY}:${HTTPS_PORT}"
# What a HANDOFF link names (`stratigraph://open?server=…`): the same host the
# browser is on, not a hardcoded em.localhost.
export EM_PUBLIC_BASE="https://${PRIMARY}:${HTTPS_PORT}/em"

# ── 4 · su (con l'override s3Dgraphy-locale se richiesto) ─────────────────────
COMPOSE=(docker-compose --env-file .env.dev -f docker-compose.dev.yml)
if [ "$LOCAL_S3D" = "yes" ]; then
  COMPOSE+=(-f docker-compose.local-s3d.yml)
  echo "▶ modo s3Dgraphy LOCALE: StratiGraph Server/StratiGraph Catalog useranno ../../s3Dgraphy/src (edita e riavvia per testare)."
fi
"${COMPOSE[@]}" --profile https up -d --build

# ── 4-bis · --demo: aspetta, poi popola ──────────────────────────────────────
#
# Il pulsantone è un FLAG, non uno script nuovo: `up` accendeva già tutto e
# stampava gli indirizzi, ma restavano tre `python dev-stack/seed_*.py` da
# ricordare. Qui vengono eseguiti quelli che ci sono già.
#
# I seed sono idempotenti per costruzione (riusano per id/nome invece di
# creare), quindi due `--demo` di fila lasciano lo stesso stato. Non è una
# proprietà da rompere: se un seed comincia a duplicare, si aggiusta lui.
if [ "$DEMO" = "yes" ]; then
  HEALTH="https://${PRIMARY}:${HTTPS_PORT}/em/v1/health"
  echo
  echo "▶ --demo: aspetto che il nodo risponda (${HEALTH})…"
  # 90s: la PRIMA accensione costruisce le immagini e Keycloak importa il realm.
  # Se scade lo DICE e si ferma qui — proseguire al buio significherebbe tre
  # seed che falliscono uno dopo l'altro con tre errori diversi, nessuno dei
  # quali è la causa.
  ready="no"
  for _ in $(seq 1 45); do
    if curl -sk -o /dev/null --max-time 2 "$HEALTH"; then ready="yes"; break; fi
    sleep 2
  done
  if [ "$ready" != "yes" ]; then
    echo "✗ il nodo non ha risposto entro 90s. NON popolo: guarda i log con"
    echo "  docker-compose -f docker-compose.dev.yml logs --tail=40 stratigraph-server"
    exit 1
  fi
  echo "✔ nodo su. Popolo…"
  # L'ORDINE conta: le immagini prima, perché seed-demo cita gli asset che
  # smoke_iiif mette nel bucket.
  for step in smoke_iiif.py seed_rooms.py seed-demo.py; do
    echo
    echo "── $step ──────────────────────────────────────────────"
    if ! python3 "$step"; then
      echo "✗ $step è fallito. Gli altri passi NON sono stati eseguiti."
      exit 1
    fi
  done
fi

# ── 5 · indirizzi + promemoria ───────────────────────────────────────────────
cat <<EOF

✔ FCN acceso$( [ "$LOCAL_S3D" = "yes" ] && echo " (s3Dgraphy locale)" ). Caddy serve: ${EM_SITE}
  Su questo computer:   https://${PRIMARY}:${HTTPS_PORT}/em/v1/health
  Le mie stanze:        https://${PRIMARY}:${HTTPS_PORT}/em/rooms/
  Console del nodo:     https://${PRIMARY}:${HTTPS_PORT}/em/admin/
  Catalogo:             https://${PRIMARY}:${HTTPS_PORT}/catalog/ui/
  Assistente di campo:  https://${PRIMARY}:${HTTPS_PORT}/chat/
$( [ -n "$BONJOUR" ] && [ "$BONJOUR" != "$PRIMARY" ] && echo "  Per l'ALTRO computer: https://${BONJOUR}:${HTTPS_PORT}/em/v1/health   (via Bonjour/mDNS)" )

Note:
  · la ROOT (/) è un cartello: elenca le rotte vere. Tutto il resto dà 404 (e non
    più 200 con un banner, che faceva sembrare "irraggiungibile" un nodo acceso).
  · certificato: se il browser lo rifiuta → ./fcn-trust-ca.sh (una volta; e dopo ogni --wipe).
$( # RILEVATO, mai eseguito: fcn-trust-ca.sh chiede una password di sistema, e uno
   # script che si blocca su un prompt dentro un giro non presidiato è una trappola.
   # La sonda è diretta — curl SENZA -k riesce solo se il sistema si fida della CA.
   # Se l'host non risponde affatto stampa comunque l'avviso: un promemoria in più
   # costa una riga, un blocco silenzioso costa un pomeriggio.
   if ! curl -s -o /dev/null --max-time 4 "https://${PRIMARY}:${HTTPS_PORT}/em/v1/health"; then
     echo "  · ⚠ LA CA NON È FIDATA su questo Mac: il browser rifiuterà la pagina e"
     echo "      un service worker non si registrerà. Rimedio, UNA volta:  ./fcn-trust-ca.sh"
     echo "      (chiede la password di sistema, per questo non lo faccio io.)"
   fi )
  · dati: studi/stanze/asset/corpus PERSISTONO fra i riavvii (volumi named). ./fcn-down.sh li
    tiene; solo ./fcn-down.sh --wipe li cancella.
  · altro computer: serve un HOSTNAME (mai IP nudo, rompe il TLS della CA interna) e che le
    due macchine si vedano in rete (hotspot che isola → travel-router · Internet-Sharing · Tailscale).
    Per usarlo come primario:  ./fcn-up.sh ${BONJOUR:-<mac>.local}
$( [ "$LOCAL_S3D" = "yes" ] && echo "  · dopo aver editato s3Dgraphy:  docker-compose -f docker-compose.dev.yml -f docker-compose.local-s3d.yml restart stratigraph-server stratigraph-catalog" )
Giù:  ./fcn-down.sh   (o --stop / --wipe / --colima)
EOF
