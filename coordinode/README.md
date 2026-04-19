# coordinode

[![PyPI](https://img.shields.io/pypi/v/coordinode)](https://pypi.org/project/coordinode/)
[![Python](https://img.shields.io/pypi/pyversions/coordinode)](https://pypi.org/project/coordinode/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/structured-world/coordinode-python/blob/main/LICENSE)
[![CI](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml/badge.svg)](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml)

Python gRPC client for [CoordiNode](https://github.com/structured-world/coordinode) — the graph-native hybrid retrieval engine for AI and GraphRAG.

## Installation

```bash
pip install coordinode
```

```bash
uv add coordinode
```

## Requirements

- Python 3.11+
- Running CoordiNode instance (`docker compose up -d` or binary)

## Quick Start

```python
from coordinode import CoordinodeClient

# Synchronous client — context manager handles connection lifecycle
with CoordinodeClient("localhost:7080") as db:
    # Cypher query — returns List[Dict[str, Any]]
    result = db.cypher("RETURN 1 AS n")
    print(result)  # [{'n': 1}]

    # With parameters
    rows = db.cypher(
        "MATCH (n:Person {name: $name}) RETURN n.age AS age",
        params={"name": "Alice"},
    )

    # Create nodes
    db.cypher(
        "CREATE (n:Document {title: $title, embedding: $vec})",
        params={"title": "RAG intro", "vec": [0.1, 0.2, 0.3, 0.4]},
    )

    # Health check
    assert db.health()
```

## Async Client

```python
import asyncio
from coordinode import AsyncCoordinodeClient

async def main():
    async with AsyncCoordinodeClient("localhost:7080") as db:
        rows = await db.cypher("MATCH (n:Concept) RETURN n.name AS name LIMIT 5")
        for row in rows:
            print(row["name"])

asyncio.run(main())
```

## Connection Options

```python
# host:port string
client = CoordinodeClient("localhost:7080")

# Separate host and port
client = CoordinodeClient("localhost", port=7080)

# TLS
client = CoordinodeClient("db.example.com:7443", tls=True)

# Custom timeout (seconds)
client = CoordinodeClient("localhost:7080", timeout=60.0)
```

## Type Mapping

CoordiNode properties map to Python types automatically:

| Python type | CoordiNode type |
|-------------|-----------------|
| `int` | `int_value` |
| `float` | `float_value` |
| `str` | `string_value` |
| `bool` | `bool_value` |
| `bytes` | `bytes_value` |
| `list[float]` | `Vector` (HNSW-indexable) |
| `list[Any]` | `PropertyList` |
| `dict[str, Any]` | `PropertyMap` |
| `None` | unset (null semantics) |

## Vector Search

```python
# Store a node with a vector embedding
db.cypher(
    "CREATE (d:Doc {title: $title, embedding: $vec})",
    params={"title": "RAG intro", "vec": [0.1] * 384},
)

# Nearest-neighbour search
results = db.vector_search(
    label="Doc",
    property="embedding",
    vector=[0.1] * 384,
    top_k=10,
    metric="cosine",  # "cosine" | "l2" | "dot" | "l1"
)
for r in results:
    print(r.node.id, r.distance)
```

## Hybrid Search (v0.4+)

Fuse BM25 full-text and vector similarity using Cypher scoring functions:

```python
# Reciprocal Rank Fusion of text + vector. Projecting `d AS doc_id` returns the
# internal node id (an integer) — fetch properties explicitly when needed.
rows = db.cypher("""
    MATCH (d:Doc)
    WHERE text_match(d, $q) OR d.embedding IS NOT NULL
    RETURN d AS doc_id,
           d.title AS title,
           rrf_score(
               text_score(d, $q),
               vec_score(d.embedding, $vec)
           ) AS score
    ORDER BY score DESC LIMIT 10
""", params={"q": "graph neural network", "vec": [0.1] * 384})
# Full node properties: db.get_node(rows[0]["doc_id"]).
```

Helpers available in Cypher: ``text_score``, ``vec_score``, ``doc_score``,
``text_match``, ``rrf_score``, ``hybrid_score``.

## ATTACH / DETACH DOCUMENT (v0.4+)

Promote a nested property to a graph node (and back):

```python
db.cypher("MATCH (a:Article {id: $id}) DETACH DOCUMENT a.body AS (d:Body)",
          params={"id": 1})
db.cypher("MATCH (a:Article {id: $id})-[:HAS_BODY]->(d:Body) "
          "ATTACH DOCUMENT d INTO a.body", params={"id": 1})
```

## Consistency Controls

```python
# Majority read for strict freshness. `n AS node_id` returns the integer id;
# use get_node(id) or project explicit properties (e.g. n.email AS email).
db.cypher(
    "MATCH (n:Account) RETURN n AS node_id, n.email AS email",
    read_concern="majority",
)

# Majority write (required for causal reads)
db.cypher("CREATE (n:Event {t: timestamp()})", write_concern="majority")

# Causal read: see at least state at raft index 42
db.cypher("MATCH (n) RETURN count(n) AS total", after_index=42)
```

Accepted values:

- ``read_concern``: ``local`` (default) · ``majority`` · ``linearizable`` · ``snapshot``
- ``write_concern``: ``w0`` · ``w1`` (default) · ``majority``
- ``read_preference``: ``primary`` (default) · ``primary_preferred`` · ``secondary`` · ``secondary_preferred`` · ``nearest``

## Related Packages

| Package | Description |
|---------|-------------|
| [`langchain-coordinode`](https://pypi.org/project/langchain-coordinode/) | LangChain `GraphStore` + `GraphCypherQAChain` |
| [`llama-index-graph-stores-coordinode`](https://pypi.org/project/llama-index-graph-stores-coordinode/) | LlamaIndex `PropertyGraphStore` |

## Links

- [Source](https://github.com/structured-world/coordinode-python)
- [CoordiNode server](https://github.com/structured-world/coordinode)
- [Issues](https://github.com/structured-world/coordinode-python/issues)
- [Changelog](https://github.com/structured-world/coordinode-python/releases)

## Support

<div align="center">

[![USDT TRC-20](https://raw.githubusercontent.com/structured-world/coordinode-python/main/assets/usdt-qr.svg)](https://github.com/sponsors/structured-world)

**USDT (TRC-20):** `TFDsezHa1cBkoeZT5q2T49Wp66K8t2DmdA`

[GitHub Sponsors](https://github.com/sponsors/structured-world) · [Open Collective](https://opencollective.com/structured-world)

</div>

## License

Apache-2.0
