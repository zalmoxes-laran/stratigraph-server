"""What this build calls itself, and what it says it can speak.

Two different questions, and the distinction is the point of the whole change:

* **`version`** is a number FOR PEOPLE. It is the coordinate somebody puts in a
  test report — «on dev2 this no longer happens» is information, «on the latest
  version» is not.
* **`speaks`** is a triple FOR MACHINES: the em.json schema, the connections
  datamodel (the EM language itself), and the connector API. A peer reads it to
  decide whether the two of them understand each other.

The convention for the first is not this service's to invent:

    <major EM>.<minor EM>.<the tool's own iteration>

and **a tool cannot be more stable than the language it speaks**. So the first
two segments are s3Dgraphy's, and this file refuses a build that claims
otherwise — read off the installed library rather than typed here, because a
number typed in a test is a number that agrees with itself for ever.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import app

REPO = pathlib.Path(__file__).resolve().parent.parent


def _declared_in_pyproject() -> str:
    text = (REPO / "pyproject.toml").read_text()
    # The `[project]` table's own `version`, not one from a dependency pin: the
    # match is anchored to a line start inside the first table.
    found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert found, "pyproject.toml declares no version"
    return found.group(1)


# ── 1 · one number, in two places that cannot import each other ─────────────

def test_pyproject_and_the_package_agree():
    """Static metadata cannot import, so the string is repeated once — and a
    repetition without a guard is a divergence with a date on it."""
    assert _declared_in_pyproject() == app.__version__, (
        f"pyproject.toml says {_declared_in_pyproject()!r} and "
        f"app/__init__.py says {app.__version__!r}. One of them is what gets "
        f"installed and the other is what the node reports about itself.")


def test_main_does_not_declare_a_version_of_its_own():
    """There were two Python literals in this package until 2026-09-18.

    `app/main.py` had its own `__version__ = "0.1.0.dev0"` beside
    `app/__init__.py`'s. Two literals in one package is one more than the number
    that can stay right, so main.py imports it — and this refuses the second
    literal coming back.
    """
    import tokenize

    kept, previous = [], tokenize.INDENT
    with open(REPO / "app" / "main.py") as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and previous in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT):
                continue
            kept.append(token.string)
            previous = token.type
    code = " ".join(kept)

    assert not re.search(r'__version__ \s* = \s* [\'"]', code.replace(" ", " ")), (
        "app/main.py assigns a literal to __version__ again: import it from "
        "`app` instead, where the convention is documented")
    from app import main
    assert main.__version__ is app.__version__


# ── 2 · the number is not more stable than the language ─────────────────────

def test_the_first_two_segments_are_the_language_s_own():
    """`1.6.x` because s3Dgraphy is `1.6.x`.

    Read off the installed library, so the day s3Dgraphy moves to 1.7 this test
    says so instead of the version quietly lying about which EM this build
    speaks.
    """
    import s3dgraphy

    library = getattr(s3dgraphy, "__version__", "")
    if not library:
        pytest.skip("this s3dgraphy declares no __version__")

    mine = app.__version__.split(".")[:2]
    theirs = library.split(".")[:2]
    assert mine == theirs, (
        f"this build calls itself {app.__version__} while it speaks s3Dgraphy "
        f"{library}. The first two segments declare which Extended Matrix "
        f"language a tool speaks, and a tool cannot be more stable than its "
        f"language.")


def test_the_version_is_no_longer_the_never_versioned_default():
    """`0.1.0.dev0` meant «nobody has ever set this»."""
    assert app.__version__ != "0.1.0.dev0"
    assert not app.__version__.startswith("0."), (
        "a 0.x version says this tool speaks Extended Matrix 0.x, which is not "
        "a language that exists")


# ── 3 · the triple, and one reader for it ───────────────────────────────────

def test_the_triple_comes_from_the_library_that_owns_it():
    from app.main import _speaks

    spoken = _speaks()
    assert set(spoken) == {"emjson", "datamodel", "connector_api"}, spoken
    assert all(spoken.values()), (
        f"a slot is empty: {spoken}. `None` means «not declared», which a "
        f"handshake must treat as unknown rather than as compatible.")

    # …and it is not composed here: the same values, from the contract itself
    from s3dgraphy.contract.connector import current_versions
    assert spoken == current_versions().as_dict()


def test_asking_what_it_speaks_cannot_take_the_liveness_check_down(monkeypatch):
    """`/health` is what a probe calls.

    A liveness check that raises because a contract module moved would report
    the node as down in every dashboard while the node was fine. So the triple
    degrades to «not declared» and the node still answers.
    """
    import app.main as main

    def explode():
        raise ImportError("current_versions moved")

    monkeypatch.setattr("s3dgraphy.contract.connector.current_versions",
                        explode)
    assert main._speaks() == {}


def test_both_faces_report_the_triple_and_the_version():
    """`/node` for the page, `/health` for a probe — and they must agree.

    Called through the app so the response MODEL is exercised too: a field that
    is not on the model is silently dropped by FastAPI, which is exactly the
    kind of absence nobody notices.
    """
    from fastapi.testclient import TestClient

    from app.main import app as application

    client = TestClient(application)
    for route in ("/v1/health", "/v1/node"):
        answer = client.get(route)
        assert answer.status_code == 200, (route, answer.text)
        body = answer.json()
        assert body["version"] == app.__version__, route
        assert body.get("speaks"), (
            f"{route} does not report the triple — if the field is missing from "
            f"the response model, FastAPI drops it without complaining")
        assert set(body["speaks"]) == {"emjson", "datamodel", "connector_api"}
