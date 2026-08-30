# CoordiNode Demo Notebooks

Interactive notebooks for LlamaIndex, LangChain, and LangGraph integrations.

## Open in Google Colab

| Notebook | What it shows | Needs |
|----------|---------------|-------|
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/00_seed_data.ipynb) **Seed Data** | Build a tech-industry knowledge graph (~35 relationships) | nothing |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/01_llama_index_property_graph.ipynb) **LlamaIndex** | `CoordinodePropertyGraphStore`: upsert, triplets, structured query | nothing |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/02_langchain_graph_chain.ipynb) **LangChain** | `CoordinodeGraph`: add_graph_documents, schema, GraphCypherQAChain | nothing |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/03_langgraph_agent.ipynb) **LangGraph** | Agent with CoordiNode as graph memory (save/query/traverse) | nothing |
| [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/structured-world/coordinode-python/blob/main/demo/notebooks/04_whats_new_in_0_5.ipynb) **What 0.5 Added** | Batch insert, `element_id`, schema revision, write/read concerns, time travel | a server (`COORDINODE_ADDR`) |

> **Note:** First run installs `coordinode-embedded` from source (Rust build, ~5 min).
> Subsequent runs use Colab's pip cache.
> The embedded Colab install is pinned to a specific commit that bundles coordinode-rs v0.4.1; the Colab notebook links above target `main`.
> The Docker Compose stack below pins the CoordiNode **server** image v0.5.3 by
> digest. Do not move it below that: 0.5.1 crashes its Raft core when the oplog
> rolls a segment that already exists, and a single-node stack never regains
> leadership afterwards.
>
> The first four run in Colab with no setup. **What 0.5 Added** does not: write
> concerns, read concerns and time travel are distribution and durability features,
> and the embedded engine has neither Raft nor replicas, so that notebook stops
> until `COORDINODE_ADDR` points at a server. Run it against the Docker Compose
> stack below.

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
