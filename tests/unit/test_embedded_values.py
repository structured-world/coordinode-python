"""Value round trips through the embedded engine.

Skipped on fresh checkouts where ``coordinode_embedded`` has not been built.
Exercised by the ``build-embedded`` CI job after the wheel is installed.
"""

from __future__ import annotations

import pytest

ce = pytest.importorskip("coordinode_embedded")

from coordinode._types import MultiVector  # noqa: E402  (after the skip guard)


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
