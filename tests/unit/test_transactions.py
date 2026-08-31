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
    """Stand-in for a gRPC failure, which is what the server sends on a rejection."""


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
