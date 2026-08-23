"""The invite link: how somebody gets into a room without an admin typing their
ORCID.

The decision this implements, in E.D.'s words: **the link is the invitation, the
ORCID is the identity, and the role comes from the ACL.** Each of the three does
one job, and the failure of any one of them is a refusal rather than a hole:

* **the link opens the door.** It names a room and the role its author is
  offering. It authenticates nobody — a URL that granted access by itself would
  be a password that gets pasted into chat rooms;
* **the ORCID is who came in.** The join goes through `auth.py`, the existing
  Keycloak/OIDC path, unchanged: no token, no entry (401). This is the one point
  where the invite and the identity meet, and neither substitutes for the other;
* **the ACL says what they may do.** Accepting an invite WRITES the offered role
  into the room's access list (`access.py`) — it does not create a parallel
  permission. A second later, the ACL is the only thing that has to be read to
  know what this person may do, and `may_assign` is still the only rule that says
  who could have offered it.

Two consequences worth stating because they are easy to get wrong:

**An invite never demotes.** Somebody who is already an editor and follows a
viewer link stays an editor. An invitation is an offer of access, not a statement
about what somebody's access should be reduced to — and a link forwarded to the
wrong person must not be able to take rights away.

**The secret is not stored.** A token is `<id>.<secret>`; the record keeps the id
in the clear and only a **sha256 of the secret**, compared in constant time. The
store therefore holds no working links: a leaked invite file is a list of rooms
somebody was invited to, not a set of keys. Same reasoning as the asset store's
digests, applied to a credential.

What is deliberately NOT here: an invite that grants `owner` or `admin` (see
`offerable`), an invite that survives revocation, and any notion of "guest"
access without an identity. A room's members are people with ORCIDs; a link is
how one of them is reached, not a way around being one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .access import Role, may_assign, parse_role

#: How long an invite lasts when nobody says. Seven days: long enough to reach
#: somebody who reads their mail on Monday, short enough that a link in an old
#: thread is not a way in a year from now.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600

#: The roles a link may carry. `owner` and `admin` are absent ON PURPOSE: those
#: are handed over by a person, to a person they have identified, and a URL is
#: not a person. `may_assign` still gates who may offer what within these two.
OFFERABLE: Tuple[Role, ...] = (Role.VIEWER, Role.EDITOR)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now() -> int:
    return int(time.time())


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass
class Invite:
    """One invitation. Thin, like the room record — and for the same reason."""

    token_id: str
    room_id: str
    #: the role this link offers, as a word (`viewer` / `editor`)
    role: str
    #: sha256 of the secret half of the token. The secret itself exists exactly
    #: once, in the answer to the request that created it.
    secret_digest: str
    created_by: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    #: unix seconds; None = never expires (a deliberate choice a caller has to
    #: make explicitly, not a default)
    expires_at: Optional[int] = None
    #: None = unlimited. A team link is used by six people; a one-person invite
    #: is `max_uses=1` and stops being a way in the moment it is used.
    max_uses: Optional[int] = None
    uses: int = 0
    revoked_at: Optional[str] = None
    #: who accepted it, in order. Not an audit log — the ACL is the record of
    #: access — but the answer to "who did this link let in", which is the
    #: question somebody asks when they regret having sent it.
    accepted_by: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Invite":
        return cls(
            token_id=str(raw.get("token_id") or ""),
            room_id=str(raw.get("room_id") or ""),
            role=str(raw.get("role") or Role.VIEWER.value),
            secret_digest=str(raw.get("secret_digest") or ""),
            created_by=(str(raw["created_by"]) if raw.get("created_by") else None),
            created_at=str(raw.get("created_at") or now_iso()),
            expires_at=(int(raw["expires_at"]) if raw.get("expires_at") else None),
            max_uses=(int(raw["max_uses"]) if raw.get("max_uses") else None),
            uses=int(raw.get("uses") or 0),
            revoked_at=(str(raw["revoked_at"]) if raw.get("revoked_at") else None),
            accepted_by=[str(x) for x in (raw.get("accepted_by") or [])])

    def as_dict(self) -> Dict[str, Any]:
        return {"token_id": self.token_id, "room_id": self.room_id,
                "role": self.role, "secret_digest": self.secret_digest,
                "created_by": self.created_by, "created_at": self.created_at,
                "expires_at": self.expires_at, "max_uses": self.max_uses,
                "uses": self.uses, "revoked_at": self.revoked_at,
                "accepted_by": list(self.accepted_by)}

    def as_public(self) -> Dict[str, Any]:
        """What an admin sees when listing. **No digest**: it is not a secret
        somebody can use, but it is also not something a UI has any reason to
        carry around."""
        out = self.as_dict()
        out.pop("secret_digest", None)
        out["state"] = self.state()
        return out

    # ── the three ways a link stops working ────────────────────────────────

    def state(self, *, now: Optional[int] = None) -> str:
        if self.revoked_at:
            return "revoked"
        moment = now if now is not None else _now()
        if self.expires_at is not None and moment > self.expires_at:
            return "expired"
        if self.max_uses is not None and self.uses >= self.max_uses:
            return "used up"
        return "live"

    def matches(self, secret: str) -> bool:
        """Constant-time, because a timing difference on a secret comparison is
        a way to guess it one character at a time."""
        return hmac.compare_digest(self.secret_digest, _digest(secret))


# ── where invites are kept ───────────────────────────────────────────────────

class InviteStore(Protocol):
    """One room's invites, as a `{token_id: record}` map. The same two methods
    as the ACL store, for the same reason: anything richer would be policy in
    the wrong place."""

    def get(self, room_id: str) -> Optional[Dict[str, Any]]: ...

    def put(self, room_id: str, records: Dict[str, Any]) -> None: ...

    def ids(self) -> List[str]: ...


class InMemoryInviteStore:
    """Tests and a laptop. Dies with the process — which for a credential store
    is the least bad kind of loss: the links stop working."""

    def __init__(self) -> None:
        self._data: Dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, room_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            raw = self._data.get(room_id)
        return json.loads(raw) if raw is not None else None

    def put(self, room_id: str, records: Dict[str, Any]) -> None:
        blob = json.dumps(records, sort_keys=True, ensure_ascii=False)
        with self._lock:
            self._data[room_id] = blob

    def ids(self) -> List[str]:
        with self._lock:
            return sorted(self._data)


class DirectoryInviteStore:
    """A directory of `<room>.invites.json`, written atomically, beside the ACLs.

    Readable by whoever can read the ACLs — and holding no usable link, which is
    why that is acceptable.
    """

    def __init__(self, root: str) -> None:
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, room_id: str) -> pathlib.Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in room_id)
        return self.root / f"{safe}.invites.json"

    def get(self, room_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(room_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, room_id: str, records: Dict[str, Any]) -> None:
        path = self._path(room_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1,
                                  sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def ids(self) -> List[str]:
        return sorted(p.name[: -len(".invites.json")]
                      for p in self.root.glob("*.invites.json"))


def invite_store_from_env(environ: Optional[Dict[str, str]] = None) -> InviteStore:
    env = environ if environ is not None else os.environ
    directory = (env.get("EM_INVITE_DIR") or env.get("EM_ACL_DIR")
                 or env.get("EM_SNAPSHOT_DIR"))
    if directory:
        return DirectoryInviteStore(directory)
    return InMemoryInviteStore()


def describe(store: InviteStore) -> str:
    return {
        "InMemoryInviteStore": "memory (not durable — links die with the process)",
        "DirectoryInviteStore": "directory (beside the ACLs)",
    }.get(type(store).__name__, type(store).__name__)


# ── the register ─────────────────────────────────────────────────────────────

class Invites:
    """The invitations of one instance, by room. A thin front over the store."""

    def __init__(self, store: InviteStore) -> None:
        self.store = store

    def _load(self, room_id: str) -> Dict[str, Invite]:
        raw = self.store.get(room_id) or {}
        out: Dict[str, Invite] = {}
        for token_id, record in raw.items():
            invite = Invite.from_dict({**record, "token_id": token_id,
                                       "room_id": room_id})
            out[token_id] = invite
        return out

    def _save(self, room_id: str, invites: Dict[str, Invite]) -> None:
        self.store.put(room_id, {k: v.as_dict() for k, v in invites.items()})

    def list(self, room_id: str) -> List[Invite]:
        return [invites for _, invites in sorted(self._load(room_id).items())]

    def mint(self, room_id: str, role: Role, *, created_by: Optional[str] = None,
             ttl_seconds: Optional[int] = DEFAULT_TTL_SECONDS,
             max_uses: Optional[int] = None) -> Tuple[Invite, str]:
        """Create an invitation. Returns `(invite, token)` — and the token is the
        ONLY time the secret exists outside somebody's clipboard."""
        token_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(24)
        invite = Invite(
            token_id=token_id, room_id=room_id, role=role.value,
            secret_digest=_digest(secret), created_by=created_by,
            expires_at=(_now() + int(ttl_seconds)) if ttl_seconds else None,
            max_uses=max_uses)
        invites = self._load(room_id)
        invites[token_id] = invite
        self._save(room_id, invites)
        return invite, f"{token_id}.{secret}"

    def revoke(self, room_id: str, token_id: str) -> Optional[Invite]:
        """Stop a link. Idempotent, and it KEEPS the record: an invitation that
        was revoked is a thing that happened, and deleting it would erase the
        answer to "was that link ever live?"."""
        invites = self._load(room_id)
        invite = invites.get(token_id)
        if invite is None:
            return None
        if not invite.revoked_at:
            invite.revoked_at = now_iso()
            self._save(room_id, invites)
        return invite

    def resolve(self, token: str) -> Tuple[Optional[Invite], Optional[str]]:
        """`(invite, refusal)` for a token — the room is FOUND from the token.

        A refusal is a sentence, and the sentences are deliberately the same for
        "no such invite" and "wrong secret": telling the difference apart would
        let somebody with a token id learn whether they had guessed it.
        """
        token = (token or "").strip()
        if "." not in token:
            return None, "that is not an invitation link"
        token_id, _, secret = token.partition(".")
        for room_id in self.store.ids():
            invite = self._load(room_id).get(token_id)
            if invite is None:
                continue
            if not invite.matches(secret):
                return None, "that invitation is not valid"
            state = invite.state()
            if state != "live":
                return None, f"that invitation is {state}"
            return invite, None
        return None, "that invitation is not valid"

    def record_use(self, invite: Invite, orcid: Optional[str]) -> Invite:
        """Count an acceptance. Called after the ACL was actually written, so a
        link is never spent by a join that did not happen."""
        invites = self._load(invite.room_id)
        held = invites.get(invite.token_id)
        if held is None:
            return invite
        held.uses += 1
        if orcid and orcid not in held.accepted_by:
            held.accepted_by.append(orcid)
        self._save(invite.room_id, invites)
        return held


def offerable(actor: Optional[Role], role: Role) -> Optional[str]:
    """None when this actor may offer this role by link, else the reason.

    Two rules, and the second one is the whole reason this function exists rather
    than a bare `role in OFFERABLE`:

    1. a link may only carry `viewer` or `editor` — an owner or an admin is
       somebody a person hands the room to, and a URL is not a person;
    2. beyond that it is **`may_assign`, unchanged**: whoever could not grant the
       role directly cannot grant it through a link either. A second permission
       rule for invitations would be a way around the first one.
    """
    if role not in OFFERABLE:
        return (f"a link cannot offer {role.value}: only "
                f"{' or '.join(r.value for r in OFFERABLE)} — an owner or an "
                f"admin is handed the room by a person, not by a URL")
    return may_assign(actor, None, role)


def parse_offer(raw: Any) -> Optional[Role]:
    """The role a request asks for, or None. Never a guess (same rule as
    `access.parse_role`, which this delegates to)."""
    return parse_role(raw)
