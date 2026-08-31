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

try:  # pragma: no cover - exercised by whichever install shape is in use
    from coordinode._types import MultiVector, Path
except ImportError:

    class MultiVector(list):  # type: ignore[no-redef]
        """Several equal-width vectors describing one item."""

        __slots__ = ()

    class Path(dict):  # type: ignore[no-redef]
        """A graph path: the node ids it runs through, and the hops between."""

        __slots__ = ()


__all__ = ["MultiVector", "Path"]
