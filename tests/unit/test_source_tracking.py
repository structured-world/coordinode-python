"""Unit tests for query source tracking.

The feature's whole value is that the location it reports is the line a person
wrote the query on, so these tests assert the line rather than the presence of
a header: a test that only checks "some file was sent" passes just as happily
when the file is a frame inside asyncio.

They also pin the shape of the wire contract, which is shared with the Rust
driver and read by the server's advisor. A renamed key here is silently
ignored on the server, which is exactly the kind of break no runtime error
would report.
"""

import asyncio
from unittest.mock import AsyncMock

import grpc
import pytest

from coordinode._proto.coordinode.v1.query import cypher_pb2
from coordinode._source import SourceLocation, identity, is_call_site, to_metadata
from coordinode.client import AsyncCoordinodeClient, CoordinodeClient


def _execute_response():
    return cypher_pb2.ExecuteCypherResponse(columns=[], rows=[])


def _stub():
    return type(
        "FakeCypherStub",
        (),
        {
            "BeginTransaction": AsyncMock(return_value=cypher_pb2.BeginTransactionResponse(transaction_id=42)),
            "ExecuteCypher": AsyncMock(return_value=_execute_response()),
            "CommitTransaction": AsyncMock(return_value=cypher_pb2.CommitTransactionResponse(applied_index=7)),
            "RollbackTransaction": AsyncMock(return_value=cypher_pb2.RollbackTransactionResponse()),
        },
    )()


def _async_client(**options):
    client = AsyncCoordinodeClient("localhost:0", **options)
    client._cypher_stub = _stub()
    return client


def _sync_client(**options):
    client = CoordinodeClient("localhost:0", **options)
    client._async._cypher_stub = _stub()
    client._connected = True
    return client


def _sent_metadata(stub_method):
    """The metadata of the one call made, as a dict.

    An absent argument reads as no metadata, which is what the client sends
    when there is none: the keyword is omitted rather than passed empty, so a
    query with tracking off makes the same call it made before the feature
    existed.
    """
    assert stub_method.await_count == 1, f"expected one call, got {stub_method.await_count}"
    return dict(stub_method.await_args.kwargs.get("metadata", ()))


# ── Off by default ───────────────────────────────────────────────────────────


class TestOffByDefault:
    """Tracking sends nothing until it is asked for, and asks the interpreter
    for nothing either: the frame is never read, so the cost of the feature
    stays with the people who turned it on."""

    def test_no_metadata_without_the_flag(self):
        async def _inner() -> None:
            client = _async_client()
            await client.cypher("RETURN 1")
            call = client._cypher_stub.ExecuteCypher.await_args
            # Not merely empty: the keyword is absent, so the default path
            # makes the call it made before this feature existed. Test
            # doubles written against that signature keep working.
            assert "metadata" not in call.kwargs

        asyncio.run(_inner())

    def test_no_frame_is_read_without_the_flag(self, monkeypatch):
        """The flag gates the frame read itself, not just the sending.

        Reading a frame is cheap but not free, and the contract for the
        default path is that it does no work at all. A capture that runs
        anyway and is then discarded would still pass the test above.
        """
        import coordinode.client as client_module

        def _forbidden(*_args, **_kwargs):
            raise AssertionError("the caller's frame was read with tracking off")

        # Patched on the client's reference to the module, not on `sys`:
        # replacing `sys._getframe` itself would also replace it for the
        # logging module, which calls it to name the line a log record came
        # from, and the interpreter would take the rest of the suite down
        # with it.
        monkeypatch.setattr(client_module._source, "capture", _forbidden)

        async def _inner() -> None:
            client = _async_client()
            await client.cypher("RETURN 1")

        asyncio.run(_inner())


# ── The reported location ────────────────────────────────────────────────────


class TestReportedLocation:
    """The line reported is the caller's own, on every path that reaches
    ExecuteCypher."""

    def test_async_query_reports_the_awaiting_line(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            await client.cypher("RETURN 1")
            expected_line = _inner.__code__.co_firstlineno + 2

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-file"] == __file__
            assert md["x-source-line"] == str(expected_line)
            assert md["x-source-function"].endswith("test_async_query_reports_the_awaiting_line.<locals>._inner")

        asyncio.run(_inner())

    def test_sync_query_reports_the_calling_line(self):
        """The synchronous client hands a coroutine to its event loop, so by
        the time the query runs, this frame has returned. The location has to
        be read on the way in or it is gone."""
        client = _sync_client(debug_source_tracking=True)
        client.cypher("RETURN 1")
        expected_line = self.test_sync_query_reports_the_calling_line.__code__.co_firstlineno + 5

        md = _sent_metadata(client._async._cypher_stub.ExecuteCypher)
        assert md["x-source-file"] == __file__
        assert md["x-source-line"] == str(expected_line)
        assert md["x-source-function"].endswith("test_sync_query_reports_the_calling_line")

    def test_transaction_statement_reports_its_own_line(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            tx = await client.begin_transaction()
            await tx.cypher("CREATE (:A)")
            expected_line = _inner.__code__.co_firstlineno + 3

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-line"] == str(expected_line)

        asyncio.run(_inner())

    def test_sync_transaction_statement_reports_its_own_line(self):
        client = _sync_client(debug_source_tracking=True)
        with client.transaction() as tx:
            tx.cypher("CREATE (:A)")
            expected_line = self.test_sync_transaction_statement_reports_its_own_line.__code__.co_firstlineno + 3

        md = _sent_metadata(client._async._cypher_stub.ExecuteCypher)
        assert md["x-source-line"] == str(expected_line)


# ── Application identity ─────────────────────────────────────────────────────


class TestApplicationIdentity:
    """Name and version are optional, and an empty one is not sent: the server
    reads a missing key and an empty value the same way, so a header carrying
    nothing is only bytes."""

    def test_name_and_version_ride_with_the_location(self):
        async def _inner() -> None:
            client = _async_client(
                debug_source_tracking=True,
                app_name="feed-service",
                app_version="2.1.0",
            )
            await client.cypher("RETURN 1")

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-app"] == "feed-service"
            assert md["x-source-version"] == "2.1.0"

        asyncio.run(_inner())

    def test_unset_identity_sends_no_key(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True, app_name="feed-service")
            await client.cypher("RETURN 1")

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-app"] == "feed-service"
            assert "x-source-version" not in md

        asyncio.run(_inner())

    def test_identity_alone_sends_nothing(self):
        """Without the flag there is no location, and the server discards a
        source context whose file is missing — so name and version alone would
        be headers the server throws away."""

        async def _inner() -> None:
            client = _async_client(app_name="feed-service", app_version="2.1.0")
            await client.cypher("RETURN 1")
            assert _sent_metadata(client._cypher_stub.ExecuteCypher) == {}

        asyncio.run(_inner())


# ── Frames that are not call sites ───────────────────────────────────────────


class TestNonCallSites:
    """A frame belonging to this package or to asyncio is what remains when
    the frame that wrote the query is already gone. Reporting it would collect
    unrelated queries under one event-loop line in the advisor, so nothing is
    sent instead."""

    def test_asyncio_frame_is_rejected(self):
        assert not is_call_site(SourceLocation(file=asyncio.__file__, line=1, function="run"))

    def test_sdk_frame_is_rejected(self):
        import coordinode.client as client_module

        assert not is_call_site(SourceLocation(file=client_module.__file__, line=1, function="cypher"))

    def test_user_frame_is_accepted(self):
        assert is_call_site(SourceLocation(file=__file__, line=1, function="test"))

    def test_neighbour_of_the_package_is_not_swallowed(self):
        """The exclusion is by directory, so a path that merely starts with
        the same characters — a sibling named `coordinode-extra` beside
        `coordinode` — must stay a call site."""
        import os

        import coordinode.client as client_module

        sdk_dir = os.path.dirname(os.path.abspath(client_module.__file__))
        neighbour = f"{sdk_dir}-extra{os.sep}app.py"
        assert is_call_site(SourceLocation(file=neighbour, line=1, function="handler"))


class TestScheduledQueries:
    """Everything that schedules the coroutine as a task runs its body after
    the frame that wrote the query has returned. The location is therefore
    read when the method is CALLED, which is the only moment every one of
    these has in common."""

    def test_create_task(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            task = asyncio.create_task(client.cypher("RETURN 1"))
            expected_line = _inner.__code__.co_firstlineno + 2
            await task

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-line"] == str(expected_line)

        asyncio.run(_inner())

    def test_gather(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            await asyncio.gather(client.cypher("RETURN 1"))
            expected_line = _inner.__code__.co_firstlineno + 2

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-line"] == str(expected_line)

        asyncio.run(_inner())

    def test_wait_for(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            await asyncio.wait_for(client.cypher("RETURN 1"), timeout=5)
            expected_line = _inner.__code__.co_firstlineno + 2

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-line"] == str(expected_line)

        asyncio.run(_inner())

    def test_task_group(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            async with asyncio.TaskGroup() as tg:
                tg.create_task(client.cypher("RETURN 1"))
            expected_line = _inner.__code__.co_firstlineno + 3

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-line"] == str(expected_line)

        asyncio.run(_inner())

    def test_transaction_statement_as_a_task(self):
        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            tx = await client.begin_transaction()
            await asyncio.create_task(tx.cypher("CREATE (:A)"))
            expected_line = _inner.__code__.co_firstlineno + 3

            md = _sent_metadata(client._cypher_stub.ExecuteCypher)
            assert md["x-source-line"] == str(expected_line)

        asyncio.run(_inner())


# ── Wire contract ────────────────────────────────────────────────────────────


class TestWireContract:
    """The keys are shared with the Rust driver and read by the server. A
    rename here is not an error anywhere: the server just stops finding the
    context, so the names are pinned in a test."""

    def test_metadata_keys(self):
        md = dict(
            to_metadata(
                SourceLocation(file="app/feed.py", line=47, function="Feed.render"),
                identity("feed-service", "2.1.0"),
            )
        )
        assert md == {
            "x-source-file": "app/feed.py",
            "x-source-line": "47",
            "x-source-function": "Feed.render",
            "x-source-app": "feed-service",
            "x-source-version": "2.1.0",
        }

    def test_no_location_means_no_metadata(self):
        assert to_metadata(None, identity("feed-service", "2.1.0")) == ()

    def test_values_are_ascii(self):
        """gRPC rejects a non-ASCII value on a key without the -bin suffix,
        and rejects it client-side: the query never reaches the server. A
        checkout under a non-ASCII path, or a function named in one — Python
        allows both — would otherwise turn tracking from a debugging aid into
        the reason every query fails.
        """
        md = dict(
            to_metadata(
                SourceLocation(file="/home/пользователь/app.py", line=47, function="Лента.render"),
                identity("сервис", "2.1.0"),
            )
        )
        for key, value in md.items():
            value.encode("ascii")  # raises if the value would be rejected
        # Escaped rather than dropped: the path still identifies the file, so
        # the advisor can still group by it and a person can still read it.
        assert "app.py" in md["x-source-file"]
        assert "render" in md["x-source-function"]
        assert md["x-source-line"] == "47"

    def test_values_are_printable(self):
        """ASCII alone is not the bar: gRPC refuses a control character in a
        header value just as it refuses a non-ASCII one, and 0x7f with them.
        An application name read from a file arrives with the trailing
        newline, and a POSIX path may legally contain one, so this is the
        likelier of the two ways to break every query."""
        md = dict(
            to_metadata(
                SourceLocation(file="/app/feed\n.py", line=47, function="render\x00"),
                identity("feed-service\n", "2.1.0\t"),
            )
        )
        for value in md.values():
            assert all(" " <= ch <= "~" for ch in value), repr(value)
        assert "feed" in md["x-source-file"]
        assert "render" in md["x-source-function"]
        assert "feed-service" in md["x-source-app"]

    def test_identity_omits_what_was_not_given(self):
        assert identity("", "") == ()
        assert identity("feed-service", "") == (("x-source-app", "feed-service"),)
        assert identity("", "2.1.0") == (("x-source-version", "2.1.0"),)


# ── Failure paths ────────────────────────────────────────────────────────────


class TestFailurePaths:
    """Tracking is a debugging aid and must never be the reason a query
    fails."""

    def test_a_failing_query_still_reports_its_error(self):
        class _Rejected(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.INVALID_ARGUMENT

        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            client._cypher_stub.ExecuteCypher = AsyncMock(side_effect=_Rejected())
            with pytest.raises(grpc.RpcError):
                await client.cypher("RETURN 1")

        asyncio.run(_inner())

    def test_a_refused_frame_read_is_not_an_error(self, monkeypatch):
        """A hardened application can install an audit hook that refuses the
        `sys._getframe` event, and the hook's exception comes out of the frame
        read rather than the ValueError a short stack gives. Either way the
        location is unavailable, and an unavailable location must not take the
        query down with it."""
        import coordinode._source as source_module

        def _refused(_depth):
            raise RuntimeError("audit hook refused sys._getframe")

        # Scoped to the single call: this replaces the attribute on the `sys`
        # module itself, which the logging machinery also reads to name the
        # line a record came from, so the substitution must not outlive the
        # one call under test.
        with monkeypatch.context() as patched:
            patched.setattr(source_module.sys, "_getframe", _refused, raising=False)
            location = source_module.capture(1)
        assert location is None

    def test_a_missing_frame_is_not_an_error(self, monkeypatch):
        """An interpreter with no Python-level frames, or a stack shorter than
        the walk, yields no location. The query goes out unattributed rather
        than failing over a debugging aid."""
        import coordinode.client as client_module

        monkeypatch.setattr(client_module._source, "capture", lambda _levels: None)

        async def _inner() -> None:
            client = _async_client(debug_source_tracking=True)
            await client.cypher("RETURN 1")
            assert _sent_metadata(client._cypher_stub.ExecuteCypher) == {}

        asyncio.run(_inner())
