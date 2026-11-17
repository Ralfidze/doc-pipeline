from google.genai import errors

from app.gemini import DATA_INSTRUCTION, GeminiClient, GeminiNotConfigured
from app.models import PageContent

MAX_INPUT_CHARS = 30_000
HEAD_CHARS = 20_000
TAIL_CHARS = 10_000

PROMPT = (
    "Summarize the document below in one short text of at most 5 sentences. "
    "Reply with the summary only.\n\n"
)


class SummaryError(Exception):
    """The summary failed. The message is one sentence for `summary_error`."""


def build_input(pages: list[PageContent]) -> str:
    """Join the page contents in page order. Cut a long input to its head and its tail."""
    text = "\n\n".join(p.content for p in sorted(pages, key=lambda p: p.page_number))
    if len(text) > MAX_INPUT_CHARS:
        return text[:HEAD_CHARS] + text[-TAIL_CHARS:]
    return text


def build_prompt(pages: list[PageContent]) -> str:
    """The task text, the data instruction, then the page text between the document markers."""
    return f"{PROMPT}{DATA_INSTRUCTION}\n\n<document>\n{build_input(pages)}\n</document>"


async def summarize(gemini: GeminiClient, pages: list[PageContent], document_id: str) -> str:
    """Return the summary. Raise `SummaryError` for each failure."""
    if not gemini.is_available:
        raise SummaryError("GEMINI_API_KEY is not set.")
    try:
        text = await gemini.generate(build_prompt(pages), document_id=document_id)
    except GeminiNotConfigured as exc:
        raise SummaryError("GEMINI_API_KEY is not set.") from exc
    except errors.APIError as exc:
        if exc.code in (429, 503):
            raise SummaryError(f"Gemini was busy or rate limited ({exc.code}) on all 4 calls.") from exc
        raise SummaryError(f"Gemini returned the error {exc.code}.") from exc
    except TimeoutError as exc:
        raise SummaryError("Gemini did not answer in time.") from exc
    except Exception as exc:  # a different Gemini or network error is a summary failure too
        raise SummaryError(f"The summary call failed ({type(exc).__name__}).") from exc
    if not text.strip():
        raise SummaryError("Gemini returned an empty summary.")
    return text.strip()
