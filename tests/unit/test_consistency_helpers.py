"""Unit tests for consistency-parameter helpers in coordinode.client."""

from __future__ import annotations

import pytest

from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb
from coordinode.client import (
    _make_read_concern,
    _make_read_preference,
    _make_write_concern,
)


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


class TestAtTimestamp:
    """Time-travel pin added alongside the existing causal-read fence."""

    def test_pins_the_read_to_a_timestamp(self) -> None:
        assert _make_read_concern(None, None, 1234567890).at_timestamp == 1234567890

    def test_absent_by_default(self) -> None:
        assert _make_read_concern("majority", None).at_timestamp == 0

    def test_combines_with_a_level(self) -> None:
        rc = _make_read_concern("snapshot", None, 42)
        assert rc.level == pb.READ_CONCERN_LEVEL_SNAPSHOT
        assert rc.at_timestamp == 42

    def test_rejects_a_fence_together_with_a_pin(self) -> None:
        """A fence waits for the log to advance; a pin reads a fixed past.

        The server calls the pair mutually exclusive and answers
        INVALID_ARGUMENT, so asking for both is a mistake worth reporting
        where it was made rather than one round trip later.
        """
        with pytest.raises(ValueError, match="after_index and at_timestamp are mutually exclusive"):
            _make_read_concern(None, 7, 42)

    def test_allows_a_zero_fence_with_a_pin(self) -> None:
        """after_index=0 fences on nothing, so it does not conflict."""
        rc = _make_read_concern(None, 0, 42)
        assert (rc.after_index, rc.at_timestamp) == (0, 42)

    @pytest.mark.parametrize("bad", [-1, True, "42", 1.5])
    def test_rejects_non_negative_integers(self, bad: object) -> None:
        with pytest.raises(ValueError, match="at_timestamp must be a non-negative integer"):
            _make_read_concern(None, None, bad)  # type: ignore[arg-type]

    def test_defaults_the_level_to_snapshot(self) -> None:
        """A pinned read is a snapshot read, and the server enforces that.

        Sending a timestamp with the level left UNSPECIFIED gets the request
        refused with FAILED_PRECONDITION, so the documented time-travel call
        could not work as written.
        """
        rc = _make_read_concern(None, None, 42)
        assert rc.level == pb.READ_CONCERN_LEVEL_SNAPSHOT

    def test_keeps_an_explicit_snapshot_level(self) -> None:
        rc = _make_read_concern("snapshot", None, 42)
        assert rc.level == pb.READ_CONCERN_LEVEL_SNAPSHOT

    @pytest.mark.parametrize("level", ["local", "majority", "linearizable"])
    def test_rejects_a_level_the_server_will_refuse(self, level: str) -> None:
        """Fail here rather than after a round trip to a server that says no."""
        with pytest.raises(ValueError, match="at_timestamp requires read_concern='snapshot'"):
            _make_read_concern(level, None, 42)


class TestWriteConcern:
    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            ("w0", pb.WRITE_CONCERN_LEVEL_W0),
            ("memory", pb.WRITE_CONCERN_LEVEL_MEMORY),
            ("cache", pb.WRITE_CONCERN_LEVEL_CACHE),
            ("w1", pb.WRITE_CONCERN_LEVEL_W1),
            ("majority", pb.WRITE_CONCERN_LEVEL_MAJORITY),
        ],
    )
    def test_valid_levels(self, level: str, expected: int) -> None:
        assert _make_write_concern(level).level == expected

    def test_every_documented_level_is_reachable(self) -> None:
        """The proto orders durability W0 < MEMORY < CACHE < W1 < MAJORITY."""
        names = ("w0", "memory", "cache", "w1", "majority")
        assert [_make_write_concern(n).level for n in names] == [
            pb.WRITE_CONCERN_LEVEL_W0,
            pb.WRITE_CONCERN_LEVEL_MEMORY,
            pb.WRITE_CONCERN_LEVEL_CACHE,
            pb.WRITE_CONCERN_LEVEL_W1,
            pb.WRITE_CONCERN_LEVEL_MAJORITY,
        ]

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
