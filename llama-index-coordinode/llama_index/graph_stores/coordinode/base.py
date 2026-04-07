"""CoordinodePropertyGraphStore — LlamaIndex PropertyGraphStore implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from llama_index.core.graph_stores.types import (
    ChunkNode,
    EntityNode,
    KG_NODES_KEY,
    KG_RELATIONS_KEY,
    LabelledNode,
    PropertyGraphStore,
    Relation,
)
from llama_index.core.vector_stores.types import VectorStoreQuery

from coordinode import CoordinodeClient


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
        properties: Optional[Dict[str, Any]] = None,
        ids: Optional[List[str]] = None,
    ) -> List[LabelledNode]:
        """Retrieve nodes by properties or IDs."""
        nodes: List[LabelledNode] = []

        if ids:
            for node_id in ids:
                result = self._client.get_node(node_id)
                if result is not None:
                    nodes.append(_node_result_to_labelled(node_id, result))
        elif properties:
            where_clauses = " AND ".join(
                f"n.{k} = ${k}" for k in properties
            )
            cypher = f"MATCH (n) WHERE {where_clauses} RETURN n, id(n) AS _id LIMIT 1000"
            result = self._client.cypher(cypher, params=properties)
            for row in result.rows:
                node_data = row[0] if row else {}
                node_id = str(row[1]) if len(row) > 1 else ""
                nodes.append(_node_result_to_labelled(node_id, node_data))

        return nodes

    def get_triplets(
        self,
        entity_names: Optional[List[str]] = None,
        relation_names: Optional[List[str]] = None,
        properties: Optional[Dict[str, Any]] = None,
        ids: Optional[List[str]] = None,
    ) -> List[List[LabelledNode]]:
        """Retrieve triplets (subject, predicate, object) as node triples."""
        conditions: List[str] = []
        params: Dict[str, Any] = {}

        if entity_names:
            conditions.append("(n.name IN $entity_names OR m.name IN $entity_names)")
            params["entity_names"] = entity_names
        if relation_names:
            rel_filter = "|".join(relation_names)
            # Inline into pattern — CoordiNode supports dynamic type lists
            rel_pattern = f"[r:{rel_filter}]"
        else:
            rel_pattern = "[r]"

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cypher = (
            f"MATCH (n)-{rel_pattern}->(m) {where} "
            "RETURN n, type(r), m, id(n) AS _src_id, id(m) AS _dst_id "
            "LIMIT 1000"
        )
        result = self._client.cypher(cypher, params=params)

        triplets: List[List[LabelledNode]] = []
        for row in result.rows:
            src_data, rel_type, dst_data, src_id, dst_id = (
                row[0], row[1], row[2], str(row[3]), str(row[4])
            )
            src = _node_result_to_labelled(src_id, src_data)
            rel = Relation(
                label=str(rel_type),
                source_id=src_id,
                target_id=dst_id,
            )
            dst = _node_result_to_labelled(dst_id, dst_data)
            triplets.append([src, rel, dst])

        return triplets

    def get_rel_map(
        self,
        graph_nodes: List[LabelledNode],
        depth: int = 2,
        limit: int = 30,
        ignore_rels: Optional[List[str]] = None,
    ) -> List[List[LabelledNode]]:
        """Get relationship map for a set of nodes up to ``depth`` hops."""
        if not graph_nodes:
            return []

        ids = [n.id for n in graph_nodes]
        rel_filter = ""
        if ignore_rels:
            # We can't dynamically exclude types in OpenCypher without WHERE
            pass

        cypher = (
            f"MATCH (n)-[r*1..{depth}]->(m) "
            f"WHERE id(n) IN $ids "
            f"RETURN n, r, m, id(n) AS _src_id, id(m) AS _dst_id "
            f"LIMIT {limit}"
        )
        result = self._client.cypher(cypher, params={"ids": ids})

        triplets: List[List[LabelledNode]] = []
        for row in result.rows:
            src_data, rels, dst_data, src_id, dst_id = (
                row[0], row[1], row[2], str(row[3]), str(row[4])
            )
            src = _node_result_to_labelled(src_id, src_data)
            dst = _node_result_to_labelled(dst_id, dst_data)
            rel_label = str(rels[0]) if isinstance(rels, list) and rels else "RELATED"
            rel = Relation(label=rel_label, source_id=src_id, target_id=dst_id)
            triplets.append([src, rel, dst])

        return triplets

    def upsert_nodes(self, nodes: List[LabelledNode]) -> None:
        """Upsert nodes into the graph."""
        for node in nodes:
            props = _labelled_to_props(node)
            label = _node_label(node)
            cypher = (
                f"MERGE (n:{label} {{id: $id}}) "
                "SET n += $props"
            )
            self._client.cypher(cypher, params={"id": node.id, "props": props})

    def upsert_relations(self, relations: List[Relation]) -> None:
        """Upsert relationships into the graph."""
        for rel in relations:
            props = rel.properties or {}
            cypher = (
                "MATCH (src {id: $src_id}), (dst {id: $dst_id}) "
                f"MERGE (src)-[r:{rel.label}]->(dst) "
                "SET r += $props"
            )
            self._client.cypher(
                cypher,
                params={
                    "src_id": rel.source_id,
                    "dst_id": rel.target_id,
                    "props": props,
                },
            )

    def delete(
        self,
        entity_names: Optional[List[str]] = None,
        relation_names: Optional[List[str]] = None,
        properties: Optional[Dict[str, Any]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        """Delete nodes and/or relations matching given criteria."""
        if ids:
            cypher = "MATCH (n) WHERE id(n) IN $ids DETACH DELETE n"
            self._client.cypher(cypher, params={"ids": ids})
        elif entity_names:
            cypher = "MATCH (n) WHERE n.name IN $names DETACH DELETE n"
            self._client.cypher(cypher, params={"names": entity_names})

    # ── Vector queries ────────────────────────────────────────────────────

    def vector_query(
        self,
        query: VectorStoreQuery,
        **kwargs: Any,
    ) -> tuple[List[LabelledNode], List[float]]:
        """Run a vector similarity query against node embeddings."""
        if query.query_embedding is None:
            return [], []

        results = self._client.vector_search(
            label=query.filters.filters[0].value if query.filters else "Chunk",
            property="embedding",
            vector=list(query.query_embedding),
            top_k=query.similarity_top_k,
        )

        nodes: List[LabelledNode] = []
        scores: List[float] = []
        for r in results:
            node = ChunkNode(
                id_=str(r.node_id),
                text=r.properties.get("text", ""),
                properties=r.properties,
            )
            nodes.append(node)
            scores.append(r.score)

        return nodes, scores

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def get_schema(self, refresh: bool = False) -> str:
        """Return schema as text."""
        return self._client.get_schema_text()

    def get_schema_str(self, refresh: bool = False) -> str:
        return self.get_schema(refresh=refresh)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CoordinodePropertyGraphStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Structured query (Cypher pass-through) ────────────────────────────

    def structured_query(
        self,
        query: str,
        param_map: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute a raw Cypher query."""
        result = self._client.cypher(query, params=param_map or {})
        rows = []
        columns = list(result.columns)
        for row in result.rows:
            rows.append(dict(zip(columns, row)))
        return rows


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


def _labelled_to_props(node: LabelledNode) -> Dict[str, Any]:
    """Extract serialisable properties from a LabelledNode."""
    props: Dict[str, Any] = dict(node.properties or {})
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
