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

    def test_no_rollback_is_sent_for_a_transaction_the_server_already_dropped(self):
        """Sending one would answer "unknown transaction id" and say nothing useful."""

        async def _inner() -> None:
            client = self._client_whose_statement_fails()
            with pytest.raises(_ServerRejected):
                async with client.transaction() as tx:
                    await tx.cypher("RETURN nonsense(")
            assert client._cypher_stub.RollbackTransaction.await_count == 0
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

    def test_rollback_after_an_aborted_statement_succeeds_without_a_call(self):
        """Its contract is met: the writes are already discarded."""

        async def _inner() -> None:
            client = self._client_whose_statement_fails()
            tx = await client.begin_transaction()
            with pytest.raises(_ServerRejected):
                await tx.cypher("RETURN nonsense(")
            await tx.rollback()
            assert client._cypher_stub.RollbackTransaction.await_count == 0
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

    def test_an_answered_rejection_sends_no_rollback(self):
        """INVALID_ARGUMENT is the server speaking: it processed the statement,
        discarded the transaction and consumed the handle. A rollback after
        that could only be answered "unknown transaction id"."""
        from unittest.mock import AsyncMock

        async def _inner() -> None:
            client = _async_client(
                ExecuteCypher=AsyncMock(side_effect=_TransportError(grpc.StatusCode.INVALID_ARGUMENT))
            )
            tx = await client.begin_transaction()
            with pytest.raises(_TransportError):
                await tx.cypher("RETURN (")
            assert client._cypher_stub.RollbackTransaction.await_count == 0

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
