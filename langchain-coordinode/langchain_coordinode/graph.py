"""CoordinodeGraph — LangChain GraphStore backed by CoordiNode."""

from __future__ import annotations

import hashlib
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
        structured = _parse_schema(text)
        # Augment with relationship triples (start_label, type, end_label) via
        # Cypher — get_schema_text() only lists edge types without direction.
        try:
            rows = self._client.cypher(
                "MATCH (a)-[r]->(b) RETURN DISTINCT a.__label__ AS src, r.__type__ AS rel, b.__label__ AS dst"
            )
            structured["relationships"] = [
                {"start": row["src"], "type": row["rel"], "end": row["dst"]}
                for row in rows
                if row.get("src") and row.get("rel") and row.get("dst")
            ]
        except Exception:  # noqa: BLE001
            pass  # Graph may have no relationships yet; structured["relationships"] stays []
        self._structured_schema = structured

    def add_graph_documents(
        self,
        graph_documents: list[Any],
        include_source: bool = False,
    ) -> None:
        """Store nodes and relationships extracted from ``GraphDocument`` objects.

        Nodes are upserted by ``id`` (used as the ``name`` property).
        Relationships are created between existing nodes; if a relationship
        between the same source and target already exists it is skipped.

        Args:
            graph_documents: List of ``langchain_community.graphs.graph_document.GraphDocument``.
            include_source: If ``True``, also store the source ``Document`` as a
                ``__Document__`` node linked to every extracted entity.
        """
        for doc in graph_documents:
            # ── Upsert nodes ──────────────────────────────────────────────
            for node in doc.nodes:
                label = _cypher_ident(node.type or "Entity")
                props = dict(node.properties or {})
                # Always enforce node.id as the merge key; incoming
                # properties["name"] must not drift from the MERGE predicate.
                props["name"] = node.id
                self._client.cypher(
                    f"MERGE (n:{label} {{name: $name}}) SET n += $props",
                    params={"name": node.id, "props": props},
                )

            # ── Create relationships (idempotent: skip if already exists) ─
            for rel in doc.relationships:
                src_label = _cypher_ident(rel.source.type or "Entity")
                dst_label = _cypher_ident(rel.target.type or "Entity")
                rel_type = _cypher_ident(rel.type)
                props = dict(rel.properties or {})
                # CoordiNode does not yet support MERGE for edges; use CREATE
                # guarded by NOT EXISTS to avoid duplicates on repeated calls.
                try:
                    self._client.cypher(
                        f"MATCH (src:{src_label} {{name: $src}}) "
                        f"MATCH (dst:{dst_label} {{name: $dst}}) "
                        f"WHERE NOT (src)-[:{rel_type}]->(dst) "
                        f"CREATE (src)-[r:{rel_type}]->(dst) SET r += $props",
                        params={"src": rel.source.id, "dst": rel.target.id, "props": props},
                    )
                except Exception:  # noqa: BLE001
                    # WHERE NOT EXISTS guard may not be supported on all server
                    # versions; fall back to unconditional CREATE
                    self._client.cypher(
                        f"MATCH (src:{src_label} {{name: $src}}) "
                        f"MATCH (dst:{dst_label} {{name: $dst}}) "
                        f"CREATE (src)-[r:{rel_type}]->(dst) SET r += $props",
                        params={"src": rel.source.id, "dst": rel.target.id, "props": props},
                    )

            # ── Optionally link source document ───────────────────────────
            if include_source and doc.source:
                src_id = getattr(doc.source, "id", None) or _stable_document_id(doc.source)
                self._client.cypher(
                    "MERGE (d:__Document__ {id: $id}) SET d.page_content = $text",
                    params={"id": src_id, "text": doc.source.page_content or ""},
                )
                for node in doc.nodes:
                    label = _cypher_ident(node.type or "Entity")
                    try:
                        self._client.cypher(
                            f"MATCH (d:__Document__ {{id: $doc_id}}) "
                            f"MATCH (n:{label} {{name: $name}}) "
                            f"WHERE NOT (d)-[:MENTIONS]->(n) "
                            f"CREATE (d)-[:MENTIONS]->(n)",
                            params={"doc_id": src_id, "name": node.id},
                        )
                    except Exception:  # noqa: BLE001
                        self._client.cypher(
                            f"MATCH (d:__Document__ {{id: $doc_id}}) "
                            f"MATCH (n:{label} {{name: $name}}) "
                            f"CREATE (d)-[:MENTIONS]->(n)",
                            params={"doc_id": src_id, "name": node.id},
                        )

        # Invalidate cached schema so next access reflects new data
        self._schema = None
        self._structured_schema = None

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


def _stable_document_id(source: Any) -> str:
    """Return a deterministic ID for a LangChain Document.

    Combines ``page_content`` and sorted ``metadata`` items so the same
    document produces the same node across different Python processes,
    making ``include_source=True`` re-ingest truly idempotent.
    """
    content = getattr(source, "page_content", "") or ""
    metadata = getattr(source, "metadata", {}) or {}
    stable = content + "|" + "|".join(f"{k}={v}" for k, v in sorted(metadata.items()))
    return hashlib.sha256(stable.encode()).hexdigest()[:32]


def _cypher_ident(name: str) -> str:
    """Escape a label/type name for use as a Cypher identifier."""
    # If already safe (alphanumeric + underscore, not starting with digit) keep as-is
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return name
    return f"`{name.replace('`', '``')}`"


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
