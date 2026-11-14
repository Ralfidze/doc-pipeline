import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import ResponseError

from app.models import (
    BatchResponse,
    DocumentItem,
    DocumentResponse,
    PageContent,
    ParserName,
    Status,
    SummaryStatus,
)
from app.storage import queue

TTL_SECONDS = 3600


def doc_key(document_id: str) -> str:
    return f"doc:{document_id}"


def pdf_key(document_id: str) -> str:
    return f"doc:{document_id}:pdf"


def pages_key(document_id: str) -> str:
    return f"doc:{document_id}:pages"


def summary_key(document_id: str) -> str:
    return f"doc:{document_id}:summary"


def batch_key(batch_id: str) -> str:
    return f"batch:{batch_id}"


@dataclass(frozen=True, slots=True)
class AcceptedFile:
    filename: str
    data: bytes


async def write_upload(
    client: Redis, batch_id: str, parser: str, files: list[AcceptedFile]
) -> list[tuple[str, str]]:
    """Write all accepted files in one MULTI transaction. Return (document_id, filename) in order."""
    now = datetime.now(UTC)
    created_at = now.isoformat()
    expires_at = int(now.timestamp()) + TTL_SECONDS
    result: list[tuple[str, str]] = []
    async with client.pipeline(transaction=True) as pipe:
        for seq, item in enumerate(files):
            document_id = str(uuid.uuid4())
            result.append((document_id, item.filename))
            pipe.hset(
                doc_key(document_id),
                mapping={
                    "status": Status.QUEUED.value,
                    "parser": parser,
                    "filename": item.filename,
                    "batch_id": batch_id,
                    "seq": seq,
                    "warnings": json.dumps([]),
                    "error_code": "",
                    "error_message": "",
                    "summary_status": SummaryStatus.PENDING.value,
                    "summary_error": "",
                    "created_at": created_at,
                    "updated_at": created_at,
                    "expires_at": expires_at,
                },
            )
            pipe.expireat(doc_key(document_id), expires_at)
            pipe.set(pdf_key(document_id), item.data, exat=expires_at)
            pipe.sadd(batch_key(batch_id), document_id)
            queue.add_message(pipe, document_id, parser)
        pipe.expireat(batch_key(batch_id), expires_at)
        await pipe.execute()
    return result


def _text(value: bytes | None) -> str:
    return value.decode() if value else ""


def _or_none(value: str) -> str | None:
    """An empty string in the hash means "no value"."""
    return value or None


async def read_document(client: Redis, document_id: str) -> DocumentResponse | None:
    """Read one document. Return None if the hash does not exist (unknown or expired)."""
    try:
        raw = await client.hgetall(doc_key(document_id))
    except ResponseError as exc:
        # An id such as "x:pages" names a string key, not a hash: not a document.
        if "WRONGTYPE" in str(exc):
            return None
        raise
    if not raw:
        return None
    h = {k.decode(): v.decode() for k, v in raw.items()}
    status = Status(h["status"])
    summary_status = SummaryStatus(h["summary_status"])

    pages: list[PageContent] | None = None
    if status in (Status.SUMMARIZING, Status.DONE):
        pages_raw = await client.get(pages_key(document_id))
        if pages_raw is not None:
            pages = [PageContent(**p) for p in json.loads(pages_raw)]
    summary: str | None = None
    if summary_status is SummaryStatus.DONE:
        summary = _or_none(_text(await client.get(summary_key(document_id))))

    return DocumentResponse(
        document_id=document_id,
        filename=h["filename"],
        parser=ParserName(h["parser"]),
        status=status,
        warnings=json.loads(h.get("warnings") or "[]"),
        error_code=_or_none(h.get("error_code", "")),
        error_message=_or_none(h.get("error_message", "")),
        pages=pages,
        summary=summary,
        summary_status=summary_status,
        summary_error=_or_none(h.get("summary_error", "")),
        created_at=h["created_at"],
        updated_at=h["updated_at"],
    )


async def read_batch(client: Redis, batch_id: str) -> BatchResponse | None:
    """Read one batch in upload order. Return None for an empty or absent set."""
    members = await client.smembers(batch_key(batch_id))
    if not members:
        return None
    ids = [m.decode() for m in members]
    async with client.pipeline(transaction=False) as pipe:
        for document_id in ids:
            pipe.hmget(doc_key(document_id), "filename", "status", "seq")
        rows = await pipe.execute()
    found = [
        (int(seq), DocumentItem(document_id=document_id, filename=filename.decode(), status=Status(status.decode())))
        for document_id, (filename, status, seq) in zip(ids, rows, strict=True)
        if filename is not None
    ]
    found.sort(key=lambda pair: pair[0])
    return BatchResponse(batch_id=batch_id, documents=[item for _, item in found])


# ---- Worker writes. Each write sets `updated_at` and sets the expiry of each key again (TS 2.3). ----


class DocumentGone(Exception):
    """The hash does not exist: the document expired. A write would make a key with no valid expiry."""


async def read_fields(client: Redis, document_id: str) -> dict[str, str] | None:
    """Read the hash as text. Return None if it does not exist."""
    raw = await client.hgetall(doc_key(document_id))
    if not raw:
        return None
    return {k.decode(): v.decode() for k, v in raw.items()}


async def read_pdf(client: Redis, document_id: str) -> bytes | None:
    return await client.get(pdf_key(document_id))


async def read_pages(client: Redis, document_id: str) -> list[PageContent] | None:
    raw = await client.get(pages_key(document_id))
    if raw is None:
        return None
    return [PageContent(**p) for p in json.loads(raw)]


async def _write(
    client: Redis,
    document_id: str,
    fields: dict[str, str],
    strings: dict[str, str] | None = None,
    extra: Callable[[Pipeline], None] | None = None,
) -> None:
    """One MULTI: hash fields, `updated_at`, string keys, expiry of each key, optional extra commands."""
    expires_at = await client.hget(doc_key(document_id), "expires_at")
    if expires_at is None:
        raise DocumentGone(document_id)
    at = int(expires_at)
    async with client.pipeline(transaction=True) as pipe:
        pipe.hset(doc_key(document_id), mapping={**fields, "updated_at": datetime.now(UTC).isoformat()})
        pipe.expireat(doc_key(document_id), at)
        for key, value in (strings or {}).items():
            pipe.set(key, value, exat=at)
        if extra is not None:
            extra(pipe)
        await pipe.execute()


async def set_status(client: Redis, document_id: str, status: Status) -> None:
    await _write(client, document_id, {"status": status.value})


async def write_pages(client: Redis, document_id: str, pages: list[PageContent], warnings: list[str]) -> None:
    """Write the pages and the `warnings` field in one transaction."""
    await _write(
        client,
        document_id,
        {"warnings": json.dumps(warnings)},
        {pages_key(document_id): json.dumps([p.model_dump() for p in pages])},
    )


async def write_summary(client: Redis, document_id: str, summary: str) -> None:
    await _write(
        client,
        document_id,
        {"summary_status": SummaryStatus.DONE.value, "summary_error": ""},
        {summary_key(document_id): summary},
    )


async def set_summary_skipped(client: Redis, document_id: str) -> None:
    await _write(client, document_id, {"summary_status": SummaryStatus.SKIPPED.value})


async def set_summary_failure(client: Redis, document_id: str, error: str) -> None:
    await _write(
        client, document_id, {"summary_status": SummaryStatus.FAILED.value, "summary_error": error}
    )


async def set_failure(
    client: Redis,
    document_id: str,
    error_code: str,
    error_message: str,
    extra: Callable[[Pipeline], None] | None = None,
) -> None:
    """Set `failed` and the error. `extra` adds commands (XACK, dead letter) to the same MULTI."""
    await _write(
        client,
        document_id,
        {
            "status": Status.FAILED.value,
            "error_code": error_code,
            "error_message": error_message,
            "summary_status": SummaryStatus.SKIPPED.value,
        },
        extra=extra,
    )
