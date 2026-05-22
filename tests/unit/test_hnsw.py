"""Recall + API tests for the native ``Hnsw`` PyO3 binding.

Skipped on fresh checkouts where ``coordinode_embedded`` has not been built
via ``maturin develop``.  Exercised by the ``build-embedded`` CI job after
the wheel is installed.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
ce = pytest.importorskip("coordinode_embedded")


def _brute_force_topk(X, q, k: int):
    dists = ((X - q) ** 2).sum(axis=1)
    return set(np.argsort(dists)[:k].tolist())


def test_metric_parsing_and_dim_validation() -> None:
    idx = ce.Hnsw(dim=8, metric="euclidean", M=4, ef_construction=20)
    assert len(idx) == 0

    # Unknown metric raises early — agentic adapters often pass through
    # ann-benchmarks's "angular" alias, so both spellings must work.
    ce.Hnsw(dim=4, metric="angular", M=4, ef_construction=20)
    ce.Hnsw(dim=4, metric="cosine", M=4, ef_construction=20)
    ce.Hnsw(dim=4, metric="l1", M=4, ef_construction=20)
    ce.Hnsw(dim=4, metric="dot", M=4, ef_construction=20)
    with pytest.raises(ValueError):
        ce.Hnsw(dim=4, metric="bogus", M=4, ef_construction=20)

    # Dimension mismatch on fit / query surfaces as ValueError, not panic.
    with pytest.raises(ValueError):
        idx.fit(np.zeros((3, 7), dtype=np.float32))   # 7 ≠ 8
    idx.fit(np.zeros((3, 8), dtype=np.float32))
    with pytest.raises(ValueError):
        idx.knn_query(np.zeros(7, dtype=np.float32), k=3)


def test_fit_returns_contiguous_id_range() -> None:
    idx = ce.Hnsw(dim=4, metric="euclidean", M=4, ef_construction=20)
    a = idx.fit(np.zeros((10, 4), dtype=np.float32))
    b = idx.fit(np.zeros((5, 4), dtype=np.float32))
    assert a == (0, 10)
    assert b == (10, 15)
    assert len(idx) == 15


def test_recall_at_10_geq_0_95() -> None:
    """N=10 000, dim=16, gaussian random — at ef ≥ 50 recall@10 must clear 0.95.

    Matches the engine's own ``hnsw::tests::recall_test_l2`` bar but at
    realistic scale (the native test runs at n=200 with self-queries; here
    we hold queries out of the training set).
    """
    rng = np.random.default_rng(42)
    X = rng.standard_normal((10_000, 16)).astype(np.float32)
    queries = rng.standard_normal((50, 16)).astype(np.float32)

    idx = ce.Hnsw(dim=16, metric="euclidean", M=16, ef_construction=200)
    idx.fit(X)

    truths = [_brute_force_topk(X, q, 10) for q in queries]

    for ef, min_recall in [(50, 0.95), (100, 0.98), (200, 0.99)]:
        idx.set_ef(ef)
        ok, total = 0, 0
        for i, q in enumerate(queries):
            got = set(idx.knn_query(q, k=10).tolist())
            ok += len(truths[i] & got)
            total += 10
        recall = ok / total
        assert recall >= min_recall, (
            f"recall@10 at ef={ef} = {recall:.3f} (expected ≥ {min_recall})"
        )


def test_knn_query_returns_int64_array() -> None:
    idx = ce.Hnsw(dim=4, metric="euclidean", M=4, ef_construction=20)
    idx.fit(np.eye(4, dtype=np.float32))
    out = idx.knn_query(np.array([1, 0, 0, 0], dtype=np.float32), k=3)
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.int64
    assert out.shape == (3,)
    assert out[0] == 0    # the eye row most similar to (1,0,0,0)
