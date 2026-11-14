from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.exceptions import ResponseError
from redis.retry import Retry

STREAM = "documents:ingest"
GROUP = "workers"


def make_client(url: str) -> Redis:
    """Shared client. No response decoding (the PDF is bytes). Short timeouts, no retry."""
    return Redis.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=1,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
    )


WORKER_SOCKET_TIMEOUT = 10  # longer than the 5000 ms block of the group read


def make_worker_client(url: str) -> Redis:
    """Worker client. Read timeout longer than the block time of step 1. No retry."""
    return Redis.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=1,
        socket_timeout=WORKER_SOCKET_TIMEOUT,
        retry=Retry(NoBackoff(), 0),
    )


async def ensure_group(client: Redis) -> None:
    try:
        await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
