"""Unit tests for the schema and traversal result wrappers.

These build the real generated proto messages rather than stand-ins, so a
field rename or removal in the proto submodule fails here instead of reaching
users as an AttributeError at runtime.
"""

import asyncio

import pytest

from coordinode._proto.coordinode.v1.graph import graph_pb2
from coordinode._proto.coordinode.v1.query import vector_pb2
from coordinode._proto.coordinode.v2.graph import schema_pb2
from coordinode._types import to_property_value
from coordinode.client import (
    AsyncCoordinodeClient,
    ConstraintInfo,
    EdgeResult,
    EdgeTypeInfo,
    LabelInfo,
    NodeResult,
    PropertyDefinitionInfo,
    TraverseResult,
)

# ── Real proto message builders ──────────────────────────────────────────────


def _scalar(s: int):
    return schema_pb2.PropertyType(scalar=s)


def _prop_def(name: str, type_=None, required: bool = False):
    return schema_pb2.PropertyDefinition(
        name=name,
        type=type_ if type_ is not None else _scalar(schema_pb2.SCALAR_TYPE_STRING),
        required=required,
    )


def _label(name: str, schema_revision: int = 1, properties=None, schema_mode: int = 0):
    return schema_pb2.Label(
        name=name,
        declared=True,
        schema_revision=schema_revision,
        properties=properties or [],
        schema_mode=schema_mode,
    )


def _edge_type(name: str, schema_revision: int = 1, properties=None):
    return schema_pb2.EdgeType(name=name, declared=True, schema_revision=schema_revision, properties=properties or [])


def _node(node_id: int, labels=None, properties=None, element_id: str = ""):
    return graph_pb2.Node(
        node_id=node_id,
        labels=labels or [],
        properties=properties or {},
        element_id=element_id,
    )


def _edge(edge_id: int, edge_type: str, source: int, target: int, properties=None, element_id: str = ""):
    return graph_pb2.Edge(
        edge_id=edge_id,
        edge_type=edge_type,
        source_node_id=source,
        target_node_id=target,
        properties=properties or {},
        element_id=element_id,
    )


def _traverse_response(nodes=None, edges=None):
    return graph_pb2.TraverseResponse(nodes=nodes or [], edges=edges or [])


# ── PropertyDefinitionInfo ───────────────────────────────────────────────────


class TestPropertyDefinitionInfo:
    def test_fields_are_mapped(self):
        p = PropertyDefinitionInfo(_prop_def("name", required=True))
        assert p.name == "name"
        assert p.type == "STRING"
        assert p.required is True
        assert p.default is None
        # A type definition carries no uniqueness; it is a constraint.
        assert not hasattr(p, "unique")

    def test_repr_contains_name(self):
        p = PropertyDefinitionInfo(_prop_def("age", _scalar(schema_pb2.SCALAR_TYPE_INT64)))
        assert "age" in repr(p)

    def test_structured_types_render_as_ddl(self):
        vector = schema_pb2.PropertyType(
            vector=schema_pb2.VectorType(dimensions=384, metric=vector_pb2.DISTANCE_METRIC_L2)
        )
        tags = schema_pb2.PropertyType(array=schema_pb2.ArrayType(element=_scalar(schema_pb2.SCALAR_TYPE_STRING)))
        assert PropertyDefinitionInfo(_prop_def("emb", vector)).type == "VECTOR(384, L2)"
        assert PropertyDefinitionInfo(_prop_def("tags", tags)).type == "LIST<STRING>"

    def test_default_value_is_decoded(self):
        d = _prop_def("status")
        d.default_value.CopyFrom(to_property_value("active"))
        assert PropertyDefinitionInfo(d).default == "active"


# ── LabelInfo ────────────────────────────────────────────────────────────────


class TestLabelInfo:
    def test_empty_properties(self):
        label = LabelInfo(_label("Person", schema_revision=2))
        assert label.name == "Person"
        assert label.schema_revision == 2
        assert label.properties == []

    def test_properties_are_wrapped(self):
        props = [_prop_def("name"), _prop_def("age", _scalar(schema_pb2.SCALAR_TYPE_INT64))]
        label = LabelInfo(_label("User", properties=props))
        assert len(label.properties) == 2
        assert all(isinstance(p, PropertyDefinitionInfo) for p in label.properties)
        assert label.properties[0].name == "name"
        assert label.properties[1].name == "age"

    def test_repr_contains_name(self):
        label = LabelInfo(_label("Movie"))
        assert "Movie" in repr(label)

    def test_schema_revision_zero(self):
        # Schema registry may return revision 0 for a newly created label.
        label = LabelInfo(_label("Draft", schema_revision=0))
        assert label.schema_revision == 0

    def test_schema_mode_defaults_to_zero(self):
        label = LabelInfo(_label("Person"))
        assert label.schema_mode == 0

    def test_schema_mode_strict(self):
        label = LabelInfo(_label("Person", schema_mode=1))
        assert label.schema_mode == 1

    def test_schema_mode_validated(self):
        label = LabelInfo(_label("Person", schema_mode=2))
        assert label.schema_mode == 2

    def test_schema_mode_flexible(self):
        label = LabelInfo(_label("Person", schema_mode=3))
        assert label.schema_mode == 3

    def test_schema_mode_in_repr(self):
        label = LabelInfo(_label("Person", schema_mode=1))
        assert "schema_mode" in repr(label)


# ── EdgeTypeInfo ─────────────────────────────────────────────────────────────


class TestEdgeTypeInfo:
    def test_basic_fields(self):
        et = EdgeTypeInfo(_edge_type("KNOWS", schema_revision=1))
        assert et.name == "KNOWS"
        assert et.schema_revision == 1
        assert et.properties == []

    def test_properties_are_wrapped(self):
        props = [_prop_def("since", _scalar(schema_pb2.SCALAR_TYPE_TIMESTAMP))]
        et = EdgeTypeInfo(_edge_type("FOLLOWS", properties=props))
        assert len(et.properties) == 1
        assert et.properties[0].name == "since"
        assert et.properties[0].type == "TIMESTAMP"

    def test_repr_contains_name(self):
        et = EdgeTypeInfo(_edge_type("RATED"))
        assert "RATED" in repr(et)


# ── ConstraintInfo ───────────────────────────────────────────────────────────


class TestConstraintInfo:
    def test_unique_constraint_names_its_index(self):
        c = ConstraintInfo(
            schema_pb2.Constraint(
                name="user_email",
                label="User",
                properties=["email"],
                kind=schema_pb2.CONSTRAINT_KIND_UNIQUE,
                state=schema_pb2.CONSTRAINT_STATE_ACTIVE,
                backing_index="user_email",
            )
        )
        assert (c.name, c.label, c.properties) == ("user_email", "User", ["email"])
        assert (c.kind, c.state, c.backing_index) == ("UNIQUE", "ACTIVE", "user_email")
        assert c.property_type is None

    def test_type_constraint_names_its_type_and_owns_no_index(self):
        c = ConstraintInfo(
            schema_pb2.Constraint(
                name="item_qty",
                label="Item",
                properties=["qty"],
                kind=schema_pb2.CONSTRAINT_KIND_PROPERTY_TYPE,
                property_type=_scalar(schema_pb2.SCALAR_TYPE_INT64),
                state=schema_pb2.CONSTRAINT_STATE_VALIDATING,
            )
        )
        assert (c.kind, c.property_type, c.state) == ("PROPERTY_TYPE", "INT64", "VALIDATING")
        assert c.backing_index is None


# ── element_id ───────────────────────────────────────────────────────────────


class TestElementId:
    """The canonical opaque identifier the server added alongside the raw ids."""

    def test_node_exposes_element_id(self):
        n = NodeResult(_node(42, ["Person"], element_id="0000000000012"))
        assert n.element_id == "0000000000012"
        # The raw id stays available for Neo4j v4 driver compatibility.
        assert n.id == 42

    def test_node_element_id_is_empty_when_server_omits_it(self):
        assert NodeResult(_node(1, ["Person"])).element_id == ""

    def test_node_element_id_in_repr(self):
        assert "0000000000012" in repr(NodeResult(_node(42, element_id="0000000000012")))

    def test_edge_exposes_endpoint_element_id(self):
        e = EdgeResult(_edge(10, "KNOWS", 1, 2, element_id="00000000000010000000000002"))
        assert e.element_id == "00000000000010000000000002"
        assert (e.source_id, e.target_id) == (1, 2)

    def test_edge_element_id_is_empty_when_server_omits_it(self):
        assert EdgeResult(_edge(10, "KNOWS", 1, 2)).element_id == ""


# ── TraverseResult ───────────────────────────────────────────────────────────


class TestTraverseResult:
    def test_empty_response(self):
        result = TraverseResult(_traverse_response())
        assert result.nodes == []
        assert result.edges == []

    def test_nodes_are_wrapped_as_node_results(self):
        nodes = [_node(1, ["Person"]), _node(2, ["Movie"])]
        result = TraverseResult(_traverse_response(nodes=nodes))
        assert len(result.nodes) == 2
        assert all(isinstance(n, NodeResult) for n in result.nodes)
        assert result.nodes[0].id == 1
        assert result.nodes[1].id == 2

    def test_edges_are_wrapped_as_edge_results(self):
        edges = [_edge(10, "KNOWS", source=1, target=2)]
        result = TraverseResult(_traverse_response(edges=edges))
        assert len(result.edges) == 1
        assert isinstance(result.edges[0], EdgeResult)
        assert result.edges[0].id == 10
        assert result.edges[0].source_id == 1
        assert result.edges[0].target_id == 2
        assert result.edges[0].type == "KNOWS"

    def test_mixed_nodes_and_edges(self):
        nodes = [_node(1, ["A"]), _node(2, ["B"]), _node(3, ["C"])]
        edges = [
            _edge(10, "REL", 1, 2),
            _edge(11, "REL", 2, 3),
        ]
        result = TraverseResult(_traverse_response(nodes=nodes, edges=edges))
        assert len(result.nodes) == 3
        assert len(result.edges) == 2

    def test_repr_shows_counts(self):
        nodes = [_node(1, [])]
        result = TraverseResult(_traverse_response(nodes=nodes))
        r = repr(result)
        assert "nodes=1" in r
        assert "edges=0" in r


# ── traverse() input validation ──────────────────────────────────────────────


class TestBuildPropertyDefinitions:
    """Unit tests for AsyncCoordinodeClient._build_property_definitions() validation.

    Validation runs before any RPC call, so no running server is required.
    """

    def test_non_dict_property_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            AsyncCoordinodeClient._build_property_definitions(["not-a-dict"])

    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="non-empty 'name' key"):
            AsyncCoordinodeClient._build_property_definitions([{"type": "string"}])

    def test_non_bool_required_raises(self):
        with pytest.raises(ValueError, match="boolean 'required'"):
            AsyncCoordinodeClient._build_property_definitions([{"name": "x", "required": "true"}])

    def test_unique_is_refused_with_a_pointer_to_constraints(self):
        """Uniqueness is a constraint, never a property flag: even unique=False is refused,
        so a caller moving from the old API cannot silently lose a guarantee."""
        for flag in (True, False):
            with pytest.raises(ValueError, match="create_constraint"):
                AsyncCoordinodeClient._build_property_definitions([{"name": "x", "unique": flag}])

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="unknown keys"):
            AsyncCoordinodeClient._build_property_definitions([{"name": "x", "nullable": True}])

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown type"):
            AsyncCoordinodeClient._build_property_definitions([{"name": "x", "type": "bytes"}])

    def test_types_defaults_and_requiredness_reach_the_wire(self):
        defs = AsyncCoordinodeClient._build_property_definitions(
            [
                {"name": "name", "required": True},
                {"name": "emb", "type": {"type": "vector", "dimensions": 384, "metric": "l2"}},
                {"name": "tags", "type": {"type": "list", "element": "string"}},
                {"name": "status", "type": "string", "default": "active"},
            ]
        )
        assert [d.name for d in defs] == ["name", "emb", "tags", "status"]
        assert defs[0].required and defs[0].type.scalar == schema_pb2.SCALAR_TYPE_STRING
        assert (defs[1].type.vector.dimensions, defs[1].type.vector.metric) == (
            384,
            vector_pb2.DISTANCE_METRIC_L2,
        )
        assert defs[2].type.array.element.scalar == schema_pb2.SCALAR_TYPE_STRING
        assert defs[3].HasField("default_value") and not defs[0].HasField("default_value")


class TestCreateConstraint:
    """create_constraint() validates the shape locally and sends what it was given."""

    @staticmethod
    def _client():
        from unittest.mock import AsyncMock

        client = AsyncCoordinodeClient("localhost:0")
        client._schema_stub = type(
            "FakeStub",
            (),
            {"CreateConstraint": AsyncMock(return_value=schema_pb2.Constraint(name="c", label="L"))},
        )()
        return client

    def test_unknown_kind_raises(self):
        async def _inner() -> None:
            with pytest.raises(ValueError, match="kind must be one of"):
                await self._client().create_constraint("L", "p", "check")

        asyncio.run(_inner())

    def test_property_type_only_with_its_kind(self):
        async def _inner() -> None:
            client = self._client()
            with pytest.raises(ValueError, match="property_type"):
                await client.create_constraint("L", "p", "unique", property_type="string")
            with pytest.raises(ValueError, match="property_type"):
                await client.create_constraint("L", "p", "property_type")

        asyncio.run(_inner())

    def test_request_carries_the_declaration(self):
        async def _inner() -> None:
            client = self._client()
            await client.create_constraint("L", ["a", "b"], "node_key", name="l_key", if_not_exists=True)
            sent = client._schema_stub.CreateConstraint.call_args.args[0]
            assert (sent.name, sent.label, list(sent.properties)) == ("l_key", "L", ["a", "b"])
            assert sent.kind == schema_pb2.CONSTRAINT_KIND_NODE_KEY and sent.if_not_exists
            assert not sent.HasField("property_type")

        asyncio.run(_inner())


class TestCreateLabelSchemaMode:
    """Unit tests for schema_mode normalization in create_label()."""

    def test_invalid_schema_mode_raises(self):
        """create_label() raises ValueError for unknown schema_mode string."""

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            with pytest.raises(ValueError, match="schema_mode must be one of"):
                await client.create_label("Foo", schema_mode="unknown")

        asyncio.run(_inner())

    def test_uppercase_schema_mode_accepted(self):
        """create_label() normalizes ' STRICT ' (with spaces and uppercase) to 'strict' before RPC."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            # Patch the schema stub so the RPC call doesn't reach a real server.
            client._schema_stub = type(
                "FakeStub",
                (),
                {"CreateLabel": AsyncMock(return_value=_label("Foo"))},
            )()
            # ' STRICT ' must normalise cleanly (strip + lower) and NOT raise ValueError.
            info = await client.create_label("Foo", schema_mode=" STRICT ")
            assert info.name == "Foo"

        asyncio.run(_inner())


class TestCreateNodesBatch:
    """Bulk node creation, added so a seed load takes one index write per index."""

    @staticmethod
    def _client_returning(nodes):
        from unittest.mock import AsyncMock

        client = AsyncCoordinodeClient("localhost:0")
        client._graph_stub = type(
            "FakeStub",
            (),
            {"CreateNodesBatch": AsyncMock(return_value=graph_pb2.CreateNodesBatchResponse(nodes=nodes))},
        )()
        return client

    def test_returns_nodes_in_input_order(self):
        async def _inner() -> None:
            client = self._client_returning([_node(1, ["A"]), _node(2, ["B"]), _node(3, ["C"])])
            out = await client.create_nodes_batch(
                [(["A"], {}), (["B"], {}), (["C"], {})],
            )
            assert [n.id for n in out] == [1, 2, 3]
            assert [n.labels for n in out] == [["A"], ["B"], ["C"]]

        asyncio.run(_inner())

    def test_sends_every_entry(self):
        async def _inner() -> None:
            client = self._client_returning([])
            await client.create_nodes_batch([(["A"], {"x": 1}), (["B"], {"y": "s"})])
            sent = client._graph_stub.CreateNodesBatch.call_args.args[0]
            assert len(sent.nodes) == 2
            assert list(sent.nodes[0].labels) == ["A"]
            assert list(sent.nodes[1].labels) == ["B"]

        asyncio.run(_inner())

    def test_empty_batch_is_a_no_op(self):
        async def _inner() -> None:
            client = self._client_returning([])
            assert await client.create_nodes_batch([]) == []

        asyncio.run(_inner())

    def test_element_id_survives_the_batch(self):
        async def _inner() -> None:
            client = self._client_returning([_node(7, ["A"], element_id="0000000000007")])
            out = await client.create_nodes_batch([(["A"], {})])
            assert out[0].element_id == "0000000000007"

        asyncio.run(_inner())


class TestTraverseValidation:
    """Unit tests for AsyncCoordinodeClient.traverse() input validation.

    Validation (direction and max_depth checks) runs before any RPC call, so no
    running server is required — only the client object needs to be instantiated.
    """

    def test_invalid_direction_raises(self):
        """traverse() raises ValueError for an unrecognised direction string."""

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            with pytest.raises(ValueError, match="Invalid direction"):
                await client.traverse(1, "KNOWS", direction="sideways")

        asyncio.run(_inner())

    def test_max_depth_below_one_raises(self):
        """traverse() raises ValueError when max_depth is less than 1."""

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            with pytest.raises(ValueError, match="max_depth must be"):
                await client.traverse(1, "KNOWS", max_depth=0)

        asyncio.run(_inner())

    def test_direction_none_raises_value_error(self):
        """traverse() raises ValueError (not AttributeError) when direction is None."""

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            with pytest.raises(ValueError, match="direction must be a str"):
                await client.traverse(1, "KNOWS", direction=None)  # type: ignore[arg-type]

        asyncio.run(_inner())

    def test_max_depth_string_raises_value_error(self):
        """traverse() raises ValueError (not TypeError) when max_depth is a string."""

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            with pytest.raises(ValueError, match="max_depth must be an integer"):
                await client.traverse(1, "KNOWS", max_depth="2")  # type: ignore[arg-type]

        asyncio.run(_inner())

    def test_max_depth_bool_raises_value_error(self):
        """traverse() raises ValueError for bool max_depth (bool is a subclass of int in Python)."""

        async def _inner() -> None:
            client = AsyncCoordinodeClient("localhost:0")
            with pytest.raises(ValueError, match="max_depth must be an integer"):
                await client.traverse(1, "KNOWS", max_depth=True)  # type: ignore[arg-type]

        asyncio.run(_inner())
