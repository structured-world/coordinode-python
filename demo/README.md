# CoordiNode Demo Notebooks

Interactive notebooks for LlamaIndex, LangChain, and LangGraph integrations.

## Open in Google Colab (no setup required)

| Notebook | What it shows |
|----------|---------------|
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/00_seed_data.ipynb) **Seed Data** | Build a tech-industry knowledge graph (~35 relationships) |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/01_llama_index_property_graph.ipynb) **LlamaIndex** | `CoordinodePropertyGraphStore`: upsert, triplets, structured query |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/02_langchain_graph_chain.ipynb) **LangChain** | `CoordinodeGraph`: add_graph_documents, schema, GraphCypherQAChain |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/03_langgraph_agent.ipynb) **LangGraph** | Agent with CoordiNode as graph memory — save/query/traverse |

> **Note:** First run installs `coordinode-embedded` from source (Rust build, ~5 min).
> Subsequent runs use Colab's pip cache.
> Notebooks are pinned to a specific commit that bundles coordinode-rs v0.3.13 (embedded engine used in Colab).
> The Docker Compose stack below uses the CoordiNode **server** image v0.3.14.

## Run locally (Docker Compose)

`demo/docker-compose.yml` provides a CoordiNode + Jupyter Lab stack:

```bash
cd demo/
docker compose up -d --build
```

Open: http://localhost:38888 (token: `demo`)

| Port | Service |
|------|---------|
| 37080 | CoordiNode gRPC |
| 37084 | CoordiNode metrics/health (`/metrics`, `/health`) |
| 38888 | Jupyter Lab |

## With OpenAI (optional)

Notebooks 02 and 03 have optional sections that use `OPENAI_API_KEY`.
They auto-skip when the key is absent — all core features work without LLM.

```bash
cd demo/
OPENAI_API_KEY=sk-... docker compose up -d
```
