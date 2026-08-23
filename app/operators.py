"""The OPERATOR: whoever looks after this node, as distinct from whoever owns a
room in it.

Two scopes, one contract. A room's owner manages their room — that is
`access.py`, and it is where every per-room decision stays. An **operator** looks
after the *node*: all the rooms, the storage behind them, the lifecycle of the
ones nobody claims. Those are different jobs, held by different people, and the
whole reason this module exists in three dozen lines is that the second must not
be reachable from the first.

**Not self-conferrable, and that is the property to protect.** An owner is
somebody the study named; an operator is somebody the *deployment* named. So the
capability comes from outside every room:

* a **Keycloak realm role** (`em-operator` by default, `EM_OPERATOR_ROLE` to
  rename it) — the right answer for a real deployment: it is granted in the realm
  by whoever administers the realm, and em-server only reads it;
* or an **ORCID allow-list** in the environment (`EM_OPERATORS`) — the same shape
  `EM_CORPUS_CURATORS` already uses, for a node where nobody wants to touch the
  realm to hand out one capability.

Neither can be reached by writing into an ACL, which is the only thing a room's
admin can do. There is deliberately **no endpoint that grants it**: the answer to
"how do I become an operator" is "ask whoever runs the node", and that is the
correct answer.

**Fail-closed.** Nothing configured means nobody is an operator — an empty
allow-list is not "everybody", it is "nobody", and a node that quietly opened its
cross-room console because a variable was unset would be the worst kind of
default. The single exception is **dev mode**, where there is no OIDC and
therefore no identities at all: there, everything is already open by
construction (`ws.authorize` makes dev mode `owner`; `corpus.may_read_whole`
makes it a curator), and pretending otherwise would be a lock drawn on a door
with no wall.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

#: The realm role that carries the capability. Renamable because a realm shared
#: with other services may already have a naming convention.
DEFAULT_ROLE = "em-operator"


def _norm(orcid: Any) -> Optional[str]:
    """One spelling for an identity — `access._norm`'s rule, reused so the two
    lists cannot disagree about whether a URL and a bare iD are the same person."""
    from .access import _norm as canonical
    return canonical(orcid)


def operator_role(environ: Optional[Dict[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    return (env.get("EM_OPERATOR_ROLE") or DEFAULT_ROLE).strip() or DEFAULT_ROLE


def operators(environ: Optional[Dict[str, str]] = None) -> List[str]:
    """The ORCIDs this node calls operators. Empty means nobody, on purpose."""
    env = environ if environ is not None else os.environ
    raw = env.get("EM_OPERATORS") or ""
    return [o for o in (_norm(part) for part in raw.split(",")) if o]


def _roles_in(claims: Dict[str, Any]) -> Iterable[str]:
    """Every role name a Keycloak token carries.

    Both places Keycloak puts them: `realm_access.roles` (realm roles) and
    `resource_access.<client>.roles` (client roles). Reading only the first would
    make the capability un-grantable on a realm that scopes roles per client,
    which is a configuration somebody else's admin chose and we do not get to
    veto.
    """
    realm = (claims.get("realm_access") or {}).get("roles") or []
    yield from (str(r) for r in realm)
    for client in (claims.get("resource_access") or {}).values():
        for role in (client or {}).get("roles") or []:
            yield str(role)
    # …and a flat `roles` claim, which some realms map instead
    for role in claims.get("roles") or []:
        yield str(role)


def is_operator(claims: Optional[Dict[str, Any]], *,
                environ: Optional[Dict[str, str]] = None) -> bool:
    """May this caller act on the NODE (all rooms, storage, lifecycle)?

    Reads the token, never a room: no ACL, no room id, nothing an owner could
    write. Dev mode is yes — see the module docstring for why that is honest
    rather than a hole.
    """
    if not claims:
        return False
    if claims.get("em_dev_mode"):
        return True
    wanted = operator_role(environ)
    if any(role == wanted for role in _roles_in(claims)):
        return True
    who = _norm(claims.get("orcid") or claims.get("preferred_username")
                or claims.get("sub"))
    return bool(who) and who in operators(environ)


def refusal(environ: Optional[Dict[str, str]] = None) -> str:
    """The 403's words: what is missing and who can give it.

    Named rather than "forbidden", because the person reading it is usually the
    node's own owner wondering why their own console will not open — and the
    answer is that owning a room is not running a node.
    """
    role = operator_role(environ)
    return ("this is the node's console, and it needs the OPERATOR capability — "
            f"the realm role «{role}», or an entry in EM_OPERATORS. Owning a room "
            "does not grant it (and no endpoint here can): ask whoever runs this "
            "node. Per-room management lives in the room's own API.")


def describe(environ: Optional[Dict[str, str]] = None) -> str:
    """A word for `/v1/health`: how this node recognises an operator.

    No names in it. A health endpoint open enough to be a probe is open enough to
    be a screenshot, and a list of the people who can administer the node is not
    something to publish.
    """
    names = operators(environ)
    role = operator_role(environ)
    if names:
        return f"realm role «{role}» or {len(names)} allow-listed ORCID(s)"
    return f"realm role «{role}» only (EM_OPERATORS is empty: fail-closed)"
