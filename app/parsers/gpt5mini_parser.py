from app.parsers.base import ParseResult


class Gpt5MiniParser:
    """Stub. The API refuses `gpt5mini` before a message exists, so the worker never calls it."""

    async def parse(self, pdf_bytes: bytes) -> ParseResult:
        raise NotImplementedError("gpt5mini parser is not implemented")
