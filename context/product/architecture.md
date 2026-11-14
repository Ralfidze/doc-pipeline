# System Architecture Overview: Doc Pipeline

- **Inputs:** `context/sources/task.md` (the task text), `context/product/product-definition.md`, the `/awos:architecture` request.
- **Codebase state:** The repository has no application code. The only stack evidence is `.env.example` and `.gitignore`.

**Labels:** An item with no label comes from the task text or from the `/awos:architecture` request. An item with **(Decision)** is a choice that the product owner confirmed in the review of this document. An item with **(Assumption)** is a choice that these sources do not state and that the product owner did not confirm. A file path in parentheses is evidence from the repository.

**Limit:** The system has three services (`redis`, `api`, `worker`) and one optional service (`frontend`). Do not add other services, stores or queues.

---

## 1. Application & Technology Stack

- **Language:** Python 3.12.
  - Reason: The task text requires Python 3.12+. One fixed version keeps the image and the lockfile the same for all runs.
- **Dependency tool:** `uv` with a committed lockfile.
  - Reason: `uv` installs from the lockfile quickly, so the image build is short and repeatable.
- **API framework:** FastAPI on `uvicorn`, one process. **(Assumption: `uvicorn`, one process)**
  - Reason: The task text requires FastAPI. The endpoints do only Redis I/O, so async handlers serve concurrent requests in one process.
- **Package layout:** One package `app/` with five modules.
  - `app/api/`: HTTP endpoints and request checks.
  - `app/worker/`: the stream consumer and the processing flow.
  - `app/parsers/`: the parser protocol and its implementations.
  - `app/summarizer/`: the Gemini summary call.
  - `app/storage/`: all Redis keys and stream commands.
  - Reason: `api` and `worker` share the storage code and the models. One package gives one image and no version difference between them.
- **Parser protocol:** `parse(pdf_bytes) -> ParseResult`. **(Assumption: the return type)**
  - `ParseResult` has `pages: list[PageContent]` and `warnings: list[str]`.
  - The request gave `parse(pdf_bytes) -> list[PageContent]`. The return type changed because of the `gemini_page_limit` warning (section 5). The warning is for the document, and a list of pages cannot hold it.
  - `PageContent` has `page_number` and `content`. **(Assumption: field names)**
  - `parse` is an `async` function. **(Assumption)**
  - A parser reports a failure with an exception that has an `error_code`. **(Assumption)**
  - Reason: One protocol lets the worker call each parser in the same way. A list of pages gives the per-page content that the task text requires.
- **Parser implementations:**
  - `PyPdfParser`: text only, with `pypdf`. It runs in a thread, because `pypdf` blocks the event loop. **(Assumption: thread)**
  - `GeminiParser`: markdown, with `gemini-3.1-flash-lite`.
  - `Gpt5MiniParser`: a stub. It makes no external call.
  - Reason for the stub: The task text names this parser value. The stub keeps the value in the protocol, so a later version adds the call without an API change.
- **Parser values:** `pypdf`, `gemini`, `gpt-5-mini`. **(Assumption: the strings)**
  - The API rejects `gpt-5-mini` before it writes to the queue. This agrees with the product definition.
- **Redis client:** `redis-py` with `redis.asyncio`. **(Assumption)**
  - Reason: It supports Redis Streams and asyncio, and it is the standard Python client.
- **Tests:** `pytest`. (`.gitignore`: `.pytest_cache/`)
  - Tests use `fakeredis` and a fake Gemini client. **(Assumption)**
  - Reason: Tests run without a network and without an API key.
- **Frontend (optional, stretch goal):** React with TypeScript, built with Vite, one page with API polling. **(Assumption: Vite)**
  - Reason: The task text permits React and API polling. Polling uses the same endpoints as `curl`, so the backend does not change.

### API surface

| Endpoint | Function | Source |
| --- | --- | --- |
| `POST /documents` | Accepts one or more PDF files and one parser value. Replies `202` with a batch identifier and one identifier for each document. | **(Assumption)** |
| `GET /documents/{id}` | Returns the status, the pages and the summary of one document. | **(Assumption)** |
| `GET /batches/{id}` | Returns the status of each document of one upload request. | **(Assumption)** |
| `GET /health` | Pings Redis. | Request |
| `GET /metrics/queue` | Returns `XLEN` and `XPENDING` of the queue. | Request |

- The API accepts a file only if it starts with `%PDF-` and is 20 MB or smaller. **(Assumption)**
  - Reason: Redis keeps the PDF bytes in memory. The check rejects a wrong file before it uses queue and Gemini capacity.

---

## 2. Data & Persistence

- **Store:** Redis 7 is the only store.
  - Reason: The task text requires Redis for the result and for the queue. One store keeps the system small.
- **Keys:**

| Key | Type | Content | TTL |
| --- | --- | --- | --- |
| `doc:{id}` | hash | Status and metadata of one document. | none **(Assumption)** |
| `doc:{id}:pdf` | string (bytes) | The uploaded PDF file. | 3600 s |
| `doc:{id}:pages` | string (JSON text) | The list of `PageContent` in page order. | none **(Assumption)** |
| `doc:{id}:summary` | string | The summary of the document. | none **(Assumption)** |
| `batch:{id}` | set | The document identifiers of one upload request. | none **(Assumption)** |

- **Reasons for the keys:**
  - `doc:{id}` is a hash, because the worker updates single fields such as the status.
  - `doc:{id}:pdf` has a TTL, because the bytes are large and the worker needs them only for the parse.
  - `doc:{id}:pages` and `doc:{id}:summary` are separate keys, because the worker writes them at different times.
  - `batch:{id}` lets the reviewer read all documents of one upload request with one call.
- **JSON text:** `doc:{id}:pages` is JSON text in a string key. The system does not use the RedisJSON module. **(Assumption)**
- **Fields of `doc:{id}`:** `status`, `parser`, `filename`, `batch_id`, `page_count`, `warnings`, `error_code`, `error`, `error_step`, `created_at`, `updated_at`. **(Assumption, except `error_code`)**
  - `error_code` is a short fixed string, for example `gemini_bad_response`. **(Decision)**
  - `warnings` is a JSON list of short fixed strings, for example `["gemini_page_limit"]`. **(Assumption: the field name and format)**
  - `GET /documents/{id}` returns `warnings` and `error_code`. **(Assumption)**
- **Status values:** `queued`, `processing`, `done`, `failed`. **(Assumption)**
- **Identifiers:** UUID4. **(Assumption)**
- **Upload write:** The API writes the hash, the PDF bytes, the set member and the stream message in one `MULTI` transaction. **(Assumption)**
  - Reason: A document never exists without its queue message.
- **Expired PDF:** If `doc:{id}:pdf` does not exist when the worker reads the message, the attempt fails. **(Assumption)**
- **Persistence:** No Redis persistence settings and no named volume. **(Assumption)**
  - Reason: The result must stay only for the demo run.

---

## 3. Queue & Async Processing

- **Queue:** Redis stream `documents:ingest` with consumer group `workers`.
  - Reason: The task text requires Redis Streams as the queue. A consumer group gives each message to one worker and tracks unacknowledged messages.
- **Message fields:** `document_id`, `parser`, `attempt`.
  - Reason: The message holds no PDF bytes, so the stream stays small. `attempt` lets the worker count failures without a second store.
- **Read loop:** asyncio, `XREADGROUP GROUP workers {consumer} COUNT 4 BLOCK 5000 STREAMS documents:ingest >`.
  - Reason for `COUNT 4`: One worker processes a maximum of four documents at the same time. This limits memory use.
  - Reason for `BLOCK 5000`: The worker waits in Redis and does not poll. It wakes every 5 s, so it can stop cleanly.
  - Reason for asyncio: The work is mostly I/O (Redis and Gemini calls).
  - The worker processes the messages of one read concurrently, then it reads again. **(Assumption)**
- **Group setup:** `api` and `worker` each create the group at start with `XGROUP CREATE documents:ingest workers 0 MKSTREAM`. They ignore the `BUSYGROUP` error. **(Assumption)**
  - Reason: The start order of the services does not matter, and `XPENDING` always has a group to read.
- **Consumer name:** The container hostname. **(Assumption)**
  - Reason: `docker compose up --scale worker=N` works without a config change.

### Processing flow of one message

1. The worker sets `status` to `processing`.
2. If `doc:{id}:pages` does not exist, the worker reads `doc:{id}:pdf`, calls the parser and writes `doc:{id}:pages`.
3. The worker builds the markdown from the pages and calls the summarizer.
4. The worker writes `doc:{id}:summary` and sets `status` to `done`.
5. The worker sends `XACK`.

- Step 2 skips the parse on a later attempt if the pages exist. **(Assumption)**
  - Reason: A summary failure does not remove the parsed content and does not repeat a Gemini parse.

### Failures

- **Retry:** If an attempt fails and `attempt` is less than 3, the worker adds a new message with `attempt + 1`. Then it acknowledges the old message. **(Assumption: the mechanism)**
- **Dead letter:** After 3 failed attempts, the worker adds the message to stream `documents:dead` and sets `status` to `failed`.
  - The dead-letter message also has an `error` field. **(Assumption)**
  - `error_step` in `doc:{id}` is `parse` or `summary`. If it is `summary`, the pages stay readable. **(Assumption)**
  - Reason: A document that always fails does not block the queue, and the failed message stays available for a check.
- **Bad Gemini response:** If `GeminiParser` fails with `gemini_bad_response` (section 5), the worker sets `status` to `failed` and `error_code` to `gemini_bad_response`. **(Decision)**
  - The worker makes no more message attempts for this document. **(Assumption)**
  - The worker adds the message to `documents:dead` and acknowledges it. **(Assumption)**
  - Reason: The parser already made its one retry. One rule stays true: each `failed` document has one message in `documents:dead`.
- **Crashed worker:** At start, the worker claims messages that are pending for more than 60 s with `XAUTOCLAIM`. **(Assumption)**
  - Reason: A message that a stopped worker did not acknowledge is processed again.
- **Delivery:** Delivery is at-least-once. The processing only overwrites keys, so a second run of one message is safe.

---

## 4. Infrastructure & Deployment

- **Runtime:** Docker Compose on one machine. No cloud provider.
  - Reason: The task text requires all components in Docker Compose.
- **Services:**

| Service | Image | Function |
| --- | --- | --- |
| `redis` | `redis:7-alpine` **(Assumption: the tag)** | Store and queue. |
| `api` | application image | FastAPI on port 8000. **(Assumption: the port)** |
| `worker` | application image | Stream consumer. |
| `frontend` (optional) | `nginx` with the built page **(Assumption)** | The stretch-goal website. |

- **One image for `api` and `worker`:** The two services use one image with different commands.
  - Reason: One build, and the two services always have the same code version.
- **Application image:** `python:3.12-slim` with dependencies from `uv sync --frozen`. **(Assumption: the base image)**
- **Start order:** `redis` has a healthcheck. `api` and `worker` start after `redis` is healthy. **(Assumption)**
- **Optional `frontend`:** The service is in the Compose profile `frontend`. **(Assumption)**
  - `nginx` sends `/api` requests to the `api` service. **(Assumption)**
  - Reason: The default `docker compose up` starts only the backend. The proxy removes the need for CORS settings.
- **Configuration:** Environment variables from `.env`. (`.env.example`, `.gitignore`)
  - `GEMINI_API_KEY`: the Gemini API key. (`.env.example`)
  - `GEMINI_MODEL_ID`: the model id, default `gemini-3.1-flash-lite`. (`.env.example`)
  - `REDIS_URL`: default `redis://redis:6379/0`. (`.env.example`)
  - Reason: `.env` is not in git, so the API key stays out of the repository.

---

## 5. External Services & APIs

- **Gemini client:** `google-genai`, with its async client.
  - Reason: It is the current Google SDK for the Gemini API. The async client fits the asyncio worker.
- **Model id:** The value of `GEMINI_MODEL_ID` after `strip()`.
  - Reason: The task text gives the id with spaces around it. A space in the id makes the API call fail.
- **Concurrency limit:** A semaphore of 2 for all Gemini calls in one worker process.
  - Reason: The free tier has low rate limits. The semaphore reduces `429` errors.
  - The limit applies to each worker process separately.
- **Call retry:** Retry on HTTP `429` and `503` with backoff 2 s, 4 s, 8 s, maximum 3 attempts.
  - The client makes the first call and a maximum of 3 retries. The waits are 2 s, 4 s and 8 s. **(Decision)**
  - Reason: `429` and `503` are temporary errors. Other errors do not go away with a retry.
  - The call retry occurs in one message attempt. If the call still fails, the message attempt fails (section 3).
- **Gemini parser call:** One request for each document. The request has the PDF bytes and asks for JSON with one markdown item for each page. **(Decision)**
  - Reason: One request for each document uses less of the free-tier request limit than one request for each page.
- **Gemini page limit:** If the PDF has more than 25 pages, `GeminiParser` sends only the first 25 pages and adds the warning `gemini_page_limit`. **(Decision)**
  - `GeminiParser` counts the pages with `pypdf` and writes the first 25 pages to a new PDF. **(Assumption)**
  - The document can still get `status` `done`. The pages and the summary cover only the first 25 pages.
  - Reason: The limit keeps the request and the JSON response small, so the response is less likely to exceed the output limit of the model.
- **Gemini response check:** The JSON response must have the same number of items as the pages sent. **(Decision)**
  - If the count differs, the parser retries the request once. **(Decision)**
  - If the count differs again, the parser fails with `error_code` `gemini_bad_response`. The worker then sets `status` to `failed` (section 3). **(Decision)**
  - A response that is not valid JSON counts as a wrong count. **(Assumption)**
  - This retry is separate from the call retry on `429` and `503`.
  - Reason: A wrong count means that the page numbers of the result are not reliable. One retry is sufficient for a random model error and keeps the request use low.
- **Summary call:** `gemini-3.1-flash-lite` for each parser.
  - Input: the first 20,000 and the last 10,000 characters of the markdown.
  - If the markdown has 30,000 characters or fewer, the input is the full markdown. **(Assumption)**
  - Reason: The limit keeps the token use and the call time bounded. The start and the end of a document usually hold the introduction and the conclusion.
- **GPT-5 Mini:** No external call in this version. The product definition puts this parser out of scope.

---

## 6. Observability & Monitoring

- **Logs:** JSON lines on stdout. Each log line of the processing flow has `document_id`.
  - The Python standard `logging` module with a JSON formatter. **(Assumption)**
  - Reason: The reviewer follows one document through `api` and `worker` with `docker compose logs` and a filter on `document_id`.
- **`GET /health`:** Pings Redis. It returns `200` if Redis answers and `503` if not. **(Assumption: the status codes)**
  - Reason: The API cannot work without Redis, so the ping is a sufficient health check.
- **`GET /metrics/queue`:** Returns `XLEN` of `documents:ingest` and the `XPENDING` summary of group `workers`.
  - It also returns `XLEN` of `documents:dead`. **(Assumption)**
  - Reason: The reviewer sees with `curl` that the queue takes messages and that the worker acknowledges them.
- **No other monitoring tools.**
  - Reason: The limit of this architecture permits no other services.
