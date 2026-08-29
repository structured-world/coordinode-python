"""Unit tests for _types.py: PropertyValue conversion round-trips.

Both directions are exercised against the real generated messages, so a change
to the PropertyValue oneof shows up here rather than in user code.
"""

import pytest

from coordinode._proto.coordinode.v1.common.types_pb2 import PropertyValue, Vector
from coordinode._types import from_property_value, to_property_value

# Proto generation is part of `make install`, so a missing stub is a broken
# checkout rather than a reason to quietly skip these.


# ── to_property_value ───────────────────────────────────────────────────────


class TestToPropertyValue:
    def test_int(self):
        pv = to_property_value(42)
        assert pv.int_value == 42

    def test_float(self):
        pv = to_property_value(3.14)
        assert abs(pv.float_value - 3.14) < 1e-6

    def test_bool_true(self):
        pv = to_property_value(True)
        assert pv.bool_value is True

    def test_bool_false(self):
        pv = to_property_value(False)
        assert pv.bool_value is False

    def test_string(self):
        pv = to_property_value("hello")
        assert pv.string_value == "hello"

    def test_bytes(self):
        pv = to_property_value(b"\x00\x01")
        assert pv.bytes_value == b"\x00\x01"

    def test_float_list_becomes_vector(self):
        pv = to_property_value([1.0, 2.0, 3.0])
        assert list(pv.vector_value.values) == pytest.approx([1.0, 2.0, 3.0])

    def test_mixed_list_becomes_list_value(self):
        pv = to_property_value(["a", "b"])
        assert len(pv.list_value.values) == 2

    def test_dict_becomes_map_value(self):
        pv = to_property_value({"x": 1, "y": 2})
        assert "x" in pv.map_value.entries
        assert "y" in pv.map_value.entries

    def test_none_produces_null(self):
        # None → unset oneof (null semantics), not an error
        pv = to_property_value(None)
        assert pv.WhichOneof("value") is None

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            to_property_value(object())


# ── from_property_value ─────────────────────────────────────────────────────


class TestFromPropertyValue:
    def test_int_value(self):
        pv = PropertyValue(int_value=7)
        assert from_property_value(pv) == 7

    def test_float_value(self):
        pv = PropertyValue(float_value=2.71)
        assert from_property_value(pv) == pytest.approx(2.71)

    def test_string_value(self):
        pv = PropertyValue(string_value="world")
        assert from_property_value(pv) == "world"

    def test_bool_value(self):
        pv = PropertyValue(bool_value=True)
        assert from_property_value(pv) is True

    def test_bytes_value(self):
        pv = PropertyValue(bytes_value=b"\xff")
        assert from_property_value(pv) == b"\xff"

    def test_vector(self):
        pv = PropertyValue(vector_value=Vector(values=[0.1, 0.2]))
        result = from_property_value(pv)
        assert result == pytest.approx([0.1, 0.2])

    def test_none_kind_returns_none(self):
        pv = PropertyValue()
        assert from_property_value(pv) is None
