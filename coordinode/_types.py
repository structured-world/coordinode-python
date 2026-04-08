"""
Python-friendly type wrappers and PropertyValue conversion.
"""

from __future__ import annotations

from typing import Any

# We import proto types lazily to avoid hard-fail when stubs aren't generated yet.

PyValue = int | float | str | bool | bytes | list[float] | list[Any] | dict[str, Any] | None


def to_property_value(py_val: PyValue) -> Any:
    """Convert a Python value to a proto PropertyValue."""
    from coordinode._proto.coordinode.v1.common.types_pb2 import (  # type: ignore[import]
        PropertyList,
        PropertyMap,
        PropertyValue,
        Vector,
    )

    pv = PropertyValue()
    if py_val is None:
        pass  # unset oneof → null semantics
    elif isinstance(py_val, bool):
        pv.bool_value = py_val
    elif isinstance(py_val, int):
        pv.int_value = py_val
    elif isinstance(py_val, float):
        pv.float_value = py_val
    elif isinstance(py_val, str):
        pv.string_value = py_val
    elif isinstance(py_val, bytes):
        pv.bytes_value = py_val
    elif isinstance(py_val, list | tuple):
        # Homogeneous float list → Vector; mixed/str list → PropertyList
        # isinstance() with X|Y union syntax is valid from Python 3.10+ (PEP 604).
        # This package requires Python >=3.11, so no tuple-of-types workaround needed.
        # bool is a subclass of int, so exclude it explicitly — [True, False] must
        # not be serialised as a Vector of 1.0/0.0 but as a PropertyList.
        if py_val and all(isinstance(v, int | float) and not isinstance(v, bool) for v in py_val):
            vec = Vector(values=[float(v) for v in py_val])
            pv.vector_value.CopyFrom(vec)
        else:
            pl = PropertyList(values=[to_property_value(v) for v in py_val])
            pv.list_value.CopyFrom(pl)
    elif isinstance(py_val, dict):
        pm = PropertyMap(entries={k: to_property_value(v) for k, v in py_val.items()})
        pv.map_value.CopyFrom(pm)
    else:
        raise TypeError(f"Unsupported property type: {type(py_val)!r}")
    return pv


def from_property_value(pv: Any) -> PyValue:
    """Convert a proto PropertyValue to a Python value."""
    kind = pv.WhichOneof("value")
    if kind is None:
        return None
    elif kind == "int_value":
        return pv.int_value
    elif kind == "float_value":
        return pv.float_value
    elif kind == "string_value":
        return pv.string_value
    elif kind == "bool_value":
        return pv.bool_value
    elif kind == "bytes_value":
        return pv.bytes_value
    elif kind == "timestamp_value":
        ts = pv.timestamp_value
        return {"wall_time": ts.wall_time, "logical": ts.logical}
    elif kind == "vector_value":
        return list(pv.vector_value.values)
    elif kind == "list_value":
        return [from_property_value(v) for v in pv.list_value.values]
    elif kind == "map_value":
        return {k: from_property_value(v) for k, v in pv.map_value.entries.items()}
    else:
        return None


def props_to_dict(proto_map: Any) -> dict[str, PyValue]:
    """Convert a proto properties map to a plain Python dict."""
    return {k: from_property_value(v) for k, v in proto_map.items()}


def dict_to_props(d: dict[str, PyValue]) -> dict[str, Any]:
    """Convert a Python dict to a proto properties map."""
    return {k: to_property_value(v) for k, v in d.items()}
