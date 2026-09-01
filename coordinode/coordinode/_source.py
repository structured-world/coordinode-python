"""Call-site attribution for queries.

When a client is built with ``debug_source_tracking=True``, every query it
sends carries the source location it was written at. The server's query
advisor groups statistics by query shape, and this is what lets it name the
line that wrote the slow one rather than only the query text, which is
usually identical across a dozen call sites.

Off by default, and off means untouched: no frame is read, no metadata is
built, nothing is sent. The cost of the feature is paid only where somebody
asked for it.

The metadata keys are the wire contract shared with the Rust driver:

===================== ==========================================
``x-source-file``     path of the file the call was written in
``x-source-line``     line number, as a string
``x-source-function`` qualified name of the enclosing function
``x-source-app``      application name, when one was configured
``x-source-version``  application version, when one was configured
===================== ==========================================

``x-source-function`` is the one key the Rust driver leaves empty: it reads
the caller through ``#[track_caller]``, and a ``Location`` there carries no
function name. A Python frame does, so this driver fills it in.
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import FrameType
from typing import NamedTuple

# Directories whose frames are never a call site. This package is the obvious
# one; asyncio is here because a query started with create_task runs its body
# after the frame that started it has gone, leaving an event-loop frame in its
# place. See is_call_site.
# The separator is part of each prefix so that a directory merely NAMED like
# one of these ("coordinode-extra" beside "coordinode") is not swallowed too.
_NOT_CALL_SITES = (
    os.path.dirname(os.path.abspath(__file__)) + os.sep,
    os.path.dirname(os.path.abspath(asyncio.__file__)) + os.sep,
)


class SourceLocation(NamedTuple):
    """Where a query was written: the file, line and enclosing function."""

    file: str
    line: int
    function: str


def is_call_site(location: SourceLocation) -> bool:
    """Whether *location* is somewhere a person could have written the query.

    A location inside this package or inside asyncio is not: it is what is
    left on the stack when the frame that wrote the query is already gone,
    which happens to a query handed to ``create_task`` and awaited later.
    Reporting it would not merely be useless. The advisor groups its
    statistics by call site, so one event-loop line would collect the queries
    of every unrelated task that took that path and present them as one place
    in the code, which is worse than the feature being quiet.
    """
    # A frame's filename is normally already absolute, and making one absolute
    # is not free: for a relative path it asks the OS for the working
    # directory, which would be a syscall on every query.
    path = location.file if os.path.isabs(location.file) else os.path.abspath(location.file)
    return not path.startswith(_NOT_CALL_SITES)


def capture(levels_up: int) -> SourceLocation | None:
    """Read the frame *levels_up* above this function's caller.

    Reading one frame directly is what keeps this cheap. The alternative,
    ``inspect.stack()``, walks the whole stack and opens each frame's source
    file to quote the lines around it: a file system round trip per frame, to
    answer a question about one of them.

    ``None`` comes back when the frame cannot be had — an interpreter with no
    Python-level frame support, or a stack shorter than the walk. Tracking
    goes quiet rather than failing a query over a debugging aid.
    """
    getframe = getattr(sys, "_getframe", None)
    if getframe is None:
        return None
    try:
        # +1 for this frame, which the caller counts from rather than into.
        frame: FrameType = getframe(levels_up + 1)
    except ValueError:
        # Asked for more stack than exists.
        return None
    code = frame.f_code
    return SourceLocation(
        file=code.co_filename,
        line=frame.f_lineno,
        # Qualified, so a method reads as "Class.method" rather than a bare
        # name that says nothing about which class it belongs to.
        function=code.co_qualname,
    )


def identity(app_name: str, app_version: str) -> tuple[tuple[str, str], ...]:
    """The metadata naming the application, built once per client.

    It cannot change after the client is constructed, so building it per query
    would be the same work repeated for every statement the client ever sends.

    Either part may be left out, and an empty one gets no header: the server
    reads a missing key and an empty value the same way, so the header would
    carry nothing.
    """
    pairs = []
    if app_name:
        pairs.append(("x-source-app", app_name))
    if app_version:
        pairs.append(("x-source-version", app_version))
    return tuple(pairs)


def to_metadata(
    location: SourceLocation | None,
    app_identity: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Build the gRPC metadata for *location*, empty when there is none.

    The application identity rides along with the location rather than on its
    own: the server reads them as one source context and discards the whole
    context when the file is missing, so sending the identity alone would put
    it on the wire for nothing.
    """
    if location is None:
        return ()
    return (
        ("x-source-file", location.file),
        ("x-source-line", str(location.line)),
        ("x-source-function", location.function),
    ) + app_identity
