"""
Python-friendly type wrappers and PropertyValue conversion.
"""

from __future__ import annotations

from typing import Any

# We import proto types lazily to avoid hard-fail when stubs aren't generated yet.

PyValue = int | float | str | bool | bytes | list[float] | list[Any] | dict[str, Any] | None


class MultiVector(list):
    """Several equal-width vectors describing one item.

    A plain ``list`` of rows, so it reads and indexes like any other sequence
    and compares equal to the nested list it looks like. The type exists only
    to carry the distinction back to the wire: a value that arrived as a
    multi-vector re-encodes as one, while an ordinary list of vectors stays an
    ordinary list. Without it a read-modify-write silently changed the property
    type, which the schema then rejected.
    """

    __slots__ = ()


class Path(dict):
    """A graph path: the node ids it runs through, and the hops between them.

    A plain ``dict`` with ``nodes`` and ``rels`` keys, so it reads, indexes and
    compares exactly like the mapping it looks like. As with
    :class:`MultiVector`, the type exists only to carry the distinction back to
    the wire: a value that arrived as a path re-encodes as one, while an
    ordinary dict that happens to hold those keys stays a map.
    """

    __slots__ = ()


def _to_path_proto(path: Path) -> Any:
    """Build the wire Path from the mapping a decoded path presents.

    Kept out of :func:`to_property_value` so that adding a value type does not
    grow the branch it sits in: the encoding of one type belongs with that type,
    not inside the dispatch over all of them.
    """
    from coordinode._proto.coordinode.v1.common.types_pb2 import (  # type: ignore[import]
        Path as PathProto,
    )
    from coordinode._proto.coordinode.v1.common.types_pb2 import (  # type: ignore[import]
        PathRel as PathRelProto,
    )

    return PathProto(
        nodes=[int(n) for n in path.get("nodes", [])],
        rels=[
            PathRelProto(
                edge_type=str(rel["type"]),
                source=int(rel["source"]),
                target=int(rel["target"]),
            )
            for rel in path.get("rels", [])
        ],
    )


def _set_sequence(pv: Any, values: Any) -> None:
    """Encode a Python sequence into the field that matches what it holds.

    A non-empty run of plain numbers is a Vector; anything else is a list of
    values. bool is a subclass of int, so it is excluded explicitly: [True,
    False] is a list of booleans, not a Vector of 1.0 and 0.0.
    """
    from coordinode._proto.coordinode.v1.common.types_pb2 import (  # type: ignore[import]
        PropertyList,
        Vector,
    )

    if values and all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
        pv.vector_value.CopyFrom(Vector(values=[float(v) for v in values]))
    else:
        pv.list_value.CopyFrom(PropertyList(values=[to_property_value(v) for v in values]))


def to_property_value(py_val: PyValue) -> Any:
    """Convert a Python value to a proto PropertyValue."""
    from coordinode._proto.coordinode.v1.common.types_pb2 import (  # type: ignore[import]
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
    elif isinstance(py_val, MultiVector):
        from coordinode._proto.coordinode.v1.common.types_pb2 import (  # type: ignore[import]
            MultiVector as MultiVectorProto,
        )

        pv.multi_vector_value.CopyFrom(
            MultiVectorProto(rows=[Vector(values=[float(v) for v in row]) for row in py_val])
        )
    elif isinstance(py_val, list | tuple):
        # list | tuple union syntax is valid in isinstance() for Python ≥3.10 (PEP 604).
        # This project targets Python ≥3.11 (pyproject.toml: requires-python = ">=3.11").
        _set_sequence(pv, py_val)
    elif isinstance(py_val, Path):
        # Before the dict branch below, which would otherwise take it: Path is
        # a dict, and encoding it as a map changes the property's wire type on
        # a read-modify-write.
        pv.path_value.CopyFrom(_to_path_proto(py_val))
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
    elif kind == "multi_vector_value":
        # Several equal-width vectors describing one item, as late-interaction
        # retrieval models produce. A plain list of lists is the natural Python
        # shape; the wire type is what distinguishes it from an array that
        # happens to hold vectors.
        return MultiVector(list(row.values) for row in pv.multi_vector_value.rows)
    elif kind == "path_value":
        # Relationship properties are not carried in the path model, so a hop
        # is its type and its endpoints.
        path = pv.path_value
        return Path(
            nodes=list(path.nodes),
            rels=[{"type": r.edge_type, "source": r.source, "target": r.target} for r in path.rels],
        )
    else:
        return None


def props_to_dict(proto_map: Any) -> dict[str, PyValue]:
    """Convert a proto properties map to a plain Python dict."""
    return {k: from_property_value(v) for k, v in proto_map.items()}


def dict_to_props(d: dict[str, PyValue]) -> dict[str, Any]:
    """Convert a Python dict to a proto properties map."""
    return {k: to_property_value(v) for k, v in d.items()}
