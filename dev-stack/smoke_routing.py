#!/usr/bin/env python3
"""The node's front door: what is routed, and what a client is told when it is not.

    python dev-stack/smoke_routing.py

Every other smoke here measures a FEATURE. This one measures the proxy, and it
exists because of a specific afternoon: the Caddyfile's last block answered
**200** with a text banner to anything unrouted, so a client that posted to the
wrong base got a success, died parsing it, and reported the node as unreachable
while the node was up. A field note dictated in a trench would have queued for
ever behind a message blaming the network.

That is not one client's bug — it is every client we connect. So the rule gets a
test:

* `/` is a BANNER, 200, for a person who lands on the node;
* anything else unrouted is **404**, for a program that missed;
* and the real routes are untouched, which is the half a change like that breaks
  by accident.
"""

from __future__ import annotations

import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from smoke_common import _TLS, Tally, arguments, call  # noqa: E402

#: The routes this node really serves. `/assets` and `/iiif` are answered by
#: MinIO and Cantaloupe, which have their own opinions about a missing object —
#: what matters here is that the answer comes from THEM.
ROUTED = [
    ("/em/v1/health", 200, "the room API"),
    ("/catalog/health", 200, "the catalogue"),
    ("/chat/health", 200, "the field assistant"),
    ("/chat/", 200, "…and its page"),
]

#: EMSTUDIO, on the node's own origin — the whole point of which is that it is
#: SAME-ORIGIN with the catalogue, so the editor can fetch a study container.
#: Measured separately from `ROUTED` because in development the upstream is a dev
#: server on somebody's laptop: absent is ORDINARY here, and the interesting
#: property is then what the node SAYS (a 502 that reads, never a 200 with HTML —
#: the fallback that answered 200 is what this whole file exists for).
EMSTUDIO = [
    ("/em/studio/", "the editor"),
    ("/em/read/", "the dissemination reader"),
]

#: Nothing here is a route. Each one used to get 200 and a banner, and that is
#: the whole reason this file exists: a fallback answering 200 to anything made a
#: client die on `res.json()` reporting «node unreachable» while the node was up.
UNROUTED = [
    "/inventato",
    "/v1/say",             # the exact path that started this: the assistant's
    "/v1/rooms",           # …and one that LOOKS like the room API without /em
    "/api/anything",
]

#: A BARE PREFIX IS NOT «nothing»: it is a route missing its slash.
#:
#: `/em`, `/catalog` and `/chat` used to be in the list above, with the comment
#: «a bare prefix is not a route either» — and they passed, because all three
#: answered 404. On 2026-09-18 the Caddyfile grew ONE rule that redirects a bare
#: prefix to its slash form with a **308** (permanent, and it preserves the
#: method: a POST to a bare prefix must not become a GET).
#:
#: So the old assertion became wrong about what the node MEANS. `/em` and
#: `/catalog` kept passing only by accident — `call` follows redirects and their
#: slash form is itself a 404 — while `/chat` failed, because `/chat/` is a real
#: page. A test that passes by accident on two of three is not a test.
#:
#: What is asserted here is the new, decided behaviour, and it is stricter than
#: the old one: the bare form answers **308** (not a page, not a 200 banner —
#: the original protection is intact) and it points at its own slash form.
#:
#: `/iiif` is NOT here on purpose: Cantaloupe strips the trailing slash and
#: 301s `/iiif/` back to `/iiif`, so redirecting it made an infinite loop, and
#: neither spelling is an endpoint there. The Caddyfile names it with its
#: measurements.
BARE_PREFIXES = ["/em", "/catalog", "/chat", "/assets", "/auth"]


def main() -> int:
    args = arguments(__doc__)
    base = args.base.rstrip("/").removesuffix("/v1").removesuffix("/em")
    tally = Tally()

    print(f"\n1 · the root is a DOOR, for a person\n   {base}/")
    # It used to be a banner listing routes. It is now a redirect to the node's
    # front door — still "for a person", and more so: the banner told you the
    # paths, the door takes you to a page that knows what this node offers.
    # What has NOT changed is the split this file exists to defend: the root is
    # answered, everything else unrouted is 404.
    # `call` FOLLOWS redirects (urllib does), so what is measured here is where a
    # person LANDS — which is the better measurement anyway: the root is not
    # interesting, the door it opens is.
    status, _headers, body = call("GET", f"{base}/")
    text = body.decode("utf-8", "replace")
    tally.ok(status == 200, "GET / lands somewhere", f"status {status}")
    tally.ok("<title>This node" in text,
             "…and that somewhere is the node's front door", text[:80])
    # a query string is not a different path: the root matcher is exact, not
    # prefix — which is the whole reason this can be split from the 404 below
    status, _headers, _body = call("GET", f"{base}/?anything=1")
    tally.ok(status == 200, "GET /?x=1 is still the root")

    print("\n2 · anything unrouted is 404, for a program")
    for path in UNROUTED:
        status, _headers, body = call("GET", f"{base}{path}")
        tally.ok(status == 404, f"GET {path}", f"status {status}")
        # …and it must not look like success to a parser
        tally.ok(b"em dev stack" not in body,
                 f"…{path} is not answered with the banner")
        # …nor with a redirect to the door, which a parser would follow into HTML
        tally.ok(b"<html" not in body.lower(),
                 f"…{path} is not answered with a page")
    print("\n2b · a bare prefix is a route missing its slash, not a miss")
    # `call` FOLLOWS redirects (urllib does), which is right everywhere else in
    # this file — what matters is usually where a person LANDS. Here the redirect
    # ITSELF is the thing under test, so this opener refuses to follow one.
    class _NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_k):
            return None

    # …and the SAME TLS context `call` uses, imported rather than copied: the
    # dev stack's Caddy has an internal CA, and an opener without it answers
    # `CERTIFICATE_VERIFY_FAILED` — which is exactly the trap `smoke.py` fell
    # into on the same day, one helper away from this one. A third copy of the
    # decision would be a third place to get it wrong.
    _opener = urllib.request.build_opener(
        _NoFollow, urllib.request.HTTPSHandler(context=_TLS))

    def _first_answer(url: str):
        try:
            with _opener.open(urllib.request.Request(url), timeout=10) as answer:
                return answer.status, dict(answer.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers)

    for prefix in BARE_PREFIXES:
        status, headers = _first_answer(f"{base}{prefix}")
        tally.ok(status == 308, f"GET {prefix} is a 308",
                 f"status {status} — 301/302 would let a browser turn a POST "
                 f"into a GET, which is the loss this chose 308 to avoid")
        tally.ok(headers.get("Location", "") == f"{prefix}/",
                 f"…and it points at {prefix}/",
                 headers.get("Location", "(no Location)"))

    # The one that cost an afternoon: a POST to a plausible-looking path.
    status, _headers, body = call("POST", f"{base}/v1/say",
                                  json_body={"transcript": "crea una nuova scheda"})
    tally.ok(status == 404,
             "POST /v1/say — the miss that used to answer 200 and be queued "
             "for ever", f"status {status}")

    print("\n3 · the real routes are untouched")
    for path, want, what in ROUTED:
        status, _headers, _body = call("GET", f"{base}{path}")
        tally.ok(status == want, f"GET {path} — {what}", f"status {status}")
    # IIIF answers its own 404 for an object that is not there, and that is the
    # point: the answer comes from Cantaloupe, not from the fallback.
    status, _headers, body = call(
        "GET", f"{base}/iiif/3/nosuchdigest/info.json")
    tally.ok(status == 404, "GET /iiif/3/<missing>/info.json is 404")
    tally.ok(b"no such route" not in body,
             "…answered by the image server, not by the node's fallback")

    print("\n4 · EMStudio, on this origin")
    for path, what in EMSTUDIO:
        status, headers, body = call("GET", f"{base}{path}")
        kind = (headers.get("content-type") or "").lower()
        if status == 200:
            tally.ok("text/html" in kind, f"GET {path} — {what} is a page", kind)
            # …and it must be EMStudio rather than the node's own HTML: the point
            # of the route is that the editor is HERE, on the catalogue's origin
            tally.ok(b"EMStudio" in body or b"emstudio" in body.lower(),
                     f"…{path} is served by EMStudio itself", body[:60])
        else:
            # the dev server is down, which is a normal state — so what is
            # measured is the HONESTY of the answer
            tally.ok(status == 502,
                     f"GET {path} — dev server down → 502, not 200", f"status {status}")
            tally.ok(b"5173" in body,
                     f"…and the 502 SAYS what to start", body[:80])
            tally.ok(b"<html" not in body.lower(),
                     f"…and is not a page a parser would follow")
    # The route order that makes all of this possible: `/em/studio/*` is a longer
    # prefix than `/em/*`, so Caddy must prefer it — and `/em/v1/...` must still
    # reach the room server. Asserted because a reordering would break one of the
    # two silently.
    status, _headers, _body = call("GET", f"{base}/em/v1/health")
    tally.ok(status == 200,
             "…and /em/v1/health still reaches the ROOM server: the more "
             "specific prefixes did not swallow /em/*", f"status {status}")

    return tally.report("routing")


if __name__ == "__main__":
    raise SystemExit(main())
