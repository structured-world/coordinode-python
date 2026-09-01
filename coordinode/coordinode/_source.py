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
    Python-level frame support, a stack shorter than the walk, or a hardened
    application whose audit hook refuses the ``sys._getframe`` event and
    raises whatever it likes in place of an answer. Every one of those is the
    same thing to a caller: no location. None of them is worth failing a
    query over, which is why the read is caught broadly rather than by the
    ValueError a short stack happens to give.
    """
    getframe = getattr(sys, "_getframe", None)
    if getframe is None:
        return None
    try:
        # +1 for this frame, which the caller counts from rather than into.
        frame: FrameType = getframe(levels_up + 1)
    except Exception:
        return None
    code = frame.f_code
    return SourceLocation(
        file=code.co_filename,
        line=frame.f_lineno,
        # Qualified, so a method reads as "Class.method" rather than a bare
        # name that says nothing about which class it belongs to.
        function=code.co_qualname,
    )


def _header_safe(value: str) -> str:
    """*value* with everything gRPC would refuse escaped out.

    A metadata key without the ``-bin`` suffix carries an HTTP/2 header value,
    and gRPC enforces the permitted range on the client: a value outside
    printable ASCII fails the call before it is sent, so an unescaped one
    would make a debugging aid the reason every query fails.

    The bar is printable ASCII, not ASCII. A newline, a tab, a NUL and 0x7f
    are each refused the same way a non-ASCII character is, and the likeliest
    source of one is mundane: an application name read from a file arrives
    with the newline that ended it. A POSIX path may legally contain one too.

    The encoding is injective, which matters more than it looks: two call
    sites arriving as one string would not merely lose detail, they would be
    reported as one place in the code, with the queries of one attributed to
    the other. Two things buy that. The escape character escapes itself, so a
    name holding a real newline differs from one holding the characters that
    spell its escape. And each form is padded to a fixed width, so a character
    outside the basic plane cannot spell the same thing as one inside it
    followed by a digit.

    Escaped rather than dropped, because an escaped path still names the file
    and still groups with itself in the advisor, which is the whole job.
    """
    # Three single scans in C, and together they say "every character is in
    # 0x20..0x7e, and none of them is the escape character": isascii rules out
    # the rest of Unicode, isprintable rules out the control characters and
    # 0x7f while counting the space as printable, and a value with no
    # backslash cannot collide with an escaped one.
    if value.isascii() and value.isprintable() and "\\" not in value:
        return value
    return "".join(_escape(ch) for ch in value)


def _escape(ch: str) -> str:
    """One character as itself, or as a fixed-width escape. See _header_safe."""
    if ch == "\\":
        return "\\\\"
    if " " <= ch <= "~":
        return ch
    point = ord(ch)
    if point < 0x100:
        return f"\\x{point:02x}"
    if point < 0x10000:
        return f"\\u{point:04x}"
    return f"\\U{point:08x}"


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
        pairs.append(("x-source-app", _header_safe(app_name)))
    if app_version:
        pairs.append(("x-source-version", _header_safe(app_version)))
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
        ("x-source-file", _header_safe(location.file)),
        # The line is an integer, so its decimal form is ASCII already.
        ("x-source-line", str(location.line)),
        ("x-source-function", _header_safe(location.function)),
    ) + app_identity
