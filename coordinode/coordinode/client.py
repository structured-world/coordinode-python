"""
CoordinodeClient: synchronous and asynchronous gRPC client for CoordiNode.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from typing import Any

import grpc
import grpc.aio

from coordinode._types import (
    PyValue,
    dict_to_props,
    from_property_value,
    props_to_dict,
)

logger = logging.getLogger(__name__)

# Matches "host:port" strings where host is either a bracketed IPv6 address
# ([::1], [2001:db8::1]) or a name/IPv4 with no colons.  Unbracketed IPv6
# addresses (e.g. "2001:db8::1") are intentionally NOT matched — they cannot
# be reliably distinguished from a "host:port" pair.
_HOST_PORT_RE = re.compile(r"^(\[.+\]|[^:]+):(\d+)$")

# Cypher identifier: must start with a letter or underscore, followed by
# letters, digits, or underscores.  Validated before interpolating user-supplied
# names/labels/properties into DDL strings to surface clear errors early.
_CYPHER_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# gRPC codes that do NOT prove the server processed the request: the request or
# its reply was lost somewhere in transit. Every other code is the server
# answering, and an answered failure consumes the transaction's handle.
#
# This is consulted for the COMMIT only, where the question is "were the writes
# applied", which no follow-up request can answer. Statement failures do not
# consult it: there the only question is whether server-side state needs
# freeing, and asking for that is harmless whatever the answer, so the
# statement path cleans up unconditionally rather than risk misjudging a code.
_AMBIGUOUS_RPC_CODES = frozenset(
    {
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.CANCELLED,
        grpc.StatusCode.UNKNOWN,
    }
)


def _rpc_outcome_is_ambiguous(exc: grpc.RpcError) -> bool:
    """Whether this failure leaves the server's state unknowable.

    A code outside the ambiguous set is an answer, so the commit's fate is
    decided. An error that cannot even report a code proves nothing either way
    and is read as ambiguous, because the two mistakes are not symmetric:
    warning about an outcome that turned out to be a clean rejection costs the
    caller one verification, while a wrongly claimed abort invites a retry that
    duplicates every write.
    """
    try:
        return exc.code() in _AMBIGUOUS_RPC_CODES
    except Exception:
        return True


def _validate_cypher_identifier(value: str, param_name: str) -> None:
    """Raise :exc:`ValueError` if *value* is not a valid Cypher identifier."""
    if not isinstance(value, str) or not _CYPHER_IDENT_RE.fullmatch(value):
        raise ValueError(
            f"{param_name} must be a valid Cypher identifier (letters, digits, underscores, "
            f"starting with a letter or underscore); got {value!r}"
        )


# ── Low-level helpers ────────────────────────────────────────────────────────


def _make_channel(host: str, port: int, tls: bool) -> grpc.Channel:
    target = f"{host}:{port}"
    if tls:
        return grpc.secure_channel(target, grpc.ssl_channel_credentials())
    return grpc.insecure_channel(target)


def _make_async_channel(host: str, port: int, tls: bool) -> grpc.aio.Channel:
    target = f"{host}:{port}"
    if tls:
        return grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())
    return grpc.aio.insecure_channel(target)


# ── Result types ─────────────────────────────────────────────────────────────


class NodeResult:
    """A node returned from a graph operation."""

    def __init__(self, proto_node: Any) -> None:
        self.id: int = proto_node.node_id
        #: Canonical opaque identifier, stable across schema changes, restarts
        #: and replication. Prefer this over :attr:`id` when referring to a node
        #: from application code; `id` stays for Neo4j v4 driver compatibility.
        self.element_id: str = proto_node.element_id
        self.labels: list[str] = list(proto_node.labels)
        self.properties: dict[str, PyValue] = props_to_dict(proto_node.properties)

    def __repr__(self) -> str:
        return f"Node(id={self.id}, element_id={self.element_id!r}, labels={self.labels}, properties={self.properties})"


class EdgeResult:
    """An edge returned from a graph operation."""

    def __init__(self, proto_edge: Any) -> None:
        self.id: int = proto_edge.edge_id
        self.type: str = proto_edge.edge_type
        self.source_id: int = proto_edge.source_node_id
        self.target_id: int = proto_edge.target_node_id
        #: Endpoint identifier, the source and target element ids in canonical
        #: order. Edges here are typed property bags between two nodes rather
        #: than first-class entities, so this is not a stable handle to one edge.
        self.element_id: str = proto_edge.element_id
        self.properties: dict[str, PyValue] = props_to_dict(proto_edge.properties)

    def __repr__(self) -> str:
        return f"Edge(id={self.id}, type={self.type!r}, {self.source_id}→{self.target_id})"


class VectorResult:
    """A vector search result."""

    def __init__(self, proto_result: Any) -> None:
        self.node = NodeResult(proto_result.node)
        self.distance: float = proto_result.distance

    def __repr__(self) -> str:
        return f"VectorResult(distance={self.distance:.4f}, node={self.node})"


class TextResult:
    """A single full-text search result with BM25 score and optional snippet."""

    def __init__(self, proto_result: Any) -> None:
        self.node_id: int = proto_result.node_id
        self.score: float = proto_result.score
        # HTML snippet with <b>…</b> highlights. Empty when unavailable.
        self.snippet: str = proto_result.snippet

    def __repr__(self) -> str:
        return f"TextResult(node_id={self.node_id}, score={self.score:.4f}, snippet={self.snippet!r})"


class PropertyDefinitionInfo:
    """A property definition from the schema (name, type, required, unique)."""

    def __init__(self, proto_def: Any) -> None:
        self.name: str = proto_def.name
        self.type: int = proto_def.type
        self.required: bool = proto_def.required
        self.unique: bool = proto_def.unique

    def __repr__(self) -> str:
        return f"PropertyDefinitionInfo(name={self.name!r}, type={self.type}, required={self.required}, unique={self.unique})"


class LabelInfo:
    """A node label returned from the schema registry."""

    def __init__(self, proto_label: Any) -> None:
        self.name: str = proto_label.name
        #: DDL snapshot identity, bumped by every schema change to this label.
        self.schema_revision: int = proto_label.schema_revision
        self.properties: list[PropertyDefinitionInfo] = [PropertyDefinitionInfo(p) for p in proto_label.properties]
        # schema_mode: 0=unspecified, 1=strict, 2=validated, 3=flexible
        self.schema_mode: int = proto_label.schema_mode

    def __repr__(self) -> str:
        return (
            f"LabelInfo(name={self.name!r}, schema_revision={self.schema_revision}, "
            f"properties={len(self.properties)}, schema_mode={self.schema_mode})"
        )


class EdgeTypeInfo:
    """An edge type returned from the schema registry."""

    def __init__(self, proto_edge_type: Any) -> None:
        self.name: str = proto_edge_type.name
        #: DDL snapshot identity, bumped by every schema change to this edge type.
        self.schema_revision: int = proto_edge_type.schema_revision
        self.properties: list[PropertyDefinitionInfo] = [PropertyDefinitionInfo(p) for p in proto_edge_type.properties]

    def __repr__(self) -> str:
        return (
            f"EdgeTypeInfo(name={self.name!r}, schema_revision={self.schema_revision}, "
            f"properties={len(self.properties)})"
        )


class TraverseResult:
    """Result of a graph traversal: reached nodes and traversed edges."""

    def __init__(self, proto_response: Any) -> None:
        self.nodes: list[NodeResult] = [NodeResult(n) for n in proto_response.nodes]
        self.edges: list[EdgeResult] = [EdgeResult(e) for e in proto_response.edges]

    def __repr__(self) -> str:
        return f"TraverseResult(nodes={len(self.nodes)}, edges={len(self.edges)})"


class TextIndexInfo:
    """Information about a full-text index returned by :meth:`create_text_index`."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.name: str = str(row.get("index", ""))
        self.label: str = str(row.get("label", ""))
        self.properties: str = str(row.get("properties", ""))
        self.default_language: str = str(row.get("default_language", ""))
        self.documents_indexed: int = int(row.get("documents_indexed", 0))

    def __repr__(self) -> str:
        return (
            f"TextIndexInfo(name={self.name!r}, label={self.label!r},"
            f" properties={self.properties!r}, documents_indexed={self.documents_indexed})"
        )


# ── Async client ─────────────────────────────────────────────────────────────

# Detached cleanup tasks spawned from cancellation handlers, referenced here
# until done so the event loop cannot garbage-collect them mid-flight.
_PENDING_CLEANUPS: set[asyncio.Task[None]] = set()


class AsyncTransaction:
    """One interactive transaction, held open across several statements.

    Obtained from :meth:`AsyncCoordinodeClient.begin_transaction`, or from
    :meth:`AsyncCoordinodeClient.transaction`, which commits on a clean exit and
    rolls back on an exception.

    Every statement reads the snapshot pinned when the transaction began, and
    its writes buffer on the server until :meth:`commit`. So the transaction
    sees a stable view of the database plus its own writes, and a conflict with
    another transaction that touched the same data surfaces at the commit rather
    than at the statement.

    Two properties of the server matter before holding one open. The handle
    lives in the memory of the node that served the begin, so every request of
    the transaction has to reach that same node: connect to a node's own
    address, or through a balancer configured for backend affinity. A balancer
    that routes each request independently breaks this even through a single
    client, since a reconnection can land on another backend mid-transaction,
    and the next statement then fails with an unknown transaction id. And an
    idle transaction is reaped, after 30 seconds by default, swept when some
    other transaction begins rather than on a timer, so a long pause between
    statements can lose the handle without a wall-clock guarantee of when.
    """

    def __init__(self, client: AsyncCoordinodeClient, transaction_id: int) -> None:
        self._client = client
        self._id = transaction_id
        # open -> committed | rolled_back | aborted | indeterminate.
        # "aborted" is the server having closed the transaction under us, which
        # it does on any statement error and on a rejected commit.
        # "indeterminate" is a commit whose reply was lost in transit: the
        # writes may all be applied or none may be, and nothing on the client
        # can tell which.
        self._state = "open"

    def __repr__(self) -> str:
        return f"AsyncTransaction(id={self._id}, state={self._state})"

    @property
    def transaction_id(self) -> int:
        """Server-side handle for this transaction. Non-zero while it exists."""
        return self._id

    @property
    def is_open(self) -> bool:
        """True while the transaction can still take statements and be committed."""
        return self._state == "open"

    def _require_open(self, action: str) -> None:
        if self._state == "open":
            return
        if self._state == "aborted":
            raise RuntimeError(
                f"Cannot {action} this transaction: an earlier failure closed it on the "
                "server, which discards its buffered writes. Nothing was applied; begin "
                "a new transaction to retry."
            )
        if self._state == "indeterminate":
            raise RuntimeError(
                f"Cannot {action} this transaction: the commit's reply was lost and the "
                "outcome is unknown. The writes may or may not be applied; verify the "
                "data before retrying, since a blind retry can duplicate them."
            )
        raise RuntimeError(f"Cannot {action} this transaction: it was already {self._state.replace('_', ' ')}.")

    def _spawn_cleanup(self) -> None:
        """Run :meth:`_best_effort_rollback` as a detached task.

        Used from cancellation handlers, where awaiting the rollback in place
        would only be cancelled again. The task keeps itself referenced until
        done (an unreferenced task can be garbage-collected mid-flight); if
        the event loop closes before it runs, the server's idle sweep remains
        the backstop, which is exactly what best-effort means.
        """
        task = asyncio.get_running_loop().create_task(self._best_effort_rollback())
        _PENDING_CLEANUPS.add(task)
        task.add_done_callback(_PENDING_CLEANUPS.discard)

    async def _best_effort_rollback(self) -> None:
        """Ask the server to drop the transaction, ignoring every failure.

        Used where the rollback is cleanup rather than the caller's request: a
        transaction that may or may not still exist server-side. When it does
        exist this frees its buffered writes now instead of at the idle sweep;
        when it does not, the server answers "unknown transaction id" and there
        was nothing to free. Neither answer changes what the caller is told.
        """
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            RollbackTransactionRequest,
        )

        with suppress(Exception):
            await self._client._cypher_stub.RollbackTransaction(
                RollbackTransactionRequest(transaction_id=self._id),
                timeout=self._client._timeout,
            )

    async def cypher(
        self,
        query: str,
        params: dict[str, PyValue] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one statement inside this transaction and return its rows.

        The write is buffered rather than applied, so it is visible to later
        statements of this transaction and to nobody else until :meth:`commit`.

        This deliberately takes no consistency arguments, unlike
        :meth:`AsyncCoordinodeClient.cypher`. Read concern, write concern, read
        preference and ``after_index`` describe a single self-contained
        statement; here the snapshot was already fixed at the begin and
        durability is decided once at the commit, so the server ignores them.
        Accepting them would only let a caller believe otherwise.

        A statement that fails on the server ends the transaction: the buffered
        writes are discarded and the handle is consumed. The failure propagates
        as-is, and any later use of this object raises instead of reporting the
        server's "unknown transaction id".
        """
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            ExecuteCypherRequest,
        )

        self._require_open("run a statement in")
        req = ExecuteCypherRequest(
            query=query,
            parameters=dict_to_props(params or {}),
            transaction_id=self._id,
        )
        try:
            resp = await self._client._cypher_stub.ExecuteCypher(req, timeout=self._client._timeout)
        except grpc.RpcError:
            # Always attempt the cleanup, without classifying the failure.
            # Whether the server processed the statement decides only whether
            # the rollback finds anything: if it aborted the transaction the
            # request is answered "unknown transaction id" and swallowed, and
            # if it kept the transaction open (a lost request, or a limit the
            # CLIENT hit while receiving an oversized reply) the buffered
            # writes are freed now instead of at the idle sweep.
            #
            # Classifying here was a way to miss cases: every code judged
            # "answered" that the server did not actually act on leaks a
            # transaction for the idle timeout. The cost of not classifying is
            # one wasted RPC on a path that is already failing.
            self._state = "aborted"
            await self._best_effort_rollback()
            raise
        except asyncio.CancelledError:
            # Cancellation is a BaseException, so the handler above never sees
            # it. No commit was sent, so nothing of this transaction can apply;
            # the handle is closed rather than left open for reuse. The
            # cancellation may still have arrived AFTER the server accepted
            # the statement, leaving the transaction alive there with its
            # buffered writes and a pinned snapshot — and with the handle
            # closed, nothing later can free it before the idle sweep. The
            # cleanup therefore runs as its own task: awaiting it here would
            # only be cancelled again, while a detached task survives this
            # task's cancellation. (A commit is different — see commit().)
            self._state = "aborted"
            self._spawn_cleanup()
            raise
        return _rows_to_dicts(resp)

    async def commit(self) -> int:
        """Apply every buffered write as one unit.

        Returns the Raft applied index of the commit, which a later read can
        pass as ``after_index`` (with ``read_concern="majority"``) when it must
        observe these writes.

        Raises if another transaction has written the same data since this one
        began: conflicts are detected here, not at the statement. A rejected
        commit applies nothing and closes the transaction.

        One failure is different from the rest: a commit whose reply is lost in
        transit (a deadline, an unavailable channel). The server may have
        applied everything before the failure, or never received the request,
        and nothing on the client can tell which. The transaction is then
        marked indeterminate rather than aborted, and every later call on it
        says so: retrying such a commit blindly can duplicate the writes, so
        the data has to be verified first.
        """
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            CommitTransactionRequest,
        )

        self._require_open("commit")
        try:
            resp = await self._client._cypher_stub.CommitTransaction(
                CommitTransactionRequest(transaction_id=self._id), timeout=self._client._timeout
            )
        except grpc.RpcError as exc:
            if _rpc_outcome_is_ambiguous(exc):
                self._state = "indeterminate"
            else:
                # An answered rejection (a write conflict, most commonly): the
                # server consumed the handle and applied nothing, so a
                # follow-up rollback would find nothing to discard.
                self._state = "aborted"
            raise
        except asyncio.CancelledError:
            # Cancellation is a BaseException, so the gRPC handler above never
            # sees it, and a deadline enforced by `asyncio.timeout()` arrives
            # this way rather than as DEADLINE_EXCEEDED. The request may have
            # reached the server and applied everything, which is exactly the
            # case the indeterminate state exists for: leaving the transaction
            # open here would invite the retry that duplicates the writes.
            self._state = "indeterminate"
            raise
        self._state = "committed"
        return int(resp.applied_index)

    async def rollback(self) -> None:
        """Discard every buffered write. Nothing reaches the database.

        After a commit whose reply was lost, that promise cannot be made: the
        writes may already be applied. This then sends the rollback anyway, in
        case the commit never arrived, and still raises, so nobody walks away
        believing the discard is certain.
        """
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            RollbackTransactionRequest,
        )

        if self._state == "indeterminate":
            # If the commit never reached the server this frees the
            # transaction; if it was applied, nothing can un-apply it.
            await self._best_effort_rollback()
            raise RuntimeError(
                "Cannot promise a rollback: the commit's reply was lost, so its writes "
                "may already be applied. A rollback request was sent in case the commit "
                "never arrived, but verify the data rather than assuming either outcome."
            )
        if self._state == "aborted":
            # The failure that closed the transaction already discarded the
            # writes, so this call's contract is met. Asking the server would
            # only get "unknown transaction id" for a transaction that is
            # correctly gone.
            self._state = "rolled_back"
            return
        self._require_open("roll back")
        # Terminal before the call, not after it. If the request is lost the
        # server may hold the transaction until the idle sweep, but no commit
        # was ever sent, so nothing of it can apply and the discard this method
        # promises still holds. What must not happen is the handle staying
        # usable: a caller who asked to discard should not be able to add
        # another statement, or commit, because their rollback did not land.
        # The failure still propagates, so they know the request did not
        # arrive.
        self._state = "rolled_back"
        await self._client._cypher_stub.RollbackTransaction(
            RollbackTransactionRequest(transaction_id=self._id), timeout=self._client._timeout
        )


class AsyncCoordinodeClient:
    """
    Async gRPC client for CoordiNode.

    Usage::

        async with AsyncCoordinodeClient("localhost:7080") as client:
            rows = await client.cypher("MATCH (n:Person) RETURN n.name LIMIT 5")

        # Also accepts separate host and port:
        async with AsyncCoordinodeClient("localhost", port=7080) as client:
            ...
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int | None = None,
        *,
        tls: bool = False,
        timeout: float = 30.0,
    ) -> None:
        # Support "host:port" as a single string (common gRPC convention).
        # _HOST_PORT_RE matches "hostname:port" and "[IPv6]:port" but not bare
        # IPv6 addresses, avoiding the ambiguity of rsplit(":", 1) on "::1".
        # port=None means "not specified by caller" — distinct from explicit port=7080.
        m = _HOST_PORT_RE.match(host)
        if m:
            parsed_port = int(m.group(2))
            if port is not None and port != parsed_port:
                raise ValueError(
                    f"Conflicting ports: port={port!r} (argument) vs {parsed_port!r} "
                    f"(embedded in host={host!r}). Specify the port in the host string "
                    "only, or use the port argument only."
                )
            host, port = m.group(1), parsed_port
        if port is None:
            port = 7080
        self._host = host
        self._port = port
        self._tls = tls
        self._timeout = timeout
        self._channel: grpc.aio.Channel | None = None

    async def __aenter__(self) -> AsyncCoordinodeClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        self._channel = _make_async_channel(self._host, self._port, self._tls)
        self._cypher_stub = _cypher_stub(self._channel)
        self._vector_stub = _vector_stub(self._channel)
        self._text_stub = _text_stub(self._channel)
        self._graph_stub = _graph_stub(self._channel)
        self._schema_stub = _schema_stub(self._channel)
        self._health_stub = _health_stub(self._channel)

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None

    async def cypher(
        self,
        query: str,
        params: dict[str, PyValue] | None = None,
        *,
        read_concern: str | None = None,
        write_concern: str | None = None,
        read_preference: str | None = None,
        after_index: int | None = None,
        at_timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an OpenCypher query. Returns rows as list of dicts.

        Consistency parameters (all optional; server defaults apply when omitted):

        - ``read_concern``: ``"local"`` (default), ``"majority"``, ``"linearizable"``, ``"snapshot"``.
          Causal reads (``after_index`` > 0) require ``"majority"`` here.
        - ``write_concern``: ``"w0"``, ``"memory"``, ``"cache"``, ``"w1"`` (default, leader-ack),
          ``"majority"``, in rising order of durability. ``"memory"`` and ``"cache"`` acknowledge
          before the write reaches Raft, so a leader crash before the background drain loses them;
          reach for those only where losing recent writes is acceptable.
        - ``read_preference``: ``"primary"`` (default), ``"primary_preferred"``, ``"secondary"``,
          ``"secondary_preferred"``, ``"nearest"``.
        - ``after_index``: raft log index for causal reads, a fence. Returned rows reflect at
          least the state at this index. Needs ``read_concern="majority"``: the fence is about
          which replica may answer, so it is the read's concern that has to be raised, not the
          write's.
        - ``at_timestamp``: timestamp to read at, a pin rather than a fence. Reads the
          database exactly as of that version without waiting, for time travel. Microseconds
          since the Unix epoch, so ``int(time.time() * 1_000_000)`` is now. Requires
          ``read_concern="snapshot"``; any other level is rejected with FAILED_PRECONDITION.
          The timestamp has to fall inside the MVCC retention window; older snapshots are
          collected and the server answers UNAVAILABLE. It cannot be combined with a
          non-zero ``after_index``: waiting for a new write and reading a fixed past are
          opposite requests, and the pair is rejected. Zero is rejected too: it is how the
          wire says "no pin", so it cannot also ask for one.
        """
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            ExecuteCypherRequest,
        )

        # Validate after_index type/range BEFORE any numeric comparison so that
        # True (bool is a subclass of int) and "7" (str) produce a clear
        # "must be a non-negative integer" error instead of a misleading
        # causal-read violation or a raw TypeError.
        if after_index is not None and (
            not isinstance(after_index, int) or isinstance(after_index, bool) or after_index < 0
        ):
            raise ValueError(f"after_index must be a non-negative integer, got {after_index!r}")
        # Causal reads (after_index > 0) need a majority READ concern: the
        # server refuses the pair otherwise, with "readConcern=LOCAL is
        # incompatible with afterClusterTime". The concern that matters is the
        # read's, not the write's, because the fence is about which replicas
        # may answer, not about how the referenced write was acknowledged.
        if after_index is not None and after_index > 0 and (read_concern or "").strip().lower() != "majority":
            raise ValueError(
                "after_index > 0 requires read_concern='majority': a causal read has to be "
                "answered by a majority-acknowledged replica, or the referenced index may "
                "not be there yet. Pass read_concern='majority'."
            )
        req = ExecuteCypherRequest(
            query=query,
            parameters=dict_to_props(params or {}),
        )
        if read_concern is not None or after_index is not None or at_timestamp is not None:
            req.read_concern.CopyFrom(_make_read_concern(read_concern, after_index, at_timestamp))
        if write_concern is not None:
            req.write_concern.CopyFrom(_make_write_concern(write_concern))
        if read_preference is not None:
            req.read_preference = _make_read_preference(read_preference)
        resp = await self._cypher_stub.ExecuteCypher(req, timeout=self._timeout)
        return _rows_to_dicts(resp)

    async def begin_transaction(self) -> AsyncTransaction:
        """Open an interactive transaction and return its handle.

        Prefer :meth:`transaction`, which cannot leave one open. Reach for this
        when the commit point is decided somewhere the ``async with`` block
        cannot follow.

        The caller owns the outcome: a transaction left neither committed nor
        rolled back holds its buffered writes on the server until the idle
        sweep collects it.
        """
        from coordinode._proto.coordinode.v1.query.cypher_pb2 import (  # type: ignore[import]
            BeginTransactionRequest,
        )

        resp = await self._cypher_stub.BeginTransaction(BeginTransactionRequest(), timeout=self._timeout)
        if resp.transaction_id == 0:
            # Zero is what ExecuteCypherRequest uses for "no transaction", so a
            # zero handle would silently turn every statement of this
            # transaction into its own auto-committed write. The protocol
            # promises a non-zero id here; refuse a server that breaks it.
            raise RuntimeError(
                "server answered BeginTransaction with transaction_id=0, which the wire "
                "reserves for auto-commit; refusing to run statements outside a transaction"
            )
        return AsyncTransaction(self, resp.transaction_id)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncTransaction]:
        """Run a block of statements as one transaction.

        Commits when the block finishes, rolls back when it raises::

            async with client.transaction() as tx:
                await tx.cypher("CREATE (:Person {name: $n})", {"n": "Alice"})
                await tx.cypher("CREATE (:Person {name: $n})", {"n": "Bob"})

        Committing or rolling back inside the block is allowed; this then leaves
        the finished transaction alone rather than committing it twice.

        An exception from the block propagates unchanged. A rollback that itself
        fails on the way out is swallowed, since the failure being reported is
        the one the caller needs and the server drops an unresolved transaction
        on its own.
        """
        tx = await self.begin_transaction()
        try:
            yield tx
        except BaseException:
            if tx.is_open:
                with suppress(Exception):
                    await tx.rollback()
            raise
        else:
            if tx.is_open:
                await tx.commit()

    async def vector_search(
        self,
        label: str,
        property: str,
        vector: Sequence[float],
        top_k: int = 10,
        metric: str = "cosine",
    ) -> list[VectorResult]:
        """Nearest-neighbour search on a labelled property."""
        from coordinode._proto.coordinode.v1.common.types_pb2 import Vector  # type: ignore[import]
        from coordinode._proto.coordinode.v1.query.vector_pb2 import (  # type: ignore[import]
            DistanceMetric,
            VectorSearchRequest,
        )

        metric_map = {
            "cosine": DistanceMetric.DISTANCE_METRIC_COSINE,
            "l2": DistanceMetric.DISTANCE_METRIC_L2,
            "dot": DistanceMetric.DISTANCE_METRIC_DOT,
            "l1": DistanceMetric.DISTANCE_METRIC_L1,
        }
        req = VectorSearchRequest(
            label=label,
            property=property,
            query_vector=Vector(values=[float(v) for v in vector]),
            top_k=top_k,
            metric=metric_map.get(metric.lower(), DistanceMetric.DISTANCE_METRIC_COSINE),
        )
        resp = await self._vector_stub.VectorSearch(req, timeout=self._timeout)
        return [VectorResult(r) for r in resp.results]

    async def hybrid_search(
        self,
        start_node_id: int,
        edge_type: str,
        vector: Sequence[float],
        top_k: int = 10,
        max_depth: int = 2,
        vector_property: str = "embedding",
        metric: str = "cosine",
    ) -> list[VectorResult]:
        """Graph traversal + vector search: traverse from start_node, then rank by embedding."""
        from coordinode._proto.coordinode.v1.common.types_pb2 import Vector  # type: ignore[import]
        from coordinode._proto.coordinode.v1.query.vector_pb2 import (  # type: ignore[import]
            DistanceMetric,
            HybridSearchRequest,
        )

        metric_map = {
            "cosine": DistanceMetric.DISTANCE_METRIC_COSINE,
            "l2": DistanceMetric.DISTANCE_METRIC_L2,
            "dot": DistanceMetric.DISTANCE_METRIC_DOT,
            "l1": DistanceMetric.DISTANCE_METRIC_L1,
        }
        req = HybridSearchRequest(
            start_node_id=start_node_id,
            edge_type=edge_type,
            max_depth=max_depth,
            vector_property=vector_property,
            query_vector=Vector(values=[float(v) for v in vector]),
            top_k=top_k,
            metric=metric_map.get(metric.lower(), DistanceMetric.DISTANCE_METRIC_COSINE),
        )
        resp = await self._vector_stub.HybridSearch(req, timeout=self._timeout)
        return [VectorResult(r) for r in resp.results]

    async def create_node(self, labels: list[str], properties: dict[str, PyValue]) -> NodeResult:
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import CreateNodeRequest  # type: ignore[import]

        req = CreateNodeRequest(labels=labels, properties=dict_to_props(properties))
        node = await self._graph_stub.CreateNode(req, timeout=self._timeout)
        return NodeResult(node)

    async def create_nodes_batch(self, nodes: Sequence[tuple[list[str], dict[str, PyValue]]]) -> list[NodeResult]:
        """Create many nodes in one atomic call, returned in input order.

        Each entry is a ``(labels, properties)`` pair with the same meaning it
        has in :meth:`create_node`. The server takes its secondary-index write
        locks once for the whole batch instead of once per node, so seeding data
        that carries vector or full-text properties is far cheaper this way than
        through a loop. Either every node is created or none is. An empty
        sequence is a valid no-op.
        """
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import (  # type: ignore[import]
            CreateNodeRequest,
            CreateNodesBatchRequest,
        )

        req = CreateNodesBatchRequest(
            nodes=[
                CreateNodeRequest(labels=labels, properties=dict_to_props(properties)) for labels, properties in nodes
            ]
        )
        resp = await self._graph_stub.CreateNodesBatch(req, timeout=self._timeout)
        return [NodeResult(n) for n in resp.nodes]

    async def get_node(self, node_id: int) -> NodeResult:
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import GetNodeRequest  # type: ignore[import]

        req = GetNodeRequest(node_id=node_id)
        node = await self._graph_stub.GetNode(req, timeout=self._timeout)
        return NodeResult(node)

    async def create_edge(
        self,
        edge_type: str,
        source_id: int,
        target_id: int,
        properties: dict[str, PyValue] | None = None,
    ) -> EdgeResult:
        from coordinode._proto.coordinode.v1.graph.graph_pb2 import CreateEdgeRequest  # type: ignore[import]

        req = CreateEdgeRequest(
            edge_type=edge_type,
            source_node_id=source_id,
            target_node_id=target_id,
            properties=dict_to_props(properties or {}),
        )
        edge = await self._graph_stub.CreateEdge(req, timeout=self._timeout)
        return EdgeResult(edge)

    async def get_schema_text(self) -> str:
        """Return schema as a human/LLM-readable string."""
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import (  # type: ignore[import]
            ListEdgeTypesRequest,
            ListLabelsRequest,
            PropertyType,  # type: ignore[import]
        )

        _type_name = {
            PropertyType.PROPERTY_TYPE_INT64: "INT64",
            PropertyType.PROPERTY_TYPE_FLOAT64: "FLOAT64",
            PropertyType.PROPERTY_TYPE_STRING: "STRING",
            PropertyType.PROPERTY_TYPE_BOOL: "BOOL",
            PropertyType.PROPERTY_TYPE_BYTES: "BYTES",
            PropertyType.PROPERTY_TYPE_TIMESTAMP: "TIMESTAMP",
            PropertyType.PROPERTY_TYPE_VECTOR: "VECTOR",
            PropertyType.PROPERTY_TYPE_LIST: "LIST",
            PropertyType.PROPERTY_TYPE_MAP: "MAP",
        }

        labels_resp = await self._schema_stub.ListLabels(ListLabelsRequest(), timeout=self._timeout)
        edges_resp = await self._schema_stub.ListEdgeTypes(ListEdgeTypesRequest(), timeout=self._timeout)

        lines = ["Node labels:"]
        for label in labels_resp.labels:
            props = ", ".join(f"{p.name}: {_type_name.get(p.type, '?')}" for p in label.properties)
            lines.append(f"  - {label.name} (properties: {props})" if props else f"  - {label.name}")

        lines.append("\nEdge types:")
        for et in edges_resp.edge_types:
            props = ", ".join(f"{p.name}: {_type_name.get(p.type, '?')}" for p in et.properties)
            lines.append(f"  - {et.name} (properties: {props})" if props else f"  - {et.name}")

        return "\n".join(lines)

    async def get_labels(self) -> list[LabelInfo]:
        """Return all node labels defined in the schema."""
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import ListLabelsRequest  # type: ignore[import]

        resp = await self._schema_stub.ListLabels(ListLabelsRequest(), timeout=self._timeout)
        return [LabelInfo(label) for label in resp.labels]

    async def get_edge_types(self) -> list[EdgeTypeInfo]:
        """Return all edge types defined in the schema."""
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import ListEdgeTypesRequest  # type: ignore[import]

        resp = await self._schema_stub.ListEdgeTypes(ListEdgeTypesRequest(), timeout=self._timeout)
        return [EdgeTypeInfo(et) for et in resp.edge_types]

    @staticmethod
    def _validate_property_dict(p: Any, idx: int) -> tuple[str, str, bool, bool]:
        """Validate a single property dict and return ``(name, type_str, required, unique)``."""
        if not isinstance(p, dict):
            raise ValueError(f"Property at index {idx} must be a dict; got {p!r}")
        name = p.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Property at index {idx} must have a non-empty 'name' key; got {p!r}")
        raw_type = p.get("type", "string")
        if "type" in p and not isinstance(raw_type, str):
            raise ValueError(f"Property {name!r} must use a string value for 'type'; got {raw_type!r}")
        type_str = str(raw_type).strip().lower()
        required = p.get("required", False)
        unique = p.get("unique", False)
        if not isinstance(required, bool) or not isinstance(unique, bool):
            raise ValueError(
                f"Property {name!r} must use boolean values for 'required' and 'unique'; got "
                f"required={required!r}, unique={unique!r}"
            )
        return name, type_str, required, unique

    @staticmethod
    def _build_property_definitions(
        properties: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
        property_type_cls: Any,
        property_definition_cls: Any,
    ) -> list[Any]:
        """Convert property dicts to proto PropertyDefinition objects.

        Shared by :meth:`create_label` and :meth:`create_edge_type` to avoid
        duplicating the type-map and validation logic.
        """
        # A 1:1 mirror of the wire enum, which is the whole list of types a
        # property can be *declared* as. Multi-vector and path are not among
        # them: they are shapes a value takes on its way in or out, carried on
        # PropertyValue, and no declarable type corresponds to either. Adding
        # a key here for one of them would name an enum member that does not
        # exist. Extend this only when the enum itself grows.
        type_map = {
            "int64": property_type_cls.PROPERTY_TYPE_INT64,
            "float64": property_type_cls.PROPERTY_TYPE_FLOAT64,
            "string": property_type_cls.PROPERTY_TYPE_STRING,
            "bool": property_type_cls.PROPERTY_TYPE_BOOL,
            "bytes": property_type_cls.PROPERTY_TYPE_BYTES,
            "timestamp": property_type_cls.PROPERTY_TYPE_TIMESTAMP,
            "vector": property_type_cls.PROPERTY_TYPE_VECTOR,
            "list": property_type_cls.PROPERTY_TYPE_LIST,
            "map": property_type_cls.PROPERTY_TYPE_MAP,
        }
        if properties is None:
            return []
        # list | tuple union syntax is valid in isinstance() for Python ≥3.10 (PEP 604).
        # This project targets Python ≥3.11 (pyproject.toml: requires-python = ">=3.11").
        if not isinstance(properties, list | tuple):
            raise ValueError(
                f"'properties' must be a list or tuple of property dicts or None; got {type(properties).__name__}"
            )
        result = []
        for idx, p in enumerate(properties):
            name, type_str, required, unique = AsyncCoordinodeClient._validate_property_dict(p, idx)
            if type_str not in type_map:
                raise ValueError(
                    f"Unknown property type {type_str!r} for property {name!r}. "
                    f"Expected 'type' to be one of: {sorted(type_map)}"
                )
            result.append(
                property_definition_cls(
                    name=name,
                    type=type_map[type_str],
                    required=required,
                    unique=unique,
                )
            )
        return result

    @staticmethod
    def _normalize_schema_mode(schema_mode: str | int, mode_map: dict[str, int]) -> int:
        """Normalize schema_mode (str or int) to a proto SchemaMode enum value.

        Shared by :meth:`create_label` and :meth:`create_edge_type` to avoid
        duplicating the validation and normalisation logic.

        Accepts:
        - ``str`` — case-insensitive, leading/trailing whitespace stripped.
        - ``int`` — must be one of the values in *mode_map*; allows round-tripping
          ``LabelInfo.schema_mode`` / ``EdgeTypeInfo.schema_mode`` back into the call.
        """
        if isinstance(schema_mode, bool):
            raise ValueError(f"schema_mode must be a str or int, got bool {schema_mode!r}.")
        if isinstance(schema_mode, int):
            # Accept int to allow round-tripping LabelInfo/EdgeTypeInfo.schema_mode.
            valid_ints = set(mode_map.values())
            if schema_mode not in valid_ints:
                raise ValueError(
                    f"schema_mode integer {schema_mode!r} is not a valid SchemaMode value; "
                    f"expected one of {sorted(valid_ints)} or a string {list(mode_map)!r}"
                )
            return schema_mode
        elif isinstance(schema_mode, str):
            normalized = schema_mode.strip().lower()
            if normalized not in mode_map:
                raise ValueError(f"schema_mode must be one of {list(mode_map)}, got {schema_mode!r}")
            return mode_map[normalized]
        else:
            raise ValueError(f"schema_mode must be a str or int, got {type(schema_mode).__name__!r}")

    async def create_label(
        self,
        name: str,
        properties: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        *,
        schema_mode: str | int = "strict",
    ) -> LabelInfo:
        """Create a node label in the schema registry.

        Args:
            name: Label name (e.g. ``"Person"``).
            properties: Optional list of property dicts with keys
                ``name`` (str), ``type`` (str), ``required`` (bool),
                ``unique`` (bool).  Type strings: ``"string"``,
                ``"int64"``, ``"float64"``, ``"bool"``, ``"bytes"``,
                ``"timestamp"``, ``"vector"``, ``"list"``, ``"map"``.
            schema_mode: ``"strict"`` (default — reject undeclared props),
                ``"validated"`` (allow extra props without interning),
                ``"flexible"`` (no enforcement).
        """
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import (  # type: ignore[import]
            CreateLabelRequest,
            PropertyDefinition,
            PropertyType,
            SchemaMode,
        )

        _mode_map = {
            "strict": SchemaMode.SCHEMA_MODE_STRICT,
            "validated": SchemaMode.SCHEMA_MODE_VALIDATED,
            "flexible": SchemaMode.SCHEMA_MODE_FLEXIBLE,
        }
        proto_schema_mode = self._normalize_schema_mode(schema_mode, _mode_map)

        proto_props = self._build_property_definitions(properties, PropertyType, PropertyDefinition)
        req = CreateLabelRequest(
            name=name,
            properties=proto_props,
            schema_mode=proto_schema_mode,
        )
        label = await self._schema_stub.CreateLabel(req, timeout=self._timeout)
        return LabelInfo(label)

    async def create_edge_type(
        self,
        name: str,
        properties: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ) -> EdgeTypeInfo:
        """Create an edge type in the schema registry.

        Args:
            name: Edge type name (e.g. ``"KNOWS"``).
            properties: Optional list of property dicts with keys
                ``name`` (str), ``type`` (str), ``required`` (bool),
                ``unique`` (bool). Same type strings as :meth:`create_label`.

        Note:
            ``schema_mode`` is not yet supported by the server for edge types
            (``CreateEdgeTypeRequest`` does not carry that field).  Schema
            mode enforcement for edge types is planned for a future release.
        """
        from coordinode._proto.coordinode.v1.graph.schema_pb2 import (  # type: ignore[import]
            CreateEdgeTypeRequest,
            PropertyDefinition,
            PropertyType,
        )

        proto_props = self._build_property_definitions(properties, PropertyType, PropertyDefinition)
        req = CreateEdgeTypeRequest(
            name=name,
            properties=proto_props,
        )
        et = await self._schema_stub.CreateEdgeType(req, timeout=self._timeout)
        return EdgeTypeInfo(et)

    async def create_text_index(
        self,
        name: str,
        label: str,
        properties: str | list[str] | tuple[str, ...],
        *,
        language: str = "",
    ) -> TextIndexInfo:
        """Create a full-text (BM25) index on one or more node properties.

        Args:
            name: Unique index name (e.g. ``"article_body"``).  Must be a
                simple Cypher identifier: letters, digits, and underscores only,
                starting with a letter or underscore.  Names with dashes or
                spaces are not supported by this method; use raw :meth:`cypher`
                with backtick-escaped identifiers instead.
            label: Node label to index (e.g. ``"Article"``).  Same identifier
                restrictions as *name* apply.
            properties: Property name or list of property names to index
                (e.g. ``"body"`` or ``["title", "body"]``).  Same identifier
                restrictions apply.
            language: Default stemming/tokenization language (e.g. ``"english"``,
                ``"russian"``).  Empty string uses the server default
                (``"english"``).  Same identifier restrictions apply.

        Returns:
            :class:`TextIndexInfo` with index metadata and document count.

        Example::

            info = await client.create_text_index("article_body", "Article", "body")
            # then: results = await client.text_search("Article", "machine learning")
        """
        _validate_cypher_identifier(name, "name")
        _validate_cypher_identifier(label, "label")
        if isinstance(properties, str):
            prop_list = [properties]
        elif isinstance(properties, list | tuple):
            prop_list = list(properties)
        else:
            raise ValueError("'properties' must be a property name (str) or a list or tuple of property names")
        if not prop_list:
            raise ValueError("'properties' must contain at least one property name")
        for prop in prop_list:
            _validate_cypher_identifier(prop, "property")
        if language:
            _validate_cypher_identifier(language, "language")
        props_expr = ", ".join(prop_list)
        lang_clause = f" DEFAULT LANGUAGE {language}" if language else ""
        cypher = f"CREATE TEXT INDEX {name} ON :{label}({props_expr}){lang_clause}"
        rows = await self.cypher(cypher)
        if rows:
            return TextIndexInfo(rows[0])
        effective_language = language or "english"
        return TextIndexInfo(
            {"index": name, "label": label, "properties": ", ".join(prop_list), "default_language": effective_language}
        )

    async def drop_text_index(self, name: str) -> None:
        """Drop a full-text index by name.

        Args:
            name: Index name previously passed to :meth:`create_text_index`.
                Must be a simple Cypher identifier (letters, digits, underscores).
                Use raw :meth:`cypher` with backtick-escaped identifiers for names
                that contain dashes or spaces.

        Example::

            await client.drop_text_index("article_body")
        """
        _validate_cypher_identifier(name, "name")
        await self.cypher(f"DROP TEXT INDEX {name}")

    async def traverse(
        self,
        start_node_id: int,
        edge_type: str,
        direction: str = "outbound",
        max_depth: int = 1,
    ) -> TraverseResult:
        """Traverse the graph from *start_node_id* following *edge_type* edges.

        Args:
            start_node_id: ID of the node to start from.
            edge_type: Edge type label to follow (e.g. ``"KNOWS"``).
            direction: ``"outbound"`` (default), ``"inbound"``, or ``"both"``.
            max_depth: Maximum hop count (default 1).

        Returns:
            :class:`TraverseResult` with ``nodes`` and ``edges`` lists.
        """
        # Validate pure string/int inputs before importing proto stubs — ensures ValueError
        # is raised even when proto stubs have not been generated yet.
        # Type guards come first so that wrong types raise ValueError, not AttributeError/TypeError.
        if not isinstance(direction, str):
            raise ValueError(f"direction must be a str, got {type(direction).__name__!r}.")
        _valid_directions = {"outbound", "inbound", "both"}
        key = direction.lower()
        if key not in _valid_directions:
            raise ValueError(f"Invalid direction {direction!r}. Must be one of: 'outbound', 'inbound', 'both'.")
        # bool is a subclass of int in Python, so `isinstance(True, int)` is True — exclude it.
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
            raise ValueError(f"max_depth must be an integer >= 1, got {max_depth!r}.")

        from coordinode._proto.coordinode.v1.graph.graph_pb2 import (  # type: ignore[import]
            TraversalDirection,
            TraverseRequest,
        )

        _direction_map = {
            "outbound": TraversalDirection.TRAVERSAL_DIRECTION_OUTBOUND,
            "inbound": TraversalDirection.TRAVERSAL_DIRECTION_INBOUND,
            "both": TraversalDirection.TRAVERSAL_DIRECTION_BOTH,
        }
        direction_value = _direction_map[key]

        req = TraverseRequest(
            start_node_id=start_node_id,
            edge_type=edge_type,
            direction=direction_value,
            max_depth=max_depth,
        )
        resp = await self._graph_stub.Traverse(req, timeout=self._timeout)
        return TraverseResult(resp)

    async def text_search(
        self,
        label: str,
        query: str,
        *,
        limit: int = 10,
        fuzzy: bool = False,
        language: str = "",
    ) -> list[TextResult]:
        """Run a full-text BM25 search over all indexed text properties for *label*.

        Args:
            label: Node label to search (e.g. ``"Article"``).
            query: Full-text query string. Supports boolean operators (``AND``,
                ``OR``, ``NOT``), phrase search (``"exact phrase"``), prefix
                wildcards (``term*``), and per-term boosting (``term^N``).
            limit: Maximum results to return (default 10). The server may apply
                its own upper bound; pass a reasonable value (e.g. ≤ 1000).
            fuzzy: If ``True``, apply Levenshtein-1 fuzzy matching to individual
                terms. Increases recall at the cost of precision.
            language: Tokenization/stemming language (e.g. ``"english"``,
                ``"russian"``). Empty string uses the index's default language.

        Returns:
            List of :class:`TextResult` ordered by BM25 score descending.
            Returns ``[]`` if no text index exists for *label*.

        Note:
            Text indexing is **not** automatic.  Before calling this method,
            create a full-text index with the Cypher DDL statement::

                CREATE TEXT INDEX my_index ON :Label(property)

            or via :meth:`create_text_index`.  Nodes written before the index
            was created are indexed immediately at DDL execution time.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError(f"limit must be an integer >= 1, got {limit!r}.")
        from coordinode._proto.coordinode.v1.query.text_pb2 import TextSearchRequest  # type: ignore[import]

        req = TextSearchRequest(label=label, query=query, limit=limit, fuzzy=fuzzy, language=language)
        resp = await self._text_stub.TextSearch(req, timeout=self._timeout)
        return [TextResult(r) for r in resp.results]

    async def health(self) -> bool:
        from coordinode._proto.coordinode.v1.health.health_pb2 import (  # type: ignore[import]
            HealthCheckRequest,
            ServingStatus,
        )

        try:
            resp = await self._health_stub.Check(HealthCheckRequest(), timeout=5.0)
            return resp.status == ServingStatus.SERVING_STATUS_SERVING
        except grpc.RpcError as e:
            logger.debug(
                "health check failed: %s %s",
                e.code(),  # type: ignore[union-attr]
                e.details(),  # type: ignore[union-attr]
            )
            return False


# ── Sync client (wraps async) ─────────────────────────────────────────────────


class Transaction:
    """Synchronous view of an :class:`AsyncTransaction`.

    Same semantics throughout, including the node affinity and the idle sweep
    described there. Obtained from :meth:`CoordinodeClient.transaction` or
    :meth:`CoordinodeClient.begin_transaction`.
    """

    def __init__(self, client: CoordinodeClient, inner: AsyncTransaction) -> None:
        self._client = client
        self._inner = inner

    def __repr__(self) -> str:
        return f"Transaction(id={self._inner.transaction_id}, state={self._inner._state})"

    @property
    def transaction_id(self) -> int:
        """Server-side handle for this transaction. Non-zero while it exists."""
        return self._inner.transaction_id

    @property
    def is_open(self) -> bool:
        """True while the transaction can still take statements and be committed."""
        return self._inner.is_open

    def cypher(
        self,
        query: str,
        params: dict[str, PyValue] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one statement inside this transaction. See :meth:`AsyncTransaction.cypher`."""
        return self._client._run(self._inner.cypher(query, params))  # type: ignore[no-any-return]

    def commit(self) -> int:
        """Apply every buffered write as one unit. See :meth:`AsyncTransaction.commit`."""
        return self._client._run(self._inner.commit())  # type: ignore[no-any-return]

    def rollback(self) -> None:
        """Discard every buffered write. See :meth:`AsyncTransaction.rollback`."""
        self._client._run(self._inner.rollback())


class CoordinodeClient:
    """
    Synchronous gRPC client for CoordiNode.

    Usage::

        with CoordinodeClient("localhost:7080") as client:
            rows = client.cypher("MATCH (n:Person) RETURN n.name LIMIT 5")
            print(rows)  # [{"n.name": "Alice"}, ...]
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int | None = None,
        *,
        tls: bool = False,
        timeout: float = 30.0,
    ) -> None:
        self._async = AsyncCoordinodeClient(host, port, tls=tls, timeout=timeout)
        self._loop = asyncio.new_event_loop()
        self._connected = False

    def __enter__(self) -> CoordinodeClient:
        if not self._connected:
            self._loop.run_until_complete(self._async.connect())
            self._connected = True
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying gRPC channel and event loop."""
        if self._connected:
            self._loop.run_until_complete(self._async.close())
            self._connected = False
        if not self._loop.is_closed():
            self._loop.close()

    def _run(self, coro: Any) -> Any:
        if self._loop.is_closed():
            raise RuntimeError("CoordinodeClient has been closed and cannot be reused")
        if not self._connected:
            self._loop.run_until_complete(self._async.connect())
            self._connected = True
        return self._loop.run_until_complete(coro)

    def cypher(
        self,
        query: str,
        params: dict[str, PyValue] | None = None,
        *,
        read_concern: str | None = None,
        write_concern: str | None = None,
        read_preference: str | None = None,
        after_index: int | None = None,
        at_timestamp: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an OpenCypher query. See :meth:`AsyncCoordinodeClient.cypher` for consistency args."""
        return self._run(
            self._async.cypher(
                query,
                params,
                read_concern=read_concern,
                write_concern=write_concern,
                read_preference=read_preference,
                after_index=after_index,
                at_timestamp=at_timestamp,
            )
        )

    def begin_transaction(self) -> Transaction:
        """Open an interactive transaction. See :meth:`AsyncCoordinodeClient.begin_transaction`."""
        return Transaction(self, self._run(self._async.begin_transaction()))

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """Run a block of statements as one transaction.

        Commits when the block finishes, rolls back when it raises::

            with client.transaction() as tx:
                tx.cypher("CREATE (:Person {name: $n})", {"n": "Alice"})
                tx.cypher("CREATE (:Person {name: $n})", {"n": "Bob"})

        See :meth:`AsyncCoordinodeClient.transaction` for the details.
        """
        tx = self.begin_transaction()
        try:
            yield tx
        except BaseException:
            if tx.is_open:
                with suppress(Exception):
                    tx.rollback()
            raise
        else:
            if tx.is_open:
                tx.commit()

    def vector_search(
        self,
        label: str,
        property: str,
        vector: Sequence[float],
        top_k: int = 10,
        metric: str = "cosine",
    ) -> list[VectorResult]:
        return self._run(self._async.vector_search(label, property, vector, top_k, metric))

    def hybrid_search(
        self,
        start_node_id: int,
        edge_type: str,
        vector: Sequence[float],
        top_k: int = 10,
        max_depth: int = 2,
        vector_property: str = "embedding",
        metric: str = "cosine",
    ) -> list[VectorResult]:
        return self._run(
            self._async.hybrid_search(start_node_id, edge_type, vector, top_k, max_depth, vector_property, metric)
        )

    def create_node(self, labels: list[str], properties: dict[str, PyValue]) -> NodeResult:
        return self._run(self._async.create_node(labels, properties))

    def create_nodes_batch(self, nodes: Sequence[tuple[list[str], dict[str, PyValue]]]) -> list[NodeResult]:
        """Create many nodes atomically. See :meth:`AsyncCoordinodeClient.create_nodes_batch`."""
        return self._run(self._async.create_nodes_batch(nodes))

    def get_node(self, node_id: int) -> NodeResult:
        return self._run(self._async.get_node(node_id))

    def create_edge(
        self,
        edge_type: str,
        source_id: int,
        target_id: int,
        properties: dict[str, PyValue] | None = None,
    ) -> EdgeResult:
        return self._run(self._async.create_edge(edge_type, source_id, target_id, properties))

    def get_schema_text(self) -> str:
        return self._run(self._async.get_schema_text())

    def get_labels(self) -> list[LabelInfo]:
        """Return all node labels defined in the schema."""
        return self._run(self._async.get_labels())

    def get_edge_types(self) -> list[EdgeTypeInfo]:
        """Return all edge types defined in the schema."""
        return self._run(self._async.get_edge_types())

    def create_label(
        self,
        name: str,
        properties: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        *,
        schema_mode: str | int = "strict",
    ) -> LabelInfo:
        """Create a node label in the schema registry."""
        return self._run(self._async.create_label(name, properties, schema_mode=schema_mode))

    def create_edge_type(
        self,
        name: str,
        properties: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ) -> EdgeTypeInfo:
        """Create an edge type in the schema registry."""
        return self._run(self._async.create_edge_type(name, properties))

    def create_text_index(
        self,
        name: str,
        label: str,
        properties: str | list[str] | tuple[str, ...],
        *,
        language: str = "",
    ) -> TextIndexInfo:
        """Create a full-text (BM25) index on one or more node properties."""
        return self._run(self._async.create_text_index(name, label, properties, language=language))

    def drop_text_index(self, name: str) -> None:
        """Drop a full-text index by name."""
        return self._run(self._async.drop_text_index(name))

    def traverse(
        self,
        start_node_id: int,
        edge_type: str,
        direction: str = "outbound",
        max_depth: int = 1,
    ) -> TraverseResult:
        """Traverse the graph from *start_node_id* following *edge_type* edges."""
        return self._run(self._async.traverse(start_node_id, edge_type, direction, max_depth))

    def text_search(
        self,
        label: str,
        query: str,
        *,
        limit: int = 10,
        fuzzy: bool = False,
        language: str = "",
    ) -> list[TextResult]:
        """Run a full-text BM25 search over all indexed text properties for *label*."""
        return self._run(self._async.text_search(label, query, limit=limit, fuzzy=fuzzy, language=language))

    def health(self) -> bool:
        return self._run(self._async.health())


# ── Consistency helpers ──────────────────────────────────────────────────────


_READ_CONCERN_MAP = {
    "local": "READ_CONCERN_LEVEL_LOCAL",
    "majority": "READ_CONCERN_LEVEL_MAJORITY",
    "linearizable": "READ_CONCERN_LEVEL_LINEARIZABLE",
    "snapshot": "READ_CONCERN_LEVEL_SNAPSHOT",
}
# Durability rises W0 < MEMORY < CACHE < W1 < MAJORITY. Anything below W1
# acknowledges before the write is replicated through Raft: a leader crash
# before the background drain loses every in-flight MEMORY or CACHE write.
_WRITE_CONCERN_MAP = {
    "w0": "WRITE_CONCERN_LEVEL_W0",
    "memory": "WRITE_CONCERN_LEVEL_MEMORY",
    "cache": "WRITE_CONCERN_LEVEL_CACHE",
    "w1": "WRITE_CONCERN_LEVEL_W1",
    "majority": "WRITE_CONCERN_LEVEL_MAJORITY",
}
_READ_PREFERENCE_MAP = {
    "primary": "READ_PREFERENCE_PRIMARY",
    "primary_preferred": "READ_PREFERENCE_PRIMARY_PREFERRED",
    "secondary": "READ_PREFERENCE_SECONDARY",
    "secondary_preferred": "READ_PREFERENCE_SECONDARY_PREFERRED",
    "nearest": "READ_PREFERENCE_NEAREST",
}


def _rows_to_dicts(resp: Any) -> list[dict[str, Any]]:
    """Decode an ExecuteCypher response into one dict per row.

    Shared by the auto-commit path and the in-transaction one: both answer with
    the same message, and a second copy of this loop is a second place for a
    decoding fix to be forgotten.

    Pairs strictly. A row carrying more or fewer values than there are columns
    is a wire-shape mismatch, and pairing loosely would hand the caller a dict
    with a key quietly missing, indistinguishable from a property the node does
    not have. Raising here names the real problem at the point it is visible.
    """
    columns = list(resp.columns)
    return [{col: from_property_value(val) for col, val in zip(columns, row.values, strict=True)} for row in resp.rows]


def _normalize_consistency_key(value: Any, field: str, mapping: dict[str, str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string; got {value!r}")
    enum_name = mapping.get(value.strip().lower())
    if enum_name is None:
        raise ValueError(f"invalid {field} {value!r}; expected one of {sorted(mapping)}")
    return enum_name


def _make_read_concern(level: str | None, after_index: int | None, at_timestamp: int | None = None) -> Any:
    from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb  # type: ignore[import]

    kwargs: dict[str, Any] = {}
    if at_timestamp is not None:
        # Reading at a pinned version IS a snapshot read: the server refuses
        # any other level with FAILED_PRECONDITION. Default to it when the
        # caller said nothing, and reject an explicit level it will refuse
        # rather than spending a round trip to be told.
        if level is None:
            level = "snapshot"
        elif _normalize_consistency_key(level, "read_concern", _READ_CONCERN_MAP) != ("READ_CONCERN_LEVEL_SNAPSHOT"):
            raise ValueError(f"at_timestamp requires read_concern='snapshot', got {level!r}")
    if level is not None:
        kwargs["level"] = getattr(pb, _normalize_consistency_key(level, "read_concern", _READ_CONCERN_MAP))
    if after_index is not None:
        if not isinstance(after_index, int) or isinstance(after_index, bool) or after_index < 0:
            raise ValueError(f"after_index must be a non-negative integer, got {after_index!r}")
        kwargs["after_index"] = after_index
    if at_timestamp is not None:
        # Positive, not merely non-negative: the field is a plain proto3 scalar
        # with no presence, so zero is not put on the wire and the server reads
        # it as "no pin". A request to read as of the epoch would come back as
        # a current read, which is the one answer time travel must not give.
        if not isinstance(at_timestamp, int) or isinstance(at_timestamp, bool) or at_timestamp < 1:
            raise ValueError(
                f"at_timestamp must be a positive integer (microseconds since the epoch), got {at_timestamp!r}"
            )
        # A fence waits for the log to reach an index; a pin reads a fixed
        # point in the past. The server calls the pair mutually exclusive and
        # answers INVALID_ARGUMENT, so say so here rather than a round trip
        # later. A zero fence waits for nothing and does not conflict.
        if after_index:
            raise ValueError(
                "after_index and at_timestamp are mutually exclusive: a causal fence "
                "waits for new writes, a pinned read asks for a fixed past"
            )
        kwargs["at_timestamp"] = at_timestamp
    return pb.ReadConcern(**kwargs)


def _make_write_concern(level: str) -> Any:
    from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb  # type: ignore[import]

    return pb.WriteConcern(level=getattr(pb, _normalize_consistency_key(level, "write_concern", _WRITE_CONCERN_MAP)))


def _make_read_preference(pref: str) -> Any:
    from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb  # type: ignore[import]

    return getattr(pb, _normalize_consistency_key(pref, "read_preference", _READ_PREFERENCE_MAP))


# ── Stub factories (deferred import) ─────────────────────────────────────────


def _cypher_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.cypher_pb2_grpc import CypherServiceStub  # type: ignore[import]

    return CypherServiceStub(channel)


def _vector_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.vector_pb2_grpc import VectorServiceStub  # type: ignore[import]

    return VectorServiceStub(channel)


def _text_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.query.text_pb2_grpc import TextServiceStub  # type: ignore[import]

    return TextServiceStub(channel)


def _graph_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.graph.graph_pb2_grpc import GraphServiceStub  # type: ignore[import]

    return GraphServiceStub(channel)


def _schema_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.graph.schema_pb2_grpc import SchemaServiceStub  # type: ignore[import]

    return SchemaServiceStub(channel)


def _health_stub(channel: Any) -> Any:
    from coordinode._proto.coordinode.v1.health.health_pb2_grpc import HealthServiceStub  # type: ignore[import]

    return HealthServiceStub(channel)
