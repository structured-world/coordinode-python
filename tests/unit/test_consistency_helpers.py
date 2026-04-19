"""Unit tests for consistency-parameter helpers in coordinode.client."""

from __future__ import annotations

import pytest

from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb
from coordinode.client import _make_read_concern, _make_read_preference, _make_write_concern


class TestReadConcern:
    def test_level_only(self) -> None:
        rc = _make_read_concern("majority", None)
        assert rc.level == pb.READ_CONCERN_LEVEL_MAJORITY
        assert rc.after_index == 0

    def test_after_index_only(self) -> None:
        rc = _make_read_concern(None, 42)
        assert rc.after_index == 42

    def test_level_and_after_index(self) -> None:
        rc = _make_read_concern("linearizable", 7)
        assert rc.level == pb.READ_CONCERN_LEVEL_LINEARIZABLE
        assert rc.after_index == 7

    def test_case_insensitive(self) -> None:
        assert _make_read_concern("MAJORITY", None).level == pb.READ_CONCERN_LEVEL_MAJORITY

    def test_invalid_level_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid read_concern"):
            _make_read_concern("strong", None)

    @pytest.mark.parametrize("bad", ["", "   ", 5, True])
    def test_rejects_blank_or_non_string_level(self, bad: object) -> None:
        with pytest.raises(ValueError, match="read_concern must be a non-empty string"):
            _make_read_concern(bad, None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [True, False, -1, 1.5, "7"])
    def test_rejects_bool_negative_non_int_after_index(self, bad: object) -> None:
        with pytest.raises(ValueError, match="after_index must be a non-negative integer"):
            _make_read_concern(None, bad)  # type: ignore[arg-type]


class TestWriteConcern:
    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            ("w0", pb.WRITE_CONCERN_LEVEL_W0),
            ("w1", pb.WRITE_CONCERN_LEVEL_W1),
            ("majority", pb.WRITE_CONCERN_LEVEL_MAJORITY),
        ],
    )
    def test_valid_levels(self, level: str, expected: int) -> None:
        assert _make_write_concern(level).level == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid write_concern"):
            _make_write_concern("w9")

    @pytest.mark.parametrize("bad", ["", "   ", None, 1])
    def test_rejects_blank_or_non_string(self, bad: object) -> None:
        with pytest.raises(ValueError, match="write_concern must be a non-empty string"):
            _make_write_concern(bad)  # type: ignore[arg-type]


class TestReadPreference:
    @pytest.mark.parametrize(
        ("pref", "expected"),
        [
            ("primary", pb.READ_PREFERENCE_PRIMARY),
            ("secondary_preferred", pb.READ_PREFERENCE_SECONDARY_PREFERRED),
            ("nearest", pb.READ_PREFERENCE_NEAREST),
        ],
    )
    def test_valid(self, pref: str, expected: int) -> None:
        assert _make_read_preference(pref) == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid read_preference"):
            _make_read_preference("leader")

    @pytest.mark.parametrize("bad", ["", "   ", None, 0])
    def test_rejects_blank_or_non_string(self, bad: object) -> None:
        with pytest.raises(ValueError, match="read_preference must be a non-empty string"):
            _make_read_preference(bad)  # type: ignore[arg-type]
