# em-server — the s3Dgraphy access API over HTTP.
#
# Stateless by construction: no volume, no writable path the app depends on, no
# state in the container. Scale it by adding replicas.
#
#   docker build --build-arg S3DGRAPHY_VERSION=<version> -t em-server .
#   docker run --rm -p 8000:8000 em-server
#
# The build argument is REQUIRED — see the note on it below.
#
# To run against a s3Dgraphy CHECKOUT instead of the published wheel while the
# language and the service move together, do NOT try to build without one: this
# line used to say `--build-arg S3DGRAPHY_SPEC=""`, and pip refuses an empty
# requirement ("Expected package name at the start of dependency specifier") —
# measured, so the escape hatch never worked. The mechanism that does is the
# dev-stack overlay, which mounts the checkout and puts it first on PYTHONPATH:
#   ./dev-stack/fcn-up.sh --local-s3d
#
FROM python:3.12-slim AS base

# PYTHONDONTWRITEBYTECODE: nothing in the image should be modified at runtime.
# PYTHONUNBUFFERED: logs reach the orchestrator as they happen, not on flush.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# EXACT pin with the extras — see pyproject.toml for the three reasons. dev12 is
# the first release carrying `s3dgraphy/api.py`, and the extras are what make
# /v1/reproject and /v1/export-ttl work rather than 501.
#
# Build with --build-arg S3DGRAPHY_EXTRAS='' for a slimmer, less capable image:
# it starts and serves, and /v1/health reports which ops it cannot do.
# The s3Dgraphy this image installs: the VERSION from one place, the EXTRAS
# from this service.
#
# `S3DGRAPHY_VERSION` has NO DEFAULT, and that is the whole point rather than an
# omission. A default here would be a second spelling of a number that must agree
# with `dev-stack/.env.dev`, and two spellings of one version are two versions the
# day somebody edits one — which is exactly what happened: this image sat
# on dev12 while the catalogue and the field assistant had drifted to dev16, in a
# stack that shares em.json files and one semantic vocabulary. A build without the
# argument REFUSES, the way `auth.py` refuses a half-configured realm, instead of
# falling back to a pin nobody chose.
#
#   docker build --build-arg S3DGRAPHY_VERSION=<version> -t em-server .
#
# The EXTRAS stay here because they are legitimately this service's own: `[geo]`
# and `[rdf]` are what make /v1/reproject and /v1/export-ttl work rather than
# answer 501. A service may choose what it needs; it may not move the version by
# itself.
ARG S3DGRAPHY_VERSION
ARG S3DGRAPHY_EXTRAS="geo,rdf"

WORKDIR /srv/em-server

# Dependencies first, in their own layer: application edits then rebuild in
# seconds instead of re-resolving the world.
COPY pyproject.toml README.md ./
# The explicit rdflib/pyproj lines are GONE, and the extras above are why: before
# dev12 there was no `[geo]`, so `s3dgraphy[rdf,geo]` silently skipped pyproj (pip
# WARNS about an unknown extra, it does not fail) and the image answered
# `reproject: false`. From dev12 on both extras are declared, so naming them is
# enough — verified in the container, not assumed.
# PyJWT[crypto] is here and NOT behind a build arg on purpose: an image that
# cannot verify a token is an image that would come up in the open dev mode on
# the shared infrastructure. The auth dependency is not optional (P1).
# `minio` is here and not behind a build arg for the same reason PyJWT is: an
# image that cannot reach the object store would come up serving assets from a
# container filesystem that disappears with the container. 400 KB.
RUN set -eu; \
    : "${S3DGRAPHY_VERSION:?required — dev-stack/.env.dev holds it}"; \
    spec="s3dgraphy${S3DGRAPHY_EXTRAS:+[${S3DGRAPHY_EXTRAS}]}==${S3DGRAPHY_VERSION}"; \
    pip install --upgrade pip && \
    pip install "$spec" "fastapi>=0.110" "uvicorn[standard]>=0.27" \
                "PyJWT[crypto]>=2.8" "minio>=7.2"

COPY app ./app

# Not root. The application writes nothing inside the image, so there is no
# reason to be able to.
#
# /srv/em-data is created here even though it is empty: a named volume mounted
# on a path the image does NOT have is created root-owned, and a non-root
# process then cannot write its first snapshot. Creating it with the right owner
# is what makes `volumes: [em_data:/srv/em-data]` work — in the dev stack and in
# the Ansible compose, which mounts exactly the same path.
RUN useradd --create-home --shell /usr/sbin/nologin emserver && \
    mkdir -p /srv/em-data && \
    chown -R emserver:emserver /srv/em-server /srv/em-data
USER emserver

EXPOSE 8000

# The orchestrator's own probe target — the same endpoint a human curls.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# One worker per container: replicas are the orchestrator's business, and a
# process count baked into an image is a decision taken in the wrong place.
#
# ── IL KEEPALIVE DEL TRASPORTO, SCRITTO INVECE CHE EREDITATO ─────────────────
#
# Questi due numeri erano già in vigore e non li aveva scelti nessuno: sono i
# default di uvicorn. Misurati il 30 settembre 2026 contro questa immagine
# (uvicorn 0.52.1, websockets 17.0.1), con un client vero e non il TestClient:
#
#   PING a t=20,0 · 40,0 · 60,0 s — periodo 20,0 s esatti
#   client che non risponde        → CLOSE a t=40,0 s, «keepalive ping timeout»
#   client in GALLERIA (pacchetti scartati, socket aperti da tutti e due i capi)
#                                  → il server chiude il suo capo a t=40,0 s,
#                                     e `/who` smette di dirlo seduto a t=40,2 s
#
# Quaranta secondi è un numero ragionevole e NON è la ragione per scriverlo. La
# ragione è che finché stava nei default di una dipendenza, un aggiornamento di
# uvicorn o un `--ws-ping-interval 0` in un playbook lo cambiava **in silenzio**
# — e la soglia del silenzio dell'applicazione (30 s, `app/presence.py`) è
# scelta per stare DENTRO questo numero. Se questo si allunga senza che nessuno
# se ne accorga, «uscito» arriva dopo «silenzioso» invece che dopo, e i tre
# stati tornano a essere due.
#
# `tests/test_chi_ce_e_chi_non_ce_piu.py` legge questa riga e confronta i numeri
# con quelli di `presence.py`: il vincolo è verificato, non commentato.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--ws-ping-interval", "20", "--ws-ping-timeout", "20"]
