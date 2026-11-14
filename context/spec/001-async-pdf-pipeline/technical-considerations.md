# Technical Specification: Async PDF Pipeline

- **Functional Specification:** [functional-spec.md](./functional-spec.md)
- **Status:** Draft
- **Author(s):** Rail Zakirov

**Labels:** An item with no label comes from the product owner's `/awos:tech` request, the functional spec or `context/product/architecture.md`. An item with **Decision:** is a choice that the product owner confirmed in the review of this document. An item with **Assumption:** is a choice of this document that the product owner did not confirm.

**Frozen contracts:** Sections 2.2 (API), 2.3 (Redis schema) and 2.4 (worker loop) are contracts. The implementation agents must not change them. If a contract cannot be met, the agent stops and reports it.

---

## 1. High-Level Technical Approach

The system has three services in Docker Compose: `redis`, `api` and `worker`. `api` and `worker` use one image and one Python package `app/`.

1. `api` (FastAPI, one `uvicorn` process) checks an upload, writes each document to Redis and adds one message for each document to the stream `documents:ingest`. All writes of one upload go in one Redis transaction. Then `api` replies `202`.
2. `worker` (asyncio) reads the stream through the consumer group `workers`. For each message it parses the PDF, writes the pages, asks Gemini for one summary, and writes the result.
3. The reviewer polls `api`, which reads the status and the result from Redis.

Redis 7 is the only store and the only queue. Each key of a document expires 3600 s after the upload.

This document replaces some Assumptions of `context/product/architecture.md`. Section 2.9 lists them.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Components and files

| Path | Responsibility | Owner agent |
| --- | --- | --- |
| `app/settings.py` | Settings from environment variables (section 2.7). An empty value is the same as "not set". | `python-backend` |
| `app/models.py` | Shared Pydantic v2 models and string enums: `Status`, `SummaryStatus`, `ParserName`, `PageContent`, the response models. | `python-backend` |
| `app/log.py` | JSON log lines on stdout. Each line of the processing flow has `document_id`. | `python-backend` |
| `app/api/main.py` | App factory, lifespan (one shared Redis client, group setup), the `503` handler. | `python-backend` |
| `app/api/documents.py` | `POST /documents`, `GET /documents/{document_id}`, `GET /batches/{batch_id}`. | `python-backend` |
| `app/api/uploads.py` | Pure functions for the upload checks of functional spec section 2.2. | `python-backend` |
| `app/api/ops.py` | `GET /health`, `GET /metrics/queue`. | `python-backend` |
| `app/storage/client.py` | Redis client factory, `ensure_group`. | `python-backend` |
| `app/storage/documents.py` | Key names, the upload transaction, reads, status writes, page and summary writes. | `python-backend` |
| `app/storage/queue.py` | Stream commands: add, read, acknowledge, retry, dead letter, claim, queue numbers. | `stream-worker` |
| `app/gemini.py` | One Gemini client: semaphore, call retry, test mode (section 2.6). | `stream-worker` |
| `app/parsers/base.py` | Parser protocol, `ParseResult`, error types. | `stream-worker` |
| `app/parsers/pdf.py` | Open and check a PDF with `pypdf` (password, damage). All parsers use it. | `stream-worker` |
| `app/parsers/pypdf_parser.py`, `gemini_parser.py`, `gpt5mini_parser.py` | `PyPdfParser`, `GeminiParser`, `Gpt5MiniParser` (stub, no external call). | `stream-worker` |
| `app/summarizer/summarizer.py` | Summary input rule and the summary call. | `stream-worker` |
| `app/worker/__main__.py`, `app/worker/loop.py` | Entry point (`python -m app.worker`), the loop of section 2.4. | `stream-worker` |
| `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md` | Image, services, settings, `curl` commands. | `python-backend` |
| `tests/`, `demo.sh` | Smoke tests and the evidence run (section 4). | `python-test` |

Dependencies: `fastapi`, `uvicorn`, `python-multipart`, `pydantic-settings`, `redis`, `pypdf`, `cryptography` (for AES-encrypted PDFs), `google-genai`. Test dependencies: `pytest`, `httpx`.

### 2.2 API contract (frozen)

All replies are JSON. A field with no value is `null`. A list with no items is `[]`. No reply omits a field.

#### `POST /documents`

- Request: `multipart/form-data` with form fields `files` (one or more PDF) and `parser` (`pypdf` | `gemini` | `gpt5mini`).
- Reply `202`:

```json
{
  "batch_id": "<uuid4>",
  "documents": [{ "document_id": "<uuid4>", "filename": "a.pdf", "status": "queued" }],
  "rejected": [{ "filename": "b.txt", "reason": "not a PDF file" }]
}
```

- `documents` has one item for each accepted file, in upload order.
- **Assumption:** `rejected` is an addition to the request's contract. Functional spec AC-18 requires it. It is `[]` if the service accepted all files.
- Refusals (no batch, no document, no stream message). The checks run in this order:

| Order | Check | Status | `detail` |
| --- | --- | --- | --- |
| 1 | `parser` is absent or not one of the three names | `400` | `unknown parser` |
| 2a | `parser=gpt5mini` and `OPENAI_API_KEY` is not set | `400` | `parser not configured` |
| 2b | `parser=gpt5mini` and `OPENAI_API_KEY` is set | `400` | `parser not implemented` |
| 2c | `parser=gemini` and `GEMINI_API_KEY` is not set | `400` | `parser not configured` |
| 3 | no file | `400` | `no files` |
| 4 | more than 10 files | `400` | `too many files` |
| 5 | per file: the content does not start with `%PDF-` | `415` if no file is valid | `not a PDF file` |
| 6 | per file: larger than 20 x 1024 x 1024 bytes | `413` if no file is valid | `file too large` |

- Per-file checks: a file that fails check 5 or 6 goes to `rejected` with the `detail` text as `reason`. If no file is valid, the reply has the status and the `detail` of the first file in the upload.
- **Assumption:** The body of each refusal is `{"detail": "<message>"}`. This is the default FastAPI error shape.
- The service never replies `422`. Section 2.5 gives the rule.

#### `GET /documents/{document_id}`

- Reply `200`:

| Field | Type | Value |
| --- | --- | --- |
| `document_id` | string | UUID4. |
| `filename` | string | Name of the uploaded file. |
| `parser` | string | `pypdf` or `gemini`. |
| `status` | string | `queued`, `parsing`, `summarizing`, `done`, `failed`. |
| `warnings` | list of string | `no_text_layer`, `gemini_page_limit`. |
| `error_code` | string or null | Set when `status` is `failed` (section 2.4). |
| `error_message` | string or null | One sentence. Set when `status` is `failed`. |
| `pages` | list or null | `[{ "page_number": 1, "content": "..." }]`. Set when `status` is `summarizing` or `done`. `page_number` starts at 1. |
| `summary` | string or null | Set when `summary_status` is `done`. |
| `summary_status` | string | `pending`, `done`, `failed`, `skipped`. |
| `summary_error` | string or null | One sentence. Set when `summary_status` is `failed`. |
| `created_at` | string | ISO 8601, UTC. |
| `updated_at` | string | ISO 8601, UTC. |

- Reply `404` with `{"detail": "document not found"}` for an unknown or expired identifier. Any text is a valid identifier input; the service does not check its format.

#### `GET /batches/{batch_id}`

- Reply `200`: `{ "batch_id": "<uuid4>", "documents": [{ "document_id": "...", "filename": "...", "status": "..." }] }`, in upload order.
- Reply `404` with `{"detail": "batch not found"}` for an unknown or expired identifier.

#### `GET /health`

- Reply `200` with `{"redis": "ok"}` if a Redis `PING` succeeds.
- Reply `503` with `{"redis": "down"}` if it fails. **Assumption:** the `503` body.

#### `GET /metrics/queue`

- Reply `200` with `{"length": <int>, "pending": <int>}`.
- `length` is `XLEN documents:ingest`. `pending` is the pending count of `XPENDING documents:ingest workers`.

#### Redis failure

- Each address replies `503` with `{"detail": "data store unavailable"}` if a Redis command fails. `GET /health` uses its own body, as above.

### 2.3 Redis schema (frozen)

All keys of one document and its batch expire at the same moment: `expires_at` = upload time + 3600 s.

| Key | Type | Content |
| --- | --- | --- |
| `doc:{id}` | hash | The fields in the table below. |
| `doc:{id}:pdf` | string (bytes) | The uploaded file. |
| `doc:{id}:pages` | string (JSON text) | `[{"page_number": 1, "content": "..."}]`, in page order. |
| `doc:{id}:summary` | string | The summary text. |
| `batch:{id}` | set | The `document_id` values of one upload. |

Exact fields of the `doc:{id}` hash. A hash cannot hold `null`, so an empty string means "no value".

| Field | Value | Writer |
| --- | --- | --- |
| `status` | `queued`, `parsing`, `summarizing`, `done`, `failed` | api, worker |
| `parser` | `pypdf` or `gemini` | api |
| `filename` | Name of the uploaded file | api |
| `batch_id` | UUID4 of the batch | api |
| `seq` | Position of the file in the upload, from `0` | api |
| `warnings` | JSON list text, for example `["no_text_layer"]`; `[]` at upload | api, worker |
| `error_code` | Empty, or a code from section 2.4 | worker |
| `error_message` | Empty, or one sentence | worker |
| `summary_status` | `pending`, `done`, `failed`, `skipped`; `pending` at upload | api, worker |
| `summary_error` | Empty, or one sentence | worker |
| `created_at` | ISO 8601, UTC | api |
| `updated_at` | ISO 8601, UTC; changes with each write | api, worker |
| `expires_at` | Unix time in seconds, `created_at` + 3600 | api |

- **Assumption:** `seq` and `expires_at` are new fields. `seq` gives the upload order, because `batch:{id}` is a set. `expires_at` gives one expiry time for all keys.
- **Assumption:** The architecture fields `page_count` and `error_step` are removed. No reply uses them.

Stream `documents:ingest`, consumer group `workers`:

| Field | Value |
| --- | --- |
| `document_id` | UUID4 of the document |
| `parser` | `pypdf` or `gemini` |
| `attempt` | Number of failed attempts before this message. The API writes `0`. |

Dead-letter stream `documents:dead`: the fields `document_id`, `parser`, `attempt`, `error_code`, `error_message`.

Rules:

- **Upload write:** `api` writes the hash, the PDF bytes, the set member, the expiry of each key and the stream message of all accepted files in one `MULTI` transaction. This is one round trip.
- **Expiry on each write:** `SET` removes a TTL, and `HSET` on an expired key makes a key with no TTL. So each write of `api` and `worker` sets the expiry of the key again to `expires_at` (`EXPIREAT`, or `SET ... EXAT`) in the same transaction.
- **Group setup:** `XGROUP CREATE documents:ingest workers 0 MKSTREAM`, with the `BUSYGROUP` error ignored. `api` and `worker` run it at start. They run it again if a command fails with `NOGROUP`.
- **Decision:** The worker does not delete acknowledged messages. `length` counts all messages that the stream still holds, also the finished ones.
- **Assumption:** `XADD` to both streams uses `MAXLEN ~ 10000`, so a stream cannot grow without limit.
- **Batch read:** `SMEMBERS`, then the `filename`, `status` and `seq` of each member, sorted by `seq`. A member with no hash is left out. An empty or absent set gives `404`.

### 2.4 Worker loop (frozen)

The six steps are the product owner's. Each **Reading** says how a step applies where its literal text would break an acceptance criterion or an architecture decision.

1. **Read.** `XREADGROUP GROUP workers <consumer> COUNT 4 BLOCK 5000 STREAMS documents:ingest >`. The consumer name is the container hostname. The worker processes the messages of one read concurrently.
2. **Parse.** For each message: set `status` to `parsing`, read `doc:{id}:pdf`, split it into pages, run the parser per page, write `doc:{id}:pages`.
3. **Summarize.** Set `status` to `summarizing`, call Gemini, write `doc:{id}:summary`.
4. **Finish.** Set `status` to `done`, `XACK`.
5. **On error.** Increase `attempt`. If `attempt` < 3, `XACK` and `XADD` the message again. If `attempt` = 3, set `status` to `failed`, `XACK`, `XADD` to `documents:dead`.
6. **Claim.** Every 30 seconds: `XAUTOCLAIM` messages idle for 60 seconds.

Readings:

- **Before step 2, guard.** If `doc:{id}` does not exist, the document expired: `XACK` and stop. If `status` is `done` or `failed`, `XACK` and stop. Reason: delivery is at-least-once, and a final status must not change.
- **Step 2, "run the parser per page". Decision:** The worker calls `parser.parse(pdf_bytes)` one time for each document. The parser returns one item for each page. `PyPdfParser` reads the text page by page. `GeminiParser` sends one request for the document. Reason: the architecture Decision "one request for each document" with the 25-page limit and the item-count check (AC-14, AC-15) needs one call for each document.
- **Step 2, repeat run.** If `doc:{id}:pages` exists, the worker skips the read and the parse and goes to step 3. Reason: a repeat run after a worker stop (AC-10) must not repeat a Gemini parse.
- **Step 2, write.** The worker writes the pages and the `warnings` field in one transaction, then sets `status` to `summarizing`.
- **Step 3, no text.** If no page has text, the worker makes no Gemini call. It sets `summary_status` to `skipped` and goes to step 4 (AC-7).
- **Step 3, summary failure.** A summary failure is not an error of step 5. The worker sets `summary_status` to `failed` and `summary_error`, writes no summary, and goes to step 4 (AC-8, AC-24). The causes are: no `GEMINI_API_KEY`, `429` or `503` on all 4 calls, a different Gemini error, an empty answer.
- **Step 4, order.** The worker writes the summary and `summary_status` first, then `status` `done`, then `XACK`.
- **Step 5, `attempt`.** The message field `attempt` is the number of failed attempts. On an error: n = `attempt` + 1. If n < 3, the worker adds a new message with `attempt` = n. If n = 3, it sets `failed` and adds the message to `documents:dead`. A document gets 3 attempts at most. The `XACK` and the `XADD` go in one `MULTI` transaction.
- **Step 5, document errors. Decision:** An error that a new attempt cannot repair skips the attempts. The worker sets `status` to `failed`, adds the message to `documents:dead` and sends `XACK` immediately. These are the first three rows of the table below. Reason: AC-6 and AC-23 need the final status in 60 seconds, and the Gemini parser already made its one retry for AC-15.
- **Step 5, Redis down.** If Redis does not answer, the worker cannot `XACK`. The message stays pending, and step 6 or the start recovery takes it later.
- **Step 6, own task.** The claim runs as its own asyncio task, with `XAUTOCLAIM documents:ingest workers <consumer> 60000 0 COUNT 10`. A claimed message goes through steps 2 to 5 with its `attempt` unchanged.
- **Step 6, messages in process.** A message that the worker still processes after 60 s is also idle for Redis. The worker keeps a set of the message ids that it processes and ignores a claimed message that is in the set. Reason: without this rule, one document is processed two times at the same moment.
- **Start recovery. Decision:** At start, before the first read of step 1, the worker reads its own pending messages one time with `XREADGROUP ... STREAMS documents:ingest 0`. Reason: `docker compose stop` and `start` keep the hostname, so the consumer name is the same, and `>` never returns messages that are already pending. Without this read, a document waits 60 to 90 s for step 6, and AC-10 (120 s) can fail. Step 6 stays for a worker that comes back with a new name.

Failure table:

| Failure | Worker action | `status` | `error_code` | `summary_status` |
| --- | --- | --- | --- | --- |
| The PDF needs a password | fail immediately | `failed` | `encrypted` | `skipped` |
| The PDF is damaged or has no page | fail immediately | `failed` | `invalid_pdf` | `skipped` |
| Gemini gives a wrong item count 2 times | fail immediately | `failed` | `gemini_bad_response` | `skipped` |
| Gemini parse call fails on all 4 calls (`429`, `503`) | step 5 | `failed` after 3 attempts | `gemini_unavailable` | `skipped` |
| A different error in the parse step | step 5 | `failed` after 3 attempts | `processing_failed` | `skipped` |
| No page has text (`pypdf`) | not an error | `done` | empty | `skipped`, warning `no_text_layer` |
| More than 25 pages (`gemini`) | not an error | `done` | empty | from the summary, warning `gemini_page_limit` |
| The summary fails | not an error | `done` | empty | `failed` |

- **Assumption:** `processing_failed` is a new error code for an error that has no code of its own. The functional spec has no code for this case.
- Status order: `queued` → `parsing` → `summarizing` → `done`, or `parsing` → `failed`. The status never goes back.
- Concurrency: 4 messages at most in process in one worker. A semaphore of 2 limits all Gemini calls of one worker process.

### 2.5 Upload handler rules

- Declare `parser` as an optional plain string form field and `files` as an optional list. A required field, an enum or a `Literal` makes FastAPI reply `422`. Map any request validation error of this route to `400`.
- Do checks 1 to 4 before the handler reads file bytes.
- Per file: read the first 5 bytes for check 5, then compare the size for check 6. Do not trust the content type or the file name. Starlette keeps a large file part on disk, so a 21 MB file does not use 21 MB of memory before the check.
- The handler is `async`. It uses the one shared Redis client from the lifespan. It does no PDF parsing and no Gemini call. It makes one Redis round trip (section 2.3). Reason: AC-9 needs each of 10 parallel uploads to reply in less than 500 ms with one `uvicorn` process.
- One exception handler for Redis errors in `app/api/main.py` gives the `503` reply for all addresses. The Redis client uses short timeouts (1 s connect, 2 s read) and no long retry, so the `503` is fast.
- The Redis client does not decode responses, because `doc:{id}:pdf` holds bytes. `app/storage/` decodes text values.

### 2.6 Parsers, summarizer and Gemini client

- **Parser protocol:** `async parse(pdf_bytes) -> ParseResult`. `ParseResult` has `pages: list[PageContent]` and `warnings: list[str]`. A parser raises a document error with an `error_code` for the first three rows of the failure table.
- **PDF check (`app/parsers/pdf.py`):** Both parsers open the PDF with `pypdf` first, so `encrypted` and `invalid_pdf` are the same for each parser. A PDF that opens with an empty password is not `encrypted`. `pypdf` work runs in a thread, because it blocks the event loop.
- **`PyPdfParser`:** One item for each page, with the text of the page. If no page has text, it adds the warning `no_text_layer`.
- **`GeminiParser`:** If the PDF has more than 25 pages, it writes the first 25 pages to a new PDF with `pypdf` and adds the warning `gemini_page_limit`. It sends one request with the PDF bytes and asks for a JSON list with one markdown item for each page. If the answer is not valid JSON or the item count differs from the pages sent, it sends the request one more time. If the second answer is also wrong, it raises `gemini_bad_response`.
- **`Gpt5MiniParser`:** A stub that raises "not implemented". The API refuses `gpt5mini` before a message exists, so the worker never calls it.
- **Summarizer:** The input is the `content` of all pages, joined in page order. If the input has more than 30,000 characters, the input is its first 20,000 and its last 10,000 characters. An empty answer is a failure.
- **Gemini client (`app/gemini.py`):** One class for the parser and the summarizer, with three layers: the semaphore of 2, then the call retry, then the raw call.
  - Model id: `GEMINI_MODEL_ID` after `strip()`.
  - Call retry: on `429` and `503` only. The first call and 3 retries, with waits of 2 s, 4 s and 8 s. The client's own retry loop does this, not an SDK option.
  - **Assumption:** A timeout of 60 s for each request.
  - SDK: `google-genai`, async client. The specialist named the calls from memory. The implementer must check the call names, the error class for `429` and `503`, and the timeout unit against the current `google-genai` documentation before the first call.
- **Test mode:** `GEMINI_TEST_MODE` changes only the raw call, so the real call retry and the real item-count check still run.
  - `rate_limit`: each raw call fails with `429`. The summary fails after the waits of 2 s, 4 s and 8 s (AC-8).
  - `bad_page_count`: each page answer has one item more than the pages sent. Summary calls return a fixed text (AC-15).
  - The test mode makes no call to Google. It works with an empty `GEMINI_API_KEY`, except that `parser=gemini` still needs the key at upload (check 2c).

### 2.7 Settings

| Variable | Purpose | Default |
| --- | --- | --- |
| `REDIS_URL` | Redis address. Compose fixes it for `api` and `worker`. | `redis://redis:6379/0` |
| `GEMINI_API_KEY` | Gemini API key. Empty: no summary, and `parser=gemini` is refused. | empty |
| `GEMINI_MODEL_ID` | Gemini model id, used after `strip()`. | `gemini-3.1-flash-lite` |
| `OPENAI_API_KEY` | Only its presence is read, for the `gpt5mini` refusal message. | empty |
| `GEMINI_TEST_MODE` | `rate_limit` or `bad_page_count`. Empty: real Gemini calls. | empty |

**Assumption:** The limits (20 MB, 10 files, 25 pages, 3600 s, 3 attempts, the retry waits) are constants in code, not settings.

### 2.8 Docker Compose

- `redis`: `redis:7-alpine`, a healthcheck with `redis-cli ping`, no named volume, no published port.
- `api`: the application image, command `uvicorn app.api.main:app --host 0.0.0.0 --port 8000`, port `8000:8000`, starts after `redis` is healthy.
- `worker`: the same image, command `python -m app.worker`, starts after `redis` is healthy, a stop grace period of 30 s.
- `frontend`: not in this spec. The Compose file can keep a place for it in the profile `frontend`.
- Image: `python:3.12-slim`, dependencies from `uv sync --frozen --no-dev`. The dependency layer comes before the copy of `app/`.
- `api` and `worker` read `.env`. `docker compose up -d` makes the containers again after a change of `.env`.
- `.env.example` lists the five variables of section 2.7.

### 2.9 Differences from the saved documents

`context/product/architecture.md` (this document replaces these items; run `/awos:architecture` to update the file):

- Status values `queued`, `parsing`, `summarizing`, `done`, `failed`, not `processing`.
- Parser value `gpt5mini`, not `gpt-5-mini`. New variables `OPENAI_API_KEY` and `GEMINI_TEST_MODE`.
- Hash fields as in section 2.3: new `seq`, `error_message`, `summary_status`, `summary_error`, `expires_at`; removed `page_count`, `error`, `error_step`.
- All keys expire after 3600 s, not only `doc:{id}:pdf`.
- A summary failure ends as `done`, with no new message attempt and no dead-letter message.
- `attempt` starts at `0`. Document errors skip the attempts.
- The claim runs every 30 s, and the worker reads its own pending messages at start.
- Upload: per-file checks with `rejected`, 10 files at most, the codes `400`, `413`, `415`.
- Tests: smoke tests against the running stack, no `fakeredis`.

`functional-spec.md` (the request's API contract differs from two tables of the functional spec; run `/awos:spec` to amend it):

- `GET /documents/{id}`: the field is `error_message`, not `error`. The reply has no `batch_id`. The reply has `created_at` and `updated_at`.
- `POST /documents`: each item of `documents` also has `status`.
- New error code `processing_failed`.

No acceptance criterion changes.

---

## 3. Impact and Risk Analysis

### System Dependencies

- Google Gemini API (free tier) for the summary of each document and for the `gemini` parser.
- Docker Compose on the machine of the reviewer.
- No other system. The repository has no existing application code.

### Potential Risks & Mitigations

The three risks of the product owner. Each risk maps to one acceptance criterion.

| Risk | Mitigation | Criterion |
| --- | --- | --- |
| Gemini `429` | The call retry (4 calls, waits 2 s, 4 s, 8 s), the semaphore of 2, and the rule that a summary failure keeps the pages and ends as `done`. | AC-8 |
| PDF with a password | The common PDF check finds it during the parse. The document fails immediately with `encrypted`, with no retry. | AC-6 |
| Worker restart | No `XACK` before the final write, the read of own pending messages at start, the claim every 30 s, and the skip of the parse if the pages exist. | AC-10 |

Other risks from the specialist review:

| Risk | Mitigation |
| --- | --- |
| The claim takes a message that the worker still processes (more than 60 s). | The set of message ids in process (section 2.4). |
| A write removes the expiry of a key, so a document never expires (AC-26). | The expiry rule on each write (section 2.3). |
| FastAPI replies `422` where the spec needs `400`. | The declaration rule of section 2.5. |
| The `google-genai` call names and the model id `gemini-3.1-flash-lite` are not verified. | The implementer checks the documentation and makes one real call before the parser work. |
| `pypdf` can repair a cut-off file without an error, so AC-23 gives `done`. | Check with the cut-off sample file early. A PDF with no page is `invalid_pdf`. |
| Free-tier limits make AC-13 and AC-14 slow or unstable. | These tests run one after the other. The 120 s limit includes one full call retry. |
| A PDF near 20 MB can be too large for one Gemini request. | Accepted. The document fails with `processing_failed` after 3 attempts. |
| A message that stops the worker process each time is processed again without limit. | Accepted for this version. |

---

## 4. Testing Strategy

The strategy is the product owner's: smoke tests only, one pytest file for each slice, `httpx` against the running Compose stack, and `demo.sh` as the evidence run.

- **Smoke tests only.** One test for each acceptance criterion. No unit tests, no `fakeredis`, no in-process app. The lines about a fake Redis and a fake Gemini client in `.claude/agents/python-test.md` do not apply to this spec.
- **Layout:** `tests/conftest.py` and one pytest file for each implementation slice of `tasks.md`:

| Slice | File | Criteria |
| --- | --- | --- |
| 1, skeleton | `tests/test_slice1_skeleton.py` | AC-12, 25 |
| 2, API | `tests/test_slice2_api.py` | AC-1, 2, 4, 5, 9, 11, 16 to 22, 26, 27, 28 |
| 3, worker | `tests/test_slice3_worker.py` | AC-3, 6, 7, 8, 10, 13, 14, 15, 23, 24 |

- Slice 4 writes these tests and `demo.sh`. It has no pytest file of its own.

- **Fixtures:** the base address (`API_URL`, default `http://localhost:8000`), one `httpx` client, an upload helper, a poll helper, the sample files, a helper that runs `docker compose`.
- **Poll helper:** it asks every 2 s. The limit is 60 s for `pypdf` and 120 s for `gemini`, from the upload reply. On a timeout it fails the test with the last reply body.
- **Sample files:** the tests make them at run time; the repository holds no binary file. A small helper writes a text PDF as raw bytes. From it come the 30-page file, the file with no text ("scanned"), the cut-off file (first half of the bytes) and the 21 MB file (valid start plus padding). `pypdf` makes the password file. **Assumption:** a file with empty pages is sufficient for `no_text_layer`; it has no real image.
- **Stack modes:** some criteria need a different `.env`. A test reads `STACK_MODE` and skips itself if the mode is not its own. `demo.sh` starts the stack again for each mode.

| `STACK_MODE` | `.env` | Criteria |
| --- | --- | --- |
| `normal` | a real `GEMINI_API_KEY`, no `OPENAI_API_KEY` | all others, with AC-5 |
| `no_gemini_key` | empty `GEMINI_API_KEY` | AC-16, 24 |
| `rate_limit` | `GEMINI_TEST_MODE=rate_limit`, a dummy `OPENAI_API_KEY` | AC-8, 10, 28 |
| `bad_page_count` | `GEMINI_TEST_MODE=bad_page_count`, a `GEMINI_API_KEY` | AC-15 |

- **AC-10 without a real key. Assumption:** the test runs in the mode `rate_limit` with `parser=pypdf`. The document stays in `summarizing` for 14 s (the retry waits). The test stops the worker in that time, starts it again, and expects `done` in 120 s. The summary fails in this mode, which AC-10 permits.
- **AC-26:** the test does not wait 61 minutes. It reads the TTL of each key of a new document with `docker compose exec redis redis-cli TTL` and expects a value from 1 to 3600. This is a partial check.
- **AC-9:** 10 threads, each upload measured with the `httpx` elapsed time. This is near to, but not the same as, the total time of `curl`.
- **Tests that stop a service** (AC-10, AC-25) have the marker `destructive`. Each one starts the service again in a finalizer and waits for `GET /health`. They run last in their mode.
- **Real Gemini calls:** AC-3 (the summary), AC-13 and AC-14 need a real key. `demo.sh` stops with an error if `GEMINI_API_KEY` is empty in the mode `normal`.
- **Evidence run `demo.sh`:**
  1. Save `.env`. Restore it at exit, also after a failure.
  2. Build and start the stack. Wait for `GET /health`.
  3. Run pytest in the mode `normal`, then the `destructive` tests.
  4. Run pytest in each other mode, with a new start of the stack for each.
  5. Print one line for each step and `ALL STEPS PASSED` at the end. Stop at the first failure with a non-zero exit code. Write the output to a log file.
- **RED validation.** The agent template asks for a test that fails before its change. The smoke tests come after the implementation slices, so the test engineer uses a negative control: run `tests/test_slice3_worker.py` with the worker stopped and record the failures, then start the worker and record the pass.
- **`demo.sh` with public files.** Before the pytest runs, `demo.sh` downloads 3 public PDF files, uploads them with `curl`, polls, and prints the status, the page count and the summary of each document.
