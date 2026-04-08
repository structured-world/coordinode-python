"""CoordinodeGraph — LangChain GraphStore backed by CoordiNode."""

from __future__ import annotations

import re
from typing import Any

from langchain_community.graphs.graph_store import GraphStore

from coordinode import CoordinodeClient


class CoordinodeGraph(GraphStore):
    """LangChain `GraphStore` backed by CoordiNode.

    Supports ``GraphCypherQAChain`` and any LangChain component that works
    with a ``GraphStore``.

    Example::

        from langchain_coordinode import CoordinodeGraph
        from langchain.chains import GraphCypherQAChain
        from langchain_openai import ChatOpenAI

        graph = CoordinodeGraph("localhost:7080")
        chain = GraphCypherQAChain.from_llm(
            ChatOpenAI(model="gpt-4o-mini"),
            graph=graph,
            verbose=True,
        )
        result = chain.invoke("What concepts are related to machine learning?")

    Args:
        addr: CoordiNode gRPC address, e.g. ``"localhost:7080"``.
        database: Database name (reserved for future multi-db support).
        timeout: Per-request gRPC deadline in seconds.
    """

    def __init__(
        self,
        addr: str = "localhost:7080",
        *,
        database: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = CoordinodeClient(addr, timeout=timeout)
        self._schema: str | None = None
        self._structured_schema: dict[str, Any] | None = None

    # ── GraphStore interface ──────────────────────────────────────────────

    @property
    def schema(self) -> str:
        """Return cached schema string (refreshed by `refresh_schema`)."""
        if self._schema is None:
            self.refresh_schema()
        return self._schema or ""

    @property
    def structured_schema(self) -> dict[str, Any]:
        """Return structured schema dict (refreshed by `refresh_schema`)."""
        if self._structured_schema is None:
            self.refresh_schema()
        return self._structured_schema or {}

    def refresh_schema(self) -> None:
        """Fetch current schema from CoordiNode."""
        text = self._client.get_schema_text()
        self._schema = text
        # Parse schema text into structured form expected by LangChain
        self._structured_schema = _parse_schema(text)

    def query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a Cypher query and return rows as dicts.

        Args:
            query: OpenCypher query string.
            params: Optional query parameters.

        Returns:
            List of row dicts (column name → value).
        """
        # cypher() returns List[Dict[str, Any]] directly — column name → value.
        return self._client.cypher(query, params=params or {})

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying gRPC connection."""
        self._client.close()

    def __enter__(self) -> CoordinodeGraph:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ── Schema parser ─────────────────────────────────────────────────────────


def _parse_schema(schema_text: str) -> dict[str, Any]:
    """Convert CoordiNode schema text into LangChain's structured format.

    LangChain's ``GraphCypherQAChain`` expects::

        {
            "node_props": {"Label": [{"property": "name", "type": "STRING"}, ...]},
            "rel_props":  {"TYPE":  [{"property": "weight", "type": "FLOAT"}, ...]},
            "relationships": [{"start": "A", "type": "REL", "end": "B"}, ...],
        }

    CoordiNode's schema text format (from ``get_schema_text()``)::

        Node labels:
          - Person (properties: name: STRING, age: INT64)
          - Company

        Edge types:
          - KNOWS (properties: since: INT64)
          - WORKS_FOR

    We parse inline ``(properties: ...)`` lists on each bullet line.
    For reliable structured access use the gRPC ``SchemaService`` directly.
    """
    node_props: dict[str, list[dict[str, str]]] = {}
    rel_props: dict[str, list[dict[str, str]]] = {}
    relationships: list[dict[str, str]] = []

    in_nodes = False
    in_rels = False

    for line in schema_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith("node labels"):
            in_nodes, in_rels = True, False
            continue
        # Accept both "Edge types:" (current format) and "Relationship types:" (legacy)
        if stripped.lower().startswith("edge types") or stripped.lower().startswith("relationship types"):
            in_nodes, in_rels = False, True
            continue

        if (in_nodes or in_rels) and (stripped.startswith("-") or stripped.startswith("*")):
            # Extract name (part before optional "(properties: ...)")
            name = stripped.lstrip("-* ").split("(")[0].strip()
            if not name:
                continue
            # Parse inline properties: "- Label (properties: prop1: TYPE, prop2: TYPE)"
            props: list[dict[str, str]] = []
            m = re.search(r"\(properties:\s*([^)]+)\)", stripped)
            if m:
                for prop_str in m.group(1).split(","):
                    kv = prop_str.strip().split(":", 1)
                    if len(kv) == 2:
                        props.append({"property": kv[0].strip(), "type": kv[1].strip()})
            if in_nodes:
                node_props[name] = props
            else:
                rel_props[name] = props

    return {
        "node_props": node_props,
        "rel_props": rel_props,
        "relationships": relationships,
    }
