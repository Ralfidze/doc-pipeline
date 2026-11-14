<!-- skip-tests: true -->
# Tasks: Async PDF Pipeline

- **Functional Specification:** [functional-spec.md](./functional-spec.md)
- **Technical Specification:** [technical-considerations.md](./technical-considerations.md)

## Rules for all tasks

- Each task is one pull request. Do not put two tasks in one pull request.
- The frozen contracts of the technical spec (sections 2.2, 2.3, 2.4) do not change. If a task cannot meet a contract, stop and report it.
- Each task ends with its evidence run. Record the commands and their real output in the pull request.
- After the evidence run, stop the stack with `docker compose down` and delete each temporary file that the run made.
- The application must start with `docker compose up -d --build` after each task.
- There is no frontend task in this plan. The frontend is a stretch goal with its own spec.
- "AC" is an acceptance criterion of the functional spec. "TS" is a section of the technical spec.

## Before you start

- AC-3, AC-13, AC-14 and `demo.sh` need a real `GEMINI_API_KEY` in `.env`. The key is empty at this time.
- The repository has no git remote. A pull request needs a remote.

## Slices

- [x] **Slice 1: Skeleton — the stack starts and reports its health**

  > Evidence for the slice: `docker compose up` starts `redis`, `api` and `worker`, and `GET /health` returns `200`.
  - [x] Task 1: Build the project skeleton: `pyproject.toml` and the `uv` lockfile, the `app/` package, the settings from environment variables, the shared Redis client with the group setup, the Pydantic models of the technical spec, the JSON logs, `GET /health`, the `Dockerfile`, `docker-compose.yml` with `redis`, `api` and `worker`, and `.env.example`. **[Agent: python-backend]**
    - Scope: TS 2.1 (files of `python-backend` except the document endpoints), TS 2.7, TS 2.8, and the `503` handler of TS 2.5.
    - The `worker` service starts `python -m app.worker`. In this task the worker connects to Redis, does the group setup, and waits. It processes no message.
    - Criteria: AC-12, AC-25.
    - Evidence: `docker compose up -d --build` shows three running services. `curl -i http://localhost:8000/health` gives `200` and `{"redis":"ok"}`. After `docker compose stop redis`, the same call gives `503`.

- [x] **Slice 2: API — the reviewer uploads PDF files and reads the status**

  > Evidence for the slice: AC-1, AC-2, AC-4, AC-5 and AC-11 pass with `curl`.
  - [x] Task 2: Build `POST /documents`: the upload checks in their fixed order, the `rejected` list, and one Redis transaction that writes each document, its PDF bytes, the batch, the expiry of each key and one stream message for each document. **[Agent: python-backend]**
    - Scope: TS 2.2 (`POST /documents`), TS 2.3 (upload write, expiry rule, stream fields), TS 2.5.
    - This task adds the "add message" function to `app/storage/queue.py`. Task 4 extends that file.
    - Criteria: AC-1, AC-4, AC-5, AC-9, AC-16, AC-18, AC-19, AC-20, AC-27, AC-28.
    - Evidence: `curl` runs for AC-1, AC-4 and AC-5 with the status code and the body of each reply. `docker compose exec redis redis-cli XLEN documents:ingest` shows one message for each accepted document.
  - [x] Task 3: Build `GET /documents/{document_id}`, `GET /batches/{batch_id}` and `GET /metrics/queue`. **[Agent: python-backend]**
    - Scope: TS 2.2 (the three addresses), TS 2.3 (batch read, group setup after `NOGROUP`).
    - A document stays `queued` in this slice, because the worker processes no message until Task 4.
    - Criteria: AC-2, AC-11, AC-17, AC-21, AC-22, AC-26.
    - Evidence: `curl` runs for AC-2 and AC-11 with the status code and the body of each reply. `redis-cli TTL` on each key of a new document gives a value from 1 to 3600.

- [x] **Slice 3: Worker — a document gets its pages and its summary**

  > Evidence for the slice: AC-3, AC-6, AC-7, AC-8 and AC-10 pass with `curl`.
  - [x] Task 4: Build the consumer loop (steps 1 to 5), the common PDF check, `PyPdfParser`, the `Gpt5MiniParser` stub, the Gemini client with the call retry and the test mode `rate_limit`, and the summarizer. **[Agent: stream-worker]**
    - Scope: TS 2.4 (steps 1 to 5, the guard, the failure table), TS 2.6 (all items except `GeminiParser`), TS 2.3 (expiry rule on each write).
    - Before the first Gemini call, check the `google-genai` call names, the error class for `429` and `503`, and the model id against the current documentation. Make one real call.
    - Criteria: AC-3, AC-6, AC-7, AC-8, AC-23, AC-24.
    - Evidence: `curl` runs for AC-3 (real `GEMINI_API_KEY`), AC-6, AC-7, and AC-8 (`GEMINI_TEST_MODE=rate_limit`), each with the final reply body.
  - [x] Task 5: Build `GeminiParser`: one request for each document, the 25-page limit with the warning `gemini_page_limit`, the item-count check with one retry, the error `gemini_bad_response`, and the test mode `bad_page_count`. **[Agent: stream-worker]**
    - Scope: TS 2.6 (`GeminiParser`, test mode), TS 2.4 (failure table rows for Gemini).
    - Criteria: AC-13, AC-14, AC-15.
    - Evidence: `curl` runs for AC-13 and AC-14 (real `GEMINI_API_KEY`) and for AC-15 (`GEMINI_TEST_MODE=bad_page_count`), each with the final reply body.
  - [x] Task 6: Build the recovery: the read of own pending messages at start, the claim task with `XAUTOCLAIM` every 30 seconds, and the set of message ids in process. **[Agent: stream-worker]**
    - Scope: TS 2.4 (step 6, "Start recovery", "messages in process", repeat run with existing pages).
    - Criteria: AC-10.
    - Evidence: with `GEMINI_TEST_MODE=rate_limit` and `parser=pypdf`, stop the worker while the document is `summarizing`, start it again, and show `done` in 120 seconds or less. Show the worker log lines with the `document_id`.

- [ ] **Slice 4: Demo and QA (Feature Testing & Regression)**

  > Verifies the whole feature end-to-end against functional-spec.md, run after all implementation slices are complete. Evidence for the slice: `demo.sh` ends with exit code 0.
  - [ ] Task 7 (not done: skip-tests, time limit): Write the smoke tests: `tests/conftest.py` and one pytest file for each of the slices 1 to 3, with one test for each acceptance criterion AC-1 to AC-28. Annotate each test with `@spec: 001-async-pdf-pipeline`, and with `@regression` if it is suitable for long-term regression. **[Agent: python-test]**
    - Scope: TS 4 (fixtures, poll helper, sample files, stack modes, markers, the rules for AC-9, AC-10 and AC-26).
    - Files: `tests/test_slice1_skeleton.py` (AC-12, AC-25), `tests/test_slice2_api.py` (AC-1, 2, 4, 5, 9, 11, 16 to 22, 26, 27, 28), `tests/test_slice3_worker.py` (AC-3, 6, 7, 8, 10, 13, 14, 15, 23, 24).
    - Smoke tests only: `httpx` against the running stack. No unit tests, no fake Redis.
    - RED check: run `tests/test_slice3_worker.py` with the worker stopped and record the failures. Then start the worker and record the pass.
    - Do not change files in `app/`. Report a defect to the owner of the code.
    - Evidence: the pytest output of each stack mode, with all tests passed or skipped for their mode.
  - [x] Task 8: Write `demo.sh` and `README.md`. `demo.sh` shows the feature with 3 public PDF files and then runs all smoke tests in each stack mode. `README.md` has the start steps, the `curl` commands, an "Assumptions" section and an "AI workflow" section. **[Agent: python-test]**
    - Decision of the product owner, 2026-11-14: Task 7 is skipped, so `demo.sh` runs no smoke tests. It shows the feature with 3 public PDF files (two text, one scanned). `README.md` gets the sections Run, Demo (the output of one run) and Known issues.
    - Scope: TS 4 (evidence run `demo.sh`, stack modes).
    - `demo.sh` downloads the 3 public PDF files, uploads them with `curl`, polls, and prints the status, the page count and the summary of each document. At least one file has 10 pages or fewer and 1 MB or less. `README.md` lists the address of each file.
    - "Assumptions": the items with the label Assumption in `context/product/product-definition.md`, `context/product/architecture.md` and the technical spec that are still valid.
    - "AI workflow": how the AWOS commands, the specs in `context/` and the specialist agents made this project, with the commit of each step.
    - Evidence: `./demo.sh` ends with exit code 0 and prints `ALL STEPS PASSED`. Keep the log of this run.
