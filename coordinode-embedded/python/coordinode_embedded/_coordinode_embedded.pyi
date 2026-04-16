"""Type stubs for the compiled Rust extension module."""

from typing import Any

class LocalClient:
    """In-process CoordiNode database — no server, no Docker required.

    Compatible with ``CoordinodeClient``: same ``.cypher()`` method returns
    ``list[dict]``.  Drop-in for local development and notebook environments
    (Google Colab, Jupyter).

    Args:
        path: Filesystem path for persistent storage, or ``":memory:"`` for an
              in-memory database that is discarded on close.

    Example::

        from coordinode_embedded import LocalClient

        with LocalClient(":memory:") as db:
            db.cypher("CREATE (n:Person {name: $name})", {"name": "Alice"})
            rows = db.cypher("MATCH (n:Person) RETURN n.name AS name")
    """

    def __init__(self, path: str) -> None: ...
    def cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query and return results as a list of dicts.

        Args:
            query:  Cypher query string.
            params: Optional dict of query parameters (``$name`` style).

        Returns:
            ``list[dict[str, Any]]`` — one dict per result row.
        """
        ...

    def close(self) -> None:
        """Close the database and release all resources.

        After calling ``close()``, any further method calls raise ``RuntimeError``.
        In-memory databases discard all data on close.
        """
        ...

    def __enter__(self) -> LocalClient: ...
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool: ...
    def __repr__(self) -> str: ...
