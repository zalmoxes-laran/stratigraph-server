"""The node map: every face the page offers must be a face this app really has.

`ENTRANCES` in `app/main.py` is a LIST, and a list of paths beside the routes it
describes is still a list that can drift. This is the test that makes it not
drift: every `path` is resolved against the app's own route table, so renaming a
route or dropping a mount fails the suite instead of shipping a page with a dead
link on it.

Without this file the list would be the fifth copy of "where the node's faces
are" — the exact mistake `/v1/auth-config`, `/v1/node` and the handoff grammar
each exist to avoid. The other half of the same discipline is what this file does
NOT test: it does not check any ADDRESS, because no address is written down here
— `entrances()` composes them from `handoff.public_base()` and leaves them empty
when the node has no public name.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount
from starlette.testclient import TestClient

from app import main
from app.main import ENTRANCES, entrances


def _routed_paths(routes=None, prefix: str = "") -> set:
    """Every path this app answers — routes, mounts, and INCLUDED ROUTERS.

    Recursive, and that is not tidiness: `app.include_router(v1)` puts a wrapper
    object in `app.routes` that carries no `path` of its own, so a flat walk finds
    twelve entries and none of the `/v1/...` ones. Measured — the first version of
    this test failed on `/v1/node` and `/v1/auth-config` while both were perfectly
    well routed, which is a test lying about the code rather than the reverse.
    """
    paths = set()
    for route in (main.app.routes if routes is None else routes):
        path = getattr(route, "path", None)
        # An included router arrives WRAPPED (`_IncludedRouter`) with no `path`
        # and no `routes`: the real ones hang off `original_router`, and their
        # `path` already carries the router's prefix. Found by printing the
        # twelve top-level entries rather than by assuming a shape.
        original = getattr(route, "original_router", None)
        nested = getattr(route, "routes", None) or getattr(original, "routes", None)
        if path:
            paths.add(prefix + path)
            if isinstance(route, Mount):
                # a mount answers everything under its prefix
                paths.add((prefix + path).rstrip("/") + "/")
        if nested and not isinstance(route, Mount):
            paths |= _routed_paths(nested, prefix + (path or ""))
    return paths


def _resolves(path: str, routed: set) -> bool:
    """Is `path` answered by a route or by a mount's prefix?"""
    if path in routed or path.rstrip("/") in routed:
        return True
    return any(r.endswith("/") and path.startswith(r) for r in routed)


@pytest.mark.parametrize("face", ENTRANCES, ids=[f["path"] for f in ENTRANCES])
def test_every_entrance_resolves_in_the_route_table(face):
    """THE ANTI-DRIFT TEST, and the point of the whole list.

    A face whose path no longer exists is a dead link on the operator's map, and
    the operator is precisely the person who cannot tell a broken page from a
    broken node.
    """
    routed = _routed_paths()
    assert _resolves(face["path"], routed), (
        f"`{face['path']}` ({face['label']}) is on the node map and is NOT a "
        f"route this app serves. Either the route was renamed — in which case fix "
        f"ENTRANCES in app/main.py, three lines under the mounts — or the face "
        f"was removed and the entry has to go with it."
    )


@pytest.mark.parametrize("face", ENTRANCES, ids=[f["path"] for f in ENTRANCES])
def test_every_entrance_actually_answers(face):
    """…and it answers something, which is a stronger claim than "it is routed".

    Not 200: `/admin/` needs a capability and `/v1/node` may need a token, and
    demanding 200 here would be testing the auth rather than the map. What is
    forbidden is 404 — the one answer that means the map is wrong.
    """
    with TestClient(main.app) as client:
        answer = client.get(face["path"])
    assert answer.status_code != 404, (
        f"`{face['path']}` answered 404: the node map points at a face that is "
        f"not there"
    )


def test_EVERY_MOUNTED_PAGE_IS_ON_THE_MAP():
    """THE DRIFT THE OTHER DIRECTION, which nothing caught until 7 October 2026.

    The tests above prove every LISTED path is routed. They cannot prove the
    reverse, and the reverse is what had gone wrong: `/work/` and `/tools/` were
    mounted eight lines above `ENTRANCES` — with a comment on that mount saying
    the faces are enumerated «three lines under the `mount` calls» — and neither
    was in the list. Meanwhile `rooms_ui/rooms.js` carried `"../work/"` and
    `"../tools/"` in its own source, so the list that exists to prevent a fourth
    copy of «where the node's faces are» had one sitting next to it.

    Asked of the app's own MOUNT TABLE, so there is no source read as text here
    and no false positive to construct: `/brand` is excluded because it is
    assets and not a face — the one exemption, named.
    """
    mounted = {route.path.rstrip("/") + "/" for route in main.app.routes
               if isinstance(route, Mount) and route.path != "/brand"}
    listed = {face["path"] for face in ENTRANCES}
    missing = sorted(m for m in mounted if m not in listed)
    assert missing == [], (
        f"{missing} is mounted and is NOT on the node map. A page nobody lists "
        f"is a page every other page has to know the address of — which is how "
        f"`../work/` ended up written inside `rooms.js`."
    )


def test_A_FACE_SAYS_WHAT_IT_NEEDS_structurally():
    """`needs` is a field and not a sentence, because a bar has to DECIDE.

    `/admin/`'s prose already said «Operator capability required» and a bar that
    read it would have been a bar that greps English.
    """
    needs = {face["path"]: face["needs"] for face in ENTRANCES}
    assert needs["/admin/"] == "operator"
    assert all(v in ("", "operator") for v in needs.values()), needs
    #: …and the four faces a PERSON opens are marked apart from the four a
    #: machine calls: a bar drawn from all eight would offer «OpenAPI» beside
    #: «Work», which is a menu that does not know who is reading it
    pages = {f["path"] for f in ENTRANCES if f["page"]}
    assert pages == {"/rooms/", "/work/", "/tools/", "/admin/"}, pages


def test_THE_FACES_TRAVEL_ON_THE_PUBLIC_ROUTE_TOO():
    """A bar shared by four pages needs them, and a visitor has no operator
    capability — so the source cannot be `/v1/admin/health`.

    What this publishes was already discoverable: `/docs` lists every route of
    this build, `/admin/` among them. Publishing the path of a locked door is
    not unlocking it.
    """
    with TestClient(main.app) as client:
        answer = client.get("/v1/node")
    assert answer.status_code == 200
    faces = answer.json()["faces"]
    assert {f["path"] for f in faces} == {f["path"] for f in ENTRANCES}
    #: e la stessa lista, non una seconda: ogni campo viene da `ENTRANCES`
    for face in faces:
        original = next(f for f in ENTRANCES if f["path"] == face["path"])
        for field in ("label", "what", "key", "needs", "page"):
            assert face[field] == original[field], (face["path"], field)


def test_the_addresses_are_composed_and_never_guessed():
    """A node with no public name shows the path and NO link.

    `http://localhost:8000/rooms/` written for convenience is a link that works
    where it was written and nowhere else — a default that is an assertion.
    """
    named = entrances("https://em.example.org/em")
    assert all(f["url"].startswith("https://em.example.org/em") for f in named)
    anonymous = entrances("")
    assert all(f["url"] == "" for f in anonymous)
    # …and the path survives either way, which is what keeps the row useful
    assert {f["path"] for f in anonymous} == {f["path"] for f in ENTRANCES}


def test_a_trailing_slash_is_not_doubled():
    assert "//rooms" not in entrances("https://x/")[0]["url"]


def test_every_face_says_what_it_is_for():
    """A map with labels and no sentences is a list of paths, which the operator
    already had (in the CHEATSHEET, which is what this replaces)."""
    for face in ENTRANCES:
        assert face["label"].strip()
        assert len(face["what"].strip()) > 25, face["path"]
