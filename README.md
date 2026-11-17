# Doc Pipeline

Doc Pipeline is an async PDF processing service. You upload PDF files, select a parser, and poll for the content of each page and one summary for each document.

## Run

Requirements: Docker with Compose.

1. Copy the settings file and set your Gemini key (free tier):

   ```
   cp .env.example .env
   ```

   Then set `GEMINI_API_KEY` in `.env`.
2. Start the stack (`redis`, `api`, `worker`):

   ```
   docker compose up -d --build
   ```

3. The API runs at `http://localhost:8000`.

Settings in `.env`:

- `REDIS_URL`: address of Redis.
- `GEMINI_API_KEY`: key for the Gemini API.
- `GEMINI_MODEL_ID`: Gemini model. Default `gemini-3.1-flash-lite`.
- `OPENAI_API_KEY`: only read for the `gpt5mini` refusal message.
- `GEMINI_TEST_MODE`: `rate_limit` or `bad_page_count`. Use it for failure tests.
- `MAX_ATTEMPTS`: attempt limit for one document. Default `3`. Use a whole number of 1 or more. A wrong value makes `api` and `worker` stop at start.
- `WORKER_TEST_CRASH_FILENAME`: tests only. The worker exits when it starts work on a file with this name. Do not set it in a usual run.

Commands:

```
# upload (one request makes one batch)
curl -F "files=@a.pdf" -F "files=@b.pdf" -F "parser=pypdf" http://localhost:8000/documents
# one document
curl http://localhost:8000/documents/{document_id}
# all documents of a batch
curl http://localhost:8000/batches/{batch_id}
curl http://localhost:8000/health
curl http://localhost:8000/metrics/queue
```

Parsers:

- `pypdf`: plain text.
- `gemini`: markdown, first 25 pages.
- `gpt5mini`: refused in this version.

Stop the stack:

```
docker compose down
```

Run the demo: `./demo.sh`

## Demo

`./demo.sh` starts the stack, downloads 3 public PDF files, uploads them with `pypdf` and `gemini`, polls until done, prints each result and checks it. The files are `https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf` (text, 1 page), `https://bitcoin.org/bitcoin.pdf` (text, 9 pages) and `https://raw.githubusercontent.com/ocrmypdf/OCRmyPDF/main/tests/resources/skew.pdf` (scanned, 1 page).

```
== Step 1: start the stack ==
#1 [internal] load local bake definitions
#1 reading from stdin 528B done
#1 DONE 0.0s

#2 [internal] load build definition from Dockerfile
#2 transferring dockerfile: 385B done
#2 DONE 0.0s

#3 [internal] load metadata for ghcr.io/astral-sh/uv:0.11.13
#3 DONE 0.9s

#4 [internal] load metadata for docker.io/library/python:3.12-slim
#4 DONE 1.2s

#5 [internal] load .dockerignore
#5 transferring context: 129B done
#5 DONE 0.0s

#6 [internal] load build context
#6 transferring context: 2.60kB done
#6 DONE 0.0s

#7 [stage-0 1/6] FROM docker.io/library/python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
#7 resolve docker.io/library/python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b 0.0s done
#7 DONE 0.0s

#8 FROM ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563
#8 resolve ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563 0.0s done
#8 DONE 0.0s

#9 [stage-0 5/6] RUN uv sync --frozen --no-dev
#9 CACHED

#10 [stage-0 4/6] COPY pyproject.toml uv.lock .python-version ./
#10 CACHED

#11 [stage-0 2/6] COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /bin/uv
#11 CACHED

#12 [stage-0 3/6] WORKDIR /app
#12 CACHED

#13 [stage-0 6/6] COPY app ./app
#13 CACHED

#14 exporting to image
#14 exporting layers done
#14 exporting manifest sha256:ac941b67e95d24176292b0c702a512533602a5a85593e87e1d81c01ca2abcc6b done
#14 exporting config sha256:21b085fb76f102c71e6206ffa099a1c748c3fcebe81fdc3d4f63d9a85b056960 done
#14 exporting attestation manifest sha256:ea5f4a9086ab37cd34749942fdccc284a574ff846ffa55f20561213218a6f06e done
#14 exporting manifest list sha256:02f7ab94b3649c172d44c5a5a62b3b908974db60f85511fdfd536996b7331fa1 done
#14 naming to docker.io/library/doc-pipeline:latest done
#14 unpacking to docker.io/library/doc-pipeline:latest done
#14 DONE 0.0s

#15 resolving provenance for metadata file
#15 DONE 0.0s
 doc-pipeline:latest  Built
 Network doc-pipeline_default  Creating
 Network doc-pipeline_default  Created
 Container doc-pipeline-redis-1  Creating
 Container doc-pipeline-redis-1  Created
 Container doc-pipeline-worker-1  Creating
 Container doc-pipeline-api-1  Creating
 Container doc-pipeline-worker-1  Created
 Container doc-pipeline-api-1  Created
 Container doc-pipeline-redis-1  Starting
 Container doc-pipeline-redis-1  Started
 Container doc-pipeline-redis-1  Waiting
 Container doc-pipeline-redis-1  Waiting
 Container doc-pipeline-redis-1  Healthy
 Container doc-pipeline-worker-1  Starting
 Container doc-pipeline-redis-1  Healthy
 Container doc-pipeline-api-1  Starting
 Container doc-pipeline-api-1  Started
 Container doc-pipeline-worker-1  Started
health: 200
== Step 2: download 3 PDF files ==
-rw-r--r--@ 1 rzakirov  staff  184292 Nov 14 16:44 bitcoin.pdf
-rw-r--r--@ 1 rzakirov  staff   13264 Nov 14 16:44 dummy.pdf
-rw-r--r--@ 1 rzakirov  staff   76013 Nov 14 16:44 skew.pdf
== Step 3: upload with parser=pypdf ==
{"batch_id":"3e460fed-abe2-4f6c-b4e9-d5da60b91d38","documents":[{"document_id":"f54f6798-db26-4d8b-8036-379a5385a002","filename":"dummy.pdf","status":"queued"},{"document_id":"b71e503d-6be1-4cfe-8d89-bc095d2b8777","filename":"bitcoin.pdf","status":"queued"},{"document_id":"9850c2ca-50e4-4b56-ae71-6953d0487ff6","filename":"skew.pdf","status":"queued"}],"rejected":[]}
== Step 4: upload with parser=gemini ==
{"batch_id":"8e48264b-47dd-458e-9897-ec5c083b00b3","documents":[{"document_id":"d4dd1172-1ac2-4502-8796-e5872240badd","filename":"skew.pdf","status":"queued"},{"document_id":"68d065be-5f3b-4338-a76a-6c63eed5eddb","filename":"dummy.pdf","status":"queued"}],"rejected":[]}
== Step 5: poll the batches ==
batch 3e460fed-abe2-4f6c-b4e9-d5da60b91d38 finished
batch 8e48264b-47dd-458e-9897-ec5c083b00b3 finished
== Step 6: documents ==
---
filename: dummy.pdf
parser: pypdf
status: done
pages: 1
warnings: []
summary_status: done
page 1 (200 chars): 'Dummy PDF file'
summary: The provided document is a dummy PDF file. It serves as a placeholder and does not contain any substantive information or meaningful content.
---
filename: bitcoin.pdf
parser: pypdf
status: done
pages: 9
warnings: []
summary_status: done
page 1 (200 chars): 'Bitcoin: A Peer-to-Peer Electronic Cash System\nSatoshi Nakamoto\nsatoshin@gmx.com\nwww.bitcoin.org\nAbstract.  A purely peer-to-peer version of  electronic cash would allow online \npayments to be sent di'
summary: Bitcoin proposes a decentralized, peer-to-peer electronic cash system that eliminates the need for trusted financial institutions. By utilizing a chain of digital signatures and a proof-of-work timestamp server, the system effectively prevents the double-spending problem. The network achieves consensus by allowing nodes to vote with CPU power, where the longest proof-of-work chain serves  [...]
---
filename: skew.pdf
parser: pypdf
status: done
pages: 1
warnings: ['no_text_layer']
summary_status: skipped
page 1 (200 chars): ''
summary_error: None
---
filename: skew.pdf
parser: gemini
status: done
pages: 1
warnings: []
summary_status: done
page 1 (200 chars): '# The LinnSequencer\n## 32 Track MIDI Sequence Recorder\n\nThe LinnSequencer is a state-of-the-art composition and performance tool for the professional musician. It is extremely powerful, yet amazingly '
summary: The LinnSequencer is a powerful yet user-friendly 32-track MIDI recorder designed for professional composition and performance. It functions like a multi-track tape deck, offering advanced features such as timing correction, real-time editing, and rapid disk-based storage. Musicians can record, overdub, and chain sequences into complete songs while retaining full control over tempo, time  [...]
---
filename: dummy.pdf
parser: gemini
status: done
pages: 1
warnings: []
summary_status: done
page 1 (200 chars): 'Dummy PDF file'
summary: The provided document is a dummy PDF file containing placeholder content. It serves as a sample for testing purposes rather than conveying substantive information. As such, it lacks meaningful text or data to summarize.
== Step 7: queue metrics ==
{"length":5,"pending":0}
== Step 8: check expected results ==
all checks ok
ALL STEPS PASSED
Stop the stack with: docker compose down
```

## Known issues

- The first API request after a Redis restart can return `503`. The next request works. Likely cause: the Redis client of the API keeps a pooled connection that is stale after the restart, and it has no retry (`app/storage/client.py`, `make_client`). One-line fix: retry one time on a stale pooled connection (give the client a retry of 1 for connection errors).
- The smoke tests (Task 7 of the task list) are not written. The acceptance criteria were checked by hand with `curl` during the build.
- The first 25 pages only are read with `parser=gemini`. The document then has the warning `gemini_page_limit`.
- Results are kept for 1 hour.

## Assumptions

- The reviewer supplies a free-tier Gemini API key in `.env`.
- One upload request makes one batch. The reply gives one identifier for each accepted file.
- A file is a PDF if its content starts with `%PDF-`. A file can have 20 MB at most, and one upload 10 files at most.
- A wrong file in an upload is listed in `rejected`; the valid files are accepted.
- If the summary fails, the document still ends as `done` with its pages, and `summary_status` is `failed`.
- A scanned PDF with `parser=pypdf` gets the warning `no_text_layer` and no summary.
- `parser=gpt5mini` is refused: real GPT-5 Mini OCR is out of scope.
- Redis is the only store and the only queue. It has no persistence settings.
- There is no frontend, no auth and no user accounts in this version.


## AI workflow

Built with AWOS (spec-driven workflow for Claude Code). I made the decisions; the agents wrote the code.

Timeline (commits, local time; pause 12:47–15:20 not counted; about 2h10 of work):

11:37  chore: bootstrap repo with awos
12:26  chore: bootstrap repo with awos and task text
12:31  awos: product definition
12:40  awos: architecture with decisions
12:45  awos: hire to install skills
15:46  awos: functional spec 001
15:56  awos: technical spec 001
15:58  awos: tasks 001
16:15  implement: slice 1 skeleton, slice 2 API
16:42  implement: slice 3 worker, Gemini parser, recovery (AC-3,6,7,8,10,13,14,15,23,24)
16:54  implement: task 8 demo.sh and README

Decisions I made against the agent's recommendation:
- Frontend is a stretch goal, not out of scope. The task lists it last, not never.
- Mixed upload (PDF + .txt): accept the valid files, reject the rest in the same reply. Better for the user, one extra field.
- Missing GEMINI_API_KEY: the service starts and works without summaries. Fail-fast would block the PyPDF path, which is priority 2.

Decisions where the agent was right and I changed my plan:
- Gemini parser: one request per document, not per page. Per page hits the free-tier rate limit.
- Permanent errors (encrypted, invalid_pdf) fail at once, no retry.
- Worker reads its pending messages at start, so a restart recovers in seconds, not 60–90 s.

Gaps the process caught:
- The blind spec verifier found that my 12 criteria had no test for the Gemini parser or the queue endpoint.
- The evidence rule in implement found a COUNT 1 worker loop and a stale-connection 503 after a Redis restart.

Not fixed by choice: the 503 (see Known issues), smoke tests (skip-tests), formal /awos:verify (demo.sh output above replaces it).
