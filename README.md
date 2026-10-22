# Professional Services AI OS (prototype)

A local-first, AI-assisted workspace for professional services workflows,
combining client and matter management, document intelligence, task workflows,
and a human-approved AI assistant. Independent prototype; it is **not** a
Stanford Innovations product.

**Built with:** Python · FastAPI · React · TypeScript · Vite · SQLite · Ollama
(local embeddings/default generation) · optional OpenRouter (hosted generation)

> **Project status:** interview-ready local development project. It uses
> fictional demonstration data and is **not** a production-ready, multi-user
> business platform. **Do not enter real or confidential client data** — this
> is a single-user demo with files stored unencrypted on disk. Local
> development binds to loopback; hosting requirements are documented under
> [Deployment](#deployment-vercel--render).

## Overview

The application brings business records, documents, and task workflows into one
workspace. The AI assistant retrieves relevant evidence, answers questions using
available context, and can **propose** a task for a human to review before the
task is created. The design keeps the assistant's ability to *read* information
separate from its ability to *change* business data: no proposed action executes
without explicit human approval.

The project exercises practical full-stack engineering: REST API design,
persistent data, document processing, retrieval-augmented generation (RAG),
authentication, request-protection controls, transactional approval workflows,
and regression testing.

## Key features

**Business workflows** — manage clients and matters; create and track tasks;
persist data in SQLite; seed fictional demo data.

**Document intelligence** — upload and validate supported files (`.pdf`, `.txt`)
with size/type checks and SHA-256 checksums; store uploads under server-generated
identifier names; extract text from PDFs with page/character limits; chunk
extracted text for retrieval; summarize documents with the configured provider
(local Ollama by default, or optional hosted OpenRouter).

**Grounded AI assistant** — answer questions from retrieved business and document
context; combine deterministic keyword/ID retrieval with optional semantic
similarity search; generate embeddings locally with Ollama `nomic-embed-text`;
store vectors in SQLite and rank by cosine similarity in Python; fall back to
keyword retrieval when embeddings are unavailable; build evidence server-side
(never from model output).

**Human-approved task creation** — the assistant proposes a supported action
(`create_task` only); the server validates the action and referenced matter and
rebuilds evidence; proposals are persisted as `pending_approval` with a
hash-verified payload; execution requires explicit human approval and runs in a
single transactional step that records an audit event with actor attribution and
prevents duplicate/replayed approvals.

**Authentication and security** — PBKDF2-HMAC-SHA256 password hashing; opaque
session tokens stored only as SHA-256 hashes; session expiry and revocation;
HttpOnly, `SameSite=Lax` session cookie; session-bound CSRF double-submit plus
origin/Referer validation on unsafe methods; explicit credentialed CORS; in-process
rate limiting and bounded LLM concurrency; safe document storage paths and
processing limits; a startup guard enforcing the single-user policy; regression
tests for authentication and workflow behavior.

## Layout

- `backend/app/main.py` — FastAPI app; `/health`, `/`, client, matter and task
  contracts unchanged from earlier phases.
- `backend/app/db.py` — SQLite connection management and schema.
- `backend/app/repository.py` — data access (sqlite3 only, no FastAPI).
- `backend/app/schemas.py` — Pydantic request/response models.
- `backend/app/documents_storage.py` — upload limits, content sniffing, UUID storage names.
- `backend/app/extraction.py` — PDF/plain-text extraction (pypdf); honest failures, no OCR.
- `backend/app/llm.py` — provider abstractions (`Summarizer`, `Assistant`, `Embedder`) + local Ollama implementations and optional OpenRouter generation.
- `backend/app/retrieval.py` — keyword/ID retrieval plus optional hybrid semantic merge for the assistant.
- `backend/app/embeddings.py` — chunk-embedding persistence (SQLite BLOBs) and bounded cosine ranking.
- `backend/app/assistant_service.py` — retrieval → prompt composition → provider → server-built evidence.
- `backend/app/proposer.py` — Step 5.4/5.5 proposal generation (validated, persisted, non-executed).
- `backend/app/approval.py` — Step 5.5 human approval + controlled execution (the only proposal write path).
- `backend/app/routers/` — `clients.py`, `matters.py`, `tasks.py`, `documents.py`, `ollama.py`, `assistant.py`.
- `backend/scripts/seed_demo.py` — optional fictional demo data (`.test` emails).
- `backend/tests/` — isolated test suite; each test uses a temporary database and storage dir.
- `frontend/` — Vite + React + TypeScript dashboard: Overview, Clients, Tasks, Documents.

## Requirements

- Python **3.13.x** recommended for deployment and **3.14.6** tested locally.
  Minimum is **3.11** (`starlette==1.9.0` requires it). All pinned runtime
  dependencies declare Python 3.11+ support, and the suite passes on 3.14.6
  locally. `uvloop==0.23.0` (via `uvicorn[standard]`) lists support through
  3.13, so 3.13 is the conservative hosted choice. Local setup uses a virtual
  environment at `backend/.venv`.
  - Runtime: `backend/.venv/bin/pip install -r backend/requirements.txt`
  - Development/tests: `backend/.venv/bin/pip install -r backend/requirements-dev.txt`
- Node.js 22 LTS or newer with npm (`cd frontend && npm install`)
- Local [Ollama](https://ollama.com) running with the `qwen3:4b` model and the
  `nomic-embed-text` embedding model (`ollama pull qwen3:4b`,
  `ollama pull nomic-embed-text`). Ollama is the default generation and
  embedding provider. Document upload, listing and keyword retrieval still work
  when it is unavailable. A hosted backend cannot reach local Ollama, so hosted
  generation may use OpenRouter instead; embeddings remain local and must be
  indexed where Ollama is reachable. See [AI provider configuration](#ai-provider-configuration)
  and [Deployment](#deployment-vercel--render).

## Getting started

Run the backend and frontend in two terminals **from the project root** (the
directory containing this `README.md`).

**1. Install dependencies**

```bash
# backend (development + tests)
python3.14 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt

# frontend
cd frontend && npm install && cd ..
```

**2. Create a login user** (one-time; no default credentials — see
[Authentication](#authentication-foundation-phase-6-step-62c-1)):

```bash
backend/.venv/bin/python -m scripts.create_user
```

**3. Start the backend** (loopback only, http://127.0.0.1:8000):

```bash
backend/.venv/bin/python -m app.launcher
```

**4. Start the frontend** (http://127.0.0.1:5173):

```bash
cd frontend
cp .env.example .env   # first time only
npm run dev
```

Open http://127.0.0.1:5173 and sign in. Use the paths in the sections below for
the detailed API contracts, controls, and limitations.

## Database

SQLite file at `backend/data/app.db` (override with the `PS_APP_DB` environment
variable). Created automatically on first run. Schema:

- `clients(id, name UNIQUE, email, created_at)`
- `matters(id, client_id → clients, title, status open|closed, created_at)`
- `tasks(id, matter_id → matters, title, status todo|in_progress|done,
  priority low|medium|high, created_at)`
- `documents(id, matter_id → matters, original_filename, stored_name UNIQUE,
  size_bytes, sha256, status uploaded|extracted|extraction_failed,
  extraction_error, created_at)`
- `document_chunks(id, document_id → documents, page_number, content)`
- `chunk_embeddings(chunk_id → document_chunks, document_id → documents,
  embedding BLOB, dimension, embed_model, created_at)` — Step 5.3; stored as
  float32 BLOBs. The table is created automatically on next start
  (`CREATE TABLE IF NOT EXISTS`); no migration step is required.
- `action_proposals(id, proposal_id UNIQUE, action_type create_task, status
  pending_approval|approved|executed|rejected|failed, payload_json, payload_hash,
  evidence_json, model, created_task_id → tasks, execution_error, created_at,
  updated_at, decided_at, executed_at)` — Step 5.5; payloads stored canonically
  with a SHA-256 hash. Created automatically on next start.
- `action_audit(id, proposal_id, event proposed|approved|executed|rejected|
  execution_failed, detail, created_at)` — Step 5.5; append-only, safe metadata
  only. Created automatically on next start.

Optional demo data (fictional only, safe to re-run):

```bash
cd backend && .venv/bin/python -m scripts.seed_demo
```

## API

| Method & path | Success | Errors |
|---|---|---|
| `GET /clients` | `200` list | — |
| `POST /clients` | `201` created client | `422` invalid body, `409` duplicate name |
| `GET /matters[?client_id=]` | `200` list | — |
| `POST /matters` | `201` created matter | `422` invalid body, `404` missing client |
| `GET /tasks[?matter_id=&status=]` | `200` list | — |
| `POST /tasks` | `201` created task | `422` invalid body, `404` missing matter |
| `GET /documents[?matter_id=]` | `200` metadata list | — |
| `POST /documents` (multipart: `matter_id`, `file`) | `201` metadata | `404` missing matter, `413` too large, `422` invalid/empty/wrong content, `409` duplicate file on same matter, `429` document rate limit |
| `POST /documents/{id}/extract` | `200` metadata | `404` unknown id, `429` document rate limit |
| `POST /documents/{id}/summarize` | `200` summary | `404`, `409` not extracted, `429` document rate limit or LLM concurrency saturated, `503` summarizer unavailable |
| `GET /ollama/health` | `200` `{available, models, summary_model, embed_model}` | — (reports `available: false` instead of erroring) |
| `GET /documents/index` | `200` index status | — |
| `POST /documents/index` | `200` backfill counts | — (always 200; per-document failures reported honestly) |
| `POST /documents/{id}/index` | `200` counts | `404` unknown id (non-extracted reported in body) |
| `POST /assistant/ask` (JSON: `{"question": "..."}`) | `200` answer | `422` blank or >2,000-char question, `429` per-user rate limit or LLM concurrency saturated, `503` assistant service unavailable |
| `POST /assistant/propose-action` (JSON: `{"request": "..."}`) | `200` proposal envelope, persisted as `pending_approval` (no task created) | `422` blank or >2,000-char request, `429` per-user rate limit or LLM concurrency saturated, `503` assistant service unavailable |
| `GET /assistant/proposals/{proposal_id}` | `200` persisted proposal record | `404` unknown id |
| `POST /assistant/proposals/{proposal_id}/approve` | `200` decision, creates the task exactly once | `404` unknown id, `409` already decided (dup/concurrent), `422` tampered/invalid payload or missing matter, `503` database busy |
| `POST /assistant/proposals/{proposal_id}/reject` | `200` decision (`rejected`) | `404` unknown id, `409` already decided, `503` database busy |

Interactive docs: `GET http://127.0.0.1:8000/docs`.

## AI provider configuration

Generation provider selection is explicit and backend-only:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PS_LLM_PROVIDER` | `ollama` | `ollama` preserves the existing local behavior; `openrouter` uses hosted generation. Other values fail startup. |
| `OPENROUTER_API_KEY` | — | Required when `PS_LLM_PROVIDER=openrouter`. Send it only through the backend environment; never put it in a `VITE_*` variable, frontend code, URL or Git. |
| `OPENROUTER_MODEL` | — | Required model identifier. It must currently be available to your OpenRouter account; this repository does not pin or guarantee a particular model. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter-compatible API base. HTTPS is required outside loopback (plain HTTP is allowed only for local tests). |
| `OPENROUTER_TIMEOUT` | `180` | Request timeout in seconds, bounded to 1–600. |
| `OPENROUTER_MAX_TOKENS` | `2048` | Output token budget, bounded to 1–8192. Kept larger than the Ollama default because reasoning models count reasoning tokens against it; too small a budget can return empty content. |

`PS_LLM_PROVIDER=openrouter` affects **document summarization and assistant
generation only**. Embeddings continue to use the existing local Ollama
`Embedder` (`OLLAMA_EMBED_MODEL` via `OLLAMA_URL`); they are not sent to
OpenRouter. If a hosted backend cannot reach Ollama for embeddings, semantic
retrieval is skipped and the keyword/ID path still works (with no embeddings
indexed, that is the normal path). Keyword fallback is preserved exactly.

OpenRouter is used through an OpenAI-compatible `POST /chat/completions` call
with the same server-composed prompts, temperature `0.2`, a bounded output
budget (`OPENROUTER_MAX_TOKENS`, default **2048**), and the single concurrency
gate. The key is sent only in the `Authorization: Bearer` header and is never
returned, logged or included in errors. Provider timeouts, rate limits, HTTP
errors, unreadable responses, error-shaped HTTP 200 bodies and empty content are
reported as safe `503` messages; an exhausted output budget is called out
separately. There is **no automatic fallback** between providers and no answer
is fabricated when the provider fails; a saturated local concurrency gate still
returns `429`.

OpenRouter model availability, free-model offers, rate limits, pricing and
service terms can change at any time. Do not assume a model is free or
permanently available; check your account and OpenRouter's current terms.
Hosted inference sends the relevant prompt and retrieved context (which may
include uploaded document text) to the selected external provider. Use
fictional demo data unless data-handling terms have been reviewed. AI endpoints
still require a configured, reachable provider; without one they return `503`.
`GET /ollama/health` remains a local Ollama/embedding health check; it does not
probe OpenRouter.

## AI assistant (Step 5.3 — hybrid keyword + semantic retrieval)

`POST /assistant/ask` answers questions with the configured generation provider:
local Ollama by default (`OLLAMA_ASSISTANT_MODEL`, default `qwen3:4b`;
`OLLAMA_ASSISTANT_TIMEOUT`, default 180 s), or OpenRouter when
`PS_LLM_PROVIDER=openrouter` (see
[AI provider configuration](#ai-provider-configuration)). Every request runs the
deterministic keyword/explicit-ID
retrieval from Step 5.2 first (unchanged SQL, unchanged limits). Step 5.3 adds
an OPTIONAL semantic pass: when the local embedder is reachable and the stored
index is compatible, extracted document chunks are also ranked by cosine
similarity and merged in, deduplicated against keyword hits. **A fresh database
has no semantic index by default.** Documents are not embedded automatically;
semantic retrieval only contributes once documents have been indexed via the
documented `POST /documents/index` workflow (or per-document
`POST /documents/{id}/index`), so the keyword/ID path is what answers questions
until then. Extracted text in
the prompt is wrapped in `<doc_text>` tags and labelled untrusted; user
questions are untrusted data that cannot override the system instructions. The
endpoint is read-only — no tools, SQL, shell, or file access, and no actions of
any kind.

**Bounded generation.** Ollama assistant and summarization calls set
`OLLAMA_NUM_PREDICT` (default **256** output tokens) and `OLLAMA_NUM_CTX`
(default **4096** context tokens). Both are read at request time, so tests and
operators can override them without a code change; invalid or non-positive
values fall back to the defaults. OpenRouter calls use `OPENROUTER_MAX_TOKENS`
(default **2048**, bounded 1–8192) as `max_tokens` and `OPENROUTER_TIMEOUT` for
the request timeout. The larger hosted budget is deliberate: reasoning models
count reasoning tokens against `max_tokens`, and a budget that is too small can
produce HTTP 200 with empty content. Retrieval is bounded well below the context
window (evidence ≤ 6,000 chars, question ≤ 2,000 chars), and summarization caps
document text at 6,000 chars. When the semantic index is empty, the
per-question query-embedding call is skipped entirely — keyword/ID retrieval is
unchanged.

**Evidence is server-side, never model-generated.** Every entry in `evidence`
points at an actual retrieved record with a stable ID (client/matter/task id,
document id and page when present), and `evidence_sufficient` is `true` only
when retrieval actually returned records — never inferred from how confident
the answer sounds.

### Embeddings and indexing (Step 5.3)

- Chunk embeddings use local Ollama `nomic-embed-text`
  (`OLLAMA_EMBED_MODEL`, `OLLAMA_EMBED_TIMEOUT` default 60 s; `OLLAMA_URL`
  reused). Vectors are stored as float32 BLOBs in `chunk_embeddings` with
  `document_id`, `dimension`, `embed_model` and `created_at`. Embeddings are
  separate from generation and are **never sent to OpenRouter**.
- **Indexing is best-effort at upload/re-extract and never fails those
  operations:** if the embedder is down, the document stays retrievable by
  keyword and can be indexed later. There is **no automatic full-corpus
  backfill at startup.**
- **Backfill / retry:** `POST /documents/index` indexes all extracted
  documents; `POST /documents/{id}/index` indexes one. Both return honest
  `{documents, indexed, skipped, failed, errors}` counts and never raise on
  provider failure. `GET /documents/index` reports
  `{embed_model, dimension, indexed_chunks, indexed_documents,
  total_documents, stale}`.
- **Model/dimension changes require re-indexing.** If the configured
  `OLLAMA_EMBED_MODEL` (or its dimension) differs from the stored index,
  `stale` is `true` and semantic retrieval is **skipped** — vectors from
  incompatible models are never ranked together. Run `POST /documents/index`
  after changing the model to rebuild, then `stale` returns to `false`.
- **Re-indexing is staged and transactional:** all new vectors are embedded and
  validated *before* the document's old index rows are deleted, so a failed
  re-index leaves the previous valid index intact. `replace_document_chunks()`
  also deletes stale embeddings when chunk IDs change.
- Corrupt, truncated, non-finite (NaN/Inf), or dimension-mismatched stored
  vectors are skipped safely, never ranked.

Semantic retrieval is bounded (env-overridable): `EMBED_MAX_TEXT_CHARS`
(default 2,000 per chunk text), `EMBED_QUERY_CHARS` (2,000),
`EMBED_MAX_TEXTS_PER_REQUEST` (16 per provider call),
`EMBED_MAX_VECTORS_SCORED` (2,000 candidates per query),
`SEMANTIC_MAX_CHUNKS` (4 merged chunks), plus the Step 5.2 limits
(`RETRIEVAL_MAX_CHUNKS` 6, `RETRIEVAL_MAX_EXCERPT_CHARS` 600,
`RETRIEVAL_MAX_CONTEXT_CHARS` 6,000).

`SEMANTIC_MIN_SIMILARITY` (default `0.25`) is a **provisional** cutoff, not a
correctness guarantee: cosine similarity reflects proximity in embedding space,
not factual support. Positive/negative retrieval tests exist, but every answer
is still machine-generated and requires human review against the cited records.

If semantic retrieval is unavailable or the index is stale, the Step 5.2
keyword/ID path is preserved exactly and the skip is recorded without logging
question or chunk text.

Request (question max 2,000 characters):

```bash
curl -s -X POST http://127.0.0.1:8000/assistant/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the most we can charge the client for this matter?"}'
```

Response:

```json
{
  "answer": "fifty thousand dollars (Document #1). This response is machine-generated and requires human review.",
  "evidence": [
    {
      "kind": "document",
      "reference": "Document #1 (\"engagement.txt\")",
      "excerpt": "The engagement letter states that our maximum remuneration for the Harbor matter is fifty thousand dollars, invoiced monthly."
    }
  ],
  "evidence_sufficient": true,
  "machine_generated": true,
  "review_required": true,
  "model": "qwen3:4b"
}
```

When nothing matches, `evidence` is `[]` and `evidence_sufficient` is `false`;
the answer is an unverified model opinion and must be reviewed by a human.

## Proposed workflow actions & approval (Steps 5.4–5.5)

`POST /assistant/propose-action` lets the assistant **propose** a single
workflow action grounded in retrieved records. In this step the only supported
action is `create_task` against an **existing** matter. Proposing still performs
**no task write**: it persists a `pending_approval` proposal plus a `proposed`
audit event, and creates no task. A task is only created when a human explicitly
approves the persisted proposal.

Every field is validated on the server against the real workspace:

- The action type is checked against a server-side allowlist (`create_task`
  only); anything else yields no proposal.
- The `matter_id` must be an integer that references a matter that actually
  exists; the model cannot invent IDs.
- Evidence references are rebuilt from the rows retrieval actually returned —
  never taken from model output.
- `due_date` is kept only if it is an explicit ISO date (`YYYY-MM-DD`);
  otherwise it is returned as `null`. Deadlines are never invented.
- If evidence is insufficient, no suitable matter exists, or the model output
  is malformed, the response carries `proposal: null` with a safe `reason`.

`status` is `pending_approval` and a `notice` states plainly that **no task has
been created**. `proposal_id` is a server-generated opaque id (32 hex chars); it
is **not** an authorisation token.

Request (max 2,000 characters):

```bash
curl -s -X POST http://127.0.0.1:8000/assistant/propose-action \
  -H "Content-Type: application/json" \
  -d '{"request": "Please add a task to prepare the Harbor disclosure schedule."}'
```

Response (abridged):

```json
{
  "proposal": {
    "proposal_id": "eabcbec6575642fa959f87db0caf368a",
    "action_type": "create_task",
    "status": "pending_approval",
    "proposed": {
      "title": "Prepare Harbor disclosure schedule",
      "description": "Prepare the initial disclosure schedule and circulate it to the client by 2026-02-10.",
      "matter_id": 1,
      "matter_title": "Harbor merger diligence",
      "due_date": "2026-02-10"
    },
    "evidence": [
      {"kind": "matter", "reference": "Matter #1 (Harbor merger diligence) [client #1, status open]", "excerpt": "title: Harbor merger diligence; status: open"}
    ],
    "notice": "This is a machine-generated proposal only. No task has been created or changed. Nothing will happen unless a human approves it in a later step.",
    "machine_generated": true,
    "review_required": true,
    "model": "qwen3:4b"
  },
  "reason": null,
  "machine_generated": true,
  "review_required": true
}
```

### Human approval and controlled execution (Step 5.5)

Each valid proposal is persisted in `action_proposals` with its validated
payload stored canonically plus a SHA-256 hash (tamper detection), and a
`proposed` row is appended to the append-only `action_audit` table. Proposal
creation and its audit row commit together.

- `GET /assistant/proposals/{proposal_id}` — fetch the persisted proposal and its
  real `status`.
- `POST /assistant/proposals/{proposal_id}/approve` — approve it. Accepts **no
  client payload**: the server loads the stored, hash-verified payload and uses
  only that. It revalidates the matter exists and the task fields are valid, then
  in a single `BEGIN IMMEDIATE` transaction marks the proposal approved, inserts
  the task, sets `status = executed` with `created_task_id`, appends the
  `executed` audit event, and commits atomically.
- `POST /assistant/proposals/{proposal_id}/reject` — reject a pending proposal
  (`rejected`), with its audit event committed atomically. No task is created.

Guarantees and honest limits:

- **Exactly one task per approval.** A guarded status update only matches
  `pending_approval`, so duplicate or concurrent approvals get `409` and create
  no second task. Response loss followed by a retry is safe (the retry also gets
  `409`).
- **Fail safe.** If task insertion fails, the task and all execution-state
  changes are rolled back together; a safe `execution_failed` category is
  recorded only after rollback. Invalid or tampered payloads (`409`/`422`) never
  write. A retryable database lock returns `503` and leaves the proposal
  pending.
- **No distributed exactly-once claim.** This is atomicity and single-writer
  serialization on one SQLite file (WAL) with a bounded busy timeout
  (`PS_SQLITE_BUSY_TIMEOUT`, default 5 s). It makes double-execution impossible;
  it does not make HTTP delivery exactly-once.
- **Dates are never rewritten.** A stored historical/past due date is recorded
  as-is, not silently corrected.

Audit rows store only safe metadata (proposal id, event, status/task id or a
safe error category, timestamp) — never prompts, document text, or full
payloads. Combined with the assistant, the model can still never construct SQL,
choose tools/routes, or execute anything: approval is the only path to a write.

Prompt-injection protections match the rest of the assistant: the user request
and retrieved document text are labelled untrusted data inside delimited tags,
the model may only choose from the server allowlist, and no model output can
trigger a write or arbitrary tool/SQL/shell execution. The prompt text reduces
injection risk but does not eliminate it.

## Document handling

- Files are stored under `backend/data/documents/` (override with `PS_DOCS_DIR`)
  with server-generated UUID names. Original filenames are metadata only and are
  never used as paths; stored paths are never returned by the API and no
  endpoint serves raw file contents.
- Limits (env-overridable): `PS_MAX_UPLOAD_BYTES` (default 5 MiB),
  `PS_MAX_PDF_PAGES` (default 100), `PS_MAX_EXTRACTED_CHARS` (default 500,000).
- Uploads are validated by extension **and** content sniffing (`%PDF-` magic for
  PDFs, UTF-8/no-NUL for text). Identical file content on the same matter → 409.
- Extraction is synchronous. Scanned/image-only PDFs get
  `status=extraction_failed` with an explicit reason — **no OCR** in this phase.
- Summarization uses the configured generation provider. The default is local
  Ollama (`OLLAMA_URL`, default `http://127.0.0.1:11434`;
  `OLLAMA_SUMMARY_MODEL`, default `qwen3:4b`; `OLLAMA_SUMMARY_TIMEOUT`, default
  180 s); hosted generation uses OpenRouter when `PS_LLM_PROVIDER=openrouter`
  (see [AI provider configuration](#ai-provider-configuration)). The API response
  keeps machine-extracted source excerpts (`source_excerpts`) separate from model
  output (`summary`, `key_points`), flags both as
  `machine_generated`/`review_required`, and treats document text as untrusted
  data in the prompt. If the configured provider is down, the endpoint returns
  503 with a clear error — never a fabricated summary.

## Running — two terminals, from this project root

Terminal 1 — backend on http://127.0.0.1:8000:

```bash
# from the project root (the directory containing this README)
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

The `--host 127.0.0.1` is deliberate **for local development**: the current
launcher binds to loopback only and has no external-binding switch. A
maintained launcher with the same enforced default is also available:

```bash
backend/.venv/bin/python -m app.launcher
```

Local development should stay on `127.0.0.1`. A hosted deployment must bind to
`0.0.0.0` behind the platform's TLS-terminating proxy and must configure exact
allowed origins, Secure cookies, persistent storage and one instance; see
[Deployment](#deployment-vercel--render). Authentication, CSRF, rate limiting
and the single-user startup guard are already enforced, but this remains a
**single-user** application with no ownership/tenant isolation. Public
`/docs`, `/redoc` and `/openapi.json` remain exposed by design.

## Authentication (Phase 6, Steps 6.2C-1–6.2C-3)

Authentication is **implemented end to end**: the backend protects every
business, document and assistant route, and the frontend provides a login
screen, session restore (`GET /auth/me`) and sign-out. Passwords are hashed with
PBKDF2-HMAC-SHA256 (unique salt per password, 600,000 iterations by default);
sessions are opaque server-side tokens of which only the SHA-256 hash is stored.
State-changing requests also require the session-bound CSRF token and an
allowed browser origin (see “CSRF” and “Cookies, CORS and HTTPS” below).
There is still **no role-based authorization and no record ownership**: every
authenticated user can read and modify every record.

Create the single login user (one-time; no default credentials). Two supported
paths share the same rules:

**Option A — local CLI (needs a shell).**

```bash
# from the project root (the directory containing this README)
backend/.venv/bin/python -m scripts.create_user
```

Prompts for a username (or `--username NAME`) and reads the password from a
**hidden prompt** — the password is never a command-line argument, never in
shell history and never printed. The password must be at least 12 characters.

**Option B — environment-driven startup (hosts without a shell).** Set these
backend-only variables before the service starts:

- `PS_BOOTSTRAP_USERNAME` — the username (not secret).
- `PS_BOOTSTRAP_PASSWORD_HASH` — **preferred**: the encoded PBKDF2 hash of the
  password. Generate it locally without exposing the plaintext to the host, then
  paste the printed hash into the host's environment:
  ```bash
  backend/.venv/bin/python -m scripts.create_user --print-hash
  ```
- `PS_BOOTSTRAP_PASSWORD` — fallback only, read when no hash is set. Prefer the
  hash so the plaintext password never lives in the hosting environment.

At startup the backend creates the account **only when the database has zero
users**, and only then runs the single-user guard. If any account already
exists, configured bootstrap is a no-op and **never overwrites or rotates** it.
Partial or invalid configuration fails startup closed. The username, password,
hash and other secrets are never logged.

Behavior common to both paths:

- There is **no hardcoded default username or password** and no committed
  credentials. `scripts.create_user` never creates a user without input; the
  startup path never creates one unless `PS_BOOTSTRAP_*` is configured.
- Refuses to overwrite an existing user. To deliberately replace a password,
  re-run the CLI with `--rotate` (the startup path never rotates).
- Refuses to create a **second distinct account**: this is a single-user demo
  (see "Single-user policy" below).
- Optional `--role user|approver` (default `user`) is a reserved label that is
  stored but **not enforced**. This demo enforces **no role-based
  authorization**, so `approver` grants **no** privileged access.

Sessions use server-side expiry (12 hours by default). Overridable for tests:
`PS_AUTH_PBKDF2_ITERATIONS`, `PS_AUTH_SESSION_TTL`. Authentication must **fail
closed**: the session/identity functions reject missing, invalid, expired and
revoked sessions — they never fall back to anonymous access.

## Single-user policy (Phase 7, Step 7.1B)

This is a **single-user demo** (local or single-instance hosted). The intended
runtime policy permits **at most one active account**. This is enforced, not
merely a convention:

- **Bootstrap (`scripts.create_user` and `PS_BOOTSTRAP_*`).** Creating the
  **first** account works as before. Rotating the existing account's password
  with `--rotate` works as before. The environment-driven startup bootstrap only
  ever creates a user when **zero** users exist and never overwrites an existing
  account. Attempting to create a **second distinct account is refused** with a
  clear error that never contains a password or hash. The refusal is enforced by
  the backend bootstrap logic, not by the frontend.
- **Runtime startup guard.** Bootstrap runs first, then the backend inspects the
  configured database (read-only) and **fails closed** if **more than one
  active account** exists, with a clear, non-sensitive error explaining how an
  operator can resolve it. **Zero** accounts is allowed so first-time setup can
  still bootstrap a user. Inactive accounts do not count toward the limit.
- **Legacy databases.** If an existing database already contains multiple
  accounts (e.g. created before this guard, or edited directly), the guard and
  the bootstrap CLI **never delete, deactivate or alter** them; they refuse and
  instruct the operator to resolve it manually.
- **Enforcement boundary.** The guard runs at startup only. It does **not**
  prevent arbitrary direct edits to the SQLite file while the process is
  running; the accurate boundary is "checked at startup, fail closed there."

The `role` field is **reserved** and is **not** an authorization boundary.
Records (clients, matters, tasks, documents, retrieval results, proposals) are
**not** owner-scoped or tenant-isolated: any authenticated user can see and act
on all of them. The public `/docs`, `/redoc` and `/openapi.json` policy is
**unchanged**. This demo is **not** safe to expose as a multi-user service or
to store real client data without further work (ownership columns and a
role-based permission matrix are explicitly out of scope). Hosting guidance
(HTTPS, exact origins, Secure cookies, persistent storage) is under
[Deployment](#deployment-vercel--render).

## Login, sessions and route enforcement (Phase 6, Step 6.2C-2)

The backend now enforces authentication on its business and AI routes.

**Endpoints**

- `GET  /auth/csrf` — seeds a pre-auth CSRF cookie so the login request itself
  can be protected.
- `POST /auth/login` — username + password; sets an **HttpOnly** session cookie
  (`ps_session`) and a readable CSRF cookie (`ps_csrf`). The raw session token is
  **only** ever set as a cookie, never returned in the JSON body. Unknown users,
  wrong passwords and inactive accounts all return the same generic `401`
  (`Invalid username or password.`).
- `POST /auth/logout` — revokes the current server-side session and clears both
  cookies. Returns `200 {"status":"logged_out"}` even when the session was
  already invalid; a forged logout without a valid CSRF token returns `403` and
  revokes nothing.
- `GET  /auth/me` — returns only `{id, username, role}` for the verified caller.

**Protection policy**

- Public: `GET /health`, `GET /`, and the interactive docs
  (`/docs`, `/redoc`, `/openapi.json`) — deliberately kept public because they
  expose no business data.
- Protected (authenticated session required, `401` otherwise): clients, matters,
  tasks, documents (list/upload/extract/index/summarize), `GET /ollama/health`,
  and all assistant endpoints (ask, propose-action, proposal status, approve,
  reject).
- Identity always comes from the verified session — request-body `user_id`,
  `role` or actor fields can never grant access.

**CSRF**

State-changing requests (`POST`/`PUT`/`PATCH`/`DELETE`) on authenticated routes
must send the session-bound CSRF token in the `X-CSRF-Token` header, matching the
`ps_csrf` cookie (a synchroniser/double-submit token whose hash is stored on the
session row). Missing or mismatched tokens get `403`. CORS and `SameSite` are
**not** treated as sufficient by themselves. When a browser supplies an
`Origin`/`Referer`, it must match one of the configured exact origins
(`PS_ALLOWED_ORIGINS`; localhost defaults for development).

**Cookies, CORS and HTTPS**

- Cookies: `HttpOnly` on the session cookie; `SameSite=Lax`; `Secure` is set
  **only** when `PS_AUTH_COOKIE_SECURE` is explicitly enabled. Local HTTP
  development (loopback) keeps cookies non-Secure so login works over
  `http://127.0.0.1:8000`; **production must set
  `PS_AUTH_COOKIE_SECURE=true`**. Never infer HTTPS from `X-Forwarded-Proto`.
- CORS: credentialed CORS (`allow_credentials=True`) is enabled for exact
  origins only — never a wildcard. `PS_ALLOWED_ORIGINS` is a comma-separated
  allowlist shared by CORS and the CSRF origin check; it trims whitespace and
  trailing slashes, normalises scheme/host, and rejects wildcards, paths,
  queries, userinfo and malformed ports at startup. When unset, the localhost
  development origins are used.
- The launcher remains loopback-only for local use. Hosted deployments use the
  Render start command documented below.
- Cross-origin deployment note: `ps_session` is an HttpOnly host-only cookie and
  the frontend reads the rotated `ps_csrf` cookie from `document.cookie`. A
  frontend on a different site from the API cannot read that cookie and
  `SameSite=Lax` cookies are not sent on cross-site fetches. Use a same-origin
  proxy (for example a Vercel rewrite of `/api/*` to the Render service) so
  cookies and CSRF stay first-party. Do not weaken `SameSite` or CSRF to work
  around this.

## Approval accountability (Phase 6, Step 6.2C-3)

Approval and rejection now records **who** decided, derived strictly from the
authenticated server-side session.

- **Schema (additive, idempotent).** Two nullable columns are added via the
  guarded-migration mechanism: `action_proposals.decided_by_user_id` and
  `action_audit.actor_user_id`, both intended to reference `users.id`. Legacy
  rows — including any decision made before authentication existed — keep
  `NULL`. No actor is ever fabricated.
- **Trusted source only.** The actor is `context.user_id` from `require_user`.
  `user_id`, `role`, `approved_by`, `actor_user_id` or `decided_by_user_id`
  supplied in a request body or query string are ignored entirely; a caller
  cannot vote on behalf of another user.
- **Transactional and append-only.** The actor is written as part of the same
  transaction as the proposal state change, the task creation and the audit
  event. A failed execution rolls the actor back with everything else; the
  audit trail is only ever appended to.
- **Event semantics unchanged.** No new event type was introduced. The existing
  `executed` event (approval that created a task) and `rejected` event each
  carry the accountable actor. `proposed` has no actor (it is machine-proposed),
  and `execution_failed` carries the approver whose execution failed.
- **Read-only exposure.** `GET /assistant/proposals/{id}` returns the recorded
  `decided_by_user_id`; it is informational and is **not** an authorisation
  token.

Protections from earlier substeps still hold: only pending proposals can be
decided, repeated/concurrent approvals cannot create duplicate tasks, and task
creation, status changes and audit events retain their atomicity and rollback
guarantees.

## Frontend login and session (Phase 6, Step 6.2C-4)

The web app now signs in against the backend session API.

- Every API request uses `credentials: "include"`, so the browser sends the
  HttpOnly session cookie. JavaScript never reads or stores the session cookie.
- On startup the app performs a **single** `GET /auth/me` to restore the
  session, showing a brief "Checking your session…" state first. A `401` is
  treated as signed-out; any other failure is shown as a distinct "could not
  confirm your session" notice rather than a silent logout.
- Signing in calls `GET /auth/csrf` to obtain a CSRF token, then
  `POST /auth/login`. Unsafe requests carry that token in the `X-CSRF-Token`
  header, matching the backend's double-submit contract. The token is held in
  memory (never in `localStorage`/`sessionStorage`) and refreshed from the
  readable `ps_csrf` cookie, which the server rotates on login.
- Signing out calls `POST /auth/logout` and always clears in-memory identity and
  CSRF state. A `403` (CSRF) is reported as a safe error, not an auth failure.
- A `401` from any protected call is surfaced as a session expiry; failures are
  never retried automatically, and approval UI still shows success only after
  the server confirms the persisted state.

Create a login user before using the app (see the bootstrap instructions above).

The frontend reads the backend base URL from `VITE_API_BASE_URL`, configured in
`frontend/.env` (copied from `frontend/.env.example`). The documented value is:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Set it only if the backend is not reachable at that default address.

Terminal 2 — frontend on http://127.0.0.1:5173:

```bash
cd frontend
cp .env.example .env   # first time only
npm run dev
```

Open http://127.0.0.1:5173 — you are shown the sign-in screen; after signing in,
the Clients page creates and lists clients via the API; the Tasks page shows each
task's status, priority and matter; the Documents page uploads PDF/text files
against a matter, shows extraction status, and (when the configured generation
provider is reachable) can summarize extracted documents.

## Rate limiting and LLM concurrency (Phase 6, Steps 6.3B–6.3D)

Expensive and exposed endpoints are protected by an **in-process fixed-window
rate limiter** plus a small **LLM generation concurrency gate**. All limits are
**in-memory and per-process**: they reset on restart and are not shared across
processes or machines. This is **not distributed rate limiting**.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PS_RL_LOGIN_MAX` / `PS_RL_LOGIN_WINDOW` | 30 / 300s | All login attempts (success or failure) per client address |
| `PS_RL_LOGIN_FAIL_MAX` / `PS_RL_LOGIN_FAIL_WINDOW` | 5 / 900s | Failed logins per normalised username + client address |
| `PS_RL_ASSISTANT_MAX` / `PS_RL_ASSISTANT_WINDOW` | 10 / 60s | `/assistant/ask` and `/assistant/propose-action` per authenticated user |
| `PS_RL_DOCUMENT_MAX` / `PS_RL_DOCUMENT_WINDOW` | 20 / 3600s | Document upload and re-extraction per authenticated user |
| `PS_RL_MAX_KEYS` | 10000 | Hard cap on tracked keys per limiter (bounds memory) |
| `PS_LLM_MAX_CONCURRENCY` | 1 | Maximum simultaneous generation calls (Ollama or OpenRouter) |

Notes:

- Configuration is read **at process startup** (limiter instances and the
  concurrency gate are created at import). Malformed, zero or negative values
  make the application fail to start rather than silently disabling protection.
- Keys are derived from the **server-resolved** session user and the **direct
  ASGI client address**. `X-Forwarded-For` and request-body identity fields are
  never trusted. Failed-login keys use a SHA-256 digest of the case-folded
  username, so changing username spelling cannot bypass the limit while the raw
  username is never stored in a key.
- Failed-login throttling is a **temporary fixed window**, not a permanent
  account lockout. A blocked attempt is refused **before** password hashing, so
  a throttled caller cannot burn CPU; a successful login never counts as a
  failure.
- Exceeding a limit returns HTTP **429** with a positive integer `Retry-After`.
  A saturated LLM gate returns **429** from assistant and summarization routes.
- Fixed windows can permit a **boundary burst** (up to twice the limit spanning
  two adjacent windows). This is an accepted simplification for a local demo.
- **Hosted deployments run one instance/worker.** Limiters and the LLM gate are
  in-process, so multiple workers or instances would each keep independent
  counters. Behind a platform proxy, `request.client` is the proxy address, so
  address-keyed limits (notably login) are shared by all clients; keep the
  default limits and do not trust `X-Forwarded-For` without a reviewed
  trusted-proxy design.
- `/docs`, `/redoc` and `/openapi.json` remain **public by design** and should
  be reviewed before exposing the service publicly.

## Deployment (Vercel + Render)

This repository is **prepared for** a Vercel frontend + Render backend
deployment; it is **not deployed**, and no real service URLs are committed.
Do not add a `frontend/vercel.json` rewrite until the actual Render service URL
is known.

**Backend on Render**

```
Root Directory:  backend
Build Command:   pip install -r requirements.txt
Start Command:   uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
Health Check:    /health
```

`app.main:app` is the entry point. Do **not** use `python -m app.launcher` on
Render — it intentionally binds to loopback. Pin `PYTHON_VERSION` to the latest
**3.13.x** (minimum 3.11; all pinned dependencies declare 3.11+ support).
Python 3.14.6 also passes the suite locally, but `uvloop`'s metadata stops at
3.13. Run a **single instance and one worker**: rate limits and the LLM gate are
in-process and SQLite is single-writer.

**Required production environment** (template: `backend/.env.production.example`):

- `PS_ALLOWED_ORIGINS` — exact comma-separated browser origin(s), for example
  `https://<your-vercel-production-origin>`; no wildcard. It is shared by CORS
  and the CSRF origin check. Invalid values fail startup.
- `PS_AUTH_COOKIE_SECURE=true` — required over HTTPS; do not set it locally.
- `PS_APP_DB=/var/data/app.db` and `PS_DOCS_DIR=/var/data/documents` — required
  with a Render persistent disk mounted at `/var/data`.
- Generation (optional): `PS_LLM_PROVIDER=openrouter` plus
  `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and optionally
  `OPENROUTER_BASE_URL` / `OPENROUTER_TIMEOUT` / `OPENROUTER_MAX_TOKENS`. The
  key is backend-only; never put it in a `VITE_*` variable or commit it. Without
  these, generation remains on local Ollama and will return `503` on Render.
- Embeddings: `OLLAMA_URL` (and `OLLAMA_EMBED_MODEL`) only if a reachable
  Ollama embedding service is configured. OpenRouter does **not** provide
  embeddings here; without a reachable embedder, keyword retrieval still works.

**Persistent storage is mandatory.** Render's default filesystem is ephemeral.
Without a persistent disk, SQLite (clients, matters, tasks, documents,
extracted text, embeddings, users, sessions, proposals, audit) **and** uploaded
files are lost on every restart, redeploy or spin-down. The disk must hold both
the database and the documents directory, and backups must include both
(including SQLite `-wal`/`-shm` or after a checkpoint). A disk also prevents
zero-downtime deploys and cannot be shared by multiple instances.

**Render free tier loses all data.** A free web service has **no persistent
disk**, and its filesystem (including SQLite and uploads) is reset on every
redeploy, restart and idle spin-down. On the free tier, expect the account,
sessions, clients, matters, tasks and documents to disappear. Do not point
`PS_APP_DB`/`PS_DOCS_DIR` at `/var/data` (that requires a paid disk); leave the
default paths so the app can write to its ephemeral filesystem. The
environment-driven bootstrap (`PS_BOOTSTRAP_*` below) recreates the login
account on each boot, so a login keeps working after a reset — but all business
data is still lost. For durable data on Render, use a paid persistent disk or an
external database, or run the demo locally. Never store real client data on the
free tier.

**Authentication across origins.** Deploy the API on the same origin as the
frontend (for example a Vercel rewrite from `/api/*` to the Render service) and
build the frontend with `VITE_API_BASE_URL=/api`. A direct cross-site
deployment (`*.vercel.app` calling `*.onrender.com`) cannot work with the
host-only `SameSite=Lax` session cookie and the readable rotated CSRF cookie;
do not weaken either control to compensate. The Vercel production origin must
be present in `PS_ALLOWED_ORIGINS`. The frontend refuses to fall back to
`http://127.0.0.1:8000` in a production build when `VITE_API_BASE_URL` is
missing.

**Single-user bootstrap.** On a host with a shell (including a paid Render
instance), create the one account interactively:

```bash
cd backend && python -m scripts.create_user --username <name>
```

On a host **without** a shell (for example the Render free tier), set
`PS_BOOTSTRAP_USERNAME` plus the preferred `PS_BOOTSTRAP_PASSWORD_HASH` (generate
it locally with `scripts.create_user --print-hash`) as backend-only environment
variables. At startup the backend creates the account only when the database has
zero users, then runs the single-user guard. It never overwrites an existing
account, and startup fails closed if more than one active account exists; the
demo does not support multi-user operation. On the free tier this bootstrap
re-runs after each data reset, so a login is always available even though
business data is not.

**AI generation is local Ollama by default; hosted OpenRouter is optional.** A
hosted backend cannot reach Ollama on `127.0.0.1`. Without a reachable generation
provider, upload/list/extract and keyword retrieval still work, but
summarization and assistant calls return `503`. Configure
`PS_LLM_PROVIDER=openrouter` with a backend-only `OPENROUTER_API_KEY` and an
`OPENROUTER_MODEL` available to your account to enable hosted generation.
Embeddings remain separate: a hosted backend without a reachable Ollama
embedding endpoint skips semantic retrieval and uses keyword retrieval
(`/ollama/health` reports the local embedding service unavailable). Do not
assume AI works in production until the chosen provider is configured and
reachable. Hosted inference sends prompt/context to the external provider; use
fictional demo data unless its data-handling terms have been reviewed. Free
models, limits and terms can change; no model or price is guaranteed.

## Tests

Backend (run from `backend/`; tests use temporary databases and storage
directories, never `data/`). Install development dependencies first with
`backend/.venv/bin/pip install -r backend/requirements-dev.txt`:

```bash
cd backend && .venv/bin/python -m pytest -v
```

Frontend:

```bash
cd frontend && npm test
```

## Frontend production build

Type-checks, then outputs to `frontend/dist`:

```bash
cd frontend && npm run build
```

A production build must set `VITE_API_BASE_URL` (Vite inlines it at build time;
the value cannot be changed without rebuilding). When the API is proxied on the
same origin, use `VITE_API_BASE_URL=/api`. If the variable is missing or blank
in a production bundle, the app throws a clear error instead of silently calling
`http://127.0.0.1:8000`; local development still defaults to the loopback
backend.

Linting (Oxlint):

```bash
cd frontend && npm run lint
```

## Known limitations (read before using)

- **Single-user demo.** The runtime policy permits at most one active account:
  bootstrap refuses a second distinct account and startup fails closed if more
  than one active account exists (see "Single-user policy"). The `role` field is
  reserved and is **not** enforced. There is **no authorization/roles
  enforcement and no encryption** (see below). Hosting requires a single
  instance and persistent storage (see [Deployment](#deployment-vercel--render)).
- **Local AI by default.** Summaries and assistant answers use Ollama at
  `http://127.0.0.1:11434` unless `PS_LLM_PROVIDER=openrouter` is configured.
  Embeddings always use Ollama; a hosted backend cannot reach it unless a remote
  embedding endpoint is configured, so semantic retrieval may be disabled while
  keyword retrieval continues to work. Without a reachable generation provider,
  summarization and assistant calls return `503`. Hosted inference sends the
  prompt and retrieved context to the selected external provider.
- **Hosted data durability is deployment-dependent.** On an ephemeral
  filesystem, all SQLite data and uploaded documents are lost on restart or
  redeploy. A persistent disk is required for real use, and backups are not
  automated by this repository.
- Authentication (backend and frontend login) is enforced and
  approval/rejection records the accountable actor, but there is **no
  authorization/roles enforcement and no encryption** — files are stored
  unencrypted on disk, and every authenticated user can read/modify all records
  (authentication is **not** record ownership or tenant isolation). Not suitable
  for real confidential legal or financial documents.
- No OCR: scanned PDFs report `extraction_failed` honestly instead of inventing text.
- Summaries are generated by the configured model and can be wrong; every
  summary is flagged `review_required` and must be checked by a human against
  the source excerpts provided alongside it.
- `/assistant/ask` combines deterministic keyword matching with best-effort
  semantic (embedding) retrieval. Semantic ranking uses cosine similarity,
  which measures proximity in embedding space — **not** factual support. The
  `SEMANTIC_MIN_SIMILARITY` cutoff is provisional. Every answer is flagged
  `review_required` for a human to check against the cited records.
- Retrieval quality depends on a small local embedding model
  (`nomic-embed-text`); changing `OLLAMA_EMBED_MODEL` requires re-indexing
  (`POST /documents/index`), and ranking is computed in Python over candidates
  loaded from SQLite — fine at this scale, but a dedicated vector index would
  be needed for large corpora. Indexing is synchronous on upload.
- This is a local demo with fictional data. Authentication is enforced, but
  there is **no authorization/roles enforcement or tenant isolation** — every
  authenticated user can read/modify all records.
- `/assistant/propose-action` persists a proposal but never creates a task. A
  task is created only when a human calls the approve endpoint. Approval accepts
  no client-supplied payload (the stored, hash-verified payload is used) and
  supports only `create_task`; there is no generic tool, SQL or shell execution.
- **Approval records the authenticated actor, not a real role check.** The
  accountable user is taken from the verified session (`decided_by_user_id` /
  `actor_user_id`), so "who approved" is recorded, but any authenticated user can
  approve or reject any proposal. Correctness relies on the state-guarded
  transaction (no duplicate tasks), not on role-based identity checks.
- Execution is atomic on a single SQLite file (WAL) with a bounded busy timeout.
  This is not distributed exactly-once: a second process or a lost HTTP response
  can still be retried, but the status guard prevents a second task.
- Pending proposals do not expire, and the proposals/audit tables grow
  append-only. Fine at demo scale.
- Evidence stored with a proposal reflects the workspace at proposal time;
  execution revalidates only that the matter still exists, not evidence freshness.
- Uploaded content is treated as untrusted in the summarization and assistant
  prompts (prompt injection is mitigated — bounded retrieval, untrusted tags,
  server-side evidence — not impossible with any LLM).
- SQLite file storage, single process, no migrations framework, no backups.
