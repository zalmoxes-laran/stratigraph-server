#!/usr/bin/env bash
# fcn-install — prepara una macchina fresca, poi cede il passo a fcn-up.sh.
#
#   ./fcn-install.sh                # controlla e prepara, poi dice cosa lanciare
#   ./fcn-install.sh fcn.local      # …e insegna QUEL nome anche al realm
#   ./fcn-install.sh --local-s3d    # …e serve anche il checkout di s3Dgraphy
#   ./fcn-install.sh --dry-run      # dice cosa farebbe e non fa niente
#
# COSA GARANTISCE, e sono le cose che hanno fatto fallire il primo avvio sul
# Raspberry Pi il 7 settembre 2026:
#
#   1  i tre repository affiancati        — se ne manca uno, lo clona
#   2  .env.dev esiste                    — non lo crea: dice la riga
#   3  Docker risponde e un compose c'è   — non li installa: dice cosa manca
#   4  em.localhost si risolve            — non tocca /etc/hosts: dice la riga
#   5  il nome primario lo impara il realm, non solo Caddy
#
# LA PROPRIETÀ CHE RIASSUME TUTTO: **fallisce prima dei pull, non dopo.**
# Sul Pi il messaggio
#
#     unable to prepare context: path "/home/paul/stratigraph-chatbot" not found
#
# è comparso DOPO cinque immagini scaricate, minuti di rete e di disco. Un
# controllo che costa un millisecondo arrivava dopo tutto il lavoro che rendeva
# inutile. Qui i controlli stanno prima, in ordine di costo.
#
# COSA NON FA, MAI:
#   · `sudo` — la riga di /etc/hosts si stampa, non si scrive. È la dottrina di
#     `fcn-trust-ca.sh`, che stampa il comando di sistema invece di eseguirlo.
#   · installare Docker o aggiungere un repository apt: aggiungere una sorgente
#     di pacchetti alla macchina di qualcuno è un atto di quella persona.
#   · toccare i container: questo script non ne accende e non ne spegne nessuno.
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next} /^[[:space:]]*$/{next} {exit}' "$0"; exit 0
fi
set -euo pipefail
cd "$(dirname "$0")"                     # stratigraph-server/dev-stack
. ./platform.sh

HTTPS_PORT="${HTTPS_PORT:-8443}"
LOCAL_S3D="no"; DRY="no"; ARG_HOST=""
for a in "$@"; do
  case "$a" in
    --local-s3d) LOCAL_S3D="yes" ;;
    --dry-run|-n) DRY="yes" ;;
    -*) echo "flag sconosciuto: $a (usa --local-s3d | --dry-run)"; exit 2 ;;
    *) ARG_HOST="$a" ;;
  esac
done
PRIMARY="${ARG_HOST:-em.localhost}"

#: Il conto di cosa è cambiato: serve per la frase finale, perché
#: «idempotente» vuol dire anche DIRE che non si è fatto niente.
CAMBIATO=0
GUAI=0
say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✔ %s\n' "$*"; }
fatto(){ printf '  ✚ %s\n' "$*"; CAMBIATO=$((CAMBIATO+1)); }
male() { printf '  ✖ %s\n' "$*" >&2; GUAI=$((GUAI+1)); }
nota() { printf "      %s\n" "$*" >&2; }

say ""
say "▶ fcn-install · $(sg_os) · $(uname -m)"
[ "$DRY" = "yes" ] && say "  (--dry-run: non cambio niente)"
say ""

# ══ 1 · I REPOSITORY FRATELLI ════════════════════════════════════════════════
#
# PRIMO, perché è il controllo che sul Pi è arrivato ultimo. E costa un
# `test -d`.
#
# GLI INDIRIZZI STANNO IN UN POSTO SOLO. Quali cartelle servono lo dice il
# compose, con le sue `context:`; da dove si clonano lo dice `x-sibling-repos`
# nello STESSO file. Non c'è nessuna lista qui dentro da tenere allineata, e se
# un `context:` nuovo non ha il suo URL questo script lo dice invece di
# clonare il nulla.
say "1 · i repository affiancati"

#: I `context:` risolti in percorsi assoluti — li stampa compose, che è la cosa
#: che poi userà quei percorsi. Misurato: funziona su un clone in cui i fratelli
#: NON ci sono ancora e `.env.dev` non esiste, che è esattamente il momento in
#: cui serve.
sg_compose_array || exit 1
#: UNA lettura del compose per tutto: i `context:` e la mappa nome→URL vengono
#: dalla stessa stampa. Erano due chiamate a `config`, che è la stessa domanda
#: fatta due volte.
reso="$("${COMPOSE[@]}" -f docker-compose.dev.yml config 2>/dev/null || true)"
contesti="$(printf '%s\n' "$reso" \
            | sed -n 's/^[[:space:]]*context:[[:space:]]*//p' | sort -u)"
if [ -z "$contesti" ]; then
  male "non riesco a leggere i \`context:\` dal compose."
  nota "\`${COMPOSE[*]} -f docker-compose.dev.yml config\` non ha stampato niente:"
  nota "senza quello non so quali repository servono, e non tiro a indovinare."
  exit 1
fi

#: e la mappa nome→URL, dallo stesso file
urls="$(printf '%s\n' "$reso" \
        | awk '/^x-sibling-repos:/{f=1;next} f&&/^[^[:space:]]/{f=0} f' \
        | sed -n 's/^[[:space:]]*\([^:]*\):[[:space:]]*\(.*\)$/\1 \2/p')"

#: con --local-s3d serve anche s3Dgraphy, che non ha un `context:` (è un volume
#: in docker-compose.local-s3d.yml). Un requisito in più, dichiarato dal flag.
if [ "$LOCAL_S3D" = "yes" ]; then
  contesti="$contesti
$(cd .. && cd .. && pwd)/s3Dgraphy"
fi

for ctx in $contesti; do
  nome="$(basename "$ctx")"
  if [ -d "$ctx" ]; then
    ok "$nome"
    continue
  fi
  url="$(echo "$urls" | awk -v n="$nome" '$1==n{print $2; exit}')"
  if [ -z "$url" ]; then
    male "manca \`$nome\` ($ctx) e non so da dove clonarlo."
    nota "Il compose lo costruisce e \`x-sibling-repos\` non lo elenca:"
    nota "aggiungi la riga \`$nome: <url>\` là, accanto agli altri."
    continue
  fi
  if [ "$DRY" = "yes" ]; then
    fatto "clonerei $nome da $url"
    continue
  fi
  if ! command -v git >/dev/null 2>&1; then
    male "manca \`$nome\` e non c'è \`git\` per clonarlo."
    nota "Installa git, o clona a mano:  git clone $url \"$ctx\""
    continue
  fi
  #: `${nome}` CON LE GRAFFE, e non `$nome`, perché il carattere dopo è `…`.
  #: Trovato eseguendo, e il verso è il contrario di quello che si penserebbe:
  #:
  #:     LC_CTYPE=C            →  clono x…            (funziona)
  #:     LC_CTYPE=en_US.UTF-8  →  nome<?>: unbound variable
  #:     LC_CTYPE=C.UTF-8      →  nome<?>: unbound variable
  #:
  #: In una locale UTF-8 — cioè quella GIUSTA — bash 3.2 accetta i byte di `…`
  #: dentro il nome della variabile, e sotto `set -u` muore. Su una macchina
  #: senza locale funziona; su una configurata a modo, no. bash 5.2 (Debian 12,
  #: il Pi) non ha il difetto: quindi era un guasto che si sarebbe visto sul
  #: Mac di chi sviluppa e non sul nodo, cioè nel modo più lento da capire.
  #: Le graffe sono corrette in ogni locale e in ogni versione — misurato.
  say "  ⇣ clono ${nome}…"
  if git clone --depth 1 "$url" "$ctx"; then
    fatto "$nome clonato"
  else
    male "il clone di $nome è fallito."
    nota "A mano:  git clone $url \"$ctx\""
  fi
done

# ══ 2 · .env.dev ═════════════════════════════════════════════════════════════
#
# Il controllo in `fcn-up.sh` c'è dal 9 ottobre e sul Pi NON è mai scattato,
# perché l'avevamo prevenuto a mano. Qui si verifica che la sua frase sia quella
# giusta, e si arriva alla stessa conclusione un passo prima.
say ""
say "2 · l'ambiente"
if [ -f .env.dev ]; then
  ok ".env.dev"
elif [ ! -f .env.dev.example ]; then
  male "non c'è né \`.env.dev\` né \`.env.dev.example\`: checkout incompleto."
else
  male "manca \`.env.dev\` (è in .gitignore: porta i valori riempiti)."
  nota "Per il dev-stack il modello va bene com'è. UNA riga:"
  nota ""
  nota "    cp .env.dev.example .env.dev"
  nota ""
  nota "Non lo copio io: dentro ci sono credenziali (senza valore, ma"
  nota "credenziali) e crearlo è un atto di una persona."
fi

# ══ 3 · DOCKER E UN COMPOSE ══════════════════════════════════════════════════
#
# `platform.sh` risponde a entrambe, e questo script NON le riscrive.
say ""
say "3 · docker"
if sg_docker_ready; then
  ok "docker risponde"
else
  #: `sg_ensure_docker` stampa il rimedio giusto per QUESTO sistema. Qui non lo
  #: si chiama per avviare niente su una macchina di qualcun altro: si chiama
  #: perché la sua frase è la frase giusta, e si registra il guaio.
  sg_ensure_docker || true
  male "docker non risponde (vedi sopra)."
fi
#: il compose l'ha già trovato il passo 1 — dirlo qui è dove chi legge lo cerca
ok "compose: ${COMPOSE[*]}"

# ══ 4 · em.localhost ═════════════════════════════════════════════════════════
#
# La predizione del 9 ottobre, confermata sul Pi: su Debian 12 la riga
# `hosts: files dns` non sintetizza `*.localhost`, e la stack sale con ogni
# container sano e ogni pagina morta.
say ""
say "4 · il nome su cui gira tutto"
risolve="no"
case "$(sg_os)" in
  macos) ping -c1 -t1 em.localhost >/dev/null 2>&1 && risolve="si" ;;
  *)     getent hosts em.localhost >/dev/null 2>&1 && risolve="si" ;;
esac
if [ "$risolve" = "si" ]; then
  ok "em.localhost si risolve"
else
  male "\`em.localhost\` NON si risolve su questa macchina."
  nota "Non è un guasto della stack: è la pila NSS. Su Debian/Ubuntu la riga"
  nota "\`hosts: files dns\` di fabbrica non sintetizza \`*.localhost\`."
  nota "Senza questo, la stack sale con tutti i container sani e OGNI pagina"
  nota "morta — il modo più confondente di fallire."
  nota ""
  nota "    echo \"127.0.0.1 em.localhost\" | sudo tee -a /etc/hosts"
  nota ""
  nota "Non la scrivo io: \`/etc/hosts\` è di sistema e questo script non fa sudo."
fi

# ══ 5 · IL NOME PRIMARIO, E IL REALM ═════════════════════════════════════════
say ""
say "5 · l'host primario"
if [ "$PRIMARY" = "em.localhost" ]; then
  ok "nessun host primario: si userà em.localhost (nessun realm da rendere)"
else
  #: Si RENDE qui, così un nome che il realm non sa imparare lo si scopre adesso
  #: e non dopo `up` — che è la forma del guasto misurato sul Pi: TLS a posto,
  #: pagina che arriva, e il login che muore per ultimo.
  if [ "$DRY" = "yes" ]; then
    fatto "renderei il realm per $PRIMARY"
  else
    set +e
    python3 render_realm.py --host "$PRIMARY" --porta "$HTTPS_PORT" --zitto \
            --out "keycloak/realm-em-dev.${PRIMARY}.json"
    rc=$?
    set -e
    case "$rc" in
      0) fatto "realm reso per $PRIMARY (redirect URI rispecchiate)" ;;
      3) ok "il realm già copre $PRIMARY" ;;
      *) male "il realm non può imparare \`$PRIMARY\` (vedi sopra)."
         nota "Caddy servirebbe quel nome e il login morirebbe con «Invalid"
         nota "parameter: redirect_uri» DOPO che tutto il resto ha funzionato." ;;
    esac
  fi
fi

# ══ E ADESSO ═════════════════════════════════════════════════════════════════
say ""
if [ "$GUAI" -gt 0 ]; then
  say "✖ $GUAI cosa/e da sistemare prima di accendere. Nessun pull è stato fatto."
  say "  (È il punto: sul Pi lo stesso guaio è comparso dopo cinque immagini"
  say "   scaricate. Qui costa niente.)"
  exit 1
fi
if [ "$CAMBIATO" -eq 0 ]; then
  say "✔ tutto già a posto: non ho cambiato niente."
else
  say "✔ pronto ($CAMBIATO cosa/e preparate)."
fi
say ""
say "  Adesso:   ./fcn-up.sh$( [ "$PRIMARY" != "em.localhost" ] && echo " $PRIMARY" )$( [ "$LOCAL_S3D" = "yes" ] && echo " --local-s3d" )"
say "  E la CA, una volta sola:   ./fcn-trust-ca.sh"
say ""
