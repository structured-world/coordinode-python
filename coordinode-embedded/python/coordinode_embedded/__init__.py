"""
coordinode-embedded — CoordiNode in-process Python bindings.

Run the full CoordiNode graph engine in-process: no server, no Docker,
no network.  Compatible with ``CoordinodeClient`` — same ``.cypher()``
API, drop-in for notebooks and local development.

Example::

    from coordinode_embedded import LocalClient

    # In-memory (Google Colab, unit tests, quick scripts)
    with LocalClient(":memory:") as db:
        db.cypher("CREATE (n:Person {name: $name})", {"name": "Alice"})
        rows = db.cypher("MATCH (n:Person) RETURN n.name AS name")
        print(rows)  # [{"name": "Alice"}]

    # Persistent storage
    db = LocalClient("/path/to/data")
    db.cypher("MERGE (n:Company {name: 'Acme'})")
    db.close()
"""

from ._coordinode_embedded import LocalClient

__all__ = ["LocalClient"]
