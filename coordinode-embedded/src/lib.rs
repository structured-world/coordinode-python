mod hnsw;

/// CoordiNode embedded Python bindings.
///
/// Exposes `LocalClient` — a `CoordinodeClient`-compatible interface that runs
/// the full graph engine in-process with no server, no gRPC, no Docker.
///
/// # Example (Python)
/// ```python
/// from coordinode_embedded import LocalClient
///
/// with LocalClient(":memory:") as db:
///     db.cypher("CREATE (n:Person {name: 'Alice'})")
///     rows = db.cypher("MATCH (n:Person) RETURN n.name AS name")
///     print(rows)  # [{"name": "Alice"}]
/// ```
use std::collections::HashMap;
use std::sync::Mutex;

use coordinode_core::graph::types::{GeoValue, Value};
use coordinode_embed::{Database, DatabaseError};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use rmpv::Value as MsgpackValue;

// ── Value → PyObject conversion ──────────────────────────────────────────────

/// The `MultiVector` tag from the `coordinode` package, when it is installed.
///
/// A list subclass, so a tagged value reads and compares exactly like the
/// nested list it replaces; the type exists only to survive a round trip. This
/// package does not depend on `coordinode` (the embedded engine is usable on
/// its own), so a missing import is an ordinary outcome and the caller falls
/// back to a plain list, which is what this returned before the tag existed.
fn multi_vector_type(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    py.import("coordinode._types")
        .and_then(|m| m.getattr("MultiVector"))
        .ok()
}

fn value_to_py(py: Python<'_>, v: Value) -> PyResult<PyObject> {
    match v {
        Value::Null => Ok(py.None()),
        // bool is interned in Python (True/False are singletons) — into_pyobject
        // returns Borrowed, so we need .to_owned() to get a movable Bound.
        Value::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
        Value::Int(i) => Ok(i.into_pyobject(py)?.into_any().unbind()),
        Value::Float(f) => Ok(f.into_pyobject(py)?.into_any().unbind()),
        Value::String(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        // Timestamp: expose as raw microseconds; callers use datetime.fromtimestamp(ts/1e6)
        Value::Timestamp(ts) => Ok(ts.into_pyobject(py)?.into_any().unbind()),
        Value::Vector(v) => {
            let list = PyList::empty(py);
            for x in v {
                list.append(x)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(value_to_py(py, item)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Map(map) => {
            let d = PyDict::new(py);
            for (k, v) in map {
                d.set_item(k, value_to_py(py, v)?)?;
            }
            Ok(d.into_any().unbind())
        }
        Value::Geo(geo) => {
            let d = PyDict::new(py);
            match geo {
                GeoValue::Point { lat, lon } => {
                    d.set_item("lat", lat)?;
                    d.set_item("lon", lon)?;
                }
            }
            Ok(d.into_any().unbind())
        }
        Value::Blob(b) | Value::Binary(b) => Ok(PyBytes::new(py, &b).into_any().unbind()),
        Value::Document(doc) => msgpack_to_py(py, doc),
        // Token-level embeddings: a list of equal-length float rows, mirroring
        // how a single Vector is exposed, but tagged so a value read here and
        // written straight back keeps its type. An untagged nested list is
        // indistinguishable from an array that happens to hold vectors, and
        // the write turns the property into one.
        Value::MultiVector(rows) => {
            let outer = PyList::empty(py);
            for row in rows {
                let inner = PyList::empty(py);
                for x in row {
                    inner.append(x)?;
                }
                outer.append(inner)?;
            }
            match multi_vector_type(py) {
                Some(cls) => Ok(cls.call1((outer,))?.unbind()),
                None => Ok(outer.into_any().unbind()),
            }
        }
        // A graph path, shaped like its msgpack form on the wire: the node ids
        // it runs through and the relationship hops between them.
        Value::Path(path) => {
            let d = PyDict::new(py);
            let nodes = PyList::empty(py);
            for n in &path.nodes {
                nodes.append(*n)?;
            }
            d.set_item("nodes", nodes)?;

            let rels = PyList::empty(py);
            for rel in &path.rels {
                let r = PyDict::new(py);
                r.set_item("type", rel.edge_type.clone())?;
                r.set_item("source", rel.source)?;
                r.set_item("target", rel.target)?;
                rels.append(r)?;
            }
            d.set_item("rels", rels)?;
            Ok(d.into_any().unbind())
        }
    }
}

// ── rmpv::Value → PyObject (for Document values) ─────────────────────────────

fn msgpack_to_py(py: Python<'_>, v: MsgpackValue) -> PyResult<PyObject> {
    match v {
        MsgpackValue::Nil => Ok(py.None()),
        MsgpackValue::Boolean(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
        MsgpackValue::Integer(i) => {
            if let Some(n) = i.as_i64() {
                Ok(n.into_pyobject(py)?.into_any().unbind())
            } else if let Some(n) = i.as_u64() {
                Ok(n.into_pyobject(py)?.into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        MsgpackValue::F32(f) => Ok(f.into_pyobject(py)?.into_any().unbind()),
        MsgpackValue::F64(f) => Ok(f.into_pyobject(py)?.into_any().unbind()),
        MsgpackValue::String(s) => match s.into_str() {
            // into_str() → Option<String> in rmpv 1.x
            Some(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
            None => Ok(py.None()), // invalid UTF-8 → None
        },
        MsgpackValue::Binary(b) => Ok(PyBytes::new(py, &b).into_any().unbind()),
        MsgpackValue::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(msgpack_to_py(py, item)?)?;
            }
            Ok(list.into_any().unbind())
        }
        MsgpackValue::Map(pairs) => {
            let d = PyDict::new(py);
            for (k, v) in pairs {
                // Keys in msgpack maps can be any type; coerce to string for Python
                let key = match k {
                    MsgpackValue::String(s) => s.into_str().unwrap_or_default(),
                    other => format!("{other}"),
                };
                d.set_item(key, msgpack_to_py(py, v)?)?;
            }
            Ok(d.into_any().unbind())
        }
        MsgpackValue::Ext(_, b) => Ok(PyBytes::new(py, &b).into_any().unbind()),
    }
}

// ── PyObject → Value conversion (for params) ─────────────────────────────────

fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(b) = obj.extract::<bool>() {
        // bool before int — Python bool is a subclass of int
        return Ok(Value::Bool(b));
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(Value::Int(i));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(Value::Float(f));
    }
    if let Ok(s) = obj.extract::<String>() {
        return Ok(Value::String(s));
    }
    if let Ok(list) = obj.downcast::<PyList>() {
        // A tagged multi-vector before the generic list branch: it IS a list,
        // so the branch below would take it and write back an array, changing
        // the property's type on a read-modify-write.
        if let Some(cls) = multi_vector_type(obj.py()) {
            if obj.is_instance(&cls).unwrap_or(false) {
                let rows: Vec<Vec<f32>> = list
                    .iter()
                    .map(|row| row.extract::<Vec<f32>>())
                    .collect::<PyResult<_>>()
                    .map_err(|_| {
                        PyValueError::new_err("MultiVector rows must be lists of numbers")
                    })?;
                return Ok(Value::MultiVector(rows));
            }
        }
        let items: PyResult<Vec<Value>> = list.iter().map(|x| py_to_value(&x)).collect();
        return Ok(Value::Array(items?));
    }
    if let Ok(d) = obj.downcast::<PyDict>() {
        let mut map = std::collections::BTreeMap::new();
        for (k, v) in d.iter() {
            let key = k
                .extract::<String>()
                .map_err(|_| PyValueError::new_err("dict keys in params must be strings"))?;
            map.insert(key, py_to_value(&v)?);
        }
        return Ok(Value::Map(map));
    }
    if let Ok(b) = obj.extract::<Vec<u8>>() {
        return Ok(Value::Binary(b));
    }
    Err(PyValueError::new_err(format!(
        "unsupported param type: {}",
        obj.get_type().name()?
    )))
}

// ── DatabaseError → Python ───────────────────────────────────────────────────

fn db_err(e: DatabaseError) -> PyErr {
    PyRuntimeError::new_err(e.to_string())
}

// ── Inner state ───────────────────────────────────────────────────────────────

enum DbState {
    /// Persistent database at a user-specified path.
    Persistent(Database),
    /// In-memory database backed by a temporary directory that is cleaned
    /// up when `LocalClient.close()` is called (or on drop).
    Memory {
        db: Database,
        _tmpdir: tempfile::TempDir,
    },
    Closed,
}

impl DbState {
    fn get_mut(&mut self) -> PyResult<&mut Database> {
        match self {
            DbState::Persistent(db) => Ok(db),
            DbState::Memory { db, .. } => Ok(db),
            DbState::Closed => Err(PyRuntimeError::new_err("LocalClient is already closed")),
        }
    }
}

// ── LocalClient ───────────────────────────────────────────────────────────────

/// In-process CoordiNode database — no server, no Docker required.
///
/// Compatible with `CoordinodeClient`: same `.cypher()` method returns
/// `list[dict]`.  Drop-in for local development and notebook environments
/// (Google Colab, Jupyter).
///
/// Args:
///     path: Filesystem path for persistent storage, or ``":memory:"`` for an
///           in-memory database that is discarded on close.
///
/// Example::
///
///     from coordinode_embedded import LocalClient
///
///     with LocalClient(":memory:") as db:
///         db.cypher("CREATE (n:Person {name: $name})", {"name": "Alice"})
///         rows = db.cypher("MATCH (n:Person) RETURN n.name AS name")
///         # [{"name": "Alice"}]
#[pyclass(module = "coordinode_embedded")]
struct LocalClient {
    state: Mutex<DbState>,
}

#[pymethods]
impl LocalClient {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        let state = if path == ":memory:" {
            let tmpdir = tempfile::tempdir()
                .map_err(|e| PyRuntimeError::new_err(format!("failed to create tempdir: {e}")))?;
            let db = Database::open(tmpdir.path()).map_err(db_err)?;
            DbState::Memory {
                db,
                _tmpdir: tmpdir,
            }
        } else {
            let db = Database::open(path).map_err(db_err)?;
            DbState::Persistent(db)
        };
        Ok(LocalClient {
            state: Mutex::new(state),
        })
    }

    /// Execute a Cypher query and return results as a list of dicts.
    ///
    /// Args:
    ///     query:  Cypher query string.
    ///     params: Optional dict of query parameters (``$name`` style).
    ///
    /// Returns:
    ///     ``list[dict[str, Any]]`` — one dict per result row.
    #[pyo3(signature = (query, params=None))]
    fn cypher(
        &self,
        py: Python<'_>,
        query: &str,
        params: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        let mut guard = self.state.lock().unwrap();
        let db = guard.get_mut()?;

        let rows = match params {
            None => db.execute_cypher(query).map_err(db_err)?,
            Some(d) => {
                let mut map: HashMap<String, Value> = HashMap::with_capacity(d.len());
                for (k, v) in d.iter() {
                    let key = k
                        .extract::<String>()
                        .map_err(|_| PyValueError::new_err("param keys must be strings"))?;
                    map.insert(key, py_to_value(&v)?);
                }
                db.execute_cypher_with_params(query, map).map_err(db_err)?
            }
        };

        let list = PyList::empty(py);
        for row in rows {
            let d = PyDict::new(py);
            for (col, val) in row {
                d.set_item(col, value_to_py(py, val)?)?;
            }
            list.append(d)?;
        }
        Ok(list.into_any().unbind())
    }

    /// Close the database and release all resources.
    ///
    /// After calling ``close()``, any further method calls raise ``RuntimeError``.
    /// In-memory databases discard all data on close.
    fn close(&self) {
        let mut guard = self.state.lock().unwrap();
        *guard = DbState::Closed;
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(&self, _exc_type: PyObject, _exc_val: PyObject, _exc_tb: PyObject) -> bool {
        self.close();
        false // do not suppress exceptions
    }

    fn __repr__(&self) -> &str {
        let guard = self.state.lock().unwrap();
        match &*guard {
            DbState::Persistent(_) => "LocalClient(persistent)",
            DbState::Memory { .. } => "LocalClient(:memory:)",
            DbState::Closed => "LocalClient(closed)",
        }
    }
}

// ── Module ────────────────────────────────────────────────────────────────────

#[pymodule]
fn _coordinode_embedded(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // No explicit NumPy C-API initialization here.  The `numpy` crate
    // (see crate-level docs: "Loading NumPy is done automatically and on
    // demand") triggers `import numpy.core` lazily the first time a
    // PyArray operation runs.  An explicit init step would be a no-op —
    // the crate exposes no public initializer (verified against the
    // 0.23/0.24 lines, and that policy is unlikely to change while the
    // crate's lazy-import design holds).
    m.add_class::<LocalClient>()?;
    m.add_class::<hnsw::Hnsw>()?;
    Ok(())
}
