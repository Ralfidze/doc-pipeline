import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.api import uploads
from app.models import (
    BatchResponse,
    DocumentItem,
    DocumentResponse,
    RejectedFile,
    Status,
    UploadResponse,
)
from app.settings import get_settings
from app.storage.documents import AcceptedFile, read_batch, read_document, write_upload

router = APIRouter()


@router.post("/documents", status_code=202, response_model=UploadResponse)
async def upload_documents(
    request: Request,
    parser: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> UploadResponse | JSONResponse:
    uploaded = files or []
    refusal = uploads.check_upload(parser, len(uploaded), get_settings())
    if refusal is not None:
        raise HTTPException(refusal.status_code, refusal.detail)
    assert parser is not None

    accepted: list[AcceptedFile] = []
    rejected: list[RejectedFile] = []
    first_refusal: uploads.Refusal | None = None
    for item in uploaded:
        name = item.filename or ""
        head = await item.read(5)
        file_refusal = uploads.check_file(head, item.size or 0)
        if file_refusal is None:
            await item.seek(0)
            accepted.append(AcceptedFile(name, await item.read()))
            continue
        first_refusal = first_refusal or file_refusal
        rejected.append(RejectedFile(filename=name, reason=file_refusal.detail))

    if not accepted:
        assert first_refusal is not None
        raise HTTPException(first_refusal.status_code, first_refusal.detail)

    batch_id = str(uuid.uuid4())
    written = await write_upload(request.app.state.redis, batch_id, parser, accepted)
    return UploadResponse(
        batch_id=batch_id,
        documents=[
            DocumentItem(document_id=doc_id, filename=name, status=Status.QUEUED)
            for doc_id, name in written
        ],
        rejected=rejected,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(request: Request, document_id: str) -> DocumentResponse:
    document = await read_document(request.app.state.redis, document_id)
    if document is None:
        raise HTTPException(404, "document not found")
    return document


@router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(request: Request, batch_id: str) -> BatchResponse:
    batch = await read_batch(request.app.state.redis, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    return batch
