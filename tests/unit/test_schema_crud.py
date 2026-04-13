"""Unit tests for R-SDK3 additions: LabelInfo, EdgeTypeInfo, TraverseResult.

All tests are mock-based — no proto stubs or running server required.
Pattern mirrors test_types.py: fake proto objects with the same attribute
interface that real generated messages provide.
"""

import asyncio

import pytest

from coordinode.client import (
    AsyncCoordinodeClient,
    EdgeResult,
    EdgeTypeInfo,
    LabelInfo,
    NodeResult,
    PropertyDefinitionInfo,
    TraverseResult,
)

# ── Fake proto stubs ─────────────────────────────────────────────────────────


class _FakePropDef:
    """Matches proto PropertyDefinition shape."""

    def __init__(self, name: str, type_: int, required: bool = False, unique: bool = False) -> None:
        self.name = name
        self.type = type_
        self.required = required
        self.unique = unique


class _FakeLabel:
    """Matches proto Label shape."""

    def __init__(self, name: str, version: int = 1, properties=None, schema_mode: int = 0) -> None:
        self.name = name
        self.version = version
        self.properties = properties or []
        self.schema_mode = schema_mode


class _FakeEdgeType:
    """Matches proto EdgeType shape."""

    def __init__(self, name: str, version: int = 1, properties=None, schema_mode: int = 0) -> None:
        self.name = name
        self.version = version
        self.properties = properties or []
        self.schema_mode = schema_mode


class _FakeNode:
    """Matches proto Node shape."""

    def __init__(self, node_id: int, labels=None, properties=None) -> None:
        self.node_id = node_id
        self.labels = labels or []
        self.properties = properties or {}


class _FakeEdge:
    """Matches proto Edge shape."""

    def __init__(self, edge_id: int, edge_type: str, source: int, target: int, properties=None) -> None:
        self.edge_id = edge_id
        self.edge_type = edge_type
        self.source_node_id = source
        self.target_node_id = target
        self.properties = properties or {}


class _FakeTraverseResponse:
    """Matches proto TraverseResponse shape."""

    def __init__(self, nodes=None, edges=None) -> None:
        self.nodes = nodes or []
        self.edges = edges or []


# ── PropertyDefinitionInfo ───────────────────────────────────────────────────


class TestPropertyDefinitionInfo:
    def test_fields_are_mapped(self):
        # type=3 = PROPERTY_TYPE_STRING (int value from proto enum)
        p = PropertyDefinitionInfo(_FakePropDef("name", 3, required=True, unique=False))
        assert p.name == "name"
        assert p.type == 3
        assert p.required is True
        assert p.unique is False

    def test_repr_contains_name(self):
        p = PropertyDefinitionInfo(_FakePropDef("age", 1))
        assert "age" in repr(p)

    def test_optional_flags_default_false(self):
        p = PropertyDefinitionInfo(_FakePropDef("x", 2))
        assert p.required is False
        assert p.unique is False


# ── LabelInfo ────────────────────────────────────────────────────────────────


class TestLabelInfo:
    def test_empty_properties(self):
        label = LabelInfo(_FakeLabel("Person", version=2))
        assert label.name == "Person"
        assert label.version == 2
        assert label.properties == []

    def test_properties_are_wrapped(self):
        props = [_FakePropDef("name", 3), _FakePropDef("age", 1)]
        label = LabelInfo(_FakeLabel("User", properties=props))
        assert len(label.properties) == 2
        assert all(isinstance(p, PropertyDefinitionInfo) for p in label.properties)
        assert label.properties[0].name == "name"
        assert label.properties[1].name == "age"

    def test_repr_contains_name(self):
        label = LabelInfo(_FakeLabel("Movie"))
        assert "Movie" in repr(label)

    def test_version_zero(self):
        # Schema registry may return version=0 for newly created labels.
        label = LabelInfo(_FakeLabel("Draft", version=0))
        assert label.version == 0

    def test_schema_mode_defaults_to_zero(self):
        label = LabelInfo(_FakeLabel("Person"))
        assert label.schema_mode == 0

    def test_schema_mode_strict(self):
        label = LabelInfo(_FakeLabel("Person", schema_mode=1))
        assert label.schema_mode == 1

    def test_schema_mode_validated(self):
        label = LabelInfo(_FakeLabel("Person", schema_mode=2))
        assert label.schema_mode == 2

    def test_schema_mode_flexible(self):
        label = LabelInfo(_FakeLabel("Person", schema_mode=3))
        assert label.schema_mode == 3

    def test_schema_mode_in_repr(self):
        label = LabelInfo(_FakeLabel("Person", schema_mode=1))
        assert "schema_mode" in repr(label)

    def test_schema_mode_missing_from_proto_defaults_zero(self):
        # Proto objects without schema_mode attribute (older server) → 0.
        class _OldLabel:
            name = "Legacy"
            version = 1
            properties = []

        label = LabelInfo(_OldLabel())
        assert label.schema_mode == 0


# ── EdgeTypeInfo ─────────────────────────────────────────────────────────────


class TestEdgeTypeInfo:
    PROPERTY_TYPE_TIMESTAMP = 6

    def test_basic_fields(self):
        et = EdgeTypeInfo(_FakeEdgeType("KNOWS", version=1))
        assert et.name == "KNOWS"
        assert et.version == 1
        assert et.properties == []

    def test_properties_are_wrapped(self):
        props = [_FakePropDef("since", self.PROPERTY_TYPE_TIMESTAMP)]
        et = EdgeTypeInfo(_FakeEdgeType("FOLLOWS", properties=props))
        assert len(et.properties) == 1
        assert et.properties[0].name == "since"

    def test_repr_contains_name(self):
        et = EdgeTypeInfo(_FakeEdgeType("RATED"))
        assert "RATED" in repr(et)

    def test_schema_mode_defaults_to_zero(self):
        et = EdgeTypeInfo(_FakeEdgeType("KNOWS"))
        assert et.schema_mode == 0

    def test_schema_mode_propagated(self):
        et = EdgeTypeInfo(_FakeEdgeType("KNOWS", schema_mode=2))
        assert et.schema_mode == 2

    def test_schema_mode_missing_from_proto_defaults_zero(self):
        class _OldEdgeType:
            name = "LEGACY"
            version = 1
            properties = []

        et = EdgeTypeInfo(_OldEdgeType())
        assert et.schema_mode == 0


# ── TraverseResult ───────────────────────────────────────────────────────────


class TestTraverseResult:
    def test_empty_response(self):
        result = TraverseResult(_FakeTraverseResponse())
        assert result.nodes == []
        assert result.edges == []

    def test_nodes_are_wrapped_as_node_results(self):
        nodes = [_FakeNode(1, ["Person"]), _FakeNode(2, ["Movie"])]
        result = TraverseResult(_FakeTraverseResponse(nodes=nodes))
        assert len(result.nodes) == 2
        assert all(isinstance(n, NodeResult) for n in result.nodes)
        assert result.nodes[0].id == 1
        assert result.nodes[1].id == 2

    def test_edges_are_wrapped_as_edge_results(self):
        edges = [_FakeEdge(10, "KNOWS", source=1, target=2)]
        result = TraverseResult(_FakeTraverseResponse(edges=edges))
        assert len(result.edges) == 1
        assert isinstance(result.edges[0], EdgeResult)
        assert result.edges[0].id == 10
        assert result.edges[0].source_id == 1
        assert result.edges[0].target_id == 2
        assert result.edges[0].type == "KNOWS"

    def test_mixed_nodes_and_edges(self):
        nodes = [_FakeNode(1, ["A"]), _FakeNode(2, ["B"]), _FakeNode(3, ["C"])]
        edges = [
            _FakeEdge(10, "REL", 1, 2),
            _FakeEdge(11, "REL", 2, 3),
        ]
        result = TraverseResult(_FakeTraverseResponse(nodes=nodes, edges=edges))
        assert len(result.nodes) == 3
        assert len(result.edges) == 2

    def test_repr_shows_counts(self):
        nodes = [_FakeNode(1, [])]
        result = TraverseResult(_FakeTraverseResponse(nodes=nodes))
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
        pass


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
                {"CreateLabel": AsyncMock(return_value=_FakeLabel("Foo"))},
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
