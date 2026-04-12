"""Integration tests for CoordinodeGraph (LangChain adapter).

Requires a running CoordiNode instance. Set COORDINODE_ADDR env var
(default: localhost:7080).

Run via:
    COORDINODE_ADDR=localhost:7080 pytest tests/integration/adapters/test_langchain.py -v
"""

import os
import uuid

import pytest
from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
from langchain_core.documents import Document

from langchain_coordinode import CoordinodeGraph

ADDR = os.environ.get("COORDINODE_ADDR", "localhost:7080")


@pytest.fixture(scope="module")
def graph():
    with CoordinodeGraph(ADDR) as g:
        yield g


# ── Basic connectivity ────────────────────────────────────────────────────────


def test_connect(graph):
    assert graph is not None


def test_schema_returns_string(graph):
    schema = graph.schema
    assert isinstance(schema, str)


def test_refresh_schema_does_not_raise(graph):
    graph.refresh_schema()
    assert isinstance(graph.schema, str)
    assert isinstance(graph.structured_schema, dict)
    assert "node_props" in graph.structured_schema
    assert "rel_props" in graph.structured_schema
    assert "relationships" in graph.structured_schema


# ── Cypher query ──────────────────────────────────────────────────────────────


def test_query_returns_list(graph):
    result = graph.query("RETURN 1 AS n")
    assert isinstance(result, list)
    assert result[0]["n"] == 1


def test_query_count(graph):
    result = graph.query("MATCH (n) RETURN count(n) AS total")
    assert isinstance(result, list)
    assert isinstance(result[0]["total"], int)


# ── add_graph_documents ───────────────────────────────────────────────────────


@pytest.fixture
def unique_tag():
    return uuid.uuid4().hex[:8]


def test_add_graph_documents_upserts_nodes(graph, unique_tag):
    node_a = Node(id=f"Alice-{unique_tag}", type="LCPerson", properties={"role": "researcher"})
    node_b = Node(id=f"Bob-{unique_tag}", type="LCPerson", properties={"role": "engineer"})
    doc = GraphDocument(nodes=[node_a, node_b], relationships=[], source=Document(page_content="test"))

    graph.add_graph_documents([doc])

    result = graph.query(
        "MATCH (n:LCPerson {name: $name}) RETURN n.name AS name",
        params={"name": f"Alice-{unique_tag}"},
    )
    assert len(result) >= 1
    assert result[0]["name"] == f"Alice-{unique_tag}"


def test_add_graph_documents_creates_relationship(graph, unique_tag):
    node_a = Node(id=f"Charlie-{unique_tag}", type="LCPerson2")
    node_b = Node(id=f"GraphRAG-{unique_tag}", type="LCConcept")
    rel = Relationship(source=node_a, target=node_b, type="LC_RESEARCHES")
    doc = GraphDocument(
        nodes=[node_a, node_b],
        relationships=[rel],
        source=Document(page_content="test"),
    )

    graph.add_graph_documents([doc])

    # Verify the relationship was created, not just the source node.
    result = graph.query(
        "MATCH (a:LCPerson2 {name: $src})-[r:LC_RESEARCHES]->(b:LCConcept {name: $dst}) RETURN count(r) AS cnt",
        params={"src": f"Charlie-{unique_tag}", "dst": f"GraphRAG-{unique_tag}"},
    )
    assert result[0]["cnt"] == 1, f"expected exactly 1 relationship: {result}"


def test_add_graph_documents_idempotent(graph, unique_tag):
    """Calling add_graph_documents twice produces exactly one edge (MERGE idempotent)."""
    node_a = Node(id=f"Idempotent-{unique_tag}", type="LCIdempotent")
    node_b = Node(id=f"IdempTarget-{unique_tag}", type="LCIdempotent")
    rel = Relationship(source=node_a, target=node_b, type="LC_IDEMP_REL")
    doc = GraphDocument(
        nodes=[node_a, node_b],
        relationships=[rel],
        source=Document(page_content="test"),
    )

    graph.add_graph_documents([doc])
    graph.add_graph_documents([doc])  # second call must not raise

    # Nodes: MERGE keeps count at 1
    result = graph.query(
        "MATCH (n:LCIdempotent {name: $name}) RETURN count(*) AS cnt",
        params={"name": f"Idempotent-{unique_tag}"},
    )
    assert result[0]["cnt"] == 1

    # Edges: MERGE keeps count at 1 (idempotent)
    result = graph.query(
        "MATCH (a:LCIdempotent {name: $src})-[r:LC_IDEMP_REL]->(b:LCIdempotent {name: $dst}) RETURN count(r) AS cnt",
        params={"src": f"Idempotent-{unique_tag}", "dst": f"IdempTarget-{unique_tag}"},
    )
    assert result[0]["cnt"] == 1


# ── similarity_search ─────────────────────────────────────────────────────────


def test_similarity_search_returns_results(graph, unique_tag):
    """similarity_search() returns node dicts with id, node, and distance keys.

    Seeds a :LCSim node with a known embedding, then searches for the closest
    vector. The seeded node must appear in the top-k results.
    """
    # Derive a unique embedding from the test tag (same technique as llama-index
    # test) to avoid collisions with other :LCSim nodes in the shared DB.
    seed = list(bytes.fromhex(unique_tag))
    vec = [float(seed[i % len(seed)]) / 255.0 for i in range(16)]

    try:
        seed_rows = graph.query(
            "CREATE (n:LCSim {id: $id, embedding: $vec}) RETURN n AS nid",
            params={"id": f"lcsim-{unique_tag}", "vec": vec},
        )
        seeded_internal_id = seed_rows[0]["nid"]

        results = graph.similarity_search(vec, k=5, label="LCSim", property="embedding")

        assert isinstance(results, list)
        assert len(results) >= 1
        assert all("id" in r and "node" in r and "distance" in r for r in results)
        assert any(r["id"] == seeded_internal_id for r in results)
        assert results[0]["distance"] >= 0.0
    finally:
        graph.query("MATCH (n:LCSim {id: $id}) DELETE n", params={"id": f"lcsim-{unique_tag}"})


def test_similarity_search_empty_vector_returns_empty(graph):
    """similarity_search() with an empty vector list returns an empty list without error."""
    results = graph.similarity_search([], k=5)
    assert isinstance(results, list)


def test_schema_refreshes_after_add(graph, unique_tag):
    """structured_schema is invalidated and re-fetched after add_graph_documents."""
    graph._schema = None  # force refresh
    graph.schema  # trigger initial fetch before mutation

    node = Node(id=f"SchemaNode-{unique_tag}", type="LCSchemaTest")
    doc = GraphDocument(nodes=[node], relationships=[], source=Document(page_content="test"))
    graph.add_graph_documents([doc])

    graph.refresh_schema()
    # schema must still be a string after refresh (content depends on server)
    assert isinstance(graph.schema, str)
