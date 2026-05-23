//! Native PyO3 wrapper around [`coordinode_vector::hnsw::HnswIndex`].
//!
//! This is the fast-path bypass used by the ann-benchmarks Docker adapter
//! and any in-process HNSW workload. It avoids Cypher's parser/planner
//! overhead so the resulting QPS / recall numbers are directly comparable
//! with library benchmarks like hnswlib, FAISS-HNSW, ScaNN and Annoy.
//!
//! For Cypher-flavoured access (`CREATE VECTOR INDEX`, `MATCH … ORDER BY
//! vector_similarity(...)`) use `LocalClient` instead.

use std::sync::Mutex;

use coordinode_core::graph::types::VectorMetric;
use coordinode_vector::hnsw::{HnswConfig, HnswIndex};
use numpy::{PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

/// All accepted spellings, kept beside [`parse_metric`] so the error message
/// and the match arms cannot drift.  When this list changes, the parser is
/// the source of truth — bump both at once.
const ACCEPTED_METRICS: &str =
    "cosine, angular, euclidean, l2, dot, dot_product, ip, inner_product, manhattan, l1";

fn parse_metric(s: &str) -> PyResult<VectorMetric> {
    match s.to_ascii_lowercase().as_str() {
        "cosine" | "angular" => Ok(VectorMetric::Cosine),
        "euclidean" | "l2" => Ok(VectorMetric::L2),
        "dot" | "dot_product" | "ip" | "inner_product" => Ok(VectorMetric::DotProduct),
        "manhattan" | "l1" => Ok(VectorMetric::L1),
        other => Err(PyValueError::new_err(format!(
            "unknown metric '{other}' — accepted: {ACCEPTED_METRICS}"
        ))),
    }
}

/// In-process HNSW index — PyO3 binding around the CoordiNode native engine.
///
/// # Example
///
/// ```python
/// import numpy as np
/// from coordinode_embedded import Hnsw
///
/// rng = np.random.default_rng(42)
/// X = rng.standard_normal((10_000, 128), dtype=np.float32)
/// q = rng.standard_normal(128, dtype=np.float32)
///
/// idx = Hnsw(dim=128, metric="euclidean", M=16, ef_construction=200)
/// idx.fit(X)
/// idx.set_ef(80)
/// labels = idx.knn_query(q, k=10)   # numpy int64 array, shape (10,)
/// ```
#[pyclass(module = "coordinode_embedded")]
pub struct Hnsw {
    inner: Mutex<HnswIndex>,
    next_id: Mutex<u64>,
    dim: u32,
}

#[pymethods]
impl Hnsw {
    /// Build a new HNSW index.
    ///
    /// # Arguments
    /// * `dim` — embedding dimension (must match the vectors passed to `fit` / `knn_query`).
    /// * `metric` — distance metric. Accepted aliases (all case-insensitive):
    ///     - cosine similarity: `cosine`, `angular`
    ///     - Euclidean (L2):    `euclidean`, `l2`
    ///     - dot product:       `dot`, `dot_product`, `ip`, `inner_product`
    ///     - Manhattan (L1):    `manhattan`, `l1`
    ///
    ///   Spellings track ann-benchmarks and VectorDBBench so existing
    ///   harnesses pass their `space` argument unchanged.
    /// * `M` — max connections per element per layer (HNSW spec). Default 16.
    /// * `ef_construction` — candidate list size during build. Default 200.
    /// * `max_elements` — hint to pre-allocate node storage. Default 1_000_000.
    #[new]
    #[pyo3(signature = (dim, metric, M=16, ef_construction=200, max_elements=1_000_000))]
    #[allow(non_snake_case)]
    fn new(
        dim: u32,
        metric: &str,
        M: usize,
        ef_construction: usize,
        max_elements: u32,
    ) -> PyResult<Self> {
        if dim == 0 {
            return Err(PyValueError::new_err("dim must be > 0"));
        }
        if M == 0 {
            return Err(PyValueError::new_err("M must be > 0"));
        }
        let metric = parse_metric(metric)?;
        // `M * 2` would panic in debug / wrap in release for adversarial M
        // (M ≥ usize::MAX / 2 + 1).  Reject before the engine sees a broken
        // config; realistic M values are 4..96, so any overflow here is a
        // caller error, not a workload to support.
        let m_max0 = M.checked_mul(2).ok_or_else(|| {
            PyValueError::new_err(format!(
                "M={M} is too large; M * 2 overflows usize"
            ))
        })?;
        let config = HnswConfig {
            m: M,
            m_max0,
            ef_construction,
            ef_search: 50,
            metric,
            max_dimensions: dim,
            max_elements,
            ..HnswConfig::default()
        };
        Ok(Self {
            inner: Mutex::new(HnswIndex::new(config)),
            next_id: Mutex::new(0),
            dim,
        })
    }

    /// Bulk-insert vectors. Accepts a 2-D float32 numpy array of shape `(N, dim)`.
    ///
    /// Each row gets an auto-assigned sequential ID starting from the next free
    /// ID (so multiple `fit` calls extend the index instead of replacing it).
    /// Returns the contiguous range `[first_id, last_id+1)` as a (start, end) tuple
    /// so callers can map their own labels onto our internal IDs.
    fn fit(&self, py: Python<'_>, vectors: PyReadonlyArray2<f32>) -> PyResult<(u64, u64)> {
        let array = vectors.as_array();
        let (n, d) = (array.shape()[0], array.shape()[1]);
        if d as u32 != self.dim {
            return Err(PyValueError::new_err(format!(
                "vector dimension mismatch: index dim={}, input dim={d}",
                self.dim
            )));
        }
        if n == 0 {
            // Empty batch is a no-op but must still report the current insertion
            // point — returning (0, 0) would break the "contiguous range
            // [first_id, last_id+1) at the actual insertion point" contract once
            // the index already holds vectors.
            let next = self
                .next_id
                .lock()
                .map_err(|e| PyRuntimeError::new_err(format!("next_id lock poisoned: {e}")))?;
            return Ok((*next, *next));
        }
        // Materialise the (id, vec) batch under the GIL, then release the GIL
        // for the build.  Use per-item `insert` rather than `insert_batch`:
        // the batch path trades within-batch plan staleness for ~5-8× build
        // throughput, but the resulting recall divergence (engine parity bar
        // is 0.7 top-10 agreement, not 1.0) is unacceptable for ann-benchmarks
        // comparisons against serial-equivalent libraries like hnswlib.  We
        // can expose a `fit_fast` opt-in later if a real workload needs the
        // build-throughput trade.
        let mut next = self
            .next_id
            .lock()
            .map_err(|e| PyRuntimeError::new_err(format!("next_id lock poisoned: {e}")))?;
        let start_id = *next;
        let mut batch: Vec<(u64, Vec<f32>)> = Vec::with_capacity(n);
        for row in array.outer_iter() {
            batch.push((*next, row.to_vec()));
            *next += 1;
        }
        let end_id = *next;
        drop(next);

        py.allow_threads(|| -> PyResult<()> {
            let mut index = self
                .inner
                .lock()
                .map_err(|e| PyRuntimeError::new_err(format!("index lock poisoned: {e}")))?;
            for (id, vec) in batch {
                index.insert(id, vec);
            }
            Ok(())
        })?;
        Ok((start_id, end_id))
    }

    /// Update runtime `ef_search`. Larger ef = higher recall, lower QPS.
    fn set_ef(&self, ef: usize) -> PyResult<()> {
        let mut index = self
            .inner
            .lock()
            .map_err(|e| PyRuntimeError::new_err(format!("index lock poisoned: {e}")))?;
        index.set_ef_search(ef);
        Ok(())
    }

    /// k-NN query. Returns a 1-D int64 numpy array of length `k` with the IDs
    /// of the nearest neighbours, ordered nearest-first. If the index has
    /// fewer than `k` elements, the result is shorter accordingly.
    fn knn_query<'py>(
        &self,
        py: Python<'py>,
        query: PyReadonlyArray1<f32>,
        k: usize,
    ) -> PyResult<Bound<'py, PyArray1<i64>>> {
        let q_view = query.as_array();
        if q_view.len() as u32 != self.dim {
            return Err(PyValueError::new_err(format!(
                "query dimension mismatch: index dim={}, query dim={}",
                self.dim,
                q_view.len()
            )));
        }
        let q: Vec<f32> = q_view.iter().copied().collect();
        let labels = py.allow_threads(|| -> PyResult<Vec<i64>> {
            let index = self
                .inner
                .lock()
                .map_err(|e| PyRuntimeError::new_err(format!("index lock poisoned: {e}")))?;
            Ok(index
                .search(&q, k)
                .into_iter()
                .map(|r| r.id as i64)
                .collect())
        })?;
        Ok(PyArray1::from_vec(py, labels))
    }

    /// Number of vectors indexed.
    fn __len__(&self) -> PyResult<usize> {
        // Read the count from the HnswIndex itself, NOT from `next_id`.
        // `next_id` is bumped under its own mutex before the inserts happen
        // under `inner`; with the GIL released around the build, a concurrent
        // `__len__` call would otherwise observe phantom IDs that haven't
        // actually landed in the index.  Locking `inner` makes the count
        // reflect committed inserts only.
        let index = self
            .inner
            .lock()
            .map_err(|e| PyRuntimeError::new_err(format!("index lock poisoned: {e}")))?;
        Ok(index.len())
    }

    fn __repr__(&self) -> String {
        // `__len__` surfaces a poisoned mutex as RuntimeError; `__repr__`
        // can't raise (Python expects it to always return a string), so a
        // poison is rendered as a visible marker rather than a silent
        // `len=0` that would mask real concurrency bugs during debugging.
        // `try_lock` is intentional: even when uncontended `__repr__` runs
        // in the debugger and must not block a concurrent build that holds
        // the lock — we'd rather show `<busy>` than deadlock the REPL.
        let len_repr = match self.inner.try_lock() {
            Ok(idx) => idx.len().to_string(),
            Err(std::sync::TryLockError::WouldBlock) => "<busy>".to_owned(),
            Err(std::sync::TryLockError::Poisoned(_)) => "<poisoned>".to_owned(),
        };
        format!("Hnsw(dim={}, len={len_repr})", self.dim)
    }
}
