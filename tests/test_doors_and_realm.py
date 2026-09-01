"""A page this server MOUNTS is a page the realm has to know — said here, not in
a browser two days later.

## What happened, and why no suite could see it

On 6 September 2026 `/em/work/` and `/em/tools/` were born. Caddy serves them
without being told anything (`handle_path /em/*` takes the lot), every suite went
green, and the defect arrived in a browser two days later:

    /em/work/    400  Invalid parameter: redirect_uri
    /em/tools/   400  Invalid parameter: redirect_uri
    /em/rooms/   302

Nothing CONNECTED the act of mounting a page to the act of declaring it to the
identity provider. Two lists, both correct on their own, and no test looked at
them together.

The decision this file implements is not «let the realm update itself» — it is
**let the test say it before the browser does**. The difference between «it
repairs itself» and «somebody tells me in time» is that the second one can be
measured.

## How it derives, and why it does not list

The pages come from THE CODE THAT MOUNTS THEM — `app.routes`, filtered to the
static mounts a browser lands on. A hand-written list here would be the fourth
list of the same doors, and the fourth is the one that goes stale.

Same shape this repository already uses twice: `test_room_id_parity.py` reads the
front door's JavaScript rather than reimplementing the slug, and
`test_every_archive_endpoint_goes_THROUGH_the_registry` reads `main.py` rather
than trusting it. Third object, same idea.

`html=True` is what separates a PAGE from an ASSET mount, and it is not a
convention invented here: `/brand` is mounted without it (it serves fonts and a
logo, and nobody signs in from there) while `/admin`, `/rooms`, `/work` and
`/tools` are mounted with it. Measured, not assumed — the assertion below fails
loudly if that ever stops being true.

## ONE DIRECTION ONLY

Every mounted page must be covered. The realm is **not** required to be minimal:
the extra URIs on `localhost:5173` and on the direct ports are there for measured
reasons (mixed content, and the sign-in ring from the Vite dev server). A test
that demanded minimality would delete somebody's working setup to satisfy itself.

## WHAT THIS TEST DOES NOT SEE, named so nobody believes it covers everything

There are three lists of these doors and this file reads ONE of them:

1. `dev-stack/keycloak/realm-em-dev.json` — **read here**. It is the file every
   fresh stack imports, which is why it is the one worth gating.
2. `heriverse-ansible/role/defaults/main.yml` → `em_server_doors`, with a
   preflight (`role/tasks/main.yml`, tag `realm-preflight`) that refuses the
   deploy when the production realm is missing a door. **Another repository: this
   test cannot see it, and a cross-repo test is deliberately out of scope.**
3. The dev-stack `Caddyfile.dev` handles `/em/studio/*` and `/em/read/*` — those
   are EMStudio and the reader, served by Caddy from a Vite build, not mounted
   here. So this test does not know about them, and the realm carries their URIs
   for reasons no assertion in this file defends.

And two things it cannot know at all: whether the realm **in execution** has been
re-imported (`--import-realm` imports only when the realm is created), and whether
the production realm — which lives in `docker-heriverse` and belongs to 3DR — has
the client at all. On 7 September 2026 it did not: one client, `heriverse`, and no
`em-console`.
"""

from __future__ import annotations

import json
import pathlib
from typing import Dict, List, Tuple
from urllib.parse import urlsplit

from starlette.staticfiles import StaticFiles

from app.main import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
REALM = json.loads((_REPO / "dev-stack" / "keycloak" / "realm-em-dev.json")
                   .read_text(encoding="utf-8"))

#: The PUBLIC browser client — the one a page signs in through. Not
#: `em_server_audience`/`em-server`, which is the resource server tokens are
#: validated FOR. Two different things with similar names.
CONSOLE = "em-console"


def mounted_pages() -> List[Tuple[str, str]]:
    """(mount path, route name) for every static mount a browser LANDS on.

    Derived from the running app, so a page added by a loop — which is how
    `/work` and `/tools` arrive — is found without anybody adding a line here.
    """
    found = []
    for route in app.routes:
        sub = getattr(route, "app", None)
        if isinstance(sub, StaticFiles) and getattr(sub, "html", False):
            found.append((str(getattr(route, "path", "")),
                          str(getattr(route, "name", "") or "?")))
    return sorted(found)


def console_uris() -> List[str]:
    for client in REALM.get("clients", []):
        if client.get("clientId") == CONSOLE:
            return [str(u) for u in client.get("redirectUris", [])]
    return []


def _covered() -> Dict[str, List[str]]:
    """mount segment → the redirect URIs that cover it.

    A URI covers a mount when its PATH ends with `/<segment>/*`, whatever prefix
    it carries: the same page is `/rooms/*` on a bare uvicorn and `/em/rooms/*`
    behind the node's Caddy, and both are the same door.
    """
    uris = console_uris()
    out: Dict[str, List[str]] = {}
    for path, _name in mounted_pages():
        segment = path.strip("/")
        out[segment] = [u for u in uris
                        if urlsplit(u).path.rstrip("/").endswith(f"/{segment}/*")
                        or urlsplit(u).path.rstrip("/*").endswith(f"/{segment}")]
    return out


def _paste_ready(segment: str) -> List[str]:
    """The URIs to add, in the spellings THIS realm already uses.

    Derived from a sibling that IS covered rather than written out here: a
    hardcoded hostname would be right for the dev stack and wrong for the next
    one, and the whole point of the message is that it can be pasted.
    """
    # FROM THE COVERED SIBLINGS, and only those. The first version read EVERY
    # URI in the client and offered six spellings — two of which came from
    # `/chat/*`, the field assistant at the root of the proxy, which is not a
    # spelling of THIS server's mounts at all. A paste-ready line that is not
    # right is worse than none: somebody pastes it and the door still refuses.
    covered = _covered()
    prefixes = set()
    for sibling, uris in covered.items():
        for uri in uris:
            split = urlsplit(uri)
            path = split.path.rstrip("/*").rstrip("/")
            if not path.endswith(f"/{sibling}"):
                continue
            head = path[: -len(f"/{sibling}")]
            prefixes.add(f"{split.scheme}://{split.netloc}{head}")
    return sorted(f"{p}/{segment}/*" for p in prefixes) or [f"/{segment}/*"]


def test_the_pages_are_DERIVED_and_the_asset_mount_is_not_one():
    """The premise of everything below. If `html=True` ever stops separating a
    page from an asset mount, this test starts checking the wrong set — and it
    should fail here rather than pass quietly on a smaller list."""
    pages = dict((p.strip("/"), n) for p, n in mounted_pages())
    assert "brand" not in pages, (
        "the asset mount is being treated as a page; nobody signs in from "
        "`/brand/`, and requiring a redirect URI for it would be noise")
    for expected in ("admin", "rooms"):
        assert expected in pages, f"{expected} is not mounted as a page any more"
    assert len(pages) >= 3, pages


def test_EVERY_MOUNTED_PAGE_IS_A_REDIRECT_URI_IN_THE_DEV_REALM():
    """The connection that did not exist. A page Caddy serves and the realm has
    never heard of works perfectly until somebody presses «sign in», and then
    answers «400 Invalid parameter: redirect_uri» — a Keycloak error page one
    redirect away from a page that looked fine."""
    assert console_uris(), (
        f"the dev realm has no {CONSOLE!r} client, so no page on this server can "
        "be signed in from at all")
    naked = {segment: uris for segment, uris in _covered().items() if not uris}
    assert not naked, (
        "these pages are mounted and the dev realm does not know them:\n"
        + "\n".join(
            f"  · {segment}  → add to realm {REALM.get('realm')!r} → Clients → "
            f"{CONSOLE} → Valid redirect URIs:\n"
            + "\n".join(f"      {uri}" for uri in _paste_ready(segment))
            for segment in sorted(naked))
        + "\n\nThe file is dev-stack/keycloak/realm-em-dev.json. Note that "
          "`--import-realm` imports ONLY when the realm is created, so a running "
          "stack needs the same change in the Keycloak admin console (or a "
          "`--force-recreate keycloak`, which re-seeds the rooms).")


def test_the_direction_is_ONE_WAY_and_a_spare_uri_is_not_a_defect():
    """A URI with no page behind it is allowed, and this test says so out loud
    rather than leaving it to be discovered.

    `localhost:5173` is the Vite dev server (the sign-in ring from EMStudio's own
    port), and the direct-port spellings exist so a bare `uvicorn` run can sign
    in too. Both are measured reasons. A test that demanded a minimal realm would
    delete somebody's working setup in order to be satisfied.
    """
    segments = {p.strip("/") for p, _ in mounted_pages()}
    spare = [u for u in console_uris()
             if not any(urlsplit(u).path.rstrip("/").endswith(f"/{s}/*")
                        or urlsplit(u).path.rstrip("/*").endswith(f"/{s}")
                        for s in segments)]
    # …and there ARE some today: this is not a hypothetical tolerance
    assert spare, ("no spare URIs at all — which is fine, but then this test is "
                   "asserting a tolerance nothing exercises")
    assert len(console_uris()) > len(segments)


def test_the_message_CARRIES_THE_URI_and_not_only_the_complaint():
    """An error that says «a redirect URI is missing» makes whoever reads it redo
    the work the assertion already did. This one hands over the line to paste,
    in the spellings this realm already uses — derived from a covered sibling, so
    it is right for the realm in front of you and not for the one I had."""
    lines = _paste_ready("qualunque-pagina")
    assert lines, "no paste-ready line could be derived"
    assert any(line.startswith("https://") for line in lines), lines
    assert all(line.endswith("/qualunque-pagina/*") for line in lines), lines
    # …and the spellings come from the realm, not from this file
    hosts = {urlsplit(line).netloc for line in lines}
    assert hosts <= {urlsplit(u).netloc for u in console_uris()}, hosts
