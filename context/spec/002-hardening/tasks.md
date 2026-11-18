# Tasks: Hardening

- **Functional Specification:** [functional-spec.md](./functional-spec.md)
- **Technical Specification:** [technical-considerations.md](./technical-considerations.md)

## Rules for all tasks

- "AC" is an acceptance criterion of the functional spec. "TS" is a section of the technical spec.
- Do the slices in their order. Slice 4 is optional. Start it only after the Verify task of slice 3 shows `6 passed`.
- The contracts of TS 2.3 and TS 2.4 do not change. The order of the six steps in TS 2.4 is a contract. If a task cannot meet a contract, stop and report it.
- No reply of the API changes. The only new value is the error code `max_attempts`.
- The application must start with `docker compose up -d --build` after each task.
- Each task ends with its evidence run. Report the commands and their real output.
- Each slice is one commit. The last task of the slice makes it, after the Verify task passed. The commit message is fixed. Do not push.
- Stage files by path. Do not use `git add -A` or `git add .`. The files `.DS_Store` and `doc-pipeline.zip` and the folder `context/` stay out of each commit.
- Do not change the file `.env`. The only exception is Task 11, which saves it and restores it.
- Put each temporary file outside the repository. After each evidence run, stop the stack with `docker compose down` and delete each temporary file that the run made.
- If a Docker fact that TS 2.8 asks to check is wrong on this machine, stop and report it.

## Before you start

Checked on 2026-11-17:

- The Docker daemon does not run on this machine. Start Docker before the first task. Each Verify task needs it.
- `.env` has a value for `GEMINI_API_KEY`. Nobody checked that the key is valid. Task 7 (AC-12, AC-13, `demo.sh`) and AC-23 need a valid key. If the key does not work, report these checks as not done, with the reason.
- The project has no browser tool for an agent. Decision of the product owner, 2026-11-17: Task 16 uses a temporary headless browser (Playwright through `npx`) for AC-21 to AC-26. This downloads a browser one time, approximately 150 MB. Nothing is added to the project.
- `uv`, `make` and Node 22 are on the machine. The builds need network access for the Docker images and for `npm`.

## Slices

- [x] **Slice 1: Attempt limit — a document that stops the worker fails after 3 attempts**

  > Commit: `fix: attempts counter in doc hash`. Evidence for the slice: a file with the name `crash.pdf` ends as `failed` with `max_attempts` and a count of 3, and the worker processes the next document.
  - [x] Task 1: Add the two settings and the storage functions for the attempt count. **[Agent: python-backend]**
    - Scope: TS 2.7 (`max_attempts` with a minimum of 1, `worker_test_crash_filename`), TS 2.3 (`begin_attempt`, the optional `attempts` argument of `set_failure`), `.env.example`.
    - Files: `app/settings.py`, `app/storage/documents.py`, `.env.example`.
    - `begin_attempt` must not make an expired document exist again. Follow the guarded write that the file has today.
    - The worker does not call the new functions in this task. The behavior of the service does not change.
    - Evidence: the stack starts and `GET /health` gives `200`. One upload of a text PDF with `parser=pypdf` reaches `done`, as before. A load of the settings with `MAX_ATTEMPTS=0` fails with a validation error.
  - [x] Task 2: Change the worker loop: count each attempt in the document hash, fail a document with no attempt left, and add the test hook. **[Agent: stream-worker]**
    - Scope: TS 2.4 (all of it), TS 2.3 (stream fields, dead-letter field `attempt`).
    - Files: `app/storage/queue.py`, `app/worker/loop.py`, `app/worker/__main__.py`.
    - Order for each message: in-process guard, final guard, count, limit check, test hook, work. The test hook comes after the limit check.
    - The message of `documents:ingest` has no `attempt` field. The error path uses the count from the hash. The codes `gemini_unavailable` and `processing_failed` stay.
    - The branch for a data store error waits the existing retry time before it returns.
    - Criteria: AC-1 to AC-7, AC-10, AC-11.
    - Evidence: with `WORKER_TEST_CRASH_FILENAME=crash.pdf` from a temporary Compose override file, upload `crash.pdf` and start the worker by hand after each exit. After the fourth start, show `failed`, `max_attempts`, a count of 3 and one entry in `documents:dead`.
  - [x] Task 3: Add `restart: unless-stopped` to the service `worker`, and add the two settings to the settings list of the README. **[Agent: python-backend]**
    - Scope: TS 2.8 (worker), TS 2.10 (row for Part 1).
    - Files: `docker-compose.yml`, `README.md`.
    - This task comes after Task 2. Without the attempt limit, the restart policy makes an endless loop for a file that stops the worker.
    - Criteria: AC-8, AC-9.
    - Evidence: the same run as Task 2, with no start by hand: the document fails in 120 seconds or less, and `docker compose ps worker` shows the worker as running.
  - [x] Task 4: Verify: check the attempt limit and the recovery on the running stack with `curl` and `redis-cli`. **[Agent: python-test]**
    - Use temporary Compose override files outside the repository for the test settings. Make the sample files (text PDF, password PDF) in a temporary folder.
    - With `WORKER_TEST_CRASH_FILENAME=crash.pdf`: AC-1, AC-2, AC-3, AC-4, AC-8.
    - With no test setting: AC-5, AC-6, AC-9.
    - With `GEMINI_TEST_MODE=rate_limit`: AC-10 (one kill, then `done` with a count of 2) and AC-11 (three kills, then `max_attempts` with a count of 3).
    - With `MAX_ATTEMPTS=2` and `WORKER_TEST_CRASH_FILENAME=crash.pdf`: AC-7.
    - Read the count with `docker compose exec redis redis-cli HGET doc:<document_id> attempts` and the dead-letter list with `XRANGE documents:dead - +`.
    - Do not change files in `app/`. Report a defect to the owner of the code.
    - At the end: `docker compose down`, and delete the override files and the sample files.
  - [x] Task 5: Commit the slice with the message `fix: attempts counter in doc hash`. **[Agent: python-backend]**
    - Stage only: `app/settings.py`, `app/storage/documents.py`, `app/storage/queue.py`, `app/worker/loop.py`, `app/worker/__main__.py`, `docker-compose.yml`, `.env.example`, `README.md`.
    - Evidence: `git show --stat HEAD` with these files and no other file.

- [x] **Slice 2: Document content as data — both Gemini requests mark the content**

  > Commit: `fix: untrusted input framing in prompts`. Evidence for the slice: `grep -rn "<document>" app/` shows the marker in the summary request and in the page request, and a document still gets its pages and its summary.
  - [x] Task 6: Put the document content between `<document>` and `</document>` in the summary request and in the Gemini parser request, after the fixed instruction. **[Agent: stream-worker]**
    - Scope: TS 2.5.
    - Files: `app/gemini.py` (`DATA_INSTRUCTION`), `app/summarizer/summarizer.py`, `app/parsers/gemini_parser.py`.
    - The instruction sentence is word for word as in TS 2.5. The marker texts are written in both prompt modules.
    - The task texts and the output rules do not change. The parser request has three parts: text, the PDF file, text.
    - Do not change marker text that a document contains. This is out of scope.
    - Criteria: AC-14.
    - Evidence: the output of `grep -rn "<document>" app/`. With `GEMINI_TEST_MODE=rate_limit` from a temporary override file, one text PDF with `parser=pypdf` reaches `done`.
  - [x] Task 7: Verify: check the two requests with a real key. **[Agent: python-test]**
    - AC-14: `grep -rn "<document>" app/` shows both requests.
    - Make the instruction test PDF of the functional spec in a temporary folder. Do AC-12 (`parser=pypdf`) and AC-13 (`parser=gemini`). Pass rule: one run, or 2 of 3 runs after a failed run.
    - Run `./demo.sh` one time. It must end with exit code 0. This shows that the new order of the parser request still gives pages.
    - If `GEMINI_API_KEY` does not work or Google Gemini refuses the requests, report AC-12, AC-13 and the `demo.sh` run as not done, with the reason. Do not report them as passed.
    - At the end: `docker compose down`, and delete the temporary files and the files that `demo.sh` downloaded.
  - [x] Task 8: Commit the slice with the message `fix: untrusted input framing in prompts`. **[Agent: stream-worker]**
    - Stage only: `app/gemini.py`, `app/summarizer/summarizer.py`, `app/parsers/gemini_parser.py`.
    - Evidence: `git show --stat HEAD` with these files and no other file.

- [x] **Slice 3: Automated tests — `make test` runs six tests and stops the service**

  > Commit: `test: pytest suite with crash recovery`. Evidence for the slice: `make test` reports `6 passed`, ends with exit code 0, and leaves no running service.
  - [x] Task 9: Write the six tests and the test run: the fixtures, `tests/test_hardening.py`, the Compose override file, `test.sh` and the `make test` target. **[Agent: python-test]**
    - Scope: TS 2.6, TS 4.
    - Files: `tests/conftest.py`, `tests/test_hardening.py`, `docker-compose.test.yml`, `test.sh`, `Makefile`, `pyproject.toml` (`testpaths`).
    - Exactly six tests, with the names and in the order of the table in TS 4. Include the "Also" asserts of that table.
    - The tests make their sample files at run time. The repository gets no binary file. Only the crash test uses the name `crash.pdf`.
    - The tests read the Redis port with `docker compose port redis 6379`. The base `docker-compose.yml` does not change.
    - `test.sh` always runs `docker compose down`, also after a failed test and after a `/health` timeout. It exits with the status of pytest.
    - Annotate each test with `@spec: 002-hardening`, and with `@regression` if it is suitable for long-term regression.
    - Do not change files in `app/`. Report a defect to the owner of the code.
    - Criteria: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8, AC-10, AC-15, AC-16.
    - Evidence: the full output of one `make test` run with `6 passed`.
  - [x] Task 10: Update the README: remove the tests item from "Known issues", add the sections "Tests" and "Not done", and correct the line "Not fixed by choice". **[Agent: python-test]**
    - Scope: TS 2.10 (rows for Part 3), functional spec section 2.5.
    - "Tests" says: the command `make test`, what it does, that it needs Docker and `uv`, that it needs no key for Google Gemini, and that it replaces a running stack and stops it at the end.
    - "Not done" names the upload page. Slice 4 changes this section if it delivers the page.
    - Criteria: AC-18, AC-19, AC-20.
    - Evidence: the three README sections as text.
  - [x] Task 11: Verify: run `make test` in the pass case and in the two fail cases. **[Agent: python-test]**
    - AC-15: `make test` shows `6 passed` and exit code 0. AC-16: `docker compose ps` then shows no running service.
    - AC-17: save `.env`, add the line `MAX_ATTEMPTS=2`, run `make test`. The output names `test_crash_dead_letter` as failed, the exit code is not 0, and no service runs after it. Restore `.env` and show with `diff` that it is the same as the saved file.
    - Run before the fix: make a temporary git worktree of commit `04c7f53` outside the repository. Copy `tests/`, `docker-compose.test.yml`, `test.sh`, `Makefile` and `.env` into it. Run `make test` there. Tests 2 and 4 pass. Tests 1, 3, 5 and 6 fail: tests 1 and 3 fail on their attempt count assert, because the old code has no count. Remove the worktree.
    - AC-18, AC-19, AC-20: read the README sections.
    - Keep the output of the three runs in the task report. The delivery report uses it.
    - At the end: no service runs, the worktree and the saved copy of `.env` are deleted, and `git status` shows no file that this task made.
  - [x] Task 12: Commit the slice with the message `test: pytest suite with crash recovery`. **[Agent: python-test]**
    - Stage only: `tests/conftest.py`, `tests/test_hardening.py`, `docker-compose.test.yml`, `test.sh`, `Makefile`, `pyproject.toml`, `README.md`.
    - Evidence: `git show --stat HEAD` with these files and no other file.

- [x] **Slice 4 (optional): Upload page — the reviewer uploads files and sees the results in a browser**

  > Commit: `feat: minimal frontend`. Start this slice only after Task 11 shows `6 passed`. Evidence for the slice: `http://localhost:3000` serves the page, and an upload through the page address reaches the service.
  - [x] Task 13: Build the page in `frontend/` with Vite, React and TypeScript: a file input for more than one file, a parser select with `pypdf` and `gemini`, the button "Upload", and the document list with polling every 2 seconds. **[Agent: react-frontend]**
    - Scope: TS 2.8 (page rules), functional spec section 2.6.
    - Files: `frontend/package.json`, `frontend/package-lock.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/vite-env.d.ts`, `.gitignore` (`*.tsbuildinfo`).
    - The page calls only relative `/api/...` addresses. The rows come from the upload reply. The page asks for one document only when its status is `done` or `failed`.
    - Show each text from a file or from Google Gemini as plain text. Do not use `dangerouslySetInnerHTML`. Do not render markdown.
    - No router, no state library, no UI kit, no styling other than the default look of the browser.
    - Evidence: `npm ci` and `npm run build` in `frontend/` end with exit code 0.
  - [x] Task 14: Add the service `frontend`: the image with the built page and `nginx` on port 3000, with the proxy from `/api/` to the service `api`. **[Agent: react-frontend]**
    - Scope: TS 2.8 (service `frontend`, `nginx` rules).
    - Files: `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`, `docker-compose.yml`, `.dockerignore`.
    - The service has no Compose profile. This is a decision of the product owner. It replaces the profile rule in the agent file and in `context/product/architecture.md`.
    - `nginx`: `proxy_pass http://api:8000/;` with the trailing slash, `client_max_body_size 210m`, request buffering off, timeouts of 120 seconds.
    - Do not change files in `app/`. The API gets no CORS setting.
    - Evidence: after `docker compose up -d --build`, `curl -i http://localhost:3000/` gives `200` with the page, and `curl -i http://localhost:3000/api/health` gives `200`.
  - [x] Task 15: Update the README: add a short section for the upload page and remove the page from "Not done". **[Agent: react-frontend]**
    - Scope: TS 2.10 (rows for Part 4).
    - The section gives the address `http://localhost:3000` and says that port 3000 must be free.
    - Evidence: the two README sections as text.
  - [x] Task 16: Verify: check the upload page through its address with `curl`, and in a temporary headless browser. **[Agent: react-frontend]**
    - Start the stack with `docker compose up -d --build`. Make the sample files (2 text PDF files, one `.txt` file, the password PDF) in a temporary folder.
    - Through `http://localhost:3000/api/`: an upload of 2 text PDF files gives `202` with 2 documents. An upload of one PDF and one `.txt` file gives one document and one rejected file with "not a PDF file". An upload of one `.txt` file gives `415` with "not a PDF file". The batch address gives the status of each document until each is final.
    - An upload larger than 210 MB through the proxy gives a reply that is not JSON. Check in the page code that such a reply shows the status code and no parse error.
    - `make test` still shows `6 passed` (the test run does not start `frontend`).
    - Browser checks: run Playwright through `npx` from a temporary folder outside the repository. Do not add Playwright to `frontend/package.json` and do not write a test file into the repository.
    - In the browser, do AC-21, AC-22, AC-24, AC-25 and AC-26 as the functional spec gives them. Take one screenshot for each criterion and look at it before you report a pass.
    - AC-23 needs a valid `GEMINI_API_KEY`. If the key does not work, check that the row shows `done`, the pages and the text of `summary_error`, and report AC-23 as not done, with the reason.
    - If the browser download fails, report AC-21 to AC-26 as "not checked in a browser" and give the reviewer the steps for each criterion. Do not report them as passed.
    - At the end: `docker compose down`, close the browser, and delete the temporary folder with the script, the screenshots and the sample files. The downloaded browser stays in the cache of the machine; report its path.
  - [x] Task 17: Commit the slice with the message `feat: minimal frontend`. **[Agent: react-frontend]**
    - Stage only: the files of Tasks 13, 14 and 15. Do not stage `frontend/node_modules` or `frontend/dist`.
    - Evidence: `git show --stat HEAD` with these files and no other file.

- [x] **Slice 5: Feature Testing & Regression**

  > Verifies the whole feature end-to-end against functional-spec.md, run after all implementation slices are complete.
  - [x] Read functional-spec.md acceptance criteria in full. Generate acceptance-level tests that verify the entire feature as a whole — not individual slices. Cover applicable layers (unit for pure logic, integration for service interactions, e2e for user flows) based on the project's testing stack. Write tests with RED validation (must fail before implementation is confirmed done). Annotate each test with `@spec: 002-hardening` and `@regression` if suitable for long-term regression. **[Agent: python-test]**
    - For this spec, the acceptance tests are the six tests of Task 9. The functional spec puts other automated tests out of scope. Do not add a test.
    - Check that each of the six tests has its annotations and that the RED runs of Task 11 are recorded. If an annotation is absent, report it. Do not change a tracked file, because this slice makes no commit.
    - Make a table of AC-1 to AC-26: the test or the manual check that covers each criterion, and its result.
  - [x] Run all generated tests. All must pass. Fix any failures before proceeding. **[Agent: python-test]**
    - Run `make test` one time. Report the full output.
    - A failure here is a defect of an earlier slice. Report it to the owner of the code. Do not change files in `app/`.
    - Return the delivery report as the last message: the output of `make test`, the `git log` of the new commits, the result of each manual check, and each item that was not done, with the reason.
