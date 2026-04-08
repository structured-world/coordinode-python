# coordinode-python

[![CI](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml/badge.svg)](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/coordinode)](https://pypi.org/project/coordinode/)
[![Python](https://img.shields.io/pypi/pyversions/coordinode)](https://pypi.org/project/coordinode/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Python SDK for [CoordiNode](https://github.com/structured-world/coordinode) — the graph-native hybrid retrieval engine for AI and GraphRAG.

Graph + Vector + Full-Text in a single transactional engine. One client, one query.

## Packages

| Package | PyPI | Description |
|---------|------|-------------|
| [`coordinode`](coordinode/) | [![PyPI](https://img.shields.io/pypi/v/coordinode?label=coordinode)](https://pypi.org/project/coordinode/) | Core gRPC client — sync + async |
| [`langchain-coordinode`](langchain-coordinode/) | [![PyPI](https://img.shields.io/pypi/v/langchain-coordinode?label=langchain-coordinode)](https://pypi.org/project/langchain-coordinode/) | LangChain `GraphStore` + `GraphCypherQAChain` |
| [`llama-index-graph-stores-coordinode`](llama-index-coordinode/) | [![PyPI](https://img.shields.io/pypi/v/llama-index-graph-stores-coordinode?label=llama-index-graph-stores-coordinode)](https://pypi.org/project/llama-index-graph-stores-coordinode/) | LlamaIndex `PropertyGraphStore` |

## Quick Start

```bash
# Start CoordiNode
docker compose up -d

# Install
pip install coordinode
# or
uv add coordinode
```

```python
from coordinode import CoordinodeClient

with CoordinodeClient("localhost:7080") as db:
    # Cypher query — returns List[Dict[str, Any]]
    rows = db.cypher(
        "MATCH (n:Concept {name: $name})-[:RELATED_TO*1..2]->(m) RETURN m.name AS name",
        params={"name": "machine learning"},
    )
    for row in rows:
        print(row["name"])
```

## LangChain — GraphRAG Pipeline

```python
from langchain_coordinode import CoordinodeGraph
from langchain.chains import GraphCypherQAChain
from langchain_openai import ChatOpenAI

graph = CoordinodeGraph("localhost:7080")
chain = GraphCypherQAChain.from_llm(
    ChatOpenAI(model="gpt-4o-mini"),
    graph=graph,
    verbose=True,
)
result = chain.invoke({"query": "What concepts are related to transformers?"})
print(result["result"])
```

## LlamaIndex — Knowledge Graph Index

```python
from llama_index.core import PropertyGraphIndex
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

store = CoordinodePropertyGraphStore("localhost:7080")
index = PropertyGraphIndex.from_documents(docs, property_graph_store=store)
engine = index.as_query_engine(include_text=True)
response = engine.query("Explain attention mechanisms")
```

## Development Setup

### Using uv (recommended)

```bash
git clone --recurse-submodules https://github.com/structured-world/coordinode-python
cd coordinode-python
uv sync          # installs all packages + dev deps from uv.lock
make proto       # generate gRPC stubs from proto submodule
uv run pytest tests/unit/ -v
```

### Using pip

```bash
git clone --recurse-submodules https://github.com/structured-world/coordinode-python
cd coordinode-python
pip install grpcio-tools
make install-pip # generates proto stubs + installs all packages in editable mode
pytest tests/unit/ -v
```

### Running integration tests

Integration tests require a running CoordiNode instance:

```bash
docker compose up -d
COORDINODE_ADDR=localhost:7080 pytest tests/integration/ -v --timeout=30
```

## Versioning

SDK versions track the server: `coordinode 0.3.x` is compatible with `coordinode-server 0.3.x`.

## License

Apache-2.0 — see [LICENSE](LICENSE).

---

## Support the Project

If you believe graph + vector + full-text retrieval should live in one engine under a genuine open-source license, consider sponsoring:

- [GitHub Sponsors](https://github.com/sponsors/structured-world)
- [Open Collective](https://opencollective.com/structured-world)

<div align="center">

![USDT TRC-20 Donation QR](assets/usdt-qr.svg)

**USDT (TRC-20):** `TFDsezHa1cBkoeZT5q2T49Wp66K8t2DmdA`

</div>

Sponsorship accelerates: vector search integration, Bolt protocol compatibility, and the Enterprise Edition for horizontal scaling.
