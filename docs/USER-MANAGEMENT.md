# User management, end to end — two faces, one contract

Who may do what in the EM ecosystem is decided **once**, in em-server. Everything
else is a face on that decision:

| face | who it is for | where it lives | scope |
|---|---|---|---|
| **Members panel** | the person who owns a room | EMStudio, in the tool | *my room* |
| **Node console** | whoever looks after the server | em-server, at `/admin` | *every room* |

Both call the same endpoints. If a rule changes in `app/access.py`, both change
with it, because neither of them holds a copy.

```
Keycloak authenticates ─────► em-server authorises ─────► MinIO stores
   (who you are, ORCID)         (per-room ACL  +  per-asset DTC)     (bytes)
```

MinIO is never addressed by a client. Every byte goes through em-server, which
reads the graph to decide whether you may have it — that is the second layer, and
it is older than this document (`app/corpus.py`, `s3dgraphy.rights`).

---

## The model in five sentences

1. **A room is a durable, thin record**: `{room_id, title, container_refs,
   created_by, created_at}` and nothing else. It exists before anybody connects.
2. **A room is not a study.** A study is the published, citable unit the Catalog
   lists; a room is the *workspace*, and it **references** 1..N containers.
3. **Membership is not in the record.** It is in the ACL, beside the snapshot, and
   a room's description *projects* it. One answer to "who may write here".
4. **Getting in**: the link is the invitation, the ORCID is the identity, the ACL
   is the role. A link opens a door; it does not answer for you.
5. **Two capabilities, not one**: a room's `owner`/`admin` manages that room; a
   node **operator** is named by the deployment (realm role or `EM_OPERATORS`) and
   **cannot be self-granted** — there is no endpoint that grants it.

---

## HOWTO · a · bring the stack up

```bash
cd em-server/dev-stack
./fcn-up.sh                       # Keycloak + MinIO + em-server + Caddy (https)
curl -sk https://em.localhost:8443/em/health | python3 -m json.tool
```

The health payload says where every piece of state lives, and how an operator is
recognised:

```json
"auth": "keycloak",
"room_store":   "directory (beside the snapshots)",
"invite_store": "directory (beside the ACLs)",
"acl_store":    "directory (beside the snapshots)",
"asset_store":  "minio (http://minio:9000, bucket em-assets)",
"operators":    "realm role «em-operator» or 1 allow-listed ORCID(s)"
```

Tokens for the two people you will play:

```bash
./dev-stack/token.sh                 # `dev` — owner of the demo room, and operator
./dev-stack/token.sh --user viewer   # a second realm identity, viewer to start with
./dev-stack/token.sh --claims        # what is inside one, when a room says 4401
```

## HOWTO · b · seed a room worth looking at

```bash
python dev-stack/seed_rooms.py
```

It writes the demo documents **and** their durable records, then goes through the
API to do the part a file cannot: the roles and one invitation link.

```
[  ok  ] cantiere-demo — record + refs ['basilica-demo', 'scavo'] · owner 0000-0002-1825-0097
[  ok  ] cantiere-demo — ACL: owner 0000-0002-1825-0097 · 0000-0001-5109-3700 → viewer · 0000-0003-1415-9265 → editor
[  ok  ] cantiere-demo — invite link (editor), shown ONCE:
           token   1e94bb1c491004ff.u3rC5R7RdRtPeonDiaLVZ22SMMFgpF1w
           EMStudio  <emstudio-url>?join=<token>&room=cantiere-demo
```

The link is printed once because that is all there is: em-server keeps a **sha256
of the secret**, never the link. A leaked invite file is a list of rooms somebody
was invited to, not a set of keys.

> `em-server` reads a room's document when the room is first **opened**, and the
> dev stack mounts the code you are editing. After changing `app/` or a seed:
> `docker-compose --env-file .env.dev -f docker-compose.dev.yml restart em-server`

## HOWTO · c · the OWNER's face (EMStudio)

```bash
cd EMStudio/frontend && npm run dev        # or open frontend/dist/index.html
```

1. **Settings ▸ Live sync** → URL `https://em.localhost:8443/em`, room
   `cantiere-demo`. **Mode ▸ Hub** asks for a token: paste
   `./dev-stack/token.sh`.
2. The footer chip says how many people are in the room. **Click it** — that is
   the Members panel.
3. As the **owner** you see the roster, a role dropdown per person, *revoke*, and
   **invitation links** (`new link` → the URL, with a copy button, shown once).
   The owner's own row says *transfer only*: a transfer is a deliberate act, not
   a dropdown, and the server refuses it from a list anyway.
4. Now be the **viewer**: reload, join with `./dev-stack/token.sh --user viewer`,
   click the chip. You get the server's sentence — *"reading the member list needs
   admin or owner"* — because a membership list is a list of the people working on
   an unpublished study. Nothing to click, and it says why.
5. **Follow a link.** Open EMStudio with the seed's token:
   `http://localhost:5173/?join=<token>&room=cantiere-demo`. It asks who you are
   (the link opened the door; it does not answer for you), calls `/v1/join`, and
   walks into the room with the role the link offered. Then it **removes the token
   from the address bar** — a credential in a URL gets pasted into bug reports.
   Following a *viewer* link as somebody who is already an editor changes nothing:
   an invitation never demotes.

## HOWTO · d · the OPERATOR's face (the node console)

```
open https://em.localhost:8443/em/admin/
```

It asks for a token once (kept in memory for the tab — never in web storage) and
then asks the node `GET /v1/admin/whoami`. Two outcomes, and both are useful:

* **not an operator** → the page says so, names the capability, and states that
  owning a room does not grant it. That is the fail-closed half;
* **operator** → two modules in the left nav:
  * **Users & Rooms** — every room on the node, including the ones nobody ever
    declared (`implicit`), with members, roles, and the invitation links;
  * **Storage** — the three stores by name, per-room asset counts, containers that
    are missing, orphan digests, and *archive / restore*.

Making yourself an operator on the dev stack (it ships with one already):

```yaml
# dev-stack/docker-compose.dev.yml → em-server → environment
EM_OPERATORS: "${DEV_OPERATOR_ORCID:-0000-0002-1825-0097}"
```

```bash
docker-compose --env-file .env.dev -f docker-compose.dev.yml up -d em-server
```

In a real deployment prefer the **realm role** — `EM_OPERATOR_ROLE`, default
`em-operator`, granted in Keycloak by whoever administers the realm. Either way
the capability comes from *outside every room*, which is the property that matters:
an admin can write into an ACL, and an ACL cannot make anybody an operator.

**Archiving is a mark, never a deletion.** The console asks for confirmation and
the API asks for the room's name back (`confirm_room_id`) — on a page listing
other people's workspaces, one click is not consent. There is no delete endpoint;
`GET /v1/admin/storage` is the report that says what is dangling, and what to do
about it is a person's decision.

## HOWTO · e · measure it instead of trusting it

Four smokes, against the live stack. Each prints every check and exits non-zero if
one fails:

```bash
python dev-stack/smoke_room_durable.py     # empty+listed · refs 1 and 2 · thin · orphan reported
python dev-stack/smoke_invite_join.py      # link+ORCID → in · revoked · no identity → 401 · never demotes
python dev-stack/smoke_user_mgmt.py        # promote · admin ≠ owner · editor ≠ manager · DTC · owner-scope
python dev-stack/smoke_node_admin.py       # whoami · all rooms · storage · archive · fail-closed · console served
```

They leave behind rooms named `smoke-*`, **archived** — which is also a
demonstration of the policy.

The unit suites:

```bash
cd em-server && .venv/bin/python -m pytest -q          # includes tests/test_rooms_register.py
cd EMStudio/frontend && node scripts/check-members.mjs # the panel, headless
```

---

## The endpoints, for anything that is not one of the two faces

**Owner scope** — gated by your role *in that room* (`access.may_assign`, owner-safe):

| | |
|---|---|
| `GET /v1/rooms` | the rooms you have a grant in |
| `POST /v1/rooms` | declare one (you become its owner) |
| `GET /v1/rooms/{id}` | the record + members + `missing_refs` |
| `GET · PUT · DELETE /v1/rooms/{id}/members[/{orcid}]` | the roster and the grants |
| `GET · POST /v1/rooms/{id}/invites` · `DELETE …/{token_id}` | links |
| `POST /v1/rooms/{id}/archive` | mark it archived (or restore) |
| `POST /v1/join` | accept an invitation (needs an identity) |

**Operator scope** — gated by the node capability:

| | |
|---|---|
| `GET /v1/admin/whoami` | am I an operator, and what is the capability |
| `GET /v1/admin/rooms` | every room, declared or not |
| `GET /v1/admin/storage` | stores, per-room assets, missing containers, orphans |
| `POST /v1/admin/rooms/{id}/archive` | lifecycle, with `confirm_room_id` |

**Per-asset (DTC), unchanged and not part of this work**: `POST /v1/corpus/append`
declares who made a file and under what licence — the **attributor is the token**,
and the file's author is not locked out of their own embargoed asset whichever room
they come through. The register speaks about the **bytes**; the ACL speaks about
the **room**. Two gates, two questions.

## What is deliberately not here

* **No delete.** Not for a room, not for an orphan asset. Archive reports and
  marks; removing somebody's workspace or their bytes is a decision with a name,
  and it does not belong at the end of a garbage collector.
* **Multi-container editing in one session.** `container_refs` is a list and the
  record carries it; the live document comes from the **primary** reference. Two
  containers open at once is a follow-up, declared rather than half-built.
* **A login of the console's own.** It uses the node's realm, because a second way
  to authenticate is a second thing to get wrong.
* **Groups in the two faces.** `access.Groups` exists and the ACL resolves group
  grants; neither face edits groups yet (`/v1/groups` does).
