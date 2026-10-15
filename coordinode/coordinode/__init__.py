"""
CoordiNode Python SDK: graph + vector + full-text in one query.

Quick start::

    from coordinode import CoordinodeClient

    with CoordinodeClient("localhost:7080") as db:
        result = db.cypher("MATCH (n:Concept) RETURN n LIMIT 5")
        for row in result:
            print(row)

Async::

    from coordinode import AsyncCoordinodeClient

    async with AsyncCoordinodeClient("localhost:7080") as db:
        result = await db.cypher("MATCH (n) RETURN count(n) AS total")
"""

from coordinode._types import MultiVector, Path, Timestamp
from coordinode.client import (
    AsyncCoordinodeClient,
    AsyncTransaction,
    ConstraintInfo,
    CoordinodeClient,
    EdgeResult,
    EdgeTypeInfo,
    LabelInfo,
    NodeResult,
    PropertyDefinitionInfo,
    TextIndexInfo,
    TextResult,
    Transaction,
    TraverseResult,
    VectorResult,
    WriteConcern,
)

try:
    from coordinode._version import __version__
except ImportError:
    __version__ = "0.0.0"  # fallback for editable installs without hatch-vcs
__all__ = [
    "CoordinodeClient",
    "AsyncCoordinodeClient",
    # Handles for a group of statements that commits or rolls back as one.
    # Exported for type annotations; both come from a client, never built
    # directly, since only the server can hand out the transaction id.
    "Transaction",
    "AsyncTransaction",
    # Values whose wire type only a tag can carry: a plain nested list encodes
    # as a list, a plain dict as a map and a plain int as an integer, so
    # sending one of these types requires the constructor, not just the shape.
    # An aware datetime encodes as a timestamp too.
    "MultiVector",
    "Path",
    "Timestamp",
    # How a write is acknowledged: member count or majority, and how durably.
    "WriteConcern",
    "NodeResult",
    "EdgeResult",
    "VectorResult",
    "TextResult",
    "LabelInfo",
    "EdgeTypeInfo",
    "PropertyDefinitionInfo",
    "ConstraintInfo",
    "TextIndexInfo",
    "TraverseResult",
]
