"""CoordinodeGraph — LangChain GraphStore backed by CoordiNode."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
        database: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = CoordinodeClient(addr, timeout=timeout)
        self._schema: Optional[str] = None
        self._structured_schema: Optional[Dict[str, Any]] = None

    # ── GraphStore interface ──────────────────────────────────────────────

    @property
    def schema(self) -> str:
        """Return cached schema string (refreshed by `refresh_schema`)."""
        if self._schema is None:
            self.refresh_schema()
        return self._schema or ""

    @property
    def structured_schema(self) -> Dict[str, Any]:
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
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Run a Cypher query and return rows as dicts.

        Args:
            query: OpenCypher query string.
            params: Optional query parameters.

        Returns:
            List of row dicts (column name → value).
        """
        result = self._client.cypher(query, params=params or {})
        rows: List[Dict[str, Any]] = []
        columns = list(result.columns)
        for row in result.rows:
            rows.append(dict(zip(columns, row)))
        return rows

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying gRPC connection."""
        self._client.close()

    def __enter__(self) -> "CoordinodeGraph":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ── Schema parser ─────────────────────────────────────────────────────────

def _parse_schema(schema_text: str) -> Dict[str, Any]:
    """Convert CoordiNode schema text into LangChain's structured format.

    LangChain's ``GraphCypherQAChain`` expects::

        {
            "node_props": {"Label": [{"property": "name", "type": "STRING"}, ...]},
            "rel_props":  {"TYPE":  [{"property": "weight", "type": "FLOAT"}, ...]},
            "relationships": [{"start": "A", "type": "REL", "end": "B"}, ...],
        }

    CoordiNode's ``/schema`` endpoint returns a human-readable text; we do a
    best-effort parse here. For reliable structured access use the gRPC
    ``SchemaService`` directly.
    """
    node_props: Dict[str, List[Dict[str, str]]] = {}
    rel_props: Dict[str, List[Dict[str, str]]] = {}
    relationships: List[Dict[str, str]] = []

    current_label: Optional[str] = None
    current_type: Optional[str] = None
    in_nodes = False
    in_rels = False

    for line in schema_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith("node labels"):
            in_nodes, in_rels = True, False
            continue
        if stripped.lower().startswith("relationship types"):
            in_nodes, in_rels = False, True
            continue

        if in_nodes:
            if stripped.startswith("-") or stripped.startswith("*"):
                label = stripped.lstrip("-* ").split()[0].strip(":")
                current_label = label
                node_props.setdefault(label, [])
            elif current_label and ":" in stripped:
                parts = stripped.split(":", 1)
                prop = parts[0].strip()
                typ = parts[1].strip().upper()
                node_props[current_label].append({"property": prop, "type": typ})

        if in_rels:
            if stripped.startswith("-") or stripped.startswith("*"):
                rel = stripped.lstrip("-* ").split()[0].strip()
                current_type = rel
                rel_props.setdefault(rel, [])
            elif current_type and "->" in stripped:
                parts = stripped.split("->")
                start = parts[0].strip().strip("(: )")
                end = parts[-1].strip().strip("(: )")
                relationships.append({
                    "start": start,
                    "type": current_type,
                    "end": end,
                })
            elif current_type and ":" in stripped:
                parts = stripped.split(":", 1)
                prop = parts[0].strip()
                typ = parts[1].strip().upper()
                rel_props[current_type].append({"property": prop, "type": typ})

    return {
        "node_props": node_props,
        "rel_props": rel_props,
        "relationships": relationships,
    }
