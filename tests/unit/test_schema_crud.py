"""Unit tests for the schema and traversal result wrappers.

These build the real generated proto messages rather than stand-ins, so a
field rename or removal in the proto submodule fails here instead of reaching
users as an AttributeError at runtime.
"""

import asyncio

import pytest

from coordinode._proto.coordinode.v1.graph import graph_pb2, schema_pb2
from coordinode.client import (
    AsyncCoordinodeClient,
    EdgeResult,
    EdgeTypeInfo,
    LabelInfo,
    NodeResult,
    PropertyDefinitionInfo,
    TraverseResult,
)

# ── Real proto message builders ──────────────────────────────────────────────


def _prop_def(name: str, type_: int, required: bool = False, unique: bool = False):
    return schema_pb2.PropertyDefinition(name=name, type=type_, required=required, unique=unique)


def _label(name: str, schema_revision: int = 1, properties=None, schema_mode: int = 0):
    return schema_pb2.Label(
        name=name,
        schema_revision=schema_revision,
        properties=properties or [],
        schema_mode=schema_mode,
    )


def _edge_type(name: str, schema_revision: int = 1, properties=None):
    return schema_pb2.EdgeType(name=name, schema_revision=schema_revision, properties=properties or [])


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
        # type=3 = PROPERTY_TYPE_STRING (int value from proto enum)
        p = PropertyDefinitionInfo(_prop_def("name", 3, required=True, unique=False))
        assert p.name == "name"
        assert p.type == 3
        assert p.required is True
        assert p.unique is False

    def test_repr_contains_name(self):
        p = PropertyDefinitionInfo(_prop_def("age", 1))
        assert "age" in repr(p)

    def test_optional_flags_default_false(self):
        p = PropertyDefinitionInfo(_prop_def("x", 2))
        assert p.required is False
        assert p.unique is False


# ── LabelInfo ────────────────────────────────────────────────────────────────


class TestLabelInfo:
    def test_empty_properties(self):
        label = LabelInfo(_label("Person", schema_revision=2))
        assert label.name == "Person"
        assert label.schema_revision == 2
        assert label.properties == []

    def test_properties_are_wrapped(self):
        props = [_prop_def("name", 3), _prop_def("age", 1)]
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
    PROPERTY_TYPE_TIMESTAMP = 6

    def test_basic_fields(self):
        et = EdgeTypeInfo(_edge_type("KNOWS", schema_revision=1))
        assert et.name == "KNOWS"
        assert et.schema_revision == 1
        assert et.properties == []

    def test_properties_are_wrapped(self):
        props = [_prop_def("since", self.PROPERTY_TYPE_TIMESTAMP)]
        et = EdgeTypeInfo(_edge_type("FOLLOWS", properties=props))
        assert len(et.properties) == 1
        assert et.properties[0].name == "since"

    def test_repr_contains_name(self):
        et = EdgeTypeInfo(_edge_type("RATED"))
        assert "RATED" in repr(et)


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


class _FakePropertyTypeAll:
    """Complete fake proto PropertyType with all enum values."""

    PROPERTY_TYPE_INT64 = 1
    PROPERTY_TYPE_FLOAT64 = 2
    PROPERTY_TYPE_STRING = 3
    PROPERTY_TYPE_BOOL = 4
    PROPERTY_TYPE_BYTES = 5
    PROPERTY_TYPE_TIMESTAMP = 6
    PROPERTY_TYPE_VECTOR = 7
    PROPERTY_TYPE_LIST = 8
    PROPERTY_TYPE_MAP = 9


class _FakePropDefCls:
    """Minimal PropertyDefinition constructor."""

    def __init__(self, **kwargs):
        pass  # Stub: kwargs intentionally ignored — only used to verify call succeeds


class TestBuildPropertyDefinitions:
    """Unit tests for AsyncCoordinodeClient._build_property_definitions() validation.

    Validation runs before any RPC call, so no running server is required.
    """

    def test_non_dict_property_raises(self):
        """_build_property_definitions() raises ValueError for non-dict entries."""
        client = AsyncCoordinodeClient("localhost:0")
        with pytest.raises(ValueError, match="must be a dict"):
            client._build_property_definitions(["not-a-dict"], _FakePropertyTypeAll, _FakePropDefCls)

    def test_missing_name_raises(self):
        """_build_property_definitions() raises ValueError when 'name' key is absent."""
        client = AsyncCoordinodeClient("localhost:0")
        with pytest.raises(ValueError, match="non-empty 'name' key"):
            client._build_property_definitions([{"type": "string"}], _FakePropertyTypeAll, _FakePropDefCls)

    def test_non_bool_required_raises(self):
        """_build_property_definitions() raises ValueError when required is not a bool."""
        client = AsyncCoordinodeClient("localhost:0")
        with pytest.raises(ValueError, match="boolean values for 'required' and 'unique'"):
            client._build_property_definitions(
                [{"name": "x", "type": "string", "required": "true"}],
                _FakePropertyTypeAll,
                _FakePropDefCls,
            )

    def test_non_bool_unique_raises(self):
        """_build_property_definitions() raises ValueError when unique is not a bool."""
        client = AsyncCoordinodeClient("localhost:0")
        with pytest.raises(ValueError, match="boolean values for 'required' and 'unique'"):
            client._build_property_definitions(
                [{"name": "x", "type": "string", "unique": 1}],
                _FakePropertyTypeAll,
                _FakePropDefCls,
            )

    def test_valid_bool_properties_accepted(self):
        """_build_property_definitions() accepts proper bool required/unique values."""
        client = AsyncCoordinodeClient("localhost:0")
        result = client._build_property_definitions(
            [{"name": "x", "type": "string", "required": True, "unique": False}],
            _FakePropertyTypeAll,
            _FakePropDefCls,
        )
        assert len(result) == 1


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
