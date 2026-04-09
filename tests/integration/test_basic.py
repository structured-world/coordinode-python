"""Integration tests for CoordinodeClient.

Requires a running CoordiNode instance. Set COORDINODE_ADDR env var
(default: localhost:7080).

Run via:
    COORDINODE_ADDR=localhost:7080 pytest tests/integration/ -v
"""

import os
import uuid

import pytest

from coordinode import CoordinodeClient

ADDR = os.environ.get("COORDINODE_ADDR", "localhost:7080")


@pytest.fixture(scope="module")
def client():
    with CoordinodeClient(ADDR) as c:
        yield c


def test_health(client):
    # health() returns bool — True when server reports SERVING
    assert client.health() is True


def test_cypher_return_literal(client):
    # cypher() returns List[Dict[str, Any]] — one row per result row
    result = client.cypher("RETURN 1 AS n")
    assert len(result) == 1
    assert result[0]["n"] == 1


def test_create_and_get_node(client):
    # CREATE returns the node as an integer ID; RETURN n.prop verifies properties.
    # Note: id(n) is not yet implemented in alpha — use RETURN n.name.
    # UUID suffix prevents collisions when tests run in parallel or are retried.
    name = f"sdk-test-node-{uuid.uuid4().hex[:8]}"
    result = client.cypher(
        "CREATE (n:IntegrationTest {name: $name}) RETURN n.name AS name",
        params={"name": name},
    )
    assert result, "CREATE returned no rows"
    assert result[0]["name"] == name

    # Verify node is retrievable
    found = client.cypher(
        "MATCH (n:IntegrationTest {name: $name}) RETURN n.name AS name",
        params={"name": name},
    )
    assert found, "MATCH returned no rows"
    assert found[0]["name"] == name

    # Clean up
    client.cypher(
        "MATCH (n:IntegrationTest {name: $name}) DELETE n",
        params={"name": name},
    )


def test_vector_search(client):
    # Insert a node with an embedding, then search for it.
    # VectorResult has .node (NodeResult) and .distance (float).
    vec = [0.1] * 16
    client.cypher(
        "CREATE (d:VecTestDoc {id: 'vs-test', embedding: $vec})",
        params={"vec": vec},
    )
    try:
        results = client.vector_search(
            label="VecTestDoc",
            property="embedding",
            vector=vec,
            top_k=1,
        )
        # API must not raise — result is a list (possibly empty in alpha)
        assert isinstance(results, list)
        # When vector index is implemented, expect at least one result
        assert len(results) >= 1
        assert hasattr(results[0], "distance")
        assert hasattr(results[0], "node")
    finally:
        client.cypher("MATCH (d:VecTestDoc {id: 'vs-test'}) DELETE d")
