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

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from smoke_common import Tally, arguments, call  # noqa: E402

#: The routes this node really serves. `/assets` and `/iiif` are answered by
#: MinIO and Cantaloupe, which have their own opinions about a missing object —
#: what matters here is that the answer comes from THEM.
ROUTED = [
    ("/em/v1/health", 200, "the room API"),
    ("/catalog/health", 200, "the catalogue"),
    ("/chat/health", 200, "the field assistant"),
    ("/chat/", 200, "…and its page"),
]

#: Nothing here is a route. Each one used to get 200 and a banner.
UNROUTED = [
    "/inventato",
    "/v1/say",             # the exact path that started this: the assistant's
    "/v1/rooms",           # …and one that LOOKS like the room API without /em
    "/api/anything",
    "/em",                 # a bare prefix is not a route either
    "/catalog",
    "/chat",
]


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

    return tally.report("routing")


if __name__ == "__main__":
    raise SystemExit(main())
