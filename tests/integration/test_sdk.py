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


# ── Graph RPC API (stubs — data not yet persisted, node_id always 0) ──────────
# GraphService (CreateNode / GetNode / CreateEdge) returns the request echoed
# back with node_id=0. The nodes are NOT stored; MATCH via Cypher returns empty.
# These tests document the current alpha behaviour so regressions are visible.

_GRAPH_RPC_REASON = (
    "GraphService stubs echo request data but do not persist nodes (id always 0). "
    "CypherService is the production path for node/edge creation in alpha."
)


@pytest.mark.xfail(reason=_GRAPH_RPC_REASON, strict=True)
def test_create_node_rpc_returns_id(client):
    """CreateNode RPC must return a non-zero node_id once implemented."""
    node = client.create_node(labels=["Person"], properties={"name": f"rpc-{uid()}"})
    assert node.id > 0


@pytest.mark.xfail(reason=_GRAPH_RPC_REASON, strict=True)
def test_create_node_rpc_persists(client):
    """Node created via RPC must be retrievable via Cypher."""
    name = f"persist-{uid()}"
    client.create_node(labels=["Person"], properties={"name": name})
    rows = client.cypher("MATCH (n:Person {name: $name}) RETURN n.name AS name", params={"name": name})
    assert len(rows) == 1, f"node not found via Cypher: {rows}"
    client.cypher("MATCH (n:Person {name: $name}) DELETE n", params={"name": name})


@pytest.mark.xfail(reason=_GRAPH_RPC_REASON, strict=True)
def test_get_node_rpc(client):
    """GetNode must return the stored node once CreateNode persists."""
    node = client.create_node(labels=["Person"], properties={"name": f"get-{uid()}"})
    fetched = client.get_node(node.id)
    assert fetched.id == node.id


@pytest.mark.xfail(reason=_GRAPH_RPC_REASON, strict=True)
def test_create_edge_rpc(client):
    """CreateEdge must create a traversable relationship once node IDs are valid."""
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
    schema = client.get_schema_text()
    assert isinstance(schema, str)


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
    # Use Cypher; graph RPC stubs not yet wired (see test_create_node_rpc_*).
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
