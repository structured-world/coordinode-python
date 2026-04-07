# coordinode-python

Python SDK for [CoordiNode](https://github.com/structured-world/coordinode) — the graph-native hybrid retrieval engine for AI and GraphRAG.

## Packages

| Package | Install | Description |
|---------|---------|-------------|
| `coordinode` | `pip install coordinode` | Core gRPC client |
| `langchain-coordinode` | `pip install langchain-coordinode` | LangChain `GraphStore` + `GraphCypherQAChain` |
| `llama-index-graph-stores-coordinode` | `pip install llama-index-graph-stores-coordinode` | LlamaIndex `PropertyGraphStore` |

## Quick start

```bash
# Start CoordiNode
docker compose up -d

# Install
pip install coordinode
```

```python
from coordinode import CoordinodeClient

with CoordinodeClient("localhost", port=7080) as db:
    rows = db.cypher(
        "MATCH (n:Concept {name: $name})-[:RELATED_TO*1..2]->(m) RETURN m.name",
        params={"name": "machine learning"},
    )
    for row in rows:
        print(row["m.name"])
```

## LangChain

```python
from langchain_coordinode import CoordinodeGraph
from langchain.chains import GraphCypherQAChain
from langchain_openai import ChatOpenAI

graph = CoordinodeGraph("localhost")
chain = GraphCypherQAChain.from_llm(ChatOpenAI(model="gpt-4o-mini"), graph=graph)
result = chain.invoke({"query": "What is related to transformers?"})
```

## LlamaIndex

```python
from llama_index.core import PropertyGraphIndex
from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

store = CoordinodePropertyGraphStore("localhost")
index = PropertyGraphIndex.from_documents(docs, property_graph_store=store)
engine = index.as_query_engine()
```

## Development

```bash
git clone --recurse-submodules https://github.com/structured-world/coordinode-python
cd coordinode-python
pip install grpcio-tools
make install   # generates proto stubs + installs all packages in editable mode
make test
```

## Versioning

SDK versions track the server: `coordinode 0.3.x` is compatible with `coordinode-server 0.3.x`.

## License

Apache-2.0
