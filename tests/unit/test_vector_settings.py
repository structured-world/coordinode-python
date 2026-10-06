"""Per-statement vector search settings on the ExecuteCypher request.

Driven through the real generated messages and a fake stub, so a renamed or
retyped proto field fails here instead of reaching a user.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from coordinode._proto.coordinode.v1.query import cypher_pb2
from coordinode._proto.coordinode.v1.replication import consistency_pb2 as pb
from coordinode.client import AsyncCoordinodeClient, CoordinodeClient


def _stub() -> object:
    return type(
        "FakeCypherStub",
        (),
        {"ExecuteCypher": AsyncMock(return_value=cypher_pb2.ExecuteCypherResponse())},
    )()


def _sent(client: AsyncCoordinodeClient) -> cypher_pb2.ExecuteCypherRequest:
    return client._cypher_stub.ExecuteCypher.call_args.args[0]


def _run(**kwargs: object) -> cypher_pb2.ExecuteCypherRequest:
    client = AsyncCoordinodeClient("localhost:0")
    client._cypher_stub = _stub()
    asyncio.run(client.cypher("RETURN 1", **kwargs))
    return _sent(client)


def test_unset_settings_leave_the_server_default() -> None:
    """Neither field is put on the wire when the caller names none: the
    server then applies its own default and the session's setting."""
    sent = _run()
    assert sent.vector_consistency == pb.VECTOR_CONSISTENCY_UNSPECIFIED
    assert not sent.HasField("vector_build_wait_ms")


def test_settings_reach_the_request() -> None:
    sent = _run(vector_consistency="exact", vector_build_wait_ms=2_500)
    assert sent.vector_consistency == pb.VECTOR_CONSISTENCY_EXACT
    assert sent.HasField("vector_build_wait_ms")
    assert sent.vector_build_wait_ms == 2_500


def test_a_zero_build_wait_is_sent_not_dropped() -> None:
    """Zero means "refuse a building index at once". The field has presence,
    so zero must be on the wire, or the server would fall back to its
    default wait."""
    sent = _run(vector_build_wait_ms=0)
    assert sent.HasField("vector_build_wait_ms")
    assert sent.vector_build_wait_ms == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"vector_consistency": "eventual"}, "invalid vector_consistency"),
        ({"vector_build_wait_ms": -1}, "vector_build_wait_ms must be"),
    ],
)
def test_an_invalid_setting_sends_nothing(kwargs: dict[str, object], message: str) -> None:
    client = AsyncCoordinodeClient("localhost:0")
    client._cypher_stub = _stub()
    with pytest.raises(ValueError, match=message):
        asyncio.run(client.cypher("RETURN 1", **kwargs))
    assert client._cypher_stub.ExecuteCypher.await_count == 0


def test_the_sync_client_passes_the_settings_on() -> None:
    client = CoordinodeClient("localhost:0")
    client._async._cypher_stub = _stub()
    client._connected = True
    try:
        client.cypher("RETURN 1", vector_consistency="snapshot", vector_build_wait_ms=7)
        sent = _sent(client._async)
    finally:
        client.close()
    assert sent.vector_consistency == pb.VECTOR_CONSISTENCY_SNAPSHOT
    assert sent.vector_build_wait_ms == 7
