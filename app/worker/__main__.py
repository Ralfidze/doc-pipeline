import asyncio
import logging
import signal
import socket

from app.gemini import GeminiClient
from app.log import setup_logging
from app.settings import get_settings
from app.storage.client import make_worker_client
from app.worker.loop import run

log = logging.getLogger("worker")


async def main() -> None:
    setup_logging()
    settings = get_settings()
    client = make_worker_client(settings.redis_url)
    gemini = GeminiClient(settings.gemini_api_key, settings.gemini_model_id, settings.gemini_test_mode)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started")
    try:
        await run(client, gemini, socket.gethostname(), stop)
    finally:
        await client.aclose()
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
