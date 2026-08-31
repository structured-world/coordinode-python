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
    """A gRPC failure with a status code, like the real client raises."""

    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


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
        handle must not stay usable."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                RollbackTransaction=AsyncMock(side_effect=_TransportError(grpc.StatusCode.UNAVAILABLE))
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.rollback()
            assert tx.is_open is False
            with pytest.raises(RuntimeError, match="already rolled back"):
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
