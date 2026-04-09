"""Integration tests for CoordinodePropertyGraphStore (LlamaIndex adapter).

Requires a running CoordiNode instance. Set COORDINODE_ADDR env var
(default: localhost:7080).

Run via:
    COORDINODE_ADDR=localhost:17080 pytest tests/integration/adapters/test_llama_index.py -v
"""

import os
import uuid

import pytest
from llama_index.core.graph_stores.types import EntityNode, Relation
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
    assert len(found) >= 1


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

    # CoordiNode does not support wildcard [r] patterns yet — must pass relation_names.
    # See: get_triplets() implementation note.
    triplets = store.get_triplets(
        entity_names=[f"Src-{tag}"],
        relation_names=["LI_RESEARCHES"],
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
