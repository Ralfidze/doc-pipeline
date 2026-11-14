import asyncio

from pypdf.errors import PyPdfError

from app.models import PageContent
from app.parsers.base import DocumentError, ParseResult
from app.parsers.pdf import open_pdf


def _parse_sync(pdf_bytes: bytes) -> ParseResult:
    reader = open_pdf(pdf_bytes)
    try:
        pages = [
            PageContent(page_number=number, content=page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]
    except PyPdfError as exc:
        raise DocumentError("invalid_pdf", "The PDF file is damaged.") from exc
    warnings = [] if any(p.content.strip() for p in pages) else ["no_text_layer"]
    return ParseResult(pages=pages, warnings=warnings)


class PyPdfParser:
    async def parse(self, pdf_bytes: bytes) -> ParseResult:
        return await asyncio.to_thread(_parse_sync, pdf_bytes)
