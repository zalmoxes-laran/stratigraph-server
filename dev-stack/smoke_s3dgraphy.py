#!/usr/bin/env python3
"""One s3Dgraphy in the stack — measured on what RUNS, not on what is written.

The three services share `em.json` files and one semantic vocabulary, and they
install s3Dgraphy separately because their EXTRAS legitimately differ. What must
not differ is the VERSION. On 2026-08-30 it did: the room server's image carried
`==1.6.0.dev12` while the catalogue and the field assistant had drifted to dev16
behind two `>=` specs that moved on their own at every rebuild. Nothing broke,
because the differences were additive — the day they are not, the symptom is a
study the catalogue indexes differently from how the server wrote it, and that is
a very long bug to chase.

**Why this reads the running services and not the Dockerfiles.** A text
comparison answers "what is written", and the question is "what runs". They are
not the same question, and this stack proves it twice over:

* the `--local-s3d` overlay mounts the s3Dgraphy CHECKOUT and puts it first on
  `PYTHONPATH`, so what runs is the checkout and not the wheel — and because the
  mounted source carries `s3dgraphy.egg-info`, it shadows the installed
  distribution's METADATA too. Measured: on such a stack all three answered
  dev16 while the server's image held dev12. The divergence was invisible
  exactly on the stack somebody is running;
* so part 2 below looks at the IMAGES as well, which is the other half of the
  truth, and says clearly when it cannot.

    python dev-stack/smoke_s3dgraphy.py
    python dev-stack/smoke_s3dgraphy.py --base https://em.localhost:8443/em/v1
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoke_common import Tally, arguments, body_of, call   # noqa: E402

#: The three faces of the stack that speak the graph language, and where each
#: publishes the version it is running. Every one of them says it in `/health` —
#: the field assistant learned to on 2026-08-31, and that silence is part of why
#: the drift went unseen.
SERVICES = (
    ("stratigraph-server", "{origin}/em/v1/health", "em-dev-server"),
    ("stratigraph-catalog", "{origin}/catalog/health", "em-dev-catalog"),
    ("stratigraph-chatbot", "{origin}/chat/health", "em-dev-chatbot"),
)


def _origin(base: str) -> str:
    """The stack's origin, from whichever spelling of the base was passed."""
    return base.rstrip("/").removesuffix("/v1").removesuffix("/em")


def _running(origin: str, tally: Tally) -> dict:
    """What each service SAYS it is running, from its own health probe."""
    seen = {}
    for name, template, _container in SERVICES:
        url = template.format(origin=origin)
        status, _headers, raw = call("GET", url)
        if status != 200:
            tally.ok(False, f"{name} answers /health", f"status {status}")
            continue
        version = str((body_of(raw) or {}).get("s3dgraphy") or "")
        if tally.ok(bool(version), f"{name} says which s3Dgraphy it runs",
                    version or "the field is absent or empty"):
            seen[name] = version
    return seen


def _in_images(tally: Tally) -> dict:
    """…and what the IMAGES hold, which is a different fact.

    Asked of the image and not of the container, deliberately: a container may be
    running a mounted checkout that shadows both the code AND the metadata (see
    the module docstring), and then the container's answer says nothing about
    what a fresh `docker-compose up` would produce.
    """
    docker = shutil.which("docker")
    if not docker:
        tally.skip("what the images hold", "no docker on this machine")
        return {}
    held = {}
    for name, _template, _container in SERVICES:
        result = subprocess.run(
            [docker, "run", "--rm", "--entrypoint", "python", f"{name}:dev", "-c",
             "import importlib.metadata as m; print(m.version('s3dgraphy'))"],
            capture_output=True, text=True, timeout=120)
        version = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if not version:
            tally.skip(f"{name}:dev", "no such image built here")
            continue
        held[name] = version
        print(f"    {name}:dev holds {version}")
    return held


def _agree(seen: dict, what: str, tally: Tally) -> None:
    versions = set(seen.values())
    tally.ok(len(versions) <= 1, f"{what}: one version across the stack",
             " · ".join(f"{n}={v}" for n, v in sorted(seen.items())))


def main() -> int:
    args = arguments(__doc__)
    origin = _origin(args.base)
    tally = Tally()

    print(f"\n1 · what the services are RUNNING\n   {origin}")
    running = _running(origin, tally)
    if running:
        _agree(running, "running", tally)

    print("\n2 · what the IMAGES hold, which a mounted checkout can hide")
    held = _in_images(tally)
    if held:
        _agree(held, "images", tally)

    # The two halves must also agree with EACH OTHER, or the stack is running
    # something no rebuild would reproduce. That is legitimate under
    # `--local-s3d` and nowhere else, so it is SAID and not failed.
    if running and held:
        drifted = {n for n in running if n in held and running[n] != held[n]}
        if drifted:
            print("\n   note: " + ", ".join(sorted(drifted))
                  + " run something their image does not hold — a mounted"
                  + " checkout (./fcn-up.sh --local-s3d). Expected there, and"
                  + " nowhere else.")

    return tally.report("s3dgraphy")


if __name__ == "__main__":
    raise SystemExit(main())
