# Deploying the whole system

*One playbook, one host, everything behind TLS. This page says what you need
before you start, what the single command does, and what to check afterwards.*

The map of the pieces is [`ARCHITECTURE-SYSTEM.md`](ARCHITECTURE-SYSTEM.md);
this is the operational half. The playbook itself lives in the
[`heriverse-ansible`](../../heriverse-ansible) repository.

---

## What comes up

Nine containers behind one Caddy, which terminates TLS and routes by path:

| public route | container | what it serves |
|---|---|---|
| `/` | heriverse | the 3D front end |
| `/server/*` | heriverse-server | Heriverse's own API |
| `/auth/*` | keycloak | identity — one realm for everything |
| `/couchdb/*` | couchdb | documents (Heriverse's scenes, and the Catalog's index) |
| `/em/*` | **StratiGraph Server** | rooms: the live graph, its assets, the WebSocket |
| `/catalog/*` | **StratiGraph Catalog** | the studies as published: search, HDT view, "open in…" |
| `/iiif/*` | cantaloupe | the IIIF Image API, straight out of the bucket |
| `/assets/*` | minio | the object store's own API |

Caddy obtains and renews the certificate itself. That single origin is not
cosmetic: it is what removes mixed-content errors, and it is what makes a token
in a query string defensible for a viewer that cannot set a header.

---

## Prerequisites — the part that is ops, and is yours

1. **A host.** Linux, Docker installed (the role does not install it), enough
   disk for the object store. This is where the studies will live.
2. **A domain pointing at it.** Caddy needs a name it can prove, over ports 80
   and 443. Without a real name there is no public certificate, and everything
   below still works but only over an internal CA nobody trusts.
3. **The images.** `StratiGraph Server` and `StratiGraph Catalog` are built from the `Dockerfile`
   in each repo and pushed to the registry the defaults name
   (`git.3dresearch.it:5050/stratigraph/…`). Change `em_server_image` /
   `em_catalog_image` if they live somewhere else.
4. **The secrets, from outside the repository.** MinIO's root credentials and
   CouchDB's. There are **no defaults** for them, deliberately: a default
   password is the same password on every deployment. Put them in the inventory,
   in an Ansible Vault file, or in the environment.

> Verified: rendering the compose template **without** the CouchDB credentials
> fails at that task with `'couchdb_user' is undefined`. That is the intended
> behaviour — the alternative is a catalogue that comes up with a password
> somebody could read in a git history.

---

## Two shapes, and the certificate tells them apart

Everything above describes one host with a public name. There are two deployments
in front of us and only one of them looks like that. The difference is not the
services — those are the same — it is how the origin gets a certificate, and that
decides what has to be true about the network.

### The institutional node — a public name

The templates already assume this shape: `Caddyfile.j2` opens with
`{{ server_name }} {` and carries **no `tls` directive**, so Caddy obtains and
renews the certificate by itself over ports 80 and 443. Nothing is handed to it.

"We already have certificates" is usually the wrong shape of answer, because a
host is one of three things and only the first works as the templates stand:

1. **Caddy is the front door.** Public A record, 80 and 443 reachable from the
   internet. ACME does the rest, including renewal. This is today's default.
2. **The institution terminates TLS** on its own reverse proxy and forwards to
   us. Then Caddy must **not** attempt ACME — it would fail and keep retrying —
   the site block has to be plain `http://`, and the proxy has to forward the
   headers the services read. A small change to the template, but not an
   automatic one.
3. **Certificates handed over as files.** `tls /path/fullchain.pem /path/key.pem`
   — and somebody owns the renewal, which is the half that is forgotten and then
   expires on a Sunday.

Two institutional nodes are **two deployments**, not one with two names: the
realm, the bucket, the studies and the audience are per-instance, and a handoff
link names the host it was asked from — so a study opened from one node points at
that node. That is correct behaviour, and not something to paper over with a
shared name.

### The Field Computing Node — a local network, no public reachability

The prerequisite above says that without a real name everything still works "but
only over an internal CA nobody trusts". On an FCN that sentence understates the
cost, because of what a browser withholds from a page it does not consider a
secure context: **camera, microphone and geolocation**. The field assistant is
exactly those three. So on a node in a trench, TLS that browsers trust is not
polish — it is the feature.

Three ways, in the order worth trying:

1. **A real subdomain resolved to a private address, with a DNS-01 certificate.**
   `fcn.<domain>` → `192.168.x.x`, and Caddy proves the name by writing a TXT
   record instead of by being reachable. The result is a publicly trusted
   certificate on a network with no inbound path, and **nothing to install on any
   phone**. The cost is real and worth stating: the stock `caddy:2-alpine` image
   has no DNS providers compiled in, so this needs a Caddy built with the
   provider plugin and an API credential for the zone. This is the one to aim
   for.

   **Two things that are easy to get wrong here, because they concern different
   moments.** *Issuing* the certificate needs the node to reach the internet
   outbound — to Let's Encrypt and to the DNS provider's API — for a few seconds;
   it needs no inbound path, no public address and no open port. *Using* it needs
   nothing at all: a certificate is a signed file with a date on it, and a browser
   checks the signature and the clock, never the internet. So the node is issued
   or renewed wherever it has connectivity — at base, on a phone hotspot, the
   evening before leaving — and then works for the rest of its ~90 days on a
   network with no uplink whatsoever. Plan the renewal before a campaign, not
   during one.

   The second is **name resolution in the field**, and it bites at use time
   rather than at issue time. The `A` record is public and points at a private
   address, which is legitimate and common; but a phone on a trench network with
   no uplink cannot ask a public resolver anything. So the field router (or the
   node itself) has to answer for that name locally — a DNS override on the
   access point mapping `fcn.<domain>` to the node's LAN address. Without it the
   certificate is perfect and nobody can reach the name it certifies. Note also
   that some resolvers refuse to return private addresses for public names
   ("DNS rebinding protection"); the local override sidesteps that too.
2. **The internal CA, trusted per device** (`tls internal` — what the dev stack
   does). Free, and it works, but every phone that will record anything has to
   install *and* trust that CA. On iOS those are two separate steps in two
   different settings screens, once per device — to be done before leaving for
   the site, not in a trench with no signal.
3. **Plain HTTP.** Not an option here. It is the configuration in which the
   assistant quietly loses the three permissions it exists for, and says nothing
   about it.

There is no fourth way. A certificate that browsers trust is one signed by a CA
they already carry, and no public CA signs anything without some contact with the
internet at some point. Either the node touches the network occasionally (1), or
the trust is installed by hand on each device (2).

The field assistant is **not in the playbook yet**: `heriverse-ansible` carries
the other nine services and not this one. When it lands its route is `/chat/*`,
under the same rule as everything else on this page — one origin, one
certificate.

---

## The command

```bash
ansible-playbook -i <inventory> playbook/heriverse.yml
```

It creates the service user, clones the Heriverse compose repository, writes the
**rendered** `docker-compose.yml` and `Caddyfile` over the ones in it, brings the
stack up and runs the entrypoint.

Two notes for whoever runs it:

* the repository has `role/` (singular), so the playbook cannot resolve the role
  `heriverse` without help. Point `ANSIBLE_ROLES_PATH` at a directory containing
  it under that name, or add an `ansible.cfg`. *Reported, not changed: it is the
  repository's own arrangement, not a typo of ours.*
* the role uses `community.docker.docker_compose_v2`, so that collection must be
  installed (`ansible-galaxy collection install community.docker`).

### Variables worth knowing

| variable | default | what it decides |
|---|---|---|
| `server_name` | `heriverse.example.com` | **the** domain. Everything public is derived from it |
| `em_server_enabled` / `em_catalog_enabled` | `true` | whether the StratiGraph services come up at all |
| `minio_enabled` | `true` | object store on. Off ⇒ StratiGraph Server falls back to a directory (single instance only) |
| `iiif_enabled` | `true` | Cantaloupe on. Needs MinIO |
| `em_catalog_couchdb_enabled` | `true` | the Catalog's index in CouchDB. Off ⇒ SQLite on a volume |
| `em_server_audience` / `em_catalog_audience` | `StratiGraph Server` | the audience each service requires in a token |
| `minio_root_user` / `_password`, `couchdb_user` / `_password` | **none** | from the inventory or the Vault |
| `EM_CORPUS_CURATORS` (env on StratiGraph Server) | **empty** | the ORCIDs allowed to read the **whole** resident DTC corpus. The register's per-file **slice** (`GET /v1/corpus?sha256=…`) is open to any authenticated caller — that is what clients use — but the lot is the provenance of every study on the instance, so it is a curation read and it is **off until somebody is named**. A refusal is a 403 that says so |
| `EM_CORPUS_OPEN` (env on StratiGraph Server) | unset | `1` opens the whole read to any authenticated caller. For a single-user instance; kept as one visible setting rather than a special case in the code |
| `EM_CORPUS_DIR` (env on StratiGraph Server) | falls back to `EM_SNAPSHOT_DIR` | where the resident corpus lives **when there is no MinIO**. With MinIO configured the register rides in the same bucket as the assets it describes (`em/corpus.em.json`) and needs no setting |

The URL topology is **already correct** in the templates: StratiGraph Server writes
`https://<server_name>/iiif/3` into its manifests and dials
`http://cantaloupe:8182/iiif/3` to measure images; StratiGraph Catalog writes
`https://<server_name>` into its "open in…" answers and reaches MinIO and CouchDB
by service name. Nothing about it has to be assembled by hand — see
[`URL-TOPOLOGY.md`](URL-TOPOLOGY.md) for why the two forms are two variables.

---

## Checking it without deploying

Everything below was **executed** while writing this page, against the templates
as they stand. None of it needs a host.

Render the compose template with example values and let Docker judge it:

```bash
ansible-playbook render.yml -e tpl_src=role/templates/docker-compose.yml.j2 -e tpl_dest=/tmp/dc.yml
docker-compose -f /tmp/dc.yml config
```

Measured: **VALID**, nine services —
`caddy cantaloupe couchdb StratiGraph Catalog StratiGraph Server heriverse heriverse-server
keycloak minio` — and the volumes `couchdb_config couchdb_data em_catalog_data
em_data keycloak_data minio_data`.

With the Catalog on its SQLite index and no object store
(`em_catalog_couchdb_enabled: false`, `minio_enabled: false`,
`iiif_enabled: false`): **VALID**, seven services, and the StratiGraph Catalog block
carries `EM_CATALOG_DB` instead of `COUCHDB_URL`. The config gating works in
both directions, which is the point of testing the *off* case at all.

Let Caddy judge its own file:

```bash
docker run --rm -v "$PWD:/work:ro" caddy:2-alpine caddy validate --config /work/Caddyfile --adapter caddyfile
```

Measured: **Valid configuration**, with all four StratiGraph routes present —
`/em/*`, `/catalog/*`, `/iiif/*`, `/assets/*`.

And the playbook itself:

```bash
ansible-galaxy collection install community.docker
ANSIBLE_ROLES_PATH=<dir containing role as "heriverse"> ansible-playbook --syntax-check playbook/heriverse.yml
```

Measured: **green**.

---

## After a deploy

```bash
curl -s https://<server_name>/em/v1/health | jq
curl -s https://<server_name>/catalog/health | jq
```

What to look for, in both answers:

* `"auth": "keycloak"` — **not** `dev-no-auth`. If it says the latter, the
  service did not get its OIDC environment and is answering to anybody;
* StratiGraph Server: `"asset_store": "minio (…)"` — if it says `memory`, uploads are
  living in a process and will vanish with it;
* StratiGraph Catalog: `"container_store": "minio (…)"` and `"index": "couchdb (…)"`.

Then the one that proves the architecture rather than the wiring:

```bash
curl -s -X POST https://<server_name>/catalog/reindex -H "Authorization: Bearer $TOKEN" | jq
```

It rebuilds the catalogue's index from the containers in the bucket. If it
answers with the number of studies you expect, the index is a projection and the
bucket is the truth — which is the property that makes the backup policy simple:
**back up the bucket** (and CouchDB, for Heriverse's own documents). The Catalog
index is derivable; the studies are not.

---

## Changing the realm after the first deploy

**Editing the realm JSON does not change a realm that already exists.** Keycloak's
`--import-realm` imports only when the realm is *absent*; on every later boot it
finds the realm and skips the file. So a change written into the repository,
committed and deployed, can sit there looking done and never be in effect —
and nothing says so. The symptom arrives later and elsewhere: a redirect URI
that is "clearly configured" and still refused, a mapper that is in git and not
in the token.

Measured on the dev stack, 2026-08-31, while adding the field assistant's client
configuration: the change had to be applied **twice** to be both real and
durable —

* **to the running realm**, through Keycloak's admin API, which is what takes
  effect now;
* **into the JSON**, which is what a *new* deployment (or a `--wipe`) will get.

Neither half is optional. The JSON alone is a plan; the admin API alone is a
change that the next fresh node will not inherit. Whoever edits a realm on a
live host should say, in the same breath, that they did both — and a realm
change that cannot be verified in a token is not verified at all: decode one and
look.

---

## What this page does not cover

* **Turning the host on** — provisioning the machine, the DNS record, the
  firewall. That is ops, and it is deliberately outside the playbook.
* **The production Catalog's own features** — PID/DOI minting, FAIR publication,
  the browse UI. Those are 3DR's, on the WP6 infrastructure; what deploys here is
  our reference implementation of the contract.
* **Backups and monitoring.** Named, not solved: the bucket is the study data,
  `em_data` is the room documents, CouchDB holds documents for two systems.
