from dataclasses import dataclass, field
from typing import Protocol

from app.models import PageContent


class DocumentError(Exception):
    """An error that a new attempt cannot repair. The worker fails the document at once."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class GeminiUnavailable(Exception):
    """Gemini failed the parse call on all 4 calls with 429 or 503. A new attempt can repair it."""

    error_code = "gemini_unavailable"


@dataclass(slots=True)
class ParseResult:
    pages: list[PageContent]
    warnings: list[str] = field(default_factory=list)


class Parser(Protocol):
    async def parse(self, pdf_bytes: bytes) -> ParseResult: ...
