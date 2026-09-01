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

The first four need no setup and run entirely in-browser on the embedded engine:

| Notebook | Open |
|----------|------|
| 00 · Seed demo knowledge graph | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/00_seed_data.ipynb) |
| 01 · LlamaIndex PropertyGraph query | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/01_llama_index_property_graph.ipynb) |
| 02 · LangChain GraphCypherQAChain | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/02_langchain_graph_chain.ipynb) |
| 03 · LangGraph agent over graph | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/03_langgraph_agent.ipynb) |
| 04 · What 0.5 added, transactions included | [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/04_whats_new_in_0_5.ipynb) |

> Start with **00** to seed the graph, which the other notebooks read from.
> The first cell installs pre-built wheels from PyPI (~30 sec).
>
> **04 is the exception:** batch writes, consistency levels, time travel and
> transactions are distribution and durability features, so it needs a server
> rather than the embedded engine. Point `COORDINODE_ADDR` at one, or run the
> Docker Compose stack in `demo/`. Without it the notebook stops with an
> explanation instead of failing cell by cell.

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
land together, or not at all, run them in a transaction (each snippet opens
its own client, so it runs as pasted):

```python
from coordinode import CoordinodeClient

with CoordinodeClient("localhost:7080") as db:
    with db.transaction() as tx:
        tx.cypher("CREATE (:Person {name: $n})", params={"n": "Alice"})
        tx.cypher("CREATE (:Person {name: $n})", params={"n": "Bob"})
        # commits here; an exception anywhere in the block rolls back
        # instead, leaving neither person in the database
```

The same surface is on `AsyncCoordinodeClient`, with `async with` and awaited
statements. When the commit point sits outside a block, drive it by hand:

```python
from contextlib import suppress

from coordinode import CoordinodeClient

with CoordinodeClient("localhost:7080") as db:
    tx = db.begin_transaction()
    try:
        tx.cypher("MERGE (n:Entity {name: $n})", params={"n": "Alice"})
        applied_index = tx.commit()
    except BaseException:
        # BaseException so an interrupt (Ctrl-C) still frees the server-side
        # transaction; the rollback failure is suppressed so it cannot
        # replace the error that caused it.
        with suppress(Exception):
            tx.rollback()
        raise
```

Requires a CoordiNode server of **v0.5.7 or newer** — the release this client
is integration-tested against. `health()` exercises a different service, so a
server without the transaction RPCs passes the health check and then refuses
`transaction()`.

Each statement reads the snapshot taken when the transaction began, so the
transaction sees a stable view of the database plus its own uncommitted writes,
which nobody else can see until the commit. A conflict with another transaction
that wrote the same data is reported by `commit()`, not by the statement, and a
rejected commit applies nothing. `commit()` returns the Raft applied index, which
a later read can pass as `after_index` (with `read_concern="majority"`) when it
must observe these writes.

`tx.cypher()` takes no consistency arguments, unlike `db.cypher()`: the snapshot
is already fixed and durability is decided once at the commit, so a per-statement
read or write concern has nothing left to mean.

Three constraints are worth knowing before holding a transaction open:

- **It belongs to one node.** The handle lives in the memory of the server that
  opened it, so every request of the transaction must reach that same node.
  Connect to a node's own address, or through a proxy configured for backend
  affinity. A single client is *not* by itself a guarantee: against a layer-7
  or per-request gRPC balancer the calls can be spread across backends, and a
  reconnection can move to another backend mid-transaction, after which the
  next statement fails with an unknown transaction id.
- **Idle transactions are collected.** The server reaps one that has been idle
  (30 seconds by default), and it sweeps when another transaction begins rather
  than on a timer, so a long pause between statements can lose the handle. A
  failed statement also ends the transaction outright: its writes are discarded
  and the handle is closed, so reusing it raises rather than reporting a
  confusing error from the server.
- **A lost reply is not an abort.** If the connection drops or a deadline
  expires while committing, the server may have applied everything or nothing,
  and the client cannot tell. The transaction is marked indeterminate: later
  calls on it say so, and `rollback()` raises instead of promising a discard.
  Verify the data rather than blindly retrying, which can duplicate the writes.

## Finding the Slow Query's Author

The server's query advisor groups what it measures by the shape of the query,
which is why its report can name a statement but not the place that wrote it:
one line of Cypher usually has a dozen call sites. Turn on source tracking and
each query carries the file, line and function it was written at, so the report
names the line instead.

```python
client = CoordinodeClient(
    "localhost:7080",
    debug_source_tracking=True,
    app_name="feed-service",     # optional, for when services share a database
    app_version="2.1.0",
)
```

It is off by default and free while off: no frame is read and the request goes
out exactly as it would have. Turn it on where you are looking rather than
everywhere, because what it sends is the paths of your source files.

The location is read when you call the method, not when the query runs, so
concurrency does not lose it: `create_task`, `gather`, `wait_for`, `shield` and
`TaskGroup` all start the coroutine long after the calling frame has returned,
and all of them still report the line you wrote. Anything outside printable
ASCII is escaped rather than sent raw — a non-ASCII path, but a newline or a
tab just as much, since gRPC refuses those in a header too and would fail the
query rather than the attribution.

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
