# Professional Services AI OS (prototype)

A local-first, AI-assisted workspace for professional services workflows,
combining client and matter management, document intelligence, task workflows,
and a human-approved AI assistant. Independent prototype; it is **not** a
Stanford Innovations product.

**Built with:** Python · FastAPI · React · TypeScript · Vite · SQLite · Ollama

> **Project status:** interview-ready local development project. It uses
> fictional demonstration data and is **not** a production-ready, multi-user
> business platform. **Do not enter real or confidential client data** — this
> is a single-user, loopback-only demo with files stored unencrypted on disk.

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
extracted text for retrieval; summarize documents with a local model.

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
- `backend/app/llm.py` — provider abstractions (`Summarizer`, `Assistant`, `Embedder`) + local Ollama implementations.
- `backend/app/retrieval.py` — keyword/ID retrieval plus optional hybrid semantic merge for the assistant.
- `backend/app/embeddings.py` — chunk-embedding persistence (SQLite BLOBs) and bounded cosine ranking.
- `backend/app/assistant_service.py` — retrieval → prompt composition → provider → server-built evidence.
- `backend/app/proposer.py` — Step 5.4/5.5 proposal generation (validated, persisted, non-executed).
- `backend/app/approval.py` — Step 5.5 human approval + controlled execution (the only proposal write path).
- `backend/app/routers/` — `clients.py`, `matters.py`, `tasks.py`, `documents.py`, `ollama.py`, `assistant.py`.
- `backend/scripts/seed_demo.py` — optional fictional demo data (`.test` emails).
- `backend/tests/` — 342 tests; each uses an isolated temporary database and storage dir.
- `frontend/` — Vite + React + TypeScript dashboard: Overview, Clients, Tasks, Documents.

## Requirements

- Python 3.14 with a virtual environment at `backend/.venv`
  (`backend/.venv/bin/pip install -r backend/requirements.txt`)
- Node.js 20+ with npm (`cd frontend && npm install`)
- Local [Ollama](https://ollama.com) running with the `qwen3:4b` model and the
  `nomic-embed-text` embedding model (`ollama pull qwen3:4b`,
  `ollama pull nomic-embed-text`). Ollama is optional: document upload, listing
  and keyword retrieval still work when it is unavailable.

## Getting started

Run the backend and frontend in two terminals **from the project root** (the
directory containing this `README.md`).

**1. Install dependencies**

```bash
# backend
python3.14 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

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

**4. Start the frontend** (http://localhost:5173):

```bash
cd frontend
cp .env.example .env   # first time only
npm run dev
```

Open http://localhost:5173 and sign in. Use the paths in the sections below for
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

## AI assistant (Step 5.3 — hybrid keyword + semantic retrieval)

`POST /assistant/ask` answers questions with the local model
(`OLLAMA_ASSISTANT_MODEL`, default `qwen3:4b`; `OLLAMA_ASSISTANT_TIMEOUT`,
default 180 s). Every request runs the deterministic keyword/explicit-ID
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

**Evidence is server-side, never model-generated.** Every entry in `evidence`
points at an actual retrieved record with a stable ID (client/matter/task id,
document id and page when present), and `evidence_sufficient` is `true` only
when retrieval actually returned records — never inferred from how confident
the answer sounds.

### Embeddings and indexing (Step 5.3)

- Chunk embeddings use local Ollama `nomic-embed-text`
  (`OLLAMA_EMBED_MODEL`, `OLLAMA_EMBED_TIMEOUT` default 60 s; `OLLAMA_URL`
  reused). Vectors are stored as float32 BLOBs in `chunk_embeddings` with
  `document_id`, `dimension`, `embed_model` and `created_at`. No external or
  paid providers are used.
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
- Summarization uses local Ollama only (`OLLAMA_URL`, default
  `http://127.0.0.1:11434`; `OLLAMA_SUMMARY_MODEL`, default `qwen3:4b`;
  `OLLAMA_SUMMARY_TIMEOUT`, default 180 s). The API response keeps machine-extracted
  source excerpts (`source_excerpts`) separate from model output (`summary`,
  `key_points`), flags both as `machine_generated`/`review_required`, and treats
  document text as untrusted data in the prompt. If Ollama is down, the endpoint
  returns 503 with a clear error — never a fabricated summary.

## Running — two terminals, from this project root

Terminal 1 — backend on http://127.0.0.1:8000:

```bash
# from the project root (the directory containing this README)
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

The `--host 127.0.0.1` is deliberate: the backend binds to loopback only. A
maintained launcher with the same enforced default is also available:

```bash
backend/.venv/bin/python -m app.launcher
```

Both pin the bind host to `127.0.0.1` and there is **no configurable
external-binding default**. Binding to a non-loopback address (`--host
0.0.0.0`, a LAN IP, etc.) exposes an API that has **no authentication or
authorization** — every client, matter, task, document, proposal and approval
endpoint becomes reachable by anyone who can route to the port, and "approval"
would record intent without any identity. Do not enable non-loopback exposure
until authentication, authorization and deployment controls (TLS, a trusted
reverse proxy, rate limiting) are in place.

## Authentication foundation (Phase 6, Step 6.2C-1)

This step added the **backend authentication foundation only**. It does **not**
yet protect any route, add a login page, or enforce authorization: this is
**not** end-to-end authentication. Passwords are hashed with PBKDF2-HMAC-SHA256
(unique salt per password, 600,000 iterations by default), and sessions are
opaque server-side tokens of which only the SHA-256 hash is stored.

Create the first login user (one-time bootstrap; no default credentials):

```bash
# from the project root (the directory containing this README)
backend/.venv/bin/python -m scripts.create_user
```

Behavior:

- Prompts for a username (or `--username NAME`) and reads the password from a
  **hidden prompt** — the password is never a command-line argument, never in
  shell history and never printed. Password must be at least 12 characters.
- There is **no hardcoded default username or password** and no committed
  credentials; users are **never** created automatically at application startup.
- Refuses to overwrite an existing user. To deliberately replace a password,
  re-run with `--rotate`.
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

This is a **single-user, loopback-only local demo**. The intended runtime policy
permits **at most one active account**. This is enforced, not merely a
convention:

- **Bootstrap (`scripts.create_user`).** Creating the **first** account works as
  before. Rotating the existing account's password with `--rotate` works as
  before. Attempting to create a **second distinct account is refused** with a
  clear error that never contains a password or hash. The refusal is enforced by
  the backend bootstrap logic, not by the frontend.
- **Runtime startup guard.** At application startup the backend inspects the
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
**unchanged**. This demo is **not** safe to expose to other machines or to use
as a multi-user service without further work (ownership columns and a
role-based permission matrix are explicitly out of scope for this step).

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
`Origin`/`Referer`, it must match one of the allowed dev origins.

**Cookies, CORS and local HTTP**

- Cookies: `HttpOnly` on the session cookie; `SameSite=Lax`; `Secure` is set
  **only** when `PS_AUTH_COOKIE_SECURE` is explicitly enabled. For local HTTP
  development (loopback) cookies are intentionally non-Secure so login works
  over `http://127.0.0.1:8000`. Never infer HTTPS from `X-Forwarded-Proto`.
- CORS: credentialed CORS (`allow_credentials=True`) is enabled for the exact
  existing dev origins only — never a wildcard. The frontend will use
  `credentials: "include"` (wired in the next substep).
- The launcher remains loopback-only.

This step added **backend** authentication only: there is **no frontend login UI
yet**, and this is **not** complete end-to-end authentication.

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

Terminal 2 — frontend on http://localhost:5173:

```bash
cd frontend
cp .env.example .env   # first time only
npm run dev
```

Open http://localhost:5173 — you are shown the sign-in screen; after signing in,
the Clients page creates and lists clients via the API; the Tasks page shows each
task's status, priority and matter; the Documents page uploads PDF/text files
against a matter, shows extraction status, and (when the local Ollama service is
running) can summarize extracted documents.

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
| `PS_LLM_MAX_CONCURRENCY` | 1 | Maximum simultaneous Ollama generation calls |

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
- **Loopback binding is still required.** Rate limiting reduces abuse but does
  not make the app safe for arbitrary network exposure. Keep the backend on
  `127.0.0.1`; exposing it needs real authn/authz, TLS, a reverse proxy and
  distributed limits first.
- `/docs`, `/redoc` and `/openapi.json` remain **public by design** on loopback.

## Tests

Backend — 342 tests (run from `backend/`; tests use temporary databases and
storage directories, never `data/`):

```bash
cd backend && .venv/bin/python -m pytest -v
```

Frontend — 32 tests across 5 files (run from `frontend/`):

```bash
cd frontend && npm test
```

## Frontend production build

Type-checks, then outputs to `frontend/dist`:

```bash
cd frontend && npm run build
```

Linting (Oxlint):

```bash
cd frontend && npm run lint
```

## Known limitations (read before using)

- **Single-user, loopback-only demo.** The runtime policy permits at most one
  active account: bootstrap refuses a second distinct account and startup fails
  closed if more than one active account exists (see "Single-user policy"). The
  `role` field is reserved and is **not** enforced. There is **no
  authorization/roles enforcement and no encryption** (see below).
- Authentication (backend and frontend login) is enforced and
  approval/rejection records the accountable actor, but there is **no
  authorization/roles enforcement and no encryption** — files are stored
  unencrypted on disk, and every authenticated user can read/modify all records
  (authentication is **not** record ownership or tenant isolation). Not suitable
  for real confidential legal or financial documents.
- No OCR: scanned PDFs report `extraction_failed` honestly instead of inventing text.
- Summaries are generated by a small local model and can be wrong; every summary
  is flagged `review_required` and must be checked by a human against the
  source excerpts provided alongside it.
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
  server-side evidence — not impossible with local models).
- SQLite file storage, single process, no migrations framework, no backups.
