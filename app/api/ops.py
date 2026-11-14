from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.models import QueueMetrics
from app.storage.queue import queue_numbers

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    try:
        await request.app.state.redis.ping()
    except RedisError:
        return JSONResponse(status_code=503, content={"redis": "down"})
    return JSONResponse(content={"redis": "ok"})


@router.get("/metrics/queue", response_model=QueueMetrics)
async def queue_metrics(request: Request) -> QueueMetrics:
    length, pending = await queue_numbers(request.app.state.redis)
    return QueueMetrics(length=length, pending=pending)
