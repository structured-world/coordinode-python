"""Unit tests for interactive transactions.

These drive the real generated proto messages through fake stubs, so a field
rename in the proto submodule fails here rather than reaching users as an
AttributeError mid-transaction.

What is worth testing without a server is the state machine around the three
RPCs, because that is where the SDK makes decisions of its own: which call it
sends, which it declines to send, and which error a caller ends up seeing. The
server's own guarantees (snapshot isolation, conflict detection at commit) are
tested against a live server in tests/integration/test_sdk.py.
"""

import asyncio

import grpc
import pytest

from coordinode._proto.coordinode.v1.common import types_pb2
from coordinode._proto.coordinode.v1.query import cypher_pb2
from coordinode.client import AsyncCoordinodeClient, CoordinodeClient

# ── Fixtures ─────────────────────────────────────────────────────────────────


class _ServerRejected(grpc.RpcError):
    """Stand-in for an ANSWERED gRPC failure: the server processed the request
    and refused it, so it carries a definitive status code the way a real
    rejection does. Transit losses are modelled by `_TransportError` below."""

    def code(self):
        return grpc.StatusCode.INVALID_ARGUMENT


def _execute_response(columns=(), rows=()):
    return cypher_pb2.ExecuteCypherResponse(
        columns=list(columns),
        rows=[cypher_pb2.Row(values=[types_pb2.PropertyValue(string_value=v) for v in row]) for row in rows],
    )


def _stub(**methods):
    """Build a fake CypherService stub whose named methods are AsyncMocks."""
    from unittest.mock import AsyncMock

    defaults = {
        "BeginTransaction": AsyncMock(return_value=cypher_pb2.BeginTransactionResponse(transaction_id=42)),
        "ExecuteCypher": AsyncMock(return_value=_execute_response()),
        "CommitTransaction": AsyncMock(return_value=cypher_pb2.CommitTransactionResponse(applied_index=7)),
        "RollbackTransaction": AsyncMock(return_value=cypher_pb2.RollbackTransactionResponse()),
    }
    defaults.update(methods)
    return type("FakeCypherStub", (), defaults)()


def _async_client(**methods):
    client = AsyncCoordinodeClient("localhost:0")
    client._cypher_stub = _stub(**methods)
    return client


def _sync_client(**methods):
    """A sync client wired to a fake stub, with connect() skipped.

    connect() would replace the fake stubs with real ones built from a channel,
    so the flag is set directly instead.
    """
    client = CoordinodeClient("localhost:0")
    client._async._cypher_stub = _stub(**methods)
    client._connected = True
    return client


# ── Context manager ──────────────────────────────────────────────────────────


class TestContextManager:
    def test_commits_on_clean_exit(self):
        async def _inner() -> None:
            client = _async_client()
            async with client.transaction() as tx:
                await tx.cypher("CREATE (:Person {name: $n})", {"n": "Alice"})
            assert client._cypher_stub.CommitTransaction.await_count == 1
            assert client._cypher_stub.CommitTransaction.call_args.args[0].transaction_id == 42
            assert client._cypher_stub.RollbackTransaction.await_count == 0

        asyncio.run(_inner())

    def test_rolls_back_on_exception_and_reraises_it(self):
        """The caller's exception is the one that propagates, not one from cleanup."""

        async def _inner() -> None:
            client = _async_client()
            with pytest.raises(ZeroDivisionError):
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:Person)")
                    1 / 0
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            assert client._cypher_stub.CommitTransaction.await_count == 0

        asyncio.run(_inner())

    def test_failed_rollback_does_not_mask_the_original_error(self):
        """A rollback that fails on the way out must not replace the real failure.

        The server drops an unresolved transaction on its own, so the caller
        loses nothing by not hearing about this.
        """
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(RollbackTransaction=AsyncMock(side_effect=_ServerRejected("gone")))
            with pytest.raises(ValueError, match="the real problem"):
                async with client.transaction():
                    raise ValueError("the real problem")

        asyncio.run(_inner())

    def test_manual_commit_inside_the_block_is_not_repeated(self):
        async def _inner() -> None:
            client = _async_client()
            async with client.transaction() as tx:
                await tx.commit()
            assert client._cypher_stub.CommitTransaction.await_count == 1

        asyncio.run(_inner())

    def test_manual_rollback_inside_the_block_is_not_followed_by_a_commit(self):
        async def _inner() -> None:
            client = _async_client()
            async with client.transaction() as tx:
                await tx.rollback()
            assert client._cypher_stub.CommitTransaction.await_count == 0
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())


# ── Statements ───────────────────────────────────────────────────────────────


class TestStatements:
    def test_statement_carries_the_transaction_handle(self):
        """Without the handle the server would auto-commit the statement."""

        async def _inner() -> None:
            client = _async_client()
            async with client.transaction() as tx:
                await tx.cypher("CREATE (:Person {name: $n})", {"n": "Alice"})
            sent = client._cypher_stub.ExecuteCypher.call_args.args[0]
            assert sent.transaction_id == 42
            assert sent.query == "CREATE (:Person {name: $n})"
            assert sent.parameters["n"].string_value == "Alice"

        asyncio.run(_inner())

    def test_rows_are_decoded(self):
        async def _inner() -> None:
            from unittest.mock import AsyncMock

            client = _async_client(
                ExecuteCypher=AsyncMock(return_value=_execute_response(["name"], [["Alice"], ["Bob"]]))
            )
            async with client.transaction() as tx:
                rows = await tx.cypher("MATCH (n:Person) RETURN n.name AS name")
            assert rows == [{"name": "Alice"}, {"name": "Bob"}]

        asyncio.run(_inner())

    def test_consistency_arguments_are_refused(self):
        """The in-transaction path ignores them server-side, so accepting them would mislead.

        The snapshot is fixed at the begin and durability is decided once at the
        commit, which leaves nothing for a per-statement read or write concern
        to mean.
        """

        async def _inner() -> None:
            client = _async_client()
            async with client.transaction() as tx:
                with pytest.raises(TypeError):
                    await tx.cypher("MATCH (n) RETURN n", read_concern="majority")

        asyncio.run(_inner())


# ── A failed statement ends the transaction ──────────────────────────────────


class TestAbort:
    """The server discards the buffered state on any statement error and consumes
    the handle, so the SDK has to stop treating the transaction as usable."""

    @staticmethod
    def _client_whose_statement_fails():
        from unittest.mock import AsyncMock

        return _async_client(ExecuteCypher=AsyncMock(side_effect=_ServerRejected("bad query")))

    def test_statement_error_propagates(self):
        async def _inner() -> None:
            client = self._client_whose_statement_fails()
            with pytest.raises(_ServerRejected):
                async with client.transaction() as tx:
                    await tx.cypher("RETURN nonsense(")

        asyncio.run(_inner())

    def test_exactly_one_cleanup_is_sent_and_never_a_commit(self):
        """This asserted that no rollback was sent, back when the statement path
        classified failures. It does send one now, unconditionally, because
        classifying was how an unprocessed statement leaked a transaction for
        the idle timeout. What still must not happen is a second cleanup from
        the context manager on the way out, or a commit."""

        async def _inner() -> None:
            client = self._client_whose_statement_fails()
            with pytest.raises(_ServerRejected):
                async with client.transaction() as tx:
                    await tx.cypher("RETURN nonsense(")
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            assert client._cypher_stub.CommitTransaction.await_count == 0

        asyncio.run(_inner())

    def test_commit_after_an_aborted_statement_explains_itself(self):
        async def _inner() -> None:
            client = self._client_whose_statement_fails()
            tx = await client.begin_transaction()
            with pytest.raises(_ServerRejected):
                await tx.cypher("RETURN nonsense(")
            with pytest.raises(RuntimeError, match="an earlier failure closed it"):
                await tx.commit()
            assert client._cypher_stub.CommitTransaction.await_count == 0

        asyncio.run(_inner())

    def test_rollback_after_an_aborted_statement_adds_no_second_call(self):
        """The failing statement already sent the cleanup, so an explicit
        rollback has nothing left to do and must not repeat the request."""

        async def _inner() -> None:
            client = self._client_whose_statement_fails()
            tx = await client.begin_transaction()
            with pytest.raises(_ServerRejected):
                await tx.cypher("RETURN nonsense(")
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            assert tx.is_open is False

        asyncio.run(_inner())

    def test_a_rejected_commit_also_ends_the_transaction(self):
        """A conflict is reported at the commit, which consumes the handle too."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(CommitTransaction=AsyncMock(side_effect=_ServerRejected("conflict")))
            tx = await client.begin_transaction()
            with pytest.raises(_ServerRejected):
                await tx.commit()
            assert tx.is_open is False
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 0

        asyncio.run(_inner())


# ── Reuse after the transaction is over ──────────────────────────────────────


class TestReuse:
    def test_statement_after_commit_raises(self):
        async def _inner() -> None:
            client = _async_client()
            tx = await client.begin_transaction()
            await tx.commit()
            with pytest.raises(RuntimeError, match="already committed"):
                await tx.cypher("MATCH (n) RETURN n")

        asyncio.run(_inner())

    def test_commit_twice_raises(self):
        async def _inner() -> None:
            client = _async_client()
            tx = await client.begin_transaction()
            await tx.commit()
            with pytest.raises(RuntimeError, match="already committed"):
                await tx.commit()
            assert client._cypher_stub.CommitTransaction.await_count == 1

        asyncio.run(_inner())

    def test_statement_after_rollback_raises(self):
        async def _inner() -> None:
            client = _async_client()
            tx = await client.begin_transaction()
            await tx.rollback()
            with pytest.raises(RuntimeError, match="already rolled back"):
                await tx.cypher("MATCH (n) RETURN n")

        asyncio.run(_inner())


# ── Explicit API ─────────────────────────────────────────────────────────────


class TestExplicitApi:
    def test_begin_returns_the_server_handle(self):
        async def _inner() -> None:
            client = _async_client()
            tx = await client.begin_transaction()
            assert tx.transaction_id == 42
            assert tx.is_open is True

        asyncio.run(_inner())

    def test_commit_returns_the_applied_index(self):
        """Which a later causal read can pass as after_index."""

        async def _inner() -> None:
            client = _async_client()
            tx = await client.begin_transaction()
            assert await tx.commit() == 7

        asyncio.run(_inner())


# ── Sync wrapper ─────────────────────────────────────────────────────────────


class TestSyncClient:
    def test_context_manager_commits(self):
        client = _sync_client()
        with client.transaction() as tx:
            tx.cypher("CREATE (:Person {name: $n})", {"n": "Alice"})
        assert client._async._cypher_stub.CommitTransaction.await_count == 1
        assert client._async._cypher_stub.ExecuteCypher.call_args.args[0].transaction_id == 42

    def test_context_manager_rolls_back_on_exception(self):
        client = _sync_client()
        with pytest.raises(ZeroDivisionError):
            with client.transaction() as tx:
                tx.cypher("CREATE (:Person)")
                1 / 0
        assert client._async._cypher_stub.RollbackTransaction.await_count == 1
        assert client._async._cypher_stub.CommitTransaction.await_count == 0

    def test_explicit_commit_returns_the_applied_index(self):
        client = _sync_client()
        tx = client.begin_transaction()
        assert tx.transaction_id == 42
        assert tx.commit() == 7
        assert tx.is_open is False

    def test_reuse_after_commit_raises(self):
        client = _sync_client()
        tx = client.begin_transaction()
        tx.commit()
        with pytest.raises(RuntimeError, match="already committed"):
            tx.cypher("MATCH (n) RETURN n")


# -- Transport failures that prove nothing ------------------------------------
#
# A gRPC error is only sometimes an answer. DEADLINE_EXCEEDED, UNAVAILABLE,
# CANCELLED and UNKNOWN mean the request or its reply was lost somewhere on the
# way, so the server may have processed the call or may never have seen it.
# Treating those like a server rejection produced two bugs: a lost statement
# left the server holding the transaction until the idle sweep, and a lost
# commit reply told the caller nothing was applied when it may all have been.


class _TransportError(grpc.RpcError):
    """A gRPC failure with a status code, like the real client raises.

    `trailing` models the trailing metadata a SERVER-answered failure
    carries; a failure generated inside the client (a receive limit, a lost
    connection) has none.
    """

    def __init__(self, code, trailing=None):
        self._code = code
        self._trailing = trailing

    def code(self):
        return self._code

    def trailing_metadata(self):
        return self._trailing


class TestAmbiguousStatementFailure:
    @staticmethod
    def _client_with_lost_statement():
        from unittest.mock import AsyncMock

        return _async_client(ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)))

    def test_sends_a_best_effort_rollback(self):
        """The statement may never have arrived, leaving the transaction open on
        the server with its buffered writes until the idle sweep. A rollback
        frees it now; if the statement did arrive and abort it, the server
        answers "unknown transaction id" and there was nothing to free."""

        async def _inner() -> None:
            client = self._client_with_lost_statement()
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("CREATE (:Person)")
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            assert tx.is_open is False

        asyncio.run(_inner())

    def test_manual_rollback_after_it_is_a_no_op(self):
        """The cleanup already happened; a second RollbackTransaction would only
        collect an "unknown transaction id" answer."""

        async def _inner() -> None:
            client = self._client_with_lost_statement()
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("CREATE (:Person)")
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())

    def test_a_failed_cleanup_does_not_mask_the_statement_error(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.UNAVAILABLE)),
                RollbackTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.UNAVAILABLE)),
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("CREATE (:Person)")

        asyncio.run(_inner())

    def test_an_answered_rejection_still_reaches_the_caller_unchanged(self):
        """INVALID_ARGUMENT is the server speaking: it processed the statement,
        discarded the transaction and consumed the handle, so the cleanup this
        path now sends is answered "unknown transaction id" and swallowed.

        This asserted no rollback was sent, back when statement failures were
        classified by code. The classification is gone because it decided,
        wrongly, for codes the server never acted on. What matters here is what
        the caller sees: their own error, and a closed handle."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.INVALID_ARGUMENT)),
                RollbackTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.NOT_FOUND)),
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError) as caught:
                await tx.cypher("RETURN (")
            assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert tx.is_open is False

        asyncio.run(_inner())


class TestIndeterminateCommit:
    @staticmethod
    def _client_with_lost_commit_reply():
        from unittest.mock import AsyncMock

        return _async_client(
            CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED))
        )

    def test_outcome_is_recorded_as_unknown_not_aborted(self):
        """The server may have applied every buffered write before the reply
        was lost, or never seen the commit. Claiming an abort would invite a
        retry that duplicates the writes."""

        async def _inner() -> None:
            client = self._client_with_lost_commit_reply()
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.commit()
            assert tx.is_open is False
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.cypher("RETURN 1")
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.commit()

        asyncio.run(_inner())

    def test_rollback_attempts_cleanup_but_refuses_to_promise_a_discard(self):
        """If the commit never arrived, the rollback frees the transaction; if
        it was applied, nothing can un-apply it. So the request is sent and the
        call still raises, because "nothing reached the database" cannot be
        promised either way."""

        async def _inner() -> None:
            client = self._client_with_lost_commit_reply()
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.commit()
            with pytest.raises(RuntimeError, match="may already be applied"):
                await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())

    def test_an_answered_commit_rejection_is_still_a_plain_abort(self):
        """A conflict is a real answer: the server consumed the handle and
        applied nothing, so no indeterminacy is involved."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.ABORTED)))
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.commit()
            with pytest.raises(RuntimeError, match="an earlier failure closed it"):
                await tx.cypher("RETURN 1")
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 0

        asyncio.run(_inner())

    def test_an_error_that_cannot_report_a_code_is_read_as_ambiguous(self):
        """The two misreadings are not symmetric: a needless cleanup rollback
        costs one RPC answered "unknown transaction id", while a wrongly
        claimed abort invites a retry that duplicates every write. So an error
        proving nothing gets the careful reading, not the convenient one."""
        from unittest.mock import AsyncMock

        class _Codeless(grpc.RpcError):
            pass

        async def _inner() -> None:
            client = _async_client(CommitTransaction=AsyncMock(side_effect=_Codeless()))
            tx = await client.begin_transaction()
            with pytest.raises(_Codeless):
                await tx.commit()
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.commit()

        asyncio.run(_inner())


class TestBeginValidation:
    def test_a_zero_handle_is_refused(self):
        """The wire defines zero on ExecuteCypherRequest as auto-commit, so a
        zero handle would silently turn every statement of this transaction
        into its own committed write. The protocol promises begin answers a
        non-zero id; a server that breaks that promise gets refused, not
        obeyed."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                BeginTransaction=AsyncMock(return_value=cypher_pb2.BeginTransactionResponse(transaction_id=0))
            )
            with pytest.raises(RuntimeError, match="transaction_id=0"):
                await client.begin_transaction()

        asyncio.run(_inner())


class TestRowDecoding:
    """The shared decoder for both the auto-commit and the in-transaction path."""

    def test_a_short_row_is_an_error_not_a_missing_key(self):
        """Silently dropping a column hands the caller a dict whose missing key
        looks like an absent property. A wire-shape mismatch is a decoding
        failure and should say so at the decode point."""
        from unittest.mock import AsyncMock

        short_row = cypher_pb2.ExecuteCypherResponse(
            columns=["a", "b"],
            rows=[cypher_pb2.Row(values=[types_pb2.PropertyValue(string_value="only-one")])],
        )

        async def _inner() -> None:
            client = _async_client(ExecuteCypher=AsyncMock(return_value=short_row))
            with pytest.raises(ValueError):
                await client.cypher("MATCH (n) RETURN n.a AS a, n.b AS b")

        asyncio.run(_inner())

    def test_a_long_row_is_an_error_too(self):
        from unittest.mock import AsyncMock

        long_row = cypher_pb2.ExecuteCypherResponse(
            columns=["a"],
            rows=[
                cypher_pb2.Row(
                    values=[
                        types_pb2.PropertyValue(string_value="one"),
                        types_pb2.PropertyValue(string_value="unexpected"),
                    ]
                )
            ],
        )

        async def _inner() -> None:
            client = _async_client(ExecuteCypher=AsyncMock(return_value=long_row))
            with pytest.raises(ValueError):
                await client.cypher("MATCH (n) RETURN n.a AS a")

        asyncio.run(_inner())


class TestCausalReadValidation:
    """The client guard for `after_index` checked the wrong field entirely.

    The server refuses a causal read unless the READ concern is majority (its
    message: "readConcern=LOCAL is incompatible with afterClusterTime"). The
    guard demanded a majority WRITE concern instead, so it rejected valid calls
    and waved through invalid ones.
    """

    def test_a_majority_read_concern_is_accepted(self):
        """This is the call the server actually wants; the guard used to refuse it."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(ExecuteCypher=AsyncMock(return_value=_execute_response()))
            await client.cypher("MATCH (n) RETURN n", after_index=7, read_concern="majority")
            sent = client._cypher_stub.ExecuteCypher.call_args.args[0]
            assert sent.read_concern.after_index == 7

        asyncio.run(_inner())

    def test_a_majority_write_concern_alone_is_refused(self):
        """The guard used to accept this and let the server reject it instead."""

        async def _inner() -> None:
            client = _async_client()
            with pytest.raises(ValueError, match="read_concern='majority'"):
                await client.cypher("MATCH (n) RETURN n", after_index=7, write_concern="majority")

        asyncio.run(_inner())

    def test_no_concern_at_all_is_refused(self):
        async def _inner() -> None:
            client = _async_client()
            with pytest.raises(ValueError, match="read_concern='majority'"):
                await client.cypher("MATCH (n) RETURN n", after_index=7)

        asyncio.run(_inner())

    def test_a_non_string_read_concern_is_a_value_error_not_a_crash(self):
        """The guard runs before the concern validators, so a non-string used
        to crash it with AttributeError from `.strip()` instead of the clear
        rejection every other invalid consistency value gets."""

        async def _inner() -> None:
            client = _async_client()
            with pytest.raises(ValueError):
                await client.cypher("MATCH (n) RETURN n", after_index=7, read_concern=1)

        asyncio.run(_inner())


# -- Failures that are not gRPC errors, and cleanup that must not be skipped ---


class TestCommitCancellation:
    def test_a_cancelled_commit_is_indeterminate_not_open(self):
        """`asyncio.CancelledError` is a BaseException, so `except grpc.RpcError`
        never sees it. The RPC may still have reached the server and applied
        everything, so leaving the transaction open invites exactly the retry
        the indeterminate state exists to prevent."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(CommitTransaction=AsyncMock(side_effect=asyncio.CancelledError()))
            tx = await client.begin_transaction()
            with pytest.raises(asyncio.CancelledError):
                await tx.commit()
            assert tx.is_open is False
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.commit()

        asyncio.run(_inner())

    def test_a_cancelled_statement_closes_the_transaction(self):
        """No commit was sent, so nothing of this transaction can ever apply;
        the handle is closed rather than left open for reuse."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(ExecuteCypher=AsyncMock(side_effect=asyncio.CancelledError()))
            tx = await client.begin_transaction()
            with pytest.raises(asyncio.CancelledError):
                await tx.cypher("CREATE (:Person)")
            assert tx.is_open is False

        asyncio.run(_inner())


class TestRollbackTransportFailure:
    def test_a_lost_rollback_still_closes_the_transaction(self):
        """The request may not have landed, so the server may still hold the
        transaction until the idle sweep. What is certain is that no commit was
        ever sent, so nothing can apply: the discard promise holds and the
        handle must not stay usable — though it stays RETRIABLE for rollback,
        so the closed state reads as aborted, not rolled back."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                RollbackTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.UNAVAILABLE))
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.rollback()
            assert tx.is_open is False
            with pytest.raises(RuntimeError, match="earlier failure closed it"):
                await tx.cypher("CREATE (:Person)")

        asyncio.run(_inner())


class TestStatementCleanupIsUnconditional:
    def test_a_client_side_size_failure_still_cleans_up(self):
        """RESOURCE_EXHAUSTED is raised by the client when the response exceeds
        its receive limit, after the server has executed the statement and kept
        the transaction open. Classifying codes missed this one; cleanup for
        statements is now unconditional, so the next unclassified code cannot
        leak a transaction either."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.RESOURCE_EXHAUSTED))
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("MATCH (n) RETURN n")
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())


class TestCancellationCleanup:
    """Cancellation can arrive after the server has the statement, so the
    transaction may still be alive there with its buffered writes and a pinned
    snapshot. Nothing can free it afterwards: the handle is closed, so both
    `rollback()` and the context manager decline to act."""

    def test_a_really_cancelled_statement_still_sends_the_cleanup(self):
        """Cancels a task mid-flight rather than raising CancelledError from a
        mock, so the shielding is exercised the way the event loop does it."""

        async def _inner() -> None:
            from unittest.mock import AsyncMock

            in_flight = asyncio.Event()

            async def never_answers(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            # Wrapped in AsyncMock: a bare function stored on the fake stub
            # class would bind as a method and receive the stub as `req`.
            client = _async_client(ExecuteCypher=AsyncMock(side_effect=never_answers))
            tx = await client.begin_transaction()
            task = asyncio.create_task(tx.cypher("CREATE (:Person)"))
            await in_flight.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # The cleanup outlives the cancellation, so give the loop a turn.
            await asyncio.sleep(0.05)
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            assert tx.is_open is False

        asyncio.run(_inner())

    def test_a_cancelled_commit_sends_no_cleanup(self):
        """The opposite case, and the reason this is not symmetric: after a
        commit the writes may be applied, and a rollback cannot un-apply them.
        Sending one could only discard a transaction the server still holds,
        turning an unknown outcome into a silently discarded one."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(CommitTransaction=AsyncMock(side_effect=asyncio.CancelledError()))
            tx = await client.begin_transaction()
            with pytest.raises(asyncio.CancelledError):
                await tx.commit()
            await asyncio.sleep(0.05)
            assert client._cypher_stub.RollbackTransaction.await_count == 0

        asyncio.run(_inner())


class TestCommitFailureClassification:
    """Which commit failures are definitive rejections and which leave the
    outcome unknown. The dangerous misreading is one-directional: telling the
    caller "nothing was applied" when the server may have applied everything
    invites a retry that duplicates the writes."""

    @staticmethod
    def _commit_failing_with(err):
        from unittest.mock import AsyncMock

        return _async_client(CommitTransaction=AsyncMock(side_effect=err))

    def test_a_local_resource_exhausted_reply_failure_is_indeterminate(self):
        """RESOURCE_EXHAUSTED can be generated INSIDE the client while
        receiving an oversized reply, after the server already applied the
        commit. Without the server's structured details in the trailing
        metadata, the code alone proves nothing."""

        async def _inner() -> None:
            client = self._commit_failing_with(_TransportError(grpc.StatusCode.RESOURCE_EXHAUSTED))
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.commit()
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())

    def test_a_server_answered_resource_exhausted_is_a_plain_abort(self):
        """The same code WITH the server's structured error details is an
        answer (a transaction-too-large rejection): nothing was applied."""

        async def _inner() -> None:
            client = self._commit_failing_with(
                _TransportError(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    trailing=[("grpc-status-details-bin", b"\x08\x08")],
                )
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.commit()
            with pytest.raises(RuntimeError, match="an earlier failure closed it"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())

    def test_a_bare_internal_error_is_indeterminate(self):
        """INTERNAL without details can come from either side of the wire and
        says nothing about whether the proposal was applied."""

        async def _inner() -> None:
            client = self._commit_failing_with(_TransportError(grpc.StatusCode.INTERNAL))
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.commit()
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())


class TestCleanupDrainOnClose:
    """The detached cancellation cleanup must not race client shutdown: the
    channel closing first would strand the transaction on the server."""

    def test_close_awaits_spawned_cleanup_before_returning(self):
        async def _inner() -> None:
            from unittest.mock import AsyncMock

            in_flight = asyncio.Event()
            rollback_done = asyncio.Event()

            async def never_answers(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            async def slow_rollback(req, timeout=None):
                # Slower than a single event-loop turn: merely yielding once
                # is not enough for this to finish, so the assertion below
                # holds only if close() genuinely awaits the cleanup task.
                await asyncio.sleep(0.05)
                rollback_done.set()

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=never_answers),
                RollbackTransaction=AsyncMock(side_effect=slow_rollback),
            )
            tx = await client.begin_transaction()
            task = asyncio.create_task(tx.cypher("CREATE (:Person)"))
            await in_flight.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await client.close()
            assert rollback_done.is_set(), "close() returned before the cleanup finished"

        asyncio.run(_inner())


class TestCleanupDeadline:
    """Best-effort cleanup must not double the caller's worst-case latency:
    a statement that already burned the full RPC deadline would otherwise be
    followed by a rollback burning another one, for a result nobody reads."""

    def test_cleanup_rollback_uses_a_short_deadline_not_the_client_timeout(self):
        async def _inner() -> None:
            from unittest.mock import AsyncMock

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED))
            )
            client._timeout = 30.0
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("RETURN 1")
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            used = client._cypher_stub.RollbackTransaction.call_args.kwargs["timeout"]
            assert used < 30.0, f"cleanup must use a short deadline, got {used}"

        asyncio.run(_inner())

    def test_cleanup_deadline_is_capped_by_a_shorter_client_timeout(self):
        """A caller who configured 100ms requests must not find a failed
        statement holding the line for a multi-second cleanup."""

        async def _inner() -> None:
            from unittest.mock import AsyncMock

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED))
            )
            client._timeout = 0.1
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("RETURN 1")
            used = client._cypher_stub.RollbackTransaction.call_args.kwargs["timeout"]
            assert used == 0.1, f"cleanup must not exceed the client timeout, got {used}"

        asyncio.run(_inner())


class TestRollbackCancellationDoesNotMaskTheBlockError:
    """A rollback cancelled on the way out of the context manager must not
    replace the exception that caused the rollback: CancelledError is a
    BaseException, so a plain `suppress(Exception)` let it through."""

    def test_async_context_preserves_the_block_exception(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(RollbackTransaction=AsyncMock(side_effect=asyncio.CancelledError()))
            with pytest.raises(ValueError, match="the real problem"):
                async with client.transaction():
                    raise ValueError("the real problem")

        asyncio.run(_inner())

    def test_sync_context_preserves_the_block_exception(self):
        from unittest.mock import AsyncMock

        client = _sync_client(RollbackTransaction=AsyncMock(side_effect=asyncio.CancelledError()))
        with pytest.raises(ValueError, match="the real problem"):
            with client.transaction():
                raise ValueError("the real problem")


class TestSyncCommitInterruption:
    """Ctrl-C during a synchronous commit crosses the loop boundary as
    KeyboardInterrupt, which the async handlers never see. The server may
    already have applied the writes, so the handle must come back
    indeterminate, not open: an "open" handle invites the duplicate retry."""

    def test_an_interrupted_sync_commit_is_indeterminate(self):
        from unittest.mock import AsyncMock

        client = _sync_client(CommitTransaction=AsyncMock(side_effect=KeyboardInterrupt()))
        tx = client.begin_transaction()
        with pytest.raises(KeyboardInterrupt):
            tx.commit()
        assert tx.is_open is False
        with pytest.raises(RuntimeError, match="outcome is unknown"):
            tx.cypher("RETURN 1")


class TestCancellationDuringInlineCleanup:
    """A statement failure runs its cleanup inline; a cancellation arriving
    DURING that cleanup must not lose it (the handle is already closed, so
    nothing later would retry), it must detach it."""

    def test_cleanup_cancelled_mid_flight_is_respawned_detached(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            cleanup_in_flight = asyncio.Event()
            cleanup_done = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    cleanup_in_flight.set()
                    await asyncio.sleep(10)
                cleanup_done.set()

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.INVALID_ARGUMENT)),
                RollbackTransaction=AsyncMock(side_effect=rollback),
            )
            tx = await client.begin_transaction()
            task = asyncio.create_task(tx.cypher("RETURN ("))
            await cleanup_in_flight.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # The detached retry must be drained by close(), like every
            # cancellation-spawned cleanup.
            await client.close()
            assert cleanup_done.is_set(), "the cleanup was lost to the cancellation"

        asyncio.run(_inner())


class TestLateCleanupDrain:
    """close() must not settle for a one-time snapshot of the cleanup set: a
    statement cancelled WHILE the drain awaits an earlier cleanup adds its
    task after the snapshot, and a single gather would strand it against a
    closed channel."""

    def test_close_drains_cleanups_spawned_during_the_drain(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            stmt2_in_flight = asyncio.Event()
            late_done = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                # Every cleanup is slower than one loop turn, so the late one
                # can only complete if close() genuinely waits for it too (a
                # one-time snapshot would return while it is still running).
                calls["n"] += 1
                mine = calls["n"]
                await asyncio.sleep(0.1)
                if mine == 2:
                    late_done.set()

            async def hang(req, timeout=None):
                stmt2_in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=hang),
                RollbackTransaction=AsyncMock(side_effect=rollback),
            )
            tx1 = await client.begin_transaction()
            tx2 = await client.begin_transaction()
            # First cancellation: its cleanup is the slow one close() drains.
            t1 = asyncio.create_task(tx1.cypher("CREATE (:A)"))
            await stmt2_in_flight.wait()
            stmt2_in_flight.clear()
            t1.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t1
            # Second statement still in flight when close() starts.
            t2 = asyncio.create_task(tx2.cypher("CREATE (:B)"))
            await stmt2_in_flight.wait()
            closer = asyncio.create_task(client.close())
            await asyncio.sleep(0.02)  # close() is now inside the drain
            t2.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t2
            await closer
            assert late_done.is_set(), "a cleanup spawned during the drain was stranded"

        asyncio.run(_inner())


class TestIndeterminateCommitCleanupInContext:
    """An automatic commit whose reply is lost may never have REACHED the
    server, leaving the transaction open there; the context-managed caller has
    already left the owning scope, so the exit sends the same best-effort
    rollback the explicit rollback() path uses, without touching the original
    error or the indeterminate verdict."""

    def test_async_context_sends_best_effort_cleanup_and_keeps_the_error(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED))
            )
            with pytest.raises(_TransportError):
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())


class TestSyncInterruptionDrainsThePendingStatement:
    """Ctrl-C reaches the private loop as KeyboardInterrupt from a callback,
    leaving the statement's task pending. A later call on the same loop would
    RESUME that statement and race it against the caller's cleanup, so the
    sync boundary must cancel and drain it before propagating."""

    def test_interrupted_statement_is_cancelled_not_left_pending(self):
        from unittest.mock import AsyncMock

        in_flight = {"seen": False}

        async def hang(req, timeout=None):
            in_flight["seen"] = True
            await asyncio.sleep(10)

        client = _sync_client(ExecuteCypher=AsyncMock(side_effect=hang))
        tx = client.begin_transaction()
        loop = client._loop

        def interrupt():
            raise KeyboardInterrupt

        loop.call_later(0.02, interrupt)
        with pytest.raises(KeyboardInterrupt):
            tx.cypher("CREATE (:A)")
        assert in_flight["seen"] is True
        # The statement's task was cancelled and drained: its cancellation
        # handler closed the handle, and nothing is left to resume.
        assert tx.is_open is False
        assert not asyncio.all_tasks(loop), "a pending task survived the interruption"


class TestNoCleanupSpawnsAfterClose:
    """Once close() has drained and released the channel, a late cancellation
    must not spawn a cleanup that would only fail against the dead transport:
    the server's idle sweep is the documented backstop then."""

    def test_a_cancellation_after_close_spawns_nothing(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            tx = await client.begin_transaction()
            task = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await in_flight.wait()
            await client.close()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.05)
            assert client._cypher_stub.RollbackTransaction.await_count == 0, (
                "a cleanup spawned after close can only fail against the closed channel"
            )

        asyncio.run(_inner())


class TestCancelledCloseDoesNotAbortCleanup:
    """Cancelling the task that runs close() must not take the in-flight
    cleanup down with it: the drain is shielded, so the rollback completes
    detached while the cancellation propagates."""

    def test_cleanup_survives_a_cancelled_close(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            stmt_in_flight = asyncio.Event()
            cleanup_started = asyncio.Event()
            cleanup_done = asyncio.Event()

            async def hang(req, timeout=None):
                stmt_in_flight.set()
                await asyncio.sleep(10)

            async def slow_rollback(req, timeout=None):
                cleanup_started.set()
                await asyncio.sleep(0.1)
                cleanup_done.set()

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=hang),
                RollbackTransaction=AsyncMock(side_effect=slow_rollback),
            )
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await stmt_in_flight.wait()
            stmt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stmt
            closer = asyncio.create_task(client.close())
            await cleanup_started.wait()
            # Let the closer actually enter its drain (a cancel delivered
            # before it starts would never reach the gather).
            await asyncio.sleep(0.03)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            await asyncio.sleep(0.15)
            assert cleanup_done.is_set(), "cancelling close() killed the cleanup"

        asyncio.run(_inner())


class TestConcurrentCommitSerialization:
    """Two tasks committing the same handle must not race the state machine:
    the second must be refused BEFORE it sends anything, or the loser's
    "unknown transaction" rejection would overwrite `committed` with
    `aborted` and invite a duplicate retry."""

    def test_a_second_concurrent_commit_is_refused_not_raced(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            async def slow_commit(req, timeout=None):
                await asyncio.sleep(0.05)
                return cypher_pb2.CommitTransactionResponse(applied_index=7)

            client = _async_client(CommitTransaction=AsyncMock(side_effect=slow_commit))
            tx = await client.begin_transaction()
            first = asyncio.create_task(tx.commit())
            await asyncio.sleep(0.01)  # first commit is now awaiting the RPC
            with pytest.raises(RuntimeError, match="in flight"):
                await tx.commit()
            assert await first == 7
            assert client._cypher_stub.CommitTransaction.await_count == 1
            # The handle records the real outcome, untouched by the refusal.
            with pytest.raises(RuntimeError, match="already committed"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())

    def test_a_statement_during_a_commit_is_refused(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            async def slow_commit(req, timeout=None):
                await asyncio.sleep(0.05)
                return cypher_pb2.CommitTransactionResponse(applied_index=7)

            client = _async_client(CommitTransaction=AsyncMock(side_effect=slow_commit))
            tx = await client.begin_transaction()
            first = asyncio.create_task(tx.commit())
            await asyncio.sleep(0.01)
            with pytest.raises(RuntimeError, match="in flight"):
                await tx.cypher("CREATE (:Late)")
            assert await first == 7
            assert client._cypher_stub.ExecuteCypher.await_count == 0

        asyncio.run(_inner())


class TestConcurrentStatementSerialization:
    """The mirror image of the commit race: while a statement awaits its RPC
    the handle must not accept a concurrent commit (the commit could land
    without the statement's write, then the statement's failure would
    overwrite `committed` with `aborted`) nor a second statement."""

    def test_a_commit_during_a_statement_is_refused(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            started = asyncio.Event()

            async def slow_execute(req, timeout=None):
                started.set()
                await asyncio.sleep(0.05)
                return _execute_response()

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=slow_execute))
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await started.wait()
            with pytest.raises(RuntimeError, match="in flight"):
                await tx.commit()
            assert client._cypher_stub.CommitTransaction.await_count == 0
            await stmt
            # The handle is usable again once the statement resolved.
            assert await tx.commit() == 7

        asyncio.run(_inner())

    def test_a_second_concurrent_statement_is_refused(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            started = asyncio.Event()

            async def slow_execute(req, timeout=None):
                started.set()
                await asyncio.sleep(0.05)
                return _execute_response()

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=slow_execute))
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await started.wait()
            with pytest.raises(RuntimeError, match="in flight"):
                await tx.cypher("CREATE (:B)")
            await stmt
            assert client._cypher_stub.ExecuteCypher.await_count == 1

        asyncio.run(_inner())


class TestLocalEncodingFailureKeepsTheHandleUsable:
    """A parameter the encoder rejects fails LOCALLY, before any RPC: the
    handle must stay open (nothing changed server-side), not be marooned in
    an in-flight state that rejects even rollback."""

    def test_a_bad_parameter_leaves_the_transaction_open(self):
        async def _inner() -> None:
            client = _async_client()
            tx = await client.begin_transaction()
            with pytest.raises(Exception):
                await tx.cypher("CREATE (:A {v: $v})", {"v": object()})
            assert client._cypher_stub.ExecuteCypher.await_count == 0
            assert tx.is_open is True
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())


class TestUnconfirmedCleanupIsRetriedByExplicitRollback:
    """When a statement failed ambiguously AND its best-effort cleanup also
    failed, the server may still hold the transaction. An explicit rollback()
    afterwards must retry the cleanup instead of declaring the transaction
    already gone."""

    def test_rollback_after_failed_cleanup_sends_again(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            calls = {"n": 0}

            async def flaky_rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise _TransportError(grpc.StatusCode.UNAVAILABLE)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.UNAVAILABLE)),
                RollbackTransaction=AsyncMock(side_effect=flaky_rollback),
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("CREATE (:A)")
            # The inline cleanup failed (suppressed); connectivity recovers.
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 2

        asyncio.run(_inner())


class TestCancelledBlockDetachesTheRollback:
    """A block unwound by cancellation must not hold __aexit__ for a full
    rollback round trip: the cleanup goes detached (drained at close), so
    the cancellation propagates immediately."""

    def test_context_exit_does_not_await_the_rollback_inline(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_done = asyncio.Event()

            async def slow_rollback(req, timeout=None):
                await asyncio.sleep(0.2)
                rollback_done.set()

            client = _async_client(RollbackTransaction=AsyncMock(side_effect=slow_rollback))
            with pytest.raises(asyncio.CancelledError):
                async with client.transaction():
                    raise asyncio.CancelledError()
            assert not rollback_done.is_set(), "the context exit awaited the rollback inline instead of detaching it"
            await client.close()
            assert rollback_done.is_set(), "the detached rollback was not drained"

        asyncio.run(_inner())


class TestCancelledChannelCloseKeepsCleanupUsable:
    """If channel.close() itself is cancelled, the transport may still be
    usable: the closing flag must be restored so later cancellations still
    spawn their cleanup instead of forfeiting it."""

    def test_cleanups_still_spawn_after_a_cancelled_close(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            class _HangingChannel:
                async def close(self):
                    await asyncio.sleep(10)

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            client._channel = _HangingChannel()
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await in_flight.wait()

            closer = asyncio.create_task(client.close())
            await asyncio.sleep(0.02)  # closer is awaiting channel.close()
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer

            stmt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stmt
            await asyncio.sleep(0.05)
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "a cancelled channel close must not permanently disable cleanup"
            )

        asyncio.run(_inner())


class TestInFlightOperationAtContextExit:
    """A block that starts a statement or commit in a background task and
    exits without awaiting it leaves the transaction in a transient state;
    reporting a successful context exit then would let buffered writes and a
    pinned snapshot outlive the owning scope unnoticed."""

    def test_exiting_with_a_statement_in_flight_raises(self):
        from contextlib import suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            bg = None
            with pytest.raises(RuntimeError, match="in flight"):
                async with client.transaction() as tx:
                    bg = asyncio.create_task(tx.cypher("CREATE (:A)"))
                    await in_flight.wait()
            bg.cancel()
            with suppress(asyncio.CancelledError):
                await bg

        asyncio.run(_inner())


class TestCancelledManualCommitCleansUpOnContextExit:
    """A manual commit() inside the block, cancelled mid-flight, marks the
    transaction indeterminate before the cancellation reaches the context
    manager; the exit must still send the detached best-effort rollback in
    case the commit never reached the server."""

    def test_cancelled_manual_commit_spawns_cleanup(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            commit_in_flight = asyncio.Event()

            async def hang_commit(req, timeout=None):
                commit_in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(CommitTransaction=AsyncMock(side_effect=hang_commit))

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    await tx.commit()

            t = asyncio.create_task(run_block())
            await commit_in_flight.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            # close() drains the detached cleanup before the assertion.
            await client.close()
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "a cancelled manual commit left the server transaction to the idle sweep"
            )

        asyncio.run(_inner())


class TestCancelledAutomaticCommitDetachesTheCleanup:
    """Cancellation during the context manager's automatic commit must not
    hold the exit for an inline rollback round trip: caught cancellation is
    not re-injected at the next await, so the exit could otherwise overrun a
    surrounding asyncio.timeout by the whole cleanup deadline."""

    def test_exit_does_not_wait_for_the_rollback(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            commit_in_flight = asyncio.Event()
            rollback_done = asyncio.Event()

            async def hang_commit(req, timeout=None):
                commit_in_flight.set()
                await asyncio.sleep(10)

            async def slow_rollback(req, timeout=None):
                await asyncio.sleep(0.2)
                rollback_done.set()

            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=hang_commit),
                RollbackTransaction=AsyncMock(side_effect=slow_rollback),
            )

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")

            t = asyncio.create_task(run_block())
            await commit_in_flight.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            assert not rollback_done.is_set(), "context exit held for the inline rollback"
            await client.close()
            assert rollback_done.is_set(), "the detached cleanup never ran"
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())


class TestCancelledCloseStillClosesTheChannel:
    """Cancelling the task awaiting close() must not abandon shutdown midway:
    the finalization continues detached, so the drain finishes AND the channel
    is released, while the cancellation propagates to the caller."""

    def test_channel_is_closed_after_a_cancelled_close(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            stmt_in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                stmt_in_flight.set()
                await asyncio.sleep(10)

            async def slow_rollback(req, timeout=None):
                await asyncio.sleep(0.15)

            class _CountingChannel:
                def __init__(self) -> None:
                    self.close_calls = 0

                async def close(self) -> None:
                    self.close_calls += 1

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=hang),
                RollbackTransaction=AsyncMock(side_effect=slow_rollback),
            )
            channel = _CountingChannel()
            client._channel = channel
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await stmt_in_flight.wait()
            stmt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stmt

            closer = asyncio.create_task(client.close())
            await asyncio.sleep(0.03)  # closer is inside the drain, rollback still running
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            await asyncio.sleep(0.3)  # finalization continues detached
            assert channel.close_calls == 1, "cancelling close() abandoned the channel"
            assert client._closing is True

        asyncio.run(_inner())


class TestLateCancellationDuringChannelCloseStillRollsBack:
    """A statement cancelled while channel.close() is in progress must still
    get its cleanup: the transport is not conclusively gone yet, so forfeiting
    the rollback to the idle sweep at that point strands an accepted
    server-side transaction for no reason."""

    def test_cleanup_spawned_during_channel_close_runs(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            stmt_in_flight = asyncio.Event()
            release_close = asyncio.Event()

            async def hang(req, timeout=None):
                stmt_in_flight.set()
                await asyncio.sleep(10)

            class _BlockedChannel:
                async def close(self) -> None:
                    await release_close.wait()

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            client._channel = _BlockedChannel()
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await stmt_in_flight.wait()

            closer = asyncio.create_task(client.close())
            await asyncio.sleep(0.02)  # closer is awaiting channel.close()
            stmt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stmt
            await asyncio.sleep(0.05)  # the detached cleanup runs while close is blocked
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "a cancellation during channel close forfeited a reachable rollback"
            )
            release_close.set()
            await closer

        asyncio.run(_inner())


class TestReconnectWaitsForActiveShutdown:
    """connect() during a detached, still-running shutdown must serialize
    with it: the finalizer clears the channel and raises the closing gate
    when it resumes, and doing that to a freshly installed transport would
    disable cleanup on the new connection and leak its channel."""

    def test_connect_serializes_with_an_inflight_close(self):
        async def _inner() -> None:
            release_close = asyncio.Event()

            class _BlockedChannel:
                async def close(self) -> None:
                    await release_close.wait()

            client = _async_client()
            client._channel = _BlockedChannel()
            closer = asyncio.create_task(client.close())
            await asyncio.sleep(0.02)  # finalizer is awaiting channel.close()
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer

            reconnect = asyncio.create_task(client.connect())
            await asyncio.sleep(0.05)
            assert not reconnect.done(), "connect() replaced the transport under an active shutdown"
            release_close.set()
            await reconnect
            await asyncio.sleep(0.05)  # give a stale finalizer time to clobber, if any
            assert client._channel is not None, "the old finalizer cleared the new channel"
            assert client._closing is False, "the old finalizer disabled cleanup on the new connection"
            await client.close()

        asyncio.run(_inner())


class TestAbandonedInFlightStatementDoesNotReopen:
    """A block that starts a background statement and then raises leaves the
    scope while the operation is still in flight; when the straggler later
    completes, it must not return the handle to `open` — nobody is left to
    commit or roll it back, so its buffered writes and pinned snapshot would
    leak until the idle sweep."""

    def test_straggler_hands_the_transaction_to_cleanup(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()
            release = asyncio.Event()

            async def gated(req, timeout=None):
                in_flight.set()
                await release.wait()
                return _execute_response()

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=gated))
            bg = None
            with pytest.raises(ValueError):
                async with client.transaction() as tx:
                    bg = asyncio.create_task(tx.cypher("CREATE (:A)"))
                    await in_flight.wait()
                    raise ValueError("boom")
            release.set()
            await bg  # the statement completes after the owner is gone
            assert tx.is_open is False, "the straggler returned the abandoned handle to open"
            await client.close()
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "an abandoned transaction was left to the idle sweep"
            )

        asyncio.run(_inner())


class TestRollbackBeforeTheDetachedCleanupFirstTurn:
    """The cleanup-confirmed flag must fall when the detached task is
    SCHEDULED, not on its first step: an explicit rollback() racing in before
    that first turn otherwise sees the flag still up, sends nothing, and a
    loop shutdown can then cancel the pending task with nothing ever sent."""

    def test_explicit_rollback_in_the_prestart_window_sends(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await in_flight.wait()
            stmt.cancel()
            # One yield: the cancellation handler runs (spawning the detached
            # cleanup), but the detached task itself is still behind us in
            # the ready queue — the pre-start window.
            await asyncio.sleep(0)
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count >= 1, (
                "rollback() trusted a cleanup that had not started yet"
            )
            with pytest.raises(asyncio.CancelledError):
                await stmt
            await client.close()

        asyncio.run(_inner())


class TestCaughtIndeterminateCommitCleansUpOnNormalExit:
    """A manual commit() inside the block that fails ambiguously and is
    CAUGHT there routes the exit through the normal path; the indeterminate
    transaction must still get the bounded best-effort cleanup in case the
    commit never reached the server, with the verdict preserved."""

    def test_normal_exit_sends_cleanup_and_keeps_the_verdict(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED))
            )
            async with client.transaction() as tx:
                await tx.cypher("CREATE (:A)")
                with ctx_suppress(grpc.RpcError):
                    await tx.commit()
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "a caught ambiguous commit left the server transaction to the idle sweep"
            )
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())


class TestSyncCaughtIndeterminateCommitCleansUpOnNormalExit:
    """Sync mirror of the async normal-exit rule: a manual commit() that
    fails ambiguously and is caught inside the block leaves the transaction
    indeterminate; the context exit must still send the bounded best-effort
    cleanup in case the commit never reached the server."""

    def test_normal_exit_sends_cleanup_and_keeps_the_verdict(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        client = _sync_client(
            CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED))
        )
        with client.transaction() as tx:
            tx.cypher("CREATE (:A)")
            with ctx_suppress(grpc.RpcError):
                tx.commit()
        assert client._async._cypher_stub.RollbackTransaction.await_count == 1, (
            "a caught ambiguous commit left the server transaction to the idle sweep"
        )
        with pytest.raises(RuntimeError, match="outcome is unknown"):
            tx.cypher("RETURN 1")


class TestRepeatedRollbackRetriesUnconfirmedCleanup:
    """rollback() on an aborted handle must not settle into rolled_back while
    the cleanup remains unconfirmed: a retry that itself failed would
    otherwise report success and lock out every later retry, leaving the
    server transaction to the idle sweep even after connectivity recovers."""

    def test_state_settles_only_after_a_confirmed_cleanup(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            rollback = AsyncMock(
                side_effect=[
                    _TransportError(grpc.StatusCode.UNAVAILABLE),
                    _TransportError(grpc.StatusCode.UNAVAILABLE),
                    cypher_pb2.RollbackTransactionResponse(),
                ]
            )
            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang), RollbackTransaction=rollback)
            tx = await client.begin_transaction()
            stmt = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await in_flight.wait()
            stmt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stmt
            await asyncio.sleep(0.01)  # the detached cleanup runs and fails (1st call)
            await tx.rollback()  # the retry fails too (2nd call); must stay retriable
            await tx.rollback()  # this retry succeeds (3rd call)
            assert client._cypher_stub.RollbackTransaction.await_count == 3
            await client.close()

        asyncio.run(_inner())


class TestCancelledNormalExitCleanupPropagates:
    """Cancellation landing while the normal-exit indeterminate cleanup is
    awaiting the server must propagate, not be swallowed into a
    successful-looking exit — and the interrupted cleanup must be retried
    detached so the server transaction is still freed."""

    def test_cancellation_mid_cleanup_is_not_swallowed(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    rollback_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                RollbackTransaction=AsyncMock(side_effect=rollback),
            )

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    with ctx_suppress(grpc.RpcError):
                        await tx.commit()

            t = asyncio.create_task(run_block())
            await rollback_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry
            assert calls["n"] == 2, "the interrupted cleanup was never retried"

        asyncio.run(_inner())


class TestAbandonedInFlightCommitIsContested:
    """A block that starts commit() in a background task and then raises has
    asked for a rollback it can no longer perform itself: the exit must send
    the best-effort rollback to CONTEST the in-flight commit, so the server
    race decides the outcome instead of the commit silently applying despite
    the exception."""

    def test_exceptional_exit_sends_a_contesting_rollback(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            commit_in_flight = asyncio.Event()
            release_commit = asyncio.Event()

            async def gated_commit(req, timeout=None):
                commit_in_flight.set()
                await release_commit.wait()
                return cypher_pb2.CommitTransactionResponse(applied_index=7)

            client = _async_client(CommitTransaction=AsyncMock(side_effect=gated_commit))
            bg = None
            with pytest.raises(ValueError):
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    bg = asyncio.create_task(tx.commit())
                    await commit_in_flight.wait()
                    raise ValueError("boom")
            await asyncio.sleep(0.02)  # the detached contesting rollback runs
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "an abandoned in-flight commit was left uncontested"
            )
            release_commit.set()
            await bg  # the fake server lets the commit win; that is its call
            await client.close()

        asyncio.run(_inner())


class TestFailedExplicitRollbackStaysRetriable:
    """An explicit rollback() whose request is lost in transit must not leave
    the handle permanently rolled_back: the server may still hold the
    transaction, so once connectivity recovers a later rollback() has to be
    able to retry instead of being rejected as already finished."""

    def test_rollback_can_be_retried_after_a_transport_failure(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback = AsyncMock(
                side_effect=[
                    _TransportError(grpc.StatusCode.UNAVAILABLE),
                    cypher_pb2.RollbackTransactionResponse(),
                ]
            )
            client = _async_client(RollbackTransaction=rollback)
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.rollback()
            assert tx.is_open is False, "a failed rollback left the handle usable"
            await tx.rollback()  # connectivity recovered; the retry must go through
            assert client._cypher_stub.RollbackTransaction.await_count == 2
            await client.close()

        asyncio.run(_inner())


class TestSyncStatementInterruptedInsideTheTask:
    """KeyboardInterrupt raised while the statement coroutine itself is
    stepping completes the task before _run() can cancel-and-drain it, so no
    async handler closes the handle; the sync wrapper must then settle the
    outcome conservatively instead of leaving the handle parked in
    "executing" forever."""

    def test_interrupted_statement_closes_the_handle(self):
        from unittest.mock import AsyncMock

        async def ki(req, timeout=None):
            raise KeyboardInterrupt

        client = _sync_client(ExecuteCypher=AsyncMock(side_effect=ki))
        tx = client.begin_transaction()
        with pytest.raises(KeyboardInterrupt):
            tx.cypher("CREATE (:A)")
        assert tx._inner._state == "aborted", "the interruption left the handle parked in-flight"
        # The server may still hold the transaction; an explicit rollback
        # must send the cleanup rather than trusting one that never ran.
        tx.rollback()
        assert client._async._cypher_stub.RollbackTransaction.await_count == 1


class TestCancelledFailedCommitCleanupPropagates:
    """When the automatic commit fails ambiguously and cancellation then
    lands during the inline cleanup, the cancellation must propagate (a
    swallowed one is simply lost — asyncio does not re-inject it) and the
    interrupted cleanup must be retried detached."""

    def test_cancellation_mid_failed_commit_cleanup_is_not_swallowed(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    rollback_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                RollbackTransaction=AsyncMock(side_effect=rollback),
            )

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")

            t = asyncio.create_task(run_block())
            await rollback_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry
            assert calls["n"] == 2, "the interrupted cleanup was never retried"

        asyncio.run(_inner())


class TestCancelledManualRollbackGetsDetachedRetry:
    """A manual rollback() cancelled mid-RPC leaves the handle aborted with
    the cleanup unconfirmed; the context exit must register the detached
    retry, or closing the client strands the server-side transaction until
    the idle sweep."""

    def test_cancelled_rollback_in_context_spawns_cleanup(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    rollback_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(RollbackTransaction=AsyncMock(side_effect=rollback))

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    await tx.rollback()

            t = asyncio.create_task(run_block())
            await rollback_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry
            assert calls["n"] == 2, "a cancelled manual rollback got no detached retry"

        asyncio.run(_inner())


class TestSyncLocalEncodingFailureKeepsTheHandleUsable:
    """Sync mirror of the async encoding-failure contract: a parameter the
    encoder rejects fails locally before any RPC, so the handle must stay
    open — the interrupt settlement is for real interruptions only, not for
    ordinary local exceptions."""

    def test_a_bad_parameter_leaves_the_sync_transaction_open(self):
        client = _sync_client()
        tx = client.begin_transaction()
        tx.cypher("CREATE (:A)")
        with pytest.raises(Exception):
            tx.cypher("CREATE (:B {v: $v})", {"v": object()})
        assert client._async._cypher_stub.ExecuteCypher.await_count == 1, (
            "the failing statement must not have reached the wire"
        )
        assert tx.is_open is True, "a local encoding failure aborted a usable transaction"
        tx.rollback()
        assert client._async._cypher_stub.RollbackTransaction.await_count == 1


class TestCancelledExceptionalRollbackPropagates:
    """Cancellation landing while the exceptional-exit rollback awaits the
    server must propagate (not be traded for the block's earlier error) and
    the interrupted rollback must get its detached retry."""

    def test_cancellation_mid_exceptional_rollback_is_not_swallowed(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    rollback_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(RollbackTransaction=AsyncMock(side_effect=rollback))

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    raise ValueError("boom")

            t = asyncio.create_task(run_block())
            await rollback_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry
            assert calls["n"] == 2, "the interrupted exceptional rollback got no retry"

        asyncio.run(_inner())


class TestSyncContextRetriesSettledInterruptCleanup:
    """An in-task interruption settles the sync handle as aborted with the
    cleanup unconfirmed; the surrounding sync transaction context must then
    send the bounded best-effort rollback on exit instead of leaving the
    server transaction to the idle sweep."""

    def test_interrupted_statement_in_context_still_rolls_back(self):
        from unittest.mock import AsyncMock

        async def ki(req, timeout=None):
            raise KeyboardInterrupt

        client = _sync_client(ExecuteCypher=AsyncMock(side_effect=ki))
        # The scope stays inside pytest.raises because its exit is what is
        # under test: it must send the cleanup AND still let the interrupt
        # through. The await count below pins where the interrupt came from,
        # so a passing test cannot mean one raised by any other phase.
        with pytest.raises(KeyboardInterrupt):
            with client.transaction() as tx:
                tx.cypher("CREATE (:A)")
        assert client._async._cypher_stub.ExecuteCypher.await_count == 1
        assert tx._inner._state == "aborted"
        assert client._async._cypher_stub.RollbackTransaction.await_count >= 1, (
            "the interrupted sync transaction was left to the idle sweep"
        )


class TestNormalExitRetriesUnconfirmedCleanup:
    """A failed statement whose own cleanup also failed, with the error
    caught inside the block, reaches the normal exit as aborted with the
    cleanup unconfirmed; the exit must retry the bounded request instead of
    leaving the server transaction to the idle sweep."""

    def test_async_normal_exit_retries(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback = AsyncMock(
                side_effect=[
                    _TransportError(grpc.StatusCode.UNAVAILABLE),
                    cypher_pb2.RollbackTransactionResponse(),
                ]
            )
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_ServerRejected()),
                RollbackTransaction=rollback,
            )
            async with client.transaction() as tx:
                with ctx_suppress(grpc.RpcError):
                    await tx.cypher("CREATE (:A)")
            assert client._cypher_stub.RollbackTransaction.await_count == 2, (
                "the failed cleanup was never retried on the normal exit"
            )
            assert tx._cleanup_confirmed is True

        asyncio.run(_inner())

    def test_sync_normal_exit_retries(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        rollback = AsyncMock(
            side_effect=[
                _TransportError(grpc.StatusCode.UNAVAILABLE),
                cypher_pb2.RollbackTransactionResponse(),
            ]
        )
        client = _sync_client(
            ExecuteCypher=AsyncMock(side_effect=_ServerRejected()),
            RollbackTransaction=rollback,
        )
        with client.transaction() as tx:
            with ctx_suppress(grpc.RpcError):
                tx.cypher("CREATE (:A)")
        assert client._async._cypher_stub.RollbackTransaction.await_count == 2, (
            "the failed cleanup was never retried on the sync normal exit"
        )


class TestSkippedShutdownCleanupIsUnconfirmed:
    """The closing gate may skip the cleanup spawn, but the transaction can
    still have reached the server: the skipped cleanup must read as
    unconfirmed, so an explicit rollback() after a reconnect retries it
    instead of trusting a cleanup that never ran."""

    def test_gated_spawn_leaves_the_cleanup_unconfirmed(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            tx = await client.begin_transaction()
            task = asyncio.create_task(tx.cypher("CREATE (:A)"))
            await in_flight.wait()
            await client.close()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert tx._cleanup_confirmed is False, "a cleanup the closing gate skipped must not read as done"
            # After a reconnect, the explicit rollback must send the retry.
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 1

        asyncio.run(_inner())


class TestSyncInterruptUnwindUsesBoundedCleanup:
    """A sync transaction block unwound by Ctrl-C or SystemExit must not
    hold the exit for the full request deadline on a stalled server: the
    cleanup goes out with the bounded deadline, not the ordinary rollback
    timeout."""

    def test_interrupt_exit_sends_the_bounded_request(self):
        from unittest.mock import AsyncMock

        recorded = {}

        async def rollback(req, timeout=None):
            recorded["timeout"] = timeout
            return cypher_pb2.RollbackTransactionResponse()

        client = _sync_client(RollbackTransaction=AsyncMock(side_effect=rollback))
        with pytest.raises(KeyboardInterrupt):
            with client.transaction() as tx:
                tx.cypher("CREATE (:A)")
                raise KeyboardInterrupt
        assert tx.is_open is False
        assert recorded["timeout"] == 5.0, (
            "an interrupt unwind must use the bounded cleanup deadline, not the full rollback timeout"
        )


class TestDirectRollbackCancellationSpawnsCleanup:
    """A transaction rolled back directly (no context manager) and cancelled
    mid-RPC has no exit handler to retry for it, and the cancelled owner may
    never call again: rollback() itself must hand the cleanup to a detached
    task before propagating the cancellation."""

    def test_cancelled_direct_rollback_gets_detached_retry(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    rollback_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(RollbackTransaction=AsyncMock(side_effect=rollback))
            tx = await client.begin_transaction()
            t = asyncio.create_task(tx.rollback())
            await rollback_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry
            assert calls["n"] == 2, "a cancelled direct rollback got no detached retry"

        asyncio.run(_inner())


class TestExceptionalExitRetriesUnconfirmedCleanup:
    """A block that catches a failed statement (whose cleanup also failed)
    and raises a DIFFERENT error reaches the exceptional exit as aborted
    with the cleanup unconfirmed; the exit must retry the bounded request
    while preserving the block's own exception."""

    def test_exceptional_exit_retries_and_keeps_the_error(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback = AsyncMock(
                side_effect=[
                    _TransportError(grpc.StatusCode.UNAVAILABLE),
                    cypher_pb2.RollbackTransactionResponse(),
                ]
            )
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_ServerRejected()),
                RollbackTransaction=rollback,
            )
            with pytest.raises(RuntimeError, match="different"):
                async with client.transaction() as tx:
                    try:
                        await tx.cypher("CREATE (:A)")
                    except grpc.RpcError:
                        raise RuntimeError("different") from None
            assert client._cypher_stub.RollbackTransaction.await_count == 2, (
                "the failed cleanup was never retried on the exceptional exit"
            )

        asyncio.run(_inner())


class TestSyncCommitCleanupInterruptPropagates:
    """Ctrl-C arriving while the sync automatic-commit cleanup is running is
    the user's word: it must propagate instead of being swallowed into
    reporting the earlier commit error."""

    def test_interrupt_during_commit_cleanup_wins(self):
        from unittest.mock import AsyncMock

        client = _sync_client(
            CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
            RollbackTransaction=AsyncMock(side_effect=KeyboardInterrupt),
        )
        with pytest.raises(KeyboardInterrupt):
            with client.transaction() as tx:
                tx.cypher("CREATE (:A)")


class TestCancelledIndeterminateRollbackSpawnsRetry:
    """rollback() on an indeterminate handle whose best-effort request is
    cancelled mid-RPC must hand the cleanup to a detached retry before
    propagating: a direct caller has no context manager to do it, and the
    commit may never have reached the server."""

    def test_cancelled_indeterminate_rollback_gets_detached_retry(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    rollback_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                RollbackTransaction=AsyncMock(side_effect=rollback),
            )
            tx = await client.begin_transaction()
            with ctx_suppress(grpc.RpcError):
                await tx.commit()  # lands indeterminate
            t = asyncio.create_task(tx.rollback())
            await rollback_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry
            assert calls["n"] == 2, "a cancelled indeterminate rollback got no retry"

        asyncio.run(_inner())


class TestAsyncInterruptUnwindDetachesTheRollback:
    """An async block unwound by KeyboardInterrupt or SystemExit must not
    hold the exit for the full request deadline: the cleanup goes detached
    (bounded, drained at close) while the interrupt propagates."""

    def test_interrupt_exit_does_not_wait_for_the_rollback(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            rollback_done = asyncio.Event()

            async def slow_rollback(req, timeout=None):
                await asyncio.sleep(0.2)
                rollback_done.set()
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(RollbackTransaction=AsyncMock(side_effect=slow_rollback))

            async def run_block() -> None:
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    raise KeyboardInterrupt

            with pytest.raises(KeyboardInterrupt):
                await run_block()
            assert not rollback_done.is_set(), "the interrupt exit held for the inline rollback"
            await client.close()
            assert rollback_done.is_set(), "the detached cleanup never ran"

        asyncio.run(_inner())


class TestCancelledBeginReclaimsTheAllocation:
    """Cancellation landing after the server allocated the transaction but
    before the begin reply reaches the caller loses the handle entirely; the
    late reply must be collected detached and handed straight to a rollback,
    or the pinned snapshot survives until the idle sweep."""

    def test_cancelled_begin_rolls_back_the_late_allocation(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            begin_started = asyncio.Event()
            release_begin = asyncio.Event()

            async def gated_begin(req, timeout=None):
                begin_started.set()
                await release_begin.wait()
                return cypher_pb2.BeginTransactionResponse(transaction_id=42)

            client = _async_client(BeginTransaction=AsyncMock(side_effect=gated_begin))
            t = asyncio.create_task(client.begin_transaction())
            await begin_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            release_begin.set()
            await client.close()  # drains the reclaim task
            rollback = client._cypher_stub.RollbackTransaction
            assert rollback.await_count == 1, "the late begin reply was never reclaimed"
            assert rollback.await_args.args[0].transaction_id == 42

        asyncio.run(_inner())


class TestCancelledAbortedRetrySpawnsCleanup:
    """The aborted-branch retry in rollback(), cancelled mid-RPC, must hand
    the cleanup to a detached task like the indeterminate and open branches
    already do: a direct caller has no exit handler to retry for it."""

    def test_cancelled_aborted_retry_gets_detached_cleanup(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            retry_started = asyncio.Event()
            calls = {"n": 0}

            async def rollback(req, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise _TransportError(grpc.StatusCode.UNAVAILABLE)
                if calls["n"] == 2:
                    retry_started.set()
                    await asyncio.sleep(10)
                return cypher_pb2.RollbackTransactionResponse()

            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_ServerRejected()),
                RollbackTransaction=AsyncMock(side_effect=rollback),
            )
            tx = await client.begin_transaction()
            with ctx_suppress(grpc.RpcError):
                await tx.cypher("CREATE (:A)")  # aborts; its cleanup fails (1st call)
            t = asyncio.create_task(tx.rollback())  # aborted branch retries (2nd call)
            await retry_started.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached retry (3rd call)
            assert calls["n"] == 3, "a cancelled aborted-branch retry got no detached cleanup"

        asyncio.run(_inner())


class TestBeginWorksWithGrpcStyleAwaitables:
    """A real grpc.aio unary stub returns a UnaryUnaryCall — an AWAITABLE,
    not a coroutine object — and loop.create_task() accepts only coroutines.
    AsyncMock hides this by returning coroutines, so this test wires a
    call-like awaitable the way the real transport does."""

    def test_begin_accepts_a_non_coroutine_awaitable(self):
        from unittest.mock import MagicMock

        class _CallLike:
            def __init__(self, resp):
                self._resp = resp

            def __await__(self):
                async def _deliver():
                    return self._resp

                return _deliver().__await__()

        async def _inner() -> None:
            client = _async_client(
                BeginTransaction=MagicMock(
                    side_effect=lambda req, timeout=None: _CallLike(
                        cypher_pb2.BeginTransactionResponse(transaction_id=42)
                    )
                )
            )
            tx = await client.begin_transaction()
            assert tx.transaction_id == 42

        asyncio.run(_inner())


class TestAsyncStatementSettlesAfterProcessControlException:
    """KeyboardInterrupt or SystemExit raised from inside the awaited
    statement matches neither the gRPC nor the cancellation handler; the
    handle must still close conservatively (aborted, cleanup unconfirmed)
    instead of staying "executing" forever."""

    def test_interrupted_statement_closes_the_handle(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(ExecuteCypher=AsyncMock(side_effect=KeyboardInterrupt))
            tx = await client.begin_transaction()
            with pytest.raises(KeyboardInterrupt):
                await tx.cypher("CREATE (:A)")
            assert tx._state == "aborted", "the interruption left the handle parked in-flight"
            await tx.rollback()  # must be able to send the cleanup
            await client.close()
            assert client._cypher_stub.RollbackTransaction.await_count >= 1

        asyncio.run(_inner())


class TestAsyncCommitSettlesAfterProcessControlException:
    """KeyboardInterrupt or SystemExit raised from inside the awaited commit
    leaves the outcome unknowable — the request may already have applied —
    so the handle must settle as indeterminate, not stay "committing"."""

    def test_interrupted_commit_is_indeterminate(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(CommitTransaction=AsyncMock(side_effect=KeyboardInterrupt))
            tx = await client.begin_transaction()
            with pytest.raises(KeyboardInterrupt):
                await tx.commit()
            assert tx._state == "indeterminate"
            with pytest.raises(RuntimeError, match="outcome is unknown"):
                await tx.cypher("RETURN 1")

        asyncio.run(_inner())


class TestUnknownTransactionAnswerConfirmsCleanup:
    """The server answering a cleanup rollback with NOT_FOUND ("unknown
    transaction id") is a definitive statement that nothing is held: the
    cleanup must read as confirmed, so no redundant retries follow and an
    explicit rollback() can settle the handle."""

    def test_not_found_confirms_the_cleanup(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_ServerRejected()),
                RollbackTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.NOT_FOUND)),
            )
            tx = await client.begin_transaction()
            with ctx_suppress(grpc.RpcError):
                await tx.cypher("CREATE (:A)")  # aborts; cleanup answered NOT_FOUND
            assert tx._cleanup_confirmed is True, "a definitive unknown-id answer read as a lost request"
            await tx.rollback()
            assert tx._state == "rolled_back"
            assert client._cypher_stub.RollbackTransaction.await_count == 1, "redundant cleanup retry"

        asyncio.run(_inner())

    def test_not_found_settles_a_direct_rollback(self):
        """A transaction the idle sweep already reclaimed answers the
        caller's own rollback() with NOT_FOUND. Nothing is held and no
        commit was ever sent, so the discard the method promises is a fact,
        not a failure: it must return and settle the handle terminally,
        rather than leaving an "aborted" one a second call would retry."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                RollbackTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.NOT_FOUND)),
            )
            tx = await client.begin_transaction()
            await tx.rollback()
            assert tx._state == "rolled_back", "a settled discard reported as an unfinished one"
            assert tx._cleanup_confirmed is True
            assert client._cypher_stub.RollbackTransaction.await_count == 1
            # Terminal, so nothing is left for a later call to re-send.
            with pytest.raises(RuntimeError, match="already rolled back"):
                await tx.rollback()

        asyncio.run(_inner())


class TestCancelledBeginReclaimerIsBounded:
    """The reclaimer that collects a cancelled begin's late reply must be
    bounded by the cleanup deadline: close() drains it before releasing the
    transport, so an unbounded wait would stall shutdown for the whole
    request timeout on a stalled server."""

    def test_close_does_not_stall_on_a_hanging_begin(self):
        from unittest.mock import AsyncMock, patch

        async def _inner() -> None:
            started = asyncio.Event()

            async def hanging_begin(req, timeout=None):
                started.set()
                await asyncio.sleep(30)
                return cypher_pb2.BeginTransactionResponse(transaction_id=42)

            # Patched for the whole scope: the reclaimer reads the deadline
            # when it first runs, which is already during the cancellation.
            with patch("coordinode.client._CLEANUP_TIMEOUT_SECS", 0.1):
                client = _async_client(BeginTransaction=AsyncMock(side_effect=hanging_begin))
                t = asyncio.create_task(client.begin_transaction())
                await started.wait()
                t.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await t
                await asyncio.wait_for(client.close(), timeout=2)

        asyncio.run(_inner())


class TestInFlightRollbackBlocksContextExit:
    """A rollback started in a background task and still awaiting the server
    when the block exits must be caught by the in-flight guard: reporting a
    successful exit would let the RPC settle after the owning scope is gone,
    with a close or a late failure stranding the server transaction."""

    def test_exiting_with_a_rollback_in_flight_raises(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            in_flight = asyncio.Event()

            async def hang(req, timeout=None):
                in_flight.set()
                await asyncio.sleep(10)

            client = _async_client(RollbackTransaction=AsyncMock(side_effect=hang))
            bg = None
            with pytest.raises(RuntimeError, match="in flight"):
                async with client.transaction() as tx:
                    bg = asyncio.create_task(tx.rollback())
                    await in_flight.wait()
            bg.cancel()
            with ctx_suppress(asyncio.CancelledError):
                await bg

        asyncio.run(_inner())


class TestTransportCancelledCleanupKeepsTheStatementError:
    """A CancelledError raised by the TRANSPORT during the inline cleanup,
    with no cancellation pending on the task, must not replace the
    statement's own gRPC failure: only a real caller cancellation
    supersedes it."""

    def test_statement_error_survives_a_spurious_cleanup_cancellation(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_ServerRejected()),
                RollbackTransaction=AsyncMock(side_effect=asyncio.CancelledError()),
            )
            tx = await client.begin_transaction()
            with pytest.raises(_ServerRejected):
                await tx.cypher("CREATE (:A)")
            assert tx._state == "aborted"

        asyncio.run(_inner())


class TestTransportCancelledCommitCleanupKeepsTheError:
    """A CancelledError raised by the TRANSPORT while the automatic commit's
    cleanup runs, with no cancellation pending on the task, must not replace
    the commit's own gRPC failure — the same rule the statement path
    follows."""

    def test_commit_error_survives_a_spurious_cleanup_cancellation(self):
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                RollbackTransaction=AsyncMock(side_effect=asyncio.CancelledError()),
            )
            with pytest.raises(_TransportError):
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
            assert tx._state == "indeterminate"

        asyncio.run(_inner())


class TestTransportCancelledCleanupKeepsANormalExit:
    """The same rule on the NORMAL exit: a manual commit caught inside the
    block leaves the handle indeterminate, and a transport-raised
    CancelledError during the exit's cleanup must not turn a successful
    block into a cancelled one."""

    def test_normal_exit_survives_a_spurious_cleanup_cancellation(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                RollbackTransaction=AsyncMock(side_effect=asyncio.CancelledError()),
            )
            async with client.transaction() as tx:
                await tx.cypher("CREATE (:A)")
                with ctx_suppress(grpc.RpcError):
                    await tx.commit()
            assert tx._state == "indeterminate"

        asyncio.run(_inner())


class TestQueuedStatementIsSeenBeforeAutoCommit:
    """A statement queued with create_task and never awaited has not reached
    its state reservation when the block exits; auto-committing then would
    report success while that write is silently dropped. The exit must give
    queued work its first step and see the in-flight operation."""

    def test_queued_statement_blocks_the_auto_commit(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            # The statement must actually reach the wire and wait there, the
            # way a real RPC does: a mock that returns without suspending
            # would finish the whole statement within that first step, and
            # there would be nothing in flight to catch.
            async def hang(req, timeout=None):
                await asyncio.sleep(10)

            client = _async_client(ExecuteCypher=AsyncMock(side_effect=hang))
            bg = None
            with pytest.raises(RuntimeError, match="in flight"):
                async with client.transaction() as tx:
                    bg = asyncio.create_task(tx.cypher("CREATE (:A)"))
            assert client._cypher_stub.CommitTransaction.await_count == 0, (
                "the block auto-committed while a queued statement was pending"
            )
            bg.cancel()
            with ctx_suppress(asyncio.CancelledError):
                await bg

        asyncio.run(_inner())


class TestCancellationAtTheSchedulingYieldCleansUp:
    """Cancellation can land while the exit's scheduling yield is suspended
    — after the block returned, before anything was decided. That path
    leaves the try suite, so it must settle the handle itself instead of
    walking away from an open transaction."""

    def test_cancellation_during_the_yield_still_cleans_up(self):
        async def _inner() -> None:
            body_done = asyncio.Event()
            holder: dict[str, object] = {}

            client = _async_client()

            async def run_block() -> None:
                async with client.transaction() as tx:
                    holder["tx"] = tx
                    # Event.set() schedules this waiter via call_soon, so the
                    # test resumes BEFORE the exit's own sleep(0)
                    # continuation: the cancel below lands exactly on that
                    # suspended yield.
                    body_done.set()

            t = asyncio.create_task(run_block())
            await body_done.wait()
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            await client.close()  # drains the detached cleanup
            assert client._cypher_stub.RollbackTransaction.await_count == 1, (
                "a transaction cancelled at the scheduling yield was left open"
            )

        asyncio.run(_inner())


class TestTransportCancelledCleanupKeepsTheBlockError:
    """The exceptional exit's indeterminate cleanup follows the same rule as
    every other cleanup: a CancelledError the transport raised, with no
    cancellation pending, must not replace the block's own exception."""

    def test_block_error_survives_a_spurious_cleanup_cancellation(self):
        from contextlib import suppress as ctx_suppress
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                CommitTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                RollbackTransaction=AsyncMock(side_effect=asyncio.CancelledError()),
            )
            with pytest.raises(RuntimeError, match="boom"):
                async with client.transaction() as tx:
                    await tx.cypher("CREATE (:A)")
                    with ctx_suppress(grpc.RpcError):
                        await tx.commit()
                    raise RuntimeError("boom")
            assert tx._state == "indeterminate"

        asyncio.run(_inner())
