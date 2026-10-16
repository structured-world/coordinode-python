"""Value types whose wire form a plain Python container cannot express.

A multi-vector is a list of lists and a path is a mapping, so neither can be
told apart from the ordinary container it looks like once it has been handed
to Python. These tags carry the distinction, and the conversion layer checks
for them ahead of the generic list and dict branches: without one, reading a
value and writing it straight back stores it as something else.

`coordinode` owns the canonical definitions, and this package deliberately
does not depend on it: the embedded engine is usable on its own. So when that
package is present its classes are re-used here, and a value tagged through
one is recognised by the other; a standalone install falls back to the
equivalents below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

try:  # pragma: no cover - exercised by whichever install shape is in use
    from coordinode._types import MultiVector, Path, Timestamp
except ImportError:

    class MultiVector(list):  # type: ignore[no-redef]
        """Several equal-width vectors describing one item."""

        __slots__ = ()

    class Path(dict):  # type: ignore[no-redef]
        """A graph path: the node ids it runs through, and the hops between."""

        __slots__ = ()

    _EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

    class Timestamp(int):  # type: ignore[no-redef]
        """An instant: microseconds since the Unix epoch, negative before 1970."""

        __slots__ = ()

        @classmethod
        def from_datetime(cls, moment: datetime) -> Timestamp:
            """The instant a timezone-aware datetime names. A naive one names none."""
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("a naive datetime names no instant; attach a timezone")
            # Integer arithmetic on the timedelta: a float of seconds loses
            # microseconds this far from the epoch.
            return cls((moment - _EPOCH) // timedelta(microseconds=1))

        def to_datetime(self) -> datetime:
            """This instant as a UTC datetime. Raises OverflowError outside years 1..9999."""
            return _EPOCH + timedelta(microseconds=int(self))


__all__ = ["MultiVector", "Path", "Timestamp"]
