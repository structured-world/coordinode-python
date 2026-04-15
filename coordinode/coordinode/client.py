"""
CoordinodeClient — synchronous and asynchronous gRPC client for CoordiNode.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

import grpc
import grpc.aio

from coordinode._types import (
    PyValue,
    dict_to_props,
    from_property_value,
    props_to_dict,
)

logger = logging.getLogger(__name__)

# Matches "host:port" strings where host is either a bracketed IPv6 address
# ([::1], [2001:db8::1]) or a name/IPv4 with no colons.  Unbracketed IPv6
# addresses (e.g. "2001:db8::1") are intentionally NOT matched — they cannot
# be reliably distinguished from a "host:port" pair.
_HOST_PORT_RE = re.compile(r"^(\[.+\]|[^:]+):(\d+)$")

# Cypher identifier: must start with a letter or underscore, followed by
# letters, digits, or underscores.  Validated before interpolating user-supplied
# names/labels/properties into DDL strings to surface clear errors early.
_CYPHER_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_cypher_identifier(value: str, param_name: str) -> None:
    """Raise :exc:`ValueError` if *value* is not a valid Cypher identifier."""
    if not isinstance(value, str) or not _CYPHER_IDENT_RE.match(value):
        raise ValueError(
            f"{param_name} must be a valid Cypher identifier (letters, digits, underscores, "
            f"starting with a letter or underscore); got {value!r}"
        )


# ── Low-level helpers ────────────────────────────────────────────────────────


def _make_channel(host: str, port: int, tls: bool) -> grpc.Channel:
    target = f"{host}:{port}"
    if tls:
        return grpc.secure_channel(target, grpc.ssl_channel_credentials())
    return grpc.insecure_channel(target)


def _make_async_channel(host: str, port: int, tls: bool) -> grpc.aio.Channel:
    target = f"{host}:{port}"
    if tls:
        return grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())
    return grpc.aio.insecure_channel(target)


# ── Result types ─────────────────────────────────────────────────────────────


class NodeResult:
    """A node returned from a graph operation."""

    def __init__(self, proto_node: Any) -> None:
        self.id: int = proto_node.node_id
        self.labels: list[str] = list(proto_node.labels)
        self.properties: dict[str, PyValue] = props_to_dict(proto_node.properties)

    def __repr__(self) -> str:
        return f"Node(id={self.id}, labels={self.labels}, properties={self.properties})"


class EdgeResult:
    """An edge returned from a graph operation."""

    def __init__(self, proto_edge: Any) -> None:
        self.id: int = proto_edge.edge_id
        self.type: str = proto_edge.edge_type
        self.source_id: int = proto_edge.source_node_id
        self.target_id: int = proto_edge.target_node_id
        self.properties: dict[str, PyValue] = props_to_dict(proto_edge.properties)

    def __repr__(self) -> str:
        return f"Edge(id={self.id}, type={self.type!r}, {self.source_id}→{self.target_id})"


class VectorResult:
    """A vector search result."""

    def __init__(self, proto_result: Any) -> None:
        self.node = NodeResult(proto_result.node)
        self.distance: float = proto_result.distance

    def __repr__(self) -> str:
        return f"VectorResult(distance={self.distance:.4f}, node={self.node})"


class TextResult:
    """A single full-text search result with BM25 score and optional snippet."""

    def __init__(self, proto_result: Any) -> None:
        self.node_id: int = proto_result.node_id
        self.score: float = proto_result.score
        # HTML snippet with <b>…</b> highlights. Empty when unavailable.
        self.snippet: str = proto_result.snippet

    def __repr__(self) -> str:
        return f"TextResult(node_id={self.node_id}, score={self.score:.4f}, snippet={self.snippet!r})"


class HybridResult:
    """A single result from hybrid text + vector search (RRF-ranked)."""

    def __init__(self, proto_result: Any) -> None:
        self.node_id: int = proto_result.node_id
        # Combined RRF score: text_weight/(60+rank_text) + vector_weight/(60+rank_vec).
        self.score: float = proto_result.score

    def __repr__(self) -> str:
        return f"HybridResult(node_id={self.node_id}, score={self.score:.6f})"


class PropertyDefinitionInfo:
    """A property definition from the schema (name, type, required, unique)."""

    def __init__(self, proto_def: Any) -> None:
        self.name: str = proto_def.name
        self.type: int = proto_def.type
        self.required: bool = proto_def.required
        self.unique: bool = proto_def.unique

    def __repr__(self) -> str:
        return f"PropertyDefinitionInfo(name={self.name!r}, type={self.type}, required={self.required}, unique={self.unique})"


class LabelInfo:
    """A node label returned from the schema registry."""

    def __init__(self, proto_label: Any) -> None:
        self.name: str = proto_label.name
        self.version: int = proto_label.version
        self.properties: list[PropertyDefinitionInfo] = [PropertyDefinitionInfo(p) for p in proto_label.properties]
        # schema_mode: 0=unspecified, 1=strict, 2=validated, 3=flexible
        self.schema_mode: int = getattr(proto_label, "schema_mode", 0)

    def __repr__(self) -> str:
        return f"LabelInfo(name={self.name!r}, version={self.version}, properties={len(self.properties)}, schema_mode={self.schema_mode})"


class EdgeTypeInfo:
    """An edge type returned from the schema registry."""

    def __init__(self, proto_edge_type: Any) -> None:
        self.name: str = proto_edge_type.name
        self.version: int = proto_edge_type.version
        self.properties: list[PropertyDefinitionInfo] = [PropertyDefinitionInfo(p) for p in proto_edge_type.properties]
        self.schema_mode: int = getattr(proto_edge_type, "schema_mode", 0)

    def __repr__(self) -> str:
        return f"EdgeTypeInfo(name={self.name!r}, version={self.version}, properties={len(self.properties)}, schema_mode={self.schema_mode})"


class TraverseResult:
    """Result of a graph traversal: reached nodes and traversed edges."""

    def __init__(self, proto_response: Any) -> None:
        self.nodes: list[NodeResult] = [NodeResult(n) for n in proto_response.nodes]
        self.edges: list[EdgeResult] = [EdgeResult(e) for e in proto_response.edges]

    def __repr__(self) -> str:
        return f"TraverseResult(nodes={len(self.nodes)}, edges={len(self.edges)})"


class TextIndexInfo:
    """Information about a full-text index returned by :meth:`create_text_index`."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.name: str = str(row.get("index", ""))
        self.label: str = str(row.get("label", ""))
        self.properties: str = str(row.get("properties", ""))
        self.default_language: str = str(row.get("default_language", ""))
        self.documents_indexed: int = int(row.get("documents_indexed", 0))

    def __repr__(self) -> str:
        return (
            f"TextIndexInfo(name={self.name!r}, label={self.label!r},"
            f" properties={self.properties!r}, documents_indexed={self.documents_indexed})"
        )


# ── Async client ─────────────────────────────────────────────────────────────


class AsyncCoordinodeClient:
    """
    Async gRPC client for CoordiNode.

    Usage::

        async with AsyncCoordinodeClient("localhost:7080") as client:
            rows = await client.cypher("MATCH (n:Person) RETURN n.name LIMIT 5")

        # Also accepts separate host and port:
        async with AsyncCoordinodeClient("localhost", port=7080) as client:
            ...
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int | None = None,
        *,
        tls: bool = False,
        timeout: float = 30.0,
    ) -> None:
        # Support "host:port" as a single string (common gRPC convention).
        # _HOST_PORT_RE matches "hostname:port" and "[IPv6]:port" but not bare
        # IPv6 addresses, avoiding the ambiguity of rsplit(":", 1) on "::1".
        # port=None means "not specified by caller" — distinct from explicit port=7080.
        m = _HOST_PORT_RE.match(host)
        if m:
            parsed_port = int(m.group(2))
            if port is not None and port != parsed_port:
                raise ValueError(
                    f"Conflicting ports: port={port!r} (argument) vs {parsed_port!r} "
                    f"(embedded in host={host!r}). Specify the port in the host string "
                    "only, or use the port argument only."
                )
            host, port = m.group(1), parsed_port
        if port is None:
            port = 7080
        self._host = host
        self._port = port
        self._tls = tls
        self._timeout = timeout
        self._channel: grpc.aio.Channel | None = None

    async def __aenter__(self) -> AsyncCoordinodeClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        self._channel = _make_async_channel(self._host, self._port, self._tls)
        self._cypher_stub = _cypher_stub(self._channel)
        self._vector_stub = _vector_stub(self._channel)
        self._text_stub = _text_stub(self._channel)
        self._graph_stub = _graph_stub(self._channel)
        self._schema_stub = _schema_stub(self._channel)
        self._health_stub = _health_stub(self._channel)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None

    async def cypher(
        self,
        query: str,
        params: dict[str, PyValue] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an OpenCypher query. Returns rows as list of dicts."""
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            ExecuteCypherRequest,
        )

        req = ExecuteCypherRequest(
            query=query,
            parameters=dict_to_props(params or {}),
        )
        resp = await self._cypher_stub.ExecuteCypher(req, timeout=self._timeout)
        columns = list(resp.columns)
        return [{col: from_property_value(val) for col, val in zip(columns, row.values)} for row in resp.rows]

    async def vector_search(
        self,
        label: str,
        property: str,
        vector: Sequence[float],
        top_k: int = 10,
        metric: str = "cosine",
    ) -> list[VectorResult]:
        """Nearest-neighbour search on a labelled property."""
        from coordinode._proto.coordinode.v1.common.types_pb2 import Vector  # type: ignore[import]
        from coordinode._proto.coordinode.v1.query.vector_pb2 import (  # type: ignore[import]
            DistanceMetric,
            VectorSearchRequest,
        )

        metric_map = {
            "cosine": DistanceMetric.DISTANCE_METRIC_COSINE,
            "l2": DistanceMetric.DISTANCE_METRIC_L2,
            "dot": DistanceMetric.DISTANCE_METRIC_DOT,
            "l1": DistanceMetric.DISTANCE_METRIC_L1,
        }
        req = VectorSearchRequest(
            label=label,
            property=property,
            query_vector=Vector(values=[float(v) for v in vector]),
            top_k=top_k,
            metric=metric_map.get(metric.lower(), DistanceMetric.DISTANCE_METRIC_COSINE),
        )
        resp = await self._vector_stub.VectorSearch(req, timeout=self._timeout)
        return [VectorResult(r) for r in resp.results]

    async def hybrid_search(
        self,
        start_node_id: int,
        edge_type: str,
        vector: Sequence[float],
        top_k: int = 10,
        max_depth: int = 2,
        vector_property: str = "embedding",
        metric: str = "cosine",
    ) -> list[VectorResult]:
        """Graph traversal + vector search: traverse from start_node, then rank by embedding."""
        from coordinode._proto.coordinode.v1.common.types_pb2 import Vector  # type: ignore[import]
        from coordinode._proto.coordinode.v1.query.vector_pb2 import (  # type: ignore[import]
            DistanceMetric,
            HybridSearchRequest,
        )

        metric_map = {
            "cosine": DistanceMetric.DISTANCE_METRIC_COSINE,
            "l2": DistanceMetric.DISTANCE_METRIC_L2,
            "dot": DistanceMetric.DISTANCE_METRIC_DOT,
            "l1": DistanceMetric.DISTANCE_METRIC_L1,
        }
        req = HybridSearchRequest(
            start_node_id=start_node_id,
            edge_type=edge_type,
            max_depth=max_depth,
            vector_property=vector_property,
            query_vector=Vector(values=[float(v) for v in vector]),
            top_k=top_k,
            metric=metric_map.get(metric.lower(), DistanceMetric.DISTANCE_METRIC_COSINE),
        )
        resp = await self._vector_stub.HybridSearch(req, timeout=self._timeout)
        return [VectorResult(r) for r in resp.results]

    async def create_node(self, labels: list[str], properties: dict[str, PyValue]) -> NodeResult:
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import CreateNodeRequest  # type: ignore[import]

        req = CreateNodeRequest(labels=labels, properties=dict_to_props(properties))
        node = await self._graph_stub.CreateNode(req, timeout=self._timeout)
        return NodeResult(node)

    async def get_node(self, node_id: int) -> NodeResult:
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import GetNodeRequest  # type: ignore[import]

        req = GetNodeRequest(node_id=node_id)
        node = await self._graph_stub.GetNode(req, timeout=self._timeout)
        return NodeResult(node)

    async def create_edge(
        self,
        edge_type: str,
        source_id: int,
        target_id: int,
        properties: dict[str, PyValue] | None = None,
    ) -> EdgeResult:
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import CreateEdgeRequest  # type: ignore[import]

        req = CreateEdgeRequest(
            edge_type=edge_type,
            source_node_id=source_id,
            target_node_id=target_id,
            properties=dict_to_props(properties or {}),
        )
        edge = await self._graph_stub.CreateEdge(req, timeout=self._timeout)
        return EdgeResult(edge)

    async def get_schema_text(self) -> str:
        """Return schema as a human/LLM-readable string."""
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import (  # type: ignore[import]
            ListEdgeTypesRequest,
            ListLabelsRequest,
            PropertyType,  # type: ignore[import]
        )

        _type_name = {
            PropertyType.PROPERTY_TYPE_INT64: "INT64",
            PropertyType.PROPERTY_TYPE_FLOAT64: "FLOAT64",
            PropertyType.PROPERTY_TYPE_STRING: "STRING",
            PropertyType.PROPERTY_TYPE_BOOL: "BOOL",
            PropertyType.PROPERTY_TYPE_BYTES: "BYTES",
            PropertyType.PROPERTY_TYPE_TIMESTAMP: "TIMESTAMP",
            PropertyType.PROPERTY_TYPE_VECTOR: "VECTOR",
            PropertyType.PROPERTY_TYPE_LIST: "LIST",
            PropertyType.PROPERTY_TYPE_MAP: "MAP",
        }

        labels_resp = await self._schema_stub.ListLabels(ListLabelsRequest(), timeout=self._timeout)
        edges_resp = await self._schema_stub.ListEdgeTypes(ListEdgeTypesRequest(), timeout=self._timeout)

        lines = ["Node labels:"]
        for label in labels_resp.labels:
            props = ", ".join(f"{p.name}: {_type_name.get(p.type, '?')}" for p in label.properties)
            lines.append(f"  - {label.name} (properties: {props})" if props else f"  - {label.name}")

        lines.append("\nEdge types:")
        for et in edges_resp.edge_types:
            props = ", ".join(f"{p.name}: {_type_name.get(p.type, '?')}" for p in et.properties)
            lines.append(f"  - {et.name} (properties: {props})" if props else f"  - {et.name}")

        return "\n".join(lines)

    async def get_labels(self) -> list[LabelInfo]:
        """Return all node labels defined in the schema."""
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import ListLabelsRequest  # type: ignore[import]

        resp = await self._schema_stub.ListLabels(ListLabelsRequest(), timeout=self._timeout)
        return [LabelInfo(label) for label in resp.labels]

    async def get_edge_types(self) -> list[EdgeTypeInfo]:
        """Return all edge types defined in the schema."""
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import ListEdgeTypesRequest  # type: ignore[import]

        resp = await self._schema_stub.ListEdgeTypes(ListEdgeTypesRequest(), timeout=self._timeout)
        return [EdgeTypeInfo(et) for et in resp.edge_types]

    @staticmethod
    def _validate_property_dict(p: Any, idx: int) -> tuple[str, str, bool, bool]:
        """Validate a single property dict and return ``(name, type_str, required, unique)``."""
        if not isinstance(p, dict):
            raise ValueError(f"Property at index {idx} must be a dict; got {p!r}")
        name = p.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Property at index {idx} must have a non-empty 'name' key; got {p!r}")
        raw_type = p.get("type", "string")
        if "type" in p and not isinstance(raw_type, str):
            raise ValueError(f"Property {name!r} must use a string value for 'type'; got {raw_type!r}")
        type_str = str(raw_type).strip().lower()
        required = p.get("required", False)
        unique = p.get("unique", False)
        if not isinstance(required, bool) or not isinstance(unique, bool):
            raise ValueError(
                f"Property {name!r} must use boolean values for 'required' and 'unique'; got "
                f"required={required!r}, unique={unique!r}"
            )
        return name, type_str, required, unique

    @staticmethod
    def _build_property_definitions(
        properties: list[dict[str, Any]] | None,
        property_type_cls: Any,
        property_definition_cls: Any,
    ) -> list[Any]:
        """Convert property dicts to proto PropertyDefinition objects.

        Shared by :meth:`create_label` and :meth:`create_edge_type` to avoid
        duplicating the type-map and validation logic.
        """
        type_map = {
            "int64": property_type_cls.PROPERTY_TYPE_INT64,
            "float64": property_type_cls.PROPERTY_TYPE_FLOAT64,
            "string": property_type_cls.PROPERTY_TYPE_STRING,
            "bool": property_type_cls.PROPERTY_TYPE_BOOL,
            "bytes": property_type_cls.PROPERTY_TYPE_BYTES,
            "timestamp": property_type_cls.PROPERTY_TYPE_TIMESTAMP,
            "vector": property_type_cls.PROPERTY_TYPE_VECTOR,
            "list": property_type_cls.PROPERTY_TYPE_LIST,
            "map": property_type_cls.PROPERTY_TYPE_MAP,
        }
        if properties is None:
            return []
        if not isinstance(properties, (list, tuple)):
            raise ValueError(f"'properties' must be a list of property dicts or None; got {type(properties).__name__}")
        result = []
        for idx, p in enumerate(properties):
            name, type_str, required, unique = AsyncCoordinodeClient._validate_property_dict(p, idx)
            if type_str not in type_map:
                raise ValueError(
                    f"Unknown property type {type_str!r} for property {name!r}. "
                    f"Expected 'type' to be one of: {sorted(type_map)}"
                )
            result.append(
                property_definition_cls(
                    name=name,
                    type=type_map[type_str],
                    required=required,
                    unique=unique,
                )
            )
        return result

    async def create_label(
        self,
        name: str,
        properties: list[dict[str, Any]] | None = None,
        *,
        schema_mode: str = "strict",
    ) -> LabelInfo:
        """Create a node label in the schema registry.

        Args:
            name: Label name (e.g. ``"Person"``).
            properties: Optional list of property dicts with keys
                ``name`` (str), ``type`` (str), ``required`` (bool),
                ``unique`` (bool).  Type strings: ``"string"``,
                ``"int64"``, ``"float64"``, ``"bool"``, ``"bytes"``,
                ``"timestamp"``, ``"vector"``, ``"list"``, ``"map"``.
            schema_mode: ``"strict"`` (default — reject undeclared props),
                ``"validated"`` (allow extra props without interning),
                ``"flexible"`` (no enforcement).
        """
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import (  # type: ignore[import]
            CreateLabelRequest,
            PropertyDefinition,
            PropertyType,
            SchemaMode,
        )

        _mode_map = {
            "strict": SchemaMode.SCHEMA_MODE_STRICT,
            "validated": SchemaMode.SCHEMA_MODE_VALIDATED,
            "flexible": SchemaMode.SCHEMA_MODE_FLEXIBLE,
        }
        if not isinstance(schema_mode, str):
            raise ValueError(f"schema_mode must be a str, got {type(schema_mode).__name__!r}")
        schema_mode_normalized = schema_mode.strip().lower()
        if schema_mode_normalized not in _mode_map:
            raise ValueError(f"schema_mode must be one of {list(_mode_map)}, got {schema_mode!r}")

        proto_props = self._build_property_definitions(properties, PropertyType, PropertyDefinition)
        req = CreateLabelRequest(
            name=name,
            properties=proto_props,
            schema_mode=_mode_map[schema_mode_normalized],
        )
        label = await self._schema_stub.CreateLabel(req, timeout=self._timeout)
        return LabelInfo(label)

    async def create_edge_type(
        self,
        name: str,
        properties: list[dict[str, Any]] | None = None,
    ) -> EdgeTypeInfo:
        """Create an edge type in the schema registry.

        Args:
            name: Edge type name (e.g. ``"KNOWS"``).
            properties: Optional list of property dicts with keys
                ``name`` (str), ``type`` (str), ``required`` (bool),
                ``unique`` (bool). Same type strings as :meth:`create_label`.
        """
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import (  # type: ignore[import]
            CreateEdgeTypeRequest,
            PropertyDefinition,
            PropertyType,
        )

        proto_props = self._build_property_definitions(properties, PropertyType, PropertyDefinition)
        req = CreateEdgeTypeRequest(name=name, properties=proto_props)
        et = await self._schema_stub.CreateEdgeType(req, timeout=self._timeout)
        return EdgeTypeInfo(et)

    async def create_text_index(
        self,
        name: str,
        label: str,
        properties: str | list[str],
        *,
        language: str = "",
    ) -> TextIndexInfo:
        """Create a full-text (BM25) index on one or more node properties.

        Args:
            name: Unique index name (e.g. ``"article_body"``).
            label: Node label to index (e.g. ``"Article"``).
            properties: Property name or list of property names to index
                (e.g. ``"body"`` or ``["title", "body"]``).
            language: Default stemming/tokenization language (e.g. ``"english"``,
                ``"russian"``).  Empty string uses the server default
                (``"english"``).

        Returns:
            :class:`TextIndexInfo` with index metadata and document count.

        Example::

            info = await client.create_text_index("article_body", "Article", "body")
            # then: results = await client.text_search("Article", "machine learning")
        """
        _validate_cypher_identifier(name, "name")
        _validate_cypher_identifier(label, "label")
        if isinstance(properties, str):
            prop_list = [properties]
        else:
            prop_list = list(properties)
        if not prop_list:
            raise ValueError("'properties' must contain at least one property name")
        for prop in prop_list:
            _validate_cypher_identifier(prop, "property")
        if language:
            _validate_cypher_identifier(language, "language")
        props_expr = ", ".join(prop_list)
        lang_clause = f" DEFAULT LANGUAGE {language}" if language else ""
        cypher = f"CREATE TEXT INDEX {name} ON :{label}({props_expr}){lang_clause}"
        rows = await self.cypher(cypher)
        if rows:
            return TextIndexInfo(rows[0])
        return TextIndexInfo({"index": name, "label": label, "properties": ", ".join(prop_list)})

    async def drop_text_index(self, name: str) -> None:
        """Drop a full-text index by name.

        Args:
            name: Index name previously passed to :meth:`create_text_index`.

        Example::

            await client.drop_text_index("article_body")
        """
        _validate_cypher_identifier(name, "name")
        await self.cypher(f"DROP TEXT INDEX {name}")

    async def traverse(
        self,
        start_node_id: int,
        edge_type: str,
        direction: str = "outbound",
        max_depth: int = 1,
    ) -> TraverseResult:
        """Traverse the graph from *start_node_id* following *edge_type* edges.

        Args:
            start_node_id: ID of the node to start from.
            edge_type: Edge type label to follow (e.g. ``"KNOWS"``).
            direction: ``"outbound"`` (default), ``"inbound"``, or ``"both"``.
            max_depth: Maximum hop count (default 1).

        Returns:
            :class:`TraverseResult` with ``nodes`` and ``edges`` lists.
        """
        # Validate pure string/int inputs before importing proto stubs — ensures ValueError
        # is raised even when proto stubs have not been generated yet.
        # Type guards come first so that wrong types raise ValueError, not AttributeError/TypeError.
        if not isinstance(direction, str):
            raise ValueError(f"direction must be a str, got {type(direction).__name__!r}.")
        _valid_directions = {"outbound", "inbound", "both"}
        key = direction.lower()
        if key not in _valid_directions:
            raise ValueError(f"Invalid direction {direction!r}. Must be one of: 'outbound', 'inbound', 'both'.")
        # bool is a subclass of int in Python, so `isinstance(True, int)` is True — exclude it.
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
            raise ValueError(f"max_depth must be an integer >= 1, got {max_depth!r}.")

        from coordinode._proto.coordinode.v1.graph.graph_pb2 import (  # type: ignore[import]
            TraversalDirection,
            TraverseRequest,
        )

        _direction_map = {
            "outbound": TraversalDirection.TRAVERSAL_DIRECTION_OUTBOUND,
            "inbound": TraversalDirection.TRAVERSAL_DIRECTION_INBOUND,
            "both": TraversalDirection.TRAVERSAL_DIRECTION_BOTH,
        }
        direction_value = _direction_map[key]

        req = TraverseRequest(
            start_node_id=start_node_id,
            edge_type=edge_type,
            direction=direction_value,
            max_depth=max_depth,
        )
        resp = await self._graph_stub.Traverse(req, timeout=self._timeout)
        return TraverseResult(resp)

    async def text_search(
        self,
        label: str,
        query: str,
        *,
        limit: int = 10,
        fuzzy: bool = False,
        language: str = "",
    ) -> list[TextResult]:
        """Run a full-text BM25 search over all indexed text properties for *label*.

        Args:
            label: Node label to search (e.g. ``"Article"``).
            query: Full-text query string. Supports boolean operators (``AND``,
                ``OR``, ``NOT``), phrase search (``"exact phrase"``), prefix
                wildcards (``term*``), and per-term boosting (``term^N``).
            limit: Maximum results to return (default 10). The server may apply
                its own upper bound; pass a reasonable value (e.g. ≤ 1000).
            fuzzy: If ``True``, apply Levenshtein-1 fuzzy matching to individual
                terms. Increases recall at the cost of precision.
            language: Tokenization/stemming language (e.g. ``"english"``,
                ``"russian"``). Empty string uses the index's default language.

        Returns:
            List of :class:`TextResult` ordered by BM25 score descending.
            Returns ``[]`` if no text index exists for *label*.

        Note:
            Text indexing is **not** automatic.  Before calling this method,
            create a full-text index with the Cypher DDL statement::

                CREATE TEXT INDEX my_index ON :Label(property)

            or via :meth:`create_text_index`.  Nodes written before the index
            was created are indexed immediately at DDL execution time.
        """
        from coordinode._proto.coordinode.v1.query.text_pb2 import TextSearchRequest  # type: ignore[import]

        req = TextSearchRequest(label=label, query=query, limit=limit, fuzzy=fuzzy, language=language)
        resp = await self._text_stub.TextSearch(req, timeout=self._timeout)
        return [TextResult(r) for r in resp.results]

    async def hybrid_text_vector_search(
        self,
        label: str,
        text_query: str,
        vector: Sequence[float],
        *,
        limit: int = 10,
        text_weight: float = 0.5,
        vector_weight: float = 0.5,
        vector_property: str = "embedding",
    ) -> list[HybridResult]:
        """Fuse BM25 text search and cosine vector search using Reciprocal Rank Fusion (RRF).

        Runs text and vector searches independently, then combines their ranked
        lists::

            rrf_score(node) = text_weight / (60 + rank_text)
                            + vector_weight / (60 + rank_vec)

        Args:
            label: Node label to search (e.g. ``"Article"``).
            text_query: Full-text query string (same syntax as :meth:`text_search`).
            vector: Query embedding vector. Must match the dimensionality stored
                in *vector_property*.
            limit: Maximum fused results to return (default 10). The server may
                apply its own upper bound; pass a reasonable value (e.g. ≤ 1000).
            text_weight: Weight for the BM25 component (default 0.5).
            vector_weight: Weight for the cosine component (default 0.5).
            vector_property: Node property containing the embedding (default
                ``"embedding"``).

        Returns:
            List of :class:`HybridResult` ordered by RRF score descending.
        """
        from coordinode._proto.coordinode.v1.query.text_pb2 import (  # type: ignore[import]
            HybridTextVectorSearchRequest,
        )

        req = HybridTextVectorSearchRequest(
            label=label,
            text_query=text_query,
            vector=[float(v) for v in vector],
            limit=limit,
            text_weight=text_weight,
            vector_weight=vector_weight,
            vector_property=vector_property,
        )
        resp = await self._text_stub.HybridTextVectorSearch(req, timeout=self._timeout)
        return [HybridResult(r) for r in resp.results]

    async def health(self) -> bool:
        from coordinode._proto.coordinode.v1.health.health_pb2 import (  # type: ignore[import]
            HealthCheckRequest,
            ServingStatus,
        )

        try:
            resp = await self._health_stub.Check(HealthCheckRequest(), timeout=5.0)
            return resp.status == ServingStatus.SERVING_STATUS_SERVING
        except grpc.RpcError as e:
            logger.debug(
                "health check failed: %s %s",
                e.code(),  # type: ignore[union-attr]
                e.details(),  # type: ignore[union-attr]
            )
            return False


# ── Sync client (wraps async) ─────────────────────────────────────────────────


class CoordinodeClient:
    """
    Synchronous gRPC client for CoordiNode.

    Usage::

        with CoordinodeClient("localhost:7080") as client:
            rows = client.cypher("MATCH (n:Person) RETURN n.name LIMIT 5")
            print(rows)  # [{"n.name": "Alice"}, ...]
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int | None = None,
        *,
        tls: bool = False,
        timeout: float = 30.0,
    ) -> None:
        self._async = AsyncCoordinodeClient(host, port, tls=tls, timeout=timeout)
        self._loop = asyncio.new_event_loop()
        self._connected = False

    def __enter__(self) -> CoordinodeClient:
        if not self._connected:
            self._loop.run_until_complete(self._async.connect())
            self._connected = True
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying gRPC channel and event loop."""
        if self._connected:
            self._loop.run_until_complete(self._async.close())
            self._connected = False
        if not self._loop.is_closed():
            self._loop.close()

    def _run(self, coro: Any) -> Any:
        if self._loop.is_closed():
            raise RuntimeError("CoordinodeClient has been closed and cannot be reused")
        if not self._connected:
            self._loop.run_until_complete(self._async.connect())
            self._connected = True
        return self._loop.run_until_complete(coro)

    def cypher(
        self,
        query: str,
        params: dict[str, PyValue] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an OpenCypher query. Returns rows as list of dicts."""
        return self._run(self._async.cypher(query, params))

    def vector_search(
        self,
        label: str,
        property: str,
        vector: Sequence[float],
        top_k: int = 10,
        metric: str = "cosine",
    ) -> list[VectorResult]:
        return self._run(self._async.vector_search(label, property, vector, top_k, metric))

    def hybrid_search(
        self,
        start_node_id: int,
        edge_type: str,
        vector: Sequence[float],
        top_k: int = 10,
        max_depth: int = 2,
        vector_property: str = "embedding",
        metric: str = "cosine",
    ) -> list[VectorResult]:
        return self._run(
            self._async.hybrid_search(start_node_id, edge_type, vector, top_k, max_depth, vector_property, metric)
        )

    def create_node(self, labels: list[str], properties: dict[str, PyValue]) -> NodeResult:
        return self._run(self._async.create_node(labels, properties))

    def get_node(self, node_id: int) -> NodeResult:
        return self._run(self._async.get_node(node_id))

    def create_edge(
        self,
        edge_type: str,
        source_id: int,
        target_id: int,
        properties: dict[str, PyValue] | None = None,
    ) -> EdgeResult:
        return self._run(self._async.create_edge(edge_type, source_id, target_id, properties))

    def get_schema_text(self) -> str:
        return self._run(self._async.get_schema_text())

    def get_labels(self) -> list[LabelInfo]:
        """Return all node labels defined in the schema."""
        return self._run(self._async.get_labels())

    def get_edge_types(self) -> list[EdgeTypeInfo]:
        """Return all edge types defined in the schema."""
        return self._run(self._async.get_edge_types())

    def create_label(
        self,
        name: str,
        properties: list[dict[str, Any]] | None = None,
        *,
        schema_mode: str = "strict",
    ) -> LabelInfo:
        """Create a node label in the schema registry."""
        return self._run(self._async.create_label(name, properties, schema_mode=schema_mode))

    def create_edge_type(
        self,
        name: str,
        properties: list[dict[str, Any]] | None = None,
    ) -> EdgeTypeInfo:
        """Create an edge type in the schema registry."""
        return self._run(self._async.create_edge_type(name, properties))

    def create_text_index(
        self,
        name: str,
        label: str,
        properties: str | list[str],
        *,
        language: str = "",
    ) -> TextIndexInfo:
        """Create a full-text (BM25) index on one or more node properties."""
        return self._run(self._async.create_text_index(name, label, properties, language=language))

    def drop_text_index(self, name: str) -> None:
        """Drop a full-text index by name."""
        return self._run(self._async.drop_text_index(name))

    def traverse(
        self,
        start_node_id: int,
        edge_type: str,
        direction: str = "outbound",
        max_depth: int = 1,
    ) -> TraverseResult:
        """Traverse the graph from *start_node_id* following *edge_type* edges."""
        return self._run(self._async.traverse(start_node_id, edge_type, direction, max_depth))

    def text_search(
        self,
        label: str,
        query: str,
        *,
        limit: int = 10,
        fuzzy: bool = False,
        language: str = "",
    ) -> list[TextResult]:
        """Run a full-text BM25 search over all indexed text properties for *label*."""
        return self._run(self._async.text_search(label, query, limit=limit, fuzzy=fuzzy, language=language))

    def hybrid_text_vector_search(
        self,
        label: str,
        text_query: str,
        vector: Sequence[float],
        *,
        limit: int = 10,
        text_weight: float = 0.5,
        vector_weight: float = 0.5,
        vector_property: str = "embedding",
    ) -> list[HybridResult]:
        """Fuse BM25 text search and cosine vector search using RRF ranking."""
        return self._run(
            self._async.hybrid_text_vector_search(
                label,
                text_query,
                vector,
                limit=limit,
                text_weight=text_weight,
                vector_weight=vector_weight,
                vector_property=vector_property,
            )
        )

    def health(self) -> bool:
        return self._run(self._async.health())


# ── Stub factories (deferred import) ─────────────────────────────────────────


def _cypher_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.cypher_pb2_grpc import CypherServiceStub  # type: ignore[import]

    return CypherServiceStub(channel)


def _vector_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.vector_pb2_grpc import VectorServiceStub  # type: ignore[import]

    return VectorServiceStub(channel)


def _text_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.text_pb2_grpc import TextServiceStub  # type: ignore[import]

    return TextServiceStub(channel)


def _graph_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.graph.graph_pb2_grpc import GraphServiceStub  # type: ignore[import]

    return GraphServiceStub(channel)


def _schema_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.graph.schema_pb2_grpc import SchemaServiceStub  # type: ignore[import]

    return SchemaServiceStub(channel)


def _health_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.health.health_pb2_grpc import HealthServiceStub  # type: ignore[import]

    return HealthServiceStub(channel)
