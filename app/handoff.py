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
CONSUMERS: Dict[str, Dict[str, str]] = {
    "emstudio": {
        "label": "EMStudio",
        "note": "opens the room and joins it live (desktop registers the scheme; "
                "the web build reads the same parameters off its own URL)",
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
    },
}


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
        "tools": {name: {**CONSUMERS[name], "scheme": link, "web": web}
                  for name in wanted},
        "carries_token": False,
        "note": "the link names a place, not a permission: the tool signs in by "
                "itself (OIDC + PKCE) and holds its token in memory. If you are "
                "not a member of this room yet, ask for an invite link instead — "
                "that one carries a role.",
    }


def parse(link: str) -> Dict[str, str]:
    """`{server, room}` out of a handoff link, or a sentence.

    Accepts both forms — `stratigraph://open?…` and the `https://…/open?…` web
    variant — because a consumer should not care which one it was handed, and
    the same reader is what makes them interchangeable.

    **Refuses a link carrying anything that looks like a credential.** Not
    because we would use it, but because accepting one teaches whoever built it
    that sending one works: the next link has a token in it and this contract's
    only security property is gone.
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
    if not room:
        raise HandoffError("the link names no room")
    if not server:
        # the web form can leave it implicit: the page IS on the server
        if parsed.scheme in ("http", "https"):
            server = f"{parsed.scheme}://{parsed.netloc}"
        else:
            raise HandoffError("the link names no server")
    return {"server": server, "room": room}


def describe() -> Dict[str, Any]:
    """For `/health`: whether this node can write a link at all, and for whom.

    An operator sees the missing `EM_PUBLIC_BASE` here rather than learning about
    it from a user whose button produced a link to `localhost`.
    """
    base = public_base()
    return {"scheme": f"{SCHEME}://{ACTION}", "public_base": base or None,
            "tools": sorted(CONSUMERS),
            "ready": bool(base)}
