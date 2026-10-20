"""Pydantic request/response models for the business operations API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ClientStatus = Literal["open", "closed"]
TaskStatus = Literal["todo", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high"]

# --- Clients ---------------------------------------------------------------


class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned

    @field_validator("email")
    @classmethod
    def email_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or "@" not in cleaned or cleaned.startswith("@"):
            raise ValueError("email must look like an email address")
        return cleaned


class Client(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
    created_at: str


# --- Matters ----------------------------------------------------------------


class MatterCreate(BaseModel):
    client_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=200)
    status: ClientStatus = "open"

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned


class Matter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    title: str
    status: str
    created_at: str


# --- Tasks ------------------------------------------------------------------


class TaskCreate(BaseModel):
    matter_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=200)
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title must not be blank")
        return cleaned


class Task(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    matter_id: int
    title: str
    status: str
    priority: str
    created_at: str


# --- Documents --------------------------------------------------------------


class Document(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    matter_id: int
    original_filename: str
    size_bytes: int
    sha256: str
    status: str
    extraction_error: str | None
    created_at: str


class SourceExcerpt(BaseModel):
    """A machine-extracted source excerpt (facts), never model-generated."""

    page_number: int | None
    excerpt: str


class DocumentSummary(BaseModel):
    """Summary response. `source_excerpts` are extracted document facts;
    `summary` and `key_points` are model-generated and require human review."""

    document_id: int
    document_title: str
    source_excerpts: list[SourceExcerpt]
    source_note: str
    summary: str
    key_points: list[str]
    model: str
    machine_generated: bool = True
    review_required: bool = True
    generated_at: str


class OllamaHealth(BaseModel):
    available: bool
    models: list[str]
    summary_model: str
    embed_model: str


class DocumentIndexStatus(BaseModel):
    """Honest embedding-index state for documents (Step 5.3)."""

    embed_model: str
    dimension: int | None
    indexed_chunks: int
    indexed_documents: int
    total_documents: int
    stale: bool


class DocumentIndexResult(BaseModel):
    """Outcome of an indexing (backfill/retry) request.

    Counts are honest: `failed` documents keep their previous index untouched
    (or stay unindexed) and can be retried; keyword retrieval still works.
    """

    embed_model: str
    dimension: int | None
    documents: int
    indexed: int
    skipped: int
    failed: int
    errors: list[str] = []


# --- Assistant (read-only Q&A) ----------------------------------------------


class AssistantRequest(BaseModel):
    """Question submitted to the read-only assistant."""

    question: str = Field(..., min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be blank")
        return cleaned


class AssistantEvidence(BaseModel):
    """A supporting business-record reference.

    Built server-side by the assistant service from the rows retrieval actually
    returned (clients, matters, tasks, and document chunks). Each instance
    points at a real existing record with a stable reference; evidence is never
    taken from model output.
    """

    kind: str = Field(..., description="Record type, e.g. client, matter, task")
    reference: str = Field(..., description="Human-readable record reference")
    excerpt: str = Field(..., description="Relevant extracted text or field values")


class AssistantResponse(BaseModel):
    """Read-only assistant answer.

    `evidence` holds server-built references to the business records retrieval
    actually returned (clients, matters, tasks and document chunks); it is never
    taken from model output, and it is empty only when retrieval found nothing.
    `evidence_sufficient` is true exactly when retrieval returned at least one
    record — never inferred from how confident the answer sounds. The `answer`
    itself is machine-generated and requires human review.
    """

    answer: str
    evidence: list[AssistantEvidence]
    evidence_sufficient: bool
    machine_generated: bool
    review_required: bool
    model: str


# --- Proposed workflow actions (Step 5.4) -----------------------------------

ActionType = Literal["create_task"]


class ActionRequest(BaseModel):
    """Natural-language request for the assistant to PROPOSE an action.

    This never executes anything; it asks the server to suggest a workflow
    action (currently only `create_task`) grounded in retrieved evidence.
    """

    request: str = Field(..., min_length=1, max_length=2000)

    @field_validator("request")
    @classmethod
    def request_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("request must not be blank")
        return cleaned


class ProposedTask(BaseModel):
    """Structured, server-validated payload for a proposed task.

    `matter_id` always references an existing matter (validated server-side);
    `due_date` is optional and null when the evidence does not establish one.
    """

    title: str
    description: str
    matter_id: int
    matter_title: str
    due_date: str | None = None


class ActionProposal(BaseModel):
    """A proposed workflow action awaiting human approval.

    `proposal_id` is a server-generated opaque identifier for a PERSISTED
    proposal row. It is not an authorisation token. `status` is always
    `pending_approval` in this response: no task has been created yet.
    Execution happens only when a human approves it via the approval endpoints.
    """

    proposal_id: str
    action_type: ActionType
    status: Literal["pending_approval"] = "pending_approval"
    proposed: ProposedTask
    evidence: list[AssistantEvidence]
    notice: str
    machine_generated: bool = True
    review_required: bool = True
    model: str


class ActionProposalResponse(BaseModel):
    """Envelope for a propose-action request.

    `proposal` is null when no safe, actionable proposal could be produced
    (insufficient evidence, no suitable matter, or unusable model output);
    `reason` then explains why in safe, non-sensitive terms.
    """

    proposal: ActionProposal | None = None
    reason: str | None = None
    machine_generated: bool = True
    review_required: bool = True


# --- Persisted proposals and human decisions (Step 5.5) ---------------------

ProposalStatus = Literal[
    "pending_approval", "approved", "executed", "rejected", "failed"
]


class ProposalRecord(BaseModel):
    """A persisted proposal as stored on the server.

    `status` reflects real persisted state. `created_task_id` is set only after
    a successful approval+execution. `proposal_id` is an opaque, server-
    generated id and is NOT an authorisation token.
    """

    proposal_id: str
    action_type: ActionType
    status: ProposalStatus
    proposed: ProposedTask
    evidence: list[AssistantEvidence]
    model: str
    created_task_id: int | None = None
    execution_error: str | None = None
    created_at: str
    updated_at: str
    decided_at: str | None = None
    decided_by_user_id: int | None = None
    executed_at: str | None = None
    notice: str


class ProposalDecision(BaseModel):
    """Result of approving or rejecting a proposal.

    On approval, `status` is `executed` and `created_task` is the task that was
    created exactly once. On rejection, `status` is `rejected`. `notice` states
    plainly what happened.
    """

    proposal_id: str
    status: ProposalStatus
    created_task: Task | None = None
    execution_error: str | None = None
    notice: str


# --- Authentication (Step 6.2C-2) -------------------------------------------


class LoginRequest(BaseModel):
    """Credentials for POST /auth/login.

    Only username and password are accepted; surplus fields are ignored by
    Pydantic's default extra handling and can never grant identity.
    """

    username: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=1, max_length=1024)


class CurrentUser(BaseModel):
    """Safe public fields for the authenticated caller.

    Never includes password hashes, salts or session/CSRF token data.
    """

    id: int
    username: str
    role: str

