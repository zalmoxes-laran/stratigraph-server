"""“Open in <tool>” — one handoff, many consumers.

The problem this kills: joining a room meant typing three things into a settings
panel — an address, a room name, and a token pasted from a terminal. Three
chances to get it wrong, and the third one is a secret travelling by clipboard.

The contract is one line and the security property is the whole design:

    stratigraph://open?server=<addr>&room=<id>

**The link carries a PLACE, never a permission.** No token, no ticket, no
signature. What it says is "there is a room called X on a server called Y" —
which is public in the same sense a street address is: knowing it gets you to the
door and not through it. The tool that receives the link does its OWN OIDC
sign-in against that server (Authorization Code + PKCE, the public client the
node console already uses) and holds the token in memory. So:

* a link in a chat, a ticket, a screenshot or a bug report leaks **nothing**;
* the token is minted for the person who clicked, not for whoever forwarded it;
* revoking access is a realm/ACL operation and takes effect immediately, which
  is not true of anything embedded in a URL.

**Being a member is a separate question.** The link opens a door; it does not
make you a member. A caller who is not in the room's ACL is refused by the room,
and the remedy is the INVITE (`app/invites.py`): a link that carries a room *and
a role*, redeemed against an identity, writing the ACL. That is a different link
with a different property, and conflating them would turn "here is where it is"
into "here, have access".

**One scheme, two entry points.** `stratigraph://` is the ecosystem's, not any
one app's: the Catalog opens a STUDY (`stratigraph-catalog/app/deeplink.py` —
which resolves to its container and, in time, its room), and this opens a ROOM
directly. `emstudio://` would have been a scheme owned by one consumer, which is
the opposite of what a handoff contract is for.

**The web variant is not a fallback, it is the honest default.** A custom scheme
only works where a handler is registered, and on a machine where it is not the
button silently does nothing — the worst failure, because it looks like the
user's fault. So every answer carries `web` too: a page on this server that tries
the scheme and, when nothing happens, says what to install.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any, Dict, List, Optional

#: The ecosystem's scheme. ONE, and not per-app — see the module docstring.
SCHEME = "stratigraph"

#: The verb. `open` is the only one today; a second (`invite`, `import`) would be
#: a second entry point on the same scheme rather than a second scheme.
ACTION = "open"

#: Tools that can consume a room handoff TODAY, measured rather than aspired to:
#: each one has a reader for this link in its own repo, and a test that holds it
#: to the same strings this module's own suite uses.
#:
#: Three, and `blender` was nearly two: EMtools was written off here as "not a
#: room client" and that was simply wrong — `sync_bridge/ws_client.py` is a
#: hand-rolled WebSocket client that joins a room on this server, and
#: `sync_manager/room_session.py` has been doing the join for a while. Checked
#: before it was added, not assumed.
#: `web_env` names the setting that says WHERE that tool's web build is served.
#: Set it and the room browser offers "open in browser" beside the desktop
#: scheme; leave it unset and that tool has only the scheme. NOT a default and
#: not a guess: a button pointing at a web app nobody deployed is a button that
#: fails after the click, which is worse than a button that is not there.
#:
#: `blender` has no `web_env` and never will — Blender is not a web app.
CONSUMERS: Dict[str, Dict[str, str]] = {
    "emstudio": {
        "label": "EMStudio",
        "note": "opens the room and joins it live (desktop registers the scheme; "
                "the web build reads the same parameters off its own URL)",
        "web_env": "EM_EMSTUDIO_WEB_URL",
    },
    "blender": {
        "label": "EMtools (Blender)",
        "note": "joins the room from the link and adopts its document — no "
                "handler is registered for the scheme, so the link is pasted "
                "into 'Open room from link…'",
    },
    "chatbot": {
        "label": "Field assistant",
        "note": "configures the field node to write into this room",
        "web_env": "EM_FIELD_ASSISTANT_URL",
    },
}


def web_app(tool: str) -> str:
    """Where that tool's WEB build is served, or "" when nobody said.

    Configuration, like every other address this module writes — see
    `public_base` for why a request header would be a hole rather than a
    convenience.
    """
    setting = (CONSUMERS.get(tool) or {}).get("web_env")
    if not setting:
        return ""
    return (os.environ.get(setting) or "").strip().rstrip("/")


def browser_url(tool: str, server: str, room: str) -> str:
    """The tool's own web app, opened ON this room.

    `<web>?server=<addr>&room=<id>` — the SAME two parameters as the scheme, and
    the same absence: no token. The web build signs in by itself (which is only
    now possible without mixed content, since Keycloak moved behind https), so
    a link a person copies out of the address bar still carries nothing.
    """
    base = web_app(tool)
    if not base:
        return ""
    query = urllib.parse.urlencode({"server": str(server).rstrip("/"),
                                    "room": str(room)})
    # EXACTLY ONE `/` BEFORE THE `?`, whatever the configuration said — and this
    # is a bug fix, measured in Chrome on 5 September 2026.
    #
    # `EM_EMSTUDIO_WEB_URL` was set to `/em/studio/`, the container really read
    # `/em/studio/`, and the button still produced `/em/studio?server=…`: the
    # slash was taken off by `web_app`'s `rstrip("/")` an instant after being
    # read, and the old rule here only put one back when the base had NO path —
    # which a path always has. So no value of the environment variable could
    # repair it, and Caddy's `handle /em/studio/*` does not match `/em/studio`:
    # the click landed on `{"detail":"Not Found"}`.
    #
    # The four forms this now normalises, all to one trailing slash:
    #   `/em/studio`  ·  `/em/studio/`  ·  `https://h`  ·  `https://h/app/`
    #
    # Why a trailing slash at all, rather than leaving it to the host: a page
    # served under a prefix resolves its own relative assets against the URL
    # DIRECTORY, and `/em/studio?x=1` makes that directory `/em/`. It is also the
    # form a browser rewrites the address bar to, so the link somebody COPIES is
    # already the one they will see back.
    return f"{base.rstrip('/')}/?{query}"


class HandoffError(ValueError):
    """A link that could not be built, said rather than half-built."""


def public_base(explicit: Optional[str] = None) -> str:
    """The address a link must NAME — the one another machine can reach.

    Configuration, never a request header. `Host` is caller-supplied, and a link
    built from it is a link an attacker can point at their own server by asking
    for it with the right header — the same rule `docs/URL-TOPOLOGY.md` states
    for what this service writes into a document.

    **No fallback, and that was measured.** The first version derived it from
    `EM_IIIF_PUBLIC`'s origin — "a deployment that said where its images are has
    already said where it is". Run against the dev stack it produced
    `stratigraph://open?server=http://localhost:8182&room=…`: the address of
    CANTALOUPE, a different service, in a link that looks perfectly well formed.
    A guess dressed as a derivation is worse than an absence, because the absence
    has a 503 with a sentence in it and the guess has a button that fails later
    somewhere else.

    So: configuration or nothing. And nothing, rather than `localhost`, because a
    link naming localhost is a link that only works on the machine that wrote it.
    """
    for value in (explicit, os.environ.get("EM_PUBLIC_BASE"),
                  os.environ.get("EM_SERVER_PUBLIC_URL")):
        if value and str(value).strip():
            return str(value).strip().rstrip("/")
    return ""


def scheme_url(server: str, room: str) -> str:
    """`stratigraph://open?server=…&room=…` — the link itself."""
    if not str(room or "").strip():
        raise HandoffError("a handoff needs a room: a link to a server is a "
                           "bookmark, not a handoff")
    if not str(server or "").strip():
        raise HandoffError(
            "this node does not know its own public address, so it cannot write "
            "a link another machine could follow. Set EM_PUBLIC_BASE.")
    query = urllib.parse.urlencode({"server": str(server).rstrip("/"),
                                    "room": str(room)})
    return f"{SCHEME}://{ACTION}?{query}"


def web_url(server: str, room: str) -> str:
    """The same handoff as a plain https link this server serves itself.

    Works in every browser, needs no handler, and is what a person can paste
    anywhere. The page it lands on tries the scheme and then explains.
    """
    query = urllib.parse.urlencode({"server": str(server).rstrip("/"),
                                    "room": str(room)})
    return f"{str(server).rstrip('/')}/open?{query}"


def open_targets(room: str, *, server: Optional[str] = None,
                 tools: Optional[List[str]] = None) -> Dict[str, Any]:
    """How to open this room, per tool.

    The same shape for every consumer on purpose: a tool reads `scheme` if it has
    a handler registered and `web` otherwise, and it never has to know which
    other tools exist.
    """
    base = public_base(server)
    wanted = [t for t in (tools or list(CONSUMERS)) if t in CONSUMERS]
    if not wanted:
        raise HandoffError(
            f"nothing here can open a room in that: today it is "
            f"{', '.join(CONSUMERS)}.")
    link = scheme_url(base, room)
    web = web_url(base, room)
    return {
        "room": room,
        "server": base,
        # The two forms of ONE link, said once. They are identical in content —
        # which is the property that makes "no token in the URL" checkable by
        # looking at either.
        "scheme": link,
        "web": web,
        # Three ways in, and each one is a fact rather than an offer:
        #   scheme  — the desktop handler (registered or not: that is the OS's)
        #   web     — this server's own /open page, which explains itself
        #   browser — the tool's OWN web build, present only when a setting
        #             names one. Absent = that tool has no web build here, and
        #             the UI draws no button for it.
        # `web_env` is dropped: the NAME of a setting is this deployment's
        # business, and an answer a browser reads should carry the address, not
        # the knob that produced it.
        "tools": {name: {k: v for k, v in
                         {**CONSUMERS[name], "scheme": link, "web": web,
                          "browser": browser_url(name, base, room)}.items()
                         if v and k != "web_env"}
                  for name in wanted},
        "carries_token": False,
        "note": "the link names a place, not a permission: the tool signs in by "
                "itself (OIDC + PKCE) and holds its token in memory. If you are "
                "not a member of this room yet, ask for an invite link instead — "
                "that one carries a role.",
    }


def parse(link: str) -> Dict[str, str]:
    """What a handoff names — a ROOM or a STUDY — or a sentence.

    Accepts both forms — `stratigraph://open?…` and the `https://…/open?…` web
    variant — because a consumer should not care which one it was handed, and
    the same reader is what makes them interchangeable.

    **TWO ACTIONS ON ONE NAMESPACE**, which is what the scheme was renamed for
    (see the module docstring): `{server, room}` opens a room here, and
    `{catalog, study}` opens a study in the Catalog. The answer carries `kind`
    so a caller does not have to infer it from which keys came back.

    Added 4 September 2026, and late: the Catalog had been emitting study links
    for a week and every consumer refused them with «the link names no room» —
    a button that could not work by construction. Three implementations of this
    grammar exist (here, `EMStudio/frontend/src/handoff.ts`, and the Catalog
    which WRITES it), so the second half of this change is
    `tests/test_handoff.py` and `check-handoff.mjs` measuring the same strings.

    **Refuses a link carrying anything that looks like a credential.** Not
    because we would use it, but because accepting one teaches whoever built it
    that sending one works: the next link has a token in it and this contract's
    only security property is gone. One list, both actions.
    """
    raw = str(link or "").strip()
    if not raw:
        raise HandoffError("empty link")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme == SCHEME:
        if (parsed.netloc or parsed.path.lstrip("/")) != ACTION:
            raise HandoffError(
                f"unknown action {parsed.netloc or parsed.path!r}: this scheme "
                f"understands {SCHEME}://{ACTION}")
    elif parsed.scheme in ("http", "https"):
        if not parsed.path.rstrip("/").endswith(f"/{ACTION}"):
            raise HandoffError(
                f"not a handoff link: {raw} (expected a path ending in /{ACTION})")
    else:
        raise HandoffError(
            f"not a handoff link: {raw} (expected {SCHEME}://{ACTION}?… or an "
            f"https link to /{ACTION})")

    query = urllib.parse.parse_qs(parsed.query)
    forbidden = sorted(k for k in query
                       if k.lower() in ("token", "access_token", "id_token",
                                        "password", "secret", "code",
                                        "authorization", "bearer", "api_key"))
    if forbidden:
        raise HandoffError(
            f"this link carries {', '.join(forbidden)} — a handoff names a place "
            f"and never a permission. Refused so that sending one never starts "
            f"working: the tool signs in by itself.")

    server = (query.get("server") or [""])[0].strip().rstrip("/")
    room = (query.get("room") or [""])[0].strip()
    study = (query.get("study") or [""])[0].strip()

    # AMBIGUITY FIRST. Two actions on one namespace need exactly one rule for
    # "which is this", and a link naming both is not a link with a preference —
    # it is one somebody built wrong, and guessing would make the guess the
    # contract. Unknown keys are still ignored, which is what keeps `focus=` and
    # whatever comes next backward compatible; `study` is not unknown any more.
    if room and study:
        raise HandoffError(
            "this link names both a room and a study; a handoff is one action. "
            f"Send {SCHEME}://{ACTION}?server=…&room=… to open a room, or "
            "?study=…&catalog=… to open a study.")

    if study:
        catalog = (query.get("catalog") or [""])[0].strip().rstrip("/")
        if not catalog and parsed.scheme in ("http", "https"):
            # the web form can leave it implicit: the page IS on the catalogue
            catalog = f"{parsed.scheme}://{parsed.netloc}"
        if not catalog:
            raise HandoffError(
                "this link names a study but no catalogue, so there is nowhere "
                "to fetch it from. The catalogue's address is part of the link "
                f"({SCHEME}://{ACTION}?study=…&catalog=…) and it comes from that "
                "deployment's configuration, never from whoever built the link.")
        return {"kind": "study", "catalog": catalog, "study": study}

    if not room:
        raise HandoffError(
            "the link names neither a room nor a study. A handoff opens one of "
            f"the two: {SCHEME}://{ACTION}?server=…&room=… for a room, or "
            "?study=…&catalog=… for a study.")
    if not server:
        # the web form can leave it implicit: the page IS on the server
        if parsed.scheme in ("http", "https"):
            server = f"{parsed.scheme}://{parsed.netloc}"
        else:
            raise HandoffError("the link names no server")
    return {"kind": "room", "server": server, "room": room}


def describe() -> Dict[str, Any]:
    """For `/health`: whether this node can write a link at all, and for whom.

    An operator sees the missing `EM_PUBLIC_BASE` here rather than learning about
    it from a user whose button produced a link to `localhost`.
    """
    base = public_base()
    return {"scheme": f"{SCHEME}://{ACTION}", "public_base": base or None,
            "tools": sorted(CONSUMERS),
            "ready": bool(base)}
