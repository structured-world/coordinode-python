"""Unit tests for _types.py — PropertyValue conversion round-trips."""

import pytest

from coordinode._types import from_property_value, to_property_value


class _FakeVec:
    """Minimal PropertyValue.vector stub."""
    def __init__(self, values):
        self.values = list(values)


class _FakeList:
    def __init__(self, values):
        self.values = list(values)


class _FakeMap:
    def __init__(self, fields):
        self.fields = dict(fields)


class _FakePV:
    """Minimal PropertyValue stub for testing from_property_value."""

    def __init__(self, kind, value):
        self._kind = kind
        self._value = value

    def WhichOneof(self, _field):
        return self._kind

    # Property accessors matching proto field names
    @property
    def int_value(self):
        return self._value

    @property
    def float_value(self):
        return self._value

    @property
    def string_value(self):
        return self._value

    @property
    def bool_value(self):
        return self._value

    @property
    def bytes_value(self):
        return self._value

    @property
    def vector(self):
        return self._value

    @property
    def list_value(self):
        return self._value

    @property
    def map_value(self):
        return self._value


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
        assert list(pv.vector.values) == pytest.approx([1.0, 2.0, 3.0])

    def test_mixed_list_becomes_list_value(self):
        pv = to_property_value(["a", "b"])
        assert len(pv.list_value.values) == 2

    def test_dict_becomes_map_value(self):
        pv = to_property_value({"x": 1, "y": 2})
        assert "x" in pv.map_value.fields
        assert "y" in pv.map_value.fields

    def test_none_raises(self):
        with pytest.raises((TypeError, AttributeError)):
            to_property_value(None)


# ── from_property_value ─────────────────────────────────────────────────────

class TestFromPropertyValue:
    def test_int_value(self):
        pv = _FakePV("int_value", 7)
        assert from_property_value(pv) == 7

    def test_float_value(self):
        pv = _FakePV("float_value", 2.71)
        assert from_property_value(pv) == pytest.approx(2.71)

    def test_string_value(self):
        pv = _FakePV("string_value", "world")
        assert from_property_value(pv) == "world"

    def test_bool_value(self):
        pv = _FakePV("bool_value", True)
        assert from_property_value(pv) is True

    def test_bytes_value(self):
        pv = _FakePV("bytes_value", b"\xff")
        assert from_property_value(pv) == b"\xff"

    def test_vector(self):
        vec = _FakeVec([0.1, 0.2])
        pv = _FakePV("vector", vec)
        result = from_property_value(pv)
        assert result == pytest.approx([0.1, 0.2])

    def test_none_kind_returns_none(self):
        pv = _FakePV(None, None)
        assert from_property_value(pv) is None
