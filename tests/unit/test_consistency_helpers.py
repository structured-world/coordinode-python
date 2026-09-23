"""Unit tests for consistency-parameter helpers in coordinode.client."""

from __future__ import annotations

import pytest

from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb
from coordinode.client import (
    WriteConcern,
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
        with pytest.raises(ValueError, match="at_timestamp must be a positive integer"):
            _make_read_concern(None, None, bad)  # type: ignore[arg-type]

    def test_rejects_zero_because_the_wire_cannot_carry_it(self) -> None:
        """Zero is how the wire says "no pin", so it cannot also mean a pin.

        `at_timestamp` is a plain proto3 scalar with no field presence, so
        zero is not serialised and the server reads the field as absent. A
        caller asking to read as of the epoch would silently get a current
        read instead, which is the one answer a time-travel query must never
        return. `test_absent_by_default` pins the other half of this: an
        unpinned request is exactly the one whose timestamp is zero.
        """
        with pytest.raises(ValueError, match="at_timestamp must be a positive integer"):
            _make_read_concern(None, None, 0)

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
    """Two independent axes: `w` (count or majority) and `journal` (state per member)."""

    def test_majority_shorthand_sets_the_mode_not_a_count(self) -> None:
        wc = _make_write_concern("majority")
        assert wc.WhichOneof("w") == "mode"
        assert wc.mode == pb.WRITE_CONCERN_MODE_MAJORITY
        assert wc.journal == pb.JOURNAL_JOURNAL

    def test_majority_is_case_insensitive(self) -> None:
        assert _make_write_concern(" Majority ").mode == pb.WRITE_CONCERN_MODE_MAJORITY

    @pytest.mark.parametrize("acks", [0, 1, 3])
    def test_count_shorthand_sets_acks(self, acks: int) -> None:
        """`acks: 0` must be on the wire: unset `w` means MAJORITY, not fire-and-forget."""
        wc = _make_write_concern(acks)
        assert wc.WhichOneof("w") == "acks"
        assert wc.acks == acks

    def test_default_writeconcern_is_journaled_majority(self) -> None:
        wc = _make_write_concern(WriteConcern())
        assert (wc.WhichOneof("w"), wc.mode, wc.journal, wc.timeout_ms) == (
            "mode",
            pb.WRITE_CONCERN_MODE_MAJORITY,
            pb.JOURNAL_JOURNAL,
            0,
        )

    @pytest.mark.parametrize(
        ("journal", "expected"),
        [("journal", pb.JOURNAL_JOURNAL), ("cache", pb.JOURNAL_CACHE), ("memory", pb.JOURNAL_MEMORY)],
    )
    def test_journal_states_on_a_single_ack(self, journal: str, expected: int) -> None:
        wc = _make_write_concern(WriteConcern(w=1, journal=journal))
        assert (wc.acks, wc.journal) == (1, expected)

    def test_volatile_journal_is_allowed_fire_and_forget(self) -> None:
        wc = _make_write_concern(WriteConcern(w=0, journal="memory"))
        assert (wc.WhichOneof("w"), wc.acks, wc.journal) == ("acks", 0, pb.JOURNAL_MEMORY)

    @pytest.mark.parametrize("w", ["majority", 2, 5])
    @pytest.mark.parametrize("journal", ["cache", "memory"])
    def test_volatile_journal_across_members_is_refused(self, w: object, journal: str) -> None:
        """The server answers INVALID_ARGUMENT: a volatile write cannot be confirmed by several members."""
        with pytest.raises(ValueError, match="accepted only with w=0 or w=1"):
            _make_write_concern(WriteConcern(w=w, journal=journal))  # type: ignore[arg-type]

    def test_timeout_is_carried(self) -> None:
        assert _make_write_concern(WriteConcern(timeout_ms=250)).timeout_ms == 250

    @pytest.mark.parametrize("bad", ["w1", "w0", "", "   ", "all"])
    def test_rejects_unknown_named_w(self, bad: str) -> None:
        """The retired level names are not silently mapped to a guess."""
        with pytest.raises(ValueError, match="invalid write_concern w"):
            _make_write_concern(bad)

    @pytest.mark.parametrize("bad", [-1, 2**32, 1.5, True])
    def test_rejects_a_count_that_is_not_a_uint32(self, bad: object) -> None:
        with pytest.raises(ValueError, match="write_concern"):
            _make_write_concern(WriteConcern(w=bad))  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [None, 1.0, True, ["majority"]])
    def test_rejects_a_value_of_the_wrong_type(self, bad: object) -> None:
        with pytest.raises(ValueError, match="write_concern must be a WriteConcern"):
            _make_write_concern(bad)  # type: ignore[arg-type]

    def test_rejects_an_unknown_journal(self) -> None:
        with pytest.raises(ValueError, match="invalid journal"):
            _make_write_concern(WriteConcern(w=1, journal="disk"))

    @pytest.mark.parametrize("bad", [-1, 2**32, True, 1.5])
    def test_rejects_a_bad_timeout(self, bad: object) -> None:
        with pytest.raises(ValueError, match="timeout_ms must be a non-negative 32-bit integer"):
            _make_write_concern(WriteConcern(timeout_ms=bad))  # type: ignore[arg-type]


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
