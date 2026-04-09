# llama-index-graph-stores-coordinode

[![PyPI](https://img.shields.io/pypi/v/llama-index-graph-stores-coordinode)](https://pypi.org/project/llama-index-graph-stores-coordinode/)
[![Python](https://img.shields.io/pypi/pyversions/llama-index-graph-stores-coordinode)](https://pypi.org/project/llama-index-graph-stores-coordinode/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/structured-world/coordinode-python/blob/main/LICENSE)
[![CI](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml/badge.svg)](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml)

[LlamaIndex](https://www.llamaindex.ai/) `PropertyGraphStore` backed by [CoordiNode](https://github.com/structured-world/coordinode) — graph + vector + full-text in a single transactional engine.

## Installation

```bash
pip install llama-index-graph-stores-coordinode
```

```bash
uv add llama-index-graph-stores-coordinode
```

## Requirements

- Python 3.11+
- Running CoordiNode instance

## Quick Start

### PropertyGraphIndex — Build from Documents

```python
from llama_index.core import PropertyGraphIndex, SimpleDirectoryReader
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

# Connect to CoordiNode
store = CoordinodePropertyGraphStore("localhost:7080")

# Build graph index from documents (auto-extracts entities + relationships)
docs = SimpleDirectoryReader("./data").load_data()
index = PropertyGraphIndex.from_documents(
    docs,
    property_graph_store=store,
    show_progress=True,
)

# Query
engine = index.as_query_engine(include_text=True)
response = engine.query("Explain the attention mechanism in transformers")
print(response)
```

### Load Existing Graph

```python
from llama_index.core import PropertyGraphIndex
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

store = CoordinodePropertyGraphStore("localhost:7080")

# Load from existing data in CoordiNode (no re-extraction)
index = PropertyGraphIndex.from_existing(property_graph_store=store)

engine = index.as_query_engine()
response = engine.query("Who are the key researchers in graph neural networks?")
```

### Vector + Keyword Hybrid Retrieval

```python
from llama_index.core import PropertyGraphIndex
from llama_index.core.indices.property_graph import (
    VectorContextRetriever,
    LLMSynonymRetriever,
)
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore
from llama_index.embeddings.openai import OpenAIEmbedding

store = CoordinodePropertyGraphStore("localhost:7080")
embed_model = OpenAIEmbedding(model="text-embedding-3-small")

index = PropertyGraphIndex.from_existing(
    property_graph_store=store,
    embed_model=embed_model,
)

# Combine vector similarity + keyword/synonym retrieval
retriever = index.as_retriever(
    sub_retrievers=[
        VectorContextRetriever(index.property_graph_store, embed_model=embed_model),
        LLMSynonymRetriever(index.property_graph_store),
    ]
)
nodes = retriever.retrieve("graph attention networks")
```

### Manual Node and Relation Upsert

```python
from llama_index.core.graph_stores.types import EntityNode, Relation
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

store = CoordinodePropertyGraphStore("localhost:7080")

# Upsert entities
nodes = [
    EntityNode(label="Person", name="Alice", properties={"role": "researcher"}),
    EntityNode(label="Concept", name="GraphRAG", properties={"field": "AI"}),
]
store.upsert_nodes(nodes)

# Upsert relationships
relations = [
    Relation(
        label="RESEARCHES",
        source_id="Alice",
        target_id="GraphRAG",
        properties={"since": 2023},
    )
]
store.upsert_relations(relations)

# Query triplets
triplets = store.get_triplets(entity_names=["Alice"])
for subj, rel, obj in triplets:
    print(f"{subj.name} -[{rel.label}]-> {obj.name}")
```

### Direct Cypher

```python
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

store = CoordinodePropertyGraphStore("localhost:7080")

result = store.structured_query(
    "MATCH (n:Person)-[:KNOWS]->(m) WHERE n.name = $name RETURN m.name AS name",
    param_map={"name": "Alice"},
)
```

## Connection Options

```python
# host:port string
store = CoordinodePropertyGraphStore("localhost:7080")

# TLS
store = CoordinodePropertyGraphStore("db.example.com:7443", tls=True)

# Custom timeout
store = CoordinodePropertyGraphStore("localhost:7080", timeout=60.0)
```

## Capabilities

| Feature | Supported |
|---------|-----------|
| Node upsert | ✅ |
| Relation upsert | ✅ |
| Triplet queries | ✅ |
| Relationship map traversal | ✅ |
| Schema inspection | ✅ |
| Direct Cypher (`structured_query`) | ✅ |
| Vector similarity queries | ✅ (returns empty until HNSW index wiring — v0.4) |
| Async support | via `AsyncCoordinodeClient` |

## Related Packages

| Package | Description |
|---------|-------------|
| [`coordinode`](https://pypi.org/project/coordinode/) | Core gRPC client |
| [`langchain-coordinode`](https://pypi.org/project/langchain-coordinode/) | LangChain `GraphStore` + `GraphCypherQAChain` |

## Links

- [Source](https://github.com/structured-world/coordinode-python)
- [CoordiNode server](https://github.com/structured-world/coordinode)
- [LlamaIndex PropertyGraph docs](https://docs.llamaindex.ai/en/stable/module_guides/indexing/lpg_index_guide/)
- [Issues](https://github.com/structured-world/coordinode-python/issues)

## Support

<div align="center">

[![USDT TRC-20](https://raw.githubusercontent.com/structured-world/coordinode-python/main/assets/usdt-qr.svg)](https://github.com/sponsors/structured-world)

**USDT (TRC-20):** `TFDsezHa1cBkoeZT5q2T49Wp66K8t2DmdA`

[GitHub Sponsors](https://github.com/sponsors/structured-world) · [Open Collective](https://opencollective.com/structured-world)

</div>

## License

Apache-2.0
