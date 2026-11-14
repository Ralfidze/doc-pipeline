import asyncio
import logging
from typing import Any

from google import genai
from google.genai import errors, types

log = logging.getLogger("gemini")

MAX_CONCURRENT_CALLS = 2
RETRY_WAITS = (2, 4, 8)  # seconds; the first call and 3 retries
RETRYABLE_CODES = (429, 503)
REQUEST_TIMEOUT_MS = 60_000  # HttpOptions.timeout is in milliseconds


class GeminiNotConfigured(Exception):
    """No API key and no test mode."""


class GeminiClient:
    """One client for parser and summarizer. Layers: semaphore, call retry, raw call."""

    def __init__(self, api_key: str, model_id: str, test_mode: str = "") -> None:
        self.model_id = model_id.strip()
        self.test_mode = test_mode.strip()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
        self._client: genai.Client | None = None
        if api_key and self.test_mode != "rate_limit":
            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
            )

    @property
    def is_available(self) -> bool:
        return self._client is not None or self.test_mode == "rate_limit"

    async def generate(
        self,
        contents: Any,
        config: types.GenerateContentConfig | None = None,
        *,
        document_id: str | None = None,
    ) -> str:
        """Layer 1: the semaphore. Return the text of the answer."""
        async with self._semaphore:
            return await self._with_retry(contents, config, document_id)

    async def _with_retry(
        self, contents: Any, config: types.GenerateContentConfig | None, document_id: str | None
    ) -> str:
        """Layer 2: retry on 429 and 503 only, with our own loop."""
        extra = {"document_id": document_id} if document_id else {}
        total = len(RETRY_WAITS) + 1
        for call_number in range(1, total + 1):
            log.info("gemini call %d of %d", call_number, total, extra=extra)
            try:
                return await self._raw_call(contents, config)
            except errors.APIError as exc:
                if exc.code not in RETRYABLE_CODES or call_number == total:
                    raise
                wait = RETRY_WAITS[call_number - 1]
                log.warning(
                    "gemini call %d failed with %s, wait %d s", call_number, exc.code, wait, extra=extra
                )
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    async def _raw_call(self, contents: Any, config: types.GenerateContentConfig | None) -> str:
        """Layer 3: one request. The test mode changes only this layer."""
        if self.test_mode == "rate_limit":
            raise errors.ClientError(
                429,
                {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "test mode rate_limit"}},
            )
        if self._client is None:
            raise GeminiNotConfigured("GEMINI_API_KEY is not set")
        response = await self._client.aio.models.generate_content(
            model=self.model_id, contents=contents, config=config
        )
        return response.text or ""
