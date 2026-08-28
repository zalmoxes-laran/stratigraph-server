"""What every smoke needs, once.

The older smokes each carry their own `call` / `check` / `token_for`, and they are
left alone — a working measurement is not worth rewriting. The four smokes added
with the user-management work import from here instead, because the fourth copy of
`urllib.request.Request(...)` is the one that starts to drift: the header-casing
bug documented in `smoke_corpus_resident.call` was found once and would have to be
found again in every copy.

Nothing here decides anything. It calls, it counts, and it prints — the smokes
themselves hold the questions.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

HERE = pathlib.Path(__file__).resolve().parent

#: The https dev-stack, which is what `fcn-up.sh` brings up. Override with
#: `--base` for the direct port (`http://localhost:8000`).
DEFAULT_BASE = "https://em.localhost:8443/em/v1"


class Tally:
    """Checks passed and failed, and the exit code that follows from them."""

    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []
        self.skips: list[str] = []

    def ok(self, condition: bool, what: str, detail: str = "") -> bool:
        self.checks += 1
        mark = "✓" if condition else "✗"
        print(f"  {mark} {what}" + (f" — {detail}" if detail else ""))
        if not condition:
            self.failures.append(what)
        return bool(condition)

    def skip(self, what: str, why: str) -> None:
        print(f"  · SKIP {what} — {why}")
        self.skips.append(f"{what}: {why}")

    def report(self, title: str) -> int:
        print(f"\n{title}: {self.checks} checks, {len(self.failures)} failed"
              + (f", {len(self.skips)} skipped" if self.skips else ""))
        for failure in self.failures:
            print(f"  ✗ {failure}")
        return 1 if self.failures else 0


#: The dev stack's Caddy uses an internal CA. A smoke that refused to talk to it
#: would be a smoke nobody runs; the certificate is the operator's own, on their
#: own machine, and the alternative is `--base http://localhost:8000`.
_TLS = ssl.create_default_context()
_TLS.check_hostname = False
_TLS.verify_mode = ssl.CERT_NONE


def call(method: str, url: str, *, token: Optional[str] = None,
         json_body: Optional[Dict[str, Any]] = None,
         data: Optional[bytes] = None,
         media_type: Optional[str] = None) -> Tuple[int, Dict[str, str], bytes]:
    """(status, lowercased headers, body). NEVER raises on an HTTP error: the
    status is the measurement, and an exception would hide the 403 we came for."""
    body = data
    headers: Dict[str, str] = {}
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if media_type:
        headers["Content-Type"] = media_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, context=_TLS, timeout=30) as answer:
            return answer.status, _lower(answer.headers), answer.read()
    except urllib.error.HTTPError as exc:
        return exc.code, _lower(exc.headers), exc.read()
    except urllib.error.URLError as exc:
        print(f"    (cannot reach {url}: {exc.reason})")
        return 0, {}, b""


def _lower(headers: Any) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def body_of(raw: bytes) -> Any:
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return {"detail": raw.decode("utf-8", "replace")}


def detail_of(raw: bytes) -> str:
    payload = body_of(raw)
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload)
    return str(payload)


def token_for(user: Optional[str] = None) -> Optional[str]:
    """A dev-realm token, through `token.sh` — the same grant every other script
    here uses, so a failure is one thing to fix and not two."""
    args = [str(HERE / "token.sh")] + (["--user", user] if user else [])
    try:
        out = subprocess.run(args, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"    (no token for {user or 'dev'}: {exc})")
        return None
    lines = [line for line in out.stdout.strip().splitlines() if line.strip()]
    return lines[-1] if lines else None


def orcid_of(jwt: Optional[str]) -> Optional[str]:
    """Who a token says it is. Decoded, never verified — StratiGraph Server checks the
    signature and it is right to be the only one that does."""
    if not jwt or "." not in jwt:
        return None
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:                                    # noqa: BLE001
        return None
    return (claims.get("orcid") or claims.get("preferred_username")
            or claims.get("sub"))


def arguments(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help=f"the API base (default {DEFAULT_BASE})")
    parser.add_argument("--owner", default=None,
                        help="the user whose token owns the room (default: dev)")
    parser.add_argument("--viewer", default="viewer",
                        help="a second realm user, with no grant to start with")
    parser.add_argument("--operator", default=None,
                        help="a realm user with the operator capability "
                             "(default: the owner, which on a dev stack IS one)")
    return parser.parse_args()


def alive(base: str) -> bool:
    status, _, _ = call("GET", base.rstrip("/") + "/health")
    if status == 200:
        return True
    print(f"the node at {base} does not answer /health ({status}). Bring the "
          f"stack up:\n  cd dev-stack && ./fcn-up.sh")
    return False


def unique(prefix: str) -> str:
    """A room id nobody else is using. Time-based, so two runs never collide and
    a leftover is obviously a leftover."""
    import time
    return f"{prefix}-{int(time.time())}"


def need(value: Any, what: str) -> Any:
    if not value:
        print(f"cannot run: {what}")
        sys.exit(2)
    return value
