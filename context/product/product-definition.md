# Product Definition: Doc Pipeline

- **Version:** 1.0
- **Status:** Proposed
- **Source of requirements:** `context/sources/task.md` (the task text)

**Labels:** An item with no label comes from the task text or from the `/awos:product` request. An item with **(Decision)** is a scope decision that the product owner confirmed. An item with **(Assumption)** is a decision that the task text does not state and that the product owner did not confirm.

---

## 1. The Big Picture (The "Why")

### 1.1. Project Vision & Purpose

Doc Pipeline is an async PDF processing service. A user uploads one or more PDF files and selects a parser. After some time, the service gives the per-page content of each file and one summary for each file.

The upload does not wait for the processing. The service processes each document in the background and stores the result.

The project name comes from the repository name. **(Assumption)**

### 1.2. Target Audience

The audience is one reviewer who tests the service with `curl`.

The task text describes users who upload PDF files on a website. In this version, the reviewer does the same actions through the API.

### 1.3. User Personas

- **Persona 1: "The Reviewer"**
  - **Role:** Checks the delivered service against the task text.
  - **Goal:** Start the service and check each feature in the priority order of the task text, with `curl` only.
  - **Frustration:** Setup that needs manual steps. An upload that blocks until the processing ends. A result that the reviewer cannot read in a terminal. **(Assumption)**

### 1.4. Success Metrics

- The reviewer starts all components with Docker Compose.
- One command starts all components. **(Assumption)**
- The upload request returns before the processing ends.
- The service serves concurrent API requests. Five uploads sent at the same time all get a result. **(Assumption: the number five)**
- Each processed document has per-page content and one summary.
- The four top priorities of the task text work: the backend, the PyPDF parser, the async queue, the Gemini summary.
- The reviewer completes the full check with `curl` in 10 minutes or less. **(Assumption)**

---

## 2. The Product Experience (The "What")

### 2.1. Core Features

- **Upload:** The service accepts one or more PDF files and a parser value. The service accepts any PDF.
- **Parser choice:** The user selects one parser for the upload.
  - PyPDF gives text only, not markdown.
  - Google `gemini-3.1-flash-lite` gives markdown.
- **Background processing:** The service puts each uploaded document in a queue and processes it asynchronously.
- **Summary:** The service writes one summary for each document. The summary always comes from `gemini-3.1-flash-lite`, for each parser.
- **Stored result:** The service stores the markdown-formatted content and the summary of each document.
- **Status and result:** The reviewer reads the status and the result of a document through the API. **(Assumption: the task text permits a `curl` demo but does not describe the read API)**

### 2.2. User Journey

1. The reviewer gets a free-tier Gemini API key and gives it to the service. **(Assumption: the reviewer supplies the key)**
2. The reviewer starts the service with Docker Compose.
3. The reviewer sends one or more public sample PDF files and a parser value with `curl`.
4. The service replies immediately with one identifier for each document. **(Assumption)**
5. The reviewer polls the API with the identifier until the processing ends. **(Assumption: polling is the only method)**
6. The reviewer reads the per-page content and the summary of each document.

---

## 3. Project Boundaries

The time limit for this version is 2 hours. The scope follows the priority order of the task text.

### 3.1. What's In-Scope for this Version

- A backend API that serves concurrent requests.
- Upload of one or more PDF files with a parser value.
- Document processing with the PyPDF parser.
- An async queue between the upload and the processing.
- One summary for each document, from `gemini-3.1-flash-lite`.
- Document processing with the Gemini parser, built after the four items above. **(Decision)** The task text names this parser but not in the priority list. It uses the same API key as the summary.
- A stored result for each document: per-page content and one summary.
- A clear failure status for a document that the service cannot process. **(Assumption)**
- If the summary fails, the reviewer still gets the parsed content and an error for the summary. **(Assumption)**
- All components wrapped in Docker Compose.
- A README with the `curl` commands for the demo. **(Assumption)**

The task text fixes these technologies: Python 3.12+ with FastAPI, Redis v7+ with Redis Streams as the queue, Docker Compose, PyPDF, `gemini-3.1-flash-lite`. `/awos:architecture` records them.

**Stretch goal: the frontend website. (Decision)**

- Build the frontend only if all in-scope items are complete before the time limit.
- The frontend is one small page: upload of PDF files, parser choice, and the result of each document.
- The page uses API polling to show the result. **(Assumption: the task text permits polling, websockets or SSE)**
- The task text fixes the technology: JavaScript or TypeScript with React or Next.js.
- If the frontend is not built, the `curl` demo is sufficient. The task text accepts this.

### 3.2. What's Out-of-Scope (Non-Goals)

- **The GPT-5 Mini parser (OCR).** The task text gives it priority five of six. **(Decision)**
  - The service rejects this parser value with a clear message. **(Assumption)**
- Websockets and SSE. The stretch frontend uses API polling only. **(Assumption)**
- User accounts and login. **(Assumption)**
- A list of past documents, and the edit or removal of results. **(Assumption)**
- Text recognition for scanned pages with the PyPDF parser. PyPDF returns only the text that the file contains. **(Assumption)**
- Deployment outside Docker Compose, monitoring and scaling. **(Assumption)**
