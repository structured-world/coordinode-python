# langchain-coordinode

[![PyPI](https://img.shields.io/pypi/v/langchain-coordinode)](https://pypi.org/project/langchain-coordinode/)
[![Python](https://img.shields.io/pypi/pyversions/langchain-coordinode)](https://pypi.org/project/langchain-coordinode/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/structured-world/coordinode-python/blob/main/LICENSE)
[![CI](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml/badge.svg)](https://github.com/structured-world/coordinode-python/actions/workflows/ci.yml)

[LangChain](https://python.langchain.com/) integration for [CoordiNode](https://github.com/structured-world/coordinode) — `GraphStore` implementation and `GraphCypherQAChain` support for GraphRAG pipelines.

## Installation

```bash
pip install langchain-coordinode
```

```bash
uv add langchain-coordinode
```

## Requirements

- Python 3.11+
- Running CoordiNode instance

## Quick Start

### GraphCypherQAChain — Question Answering over a Knowledge Graph

```python
from langchain_coordinode import CoordinodeGraph
from langchain.chains import GraphCypherQAChain
from langchain_openai import ChatOpenAI

# Connect to CoordiNode
graph = CoordinodeGraph("localhost:7080")

# Build a QA chain that generates and executes Cypher queries
chain = GraphCypherQAChain.from_llm(
    ChatOpenAI(model="gpt-4o-mini"),
    graph=graph,
    verbose=True,
)

result = chain.invoke({"query": "What concepts are related to attention mechanisms?"})
print(result["result"])
```

### Schema Inspection

```python
from langchain_coordinode import CoordinodeGraph

graph = CoordinodeGraph("localhost:7080")

# Refresh schema from database
graph.refresh_schema()

# Schema string used by the LLM to generate Cypher
print(graph.schema)
# Node properties: Person (name: String, age: Integer), Concept (name: String) ...
# Relationships: (Person)-[:KNOWS]->(Person), (Document)-[:ABOUT]->(Concept) ...
```

### Direct Cypher Queries

```python
from langchain_coordinode import CoordinodeGraph

graph = CoordinodeGraph("localhost:7080")

# Returns List[Dict[str, Any]]
result = graph.query(
    "MATCH (n:Person)-[:KNOWS]->(m) WHERE n.name = $name RETURN m.name AS colleague",
    params={"name": "Alice"},
)
for row in result:
    print(row["colleague"])
```

### LLMGraphTransformer — Extract Knowledge from Text

```python
from langchain_community.graphs.graph_document import GraphDocument
from langchain_openai import ChatOpenAI
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_coordinode import CoordinodeGraph
from langchain_core.documents import Document

graph = CoordinodeGraph("localhost:7080")
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

transformer = LLMGraphTransformer(llm=llm)

docs = [Document(page_content="Alice knows Bob. Bob works at Acme Corp.")]
graph_docs = transformer.convert_to_graph_documents(docs)

# Store extracted entities and relationships
graph.add_graph_documents(graph_docs)
```

## Connection Options

```python
# host:port string
graph = CoordinodeGraph("localhost:7080")

# TLS
graph = CoordinodeGraph("db.example.com:7443", tls=True)

# Custom timeout
graph = CoordinodeGraph("localhost:7080", timeout=60.0)
```

## API Reference

### `CoordinodeGraph`

| Method | Description |
|--------|-------------|
| `query(query, params)` | Execute Cypher, returns `List[Dict[str, Any]]` |
| `refresh_schema()` | Reload node/relationship schema from database |
| `add_graph_documents(docs)` | Batch MERGE nodes + relationships from `GraphDocument` list |
| `schema` | Schema string for LLM context |

## Related Packages

| Package | Description |
|---------|-------------|
| [`coordinode`](https://pypi.org/project/coordinode/) | Core gRPC client |
| [`llama-index-graph-stores-coordinode`](https://pypi.org/project/llama-index-graph-stores-coordinode/) | LlamaIndex `PropertyGraphStore` |

## Links

- [Source](https://github.com/structured-world/coordinode-python)
- [CoordiNode server](https://github.com/structured-world/coordinode)
- [LangChain docs](https://python.langchain.com/docs/integrations/graphs/)
- [Issues](https://github.com/structured-world/coordinode-python/issues)

## Support

<div align="center">

[![USDT TRC-20](https://raw.githubusercontent.com/structured-world/coordinode-python/main/assets/usdt-qr.svg)](https://github.com/sponsors/structured-world)

**USDT (TRC-20):** `TFDsezHa1cBkoeZT5q2T49Wp66K8t2DmdA`

[GitHub Sponsors](https://github.com/sponsors/structured-world) · [Open Collective](https://opencollective.com/structured-world)

</div>

## License

Apache-2.0
