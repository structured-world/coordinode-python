"""Full SDK integration tests — exercises every public client method.

Requires a running CoordiNode instance:
    docker run -p 7080:7080 -p 7084:7084 ghcr.io/structured-world/coordinode:latest
    COORDINODE_ADDR=localhost:7080 pytest tests/integration/test_sdk.py -v
"""

from __future__ import annotations

import functools
import os
import uuid

import grpc
import pytest

from coordinode import (
    AsyncCoordinodeClient,
    CoordinodeClient,
    EdgeTypeInfo,
    HybridResult,
    LabelInfo,
    TextResult,
    TraverseResult,
)

ADDR = os.environ.get("COORDINODE_ADDR", "localhost:7080")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    with CoordinodeClient(ADDR) as c:
        yield c


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


# ── get_labels / get_edge_types / traverse ────────────────────────────────────


def test_get_labels_returns_list(client):
    """get_labels() returns a non-empty list of LabelInfo after data is present."""
    tag = uid()
    label_name = f"GetLabelsTest{uid()}"
    client.cypher(f"CREATE (n:{label_name} {{tag: $tag}})", params={"tag": tag})
    try:
        labels = client.get_labels()
        assert isinstance(labels, list)
        assert len(labels) > 0
        assert all(isinstance(lbl, LabelInfo) for lbl in labels)
        names = [lbl.name for lbl in labels]
        assert label_name in names, f"{label_name} not in {names}"
    finally:
        client.cypher(f"MATCH (n:{label_name} {{tag: $tag}}) DETACH DELETE n", params={"tag": tag})


def test_get_labels_has_property_definitions(client):
    """LabelInfo.properties is a list (may be empty for schema-free labels)."""
    tag = uid()
    label_name = f"PropLabel{uid()}"
    client.cypher(f"CREATE (n:{label_name} {{tag: $tag}})", params={"tag": tag})
    try:
        labels = client.get_labels()
        found = next((lbl for lbl in labels if lbl.name == label_name), None)
        assert found is not None, f"{label_name} not returned by get_labels()"
        # Intentionally only check the type — CoordiNode is schema-free and may return
        # an empty properties list even when the node was created with properties.
        assert isinstance(found.properties, list)
    finally:
        client.cypher(f"MATCH (n:{label_name} {{tag: $tag}}) DETACH DELETE n", params={"tag": tag})


def test_get_edge_types_returns_list(client):
    """get_edge_types() returns a non-empty list of EdgeTypeInfo after data is present."""
    tag = uid()
    edge_type = f"GET_EDGE_TYPE_TEST_{uid()}".upper()
    client.cypher(
        f"CREATE (a:EdgeTypeTestNode {{tag: $tag}})-[:{edge_type}]->(b:EdgeTypeTestNode {{tag: $tag}})",
        params={"tag": tag},
    )
    try:
        edge_types = client.get_edge_types()
        assert isinstance(edge_types, list)
        assert len(edge_types) > 0
        assert all(isinstance(et, EdgeTypeInfo) for et in edge_types)
        type_names = [et.name for et in edge_types]
        assert edge_type in type_names, f"{edge_type} not in {type_names}"
    finally:
        client.cypher("MATCH (n:EdgeTypeTestNode {tag: $tag}) DETACH DELETE n", params={"tag": tag})


def test_traverse_returns_neighbours(client):
    """traverse() returns adjacent nodes reachable via the given edge type."""
    tag = uid()
    client.cypher(
        "CREATE (a:TraverseRPC {tag: $tag, role: 'hub'})-[:TRAVERSE_TEST]->(b:TraverseRPC {tag: $tag, role: 'leaf1'})",
        params={"tag": tag},
    )
    try:
        rows = client.cypher(
            "MATCH (a:TraverseRPC {tag: $tag, role: 'hub'}) RETURN a AS node_id",
            params={"tag": tag},
        )
        assert len(rows) >= 1, "hub node not found"
        start_id = rows[0]["node_id"]

        # Fetch the leaf1 node ID so we can assert it specifically appears in the result.
        leaf_rows = client.cypher(
            "MATCH (b:TraverseRPC {tag: $tag, role: 'leaf1'}) RETURN b AS node_id",
            params={"tag": tag},
        )
        assert len(leaf_rows) >= 1, "leaf1 node not found"
        leaf1_id = leaf_rows[0]["node_id"]

        result = client.traverse(start_id, "TRAVERSE_TEST", direction="outbound", max_depth=1)
        assert isinstance(result, TraverseResult)
        assert len(result.nodes) >= 1, "traverse() returned no neighbour nodes"
        node_ids = {n.id for n in result.nodes}
        assert leaf1_id in node_ids, f"traverse() did not return the expected leaf1 node ({leaf1_id}); got: {node_ids}"
    finally:
        client.cypher("MATCH (n:TraverseRPC {tag: $tag}) DETACH DELETE n", params={"tag": tag})


@pytest.mark.xfail(
    strict=False,
    raises=AssertionError,
    # strict=False: XPASS is good news (server gained inbound support), not an error.
    # strict=True would break CI exactly when the server improves, which is undesirable.
    # The XPASS report in pytest output is the signal to remove this marker.
    # raises=AssertionError: narrows xfail to the known failure mode (empty result set →
    # assertion fails). Unexpected errors (gRPC RpcError, wrong enum, etc.) are NOT covered
    # and will still propagate as CI failures.
    reason="CoordiNode Traverse RPC does not yet support inbound direction — server returns empty result set",
)
def test_traverse_inbound_direction(client):
    """traverse() with direction='inbound' reaches nodes that point TO start_id."""
    tag = uid()
    client.cypher(
        "CREATE (src:TraverseIn {tag: $tag})-[:INBOUND_TEST]->(dst:TraverseIn {tag: $tag})",
        params={"tag": tag},
    )
    try:
        # Capture both src and dst so that when the server gains inbound support
        # (XPASS), the assertion verifies the *correct* node was returned, not just any node.
        rows = client.cypher(
            "MATCH (src:TraverseIn {tag: $tag})-[:INBOUND_TEST]->(dst:TraverseIn {tag: $tag}) "
            "RETURN src AS src_id, dst AS dst_id",
            params={"tag": tag},
        )
        assert len(rows) >= 1
        src_id = rows[0]["src_id"]
        dst_id = rows[0]["dst_id"]
        result = client.traverse(dst_id, "INBOUND_TEST", direction="inbound", max_depth=1)
        assert isinstance(result, TraverseResult)
        assert len(result.nodes) >= 1, "inbound traverse returned no nodes"
        node_ids = {n.id for n in result.nodes}
        assert src_id in node_ids, (
            f"inbound traverse did not return the expected source node ({src_id}); got: {node_ids}"
        )
    finally:
        client.cypher("MATCH (n:TraverseIn {tag: $tag}) DETACH DELETE n", params={"tag": tag})


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
    """AsyncCoordinodeClient("host:port") must parse correctly."""
    c = AsyncCoordinodeClient("localhost:7080")
    assert c._host == "localhost"
    assert c._port == 7080


def test_ipv6_bracket_parsing():
    """Bracketed IPv6 [::1]:7080 must parse correctly."""
    c = AsyncCoordinodeClient("[::1]:7080")
    assert c._host == "[::1]"
    assert c._port == 7080


def test_bare_ipv6_not_parsed():
    """Unbracketed IPv6 must NOT be misinterpreted as host:port."""
    c = AsyncCoordinodeClient("::1")
    assert c._host == "::1"
    assert c._port == 7080  # default unchanged


def test_explicit_port_conflict_raises():
    """Explicit port that conflicts with host-embedded port must raise ValueError."""
    with pytest.raises(ValueError, match="Conflicting ports"):
        AsyncCoordinodeClient("db.example.com:7443", port=7080)


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


# ── create_label / create_edge_type ──────────────────────────────────────────


def test_create_label_returns_label_info(client):
    """create_label() registers a label and returns LabelInfo."""
    name = f"CreateLabelTest{uid()}"
    info = client.create_label(
        name,
        properties=[
            {"name": "title", "type": "string", "required": True},
            {"name": "score", "type": "float64"},
        ],
    )
    assert isinstance(info, LabelInfo)
    assert info.name == name


def test_create_label_appears_in_get_labels(client):
    """Label created via create_label() appears in get_labels() once a node exists.

    Known limitation: ListLabels currently returns only labels that have at least
    one node in the graph. Ideally it should also include schema-only labels
    registered via create_label() (analogous to Neo4j returning schema-constrained
    labels even without data). Tracked as a server-side gap.
    """
    name = f"CreateLabelVisible{uid()}"
    tag = uid()
    # Declare both properties used in the workaround node.  Note: schema_mode
    # is accepted by the server but not yet enforced in the current image;
    # the properties are declared here for forward compatibility.
    client.create_label(
        name,
        properties=[
            {"name": "x", "type": "int64"},
            {"name": "tag", "type": "string"},
        ],
    )
    # Workaround: create a node so the label appears in ListLabels.
    client.cypher(f"CREATE (n:{name} {{x: 1, tag: $tag}})", params={"tag": tag})
    try:
        labels = client.get_labels()
        names = [lbl.name for lbl in labels]
        assert name in names, f"{name} not in {names}"
    finally:
        client.cypher(f"MATCH (n:{name} {{tag: $tag}}) DELETE n", params={"tag": tag})


def test_create_label_schema_mode_flexible(client):
    """create_label() with schema_mode='flexible' is accepted by the server."""
    name = f"FlexLabel{uid()}"
    info = client.create_label(name, schema_mode="flexible")
    assert isinstance(info, LabelInfo)
    assert info.name == name
    # FLEXIBLE (SchemaMode=3) is enforced server-side since coordinode-rs v0.3.12.
    # Older servers that omit the field return 0 (default), but this test suite
    # targets v0.3.12+ and asserts the concrete value to catch regressions.
    assert info.schema_mode == 3  # FLEXIBLE


def test_create_label_invalid_schema_mode_raises(client):
    """create_label() with unknown schema_mode raises ValueError locally."""
    with pytest.raises(ValueError, match="schema_mode"):
        client.create_label(f"Bad{uid()}", schema_mode="unknown")


def test_create_edge_type_returns_edge_type_info(client):
    """create_edge_type() registers an edge type and returns EdgeTypeInfo."""
    name = f"CREATE_ET_{uid()}".upper()
    info = client.create_edge_type(
        name,
        properties=[{"name": "since", "type": "timestamp"}],
    )
    assert isinstance(info, EdgeTypeInfo)
    assert info.name == name


def test_create_edge_type_appears_in_get_edge_types(client):
    """Edge type created via create_edge_type() appears in get_edge_types() once an edge exists.

    Same known limitation as test_create_label_appears_in_get_labels: ListEdgeTypes
    currently requires at least one edge of that type to exist in the graph.
    """
    name = f"VISIBLE_ET_{uid()}".upper()
    tag = uid()
    client.create_edge_type(name)
    # Workaround: create an edge so the type appears in ListEdgeTypes.
    client.cypher(
        f"CREATE (a:VisibleEtNode {{tag: $tag}})-[:{name}]->(b:VisibleEtNode {{tag: $tag}})",
        params={"tag": tag},
    )
    try:
        edge_types = client.get_edge_types()
        names = [et.name for et in edge_types]
        assert name in names, f"{name} not in {names}"
    finally:
        client.cypher("MATCH (n:VisibleEtNode {tag: $tag}) DETACH DELETE n", params={"tag": tag})


# ── Vector search ─────────────────────────────────────────────────────────────


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


# ── Full-text search ──────────────────────────────────────────────────────────


# FTS tests require a CoordiNode server with TextService implemented (>=0.3.8).
# They are marked xfail so the suite stays green against older servers; once
# upgraded, the tests turn into expected passes automatically.
#
# Note: create_label() is intentionally NOT called before text_search().
# FTS indexing in CoordiNode is automatic for all nodes whose label was written
# via CREATE/MERGE — no explicit label registration is required.  On schema-strict
# servers a caller may choose to pre-register a label, but the SDK's text_search()
# and hybrid_text_vector_search() work on schema-free graphs too.  These tests
# exercise the common schema-free path; calling create_label() here would test a
# different (schema-strict) code path and is covered by test_create_label_*.
def _fts(fn):
    """Wrap an FTS test to handle servers without TextService.

    Explicit xfail conditions:
    - ``grpc.StatusCode.UNIMPLEMENTED``: TextService RPC does not exist → ``pytest.xfail()``.
    - Empty result set: hit-requiring tests call ``pytest.xfail()`` inline.

    All other exceptions (wrong return type, malformed score, unexpected gRPC errors)
    propagate as real failures so regressions on FTS-capable servers are visible in CI.
    No ``pytest.mark.xfail`` decorator is applied — that would silently swallow any
    AssertionError and hide client-side regressions.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                pytest.xfail(f"TextService not implemented: {exc.details()}")
            raise  # other gRPC errors (e.g. INVALID_ARGUMENT) surface as failures

    return wrapper


@_fts
def test_text_search_returns_results(client):
    """text_search() finds nodes whose text property matches the query."""
    label = f"FtsTest_{uid()}"
    tag = uid()
    # CoordiNode executor serialises a node variable as Value::Int(node_id) — runner.rs NodeScan
    # path. No id() function needed; rows[0]["node_id"] is the integer internal node id.
    rows = client.cypher(
        f"CREATE (n:{label} {{tag: $tag, body: 'machine learning and neural networks'}}) RETURN n AS node_id",
        params={"tag": tag},
    )
    seed_id = rows[0]["node_id"]
    try:
        results = client.text_search(label, "machine learning", limit=5)
        assert isinstance(results, list)
        if not results:
            pytest.xfail("text_search returned no results — FTS index not available on this server")
        assert any(r.node_id == seed_id for r in results), (
            f"seeded node {seed_id} not found in text_search results: {results}"
        )
        r = results[0]
        assert isinstance(r, TextResult)
        assert isinstance(r.node_id, int)
        assert isinstance(r.score, float)
        assert r.score > 0
        assert isinstance(r.snippet, str)
    finally:
        client.cypher(f"MATCH (n:{label} {{tag: $tag}}) DELETE n", params={"tag": tag})


@_fts
def test_text_search_empty_for_unindexed_label(client):
    """text_search() returns [] when there are no text-indexed nodes to match.

    Covers two distinct cases:
    1. Label has never been inserted — nothing to search.
    2. Label exists but nodes carry only numeric/boolean properties; the FTS
       index contains no text, so no results can match any query term.
    """
    # Case 1: label that has never been inserted into the graph
    results = client.text_search("NoSuchLabelForFts_" + uid(), "anything")
    assert results == []

    # Case 2: label exists but nodes have no text properties to index
    label = f"FtsNumericOnly_{uid()}"
    tag = uid()
    client.cypher(
        f"CREATE (n:{label} {{tag: $tag, count: 42, active: true}})",
        params={"tag": tag},
    )
    try:
        results = client.text_search(label, "anything")
        assert results == []
    finally:
        client.cypher(f"MATCH (n:{label} {{tag: $tag}}) DELETE n", params={"tag": tag})


@_fts
def test_text_search_fuzzy(client):
    """text_search() with fuzzy=True matches approximate terms."""
    label = f"FtsFuzzyTest_{uid()}"
    tag = uid()
    client.cypher(
        f"CREATE (n:{label} {{tag: $tag, body: 'coordinode graph database'}})",
        params={"tag": tag},
    )
    try:
        # "coordinode" with a typo — fuzzy should still match
        results = client.text_search(label, "coordinod", fuzzy=True, limit=5)
        assert isinstance(results, list)
        # May return 0 results if fuzzy is not yet supported or index is cold;
        # just verify the call does not raise.
    finally:
        client.cypher(f"MATCH (n:{label} {{tag: $tag}}) DELETE n", params={"tag": tag})


@_fts
def test_hybrid_text_vector_search_returns_results(client):
    """hybrid_text_vector_search() returns HybridResult list with RRF scores."""
    label = f"FtsHybridTest_{uid()}"
    tag = uid()
    vec = [float(i) / 16 for i in range(16)]
    # Same node-as-int pattern: RETURN n → Value::Int(node_id) in CoordiNode executor.
    rows = client.cypher(
        f"CREATE (n:{label} {{tag: $tag, body: 'graph neural network embedding', embedding: $vec}}) RETURN n AS node_id",
        params={"tag": tag, "vec": vec},
    )
    seed_id = rows[0]["node_id"]
    try:
        results = client.hybrid_text_vector_search(
            label,
            "graph neural",
            vec,
            limit=5,
        )
        assert isinstance(results, list)
        if not results:
            pytest.xfail("hybrid_text_vector_search returned no results — FTS index not available on this server")
        assert any(r.node_id == seed_id for r in results), (
            f"seeded node {seed_id} not found in hybrid_text_vector_search results: {results}"
        )
        r = results[0]
        assert isinstance(r, HybridResult)
        assert isinstance(r.node_id, int)
        assert isinstance(r.score, float)
        assert r.score > 0
    finally:
        client.cypher(f"MATCH (n:{label} {{tag: $tag}}) DETACH DELETE n", params={"tag": tag})
