from enum import StrEnum

from pydantic import BaseModel


class Status(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    SUMMARIZING = "summarizing"
    DONE = "done"
    FAILED = "failed"


class SummaryStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ParserName(StrEnum):
    PYPDF = "pypdf"
    GEMINI = "gemini"
    GPT5MINI = "gpt5mini"


class PageContent(BaseModel):
    page_number: int
    content: str


class DocumentItem(BaseModel):
    document_id: str
    filename: str
    status: Status


class RejectedFile(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    batch_id: str
    documents: list[DocumentItem]
    rejected: list[RejectedFile]


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    parser: ParserName
    status: Status
    warnings: list[str]
    error_code: str | None
    error_message: str | None
    pages: list[PageContent] | None
    summary: str | None
    summary_status: SummaryStatus
    summary_error: str | None
    created_at: str
    updated_at: str


class BatchResponse(BaseModel):
    batch_id: str
    documents: list[DocumentItem]


class QueueMetrics(BaseModel):
    length: int
    pending: int
