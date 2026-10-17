"""Value round trips through the embedded engine.

Skipped on fresh checkouts where ``coordinode_embedded`` has not been built.
Exercised by the ``build-embedded`` CI job after the wheel is installed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

ce = pytest.importorskip("coordinode_embedded")

# From the embedded package rather than `coordinode`, so this file runs where
# only the wheel is installed. It re-exports `coordinode`'s definitions when
# that package is present, so this is the same class either way.
from coordinode_embedded import MultiVector, Path, Timestamp  # noqa: E402  (after the skip guard)


@pytest.fixture
def db(tmp_path):
    client = ce.LocalClient(str(tmp_path / "values.db"))
    yield client
    client.close()


class TestMultiVectorRoundTrip:
    """A multi-vector read back and written straight out keeps its type.

    The converter handed back a plain nested list, which the input path
    cannot tell from an array that happens to hold vectors, so a
    read-modify-write turned the property into an array and a schema
    validating it as a multi-vector rejected the write.
    """

    def test_survives_a_read_modify_write(self, db) -> None:
        db.cypher(
            "CREATE (:Doc {tag: 'mv', emb: $mv})",
            params={"mv": MultiVector([[0.5, 1.5], [2.5, 3.5]])},
        )

        first = db.cypher("MATCH (n:Doc {tag: 'mv'}) RETURN n.emb AS emb")[0]["emb"]
        assert isinstance(first, MultiVector)
        assert first == [[0.5, 1.5], [2.5, 3.5]]

        db.cypher("MATCH (n:Doc {tag: 'mv'}) SET n.emb = $mv", params={"mv": first})

        second = db.cypher("MATCH (n:Doc {tag: 'mv'}) RETURN n.emb AS emb")[0]["emb"]
        assert isinstance(second, MultiVector), "the rewrite must not change the type"
        assert second == first

    def test_a_plain_nested_list_is_not_promoted(self, db) -> None:
        """Only the tag makes a multi-vector; a list of vectors stays a list."""
        rows = db.cypher("RETURN $rows AS rows", params={"rows": [[1.0, 2.0], [3.0, 4.0]]})

        assert not isinstance(rows[0]["rows"], MultiVector)
        assert rows[0]["rows"] == [[1.0, 2.0], [3.0, 4.0]]


class TestPathRoundTrip:
    """A path read back and written straight out keeps its type.

    The same trap as the multi-vector above, one type over: the converter
    handed back a plain dict, and the input path cannot tell that from a map
    with the same keys, so a read-modify-write turned the property into a map.
    """

    def test_survives_a_read_modify_write(self, db) -> None:
        db.cypher("CREATE (a:Stop {tag: 'p', name: 'a'})-[:HOP]->(b:Stop {tag: 'p', name: 'b'})")

        first = db.cypher("MATCH p = (:Stop {tag: 'p', name: 'a'})-[:HOP]->(:Stop) RETURN p AS p")[0]["p"]
        assert isinstance(first, Path), f"a path came back as {type(first).__name__}"
        assert len(first["nodes"]) == 2
        assert [hop["type"] for hop in first["rels"]] == ["HOP"]

        db.cypher("MATCH (n:Stop {tag: 'p', name: 'a'}) SET n.route = $p", params={"p": first})

        second = db.cypher("MATCH (n:Stop {tag: 'p', name: 'a'}) RETURN n.route AS route")[0]["route"]
        assert isinstance(second, Path), "the rewrite must not change the type"
        assert second == first

    def test_a_plain_dict_is_not_promoted(self, db) -> None:
        """Only the tag makes a path; a map with those keys stays a map."""
        rows = db.cypher("RETURN $m AS m", params={"m": {"nodes": [1, 2], "rels": []}})

        assert not isinstance(rows[0]["m"], Path)
        assert rows[0]["m"] == {"nodes": [1, 2], "rels": []}


class TestTimestampRoundTrip:
    """A timestamp written as a datetime reads back as one and writes back as one.

    The engine took a timestamp parameter for a plain integer and refused a
    datetime, and handed a stored timestamp back as a plain int that a write
    then stored as an integer: the same instant, but no longer a TIMESTAMP.
    """

    def test_a_datetime_is_stored_and_survives_a_read_modify_write(self, db) -> None:
        moment = datetime(1969, 7, 20, 20, 17, 40, 123456, tzinfo=timezone(timedelta(hours=2)))
        db.cypher("CREATE (:Event {tag: 'ts', at: $at})", params={"at": moment})

        first = db.cypher("MATCH (n:Event {tag: 'ts'}) RETURN n.at AS at")[0]["at"]
        assert isinstance(first, Timestamp), f"a timestamp came back as {type(first).__name__}"
        assert first == -14_190_139_876_544
        assert first.to_datetime() == moment

        db.cypher("MATCH (n:Event {tag: 'ts'}) SET n.copy = $at", params={"at": first})
        second = db.cypher("MATCH (n:Event {tag: 'ts'}) RETURN n.copy AS at")[0]["at"]
        assert isinstance(second, Timestamp), "the rewrite must not change the type"
        assert second == first

    def test_a_plain_int_is_not_promoted(self, db) -> None:
        """Only the tag or a datetime makes a timestamp; an int stays an int."""
        rows = db.cypher("RETURN $n AS n", params={"n": 1_700_000_000_000_000})

        assert not isinstance(rows[0]["n"], Timestamp)
        assert rows[0]["n"] == 1_700_000_000_000_000

    def test_a_naive_datetime_is_refused(self, db) -> None:
        """A naive datetime names no instant until a zone is chosen for it."""
        with pytest.raises(ValueError):
            db.cypher("RETURN $at AS at", params={"at": datetime(2024, 1, 1)})
