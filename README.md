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

## Try It in Google Colab

No setup required — runs entirely in-browser using the embedded engine:

| Notebook | Open |
|----------|------|
| 00 · Seed demo knowledge graph | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/00_seed_data.ipynb) |
| 01 · LlamaIndex PropertyGraph query | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/01_llama_index_property_graph.ipynb) |
| 02 · LangChain GraphCypherQAChain | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/02_langchain_graph_chain.ipynb) |
| 03 · LangGraph agent over graph | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/03_langgraph_agent.ipynb) |

> Start with **00** to seed the graph — the other notebooks read from it.
> The first cell installs pre-built wheels from PyPI (~30 sec).

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

## Transactions

`db.cypher(...)` commits each statement on its own. To make several statements
land together, or not at all, run them in a transaction:

```python
with db.transaction() as tx:
    tx.cypher("CREATE (:Person {name: $n})", params={"n": "Alice"})
    tx.cypher("CREATE (:Person {name: $n})", params={"n": "Bob"})
    # commits here; an exception anywhere in the block rolls back instead,
    # leaving neither person in the database
```

The same surface is on `AsyncCoordinodeClient`, with `async with` and awaited
statements. When the commit point sits outside a block, drive it by hand:

```python
tx = db.begin_transaction()
try:
    tx.cypher("MERGE (n:Entity {name: $n})", params={"n": "Alice"})
    applied_index = tx.commit()
except Exception:
    tx.rollback()
    raise
```

Each statement reads the snapshot taken when the transaction began, so the
transaction sees a stable view of the database plus its own uncommitted writes,
which nobody else can see until the commit. A conflict with another transaction
that wrote the same data is reported by `commit()`, not by the statement, and a
rejected commit applies nothing. `commit()` returns the Raft applied index, which
a later read can pass as `after_index` (with `write_concern="majority"`) when it
must observe these writes.

`tx.cypher()` takes no consistency arguments, unlike `db.cypher()`: the snapshot
is already fixed and durability is decided once at the commit, so a per-statement
read or write concern has nothing left to mean.

Two constraints are worth knowing before holding a transaction open:

- **It belongs to one node.** The handle lives in the memory of the server that
  opened it, so every statement and the commit must reach that same node. One
  client instance holds one connection and satisfies this; pointing several
  clients at a load balancer in front of replicas does not.
- **Idle transactions are collected.** The server reaps one that has been idle
  (30 seconds by default), and it sweeps when another transaction begins rather
  than on a timer, so a long pause between statements can lose the handle. A
  failed statement also ends the transaction outright: its writes are discarded
  and the handle is closed, so reusing it raises rather than reporting a
  confusing error from the server.

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
