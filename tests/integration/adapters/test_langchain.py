"""Integration tests for CoordinodeGraph (LangChain adapter).

Requires a running CoordiNode instance. Set COORDINODE_ADDR env var
(default: localhost:7080).

Run via:
    COORDINODE_ADDR=localhost:17080 pytest tests/integration/adapters/test_langchain.py -v
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
    assert result[0]["cnt"] >= 1, f"relationship not found: {result}"


def test_add_graph_documents_idempotent(graph, unique_tag):
    """Calling add_graph_documents twice must not raise."""
    node = Node(id=f"Idempotent-{unique_tag}", type="LCIdempotent")
    doc = GraphDocument(nodes=[node], relationships=[], source=Document(page_content="test"))

    graph.add_graph_documents([doc])
    graph.add_graph_documents([doc])  # second call must not raise

    result = graph.query(
        "MATCH (n:LCIdempotent {name: $name}) RETURN count(n) AS cnt",
        params={"name": f"Idempotent-{unique_tag}"},
    )
    assert result[0]["cnt"] == 1


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
