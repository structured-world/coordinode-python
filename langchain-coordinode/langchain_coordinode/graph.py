"""CoordinodeGraph — LangChain GraphStore backed by CoordiNode."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
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
        client: Optional pre-built client object (e.g. ``LocalClient`` from
            ``coordinode-embedded``) to use instead of creating a gRPC connection.
            Must expose a callable ``cypher(query, params)`` method.  When
            provided, ``addr`` and ``timeout`` are ignored.  The caller is
            responsible for closing the client.
    """

    def __init__(
        self,
        addr: str = "localhost:7080",
        *,
        database: str | None = None,
        timeout: float = 30.0,
        client: Any = None,
    ) -> None:
        # ``client`` allows passing a pre-built client (e.g. LocalClient from
        # coordinode-embedded) instead of creating a gRPC connection.  The object
        # must expose a ``.cypher(query, params)`` method and, optionally,
        # ``.get_schema_text()`` and ``.vector_search()``.
        if client is not None and not callable(getattr(client, "cypher", None)):
            raise TypeError("client must provide a callable cypher(query, params) method")
        self._owns_client = client is None
        self._client = client if client is not None else CoordinodeClient(addr, timeout=timeout)
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
        """Fetch current schema from CoordiNode.

        Prefers the structured ``get_labels()`` / ``get_edge_types()`` API
        (available since ``coordinode`` 0.6.0) over the legacy text-parsing
        path.  Injected clients (e.g. ``_EmbeddedAdapter`` in Colab notebooks)
        that do not expose those methods fall back to ``_parse_schema()``.
        Injected clients that only expose ``cypher()`` / ``close()`` (e.g.
        a bare ``coordinode-embedded`` ``LocalClient``) return an empty schema.
        """
        if hasattr(self._client, "get_schema_text"):
            self._schema = self._client.get_schema_text()
        else:
            self._schema = ""

        if hasattr(self._client, "get_labels") and hasattr(self._client, "get_edge_types"):
            try:
                node_props: dict[str, list[dict[str, str]]] = {}
                for label in self._client.get_labels():
                    node_props[label.name] = [
                        {"property": p.name, "type": _PROPERTY_TYPE_NAME.get(p.type, "UNSPECIFIED")}
                        for p in label.properties
                    ]
                rel_props: dict[str, list[dict[str, str]]] = {}
                for et in self._client.get_edge_types():
                    rel_props[et.name] = [
                        {"property": p.name, "type": _PROPERTY_TYPE_NAME.get(p.type, "UNSPECIFIED")}
                        for p in et.properties
                    ]
                if node_props or rel_props:
                    structured: dict[str, Any] = {"node_props": node_props, "rel_props": rel_props, "relationships": []}
                else:
                    # Both APIs returned empty (e.g. schema-free graph or stub adapter) —
                    # fall back to text parsing so we don't lose what get_schema_text() returned.
                    structured = _parse_schema(self._schema)
            except Exception:
                # Server may expose get_labels/get_edge_types but raise UNIMPLEMENTED
                # or another gRPC error if the schema service is not available in the
                # deployed version.  Fall back to text-based schema parsing to avoid
                # surfacing an unhandled exception to the caller.
                structured = _parse_schema(self._schema)
        else:
            structured = _parse_schema(self._schema)

        # Augment with relationship triples (start_label, type, end_label).
        # No LIMIT: RETURN DISTINCT bounds result by unique triples, not edge count.
        # Note: can simplify to labels(a)[0] once subscript-on-function support lands in the
        # published Docker image (tracked in G010 / GAPS.md).
        rows = self._client.cypher(
            "MATCH (a)-[r]->(b) RETURN DISTINCT labels(a) AS src_labels, type(r) AS rel, labels(b) AS dst_labels"
        )
        if rows:
            triples: set[tuple[str, str, str]] = set()
            for row in rows:
                start = _first_label(row.get("src_labels"))
                end = _first_label(row.get("dst_labels"))
                rel = row.get("rel")
                if start and rel and end:
                    triples.add((start, rel, end))
            structured["relationships"] = [
                {"start": start, "type": rel, "end": end} for start, rel, end in sorted(triples)
            ]
        self._structured_schema = structured

    def add_graph_documents(
        self,
        graph_documents: list[Any],
        include_source: bool = False,
    ) -> None:
        """Store nodes and relationships extracted from ``GraphDocument`` objects.

        Both nodes and relationships are upserted via ``MERGE``, so repeated
        calls with the same data are idempotent.

        Args:
            graph_documents: List of ``langchain_community.graphs.graph_document.GraphDocument``.
            include_source: If ``True``, also store the source ``Document`` as a
                ``__Document__`` node linked to every extracted entity via
                ``MENTIONS`` edges.
        """
        for doc in graph_documents:
            for node in doc.nodes:
                self._upsert_node(node)
            for rel in doc.relationships:
                self._create_edge(rel)
            if include_source and doc.source:
                self._link_document_to_entities(doc)

        # Invalidate cached schema so next access reflects new data
        self._schema = None
        self._structured_schema = None

    def _upsert_node(self, node: Any) -> None:
        """Upsert a single node by ``id`` via MERGE."""
        label = _cypher_ident(node.type or "Entity")
        props = dict(node.properties or {})
        # Always enforce node.id as the merge key; incoming
        # properties["name"] must not drift from the MERGE predicate.
        props["name"] = node.id
        self._client.cypher(
            f"MERGE (n:{label} {{name: $name}}) SET n += $props",
            params={"name": node.id, "props": props},
        )

    def _create_edge(self, rel: Any) -> None:
        """Upsert a relationship via MERGE (idempotent).

        SET r += $props is skipped when props is empty because
        SET r += {} is not supported by all server versions.
        """
        src_label = _cypher_ident(rel.source.type or "Entity")
        dst_label = _cypher_ident(rel.target.type or "Entity")
        rel_type = _cypher_ident(rel.type)
        props = dict(rel.properties or {})
        if props:
            self._client.cypher(
                f"MATCH (src:{src_label} {{name: $src}}) "
                f"MATCH (dst:{dst_label} {{name: $dst}}) "
                f"MERGE (src)-[r:{rel_type}]->(dst) SET r += $props",
                params={"src": rel.source.id, "dst": rel.target.id, "props": props},
            )
        else:
            self._client.cypher(
                f"MATCH (src:{src_label} {{name: $src}}) "
                f"MATCH (dst:{dst_label} {{name: $dst}}) "
                f"MERGE (src)-[r:{rel_type}]->(dst)",
                params={"src": rel.source.id, "dst": rel.target.id},
            )

    def _link_document_to_entities(self, doc: Any) -> None:
        """Upsert a ``__Document__`` node and MERGE ``MENTIONS`` edges to all entities."""
        src_id = getattr(doc.source, "id", None) or _stable_document_id(doc.source)
        self._client.cypher(
            "MERGE (d:__Document__ {id: $id}) SET d.page_content = $text",
            params={"id": src_id, "text": doc.source.page_content or ""},
        )
        for node in doc.nodes:
            label = _cypher_ident(node.type or "Entity")
            self._client.cypher(
                f"MATCH (d:__Document__ {{id: $doc_id}}) MATCH (n:{label} {{name: $name}}) MERGE (d)-[:MENTIONS]->(n)",
                params={"doc_id": src_id, "name": node.id},
            )

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

    def similarity_search(
        self,
        query_vector: Sequence[float],
        k: int = 5,
        label: str = "Chunk",
        property: str = "embedding",
    ) -> list[dict[str, Any]]:
        """Find nodes whose ``property`` vector is closest to ``query_vector``.

        Wraps ``CoordinodeClient.vector_search()``.  The returned list contains
        one dict per result with the keys ``node`` (node properties), ``id``
        (internal integer node ID), and ``distance`` (cosine distance, lower =
        more similar).

        Args:
            query_vector: Embedding vector to search for.
            k: Maximum number of results to return.
            label: Node label to search (default ``"Chunk"``).
            property: Embedding property name (default ``"embedding"``).

        Returns:
            List of result dicts sorted by ascending distance.
        """
        # Use len() instead of truthiness check: numpy.ndarray (and other Sequence
        # types) raise ValueError("The truth value of an array is ambiguous") when
        # used in a boolean context. len() == 0 works for all sequence types.
        if len(query_vector) == 0:
            return []
        if not hasattr(self._client, "vector_search"):
            # Injected clients (e.g. bare coordinode-embedded LocalClient) may
            # not implement vector_search — return empty rather than AttributeError.
            return []
        results = sorted(
            self._client.vector_search(
                label=label,
                property=property,
                vector=query_vector,
                top_k=k,
            ),
            key=lambda r: r.distance,
        )
        return [{"id": r.node.id, "node": r.node.properties, "distance": r.distance} for r in results]

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying client connection.

        Only closes the client if it was created internally (i.e. ``client`` was
        not passed to ``__init__``).  Externally-injected clients are owned by
        the caller and must be closed by them.  The underlying transport may be
        gRPC or an embedded in-process client.
        """
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CoordinodeGraph:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ── Schema helpers ────────────────────────────────────────────────────────

# Maps PropertyType protobuf enum integers to LangChain-compatible type strings.
# Values mirror coordinode.v1.graph.PropertyType (schema.proto).
# A static dict is intentional: importing generated proto modules here would create
# a hard dependency on coordinode's internal proto layout inside langchain-coordinode.
# This package already receives integer enum values via LabelInfo/EdgeTypeInfo from
# the coordinode SDK, so a local lookup table is the correct decoupling boundary.
_PROPERTY_TYPE_NAME: dict[int, str] = {
    0: "UNSPECIFIED",
    1: "INT64",
    2: "FLOAT64",
    3: "STRING",
    4: "BOOL",
    5: "BYTES",
    6: "TIMESTAMP",
    7: "VECTOR",
    8: "LIST",
    9: "MAP",
}


def _stable_document_id(source: Any) -> str:
    """Return a deterministic ID for a LangChain Document.

    Combines ``page_content`` and sorted ``metadata`` items so the same
    document produces the same ``__Document__`` node ID across different
    Python processes.
    """
    content = getattr(source, "page_content", "") or ""
    metadata = getattr(source, "metadata", {}) or {}
    # Use canonical JSON encoding to avoid delimiter ambiguity and ensure
    # determinism for nested/non-scalar metadata values.  default=str converts
    # non-JSON-serializable types (datetime, UUID, Path, …) to their string
    # representation so the hash never raises TypeError.
    canonical = json.dumps(
        {"content": content, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _first_label(labels: Any) -> str | None:
    """Extract a stable label from a labels() result (list of strings).

    In practice this application creates nodes with a single label, but the
    underlying CoordiNode API accepts ``list[str]`` so multi-label nodes are
    possible. ``min()`` gives a deterministic result regardless of how many
    labels are present.

    Note: once subscript-on-function support lands in the published Docker
    image (tracked in G010 / GAPS.md), this Python helper could be replaced
    by an inline Cypher expression — but keep the deterministic ``min()``
    rule rather than index 0, since label ordering is not guaranteed stable.
    """
    if isinstance(labels, list) and labels:
        return str(min(labels))
    if isinstance(labels, str):
        return labels
    return None


def _cypher_ident(name: str) -> str:
    """Escape a label/type name for use as a Cypher identifier."""
    # ASCII-only word characters: letter/digit/underscore, not starting with digit.
    if re.match(r"^[A-Za-z_]\w*$", name, re.ASCII):
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
