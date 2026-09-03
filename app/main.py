"""StratiGraph Server — the s3Dgraphy access API, over HTTP.

**P0: read-only, local, no auth.** Auth (Keycloak), assets (MinIO), the WebSocket
op-log and the deployment on the shared infrastructure are P1–P4, and they land
with 3DR. Nothing here anticipates them beyond leaving the seams where they go.

The architectural rule this file exists to honour: **FastAPI lives only in
StratiGraph Server.** s3Dgraphy stays a pure library — no web framework, no transport — and
this is a *thin adapter* over `s3dgraphy.api`. There is no new logic here, and
there must not be: if an endpoint needs to compute something, that something
belongs in the library, where it is testable without a server and reusable by
EMStudio's local bridge, by EMtools, and by EMLab.

**The contract is em-bridge's.** EMStudio already speaks to a local sidecar
(`EMStudio/tools/em_bridge.py`) with exactly these payloads; StratiGraph Server answers the
same shapes so the frontend's `bridgeUrl()` can point here instead, with nothing
else changed. Where the two differ it is stated in the endpoint's docstring.

**Stateless (12-factor).** No session, no upload directory, no database: a document
arrives in the request and leaves in the response. That is what makes it safe to
run several replicas behind a load balancer, and it is a property to defend rather
than an accident — the first endpoint that keeps a file on disk breaks it.

**Everything lives under `/v1`.** The prefix is not decoration: 3DR will build
against this contract, and a path is the cheapest promise to keep. `/v1` means the
route names and payloads of P0 do not move; the multi-client WebSocket work of P3
may well need a `/v2`, and it should be able to appear beside this one rather than
replace it.

The single exception is `GET /health`, which exists **unversioned as well**. A
health probe belongs to the infrastructure, not to the API: a Docker HEALTHCHECK, a
Kubernetes liveness probe and a Caddy upstream check should not have to be edited
the day the API version changes. `/v1/health` is the API's answer, `/health` is the
orchestrator's, and they return the same thing.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from html import escape as html_escape
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from fastapi import Request

from .assets import ASSET_STORE, asset_ref_valid
from .assets import describe as asset_describe
from .auth import AuthDependency, authenticator
from .access import Acl, Group, Role, may_assign, parse_role
from .access import describe as acl_describe
from .corpus import (CORPUS_STORE, RESIDENT, canonical_digest, may_read_whole,
                     whole_read_refusal)
from .corpus import describe as corpus_describe
from .digest_index import INDEX as DIGEST_INDEX
from .invites import Invites, invite_store_from_env, offerable, parse_offer
from .invites import describe as invite_describe
from . import operators as ops
from .blend_backups import (BLEND_MEDIA_TYPE, BlendBackups,
                            blobs_from_env as backup_blobs_from_env,
                            describe as backup_describe,
                            register_from_env as backup_register_from_env)
from .node_health import node_health, node_services
from .rooms import RoomDescriptor, RoomGraphTaken
from .store import describe as snapshot_describe
from .store import describe_rooms as room_describe
from . import ws as _ws
from .ws import (ACL_STORE, SNAPSHOT_STORE, authorize, groups, load_acl,
                 save_acl, ws_router)


#: Where the invitations live. HTTP-only on purpose: the relay never mints or
#: reads one — a link is how somebody ARRIVES, and by the time a socket opens the
#: ACL is already the only thing that matters. Kept beside the ACLs by
#: `invite_store_from_env`, because they are the same room's operational state.
INVITE_STORE = invite_store_from_env()


def invites() -> Invites:
    """The invitation register, resolved when asked (same reason as `rooms()`)."""
    return Invites(INVITE_STORE)


def rooms():
    """The relay's room registry, resolved WHEN ASKED and not bound at import.

    `from .ws import ROOMS` binds the object, so a test (or anything else) that
    replaced `ws.ROOMS` left these HTTP routes talking to the original registry
    — a footgun that has cost two debugging sessions and is documented in
    `tests/test_topology_and_visibility.py`. One registry, one place to find it.
    """
    return _ws.ROOMS


def snapshot_store():
    """The snapshot store, resolved when asked — same reason as `rooms()`.

    Binding it at import is how `_rooms_holding` ended up asking a store nobody
    was using: the gate looked correct and let an embargoed asset through in a
    test that had replaced the store. One accessor, one footgun less.
    """
    return _ws.SNAPSHOT_STORE

try:  # the whole point of the service; a clear failure beats a mysterious one
    from s3dgraphy import api as em
except ImportError as exc:  # pragma: no cover — deployment error, not runtime
    raise RuntimeError(
        "StratiGraph Server needs s3dgraphy importable: pip install s3dgraphy "
        f"(or -e ../s3Dgraphy). {exc}"
    ) from exc

__version__ = "0.1.0.dev0"

app = FastAPI(
    title="StratiGraph Server",
    version=__version__,
    summary="The s3Dgraphy access API over HTTP — read-only (P0), under /v1.",
    description=__doc__,
)

# ── ONE translation of «that graph has another home now» ─────────────────────
#
# `RoomRegistry.archive` refuses to bring a room back onto a graph a live room
# has taken, and it does so with a DOMAIN exception: `app/rooms.py` does not know
# FastAPI and is not about to learn. So the HTTP status and the wording live
# here, once — and every caller of `archive`, including one written next month,
# gets both without doing anything.
#
# THE SENTENCE IS NOT THE CREATION'S. There, «enter that room instead» is the
# whole remedy: you wanted to work on that graph and it has an address. Here the
# person is trying to get THEIR OWN room back, and sending them into somebody
# else's house does not help them. So it names who took the graph and says what
# can be done, because a refusal that only refuses leaves somebody with nothing
# to do.
#
# AND IT ONLY OFFERS WHAT EXISTS. The first version of this sentence said «point
# this room at a container of its own (POST /v1/rooms with its own
# container_refs)» — and then the live measurement refused that call with a 409,
# because `POST /v1/rooms` rightly rejects a room that is already declared. There
# is no verb, today, that changes a declared room's `container_refs`: the two
# things a person can actually do are ask the room that took the graph to step
# off it, or start a new room and carry the work there. A remedy nobody can
# perform is worse than no remedy, because it sends somebody looking for a door.
#
# (The missing verb is named in the end-of rather than invented here: who may
# re-point a room, and what happens if it is live while being re-pointed, is a
# design question and not an afternoon inside a refusal handler.)
@app.exception_handler(RoomGraphTaken)
async def _room_graph_taken(request: Request, exc: RoomGraphTaken):
    return JSONResponse(
        status_code=409,
        content={"detail": (
            f"{exc.room_id!r} cannot come back yet: the graph {exc.graph!r} is "
            f"now the live copy of the room {exc.taken_by!r} — one room, one live "
            f"graph. What you can do: ask the owner of {exc.taken_by!r} to archive "
            f"it, which frees the graph; or create a new room on a container of "
            f"your own and carry the work there. {exc.room_id!r} stays archived "
            f"and keeps its title, its creator and its references — nothing has "
            f"been lost. (Re-pointing a declared room at another container has no "
            f"verb yet.)"),
            "room_id": exc.room_id, "graph": exc.graph,
            "taken_by": exc.taken_by})


# ── who may call this from a browser ─────────────────────────────────────────
#
# Until now nothing did, and it showed: EMStudio talks to a room over a
# WebSocket (not subject to CORS) and everything worked — until the first
# ordinary HTTP call from the app. A `PUT` with an `Authorization` header is a
# preflighted request, and with no CORS policy the browser refuses it before
# StratiGraph Server ever sees it. The symptom is a bare "Failed to fetch", which reads
# like the server is down.
#
# `*` by default, and it is not laxity: **there are no cookies here**. Every
# route is opened by a bearer token the caller attaches deliberately, so a page
# that has no token gets exactly what an anonymous caller gets, whatever its
# origin. `allow_credentials` stays False for the same reason — turning it on is
# what would make `*` dangerous, and nothing here needs it.
#
# `EM_CORS_ORIGINS` (comma-separated) narrows it for a deployment that wants to.
_CORS = [o.strip() for o in (os.environ.get("EM_CORS_ORIGINS") or "*").split(",")
         if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["ETag", "X-EM-License", "X-EM-License-Default",
                    "X-EM-Embargo", "X-EM-Author", "X-EM-Authz"],
)

#: Every endpoint hangs off this router, so the prefix is declared once and cannot
#: drift between routes. A future v2 is a second router beside it, not an edit.
#:
#: **P1: the router carries the auth dependency**, so protection is a property of
#: the prefix rather than a decoration each route has to remember. A new endpoint
#: is authenticated because of where it is declared — the opposite arrangement,
#: where every handler opts in, is one forgotten line away from a public write op.
v1 = APIRouter(prefix="/v1", dependencies=[AuthDependency])

#: The two health routes live here instead, unauthenticated.
#:
#: Same prefix, no dependency: a probe is infrastructure. `/v1/health` is public
#: for the same reason `/health` is — and, more to the point, because making the
#: two paths of ONE function differ in security is how a misconfiguration hides.
#: Neither leaks anything: a version string and which optional libraries were
#: installed are things `docker inspect` already tells you.
v1_public = APIRouter(prefix="/v1")


# ── how optional dependencies are reported ────────────────────────────────────
# rdflib (TTL) and pyproj (reprojection) are optional in s3Dgraphy and stay
# optional here. A missing one is **501 Not Implemented**, never 500: the request
# was valid and the server simply cannot do that op in this build. It is the same
# mapping em-bridge uses, and it is what lets a client degrade honestly instead of
# showing an error it cannot explain.
def _missing_dependency(exc: Exception, what: str, extra: str) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail=f"{what} unavailable — this build has no {extra} ({exc})",
    )


def _load(doc: Dict[str, Any]):
    """em.json dict → (graph, warnings), with a 400 for a document we cannot read.

    A document the importer refuses is the CLIENT's problem, so it is a 400 with
    the importer's own message — not a 500, which would send someone reading server
    logs for a malformed upload.
    """
    try:
        return em.load_emjson(doc)
    except Exception as exc:
        raise HTTPException(status_code=400,
                            detail=f"not a readable em.json: {exc}") from exc


# ── health ────────────────────────────────────────────────────────────────────

class Health(BaseModel):
    ok: bool = True
    #: WHO answered. Renamed with the repo (2026-08-25): a probe reading this
    #: reads a product name, not a package. The wire `source` in `ws.py` did NOT
    #: change — that one is a protocol value the editors match on.
    service: str = "stratigraph-server"
    version: str
    s3dgraphy: Optional[str] = None
    #: which optional ops this build can actually perform. A client that reads
    #: this does not have to discover a 501 by trying.
    capabilities: Dict[str, bool] = Field(default_factory=dict)
    #: `keycloak` when tokens are enforced, `dev-no-auth` when every /v1 route is
    #: open. Reported because a warning that only exists in a log is a warning
    #: nobody reads: this way "is this deployment actually protected?" is one
    #: unauthenticated GET away, for the operator and for the client alike.
    auth: str = "dev-no-auth"
    #: P4.2 · WHERE THE DURABLE TRUTH IS. The relay holds a working copy in RAM;
    #: this says what is behind it — and an operator who reads "memory" knows
    #: their snapshots die with the process, instead of finding out.
    snapshot_store: str = "memory"
    #: how many rooms this instance currently owns (sticky routing)
    rooms: int = 0
    #: where a room's ASSET bytes live. Same question as `snapshot_store`, asked
    #: of the other half of what a room provides: the graph AND the models it
    #: points at. An operator who reads "memory" knows their uploads die with the
    #: process, instead of finding out later.
    asset_store: str = "memory"
    #: …and where the per-room ACCESS LISTS live. Same question again, asked of
    #: the third piece of room state: a deployment whose grants die with the
    #: process would silently re-open every restricted room on restart.
    acl_store: str = "memory"
    #: and where the RESIDENT DTC CORPUS lives — the documentation the asset gate
    #: reads a licence and an embargo out of. An operator who reads "memory" knows
    #: the rights they declared die with the process.
    corpus_store: str = "memory"
    #: where the DURABLE ROOM RECORDS live — the register that makes a room a
    #: place instead of a side effect of somebody connecting. An operator who
    #: reads "memory" knows their room titles and container references die with
    #: the process.
    room_store: str = "memory"
    #: …and where the INVITATIONS live. It holds no usable link (only a digest of
    #: each secret), but a deployment whose invites die on restart hands people a
    #: URL that stops working for no visible reason.
    invite_store: str = "memory"
    #: HOW this node recognises an operator — the capability that opens the
    #: node console. No names: a health endpoint open enough to be a probe is
    #: open enough to be a screenshot.
    operators: str = "none"
    #: where the OPAQUE `.blend` safety snapshots go — a separate namespace from
    #: the publishable assets, on purpose. "memory" here means somebody's safety
    #: copy would die with the process, which is the one place that must not be a
    #: surprise.
    blend_backup_store: str = "memory"


#: This node's `.blend` safety archive: the opaque snapshots people deliberately
#: keep, in their own namespace, plus the small register that says whose they are.
#: Built at import like the other stores, so a misconfigured MinIO refuses to
#: start rather than failing at somebody's first backup.
BACKUPS = BlendBackups(backup_blobs_from_env(), backup_register_from_env())


class AuthConfig(BaseModel):
    """What a BROWSER needs to sign in against this node's realm.

    Public by construction — an issuer and a client id are not secrets, and the
    one thing that would be (a client secret) does not exist for this client: the
    console is a public OIDC client and uses PKCE instead. See
    `app/node_admin/auth.js`.
    """

    #: the realm, e.g. `https://sso.example.org/realms/em`. Empty when this node
    #: runs in dev-no-auth, and the console then says so instead of redirecting
    #: to nowhere.
    issuer: str = ""
    #: the PUBLIC client the browser authenticates as (never `StratiGraph Server`, which
    #: is confidential and does not do the standard flow)
    client_id: str = ""
    #: where the IdP sends the browser back. Advertised so a deployment can be
    #: read from one place, but the console computes its OWN from the page's URL
    #: (it is served under `/admin` bare and under `/em/admin` behind a proxy)
    #: and sends that — the two must agree with what the realm allows.
    redirect_uri: str = ""
    #: the OIDC endpoints, derived from the issuer the way Keycloak lays them
    #: out. Derived rather than configured: two URLs that must agree are two
    #: URLs that will one day disagree (`auth.py` makes the same argument about
    #: the issuer and the JWKS).
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    end_session_endpoint: str = ""
    #: what the browser will ask for
    scope: str = "openid profile email"
    #: False when this node enforces nothing — the console then keeps the paste
    #: box and says why, instead of offering a Sign in that cannot work
    enforcing: bool = False


@v1_public.get("/auth-config", response_model=AuthConfig, tags=["meta"])
def auth_config() -> AuthConfig:
    """How the browser signs in to THIS node. No secret, by construction.

    The console used to be handed a bearer token pasted by hand. That is fine for
    a dev stack and wrong for a service: a person administering a node should sign
    in the way everybody else does — same IdP, same token, a different surface —
    and a console that made its own login would be a second thing to keep correct.
    So the node says where its realm is and which public client to use, and the
    browser does Authorization Code + PKCE.

    `EM_CONSOLE_CLIENT_ID` names the public client (default `em-console`). It is
    deliberately NOT `CLIENT_ID_em`: that one is confidential, has a secret, and
    does not do the standard flow — pointing a browser at it produces a login that
    fails at the last step with a message about the client rather than about the
    configuration.
    """
    settings = authenticator.settings
    issuer = str(getattr(settings, "issuer", "") or "")
    client_id = os.environ.get("EM_CONSOLE_CLIENT_ID", "em-console").strip()
    return AuthConfig(
        issuer=issuer,
        client_id=client_id if issuer else "",
        redirect_uri=os.environ.get("EM_CONSOLE_REDIRECT_URI", "").strip(),
        authorization_endpoint=(f"{issuer}/protocol/openid-connect/auth"
                                if issuer else ""),
        token_endpoint=(f"{issuer}/protocol/openid-connect/token"
                        if issuer else ""),
        end_session_endpoint=(f"{issuer}/protocol/openid-connect/logout"
                              if issuer else ""),
        scope=os.environ.get("EM_CONSOLE_SCOPE", "openid profile email").strip(),
        enforcing=bool(getattr(settings, "enforcing", False)),
    )


class NodeOffer(BaseModel):
    """One face this node offers, as somebody arriving needs to see it."""

    name: str
    label: str
    #: ok · degraded · unreachable · not configured — from the SAME probes the
    #: operator report runs, so a service cannot be up on one page and down on
    #: the other.
    state: str = "not configured"
    #: where a browser should go, or "" when this deployment published no
    #: address. Empty means the page draws no link: a guessed path is a button
    #: that 404s and a claim about where things are that nobody made.
    url: str = ""
    detail: str = ""
    #: What that face declares it CAN DO — the shape the neighbour publishes,
    #: carried through. `List[Dict]` and NOT a typed model, deliberately: a typed
    #: model would silently DROP a field the neighbour adds, and forwarding what
    #: the neighbour declares is this route's entire job. The day the field
    #: assistant declares a third capability it must arrive here, and reach the
    #: page, without anybody editing either.
    #:
    #: Shaped down in `node_health._reduce_capabilities` — objects only, strings
    #: and lists of strings only — so a public answer stays one this node can
    #: vouch for.
    capabilities: List[Dict[str, Any]] = Field(default_factory=list)


class NodeTool(BaseModel):
    """A tool you run on YOUR machine, which this node can only point at.

    The node cannot know whether EMStudio is installed on the laptop reading
    this, so it says nothing about state — two honest links beat a list that
    pretends to know. They are here rather than written into the page for the
    same reason the addresses above are: a page that carried them would be a
    place to keep aligned, and an air-gapped deployment could not point them at
    its own mirror.
    """

    name: str
    label: str
    download: str = ""
    manual: str = ""


class NodeOffers(BaseModel):
    """What this node is, and what it offers."""

    service: str = "stratigraph-server"
    version: str
    #: what the node calls itself publicly — `EM_PUBLIC_BASE`, which is also
    #: what the handoff links are built from. Empty on a node that never said.
    public_base: str = ""
    #: whether tokens are actually being checked here. `dev-no-auth` is not a
    #: detail to bury: it changes what every list on the page means.
    auth: str = "dev-no-auth"
    offers: List[NodeOffer] = Field(default_factory=list)
    #: …and what you install yourself. A different KIND of thing from `offers`,
    #: and kept apart on purpose: one is what this node runs, the other is what
    #: you run.
    tools: List[NodeTool] = Field(default_factory=list)


#: Where the desktop tools come from. Overridable per deployment (`EM_TOOLS_*`)
#: because a node on a dig with no route out should be able to point at the
#: mirror on its own disk instead of at a domain nobody can reach.
_TOOLS = (
    ("emstudio", "EMStudio", "EM_TOOLS_EMSTUDIO_DOWNLOAD",
     "https://extendedmatrix.org/download",
     "EM_TOOLS_EMSTUDIO_MANUAL", "https://docs.extendedmatrix.org"),
    ("blender", "EMtools (Blender)", "EM_TOOLS_BLENDER_DOWNLOAD",
     "https://blender.extendedmatrix.org",
     "EM_TOOLS_BLENDER_MANUAL",
     "https://docs.extendedmatrix.org/projects/EM-tools/en/1.5/installation.html"),
)


@v1_public.get("/node", response_model=NodeOffers, tags=["meta"])
def node_offers() -> NodeOffers:
    """What this node offers, and where. Public, and reduced on purpose.

    Three health questions live on this server and they are not the same one:

    * `/v1/health` — is this process up, and what can this build do (a probe for
      an orchestrator);
    * `/v1/admin/health` — what does StratiGraph Server depend on, how much is it
      holding, at which internal address. That is an infrastructure map, and it
      is operator-scoped for exactly that reason;
    * this one — what does this node OFFER, and where does a browser go. It is a
      REDUCTION of the second: same probes, same states, and then every internal
      hostname, latency and bucket count dropped.

    Why it exists at all, when the design note said no new endpoint: because the
    entrance page has to compose the node's faces without OWNING a list of where
    they live. A page that carried `/catalog` and `/chat` in its own source would
    be a fifth place to keep aligned, and would be wrong the day a deployment
    moves one. This route holds no new truth — every value in it is configuration
    the node already reads — it only publishes what the node already knows. Same
    argument as `/v1/auth-config`, and the same shape.
    """
    from . import handoff as ho

    return NodeOffers(
        version=__version__,
        # Empty when nobody configured it — `public_base` already returns "" in
        # that case rather than guessing `localhost`, and the page then says the
        # node has no public name instead of printing one that only works here.
        public_base=ho.public_base(),
        auth=authenticator.settings.describe(),
        offers=[NodeOffer(**offer) for offer in node_services()],
        tools=[NodeTool(name=name, label=label,
                        download=os.environ.get(dl_var, dl_default).strip(),
                        manual=os.environ.get(man_var, man_default).strip())
               for name, label, dl_var, dl_default, man_var, man_default
               in _TOOLS],
    )


@v1_public.get("/health", response_model=Health, tags=["meta"])
def health() -> Health:
    """Liveness, version, and what this build can do.

    `capabilities` is the part worth having: it answers "can you export TTL, can
    you reproject" without a request that fails. Probing is done by import, not by
    running an op — cheap, and it cannot have side effects.
    """
    def importable(module: str) -> bool:
        import importlib.util
        return importlib.util.find_spec(module) is not None

    version = None
    try:
        import s3dgraphy
        version = getattr(s3dgraphy, "__version__", None)
    except Exception:  # pragma: no cover
        pass
    return Health(
        version=__version__,
        s3dgraphy=version,
        capabilities={
            "validate": True,
            "export_ttl": importable("rdflib"),
            "reproject": importable("pyproj"),
            "resolve_authority": bool(em.authority_facets()),
        },
        auth=authenticator.settings.describe(),
        snapshot_store=snapshot_describe(snapshot_store()),
        asset_store=asset_describe(ASSET_STORE),
        acl_store=acl_describe(ACL_STORE),
        corpus_store=corpus_describe(CORPUS_STORE),
        room_store=room_describe(rooms().rooms_store),
        invite_store=invite_describe(INVITE_STORE),
        operators=ops.describe(),
        blend_backup_store=backup_describe(BACKUPS.blobs, BACKUPS.register),
        rooms=len(rooms().rooms()),
    )


# ── assets (the other half of what a room provides) ──────────────────────────
#
# A room gives a graph and the BYTES its assertions point at. Everything here is
# transport: the store decides what a reference is (the digest of the content),
# and this decides who may ask. No logic, per rule 1 — there is nothing to
# compute about a blob beyond hashing it, and the hashing is the store's.


class AssetInfo(BaseModel):
    ref: str
    sha256: str
    media_type: str
    size: int
    #: False when these exact bytes were already there. Content-addressing makes
    #: dedup automatic; SAYING it lets a client show "already published".
    created: bool = True
    #: who uploaded — the TOKEN's identity, never a field the client filled in
    author: Optional[str] = None


@v1.put("/rooms/{room_id}/asset", response_model=AssetInfo, tags=["assets"])
async def put_asset(room_id: str, request: Request,
                    media_type: str = Query(default="application/octet-stream",
                                            description="the MIME type of the bytes")) -> AssetInfo:
    """Publish bytes into a room's store; the reference is their digest.

    The body is the raw bytes (not multipart): an asset is one object, and a
    form wrapper would only add a boundary to parse. Re-uploading the same bytes
    is not an error and not a duplicate — it is the same object, and the answer
    says `created: false`.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body: nothing to store")
    info = ASSET_STORE.put(data, media_type)
    principal = authenticator.require_token(request)
    author = None if principal.get("em_dev_mode") else (
        principal.get("orcid") or principal.get("preferred_username")
        or principal.get("sub"))
    return AssetInfo(**info, author=author)


@v1.get("/rooms/{room_id}/asset/{ref:path}", tags=["assets"])
async def get_asset(room_id: str, ref: str, request: Request) -> Response:
    """Fetch an asset by reference — if the graph says you may have it yet.

    The caller can verify what it got: the reference IS the digest.

    **The store consults the graph; it does not duplicate it.** An asset's
    embargo is stated in the document (on its `ResourceNode`, or on the DTC
    chunk that produced it) and read here through
    `s3dgraphy.api.asset_rights` — never cached, because an embargo lives in a
    text people edit and a remembered copy is wrong the first time somebody
    changes their mind.

    The rule, and it is the study's rule applied one level down: while an
    embargo is running, these bytes are for the people who are working on them —
    **editor and above**. A viewer or an anonymous caller gets **403 with the
    date**, not a 404: pretending the file does not exist would be a lie the
    caller can disprove by asking again in March.

    The two embargoes COMPOUND, and the more restrictive wins: a room under a
    study-level embargo already refuses at the door, and an asset may be
    embargoed inside a study that is not.

    An asset the graph says nothing about is served as it always was.
    """
    if not asset_ref_valid(ref):
        raise HTTPException(status_code=400,
                            detail=f"not an asset reference: {ref!r} "
                                   f"(expected 'sha256:<hex>')")
    data = ASSET_STORE.get(ref)
    if data is None:
        raise HTTPException(status_code=404, detail=f"no asset {ref}")

    # THE NAMED ROOM FIRST, then everybody else — because the store is shared.
    #
    # Measured, and it was a hole big enough to make the rest theatre: the same
    # digest requested through a DIFFERENT room came back 200, since the embargo
    # is stated in the graph of the room that holds the picture and the asset
    # store is not partitioned by room. A gate you get past by typing another
    # room name is not a gate.
    rights = await _rights_seen_anywhere(room_id, ref, request)
    # …AND THE RESIDENT CORPUS. This is the hole that was measured: the licence
    # was declared in a corpus, and a corpus StratiGraph Server does not hold cannot make
    # anything bite. The register speaks about the BYTES, so it speaks whatever
    # room the caller came through.
    rights = await _corpus_gate(ref, request, door=room_id, room_rights=rights)

    meta = ASSET_STORE.head(ref) or {}
    headers = {"ETag": f'"{ref}"'}
    # The licence TRAVELS WITH THE BYTES. Not enforcement — a share-alike cannot
    # be imposed by an HTTP header, and pretending otherwise would be worse than
    # saying nothing. What this does is remove the excuse: whoever downloads
    # this file has been told, in the same breath, what they may do with it.
    if rights:
        headers["X-EM-License"] = str(rights.get("license_effective") or "")
        if rights.get("license_is_default"):
            headers["X-EM-License-Default"] = "true"
        if rights.get("embargo"):
            headers["X-EM-Embargo"] = str(rights["embargo"])
        authors = [a.get("orcid") or a.get("name") for a in rights.get("authors") or []]
        if authors:
            headers["X-EM-Author"] = ", ".join(str(a) for a in authors if a)
    return Response(content=data,
                    media_type=str(meta.get("media_type") or "application/octet-stream"),
                    headers=headers)


# ── the `.blend` safety archive (opaque, on demand, never publishable) ───────
#
# A different thing from the assets above, and the difference is the point. An
# asset is PUBLISHED: content-addressed, citable, served under the rights the
# graph declares. A `.blend` snapshot is KEPT: opaque, in its own namespace,
# readable only by the person who kept it, cited by nothing. See
# `app/blend_backups.py` for why the shared data is not versioned here and why
# the `backup` note does not go into the resident corpus.


class BlendBackupOut(BaseModel):
    """One snapshot, as its owner sees it."""

    sha256: str
    size: int
    label: str = ""
    #: the `.blend`'s own name, for recognising a snapshot months later
    filename: str = ""
    #: whose it is — the TOKEN's identity, never a field the client filled in
    orcid: Optional[str] = None
    created_at: str = ""
    #: when these same bytes were last archived again, and how many times. A
    #: fact, not a rewrite: `created_at` never moves.
    last_seen: str = ""
    seen: int = 1
    #: False when this snapshot was already kept HERE — dedup, said out loud
    created: bool = True
    #: …and whether bytes were actually written. Not the same question: the store
    #: is node-wide, so the same `.blend` kept in two rooms is two snapshots of
    #: one object. Conflating the two reported "already kept" for a snapshot
    #: somebody had just taken (measured).
    stored_bytes: bool = True
    detail: str = ""
    #: the DTC-shaped note: a distinct event («somebody kept a working file»),
    #: explicitly not a derivation producing a scientific product
    dtc: Dict[str, Any] = Field(default_factory=dict)


async def _backup_door(room_id: str, request: Request) -> tuple:
    """(orcid, dev_mode) for somebody archiving in this room, or a refusal.

    **Fail-closed, and stricter than an upload.** Publishing an asset needs a
    token; keeping a backup needs a token AND a role that can write here — a
    room you may only read is not a room you may store your working files in.
    Dev mode has no identities, so it reads every author's records and says so
    (`all_authors`), rather than pretending a nobody owns them.
    """
    _acl, _room, role, who = await _acting_role(room_id, request)
    if role is None or not role.can_write:
        raise HTTPException(
            status_code=403,
            detail="keeping a .blend snapshot in this room needs editor or "
                   "above: it is your working file, but it is their room")
    dev_mode = who is None and not authenticator.settings.enforcing
    return who, dev_mode


@v1.put("/rooms/{room_id}/blend-backup", response_model=BlendBackupOut,
        tags=["assets"])
async def archive_blend(room_id: str, request: Request,
                        label: str = Query(default="", description="what this snapshot is"),
                        filename: str = Query(default="", description="the .blend's own name")
                        ) -> BlendBackupOut:
    """Keep the posted bytes as an opaque snapshot of a working file.

    Deliberate, not automatic: there is no save hook and there will not be one —
    a backup that happens on every save is a quota, not a safety net. The daily
    versioning of a `.blend` stays on the modeller's disk; this is the copy that
    survives the disk.

    Idempotent by content. The same `.blend` archived twice is **one** object and
    one record: the answer says `created: false` and the record's `seen` goes up,
    while `created_at` and the label stay the first ones — a backup whose date
    moves is not a backup.
    """
    who, dev_mode = await _backup_door(room_id, request)
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400,
                            detail="empty body: there is no snapshot in zero bytes")
    try:
        record = BACKUPS.archive(room_id, data, orcid=who, label=label,
                                filename=filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BlendBackupOut(**{k: v for k, v in record.items() if k != "room_id"})


@v1.get("/rooms/{room_id}/blend-backups", response_model=List[BlendBackupOut],
        tags=["assets"])
async def list_blend_backups(room_id: str, request: Request
                             ) -> List[BlendBackupOut]:
    """The snapshots YOU kept in this room, newest first.

    Only your own. Being an editor of the room is what let you archive; it does
    not make somebody else's working file yours to read — a `.blend` in progress
    is not documentation of the study, and the register is per-author for the same
    reason a room's member list is not public.
    """
    who, dev_mode = await _backup_door(room_id, request)
    return [BlendBackupOut(**{k: v for k, v in record.items()
                              if k != "room_id"}, created=False)
            for record in BACKUPS.mine(room_id, orcid=who,
                                       all_authors=dev_mode)]


@v1.get("/rooms/{room_id}/blend-backup/{sha256}", tags=["assets"])
async def restore_blend(room_id: str, sha256: str, request: Request) -> Response:
    """The exact bytes back. Verify them: the name IS the digest.

    404 for a snapshot that is not yours, deliberately — "you did not keep this"
    and "nobody kept this" are the same answer to the person asking, and telling
    a caller which digests exist would turn a register they may not read into one
    they can probe.
    """
    who, dev_mode = await _backup_door(room_id, request)
    data = BACKUPS.fetch(room_id, sha256, orcid=who, all_authors=dev_mode)
    if data is None:
        raise HTTPException(status_code=404,
                            detail=f"no snapshot {sha256} kept by you in this room")
    return Response(content=data, media_type=BLEND_MEDIA_TYPE,
                    headers={"ETag": f'"{str(sha256).lower()}"',
                             "X-EM-Opaque-Backup": "true"})


async def _asset_rights(room_id: str, ref: str) -> Optional[Dict[str, Any]]:
    """What this room's graph says about these bytes, or None for "nothing".

    **FAIL-CLOSED.** There are three outcomes and only two of them are answers:

    * *(i)* the graph was read and says nothing about this digest → `None`, and
      the caller serves the bytes. No regression: an asset with no DTC behaves
      exactly as it always has;
    * *(ii)* the graph was read and states an embargo → the rights, and the
      caller applies the gate;
    * *(iii)* **the graph could not be read at all** → this raises, and the
      caller answers 503. It used to return `None` here, which meant "nothing
      said", which meant *serve* — so a room whose document was corrupt, or a
      store that was briefly unreachable, published every embargoed file in it.

    (iii) is the whole point of this function existing separately: whether an
    asset is embargoed is **unknowable without the graph**, so "I cannot read
    the graph" must not be allowed to answer the question. The failure of a
    store is a reason to say *not now*, never a reason to say *yes*.

    A room nobody has ever written is case (i), not (iii), and that is
    deliberate: it has no document in which an embargo could be hiding, and the
    asset store is shared across rooms rather than partitioned by them. Making
    it a 404 would break the one path that proves this — `smoke.py` uploads to a
    room it never opens — while protecting nothing, because any room that DOES
    exist would still serve the same bytes.
    """
    room = await rooms().get(room_id)      # may raise: the store is not here
    known, rights = DIGEST_INDEX.rights(room_id, room, _digest_of(ref))
    if known:
        # …from the index, which is rebuilt the moment the room's document
        # changes (`Room.revision`) rather than on a timer. What is cached is
        # what the GRAPH SAYS; whether the embargo is running is computed from
        # today's date every time it is asked, below.
        return _fresh_verdict(rights)
    return em.asset_rights(room.document, ref)


def _fresh_verdict(rights: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Re-decide `embargo_active` NOW, on cached facts.

    The date is the one part of the answer that changes without anybody writing
    anything: an embargo that ends tomorrow is over tomorrow, and an index that
    stored the verdict would go on refusing the file until somebody edited the
    graph. So the FACTS are cached and the VERDICT is not.
    """
    if not rights:
        return rights
    try:
        from s3dgraphy.study import embargo_active
    except ImportError:  # pragma: no cover — the library is a hard dep
        return rights
    return {**rights, "embargo_active": embargo_active(rights.get("embargo"))}


async def _rights_seen_anywhere(room_id: str, ref: str,
                                request: Request) -> Optional[Dict[str, Any]]:
    """The rights to REPORT, having refused if any room forbids this caller.

    Asks the named room first — it is the one the caller meant, and its answer
    is the one whose licence belongs in the headers. Then, only if nothing has
    refused yet, the other rooms this instance holds, because a digest is
    content and the same picture can be cited in several studies; an embargo
    stated in one of them is stated about these bytes.

    Raises 403 (embargoed, and this caller is not editor+ **there** — the role
    is resolved in the room that made the statement, which is the room that can
    grant it) or 503 (a document that will not read: see `_asset_rights`).

    The declared cost: on a big instance this reads every room's document. It is
    bounded by the rooms one instance owns, and a deployment that minds can pass
    the room explicitly. Correct-and-slow beats fast-and-bypassable.
    """
    first: Optional[Dict[str, Any]] = None
    checked: List[str] = []
    for candidate in [room_id] + [r for r in _rooms_holding(_digest_of(ref))
                                  if r != room_id]:
        try:
            rights = await _asset_rights(candidate, ref)
        except Exception as exc:  # noqa: BLE001 — case (iii), fail-closed
            raise HTTPException(
                status_code=503,
                detail=f"cannot verify this asset's rights right now: the room "
                       f"{candidate!r} has a document that will not read "
                       f"({type(exc).__name__}). The bytes are not served until "
                       f"it does — an embargo that cannot be read is not an "
                       f"embargo that can be ignored.") from None
        checked.append(candidate)
        if candidate == room_id:
            first = rights
        if not rights or not rights.get("embargo_active"):
            continue
        role = await _role_in_room(candidate, request)
        if role is not None and role.can_write:
            continue
        raise HTTPException(
            status_code=403,
            detail=f"this asset is under embargo until {rights.get('embargo')} "
                   f"— until then it is readable by the people working on the "
                   f"study (editor and above)")
    return first


async def _corpus_gate(ref: str, request: Request, *, door: Optional[str],
                       room_rights: Optional[Dict[str, Any]]
                       ) -> Optional[Dict[str, Any]]:
    """Apply — and report — what the RESIDENT CORPUS says about these bytes.

    The register is per-instance and content-addressed, so it speaks about the
    file rather than about a room: whichever door the caller came through, a
    licence declared in the documentation is the licence of these bytes, and an
    embargo declared there is an embargo on them.

    **FAIL-CLOSED, like the room walk**: a corpus store that will not read raises
    503. "I cannot read the documentation" must not be allowed to answer "yes" —
    that was the bug `_asset_rights` was rewritten to remove, and a second reader
    with the opposite reflex would put it back.

    **Who overrides an embargo**: editor and above **in the room the request came
    through** — the door is the room the caller is working in, and it is the room
    that can grant them the role. When no room is named (a IIIF request with no
    `?room=`) the rooms citing the digest are asked instead; when nothing cites it
    at all, an embargoed file is refused to everybody until the date passes,
    because there is no room in which anybody could be its editor. (Dev mode has
    no identities and lets everything through, as everywhere else.)

    Returns the rights to REPORT: the room's statement where it made one, filled
    in from the corpus where it did not, and the **more restrictive** embargo of
    the two — the same compounding rule the two embargo levels already follow.
    """
    try:
        corpus_rights = RESIDENT.rights_for(_digest_of(ref))
    except Exception as exc:  # noqa: BLE001 — fail-closed, exactly like a room
        raise HTTPException(
            status_code=503,
            detail=f"cannot verify this asset's rights right now: the resident "
                   f"documentation will not read ({type(exc).__name__}). The bytes "
                   f"are not served until it does — an embargo that cannot be read "
                   f"is not an embargo that can be ignored.") from None
    if not corpus_rights:
        return room_rights

    if corpus_rights.get("embargo_active"):
        allowed = False
        if not authenticator.settings.enforcing:
            allowed = True                     # dev mode has no identities
        else:
            # WHOSE FILE IT IS, first: the people named as its authors are the
            # ones the embargo is protecting, not the ones it is protecting the
            # file from. Refusing them their own photograph while it is under
            # study would be the gate working against the person it works for.
            me = _identity_of(request)
            authors = {str(a.get("orcid") or "").strip()
                       for a in corpus_rights.get("authors") or []}
            if me and me in authors:
                allowed = True
            # …then a room where the caller may WRITE — resolved WITHOUT the
            # owner bootstrap.
            #
            # Measured: naming a room nobody had ever created got 200, because a
            # room with no owner makes the first authenticated arrival its OWNER
            # (`ws.authorize`) — so a viewer walked around a corpus embargo by
            # inventing a room name in the URL. Same shape as the room-shopping
            # hole `_rights_seen_anywhere` exists for, one level up. Reading the
            # grants without claiming any is the precise fix: an invented room has
            # no ACL and its empty document names no owner, so it grants nothing,
            # while a real editor of a room that was never saved still gets in.
            candidates = ([door] if door else []) \
                + [r for r in _rooms_holding(_digest_of(ref)) if r != door]
            for candidate in [c for c in candidates if c and not allowed]:
                role = await _role_without_bootstrap(candidate, me, request)
                if role is not None and role.can_write:
                    allowed = True
                    break
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=f"this asset is under embargo until "
                       f"{corpus_rights.get('embargo')} — until then it is "
                       f"readable by the people working on the study (editor and "
                       f"above)")
    return _combine_rights(room_rights, corpus_rights)


def _combine_rights(room: Optional[Dict[str, Any]],
                    corpus: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Two statements about one file, reported as one answer.

    The room is the more specific speaker (it is the study the caller opened), so
    what it SAID wins; what it left unsaid the corpus fills in — which is the
    whole point, because "nothing said" is exactly what the file corpus used to
    leave behind. The **embargo compounds**: the later date wins, because a file
    freed in one place and embargoed in another is embargoed.
    """
    if not corpus:
        return room
    if not room:
        return corpus
    out = dict(room)
    if not out.get("license"):
        for key in ("license", "license_effective", "license_is_default"):
            if corpus.get(key) not in (None, ""):
                out[key] = corpus[key]
    if not out.get("authors") and corpus.get("authors"):
        out["authors"] = corpus["authors"]
    mine, theirs = str(out.get("embargo") or ""), str(corpus.get("embargo") or "")
    if theirs and theirs > mine:      # ISO dates compare as strings
        out["embargo"] = corpus["embargo"]
        out["embargo_active"] = corpus.get("embargo_active")
    if not out.get("via") and corpus.get("via"):
        out["via"] = corpus["via"]
    return out


def _digest_of(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1].lower()


async def _role_in_room(room_id: str, request: Request):
    """The caller's role here, from an OPTIONAL token.

    Optional because an asset URL is fetched by things that cannot log in, and
    because a public study's assets are public. No token is not an error at this
    point — it simply resolves to whatever a stranger gets, which the embargo
    rule then judges.
    """
    header = request.headers.get("authorization") or ""
    token = request.query_params.get("token")
    claims: Dict[str, Any] = {}
    if not authenticator.settings.enforcing:
        claims = {"em_dev_mode": True}
    elif header.lower().startswith("bearer ") or token:
        try:
            claims = (authenticator.require_token(request)
                      if header.lower().startswith("bearer ")
                      else authenticator.verify(str(token).strip()))
        except Exception:  # noqa: BLE001 — a bad token is simply not an identity
            claims = {}
    orcid = None if claims.get("em_dev_mode") else (
        claims.get("orcid") or claims.get("preferred_username") or claims.get("sub"))
    room = await rooms().get(room_id)
    return authorize(room, orcid, dev_mode=bool(claims.get("em_dev_mode")))


# ── the RESIDENT DTC corpus (the documentation this instance can enforce from) ─
#
# The hole it closes was measured on 17 Aug: an asset whose licence lived in a
# per-project corpus FILE was served with `x-em-license: null`, because the
# enforcement reads the rights out of a document StratiGraph Server holds and a file on a
# laptop is not one. Give the corpus a residence and the rights bite (see
# `app/corpus.py` for why it is one per instance and not one per room).
#
# Transport only, per rule 1: the three acts are `s3dgraphy`'s
# (`bucket_acquisition` / `declare_derivation` / `enrich_asset_dtc`) — the same
# protocol EMStudio and EMtools perform — and the merge is the container's.


class CorpusOut(BaseModel):
    """The corpus, or the slice of it that was asked for."""

    #: the em.json graph section, marked `em_collection: DTCCorpus`
    graph: Dict[str, Any] = Field(default_factory=dict)
    #: sha256 over the canonical JSON of the WHOLE corpus. The version is what
    #: the corpus says, not how many times somebody saved — so a client can tell
    #: "nothing changed" without diffing, and a slice still reports the version
    #: of the register it came out of.
    version: str = ""
    nodes: int = 0
    edges: int = 0
    #: True when `sha256=` narrowed the answer — a client that asked for four
    #: digests and got four nodes should not have to guess whether that is the
    #: whole register.
    sliced: bool = False
    #: where the documentation lives, in the words `/v1/health` uses
    store: str = ""


class CorpusAct(BaseModel):
    """One documentation act to append. `act` picks which, the rest is its body.

    Deliberately ONE endpoint with a named act rather than three routes: the
    three are the same kind of statement about the same register, they are all
    idempotent, and a client that performs two of them in a row should not have
    to learn two shapes.
    """

    act: str = Field(description="acquisition | derivation | attribution")
    #: acquisition: the files (ids **or** digests) and how the lot is described
    resources: Optional[List[str]] = None
    acquisition_id: Optional[str] = None
    name: Optional[str] = None
    dtc_kind: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    #: derivation: what came out, and what went in (an acquisition counts as one)
    output: Optional[str] = None
    inputs: Optional[List[str]] = None
    tool: Optional[str] = None
    process_id: Optional[str] = None
    #: resource: a file the register should know about — `checksum` is the door,
    #: and for a RESIDENT one StratiGraph Server checks its own store before believing it
    media_type: Optional[str] = None
    residency: Optional[str] = None
    url: Optional[str] = None
    url_type: Optional[str] = None
    scope: Optional[str] = None
    size: Optional[int] = None
    #: attribution: whose file this is and what may be done with it
    checksum: Optional[str] = None
    author: Optional[str] = None
    author_name: Optional[str] = None
    license: Optional[str] = None
    embargo: Optional[str] = None
    reason: Optional[str] = None
    at: Optional[str] = None


class CorpusAppendOut(BaseModel):
    act: str
    #: whatever the library reported — ids, counts, the missing references it
    #: refused to invent, its warnings. Passed through verbatim: this is a thin
    #: adapter, and summarising a report is how a caller stops being able to see
    #: that three of its four files were not there.
    report: Dict[str, Any] = Field(default_factory=dict)
    version: str = ""
    #: who signed the act — the TOKEN's identity, never a field in the body
    by: Optional[str] = None


@v1.get("/corpus", response_model=CorpusOut, tags=["corpus"], responses={
    403: {"description": "the WHOLE register is a curation read; ask for a slice"},
})
def get_corpus(
    request: Request,
    sha256: Optional[List[str]] = Query(
        default=None,
        description="one or more digests: the answer is the part of the corpus "
                    "that speaks about those files (repeat the parameter, or "
                    "pass a comma-separated list). WITHOUT this the whole "
                    "register is asked for, which is a curation read"),
) -> CorpusOut:
    """The instance's documentation — the slice a study needs, or (for a curator)
    the whole thing.

    A study cites a handful of assets out of a register that may hold thousands,
    so the slice is not an optimisation: sending the whole corpus to draw four
    nodes is what would make the resident corpus unusable for the client it
    exists for. The slice keeps the chain around those files (the acquisition
    that brought them in, the transformation that made them, their rights) —
    reached by walking the DTC edges, because an acquisition without its licence
    node would answer the rights question wrongly.

    **The slice is open to any authenticated caller; the WHOLE register is not.**
    A digest is a citation — asking about a file you hold is the register's
    purpose. The lot is something else: the provenance of every study on this
    instance, which is a curation read (`app/corpus.py` says who, and why the
    default is off). The refusal names the remedy rather than returning an empty
    answer, because "you got nothing" and "there is nothing" must not look alike.
    """
    digests: List[str] = []
    for raw in sha256 or []:
        digests.extend(part.strip() for part in str(raw).split(",") if part.strip())
    if not digests:
        principal = authenticator.require_token(request)
        dev_mode = bool(principal.get("em_dev_mode"))
        who = None if dev_mode else (principal.get("orcid")
                                     or principal.get("preferred_username")
                                     or principal.get("sub"))
        if not may_read_whole(who, dev_mode=dev_mode):
            raise HTTPException(status_code=403, detail=whole_read_refusal())
    section = RESIDENT.read_slice(digests or None)
    whole = RESIDENT.read()
    return CorpusOut(
        graph=section, version=canonical_digest(whole),
        nodes=len(section.get("nodes") or []),
        edges=len(section.get("edges") or []),
        sliced=bool(digests), store=corpus_describe(CORPUS_STORE))


class NeighbourhoodOut(BaseModel):
    """The DTC neighbourhood of one asset — its story, not the register."""

    #: the digest that was asked about, echoed so an answer is self-describing
    sha256: str
    #: the node the walk started from, or None when the corpus never heard of
    #: these bytes. Not an empty neighbourhood: «I do not know this file» and
    #: «this file has no story» are different answers.
    start: Optional[str] = None
    #: the ceiling that was in force, and whether it bit
    hops: int = 0
    truncated: bool = False
    #: which nodes were left unexpanded when it did — said, because a walk that
    #: stopped and did not say so is a walk whose answer looks complete
    frontier: List[str] = Field(default_factory=list)
    #: one card per node reached, each with its `context`: the author, the
    #: licence, the embargo that hang off THAT node
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    #: the chain edges inside the neighbourhood
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    #: the corpus version this answer came out of, as the slice already reports
    version: str = ""


@v1.get("/corpus/neighbourhood", response_model=NeighbourhoodOut, tags=["corpus"])
def corpus_neighbourhood(
    request: Request,
    sha256: str = Query(description="the asset's digest — what a store browser "
                                    "has in its hand"),
    hops: int = Query(default=0, ge=0, le=20,
                      description="how far along the chain to walk; 0 means the "
                                  "library's own default, which is a safety net "
                                  "rather than a filter"),
) -> NeighbourhoodOut:
    """What made this file, what it went on to make, and through which events.

    **The browser gets the neighbourhood, never the register.** That is the same
    rule `GET /v1/corpus` already enforces — a whole read is curation, a slice by
    digest is an ordinary gesture — and here it is not even a rule to enforce:
    the answer is bounded by construction. The walk follows only the three DTC
    chain edges (`dtc_had_input`, `dtc_had_output`, `dtc_derived_from`), and
    anything reached by a `has_*` is an ATTRIBUTE of the node it hangs off rather
    than a place to continue from.

    Which is why there is no list of node types not to cross, and no ceiling on a
    node's degree: degree changes by itself as the data grows, so the same
    question would answer differently in March and in July. A new kind of context
    — a site, a campaign, a funding body — is non-traversable **by
    construction**, because it is not one of the three edges. Nothing to update.

    The traversal itself is s3Dgraphy's (`s3dgraphy.dtc.neighbourhood`): the
    vocabulary and the walk belong to the library, where EMStudio and EMtools
    reach them too. This route reads the corpus once per version (see
    `ResidentCorpus.graph`) and hands the graph over.
    """
    from s3dgraphy.dtc import neighbourhood_of_digest
    from s3dgraphy.dtc.neighbourhood import DEFAULT_HOPS

    authenticator.require_token(request)      # a digest is a citation; a name is not
    digest = str(sha256 or "").strip()
    if not digest:
        raise HTTPException(status_code=400,
                            detail="ask about a digest: sha256=<the asset's hash>")
    graph, version = RESIDENT.graph()
    answer = neighbourhood_of_digest(graph, digest,
                                     hops=hops or DEFAULT_HOPS)
    return NeighbourhoodOut(sha256=digest, version=version, **answer)


@v1.post("/corpus/append", response_model=CorpusAppendOut, tags=["corpus"])
def append_corpus(request: Request,
                  act: CorpusAct = Body(...)) -> CorpusAppendOut:
    """Perform one documentation act on the resident corpus.

    **Who may**: any authenticated caller (the router's dependency). The corpus
    is the register of what this instance's rooms are made of, and a person who
    can log in and upload bytes can say where they came from — while the
    ATTRIBUTOR is taken from the token, so nobody signs a statement in somebody
    else's name. A finer rule (a role over the register) is a decision about
    curation, not a line of code, and it is not invented here.

    Idempotent, like the acts themselves: the same acquisition twice is one lot,
    and a client that retries after a timeout does not double anything.
    """
    who = _caller(request)
    # A RESIDENT file is one this instance HOLDS. Checking it here is what makes
    # the register a description of the store rather than of somebody's intention:
    # a digest nobody uploaded would sit in the corpus carrying a licence for
    # bytes that are not there, and the asset gate would look it up for ever.
    if str(act.act).strip().lower() == "resource":
        residency = (act.residency or "resident").strip().lower()
        ref = str(act.checksum or "")
        if residency == "resident":
            if not asset_ref_valid(ref):
                raise HTTPException(
                    status_code=400,
                    detail=f"not an asset reference: {ref!r} (expected "
                           f"'sha256:<hex>')")
            if ASSET_STORE.head(ref) is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no asset {ref} in this instance's store: register a "
                           f"file after uploading it (or declare it "
                           f"`residency: reference` if the bytes live elsewhere)")
    try:
        report, version = RESIDENT.append(act.act,
                                         act.model_dump(exclude_none=True),
                                         author=who)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        # the library's way of saying "that file is not in here"
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CorpusAppendOut(act=act.act, report=report, version=version, by=who)


@v1.post("/corpus/merge", response_model=CorpusAppendOut, tags=["corpus"])
def merge_corpus_endpoint(request: Request,
                          graph: Dict[str, Any] = Body(...)) -> CorpusAppendOut:
    """Fold a project's **file** corpus into the resident one — the promote path.

    Offline is not a lesser mode: a study documented on a laptop carries its DTC
    inside its own em.json, and joining a room must bring that documentation with
    it. Additive and per-UUID (`s3dgraphy.api.merge_corpus`, the container's own
    merge), so promoting twice does not duplicate and two people who photographed
    the same stone end up with one entry.

    The body is the corpus **member section** (`{graph_id, nodes, edges, data}`),
    or a container from which it is taken.
    """
    who = _caller(request)
    section = graph
    if isinstance(graph, dict) and isinstance(graph.get("graphs"), dict):
        section = next((g for g in graph["graphs"].values()
                        if isinstance(g, dict)
                        and em.is_dtc_corpus(g)), None)
        if section is None:
            raise HTTPException(
                status_code=400,
                detail="this container has no DTC corpus member: nothing to merge "
                       "(a member is a corpus by its `data.em_collection` marker, "
                       "not by being called `dtc`)")
    if not isinstance(section, dict) or "nodes" not in section:
        raise HTTPException(status_code=400,
                            detail="expected a corpus graph section with `nodes`")
    report, version = RESIDENT.merge(section, author=who)
    return CorpusAppendOut(act="merge", report=report, version=version, by=who)


# ── IIIF · the authorisation the image server does not have ───────────────────
#
# Cantaloupe reads MinIO by sha256 and StratiGraph Server is NOT in the path of a pixel.
# Taking the canvas out of the manifest withholds the digest — which is real,
# because the manifest is where a digest comes from — but somebody who already
# has the hex reaches the image directly. That is the hole this closes.
#
# The shape: the proxy in front of the image server asks StratiGraph Server first
# (`forward_auth`), for every `/iiif/*` request. StratiGraph Server stays the authority —
# it is the process that can read the graph, resolve the room's ACL and apply
# exactly the rule the asset route applies. Cantaloupe keeps knowing nothing
# about rights, which is right: an image server should serve images.
#
# The alternative was a Cantaloupe DELEGATE script (Ruby, calling back here).
# Rejected for two reasons: it puts a second implementation of the rule in a
# second language inside a component whose job is not authorisation, and it is
# invisible to anybody reading the routing — the place people look when they ask
# "who can reach this". `forward_auth` is one stanza in the file that already
# decides what reaches what.

@v1_public.get("/iiif-authz", tags=["iiif"], responses={
    200: {"description": "let it through"},
    403: {"description": "under embargo for this caller"},
    503: {"description": "the rights cannot be read right now"},
})
async def iiif_authz(request: Request, response: Response,
                     token: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """May the caller of THIS image request have it? For a proxy to ask.

    Answers about the request the proxy is holding, which it hands over in the
    headers it copies (`X-Forwarded-Uri` / `X-Original-Uri`): the IIIF
    identifier is the bare sha256, so the digest is in the path. No body, no
    guessing — the same digest the asset route would be asked for.

    **The same rule as `get_asset`, from the same code.** While an embargo runs
    the image is for editor and above; everybody else gets 403. Two rules for
    one question would be one rule and one bug.

    Unauthenticated by design: it is a question about somebody ELSE's request,
    and the answer for a caller with no token is exactly the answer a caller
    with no token should get.
    """
    digest = _digest_from_iiif_path(
        request.headers.get("x-forwarded-uri")
        or request.headers.get("x-original-uri")
        or request.query_params.get("uri") or "")
    if not digest:
        # Nothing that looks like a digest: this is not an image request we can
        # judge (`info.json` of something else, a static asset). Letting it
        # through is correct — the gate exists for images, and refusing what it
        # cannot read would break the image server for everything else.
        response.headers["X-EM-Authz"] = "not-an-image-request"
        return {"ok": True, "reason": "no digest in the request"}

    # The SAME walk the asset route uses, and deliberately the same function:
    # two implementations of "may this person see this picture" would be one
    # rule and one bug, and the bug would be the one nobody tested.
    room_id = (request.query_params.get("room")
               or request.headers.get("x-em-room") or "").strip()
    if room_id:
        await _rights_seen_anywhere(room_id, digest, request)
        checked = 1
    else:
        rooms_to_ask = _rooms_holding(digest)
        checked = len(rooms_to_ask)
        for candidate in rooms_to_ask:
            await _rights_seen_anywhere(candidate, digest, request)
    # …and the resident documentation, ONCE and even when no room mentions this
    # picture at all: an image published from the register is still an image the
    # register can embargo, and a gate that only looked at rooms would let it
    # through precisely when nobody had opened the study.
    await _corpus_gate(digest, request, door=room_id or None, room_rights=None)
    response.headers["X-EM-Authz"] = "ok"
    return {"ok": True, "digest": digest, "rooms_checked": checked}


def _digest_from_iiif_path(path: str) -> Optional[str]:
    """The sha256 out of a IIIF URL, or None.

    `/iiif/3/<identifier>/full/max/0/default.jpg` — and the identifier is the
    bare hex, because that is what Cantaloupe uses as the object key (measured,
    the night the `sha256%3A` form 404'd). Anything else in that position is not
    ours to judge.
    """
    import re
    import urllib.parse

    for part in urllib.parse.unquote(path).split("/"):
        candidate = part.rsplit(":", 1)[-1]
        if re.fullmatch(r"[0-9a-f]{64}", candidate.lower()):
            return candidate.lower()
    return None


def _identity_of(request: Request) -> Optional[str]:
    """The caller's ORCID from an OPTIONAL token, or None. Never an error: an
    asset URL is fetched by things that cannot log in, and a stranger is a valid
    answer to "who is this"."""
    header = request.headers.get("authorization") or ""
    token = request.query_params.get("token")
    claims: Dict[str, Any] = {}
    try:
        if header.lower().startswith("bearer "):
            claims = authenticator.require_token(request)
        elif token:
            claims = authenticator.verify(str(token).strip())
    except Exception:  # noqa: BLE001 — a bad token is simply not an identity
        claims = {}
    if claims.get("em_dev_mode"):
        return None
    return (claims.get("orcid") or claims.get("preferred_username")
            or claims.get("sub"))


async def _role_without_bootstrap(room_id: str, orcid: Optional[str],
                                  request: Request):
    """The caller's role in a room, READING the grants and claiming none.

    `ws.authorize` also performs the owner bootstrap — the first authenticated
    arrival in an unowned room becomes its owner — which is right when somebody
    opens a room they made and wrong as an authorisation check: it turns a room
    name typed into a URL into a role. This is the read-only half: the ACL as
    stored, plus the owner the STUDY declares, and nothing written back.
    """
    from . import access

    acl = load_acl(room_id)
    room = await rooms().get(room_id)
    if acl.owner is None:
        declared = access.owner_from_document(room.document)
        if declared:
            acl = Acl(owner=declared, members=acl.as_dict().get("members"),
                      groups=acl.as_dict().get("groups"))
    return access.role_of(acl, orcid, room.visibility, embargo=room.embargo,
                          groups_of=groups().expander())


def _rooms_holding(digest: str) -> List[str]:
    """Which rooms might have something to say about this digest.

    A IIIF request carries no room — the identifier is the content itself — so
    the question is asked of every room this instance holds. That is honest for
    a single-instance deployment and it is stated as a limit for a sharded one:
    a room owned by another replica cannot be consulted from here, and the
    remedy is the `?room=` the proxy can be told to pass.

    Live rooms first, then whatever the store has: a room that is open is a room
    somebody is working in, and its document is the most recent.
    """
    live = rooms().rooms()
    store = snapshot_store()
    stored = []
    try:
        stored = [r for r in (store.rooms() if hasattr(store, "rooms") else [])
                  if r not in live]
    except Exception:  # pragma: no cover — a store that cannot list is not fatal
        stored = []
    candidates = list(live) + stored
    # …and the INDEX narrows it. Only the rooms that mention this digest are
    # worth reading; the rest cannot have anything to say about it. A room the
    # index has not read is kept in the list — "not read" must never pass for
    # "says nothing", which is the difference between an index and a hole.
    registry = rooms()
    return DIGEST_INDEX.rooms_for(
        digest, [(room_id, registry.peek(room_id)) for room_id in candidates])


# ── IIIF: the manifest of a room's image or document ──────────────────────────

# ── URL topology · one house for internal↔public ─────────────────────────────
#
# Every service→service URL in this deployment has TWO forms, and confusing them
# fails opaquely — a 403, an empty body, a mixed-content block. The pairs are
# listed once in `docs/URL-TOPOLOGY.md`; the rule is one line:
#
#     StratiGraph Server SPEAKS on the internal form and WRITES the public form into the
#     documents it serves.
#
# `EM_IIIF_PUBLIC` is what goes into a manifest (other people's viewers fetch
# it, so it must name a host they can reach). `EM_IIIF_INTERNAL` is how this
# process reaches the same image server to read `info.json` — inside a compose
# network `localhost` is StratiGraph Server itself, so using the public form here
# measures nothing and every canvas silently gets a placeholder size.
#
# The older `EM_IIIF_BASE` / `EM_IIIF_INTERNAL_BASE` spellings are still read, in
# that order: one setting with two names and a precedence, never two settings
# that will one day disagree.
#
# And the rule has a HARD half, which is the one a request can attack:
#
#     the address StratiGraph Server DIALS comes only from the CONFIG.
#
# A request parameter may, at most, change the URL WRITTEN INTO the document it
# gets back. Never the outgoing call. The two are easy to conflate because they
# name the same service — that conflation is exactly what `?image_base=` did
# (`internal = IIIF_INTERNAL or base`, so a deployment with no IIIF configured
# dialled whatever host the caller named), and it is why this is now stated
# rather than assumed. `_measure_images` refuses an unconfigured base for the
# same reason: no address is a fact to report, not a gap for a caller to fill.
def _env_url(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value.rstrip("/")
    return ""


IIIF_PUBLIC = _env_url("EM_IIIF_PUBLIC", "EM_IIIF_BASE")
IIIF_INTERNAL = _env_url("EM_IIIF_INTERNAL", "EM_IIIF_INTERNAL_BASE") or IIIF_PUBLIC

#: Kept as the old name so nothing else in this module has to change spelling.
IIIF_BASE = IIIF_PUBLIC
IIIF_INTERNAL_BASE = IIIF_INTERNAL


# On the router WITHOUT the blanket auth dependency, and doing the check itself:
# a viewer cannot set a header, so the token has to be allowed in the query — and
# a router-level dependency refuses the request before the handler can look.
@v1_public.get("/rooms/{room_id}/iiif/{target_id}/manifest", tags=["iiif"])
async def iiif_manifest(room_id: str, target_id: str, request: Request,
                        response: Response,
                        image_base: Optional[str] = Query(default=None),
                        token: Optional[str] = Query(default=None)
                        ) -> Dict[str, Any]:
    """A IIIF Presentation 3 manifest for an image or a document in this room.

    The graph is the source and the manifest is a view of it — built by
    `s3dgraphy.api.iiif_manifest`, because that is where the logic belongs
    (rule #1). What StratiGraph Server adds is the two things a library must not do:

    * it knows the deployment's **public** Image API base;
    * it can **fetch `info.json`** to learn each image's pixel size. A library
      that made HTTP calls would be untestable and would break offline; a server
      that refused to would emit canvases with placeholder dimensions.

    **Public or restricted, per STUDY** (the tiers of D2.2 §3.4). A room whose
    document says `header.visibility: "public"` is the *dissemination* tier —
    validated work, meant to be read by anybody — so its manifest is served
    **without a token**: that is what publishing means. Anything else is
    in-progress and stays behind the token, which is also the DEFAULT, because a
    study served too openly cannot be un-served.

    What the gate actually protects is worth stating, because it is not obvious:
    the image service (Cantaloupe) has no auth of its own, but an image is
    addressed by its **sha256**, and the only place a digest comes from is the
    graph. **The manifest is the capability.** Refuse it and a restricted study's
    assets are unguessable; serve it and you have published them — which is why
    the decision lives with the study rather than in a config file.

    The token may arrive in the header (a program) or in the query (a VIEWER):
    Mirador fetches this URL itself and cannot be asked to set a header, and
    refusing the query would mean no IIIF viewer could open one of our manifests.
    """
    # …and a viewer fetching from its own origin needs CORS. Read-only, and only
    # on this route: a manifest exists to be fetched by other people's software.
    response.headers["Access-Control-Allow-Origin"] = "*"
    base = (image_base or IIIF_PUBLIC).rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail="this deployment has no IIIF image service configured: set "
                   "EM_IIIF_PUBLIC (e.g. https://host/iiif/3) or pass "
                   "?image_base=. A manifest pointing at nothing would look "
                   "like a broken image rather than a missing service.")

    room = await rooms().get(room_id)
    if not room.is_public:
        _authorise_manifest(request, token)
    graph, warnings = _room_graph(room)
    # SPEAK on the internal form, WRITE the public one into the document.
    #
    # `?image_base=` chooses what goes INTO the manifest — a caller staging a
    # different public host, for instance. It must NOT change how this process
    # dials the image server: the internal address is a property of the
    # deployment, not of the request. Getting that backwards is how a manifest
    # asked for over https ended up with placeholder canvas sizes, because the
    # server tried to reach itself through the public name.
    #
    # CONFIG ONLY — and no `or base` fallback. With one, a deployment that has
    # no IIIF configured would dial whatever host the caller put in
    # `?image_base=`: the request would be choosing where the server connects,
    # which is the thing this must never allow. Unconfigured means unmeasured;
    # the canvases then carry no size and the library says so.
    sizes = _measure_images(graph, IIIF_INTERNAL) if IIIF_INTERNAL else {}
    try:
        # The whole DOCUMENT goes in, not just the graph: the rights of an image
        # may be stated on a DTC chunk in another section of the container, and a
        # manifest that read only the active graph would publish an embargoed
        # picture because the embargo was written next door.
        manifest = em.iiif_manifest(graph, target_id, image_base=base,
                                    manifest_id=str(request.url).split("?")[0],
                                    sizes=sizes, document=room.document)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if warnings:
        manifest.setdefault("em:warnings", []).extend(warnings)
    return manifest


def _authorise_manifest(request: Request, token: Optional[str]) -> None:
    """Bearer header, or `?token=` — the manifest is read by viewers."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") or not token:
        authenticator.require_token(request)
        return
    if not authenticator.settings.enforcing:
        return
    try:
        authenticator.verify(token.strip())
    except HTTPException:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise HTTPException(status_code=401,
                            detail=f"token refused: {exc}") from None


def _room_graph(room: Any):
    """The room's ACTIVE graph, loaded through the library's own reader."""
    document = room.document
    graphs = document.get("graphs") or {}
    graph_id = document.get("active_graph_id") or next(iter(graphs), None)
    if not graph_id:
        raise HTTPException(status_code=404,
                            detail=f"room {room.room_id!r} holds no graph yet")
    graph, warnings = em.load_emjson({"header": document.get("header", {}),
                                      "graph": graphs[graph_id]})
    return graph, list(warnings)


def _measure_images(graph: Any, base: str) -> Dict[str, Any]:
    """Ask the image server how big each image actually is.

    One `info.json` per image, and a failure is not fatal: an image the service
    cannot answer for gets no entry, and the library then says so in the
    manifest rather than pretending. A missing size costs an aspect ratio; a
    failed request must not cost the whole manifest.

    `base` is the INTERNAL address and comes from the config — the guard below
    is the second lock on the same door as the caller's: this function must not
    become the place where a request-supplied host gets dialled just because
    somebody passed it in one day.
    """
    import json as _json
    import urllib.error
    import urllib.request

    from s3dgraphy.iiif import image_identifier, is_image

    if not base:
        return {}
    sizes: Dict[str, Any] = {}
    for node in graph.nodes:
        if getattr(node, "node_type", "") != "resource" or not is_image(node):
            continue
        identifier = image_identifier(node)
        if not identifier:
            continue
        try:
            with urllib.request.urlopen(f"{base}/{identifier}/info.json",
                                        timeout=4) as answer:
                info = _json.loads(answer.read())
            sizes[node.node_id] = (int(info["width"]), int(info["height"]))
        except (urllib.error.URLError, KeyError, ValueError, OSError):
            continue
    return sizes


# ── photogrammetry: a cluster of photographs becomes a placed model ───────────
#
# The connector's HTTP face. Everything it DECIDES is s3Dgraphy's (the two modes,
# the refusals, the shape of the provenance); what is here is the staging, the
# out-of-band run and the landing — see `app/photogrammetry.py`.
#
# Two endpoints and no more: start a job, poll it. A reconstruction is minutes,
# so a synchronous answer would be a request something drops halfway.


class GCPPoint(BaseModel):
    """One ground control point, as a client states it."""

    id: str
    #: [x, y, z] in the set's CRS
    world: List[float]
    #: where it was SEEN: [{"image": "IMG_0042.JPG", "pixel": [x, y]}, …]. The
    #: image names are the resources' names in the room — that is how a pixel
    #: finds its photograph.
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    uncertainty: Optional[float] = None


class GCPSetIn(BaseModel):
    points: List[GCPPoint] = Field(default_factory=list)
    #: `EPSG:32633`, a proj string, or null for a site-local grid. Absolute mode
    #: REFUSES null: registration into an unnamed frame is not absolute.
    crs: Optional[str] = None
    id: Optional[str] = None
    name: str = "Ground control points"


class PhotogrammetryIn(BaseModel):
    room_id: str
    #: the cluster: an acquisition (crmdig:D12), a single resource, or a unit
    #: with photographs on it. All three are real records, so all three are read.
    cluster: str
    #: what is being reconstructed — usually the stratigraphic unit. The model is
    #: linked to it so the assembly does not have to guess.
    subject: Optional[str] = None
    #: `local` (scaled and oriented in a site frame — honestly NOT
    #: georeferenced) or `absolute` (registered against control points).
    mode: str = "local"
    gcps: Optional[GCPSetIn] = None
    #: passed to the engine verbatim, over the connector's own defaults
    options: Dict[str, Any] = Field(default_factory=dict)


#: What this connector calls itself ON THE WIRE. A client filters by `source`,
#: so «where did this operation come from» must be a word and not a guess — and
#: one word, named here, rather than a literal at the call site.
CONNECTOR_SOURCE = "photogrammetry"
#: How long the thread waits for the event loop to apply, keep and announce. A
#: delta is a few dozen operations plus one snapshot write, so a minute is
#: generous — and BOUNDED, which is the point: a thread blocked forever on a loop
#: that died is a job that never reports either way.
APPLY_DEADLINE = 60.0


class PhotogrammetryJob(BaseModel):
    job_id: str
    room_id: str
    status: str
    mode: str
    author: Optional[str] = None
    created_at: float
    finished_at: Optional[float] = None
    #: the ENGINE's id for the run. The one durable handle: this process's job
    #: registry is in memory, so after a restart this is how a run is found.
    task_uuid: Optional[str] = None
    progress: float = 0.0
    image_count: int = 0
    detail: str = ""
    error: Optional[str] = None
    result: Dict[str, Any] = Field(default_factory=dict)


@v1.post("/photogrammetry", response_model=PhotogrammetryJob,
         status_code=202, tags=["photogrammetry"])
async def start_photogrammetry(request: Request,
                               ask: PhotogrammetryIn = Body(...)
                               ) -> PhotogrammetryJob:
    """Reconstruct a cluster of photographs into a placed 3D model.

    **Who may**: editor and above in the room. A reconstruction WRITES — a
    resource, a genesis event, a placement — so it is gated exactly as any other
    write is, and by the same resolution the WebSocket door uses.

    **202, with a job id.** The engine takes minutes on a small unit and longer
    on a large one; poll `GET /v1/photogrammetry/{job_id}`. What comes back when
    it is done is not a file to download but the IDS of what appeared in the
    graph — the bytes are in the room's store, behind the same gate every other
    asset is behind.

    Refused before a job id is handed out: an unknown mode, `absolute` without
    control points or without a CRS, control points in `local` mode, a cluster
    this node cannot fully stage. Refused after, and reported on the job: the
    engine being down, a run that fails, an archive with no model in it.
    """
    from . import photogrammetry as pg

    if not pg.configured():
        raise HTTPException(
            status_code=503,
            detail="this node has no photogrammetric engine: set NODEODM_URL "
                   "to the engine's address (the dev stack runs one as `nodeodm`)")
    _acl, room, role, who = await _acting_role(ask.room_id, request)
    if role is None or not role.can_write:
        raise HTTPException(
            status_code=403,
            detail="building a model in this room needs editor or above: it "
                   "writes a resource, a genesis event and a placement")
    if who is None and authenticator.settings.enforcing:
        # belt and braces: the contract's core refuses an unattributed write, and
        # a 500 from inside a thread is a worse place to learn it
        raise HTTPException(status_code=403,
                            detail="a model with nobody's name on it is a record "
                                   "nobody can defend: sign in")

    job = pg.JOBS.new(ask.room_id, author=who, mode=ask.mode)

    # THE LOOP, TAKEN HERE, while we are still on it. The applier below runs on a
    # daemon thread, and a thread cannot find the running loop by asking (there
    # is none on that thread) — so it is captured at the moment the job starts,
    # which is the moment this endpoint is on the loop.
    loop = asyncio.get_running_loop()

    def _apply(the_job: "pg.Job", run: Dict[str, Any]) -> None:
        """The thread's half: turn the delta into operations and HAND THEM OVER.
        
        It does NOT touch the room. `room.lock` is an `asyncio.Lock` and this is a
        thread, so a lock taken here excludes nobody — and `em.apply_op` and
        `em.compact` walk the same nested structures the event loop is mutating
        for whoever is editing. Measured consequence, before this: the ops landed
        in memory, the store held nothing, nobody in the room was told, and the
        job said `done` with a count that confirmed it.
        """
        ops = pg.delta_to_ops(run.get("delta") or {})
        the_job.result["ops"] = len(ops)
        if not ops:
            the_job.result["ops_applied"] = 0
            return
        # …and WAITS for the loop, because the job's status has to mean something:
        # `done` after a save that never happened is the failure this repair is
        # about. A timeout or an exception propagates to `photogrammetry.py`,
        # which already reports which half landed.
        future = asyncio.run_coroutine_threadsafe(
            _ws.apply_from_connector(room, ops, source=CONNECTOR_SOURCE,
                                     author=the_job.author),
            loop)
        outcome = future.result(timeout=APPLY_DEADLINE)
        the_job.result["ops_applied"] = outcome["applied"]
        if outcome.get("refused"):
            # NAMED, not swallowed: an idempotent re-run is not a fault, and a
            # refusal that is one has to be readable on the job.
            the_job.result["refused"] = outcome["refused"]
        if outcome.get("kept"):
            the_job.result["kept_at"] = (outcome["kept"] or {}).get("at")

    try:
        pg.start(job, document=room.document, cluster_ref=ask.cluster,
                 subject_id=ask.subject,
                 gcps_payload=(ask.gcps.model_dump() if ask.gcps else None),
                 fetch=ASSET_STORE.get,
                 publish=ASSET_STORE.put,
                 apply_delta=_apply,
                 options=dict(ask.options or {}))
    except pg.StagingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:            # a control point that controls nothing
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return PhotogrammetryJob(**job.as_dict())


@v1.get("/photogrammetry/{job_id}", response_model=PhotogrammetryJob,
        tags=["photogrammetry"])
async def photogrammetry_job(job_id: str, request: Request) -> PhotogrammetryJob:
    """Where a reconstruction is now.

    Gated by the ROOM the job is in, not by who started it: a run is a fact
    about the study, and an editor who arrives after their colleague went home
    needs to know whether the model is coming.

    A job this process has forgotten (a restart) is a 404 — and the run may
    still be alive on the engine. That is the declared limit of an in-memory
    registry, and the `task_uuid` on the record is what survives it.
    """
    from . import photogrammetry as pg

    from . import access

    job = pg.JOBS.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"no job {job_id} on this node. A restart forgets jobs; the "
                   f"run itself may still be on the engine.")
    _acl, _room, role, _who = await _acting_role(job.room_id, request)
    if role is None:
        raise HTTPException(status_code=access.http_refusal_code(_who),
                            detail="not your room")
    return PhotogrammetryJob(**job.as_dict())


# ── validate ──────────────────────────────────────────────────────────────────

@v1.post("/validate", tags=["graph"])
def validate(doc: Dict[str, Any] = Body(..., description="an em.json document")
             ) -> Dict[str, Any]:
    """Header/format conformance plus stats — `api.validate`.

    The load warnings travel in the RESPONSE rather than to a log: they are about
    the caller's document, and a service that keeps them to itself makes the client
    guess why a graph behaves oddly.
    """
    graph, warnings = _load(doc)
    report = em.validate(graph)
    return {"ok": True, "report": report, "warnings": list(warnings)}


# ── RDF / CIDOC projection ────────────────────────────────────────────────────

@v1.post("/export-ttl", tags=["graph"],
         responses={200: {"content": {"text/turtle": {}}}})
def export_ttl(doc: Dict[str, Any] = Body(..., description="an em.json document"),
               base_uri: Optional[str] = Query(None)) -> Response:
    """em.json → Turtle (`api.project_ttl`), as `text/turtle`.

    Same media type and same `Content-Disposition` as em-bridge, so a browser that
    downloads from the sidecar downloads identically from here. 501 without rdflib.
    """
    graph, _warnings = _load(doc)
    try:
        ttl = em.project_ttl(graph, base_uri=base_uri)
    except em.MissingDependency as exc:
        raise _missing_dependency(exc, "TTL export", "rdflib") from exc
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"TTL export failed: {exc}") from exc
    graph_id = (doc.get("graph") or {}).get("graph_id") or "graph"
    filename = f"{graph_id}.ttl".replace("/", "_")
    return Response(
        content=ttl,
        media_type="text/turtle",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── coordinate reprojection ───────────────────────────────────────────────────

class ReprojectRequest(BaseModel):
    """One point or many, exactly as em-bridge accepts them."""
    x: Optional[float] = None
    y: Optional[float] = None
    points: Optional[List[List[float]]] = None
    epsg_source: int
    epsg_target: int = 4326


@v1.post("/reproject", tags=["geo"])
def reproject(req: ReprojectRequest) -> Dict[str, Any]:
    """EPSG → EPSG via `api.reproject_many` (pyproj).

    The batch form builds ONE transformer, which is why a footprint should be sent
    as `points` rather than as five requests. 501 without pyproj — and the caller
    is then expected to refuse honestly rather than guess, which is what EMStudio's
    map does.
    """
    if req.points is not None:
        if not req.points:
            raise HTTPException(status_code=400,
                               detail="'points' must be a non-empty list of [x, y]")
        if len(req.points) > 512:
            # A footprint is a handful of corners. The cap stops this becoming a
            # projection service by accident.
            raise HTTPException(status_code=400,
                                detail="at most 512 points per request")
        try:
            pts = [(float(p[0]), float(p[1])) for p in req.points]
        except (TypeError, ValueError, IndexError) as exc:
            raise HTTPException(
                status_code=400,
                detail="each point must be an [x, y] pair of numbers") from exc
    elif req.x is None or req.y is None:
        raise HTTPException(status_code=400,
                            detail="send {x, y} or {points: [[x, y], …]}")
    else:
        pts = [(req.x, req.y)]

    try:
        out = em.reproject_many(pts, req.epsg_source, req.epsg_target)
    except em.MissingDependency as exc:
        raise _missing_dependency(exc, "reprojection", "pyproj (the [geo] extra)") from exc
    except ValueError as exc:
        # An unknown EPSG, or a point outside the frame's domain: the client asked
        # something impossible and should hear which.
        raise HTTPException(status_code=400, detail=f"reproject failed: {exc}") from exc

    payload: Dict[str, Any] = {
        "ok": True,
        "epsg_source": req.epsg_source,
        "epsg_target": req.epsg_target,
        "points": [[x, y] for x, y in out],
    }
    if req.points is None:
        x, y = out[0]
        # Named axes for the single-point form, so no caller has to remember
        # whether [0] was the longitude (it is).
        if req.epsg_target == 4326:
            payload["lon"], payload["lat"] = x, y
        else:
            payload["x"], payload["y"] = x, y
    return payload


# ── authority resolution ──────────────────────────────────────────────────────

def _resolve_authority(term: str, facet: str) -> Dict[str, Any]:
    facets = em.authority_facets()
    if not facets:
        raise HTTPException(status_code=501,
                            detail="authority resolver unavailable in this build")
    if (facet or "").upper() not in facets:
        raise HTTPException(
            status_code=400,
            detail=f"unknown facet {facet!r}; expected one of {sorted(facets)}")
    return {"ok": True, "term": term, "facet": facet.upper(),
            "candidates": em.resolve_authority(term, facet)}


@v1.get("/resolve-authority", tags=["authority"])
def resolve_authority_get(term: str = Query(...), facet: str = Query(...)
                          ) -> Dict[str, Any]:
    """Ranked offline authority candidates. GET, for a link or a curl."""
    return _resolve_authority(term, facet)


class AuthorityRequest(BaseModel):
    term: str
    facet: str


@v1.post("/resolve-authority", tags=["authority"])
def resolve_authority_post(req: AuthorityRequest) -> Dict[str, Any]:
    """The same op as POST — em-bridge offers both verbs and so does this."""
    return _resolve_authority(req.term, req.facet)


# ── who may work in a room (P5 · access control) ──────────────────────────────
#
# The relay enforces the roles; these routes are how they get handed out. They
# are deliberately small: an ACL is a map from ORCID to role, and every rule
# beyond that lives in `access.py::may_assign`, in one place, so a refusal here
# and a refusal at the door cannot disagree.
#
# Every refusal is a **403 with the reason**. "Forbidden" on its own is what
# makes people file bugs against a working access-control system.

class MemberIn(BaseModel):
    role: str = Field(description="viewer · editor · admin · owner")


class MemberOut(BaseModel):
    orcid: str
    role: str


class GroupGrantOut(BaseModel):
    """A GROUP's grant in this room's ACL — the other half of the access list.

    Half an ACL shown as if it were the whole one is the failure that looks like
    a success: somebody looks at the panel, does not see the team, and adds six
    people by hand who already had the role.
    """
    group_id: str
    role: str
    #: the group's human name, from the registry — absent when the registry has
    #: no such group any more, which is a fact worth showing rather than hiding:
    #: a grant to a group nobody can enumerate grants nothing to anybody.
    name: Optional[str] = None
    #: how many ORCIDs the registry has in it. `None` when the group is unknown.
    members: Optional[int] = None


class Members(BaseModel):
    room: str
    owner: Optional[str] = None
    members: List[MemberOut] = Field(default_factory=list)
    #: …AND THE GROUPS, because `role_for` reads both and a panel that showed
    #: only one would be describing an access list that does not exist.
    groups: List[GroupGrantOut] = Field(default_factory=list)
    #: what the CALLER may do here — so a UI can draw the right buttons without
    #: a second request, and without guessing from the absence of an error
    your_role: Optional[str] = None


def _members_view(room_id: str, acl: Acl, role: Optional[Role]) -> Members:
    """The access list as one answer — people AND groups.

    ONE derivation, and it is the point of the helper rather than tidiness: this
    shape was built inline in four places (the GET, the two member writes, and
    now the group writes). Adding groups to four sites is how one of them keeps
    answering with half the ACL — and a panel reading that site would show a room
    with no team in it while the team is working in it.
    """
    known = groups().all()
    grants = []
    for gid, granted in sorted(acl.groups.items()):
        group = known.get(gid)
        grants.append(GroupGrantOut(
            group_id=gid, role=granted.value,
            name=group.name if group else None,
            members=len(group.members) if group else None))
    return Members(room=room_id, owner=acl.owner,
                   members=[MemberOut(orcid=k, role=v.value)
                            for k, v in sorted(acl.members.items())],
                   groups=grants,
                   your_role=role.value if role else None)


async def _acting_role(room_id: str, request: Request) -> tuple:
    """(acl, room, the caller's role) — or a 403 saying why not.

    Resolved exactly the way the WebSocket door resolves it (same `authorize`),
    because two implementations of "who are you here" is one more than the
    number that can stay right.
    """
    principal = authenticator.require_token(request)
    dev_mode = bool(principal.get("em_dev_mode"))
    orcid = None if dev_mode else (principal.get("orcid")
                                   or principal.get("preferred_username")
                                   or principal.get("sub"))
    room = await rooms().get(room_id)
    role = authorize(room, orcid, dev_mode=dev_mode)
    return load_acl(room_id), room, role, orcid


@v1.get("/rooms/{room_id}/members", response_model=Members, tags=["access"])
async def list_members(room_id: str, request: Request) -> Members:
    """Who may work in this room. Owner and admins only.

    Not public, and not for editors either: a membership list is a list of the
    people working on an unpublished study, which is somebody's business rather
    than everybody's.
    """
    acl, _room, role, _who = await _acting_role(room_id, request)
    if role is None or not role.can_manage:
        raise HTTPException(status_code=403,
                            detail="reading the member list needs admin or owner")
    return _members_view(room_id, acl, role)


@v1.put("/rooms/{room_id}/members/{orcid}", response_model=Members, tags=["access"])
async def set_member(room_id: str, orcid: str, body: MemberIn,
                     request: Request) -> Members:
    """Give somebody a role — or move the room to a new owner.

    `owner` is a **transfer**, not a second owner: a room has one, the previous
    owner is left as an admin (losing the room should not lock the person who
    built it out of it), and only the owner may do it.
    """
    acl, room, role, _who = await _acting_role(room_id, request)
    wanted = parse_role(body.role)
    if wanted is None:
        raise HTTPException(status_code=400,
                            detail=f"unknown role {body.role!r}: "
                                   f"expected viewer, editor, admin or owner")
    target = orcid.strip()
    current = acl.members.get(target) or (Role.OWNER if acl.owner == target else None)
    refusal = may_assign(role, current, wanted)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)

    if wanted is Role.OWNER:
        previous = acl.owner
        acl.owner = target
        acl.members.pop(target, None)
        if previous and previous != target:
            acl.members[previous] = Role.ADMIN
        # the study carries its owner too (that is where an owner belongs), so
        # the header follows the ACL rather than drifting behind it
        header = room.document.setdefault("header", {})
        if isinstance(header, dict):
            header["owner"] = target
    else:
        if acl.owner == target:
            raise HTTPException(
                status_code=403,
                detail="the owner cannot be demoted; transfer the room first")
        acl.members[target] = wanted
    save_acl(room_id, acl)
    return _members_view(room_id, acl, role)


# ── GIVING A ROLE TO A TEAM ───────────────────────────────────────────────────
#
# The half-built collective, measured: `Acl` holds `groups: {name → role}`,
# `role_for` reads them through the `groups_of` seam, the registry `/v1/groups`
# names teams and their members — and NOTHING wrote a group into a room's ACL.
# The comment above the registry says «the grant stays in each room's ACL» and
# nothing put it there. You could name a team; you could not give it a role.
#
# Not blocking — six people are added by hand — but it is why twelve does not
# scale, and the ACL was already waiting for it.
class GroupGrantIn(BaseModel):
    role: str = Field(description="viewer, editor or admin — never owner")


def _group_grant_refusal(wanted: Optional[Role]) -> Optional[str]:
    """Why a group may not hold this role, or None.

    A GROUP CANNOT BE OWNER, and it is not a preference. `role_for` returns the
    STRONGEST grant, so a group holding `owner` would make every one of its
    members an owner — while `acl.owner` (and `header.owner`, which follows it)
    goes on naming one person or nobody. The room would have an «owner» field
    that lies, and the transfer (`PUT …/members/{orcid}` with `owner`, which is a
    transfer because «a room has one») would not know whom to take it from.
    """
    if wanted is Role.OWNER:
        return ("a group cannot be the owner: a room has exactly one, and the "
                "transfer takes it from that person and gives it to another. A "
                "group holding `owner` would make every member an owner while "
                "`acl.owner` still named one person — a room whose owner field "
                "lies. Give the group `admin` if the team should manage the "
                "room, and transfer the room to a PERSON.")
    return None


@v1.put("/rooms/{room_id}/groups/{group_id}", response_model=Members,
        tags=["access"])
async def set_room_group(room_id: str, group_id: str, body: GroupGrantIn,
                         request: Request) -> Members:
    """Give a team a role in this room.

    THE SAME POLICY AS A PERSON, through the same function. `may_assign` takes
    (actor, the target's current role, the new role) and its rules apply here
    unchanged — the «current role» is what the group holds in this ACL today. A
    second rule written by hand would be a second access policy, which is the one
    that diverges on a Tuesday.
    """
    acl, _room, role, _who = await _acting_role(room_id, request)
    wanted = parse_role(body.role)
    if wanted is None:
        raise HTTPException(status_code=400,
                            detail=f"unknown role {body.role!r}: "
                                   f"expected viewer, editor or admin")
    refusal = _group_grant_refusal(wanted)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    target = group_id.strip()
    if not target:
        raise HTTPException(status_code=400, detail="a group id is not empty")
    # A GRANT TO A GROUP NOBODY CAN ENUMERATE GRANTS NOTHING, and saying so at
    # the moment of writing beats a panel that shows a team with no members and
    # a room where nobody arrived. The registry is readable by any authenticated
    # caller (a team's name is not a secret), so this is not a permission leak.
    if groups().get(target) is None:
        raise HTTPException(
            status_code=404,
            detail=(f"there is no group {target!r} on this node, so the grant "
                    f"would resolve to nobody. Create it first (PUT /v1/groups/"
                    f"{target}) — or check the spelling against GET /v1/groups."))

    current = acl.groups.get(target)
    refusal = may_assign(role, current, wanted)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    acl.groups[target] = wanted
    save_acl(room_id, acl)
    return _members_view(room_id, acl, role)


@v1.delete("/rooms/{room_id}/groups/{group_id}", response_model=Members,
           tags=["access"])
async def remove_room_group(room_id: str, group_id: str,
                            request: Request) -> Members:
    """Take a team's role away. Removing a grant that is not there is not an
    error — the requested state (this team has no role here) is what results,
    which is the same reasoning `remove_member` already follows.

    A group whose registry entry is GONE can still be revoked: the grant is in
    the ACL, and refusing to clean up an ACL because the registry moved on would
    leave a room with a grant nobody can remove.
    """
    acl, _room, role, _who = await _acting_role(room_id, request)
    target = group_id.strip()
    refusal = may_assign(role, acl.groups.get(target), None)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    acl.groups.pop(target, None)
    save_acl(room_id, acl)
    return _members_view(room_id, acl, role)


@v1.delete("/rooms/{room_id}/members/{orcid}", response_model=Members,
           tags=["access"])
async def remove_member(room_id: str, orcid: str, request: Request) -> Members:
    """Revoke a role. Removing somebody who has none is not an error — the
    requested state (this person has no grant here) is the state that results."""
    acl, _room, role, _who = await _acting_role(room_id, request)
    target = orcid.strip()
    if acl.owner == target:
        raise HTTPException(status_code=403,
                            detail="the owner cannot be removed; transfer the "
                                   "room to somebody else first")
    refusal = may_assign(role, acl.members.get(target), None)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    acl.members.pop(target, None)
    save_acl(room_id, acl)
    return _members_view(room_id, acl, role)


# ── the room register: a room is a PLACE, and a place can be listed ──────────
#
# A room used to exist only as a side effect: somebody connected, and a container
# with that name was found or invented. You could not name one, list one, or
# invite anybody to one. The durable record (`rooms.RoomDescriptor`) fixes that
# and stays THIN — `{room_id, title, container_refs, created_by, created_at}` —
# with two consequences visible in the API below:
#
# * `members` in a response is **projected from the ACL**, never stored in the
#   record. One membership truth, and it is `access.py`'s;
# * `container_refs` is a LIST, because a room is a workspace that references
#   1..N containers, while a *study* is the published unit the Catalog cites. A
#   room with one reference behaves exactly as it always did.


class RoomRefIn(BaseModel):
    room_id: str = Field(description="the id this room is reachable by")
    title: str = Field(default="", description="what people call it")
    container_refs: List[str] = Field(
        default_factory=list,
        description="the em.json containers it works on (default: the room id)")


class RoomOut(BaseModel):
    room_id: str
    title: str
    container_refs: List[str]
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    archived_at: Optional[str] = None
    #: True when nobody declared this room: it predates the register and is
    #: described from its name alone. Said out loud rather than hidden, because
    #: "this room has no record" is a real state during a migration.
    implicit: bool = False
    #: PROJECTED from the ACL. Not stored here — see the note above.
    owner: Optional[str] = None
    members: List[MemberOut] = Field(default_factory=list)
    #: what the CALLER may do here, so a UI draws the right buttons without a
    #: second request
    your_role: Optional[str] = None
    #: containers this room points at that the snapshot store does not have.
    #: REPORTED, never raised: a workspace whose container was moved still exists,
    #: and the honest answer is the name of what is missing.
    missing_refs: List[str] = Field(default_factory=list)


def _describe_room(descriptor: RoomDescriptor, *, role: Optional[Role] = None,
                   with_members: bool = True) -> RoomOut:
    """One room, described. The ACL is read HERE and projected — the record has
    no idea who the members are, which is what keeps it from disagreeing."""
    acl = load_acl(descriptor.room_id)
    members: List[MemberOut] = []
    if with_members:
        members = [MemberOut(orcid=k, role=v.value)
                   for k, v in sorted(acl.members.items())]
    return RoomOut(
        room_id=descriptor.room_id, title=descriptor.title,
        container_refs=list(descriptor.container_refs),
        created_by=descriptor.created_by, created_at=descriptor.created_at,
        archived_at=descriptor.archived_at, implicit=descriptor.implicit,
        owner=acl.owner, members=members,
        your_role=getattr(role, "value", None),
        missing_refs=rooms().missing_refs(descriptor))


def _caller_identity(request: Request) -> tuple:
    """`(orcid, dev_mode)` from the token — the same three-line resolution
    `_acting_role` uses, factored out for the routes that must NOT materialise a
    live room just to answer a question about the register.

    NOT called `_caller`: this module already has one of those (further down, the
    corpus's), which returns just the ORCID. Two functions with one name is a
    collision the interpreter resolves silently and in whichever order the file
    happens to be written — caught here before it shipped, by reading the corpus
    endpoint rather than by a failing test.
    """
    principal = authenticator.require_token(request)
    dev_mode = bool(principal.get("em_dev_mode"))
    orcid = None if dev_mode else (principal.get("orcid")
                                   or principal.get("preferred_username")
                                   or principal.get("sub"))
    return orcid, dev_mode


@v1.get("/rooms", response_model=List[RoomOut], tags=["rooms"])
async def list_rooms(request: Request) -> List[RoomOut]:
    """The rooms this caller has a grant in.

    **A listing is not a discovery service.** What comes back is what the ACL
    grants — owner, member, or a group they are in — and NOT every public room on
    the instance: a public study is readable by anybody who has its name, which is
    a different statement from "here is everything this server holds". In dev mode
    (no OIDC, no identities) everything is listed, for the same reason `authorize`
    makes dev mode owner: there is nobody to distinguish.

    Declared rooms only. A room that exists as a snapshot and was never declared
    is reachable by name and absent from here — inventing entries for it would
    make the register a guess about what somebody meant.
    """
    orcid, dev_mode = _caller_identity(request)
    expander = groups().expander()
    out: List[RoomOut] = []
    for descriptor in rooms().declared():
        if dev_mode:
            role = Role.OWNER
        else:
            role = load_acl(descriptor.room_id).role_for(orcid, groups_of=expander)
            if role is None:
                continue
        # the member list is for admins; a listing shows the room, not the team
        out.append(_describe_room(descriptor, role=role,
                                  with_members=bool(role and role.can_manage)))
    return out


@v1.post("/rooms", response_model=RoomOut, status_code=201, tags=["rooms"])
async def create_room(body: RoomRefIn, request: Request) -> RoomOut:
    """Declare a room. It exists from here: empty, listable, nobody connected.

    Whoever creates it is its **owner**, written into the ACL — the same place
    every other grant lives. An identity is required for exactly that reason: a
    room owned by nobody is a room nobody can hand out access to (which is the
    hole `access.claim_owner` exists to patch for rooms that predate this).
    """
    orcid, dev_mode = _caller_identity(request)
    room_id = body.room_id.strip()
    if not room_id or any(c in room_id for c in "/\\ "):
        raise HTTPException(status_code=400,
                            detail="a room id is one path segment, no spaces")
    if not rooms().descriptor(room_id).implicit:
        raise HTTPException(status_code=409,
                            detail=f"room {room_id!r} is already declared")
    if not orcid and not dev_mode:
        raise HTTPException(
            status_code=401,
            detail="creating a room needs an identity: its creator is its owner")

    # ── UNA STANZA, UN GRAFO VIVO ─────────────────────────────────────────────
    #
    # The design note's rule that does not negotiate, and nothing enforced it
    # until this was measured on 6 September 2026: a second room pointing at the
    # first's container was accepted, opened the first's graph, took its own
    # operations, and saved — leaving two files each declaring to hold the same
    # graph, divergent, with nothing saying which one it is. A fork that never
    # declared itself, and the note says a fork is legal precisely WHEN it says so.
    #
    # Refused, and the sentence names the room to enter instead: the point of the
    # rule is that the live copy has one address, so being told that address is
    # the whole remedy. (`primary_ref` is what becomes the live document; a second
    # reference the session does not edit is not a second table.)
    refs = [str(r) for r in (body.container_refs or [room_id]) if str(r)]
    primary = refs[0] if refs else room_id
    taken = rooms().room_already_on(primary, except_room=room_id)
    if taken:
        raise HTTPException(
            status_code=409,
            detail=(f"the graph {primary!r} is already the live copy of the room "
                    f"{taken!r} — one room, one live graph. Enter {taken!r} to "
                    f"work on it, or create a room of your own with its own "
                    f"container."))

    descriptor = rooms().create(room_id, title=body.title,
                               container_refs=body.container_refs or None,
                               created_by=orcid)
    if orcid:
        acl = load_acl(room_id)
        if not acl.owner:
            acl.owner = orcid
            save_acl(room_id, acl)
    return _describe_room(descriptor, role=Role.OWNER)


@v1.get("/rooms/{room_id}", response_model=RoomOut, tags=["rooms"])
async def get_room(room_id: str, request: Request) -> RoomOut:
    """One room's record. Needs a role — the same door as everything else.

    Note what this does NOT do: it does not open the room. A record can be read
    without materialising a working copy, which is what makes the register usable
    from a list view.
    """
    _acl, _room, role, _who = await _acting_role(room_id, request)
    if role is None:
        raise HTTPException(status_code=403, detail="not a member of this room")
    return _describe_room(rooms().descriptor(room_id), role=role,
                          with_members=bool(role.can_manage))


# ── THE REST DOOR FOR OPERATIONS ─────────────────────────────────────────────
#
# Not a new capability: `ws.apply_from_connector` has done the work since 9
# September, and the comment above it says why it exists — «a connector written
# next month gets the lock, the save and the announcement by calling this». This
# is that connector's door, and the second caller of that function.
#
# WHY A DOOR AT ALL, when there is a socket. A socket is a POSTURE: you take a
# seat, you are in the roster, your selection is other people's business, and
# you are expected to still be there in a minute. pyarchinit-mini is not that —
# it is a CORRESPONDENT. It has a site's stratigraphy on disk and wants to hand
# it over, once, and hear what landed. Holding a WebSocket open to deliver
# twelve units would be building a posture the caller does not have, and every
# such client would have to implement the handshake, the presence and the
# reconnect to say one sentence.

#: What this door calls itself ON THE WIRE. `source` names the PRODUCER of a
#: message — `em-server` for the relay's own words, `emstudio` for the editor,
#: `photogrammetry` for the reconstruction connector. Here the producer is
#: whoever posted, and this server does not know who that is beyond their
#: identity; what it does know is the door they came through. So the word names
#: the door.
#:
#: NOT `CONNECTOR_SOURCE`, which is the string `"photogrammetry"`: a client
#: filtering by source would see a stratigraphic delivery labelled as a
#: reconstruction.
#:
#: And NOT taken from the request. A caller-chosen `source` is a caller-chosen
#: provenance, which is the same class of statement as a caller-chosen `author`
#: — and that one the server already refuses.
REST_OPS_SOURCE = "rest"

#: How many operations one request may carry. MEASURED, because the constraint
#: is not the bytes: `apply_from_connector` holds `room.lock` for the WHOLE
#: batch, so a long batch is a room frozen for everybody editing in it — and
#: `room.apply` is O(n) in the graph's size. Timed on this machine, one
#: `add_node` at a time, on a graph that grows underneath:
#:
#:      graph      per op
#:        100      27 µs
#:       1000      49 µs
#:       2000      93 µs
#:       5000     208 µs
#:      10000     436 µs
#:
#: so the lock is held for roughly N × f(graph), not for N. A thousand
#: operations cost ~93 ms on a two-thousand-node study and ~2 s on a
#: ten-thousand-node one — which is the honest statement: this cap bounds one
#: factor of a product, and the other factor belongs to whoever owns the study.
#:
#: A THOUSAND is chosen to sit above the real unit of work and below the point
#: where the hold becomes visible. The unit of work is a site: pyarchinit-mini's
#: own tutorial database holds 60 stratigraphic units and 187 relationships,
#: i.e. a few hundred operations for a whole excavation, so one site is one
#: request. Anything larger is a caller that should be paging, and a 413 tells
#: them so.
OPS_BATCH_MAX = int(os.environ.get("OPS_BATCH_MAX") or "1000")


class OpsIn(BaseModel):
    """A batch of CRDT operations, and optionally which section they belong to."""

    ops: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="CRDT operations, in the wire's own shape. `author` is "
                    "ignored if present: the server writes the caller's.")
    graph_id: Optional[str] = Field(
        default=None,
        description="which graph of the container. Absent means the active one.")


class OpsOut(BaseModel):
    applied: int
    #: Named individually, because a caller has to be able to tell an operation
    #: that was refused for being stale from one that was refused for being
    #: malformed — the first is normal, the second is a bug at their end.
    refused: List[Dict[str, Any]] = Field(default_factory=list)
    #: What the snapshot store kept, or null when nothing applied and so nothing
    #: was written.
    kept: Optional[Dict[str, Any]] = None


@v1.post("/rooms/{room_id}/ops", response_model=OpsOut, tags=["rooms"])
async def apply_ops(room_id: str, body: OpsIn, request: Request) -> OpsOut:
    """Hand a batch of graph operations to a room, over HTTP.

    **Who may**: editor and above, resolved by the same `_acting_role` the
    WebSocket door and the photogrammetry door use — because two
    implementations of «who are you here» is one more than the number that can
    stay right.

    **The author is the caller's identity, always.** An `author` in the payload
    is dropped (`apply_from_connector` pops it): an author nobody verified is
    not an author.

    **A REFUSAL IS NOT AN ERROR.** An operation that is stale, or that has
    already been applied, comes back inside `refused` on a **200**. This is the
    one design decision in this endpoint worth arguing, so here is the argument:
    a 4xx tells a client to change something and try again, and a CRDT client
    that retries a refused operation will be refused again for ever — the
    refusal IS the convergent answer. The status code answers «did the room
    hear you», and the body answers «what did it do about it».

    **413 above `OPS_BATCH_MAX`**, and the constant carries the measurement.
    """
    _acl, room, role, who = await _acting_role(room_id, request)
    if role is None or not role.can_write:
        raise HTTPException(
            status_code=403,
            detail="writing operations into this room needs editor or above: "
                   "it changes the graph everybody else is reading")
    if who is None and authenticator.settings.enforcing:
        # The same belt and braces as the photogrammetry door, and the same
        # reason: the contract's core refuses an unattributed write, and a 500
        # from deeper in is a worse place to learn it.
        raise HTTPException(status_code=403,
                            detail="operations with nobody's name on them are a "
                                   "record nobody can defend: sign in")

    if not body.ops:
        # Nothing to do, and nothing wrong with asking. Not a 400: an adapter
        # that produced no operations because a site has no units has behaved
        # correctly, and a client should not have to special-case the empty
        # delivery.
        return OpsOut(applied=0, refused=[], kept=None)
    if len(body.ops) > OPS_BATCH_MAX:
        raise HTTPException(
            status_code=413,
            detail=f"{len(body.ops)} operations in one request, and this node "
                   f"accepts {OPS_BATCH_MAX}. The limit is the room's lock: the "
                   f"batch is applied under it, so a long batch is a room frozen "
                   f"for everybody editing in it. Send it in parts — the "
                   f"operations are idempotent, so parts are safe.")

    outcome = await _ws.apply_from_connector(
        room, body.ops, source=REST_OPS_SOURCE,
        graph_id=body.graph_id, author=who)
    return OpsOut(**outcome)


class ArchiveIn(BaseModel):
    archived: bool = Field(default=True,
                           description="false brings the room back")


@v1.post("/rooms/{room_id}/archive", response_model=RoomOut, tags=["rooms"])
async def archive_room(room_id: str, body: ArchiveIn,
                       request: Request) -> RoomOut:
    """Mark a room archived, or bring it back. Owner and admin only.

    **Not a deletion, and there is no deletion.** A room whose container was
    moved, or whose dig is over for the season, is marked — it stays listed, it
    keeps its title, its creator and its references, and it says when it was
    archived. A sweep that removed such rooms would be a policy nobody wrote
    down, running on a schedule; `GET /v1/rooms` already reports what is
    dangling (`missing_refs`), and what to do about it is a person's call.
    """
    _acl, _room, role, _who = await _acting_role(room_id, request)
    if role is None or not role.can_manage:
        raise HTTPException(status_code=403,
                            detail="archiving a room needs admin or owner")
    descriptor = rooms().descriptor(room_id)
    if descriptor.implicit:
        raise HTTPException(
            status_code=404,
            detail=f"room {room_id!r} has no record to archive: it predates the "
                   f"register (declare it with POST /v1/rooms first)")
    return _describe_room(rooms().archive(room_id, archived=body.archived),
                          role=role, with_members=True)


# ── the invite link ──────────────────────────────────────────────────────────
#
# The link is the invitation, the ORCID is the identity, the ACL is the role.
# See `app/invites.py` for why each of the three does exactly one job.


class InviteIn(BaseModel):
    role: str = Field(default="viewer", description="viewer or editor")
    #: how long the link lives. 0 or null = no expiry, which a caller has to ask
    #: for rather than get by forgetting.
    ttl_seconds: Optional[int] = Field(default=None)
    max_uses: Optional[int] = Field(default=None)


class InviteOut(BaseModel):
    token_id: str
    room_id: str
    role: str
    state: str
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    expires_at: Optional[int] = None
    max_uses: Optional[int] = None
    uses: int = 0
    revoked_at: Optional[str] = None
    accepted_by: List[str] = Field(default_factory=list)
    #: the token, **only in the answer that created it**. It is not stored (the
    #: record keeps a sha256), so this is the one moment it exists to be copied.
    token: Optional[str] = None


class JoinIn(BaseModel):
    token: str


class JoinOut(BaseModel):
    room_id: str
    title: str
    role: str
    #: True when the ACL already granted this person as much or more, and the
    #: invitation therefore changed nothing. An invite never demotes.
    already_had: bool = False


def _invite_out(invite: Any, *, token: Optional[str] = None) -> InviteOut:
    return InviteOut(**invite.as_public(), token=token)


@v1.post("/rooms/{room_id}/invites", response_model=InviteOut, status_code=201,
         tags=["rooms"])
async def create_invite(room_id: str, body: InviteIn,
                        request: Request) -> InviteOut:
    """Mint a shareable link that offers a role in this room.

    Who may do it: `may_assign`, unchanged — whoever could not grant the role by
    hand cannot grant it by URL either (`invites.offerable`). And a link may only
    ever carry `viewer` or `editor`: an owner or an admin is somebody a person
    hands the room to.
    """
    _acl, _room, role, who = await _acting_role(room_id, request)
    wanted = parse_offer(body.role)
    if wanted is None:
        raise HTTPException(status_code=400,
                            detail=f"unknown role {body.role!r}: expected "
                                   f"viewer or editor")
    refusal = offerable(role, wanted)
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)
    ttl = body.ttl_seconds
    invite, token = invites().mint(
        room_id, wanted, created_by=who,
        ttl_seconds=(ttl if ttl else None) if ttl is not None else None,
        max_uses=body.max_uses)
    return _invite_out(invite, token=token)


@v1.get("/rooms/{room_id}/invites", response_model=List[InviteOut], tags=["rooms"])
async def list_invites(room_id: str, request: Request) -> List[InviteOut]:
    """Which links are out there, and in what state. Admin and owner only — a
    list of invitations is a list of who was asked to join an unpublished study.

    No secrets in the answer: the store never had them.
    """
    _acl, _room, role, _who = await _acting_role(room_id, request)
    if role is None or not role.can_manage:
        raise HTTPException(status_code=403,
                            detail="reading the invitations needs admin or owner")
    return [_invite_out(invite) for invite in invites().list(room_id)]


@v1.delete("/rooms/{room_id}/invites/{token_id}", response_model=InviteOut,
           tags=["rooms"])
async def revoke_invite(room_id: str, token_id: str,
                        request: Request) -> InviteOut:
    """Stop a link. The record stays, marked `revoked`: an invitation that was
    live is a thing that happened, and deleting the row would erase the answer."""
    _acl, _room, role, _who = await _acting_role(room_id, request)
    if role is None or not role.can_manage:
        raise HTTPException(status_code=403,
                            detail="revoking an invitation needs admin or owner")
    invite = invites().revoke(room_id, token_id)
    if invite is None:
        raise HTTPException(status_code=404,
                            detail=f"no invitation {token_id!r} in this room")
    return _invite_out(invite)


@v1.post("/join", response_model=JoinOut, tags=["rooms"])
async def join_by_link(body: JoinIn, request: Request) -> JoinOut:
    """Accept an invitation: the link says which room, the token says who invited
    you, and **the ORCID has to be yours**.

    The three refusals, and they are three different sentences on purpose:

    * **no identity** → 401. A link is not a credential; it opens a door that
      still asks who you are. (Dev mode has no identities at all, so there is
      nobody to write into an ACL — it is refused there too, which is the honest
      answer rather than granting an anonymous editor.)
    * **the link is not live** → 403 with the state: not valid, revoked, expired,
      used up. Not 404: whether a token exists is not something a caller who
      guessed one should learn.
    * **it worked** → the role is WRITTEN INTO THE ACL and the answer says what
      it is. From then on the ACL is the only thing anybody reads.

    And it never demotes: somebody who is already an editor keeps editing after
    following a viewer link. An invitation is an offer of access, not a statement
    about what somebody's access should be reduced to.
    """
    orcid, dev_mode = _caller_identity(request)
    if not orcid:
        raise HTTPException(
            status_code=401,
            detail=("an invitation opens the door; you still have to say who you "
                    "are. Sign in with ORCID and follow the link again."
                    if not dev_mode else
                    "this instance has no identities (dev mode, no OIDC): there "
                    "is nobody to add to the room's access list"))
    invite, refusal = invites().resolve(body.token)
    if invite is None:
        raise HTTPException(status_code=403, detail=refusal or "not a valid invitation")

    offered = parse_offer(invite.role) or Role.VIEWER
    acl = load_acl(invite.room_id)
    held = acl.role_for(orcid, groups_of=groups().expander())
    already = held is not None and held.rank >= offered.rank
    if not already:
        acl.members[orcid] = offered
        save_acl(invite.room_id, acl)
    invites().record_use(invite, orcid)
    descriptor = rooms().descriptor(invite.room_id)
    return JoinOut(room_id=invite.room_id, title=descriptor.title,
                   role=(held or offered).value if already else offered.value,
                   already_had=already)


# ── the NODE's scope: everything, for whoever looks after the node ───────────
#
# Two scopes on one contract, and the split is the point:
#
#   owner-scope     `/v1/rooms/…`        — my room, gated by my role IN it
#   operator-scope  `/v1/admin/…`        — every room, gated by a capability
#                                          granted OUTSIDE every room
#
# The endpoints below add no policy of their own: they read the same registry,
# the same ACLs and the same asset store the room routes read, and they call the
# same `RoomRegistry.archive`. What they add is REACH — and the price of reach is
# a capability an owner cannot give themselves (`app/operators.py`).
#
# Console-ready on purpose: plain JSON, one request per panel, because both faces
# consume it — the node console shipped in `app/node_admin/` and whatever an
# operator writes in a terminal.


def _require_operator(request: Request) -> Optional[str]:
    """The caller, if they may act on the node. 403 with the remedy otherwise."""
    principal = authenticator.require_token(request)
    if not ops.is_operator(principal):
        raise HTTPException(status_code=403, detail=ops.refusal())
    if principal.get("em_dev_mode"):
        return None
    return (principal.get("orcid") or principal.get("preferred_username")
            or principal.get("sub"))


class NodeWhoAmI(BaseModel):
    operator: bool
    orcid: Optional[str] = None
    #: how this node recognises one — so the console can say what is missing
    #: rather than showing an empty page
    capability: str = ""
    auth: str = ""


class StorageRoom(BaseModel):
    room_id: str
    #: digests this room's document points at
    assets: int = 0
    #: …and how many of those the asset store actually holds
    present: int = 0
    missing: List[str] = Field(default_factory=list)
    declared: bool = False
    archived_at: Optional[str] = None
    missing_refs: List[str] = Field(default_factory=list)


class BlockedReturn(BaseModel):
    """An archived room whose graph a live room has taken meanwhile.

    The state that WILL produce a 409 the next time somebody tries to bring the
    archived room back — and an operator should learn it from the report, not from
    somebody else's refusal. Everything needed to act is here: which room is
    asleep, which graph it still claims as its own, and who is on it now.
    """
    room_id: str
    graph: str
    taken_by: str


class StorageOut(BaseModel):
    asset_store: str
    snapshot_store: str
    room_store: str
    #: every digest the store holds that no room's document mentions. An orphan
    #: is not deleted here: it is NAMED, and what to do about it is a decision.
    orphan_assets: List[str] = Field(default_factory=list)
    #: …and the same discipline for a lifecycle knot: NAMED, never untied here.
    #: Untying it means either moving somebody's room to another container or
    #: taking a graph off a live room, and neither is a report's business.
    blocked_returns: List[BlockedReturn] = Field(default_factory=list)
    rooms: List[StorageRoom] = Field(default_factory=list)


@v1.get("/admin/whoami", response_model=NodeWhoAmI, tags=["node"])
async def node_whoami(request: Request) -> NodeWhoAmI:
    """Am I an operator? Answered WITHOUT a 403, because the console asks this
    before it draws anything: a page that shows an error where it could show
    "you are not an operator, here is who grants it" teaches people to reload."""
    principal = authenticator.require_token(request)
    is_op = ops.is_operator(principal)
    who = None if principal.get("em_dev_mode") else (
        principal.get("orcid") or principal.get("preferred_username")
        or principal.get("sub"))
    return NodeWhoAmI(operator=is_op, orcid=who, capability=ops.describe(),
                      auth=authenticator.settings.describe())


@v1.get("/admin/rooms", response_model=List[RoomOut], tags=["node"])
async def node_rooms(request: Request) -> List[RoomOut]:
    """EVERY room on this node — declared or not, empty or not, archived or not.

    The difference from `GET /v1/rooms` is only reach: an owner sees the rooms
    they have a grant in, an operator sees the node. Same shape, so a console and
    a room panel render the same object.

    Snapshot-only rooms (never declared, from before the register) are included
    with `implicit: true`: they are exactly what an operator needs to see, since
    they are the ones nobody has titled or claimed.
    """
    _require_operator(request)
    registry = rooms()
    ids = set(registry.rooms_store.ids())
    # `snapshot_store()`, never the imported name: binding it at import is the
    # footgun this module documents twice (see `rooms()` and `snapshot_store()`),
    # and I walked into it — a test that replaced the store saw an admin listing
    # that had never heard of its rooms.
    snapshots = getattr(snapshot_store(), "rooms", None)
    if callable(snapshots):
        ids |= set(snapshots())
    ids |= set(registry.rooms())          # …and whatever is live right now
    return [_describe_room(registry.descriptor(room_id), role=Role.OWNER,
                           with_members=True) for room_id in sorted(ids)]


@v1.get("/admin/storage", response_model=StorageOut, tags=["node"])
async def node_storage(request: Request) -> StorageOut:
    """What the node is holding, and what does not line up.

    Four questions an operator actually has, and none of them is answerable from
    inside one room:
    * which rooms exist and which of their containers are missing;
    * how many assets each room points at, and how many of those the store has;
    * which stored digests **no** room mentions — the orphans;
    * which archived room **cannot come back**, because a live room took its graph
      while it slept. That one is a refusal waiting to happen, and an operator
      should meet it here rather than in somebody else's 409.

    MinIO is behind StratiGraph Server here as everywhere else: this reads the store
    through the same interface the asset route uses, and hands back numbers. No
    presigned URL, no bucket listing to a browser.
    """
    _require_operator(request)
    registry = rooms()
    ids = set(registry.rooms_store.ids())
    # `snapshot_store()`, never the imported name: binding it at import is the
    # footgun this module documents twice (see `rooms()` and `snapshot_store()`),
    # and I walked into it — a test that replaced the store saw an admin listing
    # that had never heard of its rooms.
    snapshots = getattr(snapshot_store(), "rooms", None)
    if callable(snapshots):
        ids |= set(snapshots())
    ids |= set(registry.rooms())

    seen: set = set()
    out_rooms: List[StorageRoom] = []
    for room_id in sorted(ids):
        descriptor = registry.descriptor(room_id)
        document = snapshot_store().get(descriptor.primary_ref)
        digests = _digests_in(document)
        seen |= digests
        missing = [d for d in sorted(digests) if ASSET_STORE.head(d) is None]
        out_rooms.append(StorageRoom(
            room_id=room_id, assets=len(digests),
            present=len(digests) - len(missing), missing=missing,
            declared=not descriptor.implicit,
            archived_at=descriptor.archived_at,
            missing_refs=registry.missing_refs(descriptor)))

    # ── THE KNOT AN OPERATOR SHOULD SEE BEFORE IT BITES ──────────────────────
    #
    # An archived room still naming a graph that a LIVE room has taken cannot come
    # back: `RoomRegistry.archive` refuses it with a 409. That refusal is correct
    # and it arrives at the worst possible moment — when somebody is trying to
    # start a season again. So the report says it first.
    #
    # ASKED THROUGH `room_already_on`, the same derivation that does the refusing,
    # so the report and the rule cannot disagree about what a collision is. A
    # second implementation here would be a report that is right until one of the
    # two is edited.
    blocked = []
    for room_id in sorted(ids):
        descriptor = registry.descriptor(room_id)
        if not descriptor.archived_at:
            continue
        taken = registry.room_already_on(descriptor.primary_ref,
                                         except_room=room_id)
        if taken:
            blocked.append(BlockedReturn(room_id=room_id,
                                         graph=descriptor.primary_ref,
                                         taken_by=taken))

    return StorageOut(
        asset_store=asset_describe(ASSET_STORE),
        snapshot_store=snapshot_describe(snapshot_store()),
        room_store=room_describe(registry.rooms_store),
        orphan_assets=sorted(_stored_digests() - seen),
        blocked_returns=blocked,
        rooms=out_rooms)


def _digests_in(document: Optional[Dict[str, Any]]) -> set:
    """Every asset reference a container's nodes point at."""
    found: set = set()
    if not isinstance(document, dict):
        return found
    graphs = document.get("graphs")
    sections = list(graphs.values()) if isinstance(graphs, dict) else [document]
    for section in sections:
        if not isinstance(section, dict):
            continue
        for node in section.get("nodes") or []:
            data = node.get("data") if isinstance(node, dict) else None
            ref = str((data or {}).get("checksum") or "")
            if asset_ref_valid(ref):
                found.add(ref)
    return found


def _stored_digests() -> set:
    """What the asset store holds, when it can say.

    Only the stores that can enumerate answer; MinIO's client can list a bucket
    but this does not ask it to — a listing of every object on a shared bucket is
    an expensive question to answer on a page load, and an orphan report that is
    silently partial is worse than one that says so. When the store cannot
    enumerate, `orphan_assets` is empty and the console says why.
    """
    for name in ("refs", "digests", "keys"):
        lister = getattr(ASSET_STORE, name, None)
        if callable(lister):
            try:
                return {str(r) for r in lister()}
            except Exception:      # noqa: BLE001 — a store that will not list
                return set()
    data = getattr(ASSET_STORE, "_data", None)
    if isinstance(data, dict):
        return {str(k) for k in data}
    return set()


class NodeEntrance(BaseModel):
    """One face of THIS server — a path this app really mounts."""

    path: str
    label: str
    what: str
    #: the full public address, or "" when this node has no public name. Never a
    #: guess: see `entrances`.
    url: str = ""


class NodeCheck(BaseModel):
    name: str
    #: ok · degraded · unreachable · not configured — four states, because a
    #: service nobody asked for is not a failure and a service that did not
    #: answer must never read as ok
    state: str
    target: Optional[str] = None
    latency_ms: Optional[int] = None
    detail: str = ""
    facts: Dict[str, Any] = Field(default_factory=dict)
    #: where a BROWSER goes for this face, from configuration only — empty when
    #: nobody said, because a console URL nobody configured is a button that
    #: works on one laptop
    browser: Optional[str] = None
    #: the URL the probe actually asked, so an operator can put the same question
    #: to a terminal and compare. If the two answers differ, the page is lying
    #: and that is the bug worth finding
    probe: Optional[str] = None


class NodeHealthOut(BaseModel):
    verdict: str
    #: the wall-clock bound each probe ran under, so the page can say what a
    #: `unreachable` actually means here
    deadline_s: float
    checks: List[NodeCheck] = Field(default_factory=list)
    versions: Dict[str, Any] = Field(default_factory=dict)
    #: THIS server's own faces — the doors somebody restarts from. Listed beside
    #: the `mount` calls that create them (`ENTRANCES`), and held to the app's
    #: real route table by `tests/test_node_map.py`.
    entrances: List[NodeEntrance] = Field(default_factory=list)


@v1.get("/admin/health", response_model=NodeHealthOut, tags=["node"])
async def node_health_report(request: Request) -> NodeHealthOut:
    """Is this node well — the services around StratiGraph Server, not just StratiGraph Server.

    Operator-scoped on purpose, and it is a different question from `/v1/health`:
    that one is a public probe for an orchestrator ("is the process up, what can
    this build do"), this one names the things StratiGraph Server DEPENDS on and how much
    they are holding. The two answers belong to two audiences, and merging them
    would either leak an infrastructure map to anybody or hide it from the person
    who needs it.

    Every probe is bounded by a wall clock (`EM_HEALTH_DEADLINE`): a health page
    that can hang is worse than none, because it looks like it is about to answer.
    See `app/node_health.py` for why a socket timeout alone is not a bound.
    """
    from . import handoff as ho

    _require_operator(request)
    return NodeHealthOut(**node_health(
        version=__version__, s3dgraphy=_s3dgraphy_version(),
        asset_store=ASSET_STORE),
        entrances=[NodeEntrance(**face) for face in entrances(ho.public_base())])


def _s3dgraphy_version() -> Optional[str]:
    try:
        import s3dgraphy
        return getattr(s3dgraphy, "__version__", None)
    except Exception:                              # noqa: BLE001
        return None


class NodeArchiveIn(BaseModel):
    archived: bool = True
    #: the operator has to name the room again. A cross-room console with a
    #: one-click destructive-looking action on a list is how the wrong row gets
    #: clicked; the API asks for the name back, and the console asks the person.
    confirm_room_id: str = Field(default="")


@v1.post("/admin/rooms/{room_id}/archive", response_model=RoomOut, tags=["node"])
async def node_archive_room(room_id: str, body: NodeArchiveIn,
                            request: Request) -> RoomOut:
    """Archive (or restore) any room on the node — the lifecycle action.

    Same call the room's own admin makes (`RoomRegistry.archive`), and the same
    promise: **a mark, never a deletion**. There is no delete here either, and
    `GET /v1/admin/storage` is the report that tells an operator which rooms are
    dangling in the first place.

    `confirm_room_id` must match. Typed confirmation on a cross-room console is
    not ceremony: the operator is looking at a list of somebody else's rooms.
    """
    _require_operator(request)
    if body.confirm_room_id != room_id:
        raise HTTPException(
            status_code=400,
            detail=(f"confirm the room by name: send confirm_room_id={room_id!r}. "
                    f"On a console that lists other people's rooms, a single click "
                    f"is not consent."))
    registry = rooms()
    descriptor = registry.descriptor(room_id)
    if descriptor.implicit:
        # An undeclared room has no record to mark. Declaring it here would put a
        # title on somebody's study that nobody chose — so it is declared as
        # itself, which is what the implicit descriptor already says.
        registry.declare(descriptor)
    return _describe_room(registry.archive(room_id, archived=body.archived),
                          role=Role.OWNER, with_members=True)


# ── groups: a name for a set of people ────────────────────────────────────────
#
# A room grants a role to a NAME ("the excavation team") instead of to six
# ORCIDs it would then have to keep in step with reality. The registry is
# instance-wide; the grant stays in each room's ACL, where it belongs.
#
# Who may manage one: **whoever made it**. The same arrangement rooms use, and
# for the same reason — somebody has to be able to fix it, and "everybody" is
# not an answer. There is no global administrator in this service and inventing
# one here would be a bigger decision than a group list deserves.

class GroupIn(BaseModel):
    id: str = Field(description="short stable id, e.g. `scavo-2026`")
    name: str = Field(default="", description="how humans read it")
    members: List[str] = Field(default_factory=list)


class GroupOut(BaseModel):
    id: str
    name: str
    owner: Optional[str] = None
    members: List[str] = Field(default_factory=list)


def _caller(request: Request) -> Optional[str]:
    principal = authenticator.require_token(request)
    if principal.get("em_dev_mode"):
        return None            # dev mode has no identities; see `access.py`
    return (principal.get("orcid") or principal.get("preferred_username")
            or principal.get("sub"))


def _may_manage(group: Group, who: Optional[str], request: Request) -> None:
    """The owner of a group manages it. Dev mode manages everything, because dev
    mode has no identities to distinguish."""
    if not authenticator.settings.enforcing:
        return
    if group.owner and who and group.owner == who:
        return
    raise HTTPException(
        status_code=403,
        detail=f"the group {group.id!r} is managed by whoever created it "
               f"({group.owner or 'nobody recorded'})")


@v1.get("/groups", response_model=List[GroupOut], tags=["access"])
def list_groups(request: Request) -> List[GroupOut]:
    """Every group this instance knows. Readable by any authenticated caller:
    a group is a name for a team, not a secret, and you cannot be granted a role
    through one you are not allowed to see the name of."""
    authenticator.require_token(request)
    return [GroupOut(**g.as_dict()) for g in sorted(groups().all().values(),
                                                    key=lambda g: g.id)]


@v1.put("/groups/{group_id}", response_model=GroupOut, tags=["access"])
def put_group(group_id: str, body: GroupIn, request: Request) -> GroupOut:
    """Create a group, or replace the one you own.

    Idempotent: the same call twice leaves one group with those members. The
    creator becomes the owner, and only they may replace it afterwards.
    """
    who = _caller(request)
    registry = groups()
    existing = registry.get(group_id)
    if existing is not None:
        _may_manage(existing, who, request)
    group = Group(group_id, body.name or (existing.name if existing else group_id),
                  body.members, owner=(existing.owner if existing else who))
    registry.put(group)
    return GroupOut(**group.as_dict())


@v1.put("/groups/{group_id}/members/{orcid}", response_model=GroupOut,
        tags=["access"])
def add_group_member(group_id: str, orcid: str, request: Request) -> GroupOut:
    who = _caller(request)
    registry = groups()
    group = registry.get(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"no group {group_id!r}")
    _may_manage(group, who, request)
    group.add(orcid)
    registry.put(group)
    return GroupOut(**group.as_dict())


@v1.delete("/groups/{group_id}/members/{orcid}", response_model=GroupOut,
           tags=["access"])
def remove_group_member(group_id: str, orcid: str, request: Request) -> GroupOut:
    """Take somebody out. Removing who is not there is not an error — the state
    asked for is the state that results."""
    who = _caller(request)
    registry = groups()
    group = registry.get(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"no group {group_id!r}")
    _may_manage(group, who, request)
    group.remove(orcid)
    registry.put(group)
    return GroupOut(**group.as_dict())


@v1.delete("/groups/{group_id}", tags=["access"])
def delete_group(group_id: str, request: Request) -> Dict[str, Any]:
    """Drop the group. The grants that named it stay in the rooms' ACLs and stop
    resolving — reported rather than chased: a server that walked every room to
    scrub a name would be doing a migration nobody asked for, and the grant is
    visible in the room's member list, where somebody can decide."""
    who = _caller(request)
    registry = groups()
    group = registry.get(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"no group {group_id!r}")
    _may_manage(group, who, request)
    return {"ok": registry.drop(group_id), "group": group_id,
            "note": "grants naming this group stay in the rooms' ACLs and now "
                    "resolve to nothing"}


# ── the HANDOFF: "open this room in <tool>" ──────────────────────────────────
#
# The contract lives in `app/handoff.py`, including the reason the link carries no
# token. What is here is the two faces of it: the JSON a UI asks for, and the
# plain page a person can paste anywhere.


class HandoffOut(BaseModel):
    room: str
    server: str
    #: `stratigraph://open?server=…&room=…`
    scheme: str
    #: the same handoff as an https link this server serves itself
    web: str
    tools: Dict[str, Any] = Field(default_factory=dict)
    #: asserted in the answer, and checked by a test that greps the URLs: a
    #: handoff names a place and never a permission
    carries_token: bool = False
    note: str = ""


class WhoAmI(BaseModel):
    """Who the token says you are. Identity, and nothing about capability."""

    orcid: Optional[str] = None
    name: Optional[str] = None
    #: False on a node with no OIDC: there is nobody to be, and saying so beats
    #: showing an empty name as if the sign-in had failed
    enforcing: bool = True


@v1.get("/whoami", response_model=WhoAmI, tags=["meta"])
def member_whoami(request: Request) -> WhoAmI:
    """The MEMBER-facing identity — deliberately not `/v1/admin/whoami`.

    That one answers "may you administer this node", which is a capability and is
    gated as one. This answers "who am I", which every signed-in page needs to
    put a name in a corner, and which no page should have to pass an operator
    check to learn. Two questions, two endpoints — the same separation the
    console's own sign-in makes between identity and capability.
    """
    principal = authenticator.require_token(request)
    dev_mode = bool(principal.get("em_dev_mode"))
    return WhoAmI(
        orcid=None if dev_mode else (principal.get("orcid")
                                     or principal.get("preferred_username")
                                     or principal.get("sub")),
        name=principal.get("name") or None,
        enforcing=bool(authenticator.settings.enforcing))


@v1.get("/rooms/{room_id}/open", response_model=HandoffOut, tags=["rooms"])
async def room_handoff(room_id: str, request: Request) -> HandoffOut:
    """How to open this room in a tool, without typing anything.

    **Who may**: anybody with a grant here — the same rule the listing follows.
    A handoff for a room you may not enter would be a way to find out which rooms
    exist, and a listing is not a discovery service.

    The answer carries no credential. The tool that follows the link signs in by
    itself (OIDC + PKCE) and holds its token in memory.
    """
    from . import access
    from . import handoff as ho

    _acl, _room, role, who = await _acting_role(room_id, request)
    if role is None:
        raise HTTPException(
            status_code=access.http_refusal_code(who),
            detail="no grant in this room: ask its owner for an invite link — "
                   "that one carries a role, which is what this one deliberately "
                   "does not")
    try:
        return HandoffOut(**ho.open_targets(room_id))
    except ho.HandoffError as exc:
        # a node that does not know its own public address cannot write a link
        # another machine could follow, and saying so beats writing localhost
        raise HTTPException(status_code=503, detail=str(exc)) from None


@v1_public.post("/handoff/parse", tags=["rooms"])
def parse_handoff(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """`{link}` → `{server, room}`, or the reason it is not a handoff.

    Public and read-only: it parses a string and reaches nothing. It exists so
    that the THREE consumers (this server's own page, EMStudio, the field
    assistant) can be measured against one implementation of the grammar rather
    than three that drift — the same argument the wire's `read()` makes.
    """
    from . import handoff as ho

    try:
        return {"ok": True, **ho.parse(str(body.get("link") or ""))}
    except ho.HandoffError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/open", response_class=HTMLResponse, tags=["rooms"])
def open_page(server: str = Query(default=""),
              room: str = Query(default="")) -> HTMLResponse:
    """The web half of the handoff: a page that tries the scheme, then explains.

    Outside `/v1` and unauthenticated, deliberately — it is the thing a person
    pastes into a browser, and a 401 on it would be a blank page with a status
    code. It carries no data about the room: the parameters came from the caller
    and go straight back out, and every real check happens when the tool that
    opens signs in.

    Why a page at all, rather than a bare redirect to the scheme: on a machine
    with no handler registered a redirect does NOTHING, silently, and looks like
    the user's fault. This says what happened and what to install.
    """
    from . import handoff as ho

    safe_room = html_escape(str(room or ""))
    safe_server = html_escape(str(server or ""))
    try:
        link = ho.scheme_url(server or ho.public_base(), room)
    except ho.HandoffError:
        link = ""
    safe_link = html_escape(link)
    return HTMLResponse(f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open room {safe_room}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 16px/1.55 system-ui, sans-serif; max-width: 34rem;
        margin: 12vh auto; padding: 0 1.2rem; }}
 code {{ background: rgba(127,127,127,.18); padding: .1em .35em; border-radius: 3px;
        word-break: break-all; }}
 a.go {{ display: inline-block; margin: 1.2rem 0; padding: .6rem 1.1rem;
        border: 1px solid currentColor; border-radius: 6px;
        text-decoration: none; font-weight: 600; }}
 p.small {{ opacity: .75; font-size: .9em; }}
</style>
<h1>Room <code>{safe_room}</code></h1>
<p>on <code>{safe_server or '(this server)'}</code></p>
<a class="go" id="go" href="{safe_link}">Open in a StratiGraph tool</a>
<p class="small" id="said"></p>
<p class="small">
  This link names a <strong>place</strong>, not a permission — there is no token
  in it. Whichever tool opens it signs you in itself and keeps the token in
  memory. If you are not a member of this room yet, ask its owner for an
  <em>invite</em> link: that one carries a role.
</p>
<script>
  // Try the handler, then SAY what happened. A page that only redirected would
  // be a blank tab on every machine with no handler registered.
  const said = document.getElementById("said");
  const go = document.getElementById("go");
  if (!go.getAttribute("href")) {{
    said.textContent = "This server does not know its own public address, so it "
      + "cannot write a link another machine could follow (EM_PUBLIC_BASE).";
    go.remove();
  }} else {{
    let left = false;
    window.addEventListener("blur", () => {{ left = true; }});
    setTimeout(() => {{ go.click(); }}, 120);
    setTimeout(() => {{
      if (left) return;
      said.textContent = "Nothing opened — no StratiGraph tool is registered for "
        + "this link on this machine. Install EMStudio, or open the room from "
        + "inside it, or copy the link above.";
    }}, 1800);
  }}
</script>
""")


# ── static, and never stale ──────────────────────────────────────────────────
#
# MEASURED 2026-08-30, while restyling: an edited `console.css` did NOT reach a
# browser that already had the page open — it kept parsing yesterday's rules and
# the new theme simply did not appear. The mount sends an `ETag` and a
# `Last-Modified` but no `Cache-Control`, so a browser applies HEURISTIC
# freshness and may serve a stale copy without asking.
#
# `no-cache` does not mean "do not store": it means "store, but revalidate". With
# the ETag already there, that is a 304 and a few bytes — and the operator never
# opens a console that is a version behind, which for an instrument panel is the
# failure that matters. The fonts and the logos pay the same conditional request
# and are answered 304 too; a device that is offline still gets them out of its
# own cache, because that is what a failed revalidation falls back to.
class _FreshStatic(StaticFiles):
    async def get_response(self, path: str, scope):        # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


# ── the NODE CONSOLE, served by the process it administers ───────────────────
#
# Static files, no build step, mounted OUTSIDE `/v1` because it is not the API: it
# is a face on the API, and the same one an operator opens in a browser.
#
# It is deliberately NOT behind the router's auth dependency. A 401 on the HTML
# would be a blank page with a status code; the page loads, asks the node
# `GET /v1/admin/whoami`, and then either draws the console or says — in a
# sentence — that this identity is not an operator and who grants that. The
# *data* is behind the capability, which is where a gate belongs; the shell is
# just a shell.
_CONSOLE = pathlib.Path(__file__).resolve().parent / "node_admin"
if _CONSOLE.is_dir():
    app.mount("/admin", _FreshStatic(directory=str(_CONSOLE), html=True),
              name="node-console")

# …and the MEMBER-facing one beside it. A second page rather than a panel in the
# first, because the scope is different: the console is about the NODE (every
# room, the storage, the lifecycle) and needs an operator capability; this is
# about YOUR rooms and needs only that you are somebody. It imports the console's
# sign-in module relatively (`../admin/auth.js`), so there is ONE PKCE
# implementation and it survives the prefix a proxy adds.
# The BRAND both faces wear, served beside them. Vendored from
# `stratigraph-brand/` by `sync-brand.sh` — same-origin and never a CDN, because
# a node may be deployed where there is no route out and a console that lost its
# typeface with the uplink would lose it exactly when somebody is trying to find
# out why.
#
# Mounted rather than listed file by file: the set is data, and adding a font
# weight to the brand should not mean editing this module.
_BRAND = pathlib.Path(__file__).resolve().parent / "brand"
if _BRAND.is_dir():
    app.mount("/brand", _FreshStatic(directory=str(_BRAND)), name="brand")

_ROOMS_UI = pathlib.Path(__file__).resolve().parent / "rooms_ui"
if _ROOMS_UI.is_dir():
    app.mount("/rooms", _FreshStatic(directory=str(_ROOMS_UI), html=True),
              name="rooms-browser")

# ── THE PAGES SEPARATE BY VERB (design note, 6 September 2026) ────────────────
#
# `/rooms/` used to do four jobs at once, so whoever arrived for one walked
# through the other three. It is now the VESTIBULE — who you are, how the node
# is, where to go — and two of the verbs are pages of their own:
#
#     /work/    lavorare      enter a room, create one, bring a file in
#     /tools/   attrezzarsi   what runs here, and what you install
#
# The other two were already elsewhere: consultare is the CATALOGUE (measured:
# `/catalog/ui/` already lists the published studies and already has the by-HDT
# view) and amministrare is `/admin/`.
#
# MOUNTED AT THE TOP, not nested under `/rooms/`, and the reason is not
# cosmetic: the pages share one script, and it derives the API's address by
# stripping its own door from the path (`/em/work/` → `/em/v1`). Nested at
# `/em/rooms/work/` that strip yields `/em/rooms/v1` and every call 404s behind a
# "Loading…" — the exact trap `rooms.js` already carries a comment about, one
# spelling later. The URL saying the verb is the smaller half of the reason.
    for _verb in ("work", "tools"):
        _page = _ROOMS_UI / _verb
        if _page.is_dir():
            app.mount(f"/{_verb}", _FreshStatic(directory=str(_page), html=True),
                      name=f"door-{_verb}")

# ── THE NODE'S OWN FACES, listed where they are MOUNTED ──────────────────────
#
# The entrance page has to be able to say «here is where you start again» without
# owning a list of addresses — the same rule as `/v1/auth-config` and `/v1/node`:
# the node declares, the page composes. So the faces are enumerated HERE, three
# lines under the `mount` calls and the routes they name, because a list that
# lives beside what it describes is the one that gets updated when that thing
# moves.
#
# It is still a list, so it can still drift. Which is why
# `tests/test_node_map.py` resolves every `path` in here against the app's real
# route table and fails the suite when one of them does not exist. Without that
# test this would be the fifth copy to keep aligned — exactly the mistake this
# whole arc has been undoing.
#
# NOT a new endpoint: this rides on `/v1/admin/health`, which is already the
# operator's infrastructure map.
ENTRANCES: List[Dict[str, str]] = [
    {"path": "/rooms/", "label": "The front door",
     "what": "rooms, studies and monuments — where somebody who came to work "
             "starts, and where somebody who came to look around starts too"},
    {"path": "/admin/", "label": "Node console",
     "what": "every room on this node, the storage behind them, the lifecycle "
             "of the ones nobody claims. Operator capability required"},
    {"path": "/docs", "label": "OpenAPI",
     "what": "every route this build serves, with its shapes — the answer to "
             "«what can I call» that does not need a person"},
    {"path": "/health", "label": "Public probe",
     "what": "is the process up and what can this build do. Unauthenticated, "
             "for an orchestrator"},
    {"path": "/v1/node", "label": "What this node offers",
     "what": "the neighbours and their states, reduced — no internal hostname, "
             "no latency. What the front door composes itself from"},
    {"path": "/v1/auth-config", "label": "How to sign in",
     "what": "the realm, the client and the endpoints a browser needs for "
             "OIDC + PKCE. Empty of secrets by construction"},
]


def entrances(public_base: str) -> List[Dict[str, str]]:
    """The faces above, each with the address another machine can reach.

    `url` is empty when this node has no public name — `handoff.public_base()`
    returns "" rather than guessing, and a page that printed `http://localhost:8000`
    would be handing an operator a link that works only where it was written.
    The path is always there, which is what makes the row useful anyway.
    """
    base = (public_base or "").rstrip("/")
    return [{**face, "url": f"{base}{face['path']}" if base else ""}
            for face in ENTRANCES]


app.include_router(v1_public)
app.include_router(v1)
#: P4.2 · the relay. A router of its own because it is a different KIND of thing:
#: everything above is stateless request/response, and this holds connections. It
#: authenticates in the handshake rather than through the router dependency —
#: a WebSocket has no place to put a 401 body, so the refusal is a close code.
app.include_router(ws_router)


@app.get("/health", response_model=Health, tags=["meta"],
         summary="Unversioned probe alias — same payload as /v1/health")
def health_probe() -> Health:
    """The orchestrator's health check.

    Deliberately outside `/v1`: a Docker HEALTHCHECK, a k8s probe or a Caddy
    upstream check is infrastructure, and it must not need editing when the API
    version moves. Same function, same payload — there is nothing to keep in sync.
    """
    return health()


# ── what this build deliberately does NOT do ──────────────────────────────────
# No write op, no upload, no asset store, no WebSocket. Each is a phase of its own
# with a decision attached (which bucket layout, which conflict policy), and
# shipping a placeholder for any of them would be worse than the absence: a stub
# endpoint gets called.
#
#   P1 — DONE: Keycloak bearer tokens on the shared realm (`app/auth.py`). ORCID as
#        the user identity is configured in the realm, not here.
#   P2 — MinIO assets: the same stable-ID resolver s3Dgraphy already has
#   P3 — the op-log WebSocket (ADR-002: one host per session, CRDT later)
#   P4 — deployment (WP6)


def main() -> None:  # pragma: no cover — entry point
    """`python -m app.main` for a quick local run; production uses uvicorn."""
    import uvicorn
    uvicorn.run("app.main:app", host=os.environ.get("EM_SERVER_HOST", "127.0.0.1"),
                port=int(os.environ.get("EM_SERVER_PORT", "8000")), reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
