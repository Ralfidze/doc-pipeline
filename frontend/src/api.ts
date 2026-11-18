export type Status = "queued" | "parsing" | "summarizing" | "done" | "failed";
export type SummaryStatus = "pending" | "done" | "failed" | "skipped";
export type ParserName = "pypdf" | "gemini";

export interface DocumentItem {
  document_id: string;
  filename: string;
  status: Status;
}

export interface RejectedFile {
  filename: string;
  reason: string;
}

export interface UploadResponse {
  batch_id: string;
  documents: DocumentItem[];
  rejected: RejectedFile[];
}

export interface BatchResponse {
  batch_id: string;
  documents: DocumentItem[];
}

export interface PageContent {
  page_number: number;
  content: string;
}

export interface DocumentResponse {
  document_id: string;
  filename: string;
  parser: ParserName;
  status: Status;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  pages: PageContent[] | null;
  summary: string | null;
  summary_status: SummaryStatus;
  summary_error: string | null;
  created_at: string;
  updated_at: string;
}

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; message: string };

async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
    }
  } catch {
    // The body is not JSON (for example a proxy page). Use the status code.
  }
  return `Request failed with status ${res.status}`;
}

async function request<T>(url: string, init: RequestInit): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    return { ok: false, status: 0, message: "Network error" };
  }
  if (!res.ok) {
    return { ok: false, status: res.status, message: await errorMessage(res) };
  }
  try {
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, status: res.status, message: `Bad reply, status ${res.status}` };
  }
}

export function uploadDocuments(
  files: readonly File[],
  parser: ParserName,
): Promise<ApiResult<UploadResponse>> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  form.append("parser", parser);
  return request<UploadResponse>("/api/documents", { method: "POST", body: form });
}

export function getBatch(
  batchId: string,
  signal: AbortSignal,
): Promise<ApiResult<BatchResponse>> {
  return request<BatchResponse>(`/api/batches/${encodeURIComponent(batchId)}`, { signal });
}

export function getDocument(
  documentId: string,
  signal: AbortSignal,
): Promise<ApiResult<DocumentResponse>> {
  return request<DocumentResponse>(`/api/documents/${encodeURIComponent(documentId)}`, {
    signal,
  });
}
