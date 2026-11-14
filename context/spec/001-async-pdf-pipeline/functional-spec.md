# Functional Specification: Async PDF Pipeline

- **Topic:** A reviewer uploads PDF files, selects a parser, and polls for the per-page content and one summary of each document.
- **Status:** Draft
- **Author:** Rail Zakirov

---

## 1. Overview and Rationale (The "Why")

A reviewer must check an async document service in a short time. The processing of a PDF file can take from seconds to minutes. If an upload waits for the processing, the reviewer cannot see that the service is async and cannot send uploads in parallel.

With this feature, the reviewer sends PDF files and a parser name and gets identifiers immediately. Then the reviewer asks for the status until the per-page content and the summary are ready. If a document has a problem, the reviewer sees a fixed error code or a fixed warning.

The user of this feature is one reviewer who works with `curl`. For this user, the visible surface is the HTTP interface: the addresses, the status codes and the field names in the replies. This specification names them. It does not describe how the service works inside.

The feature is a success if each acceptance criterion passes. Each criterion is one test scenario that the reviewer does with `curl`.

AC-1 to AC-12 come from the product owner. AC-13 to AC-28 have the label "(added)". They cover behavior that the product owner decided earlier and cases that the first twelve criteria leave open.

### Terms

- **Reviewer:** The person who tests the service with `curl`.
- **Upload:** One `POST /documents` request with one or more files and one parser name.
- **Document:** One uploaded file. Each document has a `document_id`.
- **Batch:** All documents of one upload. Each batch has a `batch_id`.
- **Parser:** The method that reads the pages of a document. The parser names are `pypdf`, `gemini` and `gpt5mini`.
- **Page:** One page of a document in the result. A page has a `page_number`, which starts at 1, and a `content`.
- **Summary:** One text about the full document. Google Gemini writes the summary for each parser. Each text that is not empty is a valid summary.
- **Worker:** The background service that processes the documents. There is one worker. The reviewer can stop it and start it.
- **Data store:** The service that keeps the documents and the results.
- **Final status:** `done` or `failed`. A document does not change after it has a final status.

### Test setup

- The reviewer starts the service with `docker compose up -d` in the project folder.
- The service listens at `http://localhost:8000`. Each address in this specification starts there.
- The settings `GEMINI_API_KEY` and `OPENAI_API_KEY` are lines in the file `.env`. A setting is "not set" if its line is empty or absent. After a change of the file, the reviewer runs `docker compose up -d` again.
- The reviewer stops and starts the worker with `docker compose stop worker` and `docker compose start worker`.
- The reviewer stops and starts the data store with `docker compose stop redis` and `docker compose start redis`.
- An upload is a form with one `files` field for each file and one `parser` field. Example: `curl -F "files=@a.pdf" -F "files=@b.pdf" -F "parser=pypdf" http://localhost:8000/documents`.
- The "sample text PDF" is a PDF file with text that a person can select, with 10 pages or fewer and a size of 1 MB or less.
- The setting `GEMINI_TEST_MODE` in the file `.env` makes Google Gemini fail for a test. With the value `rate_limit`, each request to Google Gemini fails with the rate-limit error `429`. With the value `bad_page_count`, each page answer of Google Gemini has a wrong number of pages. If the setting is not set, the service uses Google Gemini as usual.
- Time limits start at the upload reply. If a criterion gives no time limit, "reaches the status" means in 60 seconds or less with `parser=pypdf` and in 120 seconds or less with `parser=gemini`.
- A statement outside the criteria is a requirement too. The reviewer can check it in the replies of the criteria of the same section.

---

## 2. Functional Requirements (The "What")

### 2.1 Upload documents

The reviewer sends one or more PDF files and one parser name in one upload. The service replies before it processes a file.

The reply has one `batch_id` and a list `documents`. The list has one item for each accepted file, in the order of the files in the upload. Each item has a `document_id` and the `filename`. The reply also has a list `rejected`, which is empty if the service accepted all files (see section 2.2).

- **Acceptance Criteria:**
  - [ ] **AC-1.** When the reviewer sends `POST /documents` with 2 PDF files and `parser=pypdf`, then the reply has status code `202` and a body with one `batch_id` and 2 `document_id` values.
  - [ ] **AC-9.** When the reviewer starts 10 `curl` uploads of the sample text PDF at the same time, then each upload gets its `202` reply in less than 500 ms, measured as the total time that `curl` reports.

### 2.2 Refuse a wrong upload

The service checks an upload before it accepts it. A refused upload creates no batch and no document. The body of the reply to a refused upload contains a fixed message.

First, the service checks the upload as a whole. It does the checks in this order and refuses the upload with the first problem that it finds:

| Order | Problem | Status code | Message |
| --- | --- | --- | --- |
| 1 | The parser name is absent or not known. | `400` | "unknown parser" |
| 2 | The parser is not available. | `400` | "parser not configured" or "parser not implemented" |
| 3 | The upload has no file. | `400` | "no files" |
| 4 | The upload has more than 10 files. | `400` | "too many files" |

Then the service checks each file:

| Problem | Reason | Status code if no file is valid |
| --- | --- | --- |
| The file is not a PDF. | "not a PDF file" | `415` |
| The file is larger than 20 MB. | "file too large" | `413` |

The service accepts the valid files of an upload and rejects the others. The reply then has status code `202`. The list `rejected` has one item for each rejected file, with the `filename` and the `reason`.

If no file of an upload is valid, the service refuses the upload. The status code is the code of the first file in the upload, and the body contains the reason as the message.

A file is a PDF if its content starts with the PDF signature `%PDF-`. The service looks at the content, not at the name. A text file with a `.pdf` name is rejected, and so is an empty file.

A file can have 20 MB at most (20 x 1024 x 1024 bytes). A file of exactly this size is accepted.

The parser name `gpt5mini` is not available in this version. If `OPENAI_API_KEY` is not set, the message is "parser not configured". If `OPENAI_API_KEY` is set, the service still refuses `gpt5mini`, and the message is "parser not implemented".

- **Acceptance Criteria:**
  - [ ] **AC-4.** When the reviewer sends `POST /documents` with a `.txt` file, then the reply has status code `415`.
  - [ ] **AC-5.** Given `OPENAI_API_KEY` is not set, when the reviewer sends `POST /documents` with a PDF file and `parser=gpt5mini`, then the reply has status code `400` and the message "parser not configured".
  - [ ] **AC-18 (added).** When the reviewer sends `POST /documents` with one PDF file, one `.txt` file and `parser=pypdf`, then the reply has status code `202`, one `document_id`, and a `rejected` list with one item that has the name of the `.txt` file and the reason "not a PDF file".
  - [ ] **AC-19 (added).** When the reviewer sends `POST /documents` with a PDF file and `parser=word`, then the reply has status code `400` and the message "unknown parser".
  - [ ] **AC-20 (added).** When the reviewer sends `POST /documents` with one PDF file of 21 MB and `parser=pypdf`, then the reply has status code `413`.
  - [ ] **AC-27 (added).** When the reviewer sends `POST /documents` with 11 PDF files and `parser=pypdf`, then the reply has status code `400` and the message "too many files".
  - [ ] **AC-28 (added).** Given `OPENAI_API_KEY` is set, when the reviewer sends `POST /documents` with a PDF file and `parser=gpt5mini`, then the reply has status code `400` and the message "parser not implemented".

### 2.3 Follow one document

The reviewer asks for one document with its `document_id`. The service gives status code `200` for each status, also for `failed`.

| Status | Meaning |
| --- | --- |
| `queued` | The service accepted the document. The processing did not start. |
| `parsing` | The service reads the pages. |
| `summarizing` | The pages are ready. The service waits for the summary. |
| `done` | The processing ended. The pages are present. |
| `failed` | The service could not read the document. There are no pages. |

The reply always has the fields in this table. A field with no value is `null`.

| Field | Content |
| --- | --- |
| `document_id`, `batch_id`, `filename`, `parser` | The values from the upload. |
| `status` | One status from the table above. |
| `pages` | The list of pages. It has a value when the status is `summarizing` or `done`. |
| `summary` | The summary text. It has a value when `summary_status` is `done`. |
| `summary_status` | See section 2.7. |
| `summary_error` | The cause of a failed summary, as a sentence. It has a value when `summary_status` is `failed`. |
| `warnings` | A list of fixed words. The list is empty if there is no warning. |
| `error_code` | A fixed word. It has a value when the status is `failed`. |
| `error` | The cause of the failure, as a sentence. It has a value when the status is `failed`. |

The service sets no limit on how often the reviewer asks. The tests in this specification ask every 2 seconds.

The service keeps a document and its batch for 1 hour after the upload. From 61 minutes after the upload, the replies for the document and for its batch are the same as for an unknown identifier: status code `404`.

- **Acceptance Criteria:**
  - [ ] **AC-2.** When the reviewer sends `GET /documents/{id}` with a `document_id` from an upload reply, then the reply has status code `200` and a `status` that is one of `queued`, `parsing`, `summarizing`, `done`, `failed`.
  - [ ] **AC-21 (added).** When the reviewer sends `GET /documents/abc`, where `abc` is a text that no upload reply gave, then the reply has status code `404`.
  - [ ] **AC-26 (added).** Given a document that the reviewer uploaded 61 minutes ago or earlier, when the reviewer sends `GET /documents/{id}` with its `document_id`, then the reply has status code `404`.

### 2.4 Get the pages and the summary with the `pypdf` parser

With `parser=pypdf`, the result has one page for each page of the PDF file, in page order. The `content` of a page is plain text. The result also has one `summary` for the document, and `summary_status` is `done`.

- **Acceptance Criteria:**
  - [ ] **AC-3.** Given a valid `GEMINI_API_KEY` is set, when the reviewer uploads the sample text PDF with `parser=pypdf` and sends `GET /documents/{id}` every 2 seconds, then within 60 seconds of the upload reply the `status` is `done` and the body has `pages` (a list in which each item has `page_number` and `content`) and a `summary`.

### 2.5 Get the pages as markdown with the `gemini` parser

With `parser=gemini`, Google Gemini reads the pages and writes the `content` of each page as markdown. Plain text is also valid markdown, so no criterion checks the markdown syntax. The document gets one summary, by the same rules as with `pypdf`.

The `gemini` parser reads 25 pages at most. If a PDF file has more than 25 pages, the result has only the first 25 pages and the warning `gemini_page_limit`. The summary then covers only these pages.

The `gemini` parser also reads scanned pages. A scanned PDF file gets a usual result with this parser, with no `no_text_layer` warning.

Google Gemini can give an answer that does not have one item for each page. The service then asks one more time. If the second answer is also wrong, the document fails with the error code `gemini_bad_response`.

If Google Gemini does not answer the page request after 4 tries, the document fails with the error code `gemini_unavailable`. No criterion tests this code.

The `gemini` parser needs `GEMINI_API_KEY`. If the key is not set, the service refuses `parser=gemini` at the upload with the message "parser not configured". The `pypdf` parser still works with no key: a document gets its pages, and the summary fails (see section 2.7).

- **Acceptance Criteria:**
  - [ ] **AC-13 (added).** Given a valid `GEMINI_API_KEY` is set, when the reviewer uploads the sample text PDF with `parser=gemini` and sends `GET /documents/{id}` every 2 seconds, then within 120 seconds of the upload reply the `status` is `done` and the body has one `pages` item for each page of the PDF and a `summary`.
  - [ ] **AC-14 (added).** Given a valid `GEMINI_API_KEY` is set, when the reviewer uploads a PDF of 30 pages with `parser=gemini`, then within 120 seconds the document reaches the status `done` with exactly 25 `pages` items, numbered 1 to 25, and `warnings` has `gemini_page_limit`.
  - [ ] **AC-15 (added).** Given the service runs with `GEMINI_TEST_MODE=bad_page_count`, when the reviewer uploads the sample text PDF with `parser=gemini`, then within 120 seconds the document reaches the status `failed` with `error_code` `gemini_bad_response`.
  - [ ] **AC-16 (added).** Given `GEMINI_API_KEY` is not set, when the reviewer sends `POST /documents` with a PDF file and `parser=gemini`, then the reply has status code `400` and the message "parser not configured".

### 2.6 Documents that the service cannot read fully

The service accepts each file that is a PDF by the rule in section 2.2. It finds a problem with the content during the processing, not at the upload.

A document that the service cannot read gets the final status `failed`. The reply then has a fixed `error_code` and no pages. The result is the same with each parser.

| `error_code` | Cause |
| --- | --- |
| `encrypted` | The PDF file needs a password. |
| `invalid_pdf` | The file starts as a PDF, but it is damaged. |
| `gemini_bad_response` | Google Gemini gave a wrong answer two times (see section 2.5). |
| `gemini_unavailable` | Google Gemini did not answer the page request (see section 2.5). |

A scanned PDF file has pages that are images with no text. The `pypdf` parser does not read text from images. Such a document gets the status `done`, pages with an empty `content`, and the warning `no_text_layer`. The service gives this warning only if no page of the document has text.

A document with no text gets no summary. Its `summary` is `null` and its `summary_status` is `skipped`.

- **Acceptance Criteria:**
  - [ ] **AC-6.** When the reviewer uploads a PDF file that needs a password with `parser=pypdf`, then within 60 seconds the document reaches the status `failed` with `error_code` `encrypted`.
  - [ ] **AC-7.** When the reviewer uploads a scanned PDF file with `parser=pypdf`, then within 60 seconds the document reaches the status `done` and `warnings` is `["no_text_layer"]`.
  - [ ] **AC-23 (added).** When the reviewer uploads a PDF file that is cut off in the middle with `parser=pypdf`, then within 60 seconds the document reaches the status `failed` with `error_code` `invalid_pdf`.

### 2.7 Keep the pages if the summary fails

A problem with the summary does not remove the pages. If Google Gemini refuses a summary request because of its rate limit or because it is busy, the service tries again. The service makes 4 tries in total: the first call and 3 retries. The service does not try again after a different error.

If no try gives a summary, the document still gets the status `done`. The pages are present, `summary` is `null`, `summary_status` is `failed`, and `summary_error` gives the cause as a sentence.

| `summary_status` | Meaning |
| --- | --- |
| `pending` | The summary is not ready. This is the value while the status is `queued`, `parsing` or `summarizing`. |
| `done` | The summary is present. |
| `failed` | The service could not get a summary. `summary_error` gives the cause. |
| `skipped` | The service did not ask for a summary, because the document has no text or the document failed. |

- **Acceptance Criteria:**
  - [ ] **AC-8.** Given the service runs with `GEMINI_TEST_MODE=rate_limit`, so that each summary request to Google Gemini fails with the rate-limit error `429` (the first call and 3 retries), when the reviewer uploads the sample text PDF with `parser=pypdf`, then within 60 seconds the document reaches the status `done` with `summary_status` `failed`, a `summary_error` that is not empty, and the `pages` present.
  - [ ] **AC-24 (added).** Given `GEMINI_API_KEY` is not set, when the reviewer uploads the sample text PDF with `parser=pypdf`, then within 60 seconds the document reaches the status `done` with `summary_status` `failed`, a `summary_error` that is not empty, and the `pages` present.

### 2.8 Continue after a worker stop

A stop of the worker does not lose a document. A document that was in process during the stop reaches its final status after the worker starts again. A document that the reviewer uploads during the stop waits with the status `queued`.

To get a document in process for this test, the reviewer uploads a PDF file of 10 pages or more with `parser=gemini` and stops the worker immediately after the upload reply.

- **Acceptance Criteria:**
  - [ ] **AC-10.** Given a document with the status `parsing` or `summarizing`, when the reviewer stops the worker and then starts it again, then the document reaches the status `done` within 120 seconds of the start.

### 2.9 Follow a batch

The reviewer asks for all documents of one upload with the `batch_id`. The reply has the `batch_id` and a list `documents`. The list has one item for each document, in the order of the upload. Each item has the `document_id`, the `filename` and the `status`. A batch has no status of its own.

- **Acceptance Criteria:**
  - [ ] **AC-11.** When the reviewer sends `GET /batches/{batch_id}` with a `batch_id` from an upload reply, then the reply has status code `200` and one item with `document_id` and `status` for each document of the batch.
  - [ ] **AC-22 (added).** When the reviewer sends `GET /batches/abc`, where `abc` is a text that no upload reply gave, then the reply has status code `404`.

### 2.10 Check that the service is ready

The reviewer asks the service if it can work. The service can work only if the data store runs. The reply does not depend on the worker.

While the data store is stopped, each other address of the service also replies with status code `503`.

- **Acceptance Criteria:**
  - [ ] **AC-12.** Given the data store runs, when the reviewer sends `GET /health`, then the reply has status code `200`.
  - [ ] **AC-25 (added).** Given the reviewer stopped the data store, when the reviewer sends `GET /health`, then the reply has status code `503`.

### 2.11 See the queue numbers

The reviewer asks for two numbers. `length` is the number of documents that the service put into the queue and still keeps there. `pending` is the number of documents that the worker took and did not finish. With these numbers, the reviewer sees that uploads go into the queue and that the worker completes them.

- **Acceptance Criteria:**
  - [ ] **AC-17 (added).** Given the worker is stopped and the reviewer noted the `length` from `GET /metrics/queue`, when the reviewer sends 3 uploads of one PDF file each and sends `GET /metrics/queue` again, then the reply has status code `200` and a `length` that is 3 more than before.

---

## 3. Scope and Boundaries

### In-Scope

- Upload of one or more PDF files with one parser name, and an immediate reply with identifiers.
- Refusal of a wrong upload with a status code and a message.
- The status of one document and the status of each document of a batch.
- The result of a document: the pages and one summary.
- The parsers `pypdf` and `gemini`.
- Fixed error codes and fixed warnings.
- A result with pages when the summary fails.
- No loss of a document after a stop and start of the worker.
- A health check and the queue numbers.

### Out-of-Scope

From the product owner:

- Auth.
- User accounts.
- Billing.
- Storage longer than 1 hour.
- Real GPT-5 Mini OCR. The parser name `gpt5mini` exists, but the service refuses it.
- The frontend. It is a stretch goal and gets its own specification later.

From the product definition:

- Websockets and SSE.
- A list of past documents, and the edit or removal of results.
- Text recognition for scanned pages with the `pypdf` parser.

---

## Change Log

_No amendments._
