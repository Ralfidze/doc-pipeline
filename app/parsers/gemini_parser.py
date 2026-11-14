import asyncio
import json
import logging
from io import BytesIO

from google.genai import errors, types
from pypdf import PdfWriter
from pypdf.errors import PyPdfError

from app.gemini import GeminiClient
from app.models import PageContent
from app.parsers.base import DocumentError, GeminiUnavailable, ParseResult
from app.parsers.pdf import open_pdf

log = logging.getLogger("parser.gemini")

MAX_PAGES = 25
REQUEST_TRIES = 2  # the first request and one more after a wrong answer

PROMPT = (
    "The attached PDF file has {count} pages. Read each page and write its content as markdown. "
    "Keep the text of the page as it is: headings, lists and tables in markdown. "
    "If a page is a scan, read the text from the image. "
    "Reply with a JSON list of exactly {count} strings. "
    "Item 1 is page 1, item 2 is page 2, and so on, in page order. "
    "One item for each page, and no other item."
)


def _prepare_sync(pdf_bytes: bytes) -> tuple[bytes, int, bool]:
    """Check the PDF. Return (bytes to send, pages sent, was cut to the page limit). Blocking."""
    reader = open_pdf(pdf_bytes)
    count = len(reader.pages)
    if count <= MAX_PAGES:
        return pdf_bytes, count, False
    try:
        writer = PdfWriter()
        for index in range(MAX_PAGES):
            writer.add_page(reader.pages[index])
        out = BytesIO()
        writer.write(out)
    except PyPdfError as exc:
        raise DocumentError("invalid_pdf", "The PDF file is damaged.") from exc
    return out.getvalue(), MAX_PAGES, True


def _read_items(text: str, expected: int) -> list[str] | None:
    """Return the page items, or None if the answer is wrong (not JSON, not a list, wrong count)."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    if not all(isinstance(item, str) for item in data):
        return None
    return data


class GeminiParser:
    """One request for each document. One more request after a wrong answer."""

    def __init__(self, gemini: GeminiClient, document_id: str | None = None) -> None:
        self._gemini = gemini
        self._document_id = document_id

    async def parse(self, pdf_bytes: bytes) -> ParseResult:
        document_id = self._document_id
        send_bytes, count, was_cut = await asyncio.to_thread(_prepare_sync, pdf_bytes)
        warnings = ["gemini_page_limit"] if was_cut else []
        extra = {"document_id": document_id} if document_id else {}
        contents = [
            types.Part.from_bytes(data=send_bytes, mime_type="application/pdf"),
            PROMPT.format(count=count),
        ]
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=list[str]
        )
        for request_number in range(1, REQUEST_TRIES + 1):
            log.info("gemini page request %d of %d, %d pages", request_number, REQUEST_TRIES, count, extra=extra)
            try:
                text = await self._gemini.generate(
                    contents, config, document_id=document_id, page_count=count
                )
            except errors.APIError as exc:
                if exc.code in (429, 503):
                    raise GeminiUnavailable(
                        f"Gemini did not answer the page request ({exc.code}) after 4 calls."
                    ) from exc
                raise
            items = _read_items(text, count)
            if items is not None:
                pages = [PageContent(page_number=n, content=item) for n, item in enumerate(items, start=1)]
                return ParseResult(pages=pages, warnings=warnings)
            log.warning("gemini page answer is wrong (expected %d items)", count, extra=extra)
        raise DocumentError(
            "gemini_bad_response", "Gemini gave a wrong answer for the pages two times."
        )
