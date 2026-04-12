"""Integration tests for CoordinodePropertyGraphStore (LlamaIndex adapter).

Requires a running CoordiNode instance. Set COORDINODE_ADDR env var
(default: localhost:7080).

Run via:
    COORDINODE_ADDR=localhost:7080 pytest tests/integration/adapters/test_llama_index.py -v
"""

import os
import uuid

import pytest
from llama_index.core.graph_stores.types import EntityNode, Relation
from llama_index.core.vector_stores.types import VectorStoreQuery
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

ADDR = os.environ.get("COORDINODE_ADDR", "localhost:7080")


@pytest.fixture(scope="module")
def store():
    with CoordinodePropertyGraphStore(ADDR) as s:
        yield s


@pytest.fixture
def tag():
    return uuid.uuid4().hex[:8]


# ── Basic connectivity ────────────────────────────────────────────────────────


def test_connect(store):
    assert store is not None


def test_get_schema(store):
    schema = store.get_schema()
    assert isinstance(schema, str)


def test_structured_query_literal(store):
    result = store.structured_query("RETURN 1 AS n")
    assert isinstance(result, list)
    assert result[0]["n"] == 1


# ── Node operations ───────────────────────────────────────────────────────────


def test_upsert_and_get_nodes(store, tag):
    nodes = [
        EntityNode(label="LITestPerson", name=f"Alice-{tag}", properties={"role": "researcher"}),
        EntityNode(label="LITestConcept", name=f"GraphRAG-{tag}", properties={"field": "AI"}),
    ]
    store.upsert_nodes(nodes)

    found = store.get(properties={"name": f"Alice-{tag}"})
    assert len(found) >= 1
    assert any(getattr(n, "name", None) == f"Alice-{tag}" for n in found)


def test_upsert_nodes_idempotent(store, tag):
    """Upserting the same node twice must not raise and must not duplicate."""
    node = EntityNode(label="LIIdempotent", name=f"Idem-{tag}")
    store.upsert_nodes([node])
    store.upsert_nodes([node])  # second call must not raise

    found = store.get(properties={"name": f"Idem-{tag}"})
    assert len(found) == 1


def test_get_by_id(store, tag):
    node = EntityNode(label="LIGetById", name=f"ById-{tag}")
    node_id = node.id
    store.upsert_nodes([node])

    found = store.get(ids=[node_id])
    assert len(found) >= 1


# ── Relation operations ───────────────────────────────────────────────────────


def test_upsert_and_get_triplets(store, tag):
    src = EntityNode(label="LIRelPerson", name=f"Src-{tag}")
    dst = EntityNode(label="LIRelConcept", name=f"Dst-{tag}")
    store.upsert_nodes([src, dst])

    rel = Relation(
        label="LI_RESEARCHES",
        source_id=src.id,
        target_id=dst.id,
        properties={"since": 2024},
    )
    store.upsert_relations([rel])

    # Wildcard [r] works — no need to specify relation_names.
    triplets = store.get_triplets(
        entity_names=[f"Src-{tag}"],
    )
    assert isinstance(triplets, list)
    assert len(triplets) >= 1

    labels = [t[1].label for t in triplets]
    assert "LI_RESEARCHES" in labels


def test_get_rel_map(store, tag):
    src = EntityNode(label="LIRelMap", name=f"RMapSrc-{tag}")
    dst = EntityNode(label="LIRelMap", name=f"RMapDst-{tag}")
    store.upsert_nodes([src, dst])

    rel = Relation(label="LI_RELATED", source_id=src.id, target_id=dst.id)
    store.upsert_relations([rel])

    result = store.get_rel_map([src], depth=1, limit=10)
    assert isinstance(result, list)
    assert len(result) >= 1


def test_get_rel_map_depth_gt1_raises(store, tag):
    """depth > 1 must raise NotImplementedError until multi-hop is supported."""
    node = EntityNode(label="LIRelMapDepth", name=f"DepthNode-{tag}")
    store.upsert_nodes([node])

    with pytest.raises(NotImplementedError):
        store.get_rel_map([node], depth=2, limit=10)


# ── Delete ────────────────────────────────────────────────────────────────────


def test_delete_by_id(store, tag):
    node = EntityNode(label="LIDelete", name=f"Del-{tag}")
    store.upsert_nodes([node])

    store.delete(ids=[node.id])

    found = store.get(ids=[node.id])
    assert len(found) == 0


def test_delete_by_entity_name(store, tag):
    node = EntityNode(label="LIDeleteByName", name=f"DelNamed-{tag}")
    store.upsert_nodes([node])

    store.delete(entity_names=[f"DelNamed-{tag}"])

    found = store.get(properties={"name": f"DelNamed-{tag}"})
    assert len(found) == 0


# ── Vector query ──────────────────────────────────────────────────────────────


def test_vector_query_returns_results(store, tag):
    """vector_query() returns nodes and scores for an embedding that matches stored data.

    vector_query() without filters defaults to label="Chunk", so the seed node must use
    that label to be found by the underlying vector_search() call.
    """
    # Derive a unique embedding from the test tag so that no other :Chunk in the shared
    # integration DB can have the same or closer vector, preventing flaky top-k results.
    # tag is uuid4().hex[:8] → 8 hex chars → 4 bytes of entropy.
    seed = list(bytes.fromhex(tag))
    vec = [float(seed[i % len(seed)]) / 255.0 for i in range(16)]
    # Seeding is inside the try block so that the finally cleanup always runs even if
    # the CREATE succeeds but extracting seeded_internal_id raises (e.g., unexpected
    # response format). vector_query() defaults label to "Chunk" when no
    # MetadataFilters are provided.
    try:
        # In CoordiNode, `CREATE (n:...) RETURN n` returns the internal integer node ID,
        # NOT a property map. This is CoordiNode-specific behaviour verified empirically:
        #   seed_rows[0]["nid"]  →  90  (int)
        # ChunkNode.id_ is set from vector_search's r.node.id (same internal integer),
        # so comparing str(node.id_) == str(seed_rows[0]["nid"]) correctly identifies
        # the specific seeded node.
        #
        # NOTE: vector_search returns Node(id=N, properties={}) — the properties dict is
        # always EMPTY, so node.properties.get("id") would always be None and cannot be
        # used for identification.
        seed_rows = store._client.cypher(
            "CREATE (n:Chunk {id: $id, text: $text, embedding: $vec}) RETURN n AS nid",
            params={"id": f"vec-{tag}", "text": "test chunk", "vec": vec},
        )
        seeded_internal_id = str(seed_rows[0]["nid"])
        # top_k=5: even if other :Chunk nodes exist with similar vectors, the unique
        # tag-based embedding ensures ours is among the closest results.
        query = VectorStoreQuery(query_embedding=vec, similarity_top_k=5)
        nodes, scores = store.vector_query(query)

        assert isinstance(nodes, list)
        assert isinstance(scores, list)
        assert len(nodes) >= 1
        # ChunkNode.id_ == str(r.node.id) == internal CoordiNode node ID captured above.
        assert any(str(getattr(node, "id_", "")) == seeded_internal_id for node in nodes)
        assert len(scores) == len(nodes)
        assert scores[0] >= 0.0
    finally:
        store._client.cypher("MATCH (n:Chunk {id: $id}) DELETE n", params={"id": f"vec-{tag}"})


def test_vector_query_empty_embedding_returns_empty(store):
    """vector_query() with no query_embedding returns empty lists without error."""
    query = VectorStoreQuery(query_embedding=None, similarity_top_k=5)
    nodes, scores = store.vector_query(query)
    assert nodes == []
    assert scores == []
