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

    def __repr__(self) -> str:
        return f"LabelInfo(name={self.name!r}, version={self.version}, properties={len(self.properties)})"


class EdgeTypeInfo:
    """An edge type returned from the schema registry."""

    def __init__(self, proto_edge_type: Any) -> None:
        self.name: str = proto_edge_type.name
        self.version: int = proto_edge_type.version
        self.properties: list[PropertyDefinitionInfo] = [PropertyDefinitionInfo(p) for p in proto_edge_type.properties]

    def __repr__(self) -> str:
        return f"EdgeTypeInfo(name={self.name!r}, version={self.version}, properties={len(self.properties)})"


class TraverseResult:
    """Result of a graph traversal: reached nodes and traversed edges."""

    def __init__(self, proto_response: Any) -> None:
        self.nodes: list[NodeResult] = [NodeResult(n) for n in proto_response.nodes]
        self.edges: list[EdgeResult] = [EdgeResult(e) for e in proto_response.edges]

    def __repr__(self) -> str:
        return f"TraverseResult(nodes={len(self.nodes)}, edges={len(self.edges)})"


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
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import (  # type: ignore[import]
            TraversalDirection,
            TraverseRequest,
        )

        _direction_map = {
            "outbound": TraversalDirection.TRAVERSAL_DIRECTION_OUTBOUND,
            "inbound": TraversalDirection.TRAVERSAL_DIRECTION_INBOUND,
            "both": TraversalDirection.TRAVERSAL_DIRECTION_BOTH,
        }
        key = direction.lower()
        if key not in _direction_map:
            raise ValueError(f"Invalid direction {direction!r}. Must be one of: 'outbound', 'inbound', 'both'.")
        direction_value = _direction_map[key]

        req = TraverseRequest(
            start_node_id=start_node_id,
            edge_type=edge_type,
            direction=direction_value,
            max_depth=max_depth,
        )
        resp = await self._graph_stub.Traverse(req, timeout=self._timeout)
        return TraverseResult(resp)

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

    def traverse(
        self,
        start_node_id: int,
        edge_type: str,
        direction: str = "outbound",
        max_depth: int = 1,
    ) -> TraverseResult:
        """Traverse the graph from *start_node_id* following *edge_type* edges."""
        return self._run(self._async.traverse(start_node_id, edge_type, direction, max_depth))

    def health(self) -> bool:
        return self._run(self._async.health())


# ── Stub factories (deferred import) ─────────────────────────────────────────


def _cypher_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.cypher_pb2_grpc import CypherServiceStub  # type: ignore[import]

    return CypherServiceStub(channel)


def _vector_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.vector_pb2_grpc import VectorServiceStub  # type: ignore[import]

    return VectorServiceStub(channel)


def _graph_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.graph.graph_pb2_grpc import GraphServiceStub  # type: ignore[import]

    return GraphServiceStub(channel)


def _schema_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.graph.schema_pb2_grpc import SchemaServiceStub  # type: ignore[import]

    return SchemaServiceStub(channel)


def _health_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.health.health_pb2_grpc import HealthServiceStub  # type: ignore[import]

    return HealthServiceStub(channel)
