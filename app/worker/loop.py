import asyncio
import contextlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.gemini import GeminiClient
from app.models import Status, SummaryStatus
from app.parsers.base import DocumentError, GeminiUnavailable, Parser
from app.parsers.gemini_parser import GeminiParser
from app.parsers.gpt5mini_parser import Gpt5MiniParser
from app.parsers.pypdf_parser import PyPdfParser
from app.storage import documents, queue
from app.storage.client import ensure_group
from app.summarizer.summarizer import SummaryError, summarize

log = logging.getLogger("worker")

READ_COUNT = 4  # COUNT of step 1: also the most messages in process
MAX_ATTEMPTS = 3
REDIS_RETRY_SECONDS = 1.0
DRAIN_SECONDS = 25.0  # below the stop grace period of 30 s
CLAIM_INTERVAL_SECONDS = 30.0  # step 6: the time between two claims

def _get_parser(name: str, gemini: GeminiClient, document_id: str) -> Parser:
    match name:
        case "pypdf":
            return PyPdfParser()
        case "gemini":
            return GeminiParser(gemini, document_id)
        case "gpt5mini":
            return Gpt5MiniParser()
        case _:
            raise ValueError(f"unknown parser: {name!r}")


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
        result = await _get_parser(parser_name, gemini, document_id).parse(pdf)
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
        except Exception as exc:
            log.exception("attempt %d of %d failed", attempt, MAX_ATTEMPTS, extra=extra)
            if attempt < MAX_ATTEMPTS:
                await queue.retry(client, msg.msg_id, document_id, msg.fields.get("parser", ""), attempt)
            elif isinstance(exc, GeminiUnavailable):
                await _fail(
                    client, msg, document_id, attempt, exc.error_code,
                    f"Gemini did not answer the page request after {MAX_ATTEMPTS} attempts.",
                )
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


class Processor:
    """Runs messages through steps 2 to 5, from all three sources (read, recovery, claim).

    One semaphore of 4 slots limits the messages in process in total. The set `in_process`
    holds the ids of the messages that the worker took and did not finish: a claimed message
    in the set is ignored.
    """

    def __init__(self, client: Redis, gemini: GeminiClient) -> None:
        self.client = client
        self.gemini = gemini
        self.in_process: set[str] = set()
        self.tasks: set[asyncio.Task[None]] = set()
        self._slots = asyncio.Semaphore(READ_COUNT)

    def spawn(self, msg: queue.StreamMessage, source: str) -> asyncio.Task[None] | None:
        """Start a task for the message. Return None if its id is already in process."""
        extra = {"document_id": msg.fields.get("document_id", "")}
        if msg.msg_id in self.in_process:
            log.info("message %s from %s ignored, already in process", msg.msg_id, source, extra=extra)
            return None
        self.in_process.add(msg.msg_id)
        log.info("message %s from %s, attempt %s", msg.msg_id, source, msg.fields.get("attempt"), extra=extra)
        task = asyncio.create_task(self._run(msg))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _run(self, msg: queue.StreamMessage) -> None:
        try:
            async with self._slots:
                await handle_message(self.client, self.gemini, msg)
        finally:
            self.in_process.discard(msg.msg_id)


async def _run_batch(
    proc: Processor, messages: list[queue.StreamMessage], source: str, stop: asyncio.Event
) -> None:
    """Process all messages of one read at the same time. Return when all are done, or on stop."""
    tasks = {t for msg in messages if (t := proc.spawn(msg, source)) is not None}
    if not tasks:
        return
    stop_waiter = asyncio.create_task(stop.wait())
    try:
        while tasks and not stop.is_set():
            await asyncio.wait({*tasks, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
            tasks = {t for t in tasks if not t.done()}
    finally:
        stop_waiter.cancel()


async def recover_own_pending(
    proc: Processor, consumer: str, stop: asyncio.Event, count: int = READ_COUNT
) -> None:
    """Start recovery: read the own pending messages (id `0`) until a read returns none."""
    total = 0
    while not stop.is_set():
        try:
            messages = await queue.read_group(proc.client, consumer, count, start_id="0")
        except (RedisError, OSError) as exc:
            log.warning("Redis does not answer on the start recovery: %s", exc)
            await _sleep(stop, REDIS_RETRY_SECONDS)
            continue
        if not messages:
            break
        total += len(messages)
        log.info("start recovery: %d own pending messages", len(messages))
        await _run_batch(proc, messages, "recovery", stop)
    log.info("start recovery done, %d messages", total)


async def claim_once(
    proc: Processor,
    consumer: str,
    min_idle_ms: int = queue.CLAIM_IDLE_MS,
    count: int = queue.CLAIM_COUNT,
) -> list[asyncio.Task[None]]:
    """Step 6, one claim. Start a task for each claimed message that is not in process."""
    messages = await queue.autoclaim(proc.client, consumer, min_idle_ms, count)
    log.info("claim: %d messages idle for %d ms or more", len(messages), min_idle_ms)
    tasks = [proc.spawn(msg, "claim") for msg in messages]
    return [t for t in tasks if t is not None]


async def claim_loop(
    proc: Processor,
    consumer: str,
    stop: asyncio.Event,
    interval: float = CLAIM_INTERVAL_SECONDS,
    min_idle_ms: int = queue.CLAIM_IDLE_MS,
) -> None:
    """Step 6, own task: a claim every `interval` seconds. An error never ends this task."""
    while not stop.is_set():
        await _sleep(stop, interval)
        if stop.is_set():
            break
        try:
            await claim_once(proc, consumer, min_idle_ms)
        except (RedisError, OSError) as exc:
            log.warning("claim: Redis does not answer: %s", exc)
        except Exception:
            log.exception("claim: unexpected error")


async def run(client: Redis, gemini: GeminiClient, consumer: str, stop: asyncio.Event) -> None:
    """The consumer loop. It does not exit when Redis is down: it waits and goes on."""
    await _setup_group(client, stop)
    proc = Processor(client, gemini)
    claimer = asyncio.create_task(claim_loop(proc, consumer, stop))
    await recover_own_pending(proc, consumer, stop)
    log.info("consumer %s reads %s", consumer, queue.STREAM)
    while not stop.is_set():
        try:
            messages = await _read_or_stop(client, consumer, READ_COUNT, stop)
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
        # Process all messages of one read at the same time. Read again only when all are done.
        await _run_batch(proc, messages, "read", stop)

    claimer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await claimer
    if proc.tasks:
        log.info("stop: wait for %d messages in process", len(proc.tasks))
        _, pending = await asyncio.wait(proc.tasks, timeout=DRAIN_SECONDS)
        for task in pending:
            task.cancel()
