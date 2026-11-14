from dataclasses import dataclass

from app.models import ParserName
from app.settings import Settings

MAX_FILES = 10
MAX_FILE_BYTES = 20 * 1024 * 1024
PDF_SIGNATURE = b"%PDF-"


@dataclass(frozen=True, slots=True)
class Refusal:
    status_code: int
    detail: str


NOT_A_PDF = Refusal(415, "not a PDF file")
TOO_LARGE = Refusal(413, "file too large")


def check_upload(parser: str | None, file_count: int, settings: Settings) -> Refusal | None:
    """Checks 1 to 4 of TS 2.2, in order. Return the first problem."""
    if parser not in {p.value for p in ParserName}:
        return Refusal(400, "unknown parser")
    if parser == ParserName.GPT5MINI:
        if not settings.openai_api_key:
            return Refusal(400, "parser not configured")
        return Refusal(400, "parser not implemented")
    if parser == ParserName.GEMINI and not settings.gemini_api_key:
        return Refusal(400, "parser not configured")
    if file_count == 0:
        return Refusal(400, "no files")
    if file_count > MAX_FILES:
        return Refusal(400, "too many files")
    return None


def check_file(head: bytes, size: int) -> Refusal | None:
    """Checks 5 and 6 of TS 2.2 for one file. `head` holds the first 5 bytes."""
    if not head.startswith(PDF_SIGNATURE):
        return NOT_A_PDF
    if size > MAX_FILE_BYTES:
        return TOO_LARGE
    return None
