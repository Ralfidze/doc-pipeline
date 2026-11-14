from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api import documents, ops
from app.log import setup_logging
from app.settings import get_settings
from app.storage.client import ensure_group, make_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    client = make_client(get_settings().redis_url)
    app.state.redis = client
    await ensure_group(client)
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="doc-pipeline", lifespan=lifespan)
app.include_router(ops.router)
app.include_router(documents.router)


@app.exception_handler(RedisError)
async def redis_error_handler(request: Request, exc: RedisError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "data store unavailable"})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """The upload route never replies 422 (TS 2.5). Other routes keep the default."""
    if request.method == "POST" and request.url.path == "/documents":
        return JSONResponse(status_code=400, content={"detail": "invalid upload"})
    return await request_validation_exception_handler(request, exc)
