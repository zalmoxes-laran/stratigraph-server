"""What a neighbour can do reaches the front door — and nobody keeps a list.

The field assistant declares its capabilities since 2026-09-02: which
transcription engine, which intent model, and — when they are absent — which
variable would configure them. That declaration was not arriving anywhere a
person could see it: the probe forwarded three fields and the public reduction
dropped the rest. The two readings did not contradict each other — **one was
silent** — and the rule we have been applying for three days,

    if the node has an AI you have functions; if it does not, you do not — and
    the page says so,

stayed a fact internal to one service instead of being what the user sees.

**The property this file defends is the one that matters most, and it is not
"speech and intent appear".** It is that NOBODY KEEPS A LIST: the day a service
declares a third capability it must reach the page without anybody editing the
probe, the reduction, or the page. So the tests below invent capabilities that do
not exist anywhere in the codebase and assert they arrive.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from app import node_health                                       # noqa: E402
from app.node_health import OK, Check, _reduce_capabilities       # noqa: E402


@pytest.fixture()
def client():
    from app import main

    return TestClient(main.app)


def _neighbour(monkeypatch, payload: Dict[str, Any]) -> None:
    """Make the field assistant answer `payload` from its `/health`."""
    monkeypatch.setenv("EM_CHATBOT_INTERNAL", "http://field-assistant.invalid:8000")

    def fake_fetch(url, *, expect_json=False):
        return 200, payload, None

    monkeypatch.setattr(node_health, "_fetch", fake_fetch)


# ── the property that matters: no list, anywhere ─────────────────────────────

def test_a_capability_NOBODY_HAS_HEARD_OF_reaches_the_public_route(client,
                                                                  monkeypatch):
    """`gravimetry` exists in no service, no schema and no page. If it arrives,
    then nothing on this path enumerates capabilities."""
    _neighbour(monkeypatch, {
        "version": "9.9", "accepts_dictation": True,
        "capabilities": [
            {"name": "gravimetry", "state": "active",
             "engine": "grav-o-tron 4 (on this node)", "missing": []},
        ],
    })
    offer = next(o for o in client.get("/v1/node").json()["offers"]
                 if o["name"] == "stratigraph-chatbot")
    assert offer["capabilities"] == [
        {"name": "gravimetry", "state": "active",
         "engine": "grav-o-tron 4 (on this node)", "missing": []},
    ]


def test_a_FIELD_nobody_declared_reaches_it_too(client, monkeypatch):
    """Not only a new capability — a new key on an existing one. This is why the
    route carries `List[Dict]` and not a typed model: a typed model would drop
    it, silently, and forwarding what the neighbour declares is the whole job."""
    _neighbour(monkeypatch, {
        "capabilities": [{"name": "intent", "state": "configured",
                          "policy": "requires a local endpoint"}],
    })
    offer = next(o for o in client.get("/v1/node").json()["offers"]
                 if o["name"] == "stratigraph-chatbot")
    assert offer["capabilities"][0]["policy"] == "requires a local endpoint"


def test_nothing_in_the_probe_or_the_reduction_names_a_capability():
    """The source, read: `speech` and `intent` must not appear as literals on
    this path. A name written down is a list, and a list goes stale."""
    for module in ("node_health.py", "main.py"):
        text = (_REPO / "app" / module).read_text(encoding="utf-8")
        code = re.sub(r"#[^\n]*|'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"", "", text)
        for name in ('"speech"', "'speech'", '"intent"', "'intent'"):
            assert name not in code, f"{module} names {name}"


def test_the_page_names_no_capability_either():
    page = (_REPO / "app" / "rooms_ui" / "rooms.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", page)
    body = code[code.index("function capabilityRow("):]
    body = body[:body.index("\nfunction ")]
    for name in ("speech", "intent", "whisper"):
        assert name not in body, f"the renderer names {name}"
    # …and it loops over whatever arrived
    assert "offer.capabilities || []" in code


# ── it is still a reduction ──────────────────────────────────────────────────

def test_the_reduction_drops_a_shape_it_cannot_vouch_for():
    """A public route that forwarded a nested structure verbatim would be a
    reduction in name only."""
    facts = {"capabilities": [
        {"name": "ok", "engine": "e", "missing": ["A", "B"]},
        {"name": "nested", "detail": {"host": "inside.invalid", "port": 5432}},
        "not an object",
        {"name": "numbers", "missing": [1, 2]},
    ]}
    reduced = _reduce_capabilities(facts)
    assert reduced[0] == {"name": "ok", "engine": "e", "missing": ["A", "B"]}
    assert reduced[1] == {"name": "nested"}, "the nested object was carried"
    assert "inside.invalid" not in json.dumps(reduced)
    assert reduced[2] == {"name": "numbers", "missing": ["1", "2"]}


@pytest.mark.parametrize("declared", [None, "a string", 42, {"name": "x"}])
def test_a_neighbour_that_declares_nonsense_costs_nothing(declared):
    assert _reduce_capabilities({"capabilities": declared}) == []


def test_a_service_that_declares_nothing_carries_an_empty_list(client):
    """Most of them do, and an empty list is not a missing field."""
    for offer in client.get("/v1/node").json()["offers"]:
        assert isinstance(offer["capabilities"], list)


# ── and a service that is DOWN does not take its capabilities' place ────────

def test_an_unreachable_neighbour_says_so_and_declares_nothing(client,
                                                              monkeypatch):
    """The section must say the assistant is down — not fall silent, and not
    print stale capabilities as if it were up."""
    monkeypatch.setenv("EM_CHATBOT_INTERNAL", "http://field-assistant.invalid:8000")

    def refuses(url, *, expect_json=False):
        return 0, None, "connection refused"

    monkeypatch.setattr(node_health, "_fetch", refuses)
    offer = next(o for o in client.get("/v1/node").json()["offers"]
                 if o["name"] == "stratigraph-chatbot")
    assert offer["state"] == "unreachable"
    assert "did not answer" in offer["detail"]
    assert offer["capabilities"] == []


def test_the_probe_still_forwards_what_it_forwarded_before(monkeypatch):
    """`capabilities` was ADDED to the forwarded keys, not swapped in."""
    monkeypatch.setattr(node_health, "_fetch",
                        lambda url, *, expect_json=False: (
                            200, {"version": "1.2", "auth": "keycloak",
                                  "accepts_dictation": True,
                                  "capabilities": [{"name": "x"}]}, None))
    check: Check = node_health._probe_field_assistant("http://x:8000")
    assert check.state == OK
    assert check.facts["version"] == "1.2"
    assert check.facts["auth"] == "keycloak"
    assert check.facts["accepts_dictation"] is True
    assert check.facts["capabilities"] == [{"name": "x"}]
