"""
CoordiNode Python SDK — graph + vector + full-text in one query.

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

from coordinode.client import (
    AsyncCoordinodeClient,
    CoordinodeClient,
    EdgeResult,
    EdgeTypeInfo,
    LabelInfo,
    NodeResult,
    PropertyDefinitionInfo,
    TraverseResult,
    VectorResult,
)

try:
    from coordinode._version import __version__
except ImportError:
    __version__ = "0.0.0"  # fallback for editable installs without hatch-vcs
__all__ = [
    "CoordinodeClient",
    "AsyncCoordinodeClient",
    "NodeResult",
    "EdgeResult",
    "VectorResult",
    "LabelInfo",
    "EdgeTypeInfo",
    "PropertyDefinitionInfo",
    "TraverseResult",
]
