"""Full SDK integration tests — exercises every public client method.

Requires a running CoordiNode instance:
    docker run -p 7080:7080 -p 7084:7084 ghcr.io/structured-world/coordinode:latest
    COORDINODE_ADDR=localhost:7080 pytest tests/integration/test_sdk.py -v
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from coordinode import AsyncCoordinodeClient, CoordinodeClient

ADDR = os.environ.get("COORDINODE_ADDR", "localhost:7080")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    with CoordinodeClient(ADDR) as c:
        yield c


@pytest.fixture(scope="module")
def run():
    """Run a coroutine synchronously (re-used event loop for the module)."""
    loop = asyncio.new_event_loop()
    yield loop.run_until_complete
    loop.close()


def uid() -> str:
    return uuid.uuid4().hex[:8]


# ── Health ────────────────────────────────────────────────────────────────────


def test_health(client):
    assert client.health() is True


# ── Cypher basics ─────────────────────────────────────────────────────────────


def test_cypher_literal(client):
    rows = client.cypher("RETURN 42 AS n")
    assert rows == [{"n": 42}]


def test_cypher_string_param(client):
    rows = client.cypher("RETURN $s AS s", params={"s": "hello"})
    assert rows == [{"s": "hello"}]


def test_cypher_float_param(client):
    rows = client.cypher("RETURN $f AS f", params={"f": 3.14})
    assert len(rows) == 1
    assert abs(rows[0]["f"] - 3.14) < 1e-6


def test_cypher_bool_param(client):
    rows = client.cypher("RETURN $b AS b", params={"b": True})
    assert rows == [{"b": True}]


def test_cypher_int_param(client):
    rows = client.cypher("RETURN $i AS i", params={"i": -7})
    assert rows == [{"i": -7}]


def test_cypher_null_param(client):
    rows = client.cypher("RETURN $n AS n", params={"n": None})
    assert rows == [{"n": None}]


def test_cypher_no_rows(client):
    rows = client.cypher("MATCH (n:NonExistent_ZZZ) RETURN n")
    assert rows == []


# ── Graph RPC API ─────────────────────────────────────────────────────────────


def test_create_node_rpc_returns_id(client):
    """CreateNode RPC returns a non-zero node_id."""
    node = client.create_node(labels=["Person"], properties={"name": f"rpc-{uid()}"})
    assert node.id > 0


def test_create_node_rpc_persists(client):
    """Node created via RPC is retrievable via Cypher."""
    name = f"persist-{uid()}"
    client.create_node(labels=["Person"], properties={"name": name})
    rows = client.cypher("MATCH (n:Person {name: $name}) RETURN n.name AS name", params={"name": name})
    assert len(rows) == 1, f"node not found via Cypher: {rows}"
    client.cypher("MATCH (n:Person {name: $name}) DELETE n", params={"name": name})


def test_get_node_rpc(client):
    """GetNode returns the stored node with matching id."""
    node = client.create_node(labels=["Person"], properties={"name": f"get-{uid()}"})
    fetched = client.get_node(node.id)
    assert fetched.id == node.id


def test_create_edge_rpc(client):
    """CreateEdge connects two nodes; edge_id > 0 and endpoints match."""
    a = client.create_node(labels=["EdgeTest"], properties={"name": f"a-{uid()}"})
    b = client.create_node(labels=["EdgeTest"], properties={"name": f"b-{uid()}"})
    edge = client.create_edge("KNOWS", a.id, b.id, properties={"since": 2020})
    assert edge.id > 0
    assert edge.source_id == a.id
    assert edge.target_id == b.id


# ── Cypher node/edge create + retrieve ───────────────────────────────────────


def test_cypher_create_and_match(client):
    tag = uid()
    client.cypher(
        "CREATE (n:CypherTest {tag: $tag, score: $score})",
        params={"tag": tag, "score": 99},
    )
    rows = client.cypher(
        "MATCH (n:CypherTest {tag: $tag}) RETURN n.score AS score",
        params={"tag": tag},
    )
    assert rows == [{"score": 99}]
    client.cypher("MATCH (n:CypherTest {tag: $tag}) DELETE n", params={"tag": tag})


def test_cypher_traverse_edge(client):
    # Use Cypher CREATE for nodes+edge — graph RPC stubs are not yet wired.
    tag = uid()
    client.cypher(
        "CREATE (a:TraverseTest {role: 'source', tag: $tag})"
        "-[:POINTS_TO]->"
        "(b:TraverseTest {role: 'target', tag: $tag})",
        params={"tag": tag},
    )
    rows = client.cypher(
        "MATCH (a:TraverseTest {tag: $tag})-[:POINTS_TO]->(b:TraverseTest {tag: $tag}) "
        "RETURN a.role AS src, b.role AS dst",
        params={"tag": tag},
    )
    assert len(rows) == 1
    assert rows[0]["src"] == "source"
    assert rows[0]["dst"] == "target"
    client.cypher("MATCH (n:TraverseTest {tag: $tag}) DETACH DELETE n", params={"tag": tag})


# ── Property types round-trip ─────────────────────────────────────────────────


def test_property_types_roundtrip(client):
    """All scalar types must survive a write→read round-trip."""
    tag = uid()
    client.cypher(
        "CREATE (n:TypeTest {  tag: $tag, i: $i, f: $f, s: $s, b: $b})",
        params={"tag": tag, "i": 42, "f": 1.5, "s": "hello", "b": True},
    )
    rows = client.cypher(
        "MATCH (n:TypeTest {tag: $tag}) RETURN n.i AS i, n.f AS f, n.s AS s, n.b AS b",
        params={"tag": tag},
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["i"] == 42
    assert abs(r["f"] - 1.5) < 1e-6
    assert r["s"] == "hello"
    assert r["b"] is True
    client.cypher("MATCH (n:TypeTest {tag: $tag}) DELETE n", params={"tag": tag})


def test_bool_not_serialised_as_vector(client):
    """[True, False] must round-trip as a list of bools, NOT a vector of floats."""
    tag = uid()
    client.cypher(
        "CREATE (n:BoolListTest {tag: $tag, flags: $flags})",
        params={"tag": tag, "flags": [True, False, True]},
    )
    rows = client.cypher(
        "MATCH (n:BoolListTest {tag: $tag}) RETURN n.flags AS flags",
        params={"tag": tag},
    )
    assert len(rows) == 1
    flags = rows[0]["flags"]
    assert flags == [True, False, True], f"expected [True, False, True], got {flags!r}"
    client.cypher("MATCH (n:BoolListTest {tag: $tag}) DELETE n", params={"tag": tag})


# ── Schema ────────────────────────────────────────────────────────────────────


def test_get_schema_text(client):
    """get_schema_text returns a non-empty string that lists labels and edge types."""
    tag = uid()
    client.cypher(
        "CREATE (a:SchemaTestLabel {tag: $tag})-[:SCHEMA_EDGE]->(b:SchemaTestLabel {tag: $tag})",
        params={"tag": tag},
    )
    try:
        schema = client.get_schema_text()
        assert isinstance(schema, str)
        assert len(schema) > 0, "schema text must not be empty after data creation"
        assert "SchemaTestLabel" in schema, f"label missing from schema text: {schema!r}"
        assert "SCHEMA_EDGE" in schema, f"edge type missing from schema text: {schema!r}"
    finally:
        client.cypher("MATCH (n:SchemaTestLabel {tag: $tag}) DETACH DELETE n", params={"tag": tag})


# ── Hybrid search ─────────────────────────────────────────────────────────────


def test_hybrid_search_returns_results(client):
    """hybrid_search traverses graph edges then ranks neighbours by vector distance."""
    tag = uid()
    vec = [float(i) / 8 for i in range(8)]
    # Create two nodes connected by RELATED edge, both have embeddings.
    client.cypher(
        "CREATE (a:HybridTest {tag: $tag, embedding: $vec})-[:RELATED]->(b:HybridTest {tag: $tag, embedding: $vec})",
        params={"tag": tag, "vec": vec},
    )
    # Retrieve the start node id.
    # `a` in RETURN resolves to Value::Int(node_id) via NodeScan — no id() function needed.
    rows = client.cypher(
        "MATCH (a:HybridTest {tag: $tag})-[:RELATED]->(b) RETURN a AS aid",
        params={"tag": tag},
    )
    try:
        assert len(rows) >= 1, f"setup nodes not found: {rows}"
        start_id = rows[0]["aid"]
        results = client.hybrid_search(
            start_node_id=start_id,
            edge_type="RELATED",
            vector=vec,
            top_k=1,
            vector_property="embedding",
        )
        assert len(results) >= 1, "hybrid_search returned no results"
        assert hasattr(results[0], "distance")
        assert hasattr(results[0], "node")
    finally:
        client.cypher("MATCH (n:HybridTest {tag: $tag}) DETACH DELETE n", params={"tag": tag})


# ── Host:port string parsing ──────────────────────────────────────────────────


def test_hostport_string_parsing():
    """CoordinodeClient("host:port") must parse correctly."""
    c = CoordinodeClient("localhost:7080")
    assert c._async._host == "localhost"
    assert c._async._port == 7080


def test_ipv6_bracket_parsing():
    """Bracketed IPv6 [::1]:7080 must parse correctly."""
    c = CoordinodeClient("[::1]:7080")
    assert c._async._host == "[::1]"
    assert c._async._port == 7080


def test_bare_ipv6_not_parsed():
    """Unbracketed IPv6 must NOT be misinterpreted as host:port."""
    c = CoordinodeClient("::1")
    assert c._async._host == "::1"
    assert c._async._port == 7080  # default unchanged


# ── Async client ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_client_health():
    async with AsyncCoordinodeClient(ADDR) as c:
        assert await c.health() is True


@pytest.mark.asyncio
async def test_async_client_cypher():
    async with AsyncCoordinodeClient(ADDR) as c:
        rows = await c.cypher("RETURN 7 AS n")
    assert rows == [{"n": 7}]


@pytest.mark.asyncio
async def test_async_create_node():
    # Create node via Cypher to verify async client write path.
    tag = uid()
    async with AsyncCoordinodeClient(ADDR) as c:
        await c.cypher("CREATE (n:AsyncTest {tag: $tag})", params={"tag": tag})
        rows = await c.cypher("MATCH (n:AsyncTest {tag: $tag}) RETURN n.tag AS t", params={"tag": tag})
        assert rows == [{"t": tag}]
        await c.cypher("MATCH (n:AsyncTest {tag: $tag}) DELETE n", params={"tag": tag})


# ── Vector search (xfail until wired) ─────────────────────────────────────────


def test_vector_search_returns_results(client):
    tag = uid()
    vec = [float(i) / 16 for i in range(16)]
    client.cypher(
        "CREATE (n:VecSDKTest {tag: $tag, embedding: $vec})",
        params={"tag": tag, "vec": vec},
    )
    try:
        results = client.vector_search(label="VecSDKTest", property="embedding", vector=vec, top_k=1)
        assert len(results) >= 1
        assert hasattr(results[0], "distance")
        assert hasattr(results[0], "node")
    finally:
        client.cypher("MATCH (n:VecSDKTest {tag: $tag}) DELETE n", params={"tag": tag})
