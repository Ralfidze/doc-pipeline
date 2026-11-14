from dataclasses import dataclass

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import ResponseError

from app.storage.client import GROUP, STREAM, ensure_group

DEAD_STREAM = "documents:dead"
STREAM_MAXLEN = 10000


def add_message(conn: Redis | Pipeline, document_id: str, parser: str, attempt: int = 0) -> None:
    """Queue `XADD` for one document. Only ids, never PDF bytes. On a pipeline this does not send."""
    conn.xadd(
        STREAM,
        {"document_id": document_id, "parser": parser, "attempt": str(attempt)},
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )


async def queue_numbers(client: Redis) -> tuple[int, int]:
    """Return (length, pending). Run the group setup once if the group is missing (NOGROUP)."""
    try:
        return await _numbers(client)
    except ResponseError as exc:
        if "NOGROUP" not in str(exc):
            raise
    await ensure_group(client)
    return await _numbers(client)


async def _numbers(client: Redis) -> tuple[int, int]:
    length = await client.xlen(STREAM)
    summary = await client.xpending(STREAM, GROUP)
    return length, int(summary["pending"])


BLOCK_MS = 5000


@dataclass(frozen=True, slots=True)
class StreamMessage:
    msg_id: str
    fields: dict[str, str]


async def read_group(
    client: Redis, consumer: str, count: int, start_id: str = ">"
) -> list[StreamMessage]:
    """Step 1: `XREADGROUP ... COUNT n BLOCK 5000 ... >`. Run the group setup after NOGROUP.

    The start recovery passes `start_id="0"`: Redis then returns the pending messages of this
    consumer and ignores BLOCK.
    """
    try:
        reply = await _read(client, consumer, count, start_id)
    except ResponseError as exc:
        if "NOGROUP" not in str(exc):
            raise
        await ensure_group(client)
        reply = await _read(client, consumer, count, start_id)
    messages: list[StreamMessage] = []
    for _stream, entries in reply or []:
        messages.extend(_to_messages(entries))
    return messages


def _to_messages(entries: list) -> list[StreamMessage]:
    # The fields of an entry that was trimmed from the stream are None: give an empty dict.
    return [
        StreamMessage(
            msg_id.decode(),
            {k.decode(): v.decode() for k, v in (raw or {}).items()},
        )
        for msg_id, raw in entries
    ]


async def _read(client: Redis, consumer: str, count: int, start_id: str) -> list:
    return await client.xreadgroup(
        GROUP, consumer, {STREAM: start_id}, count=count, block=BLOCK_MS
    )


CLAIM_IDLE_MS = 60_000
CLAIM_COUNT = 10


async def autoclaim(
    client: Redis, consumer: str, min_idle_ms: int = CLAIM_IDLE_MS, count: int = CLAIM_COUNT
) -> list[StreamMessage]:
    """Step 6: `XAUTOCLAIM documents:ingest workers <consumer> 60000 0 COUNT 10`."""
    try:
        reply = await _autoclaim(client, consumer, min_idle_ms, count)
    except ResponseError as exc:
        if "NOGROUP" not in str(exc):
            raise
        await ensure_group(client)
        reply = await _autoclaim(client, consumer, min_idle_ms, count)
    return _to_messages(reply[1])


async def _autoclaim(client: Redis, consumer: str, min_idle_ms: int, count: int) -> list:
    return await client.xautoclaim(
        STREAM, GROUP, consumer, min_idle_time=min_idle_ms, start_id="0", count=count
    )


def add_ack(conn: Redis | Pipeline, msg_id: str) -> None:
    """Queue `XACK`. On a pipeline this does not send."""
    conn.xack(STREAM, GROUP, msg_id)


async def ack(client: Redis, msg_id: str) -> None:
    await client.xack(STREAM, GROUP, msg_id)


async def retry(client: Redis, msg_id: str, document_id: str, parser: str, attempt: int) -> None:
    """Step 5: `XACK` and `XADD` with the new `attempt` in one MULTI."""
    async with client.pipeline(transaction=True) as pipe:
        add_ack(pipe, msg_id)
        add_message(pipe, document_id, parser, attempt)
        await pipe.execute()


def add_dead(
    conn: Redis | Pipeline,
    document_id: str,
    parser: str,
    attempt: int,
    error_code: str,
    error_message: str,
) -> None:
    """Queue `XADD` to the dead-letter stream. On a pipeline this does not send."""
    conn.xadd(
        DEAD_STREAM,
        {
            "document_id": document_id,
            "parser": parser,
            "attempt": str(attempt),
            "error_code": error_code,
            "error_message": error_message,
        },
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )
