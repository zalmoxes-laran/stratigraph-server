# The node console, and how a panel is added

`app/node_admin/` is the operator's instrument panel: one HTML page, one
stylesheet, no build step. It exists because a node has questions nobody's room
can answer — *is every service up, what is the bucket holding, who is in the
rooms I never declared* — and because those questions must not be answerable by
whoever owns a room.

Two things are worth writing down: the shape of a module, and the two properties
that shape protects.

## Signing in

The console authenticates against **this node's own realm** (Authorization Code
+ PKCE) and holds the token in memory for the tab. `GET /v1/auth-config` tells the
browser where the realm is and which **public** client to use (`em-console`);
`app/node_admin/auth.js` does the flow. Three properties worth keeping:

* **no secret in the page** — that is what PKCE is for, and the node's own
  `StratiGraph Server` client (confidential, no standard flow) is deliberately not the one
  the browser uses;
* **nothing is stored** — the access and refresh tokens live in memory; the PKCE
  verifier has to survive the redirect, so it goes in `sessionStorage` and is
  deleted the moment the code is exchanged;
* **the capability does not move into the client** — signing in says who you are,
  `/v1/admin/whoami` still says whether you may administer this node.

The pasted-token box remains as the fallback (dev, or a realm outage).

## The shape

A module is **three things and nothing else**:

1. a file `app/node_admin/modules/<name>.js`;
2. one line in `app/node_admin/boot.js` — `import "./modules/<name>.js";`
3. an operator-scoped endpoint `GET /v1/admin/<name>` it fetches, and a
   read-mostly render of the answer.

```js
// app/node_admin/modules/<name>.js
import { api, escapeHtml, register } from "../console.js";

async function render(root) {
  const report = await api.get("/admin/<name>");   // `api` carries the token
  root.innerHTML = `<h1>…</h1>`;                   // …and the /v1 base path
}

register({ id: "<name>", title: "<Title>", render });
```

That is the whole extension story. **`console.js` does not change to admit a
module** — it holds the token, calls the node, and shows one registered panel at
a time. It never learns a module's name.

Asserted, not asked for politely:
`tests/test_node_admin.py::test_a_module_is_admitted_without_the_shell_knowing_its_name`
reads the files and fails if the shell names a module, or if a module in
`modules/` is not imported by `boot.js` — a panel nobody imports is a panel that
silently is not there.

### Why `boot.js` exists at all

The ordering used to live in the shell as `await import("./modules/…")`. Each
module imports `register` from the shell, so that was a circular import *with a
top-level await* — and those deadlock **silently**: the page said "Loading…" for
ever and logged nothing. It was found by opening the page, not by reading the
code. `boot.js` is the one file that knows which modules exist; the dependency
now points one way (module → shell) and `boot()` runs after they have registered.

### The rules a module lives by

* **read-mostly.** An action is allowed, but it is gated by the same capability
  and confirmed by name — `Storage`'s archive asks for the room's id back,
  because on a page listing other people's workspaces one click is not consent.
* **no colours.** Every colour comes from the tokens in `console.css`. A module
  that wrote a hex value would be a second palette.
* **no secrets.** The endpoint decides what leaves the process; a module renders
  what it is given and never reaches for the token.
* **one endpoint.** A panel that fans out into five calls is a panel whose
  loading state is a lie.

## The panels

| module | endpoint | what it answers |
| --- | --- | --- |
| `users-rooms.js` | `GET /v1/admin/rooms` | every room on the node, `implicit` ones included, with members, roles and invitation links |
| `storage.js` | `GET /v1/admin/storage` | the three stores by name, per-room assets, missing containers, orphan digests, archive/restore |
| `health.js` | `GET /v1/admin/health` | per-service state with latency, what the bucket holds, and the versions in view |

### Declared, and deliberately not built

Named in `boot.js` with the endpoint each will fetch — and **not shipped**,
because a nav entry that opens an empty panel is worse than one that is not
there:

| planned | endpoint | what it would answer |
| --- | --- | --- |
| `corpus.js` | `GET /v1/admin/corpus` | the resident DTC register: how many acts, by whom, which digests carry rights |
| `iiif.js` | `GET /v1/admin/iiif` | Cantaloupe: cache size, derivatives held, `forward_auth` refusals |
| `catalog.js` | `GET /v1/admin/catalog` | StratiGraph Catalog: indexed studies, last reindex, what the index disagrees with |
| `drift.js` | `GET /v1/admin/drift` | datamodel drift across the consumers (Health already shows this node's half) |

`tests/test_node_admin.py::test_the_next_panels_are_declared_and_not_half_built`
keeps that honest: each is named with its endpoint, none exists as a file, and
each endpoint answers 404.

## Node Health, in particular

`GET /v1/admin/health` probes StratiGraph Server itself, MinIO, Keycloak, IIIF and
StratiGraph Catalog, and reports **four states**:

| state | means |
| --- | --- |
| `ok` | answered, and answered well |
| `degraded` | answered, but not usefully — a realm that publishes **no signing keys** verifies no token, and `ok` would send the operator looking in the wrong place |
| `unreachable` | did not answer inside the deadline |
| `not configured` | nobody asked for it here. **Not a failure** — a node with no Catalog is not a broken node, and painting it red teaches an operator to ignore the colour that matters |

Two things it gets right on purpose, both because the naive version was measured
first:

* **an HTTP status is an answer.** A 404 from a service that is up and a
  connection that never happened are different facts; collapsing them is how a
  health page cries wolf.
* **the bound is a wall clock, not a socket option.** `urlopen(timeout=N)`
  bounds *one connection attempt*; a black-holed address or a name with sixteen
  addresses behind it walks straight past it. So each probe runs in a **daemon
  thread** joined with a deadline (`EM_HEALTH_DEADLINE`, default 2.5 s). Written
  with `with ThreadPoolExecutor(...)` it would have *looked* bounded and still
  hung, because the context manager exits through `shutdown(wait=True)`; written
  with a non-daemon worker the report came back in 1.01 s and the **process**
  took 30 s to die. Both were measured. A probe that overruns is abandoned and
  ends at its own socket timeout — worst case one abandoned thread per
  unreachable service per page load, which is declared here rather than hidden.

The probes are sequential: five services × 2.5 s is a 12.5 s worst case that
nobody sees, because a service that is *down* answers in milliseconds
(connection refused), and a pool of pools is a way to make the health endpoint
the thing that falls over.

Knobs: `EM_HEALTH_DEADLINE` (wall clock per probe), `EM_HEALTH_TIMEOUT` (socket
timeout inside it, lower so a well-behaved failure reports itself),
`EM_HEALTH_COUNT_CAP` (how many objects the bucket listing counts before it says
`truncated` — a count that stopped at the cap without saying so would be a wrong
number wearing a right one's clothes).

The page prints **hosts, never URLs**: a MinIO endpoint can carry credentials in
its userinfo, and a health page is a screenshot waiting to happen
(`test_the_page_never_prints_a_credential`).

## Measuring it

```bash
python dev-stack/smoke_node_admin.py        # includes health: states, bound, 401/403
```

By hand, the measure that matters — a stopped service must not read `ok`:

```bash
docker-compose --env-file .env.dev -f dev-stack/docker-compose.dev.yml stop minio
# reload https://em.localhost:8443/em/admin/ → Node Health → minio: unreachable
docker-compose --env-file .env.dev -f dev-stack/docker-compose.dev.yml start minio
```
