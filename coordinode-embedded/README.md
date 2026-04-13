# coordinode-embedded

In-process CoordiNode graph database for Python — no server, no Docker required.

```python
from coordinode_embedded import LocalClient

with LocalClient(":memory:") as db:
    db.cypher("CREATE (n:Person {name: $name})", {"name": "Alice"})
    rows = db.cypher("MATCH (n:Person) RETURN n.name AS name")
    print(rows)  # [{"name": "Alice"}]
```

## Installation

```bash
pip install coordinode-embedded
```

## Usage

`LocalClient` is a drop-in replacement for `CoordinodeClient` for local development,
notebooks (Jupyter, Google Colab), and testing — same `.cypher()` API, zero infrastructure.

```python
from coordinode_embedded import LocalClient

# In-memory (discarded on close)
with LocalClient(":memory:") as db:
    db.cypher("CREATE (n:Person {name: 'Alice'})")
    rows = db.cypher("MATCH (n:Person) RETURN n.name AS name")

# Persistent storage
db = LocalClient("/path/to/db")
db.cypher("CREATE (n:Item {id: 1})")
db.close()
```

## Links

- [CoordiNode](https://github.com/structured-world/coordinode) — the graph database engine
- [coordinode-python](https://github.com/structured-world/coordinode-python) — Python SDK
