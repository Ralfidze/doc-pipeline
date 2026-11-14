import asyncio
import contextlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.gemini import GeminiClient
from app.models import Status, SummaryStatus
from app.parsers.base import DocumentError, Parser
from app.parsers.gpt5mini_parser import Gpt5MiniParser
from app.parsers.pypdf_parser import PyPdfParser
from app.storage import documents, queue
from app.storage.client import ensure_group
from app.summarizer.summarizer import SummaryError, summarize

log = logging.getLogger("worker")

MAX_IN_PROCESS = 4
MAX_ATTEMPTS = 3
REDIS_RETRY_SECONDS = 1.0
DRAIN_SECONDS = 25.0  # below the stop grace period of 30 s

# Task 5 adds `gemini`. Until then a message with `parser=gemini` is an error of the parse step.
PARSERS: dict[str, type[Parser]] = {"pypdf": PyPdfParser, "gpt5mini": Gpt5MiniParser}


def _get_parser(name: str) -> Parser:
    return PARSERS[name]()


async def _process(client: Redis, gemini: GeminiClient, msg: queue.StreamMessage, document_id: str) -> None:
    """Steps 2 to 4 with the guard. Raise on an error: the caller applies step 5."""
    extra = {"document_id": document_id}
    state = await documents.read_fields(client, document_id)
    if state is None:
        log.info("document expired, message acknowledged", extra=extra)
        await queue.ack(client, msg.msg_id)
        return
    if state["status"] in (Status.DONE.value, Status.FAILED.value):
        log.info("document has a final status, message acknowledged", extra=extra)
        await queue.ack(client, msg.msg_id)
        return

    pages = await documents.read_pages(client, document_id)
    if pages is None:
        await documents.set_status(client, document_id, Status.PARSING)
        pdf = await documents.read_pdf(client, document_id)
        if pdf is None:
            raise RuntimeError("PDF bytes are missing")
        parser_name = msg.fields.get("parser", "")
        log.info("parse start, parser %s", parser_name, extra=extra)
        result = await _get_parser(parser_name).parse(pdf)
        await documents.write_pages(client, document_id, result.pages, result.warnings)
        pages = result.pages
        log.info("parse done, %d pages", len(pages), extra=extra)
    else:
        log.info("pages exist, parse skipped", extra=extra)

    await documents.set_status(client, document_id, Status.SUMMARIZING)
    if state["summary_status"] == SummaryStatus.PENDING.value:
        if not any(p.content.strip() for p in pages):
            await documents.set_summary_skipped(client, document_id)
            log.info("no text, summary skipped", extra=extra)
        else:
            try:
                summary = await summarize(gemini, pages, document_id)
            except SummaryError as exc:
                await documents.set_summary_failure(client, document_id, str(exc))
                log.warning("summary failed: %s", exc, extra=extra)
            else:
                await documents.write_summary(client, document_id, summary)
                log.info("summary done", extra=extra)

    await documents.set_status(client, document_id, Status.DONE)
    await queue.ack(client, msg.msg_id)
    log.info("document done", extra=extra)


async def _fail(
    client: Redis,
    msg: queue.StreamMessage,
    document_id: str,
    attempt: int,
    error_code: str,
    error_message: str,
) -> None:
    """Set `failed`, `XACK` and the dead-letter `XADD` in one MULTI."""

    def extra_commands(pipe) -> None:  # noqa: ANN001
        queue.add_ack(pipe, msg.msg_id)
        queue.add_dead(
            pipe, document_id, msg.fields.get("parser", ""), attempt, error_code, error_message
        )

    await documents.set_failure(client, document_id, error_code, error_message, extra_commands)
    log.error("document failed: %s", error_code, extra={"document_id": document_id})


async def handle_message(client: Redis, gemini: GeminiClient, msg: queue.StreamMessage) -> None:
    document_id = msg.fields.get("document_id", "")
    if not document_id:
        log.error("message %s has no document_id, acknowledged", msg.msg_id)
        await queue.ack(client, msg.msg_id)
        return
    extra = {"document_id": document_id}
    attempt = int(msg.fields.get("attempt", "0") or 0) + 1  # failed attempts, this one included
    try:
        try:
            await _process(client, gemini, msg, document_id)
        except documents.DocumentGone:
            log.info("document expired during processing", extra=extra)
            await queue.ack(client, msg.msg_id)
        except DocumentError as exc:
            await _fail(client, msg, document_id, attempt, exc.error_code, exc.message)
        except RedisError:
            raise
        except Exception:
            log.exception("attempt %d of %d failed", attempt, MAX_ATTEMPTS, extra=extra)
            if attempt < MAX_ATTEMPTS:
                await queue.retry(client, msg.msg_id, document_id, msg.fields.get("parser", ""), attempt)
            else:
                await _fail(
                    client, msg, document_id, attempt, "processing_failed",
                    f"The document could not be processed after {MAX_ATTEMPTS} attempts.",
                )
    except documents.DocumentGone:
        await queue.ack(client, msg.msg_id)
    except RedisError:
        # Step 5, Redis down: no XACK is possible. The message stays pending.
        log.warning("Redis error, message stays pending", exc_info=True, extra=extra)


async def _read_or_stop(client: Redis, consumer: str, count: int, stop: asyncio.Event) -> list[queue.StreamMessage] | None:
    """Step 1, but a stop signal cancels the blocking read. Return None on stop."""
    read = asyncio.create_task(queue.read_group(client, consumer, count))
    waiter = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({read, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
    if not read.done():
        read.cancel()
        with contextlib.suppress(asyncio.CancelledError, RedisError):
            await read
        return None
    return read.result()


async def _setup_group(client: Redis, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await ensure_group(client)
            return
        except RedisError as exc:
            log.warning("Redis not ready for the group setup: %s", exc)
            await _sleep(stop, REDIS_RETRY_SECONDS)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def run(client: Redis, gemini: GeminiClient, consumer: str, stop: asyncio.Event) -> None:
    """The consumer loop. It does not exit when Redis is down: it waits and goes on."""
    await _setup_group(client, stop)
    tasks: set[asyncio.Task[None]] = set()
    log.info("consumer %s reads %s", consumer, queue.STREAM)
    while not stop.is_set():
        free = MAX_IN_PROCESS - len(tasks)
        if free <= 0:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            continue
        try:
            messages = await _read_or_stop(client, consumer, free, stop)
        except ResponseError as exc:
            log.warning("Redis error on read: %s", exc)
            await _sleep(stop, REDIS_RETRY_SECONDS)
            continue
        except (RedisError, OSError) as exc:
            log.warning("Redis does not answer: %s", exc)
            await _sleep(stop, REDIS_RETRY_SECONDS)
            continue
        if messages is None:
            break
        for msg in messages:
            task = asyncio.create_task(handle_message(client, gemini, msg))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    if tasks:
        log.info("stop: wait for %d messages in process", len(tasks))
        _, pending = await asyncio.wait(tasks, timeout=DRAIN_SECONDS)
        for task in pending:
            task.cancel()
