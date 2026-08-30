# The dev realm

`realm-em-dev.json` is imported by the dev-stack Keycloak at start-up. Everything
in it is a **development** credential and none of it is ever a production one:
the real realm is the shared Keycloak the Ansible role points at.

**The file itself carries no comments on purpose.** Keycloak's importer
deserializes strictly and refuses an unknown key — a `_comment` field at the top
stops the container from starting, with a Jackson stack trace that says nothing
about realms. So the explanation lives here.

What it seeds, and why each piece is needed to get a token with `curl`:

| piece | why |
|---|---|
| realm `em-dev` | the isolated realm this stack validates against |
| client `em-server` | confidential (`em-dev-secret`), **service accounts ON** so `client_credentials` works, **direct access grants ON** so a password grant works too — which is what a human uses |
| mapper `audience` | **the one that is always missing.** Without it the token's `aud` is `account`, and StratiGraph Server answers `403 … issued for another client`. It is the single most common reason a correct-looking token is refused |
| mapper `orcid` | puts the user's ORCID iD in the token, so the room stamps an identity (`_identity()` in `app/ws.py` reads `orcid` first) instead of leaving edits unsigned |
| user `dev` / `dev` | the human; carries the ORCID attribute. Bootstraps as the **owner** of any room they are the first to join |
| user `viewer` / `viewer` | a second, ordinary authenticated identity with **no** membership anywhere. Added 2026-08-17 for the embargo end-to-end (`dev-stack/smoke_embargo_viewer.py`): the gate refuses anybody below editor, and with one user in the realm there was nobody to be refused — the 403 could only be measured against a stand-in. Its ORCID is a different one, so a room can tell the two apart |

No custom scope is required, so `OIDC_REQUIRED_SCOPE` stays unset.

Changing any of this means changing **this file**, not only `.env.dev`: the env
file selects which realm/client to ask for, the JSON is what actually exists.

**And a change here needs a RE-IMPORT.** Keycloak runs `start-dev
--import-realm`, which imports only when the realm is not already in the
container's own database — so editing this file changes nothing until the
container is recreated:

    cd dev-stack && docker-compose -f docker-compose.dev.yml --env-file .env.dev \
        up -d --force-recreate keycloak

The re-import mints **new realm keys**: every token issued before it stops
verifying. That is expected, and it is why a room that suddenly answers 4401
right after a realm change is not a bug — ask for a token again.

## Re-importing the realm after you change it — no `--wipe` needed

MEASURED 2026-08-29, because "import happens once" had become folklore here and
the folklore was costing a `--wipe` (which erases the studies, the rooms and the
bucket) every time somebody added a redirect URI.

This Keycloak has **no data volume**: the service mounts only
`realm-em-dev.json`, read-only, and `start-dev` keeps its database inside the
container. So the realm is re-imported whenever the CONTAINER is recreated:

```bash
docker compose --env-file .env.dev -f docker-compose.dev.yml \
  up -d --force-recreate --no-build keycloak
```

Healthy in ~30s, and the new realm is live. `--wipe` is for when you want the
DATA gone, which is a different intention and should stay a different command.

The trade-off, stated: nothing you do in the admin console survives a recreate
either. Edit the JSON, not the running realm — the JSON is the one that is in
git.

## `/auth`, and why Keycloak has ONE public URL

Keycloak serves under `KC_HTTP_RELATIVE_PATH=/auth` so Caddy's `handle /auth/*`
lands (with `handle`, the prefix is KEPT, so Keycloak must expect it — with
`handle_path` it is stripped and Keycloak must not). And `KC_HOSTNAME_URL` fixes
the FRONTEND url, which is what decides the `iss` a token carries.

That last part is the fix to a real bug, not tidiness. `app/auth.py` verifies
`iss` strictly against `OIDC_ISSUER`; with two spellings of the realm (the proxy
on 8443 and the direct port on 8085) tokens from one door are refused by a
service configured for the other. One frontend URL means one `iss`, whichever
door it came through — measured: a token from `token.sh` (direct port) carries
`iss = https://em.localhost:8443/auth/realms/em-dev` and `GET /v1/whoami` answers
200.
