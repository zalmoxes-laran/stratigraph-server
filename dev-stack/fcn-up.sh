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

# CHE MACCHINA È QUESTA. `platform.sh` non assume: chiede.
. ./platform.sh

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

# ── 1 · Docker che risponde ──────────────────────────────────────────────────
#
# Era `colima status` SEMPRE, e Colima è di macOS: su Linux il demone è di
# sistema, su Windows non esiste. La domanda vera non è «che sistema è questo»
# ma **«docker risponde?»** — e colima è il rimedio su macOS quando la risposta
# è no, non un passo prima di averla chiesta.
sg_ensure_docker || exit 1

# ── 2 · host primario (browser) + gli indirizzi che Caddy serve ──────────────
PRIMARY="${ARG_HOST:-em.localhost}"                 # dove punta il browser (URL pubblici)
# Il nome con cui l'altro computer può raggiungere questo (mai un IP: la CA
# interna di Caddy non fa certificati per un IP nudo). `scutil` su macOS,
# `hostname -s` altrove — la differenza sta in `platform.sh`, non qui.
BONJOUR="$(sg_local_hostname)"; [ -n "$BONJOUR" ] && BONJOUR="${BONJOUR}.local"
# Caddy serve em.localhost SEMPRE, + il primario e il nome Bonjour se diversi (hostname, mai IP)
addrs="https://em.localhost"
[ "$PRIMARY" != "em.localhost" ] && addrs="$addrs, https://$PRIMARY"
[ -n "$BONJOUR" ] && [ "$BONJOUR" != "em.localhost" ] && [ "$BONJOUR" != "$PRIMARY" ] && addrs="$addrs, https://$BONJOUR"
export EM_SITE="$addrs"
export EM_DEV_DOMAIN="$PRIMARY"

# ── 3 · gli URL PUBBLICI puntano all'host primario ───────────────────────────
# `/iiif/3` E NON `/iiif`: misurato l'8 settembre 2026, `/iiif` dà 404 e
# `/iiif/3` dà 200 — è l'Image API 3 di Cantaloupe, e `app/main.py:1863` scrive
# la forma attesa (`https://host/iiif/3`). Questo export era MORTO perché il
# compose cablava la riga, quindi il percorso sbagliato non si era mai visto:
# leggere la variabile senza correggere qui avrebbe rotto IIIF.
export EM_IIIF_PUBLIC="https://${PRIMARY}:${HTTPS_PORT}/iiif/3"
# ── §3 · QUESTO EXPORT È STATO TOLTO, e la sua assenza è la correzione ──────
#
# Era:  export EM_CATALOG_EMSTUDIO_URL="https://${PRIMARY}:${HTTPS_PORT}"
#
# cioè un'ORIGINE NUDA, senza percorso. Il catalogo la consuma come base di
# EMStudio (`stratigraph-catalog/app/deeplink.py:120-124`):
#
#     web = _base("EM_CATALOG_EMSTUDIO_URL")
#     "web": f"{web}/?study={quoted}&emjson=…"
#
# quindi il link diventava `https://<host>:8443/?study=…`, la HOME del nodo.
# Misurato dal vivo l'8 settembre, chiedendolo al catalogo che gira:
#
#     web = https://em.localhost:8443/?study=studio-x&emjson=…   → 302
#     https://em.localhost:8443/em/studio/?study=studio-x        → 200
#
# E NON serviva un host primario per rompersi: `PRIMARY` vale `em.localhost`
# quando non si passa niente, l'ambiente di shell batte `--env-file`, quindi
# questo export vinceva SEMPRE sul default `/em/studio` del compose. Il rinomino
# non peggiorava questa variabile: era già rotta in entrambe le direzioni.
#
# La correzione è togliere l'export, non aggiustarlo: è un indirizzo sullo stesso
# nodo, quindi sta in categoria 1 e il default relativo del compose lo segue da
# sé. Un export assoluto era esattamente lo sbaglio che la regola nomina.
# (Nessuna riga cambiata in stratigraph-catalog.)
# ⚠ VERIFICA: l'issuer OIDC dipende da come Caddy espone Keycloak nel Caddyfile.dev
export OIDC_ISSUER="https://${PRIMARY}:${HTTPS_PORT}/auth/realms/${DEV_REALM}"
# …and the origin Keycloak stamps into every token, so `iss` is ONE string
# whichever door it came through (the proxy, or the direct port).
export OIDC_PUBLIC_ORIGIN="https://${PRIMARY}:${HTTPS_PORT}"
# What a HANDOFF link names (`stratigraph://open?server=…`): the same host the
# browser is on, not a hardcoded em.localhost.
export EM_PUBLIC_BASE="https://${PRIMARY}:${HTTPS_PORT}/em"

# ── 4 · su (con l'override s3Dgraphy-locale se richiesto) ─────────────────────
# ── 3-bis · IL REALM IMPARA IL NOME (prima di `up`, perché è un import) ──────
#
# Misurato sul Pi il 7 settembre 2026: `./fcn-up.sh fcn.local` fa emettere a
# Caddy un certificato per quel nome, la pagina ARRIVA da un'altra macchina —
# TLS a posto, CA fidata, Keycloak che risponde — e poi il login muore con
#
#     We are sorry…   Invalid parameter: redirect_uri
#
# perché le 28 redirect URI del client `em-console` sono cablate su
# `em.localhost:8443` e su `localhost`. L'argomento «host primario» era
# applicato a METÀ: Caddy lo imparava, il realm no. E `--help` lo offre dicendo
# «per l'altro computer», cioè promette esattamente la cosa che non funziona.
#
# `render_realm.py` RISPECCHIA le URI dell'autorità canonica per il nome nuovo:
# i percorsi restano quelli già dichiarati, cambia solo l'host, e un jolly nella
# parte host è rifiutato — sarebbe un open redirect.
#
# Il file reso è ignorato da git e il compose lo monta via `${REALM_FILE}`, il
# cui default è il file committato: senza argomenti, `up` monta esattamente
# quello di prima.
if [ "$PRIMARY" != "em.localhost" ]; then
  REALM_RESO="keycloak/realm-em-dev.${PRIMARY}.json"
  set +e
  python3 render_realm.py --host "$PRIMARY" --porta "$HTTPS_PORT" \
          --out "$REALM_RESO"
  rc=$?
  set -e
  case "$rc" in
    0) export REALM_FILE="./$REALM_RESO" ;;
    3) : ;;                       #: già coperto: si monta il committato
    *) echo "✖ non so insegnare \`$PRIMARY\` al realm di Keycloak." >&2
       echo "  Caddy servirebbe quel nome e il login morirebbe con «Invalid" >&2
       echo "  parameter: redirect_uri» DOPO che tutto il resto ha funzionato," >&2
       echo "  che è il modo peggiore di fallire. Mi fermo qui." >&2
       exit 1 ;;
  esac
fi

# ── 4-zero · IL FILE CHE CHI CLONA NON HA ────────────────────────────────────
#
# `.env.dev` è in `.gitignore` (contiene i valori riempiti) e `.env.dev.example`
# è committato. Ma questa riga lo USA con `--env-file` senza controllarlo, e su
# un clone fresco `./fcn-up.sh` moriva prima di qualunque altra cosa con
#
#     couldn't find env file: …/dev-stack/.env.dev
#
# misurato il 9 ottobre 2026. È della stessa famiglia del `command not found`
# del compose: un primo avvio che si ferma alla prima riga, su una macchina che
# non è quella di chi ha scritto lo script.
#
# NON lo copio io: `.env.dev` porta credenziali (senza valore, ma credenziali) e
# crearlo è un atto di una persona — la stessa ragione per cui `fcn-trust-ca.sh`
# stampa il comando `sudo` invece di eseguirlo. Qui si stampa la riga da
# incollare, che è una cosa sola.
if [ ! -f .env.dev ]; then
  echo "✖ Manca \`dev-stack/.env.dev\`, e \`--env-file\` lo vuole." >&2
  if [ -f .env.dev.example ]; then
    echo "  È in .gitignore di proposito: porta i valori riempiti. Il modello c'è," >&2
    echo "  e per il dev-stack va bene così com'è:" >&2
    echo >&2
    echo "      cp .env.dev.example .env.dev" >&2
    echo >&2
    echo "  (dentro ci sono minioadmin/minioadmin e un realm em-dev: valori che" >&2
    echo "   sarebbero una vulnerabilità su qualcosa di raggiungibile.)" >&2
  else
    echo "  E non trovo nemmeno \`.env.dev.example\`: questo checkout è incompleto." >&2
  fi
  exit 1
fi

# QUALE compose. Era `docker-compose` scritto a mano: è il binario autonomo, e
# su una macchina nuova (Docker Engine su Linux, Docker Desktop su Windows) non
# c'è — la PRIMA riga risponde `command not found`. `sg_compose_array` chiede
# quale dei due esiste e riempie l'array; se non c'è nessuno dei due dice cosa
# manca invece di proseguire al buio.
sg_compose_array || exit 1
COMPOSE+=(--env-file .env.dev -f docker-compose.dev.yml)
if [ "$LOCAL_S3D" = "yes" ]; then
  COMPOSE+=(-f docker-compose.local-s3d.yml)
  #: E QUESTO È UN BIND MOUNT, NON UN `context:` — quindi il controllo dei
  #: fratelli qui sotto NON lo vede, perché `config` non lo elenca fra i
  #: contesti. Misurato l'8 settembre 2026: con la sorgente assente il mount
  #: RIESCE e monta una cartella VUOTA, il container parte sano, e
  #: `PYTHONPATH=/s3dgraphy-src` ricade sul wheel installato. Cioè
  #: `--local-s3d` non fa NIENTE, in silenzio: si edita s3Dgraphy, si riavvia,
  #: e non cambia niente. È la forma «funziona quasi», che costa ore.
  if [ ! -d ../../s3Dgraphy/src ]; then
    echo "✖ --local-s3d, ma \`../../s3Dgraphy/src\` non c'è." >&2
    echo "  Non è un \`context:\`, è un bind mount: docker monterebbe una" >&2
    echo "  cartella VUOTA, i container partirebbero sani e PYTHONPATH" >&2
    echo "  ricadrebbe sul wheel — cioè --local-s3d non farebbe niente, e" >&2
    echo "  senza dirlo." >&2
    echo >&2
    echo "      ./fcn-install.sh --local-s3d      (lo clona)" >&2
    exit 1
  fi
  echo "▶ modo s3Dgraphy LOCALE: StratiGraph Server/StratiGraph Catalog useranno ../../s3Dgraphy/src (edita e riavvia per testare)."
fi
# ── 4-bis · I FRATELLI, PRIMA DEI PULL ───────────────────────────────────────
#
# `fcn-install.sh` è il posto dove questo si prepara, ma `fcn-up.sh` è quello
# che la gente lancia — è il comando che sta nel README e nell'aiuto. Su un
# clone del solo `stratigraph-server`, misurato sul Pi il 7 settembre 2026:
#
#     unable to prepare context: path "/home/paul/stratigraph-chatbot" not found
#
# e quel messaggio è comparso DOPO cinque immagini scaricate. Il controllo costa
# un `test -d` per repository e va fatto qui, non solo nell'installer: un
# controllo che esiste solo sulla strada documentata protegge la strada
# documentata.
#
# I percorsi li chiede al compose — un solo posto che li sa, e sono già
# assoluti.
mancanti=""
for ctx in $("${COMPOSE[@]}" config 2>/dev/null              | sed -n 's/^[[:space:]]*context:[[:space:]]*//p' | sort -u); do
  [ -d "$ctx" ] || mancanti="$mancanti $ctx"
done
if [ -n "$mancanti" ]; then
  echo "✖ il compose costruisce da repository affiancati che non ci sono:" >&2
  for m in $mancanti; do echo "     $m" >&2; done
  echo >&2
  echo "  Non comincio a scaricare immagini: sul Pi lo stesso guaio è comparso" >&2
  echo "  dopo cinque pull, cioè dopo tutto il lavoro che rendeva inutile." >&2
  echo >&2
  echo "      ./fcn-install.sh$( [ "$LOCAL_S3D" = "yes" ] && echo " --local-s3d" )" >&2
  echo >&2
  echo "  li clona (gli indirizzi stanno in \`x-sibling-repos\`, nel compose)." >&2
  exit 1
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
    echo "  ${COMPOSE[0]}${COMPOSE[1]:+ ${COMPOSE[1]}} -f docker-compose.dev.yml logs --tail=40 stratigraph-server"
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
$( # ── PER L'ALTRO COMPUTER: PROVATO, NON PROMESSO ─────────────────────────
   #
   # Misurato il 7 settembre 2026: avahi è attivo sul Pi e PUBBLICA, e da un Mac
   # sulla stessa LAN `ping fcn.local` risponde `cannot resolve`. La causa non è
   # il Pi: è l'access point che non propaga il multicast fra i client, che sugli
   # AP di consumo è la norma. Quindi quella riga era un indirizzo che non
   # funziona, stampato come se funzionasse — la stessa forma del `curl` senza
   # `-f`.
   #
   # Adesso si prova, e si dice COSA si è trovato. E si dice anche il limite
   # della sonda, che è il punto: da qui si può misurare solo che QUESTA macchina
   # risolve il nome. Che lo risolva l'altra è la cosa che serve e che da qui non
   # si vede.
   if [ -n "$BONJOUR" ] && [ "$BONJOUR" != "$PRIMARY" ]; then
     esito="$(sg_reaches "https://${BONJOUR}:${HTTPS_PORT}/em/v1/health" 4)"
     if [ "$esito" = "si" ]; then
       echo "  Per l'ALTRO computer: https://${BONJOUR}:${HTTPS_PORT}/em/v1/health"
       echo "      ✔ questo nome risponde DA QUI. Che risponda dall'altra macchina"
       echo "        dipende dal multicast fra i client del vostro access point,"
       echo "        e molti AP di consumo non lo propagano: da qui non lo vedo."
     else
       echo "  Per l'ALTRO computer: ⚠ \`${BONJOUR}\` NON risponde nemmeno da qui"
       echo "      ($([ "$esito" = "nome" ] && echo "il nome non si risolve" || echo "si risolve ma non risponde")) — non lo stampo come indirizzo buono."
       ip="$(sg_lan_ip)"
       if [ -n "$ip" ]; then
         echo "      Il ripiego è insegnare il nome all'ALTRA macchina, UNA riga:"
         echo "          $ip  ${BONJOUR}      → nel suo /etc/hosts"
         echo "        (su Windows: C:\\Windows\\System32\\drivers\\etc\\hosts)"
         echo "      E poi https://${BONJOUR}:${HTTPS_PORT}/em/v1/health da lì."
         echo "      NON l'IP nudo: la CA interna di Caddy non firma per un IP,"
         echo "      quindi il nome serve comunque — si insegna, non si aggira."
       fi
     fi
   fi )

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
$( [ "$LOCAL_S3D" = "yes" ] && echo "  · dopo aver editato s3Dgraphy:  ${COMPOSE[0]}${COMPOSE[1]:+ ${COMPOSE[1]}} -f docker-compose.dev.yml -f docker-compose.local-s3d.yml restart stratigraph-server stratigraph-catalog" )
Giù:  ./fcn-down.sh   (o --stop / --wipe / --colima)
EOF
