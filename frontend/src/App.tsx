import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { getBatch, getDocument, uploadDocuments } from "./api";
import type {
  DocumentItem,
  DocumentResponse,
  ParserName,
  RejectedFile,
  Status,
} from "./api";

const POLL_MS = 2000;

interface Row {
  id: string;
  filename: string;
  status: Status;
  detail: DocumentResponse | null;
}

function isFinal(status: Status): boolean {
  return status === "done" || status === "failed";
}

function DetailView({ doc }: { doc: DocumentResponse }) {
  return (
    <div>
      {doc.status === "failed" && (
        <p>
          Error: {doc.error_code} - {doc.error_message}
        </p>
      )}
      {doc.warnings.map((warning, i) => (
        <p key={i}>Warning: {warning}</p>
      ))}
      {doc.status === "done" && (
        <>
          {(doc.pages ?? []).map((page) => (
            <div key={page.page_number}>
              <strong>Page {page.page_number}</strong>
              <pre>{page.content}</pre>
            </div>
          ))}
          {doc.summary_status === "done" && <p>Summary: {doc.summary}</p>}
          {doc.summary_status === "failed" && <p>{doc.summary_error}</p>}
          {doc.summary_status === "skipped" && <p>no summary</p>}
        </>
      )}
    </div>
  );
}

export default function App() {
  const [files, setFiles] = useState<File[]>([]);
  const [parser, setParser] = useState<ParserName>("pypdf");
  const [isBusy, setIsBusy] = useState(false);
  const [uploadNote, setUploadNote] = useState("");
  const [pollNote, setPollNote] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [rejected, setRejected] = useState<RejectedFile[]>([]);
  const pollRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | undefined>(undefined);

  function stopPolling(): void {
    pollRef.current?.abort();
    pollRef.current = null;
    window.clearTimeout(timerRef.current);
  }

  useEffect(() => stopPolling, []);

  function startPolling(batchId: string, items: DocumentItem[]): void {
    stopPolling();
    const ctl = new AbortController();
    pollRef.current = ctl;
    const loaded = new Set<string>();
    const ids = items.map((item) => item.document_id);

    const tick = async (): Promise<void> => {
      const batch = await getBatch(batchId, ctl.signal);
      if (ctl.signal.aborted) return;
      if (!batch.ok) {
        if (batch.status === 404) {
          setPollNote("The batch expired, upload again.");
          return;
        }
        setPollNote(`Cannot read the batch: ${batch.message}. Trying again.`);
        timerRef.current = window.setTimeout(() => void tick(), POLL_MS);
        return;
      }
      setPollNote("");
      const statuses = new Map(batch.data.documents.map((d) => [d.document_id, d.status]));
      setRows((old) =>
        old.map((row) => {
          const status = statuses.get(row.id);
          return status === undefined ? row : { ...row, status };
        }),
      );
      for (const id of ids) {
        const status = statuses.get(id);
        if (status === undefined || !isFinal(status) || loaded.has(id)) continue;
        const doc = await getDocument(id, ctl.signal);
        if (ctl.signal.aborted) return;
        if (!doc.ok) continue; // try again on the next tick
        loaded.add(id);
        setRows((old) =>
          old.map((row) =>
            row.id === id ? { ...row, status: doc.data.status, detail: doc.data } : row,
          ),
        );
      }
      if (ids.every((id) => loaded.has(id))) return;
      timerRef.current = window.setTimeout(() => void tick(), POLL_MS);
    };

    timerRef.current = window.setTimeout(() => void tick(), POLL_MS);
  }

  function onFiles(event: ChangeEvent<HTMLInputElement>): void {
    setFiles(Array.from(event.target.files ?? []));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (files.length === 0 || isBusy) return;
    setIsBusy(true);
    setUploadNote("");
    const result = await uploadDocuments(files, parser);
    setIsBusy(false);
    if (!result.ok) {
      setUploadNote(`Upload refused: ${result.message}`);
      return;
    }
    stopPolling();
    setPollNote("");
    setRejected(result.data.rejected);
    setRows(
      result.data.documents.map((d) => ({
        id: d.document_id,
        filename: d.filename,
        status: d.status,
        detail: null,
      })),
    );
    setUploadNote(
      `Uploaded ${result.data.documents.length} file(s), ${result.data.rejected.length} rejected.`,
    );
    startPolling(result.data.batch_id, result.data.documents);
  }

  return (
    <main>
      <h1>Document upload</h1>
      <form onSubmit={(e) => void onSubmit(e)}>
        <p>
          <label htmlFor="files">PDF files </label>
          <input id="files" type="file" multiple onChange={onFiles} />
        </p>
        <p>
          <label htmlFor="parser">Parser </label>
          <select
            id="parser"
            value={parser}
            onChange={(e) => setParser(e.target.value as ParserName)}
          >
            <option value="pypdf">pypdf</option>
            <option value="gemini">gemini</option>
          </select>
        </p>
        <button type="submit" disabled={files.length === 0 || isBusy}>
          Upload
        </button>
      </form>
      <div role="status">
        {uploadNote && <p>{uploadNote}</p>}
        {pollNote && <p>{pollNote}</p>}
      </div>
      {rejected.length > 0 && (
        <ul aria-label="Rejected files">
          {rejected.map((r, i) => (
            <li key={i}>
              {r.filename}: {r.reason}
            </li>
          ))}
        </ul>
      )}
      <ul aria-label="Documents">
        {rows.map((row) => (
          <li key={row.id}>
            <strong>{row.filename}</strong> - status: {row.status}
            {row.detail !== null && <DetailView doc={row.detail} />}
          </li>
        ))}
      </ul>
    </main>
  );
}
