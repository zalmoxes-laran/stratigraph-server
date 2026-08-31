"""The corpus is parsed once, and the browser gets a neighbourhood — not the register.

Two things, and they answer two different worries.

**Parsing.** At some tens of thousands of nodes the corpus document is 10–30 MB.
Building it into a graph once is a second; building it at every click is what
drowns a mini-PC that is also grinding photogrammetry. The mechanism was already
in the house: `version()` is the sha256 of the canonical JSON of the whole
register. So the PARSE is memoised against it — and **not the content**, which is
the distinction the class docstring insists on: the bytes are re-read every time,
and a licence somebody edits invalidates the parse on the very next call.

**The answer's size.** A digest is a citation, so asking about a file you hold is
what the register is for; the LOT is a curation read and stays refused. The
neighbourhood needs no rule to keep it small: only the three DTC chain edges are
walked, and anything reached by a `has_*` is an attribute of the node it hangs
off. So the tests here measure the SECOND access, not the first — and that a
caller who is not a curator gets a story and not a register.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import corpus as corpus_module                        # noqa: E402
from app import main as main_module                            # noqa: E402
from app.corpus import InMemoryCorpusStore, ResidentCorpus     # noqa: E402

BRUNO = "0000-0001-5109-3700"


def digest(n: int) -> str:
    return "sha256:" + f"{n:02x}" * 32


def dig_corpus() -> dict:
    """A corpus with a real chain in it, built through the library.

    Two photographs, one alignment, two meshes; one mesh goes on into an
    orthophoto. And an author on the event — the context that must arrive as an
    attribute and not as a place to walk to.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent
                           / "s3Dgraphy" / "src"))
    from s3dgraphy import api as em
    from s3dgraphy.dtc import DTC_CORPUS_COLLECTION
    from s3dgraphy.graph import Graph
    from s3dgraphy.nodes.dtc_process_node import DTCProcessNode
    from s3dgraphy.nodes.resource_node import ResourceNode

    g = Graph(graph_id="dtc")
    for i in (0, 9):
        g.add_node(ResourceNode(f"img{i}", name=f"IMG_000{i}.jpg",
                                checksum=digest(i), residency="resident"))
        g.add_node(ResourceNode(f"mesh{i}", name=f"mesh{i}.ply",
                                checksum=digest(100 + i), residency="resident"))
    g.add_node(ResourceNode("ortho0", name="ortho0.tif",
                            checksum=digest(200), residency="resident"))
    for pid, name in (("proc-align", "Alignment"), ("proc-ortho", "Orthophoto")):
        g.add_node(DTCProcessNode(pid, name=name, dtc_kind="transformation"))
    for i in (0, 9):
        g.add_edge(f"in{i}", "proc-align", f"img{i}", "dtc_had_input")
        g.add_edge(f"out{i}", "proc-align", f"mesh{i}", "dtc_had_output")
        g.add_edge(f"der{i}", f"mesh{i}", f"img{i}", "dtc_derived_from")
    g.add_edge("in-o", "proc-ortho", "mesh0", "dtc_had_input")
    g.add_edge("out-o", "proc-ortho", "ortho0", "dtc_had_output")
    g.add_edge("der-o", "ortho0", "mesh0", "dtc_derived_from")

    from s3dgraphy.nodes.author_node import AuthorNode

    g.add_node(AuthorNode("author-bruno", name="Bruno", surname="B.", orcid=BRUNO))
    g.add_edge("auth", "proc-align", "author-bruno", "has_author")

    section = em.graph_to_emjson(g)["graph"]
    section.setdefault("data", {})["em_collection"] = DTC_CORPUS_COLLECTION
    return section


@pytest.fixture()
def node(monkeypatch):
    """A server whose corpus is the little dig above."""
    store = InMemoryCorpusStore()
    store.put(dig_corpus())
    resident = ResidentCorpus(store)
    monkeypatch.setattr(main_module, "CORPUS_STORE", store)
    monkeypatch.setattr(main_module, "RESIDENT", resident)
    return resident


@pytest.fixture()
def client():
    return TestClient(main_module.app)


# ── 1 · parsed once, revalidated by version ─────────────────────────────────

def test_the_second_access_does_NOT_re_parse(node):
    """The measurement that matters is the second one: the first has to build."""
    node.graph()
    assert node.parse_builds == 1 and node.parse_hits == 0
    node.graph()
    node.graph()
    assert node.parse_builds == 1, "the corpus was parsed again"
    assert node.parse_hits == 2


def test_two_requests_in_a_row_do_not_re_parse(node, client):
    for _ in range(2):
        answer = client.get("/v1/corpus/neighbourhood",
                            params={"sha256": digest(0)})
        assert answer.status_code == 200
    assert node.parse_builds == 1, "each request rebuilt the graph"


def test_a_CHANGED_corpus_invalidates_it(node):
    """The bytes are never remembered. A licence somebody edits must be seen on
    the very next call — which is the whole reason this caches the PARSE and not
    the content."""
    graph_before, version_before = node.graph()
    section = node.read()
    section["nodes"].append({"id": "late", "type": "resource", "name": "late.jpg",
                             "data": {"checksum": digest(250)}})
    node.store.put(section)
    graph_after, version_after = node.graph()
    assert version_after != version_before
    assert graph_after is not graph_before
    assert node.parse_builds == 2


def test_the_version_reported_is_the_one_the_answer_came_out_of(node, client):
    answer = client.get("/v1/corpus/neighbourhood",
                        params={"sha256": digest(0)}).json()
    assert answer["version"] == node.version()


# ── 2 · the browser gets a story, never the register ────────────────────────

def test_the_neighbourhood_of_a_photograph_is_its_chain(node, client):
    answer = client.get("/v1/corpus/neighbourhood",
                        params={"sha256": digest(0)}).json()
    assert answer["start"] == "img0"
    reached = {card["id"] for card in answer["nodes"]}
    assert {"img0", "proc-align", "mesh0", "proc-ortho", "ortho0"} <= reached


def test_the_author_arrives_as_an_ATTRIBUTE_and_not_as_a_node(node, client):
    answer = client.get("/v1/corpus/neighbourhood",
                        params={"sha256": digest(0)}).json()
    reached = {card["id"] for card in answer["nodes"]}
    assert "author-bruno" not in reached
    event = next(c for c in answer["nodes"] if c["id"] == "proc-align")
    assert [c["id"] for c in event["context"]] == ["author-bruno"]


def test_the_answer_is_the_neighbourhood_and_not_the_whole_register(node, client):
    """The register holds nine nodes; a one-hop answer holds three. Nothing
    enforces that — the traversal is bounded because the context is not walked."""
    whole = len(node.read()["nodes"])
    answer = client.get("/v1/corpus/neighbourhood",
                        params={"sha256": digest(0), "hops": 1}).json()
    assert len(answer["nodes"]) < whole
    assert answer["truncated"] is True
    assert answer["frontier"], "…and it says where it stopped"


def test_a_caller_who_is_not_a_curator_gets_the_story_and_NOT_the_register(
        node, client, monkeypatch):
    """Two gates, one route each: the whole read refuses, the neighbourhood does
    not. This is the pair, asserted together — because «you got nothing» and
    «there is nothing» must not look alike."""
    monkeypatch.setattr(main_module, "may_read_whole",
                        lambda who, dev_mode=False: False)
    refused = client.get("/v1/corpus")
    assert refused.status_code == 403
    assert "curation" in refused.json()["detail"].lower() \
        or "curator" in refused.json()["detail"].lower()

    allowed = client.get("/v1/corpus/neighbourhood",
                         params={"sha256": digest(0)})
    assert allowed.status_code == 200
    assert allowed.json()["start"] == "img0"


def test_a_digest_the_corpus_never_heard_of_is_an_honest_NOTHING(node, client):
    answer = client.get("/v1/corpus/neighbourhood",
                        params={"sha256": digest(250)}).json()
    assert answer["start"] is None
    assert answer["nodes"] == []


def test_asking_without_a_digest_is_refused_with_the_remedy(node, client):
    answer = client.get("/v1/corpus/neighbourhood", params={"sha256": " "})
    assert answer.status_code == 400
    assert "sha256" in answer.json()["detail"]


def test_the_ceiling_is_bounded_by_the_route_too(node, client):
    """A caller cannot ask for a walk of a thousand hops: the route's own bound
    is the last fence, after the one the traversal keeps."""
    assert client.get("/v1/corpus/neighbourhood",
                      params={"sha256": digest(0), "hops": 999}).status_code == 422
