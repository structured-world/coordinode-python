"""Type stubs for the compiled Rust extension module."""

from typing import Any

import numpy as np
import numpy.typing as npt

class Hnsw:
    """In-process HNSW index — fast-path bypass around Cypher.

    Use this when you want library-grade vector search throughput without
    the Cypher parser/planner cost.  Mirrors the hnswlib / FAISS-HNSW
    surface used by the ann-benchmarks harness.

    Args:
        dim: Embedding dimension.  Must match the vectors passed to ``fit``
             and ``knn_query``.
        metric: Distance metric. Accepted spellings (case-insensitive):
                  - cosine similarity: ``"cosine"``, ``"angular"``
                  - Euclidean (L2):    ``"euclidean"``, ``"l2"``
                  - dot product:       ``"dot"``, ``"dot_product"``, ``"ip"``, ``"inner_product"``
                  - Manhattan (L1):    ``"manhattan"``, ``"l1"``
        M: Max connections per element per layer (HNSW spec). Default 16.
        ef_construction: Candidate list size during build. Default 200.
        max_elements: Hint to pre-allocate node storage. Default 1_000_000.

    Example::

        import numpy as np
        from coordinode_embedded import Hnsw

        rng = np.random.default_rng(42)
        X = rng.standard_normal((10_000, 128), dtype=np.float32)
        q = rng.standard_normal(128, dtype=np.float32)

        idx = Hnsw(dim=128, metric="euclidean", M=16, ef_construction=200)
        idx.fit(X)
        idx.set_ef(80)
        labels = idx.knn_query(q, k=10)   # int64 ndarray, shape (10,)
    """

    def __init__(
        self,
        dim: int,
        metric: str,
        M: int = 16,
        ef_construction: int = 200,
        max_elements: int = 1_000_000,
    ) -> None: ...
    def fit(self, vectors: npt.NDArray[np.float32]) -> tuple[int, int]:
        """Bulk-insert vectors.  Returns the contiguous ``(start, end)`` ID range
        assigned to this batch.  Multiple ``fit`` calls extend the index rather
        than replacing it.
        """
        ...

    def set_ef(self, ef: int) -> None:
        """Update runtime ``ef_search``. Higher ef = higher recall, lower QPS."""
        ...

    def knn_query(
        self, query: npt.NDArray[np.float32], k: int
    ) -> npt.NDArray[np.int64]:
        """k-NN query.  Returns nearest neighbour IDs, ordered nearest-first."""
        ...

    def __len__(self) -> int: ...
    def __repr__(self) -> str: ...

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
