"""Integration tests for CoordinodeClient.

Requires a running CoordiNode instance. Set COORDINODE_ADDR env var
(default: localhost:7080).

Run via:
    COORDINODE_ADDR=localhost:7080 pytest tests/integration/ -v
"""

import os
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
    result = client.cypher(
        "CREATE (n:IntegrationTest {name: $name}) RETURN id(n) AS node_id",
        params={"name": "sdk-test-node"},
    )
    assert result, "CREATE returned no rows"
    node_id = result[0]["node_id"]
    assert node_id is not None

    # Clean up
    client.cypher(
        "MATCH (n:IntegrationTest {name: $name}) DELETE n",
        params={"name": "sdk-test-node"},
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
        assert len(results) >= 1
        assert hasattr(results[0], "distance")
        assert hasattr(results[0], "node")
    finally:
        client.cypher("MATCH (d:VecTestDoc {id: 'vs-test'}) DELETE d")
