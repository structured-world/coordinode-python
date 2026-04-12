"""CoordinodePropertyGraphStore — LlamaIndex PropertyGraphStore implementation."""

from __future__ import annotations

from typing import Any

from coordinode import CoordinodeClient
from llama_index.core.graph_stores.types import (
    ChunkNode,
    EntityNode,
    LabelledNode,
    PropertyGraphStore,
    Relation,
)
from llama_index.core.vector_stores.types import VectorStoreQuery


def _cypher_ident(value: str) -> str:
    """Backtick-escape a Cypher identifier (label, rel-type, property key).

    Doubles any embedded backticks per the OpenCypher spec so that arbitrary
    strings can be used safely as identifiers without Cypher injection.
    """
    return f"`{value.replace('`', '``')}`"


def _cypher_param_name(key: str) -> str:
    """Return a valid Cypher parameter name derived from *key*.

    Cypher parameter names must be valid identifiers (letters, digits, ``_``).
    Replace any other character with ``_`` and prepend ``p_`` when the result
    starts with a digit.
    """
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in key)
    if safe and safe[0].isdigit():
        safe = f"p_{safe}"
    return safe or "p_"


class CoordinodePropertyGraphStore(PropertyGraphStore):
    """LlamaIndex ``PropertyGraphStore`` backed by CoordiNode.

    Supports ``PropertyGraphIndex`` and LlamaIndex Knowledge Graph workflows.

    Example::

        from llama_index.core import PropertyGraphIndex
        from llama_index.graph_stores.coordinode import CoordinodePropertyGraphStore

        graph_store = CoordinodePropertyGraphStore("localhost:7080")
        index = PropertyGraphIndex.from_documents(
            documents,
            property_graph_store=graph_store,
        )
        query_engine = index.as_query_engine(include_text=True)
        response = query_engine.query("What is machine learning?")

    Args:
        addr: CoordiNode gRPC address, e.g. ``"localhost:7080"``.
        timeout: Per-request gRPC deadline in seconds.
    """

    supports_structured_queries: bool = True
    supports_vector_queries: bool = True

    def __init__(
        self,
        addr: str = "localhost:7080",
        *,
        timeout: float = 30.0,
    ) -> None:
        self._client = CoordinodeClient(addr, timeout=timeout)

    # ── Node operations ───────────────────────────────────────────────────

    def get(
        self,
        properties: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> list[LabelledNode]:
        """Retrieve nodes by properties or IDs."""
        nodes: list[LabelledNode] = []

        if ids:
            # Query by the stored n.id property (string adapter ID), not by the
            # graph-internal integer node ID that get_node() expects.
            cypher = "MATCH (n) WHERE n.id IN $ids RETURN n, n.id AS _nid LIMIT 1000"
            result = self._client.cypher(cypher, params={"ids": ids})
            for row in result:
                node_data = row.get("n", {})
                node_id = str(row.get("_nid", ""))
                nodes.append(_node_result_to_labelled(node_id, node_data))
        elif properties:
            # Use indexed parameter names (p0, p1, …) to avoid collisions when
            # different property keys sanitize to the same Cypher identifier
            # (e.g. "a-b" and "a_b" both become "a_b").
            param_map: dict[str, Any] = {}
            clauses: list[str] = []
            for idx, (k, v) in enumerate(properties.items()):
                pname = f"p{idx}"
                clauses.append(f"n.{_cypher_ident(k)} = ${pname}")
                param_map[pname] = v
            where_clauses = " AND ".join(clauses)
            cypher = f"MATCH (n) WHERE {where_clauses} RETURN n, n.id AS _nid LIMIT 1000"
            result = self._client.cypher(cypher, params=param_map)
            for row in result:
                node_data = row.get("n", {})
                node_id = str(row.get("_nid", ""))
                nodes.append(_node_result_to_labelled(node_id, node_data))

        return nodes

    def get_triplets(
        self,
        entity_names: list[str] | None = None,
        relation_names: list[str] | None = None,
        properties: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> list[list[LabelledNode]]:
        """Retrieve triplets (subject, predicate, object) as node triples."""
        conditions: list[str] = []
        params: dict[str, Any] = {}

        if properties or ids:
            raise NotImplementedError("get_triplets() does not yet support filtering by properties or ids")
        if entity_names:
            conditions.append("(n.name IN $entity_names OR m.name IN $entity_names)")
            params["entity_names"] = entity_names
        if relation_names:
            rel_filter = "|".join(_cypher_ident(t) for t in relation_names)
            rel_pattern = f"[r:{rel_filter}]"
        else:
            rel_pattern = "[r]"

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cypher = (
            f"MATCH (n)-{rel_pattern}->(m) {where} "
            "RETURN n, type(r) AS rel_type, m, n.id AS _src_id, m.id AS _dst_id "
            "LIMIT 1000"
        )
        result = self._client.cypher(cypher, params=params)

        triplets: list[list[LabelledNode]] = []
        for row in result:
            src_data = row.get("n", {})
            rel_type = row.get("rel_type") or "RELATED"
            dst_data = row.get("m", {})
            src_id = str(row.get("_src_id", ""))
            dst_id = str(row.get("_dst_id", ""))
            src = _node_result_to_labelled(src_id, src_data)
            rel = Relation(label=str(rel_type), source_id=src_id, target_id=dst_id)
            dst = _node_result_to_labelled(dst_id, dst_data)
            triplets.append([src, rel, dst])

        return triplets

    def get_rel_map(
        self,
        graph_nodes: list[LabelledNode],
        depth: int = 1,
        limit: int = 30,
        ignore_rels: list[str] | None = None,
    ) -> list[list[LabelledNode]]:
        """Get relationship map for a set of nodes up to ``depth`` hops.

        Note: only ``depth=1`` (single hop) is supported. ``depth > 1`` raises
        ``NotImplementedError`` because CoordiNode does not yet serialise
        variable-length path results.
        """
        if depth != 1:
            raise NotImplementedError(
                "CoordinodePropertyGraphStore.get_rel_map() currently supports depth=1 only; "
                "variable-length path queries are not yet available in CoordiNode"
            )

        if not graph_nodes:
            return []

        ignored = set(ignore_rels) if ignore_rels else set()
        node_ids = [n.id for n in graph_nodes]
        safe_limit = int(limit)
        params: dict[str, object] = {"ids": node_ids}

        # Push ignore_rels filter into the WHERE clause so LIMIT applies only
        # to non-ignored edges and callers receive up to `limit` visible results.
        if ignored:
            params["ignored"] = list(ignored)
            ignore_clause = "AND type(r) NOT IN $ignored "
        else:
            ignore_clause = ""

        cypher = (
            "MATCH (n)-[r]->(m) "
            f"WHERE n.id IN $ids {ignore_clause}"
            f"RETURN n, type(r) AS _rel_type, m, n.id AS _src_id, m.id AS _dst_id "
            f"LIMIT {safe_limit}"
        )
        result = self._client.cypher(cypher, params=params)

        triplets: list[list[LabelledNode]] = []
        for row in result:
            src_data = row.get("n", {})
            dst_data = row.get("m", {})
            src_id = str(row.get("_src_id", ""))
            dst_id = str(row.get("_dst_id", ""))
            rel_label = str(row.get("_rel_type") or "RELATED")
            src = _node_result_to_labelled(src_id, src_data)
            dst = _node_result_to_labelled(dst_id, dst_data)
            rel = Relation(label=rel_label, source_id=src_id, target_id=dst_id)
            triplets.append([src, rel, dst])

        return triplets

    def upsert_nodes(self, nodes: list[LabelledNode]) -> None:
        """Upsert nodes into the graph."""
        for node in nodes:
            props = _labelled_to_props(node)
            label = _cypher_ident(_node_label(node))
            cypher = f"MERGE (n:{label} {{id: $id}}) SET n += $props"
            self._client.cypher(cypher, params={"id": node.id, "props": props})

    def upsert_relations(self, relations: list[Relation]) -> None:
        """Upsert relationships into the graph (idempotent via MERGE)."""
        for rel in relations:
            props = rel.properties or {}
            label = _cypher_ident(rel.label)
            if props:
                cypher = (
                    f"MATCH (src {{id: $src_id}}) MATCH (dst {{id: $dst_id}}) "
                    f"MERGE (src)-[r:{label}]->(dst) SET r += $props"
                )
                self._client.cypher(
                    cypher,
                    params={"src_id": rel.source_id, "dst_id": rel.target_id, "props": props},
                )
            else:
                cypher = f"MATCH (src {{id: $src_id}}) MATCH (dst {{id: $dst_id}}) MERGE (src)-[r:{label}]->(dst)"
                self._client.cypher(
                    cypher,
                    params={"src_id": rel.source_id, "dst_id": rel.target_id},
                )

    def delete(
        self,
        entity_names: list[str] | None = None,
        relation_names: list[str] | None = None,
        properties: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        """Delete nodes and/or relations matching given criteria."""
        if relation_names or properties:
            raise NotImplementedError("delete() does not yet support filtering by relation_names or properties")
        if ids:
            # Use n.id (string adapter ID) for consistency with get()
            cypher = "MATCH (n) WHERE n.id IN $ids DETACH DELETE n"
            self._client.cypher(cypher, params={"ids": ids})
        elif entity_names:
            cypher = "MATCH (n) WHERE n.name IN $names DETACH DELETE n"
            self._client.cypher(cypher, params={"names": entity_names})

    # ── Vector queries ────────────────────────────────────────────────────

    def vector_query(
        self,
        query: VectorStoreQuery,
        **kwargs: Any,
    ) -> tuple[list[LabelledNode], list[float]]:
        """Run a vector similarity query against node embeddings."""
        if query.query_embedding is None:
            return [], []

        results = self._client.vector_search(
            label=query.filters.filters[0].value if query.filters else "Chunk",
            property="embedding",
            vector=list(query.query_embedding),
            top_k=query.similarity_top_k,
        )

        nodes: list[LabelledNode] = []
        scores: list[float] = []
        for r in results:
            # VectorResult has .node (NodeResult with .id/.properties) and .distance
            node = ChunkNode(
                id_=str(r.node.id),
                text=r.node.properties.get("text", ""),
                properties=r.node.properties,
            )
            nodes.append(node)
            scores.append(r.distance)

        return nodes, scores

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def get_schema(self, refresh: bool = False) -> str:
        """Return schema as text."""
        return self._client.get_schema_text()

    def get_schema_str(self, refresh: bool = False) -> str:
        return self.get_schema(refresh=refresh)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CoordinodePropertyGraphStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Structured query (Cypher pass-through) ────────────────────────────

    def structured_query(
        self,
        query: str,
        param_map: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a raw Cypher query."""
        # cypher() returns List[Dict[str, Any]] — column name → value.
        return self._client.cypher(query, params=param_map or {})


# ── Helpers ───────────────────────────────────────────────────────────────


def _node_result_to_labelled(node_id: str, data: Any) -> LabelledNode:
    """Convert a raw node result to a LlamaIndex LabelledNode."""
    if isinstance(data, dict):
        props = {k: v for k, v in data.items() if k not in ("id", "_id")}
        name = props.pop("name", node_id)
        text = props.pop("text", None)
        if text is not None:
            return ChunkNode(id_=node_id, text=str(text), properties=props)
        return EntityNode(name=str(name), label="Entity", properties=props)
    return EntityNode(name=node_id, label="Entity")


def _labelled_to_props(node: LabelledNode) -> dict[str, Any]:
    """Extract serialisable properties from a LabelledNode."""
    props: dict[str, Any] = dict(node.properties or {})
    if isinstance(node, ChunkNode):
        props["text"] = node.text
    elif isinstance(node, EntityNode):
        props["name"] = node.name
    return props


def _node_label(node: LabelledNode) -> str:
    """Return a Cypher-safe label for a node."""
    if isinstance(node, ChunkNode):
        return "Chunk"
    if isinstance(node, EntityNode):
        return node.label or "Entity"
    return "Node"
