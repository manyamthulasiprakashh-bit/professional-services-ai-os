"""AI assistant endpoints: read-only Q&A and action proposals (Steps 5.4-5.5)."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
import sqlite3

from .. import assistant_service, approval, proposer, repository
from ..auth import SessionContext
from ..db import get_db
from ..llm import (
    LLMConcurrencyError,
    LLMUnavailableError,
    Assistant,
    Embedder,
    get_assistant,
    get_embedder,
)
from ..ratelimit_config import assistant_rate_limit
from ..schemas import (
    ActionProposalResponse,
    ActionRequest,
    AssistantEvidence,
    AssistantRequest,
    AssistantResponse,
    ProposalDecision,
    ProposalRecord,
)
from ..security import require_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/assistant", tags=["assistant"], dependencies=[Depends(require_user)]
)


@router.post("/ask", response_model=AssistantResponse)
def ask(
    payload: AssistantRequest,
    _limit: None = Depends(assistant_rate_limit),
    conn: sqlite3.Connection = Depends(get_db),
    assistant: Assistant = Depends(get_assistant),
    embedder: Embedder = Depends(get_embedder),
) -> AssistantResponse:
    """Answer a question using bounded hybrid retrieval + the local model.

    Evidence and `evidence_sufficient` are decided by server-side retrieval,
    never by the model. Semantic retrieval is best-effort: if the embedder is
    unavailable or the index is stale, keyword/ID retrieval still applies.
    503 only if the assistant service itself is unavailable. 429 when the
    per-user rate limit or the LLM concurrency gate is exceeded.
    """
    try:
        response = assistant_service.answer_question(
            payload.question, conn, assistant, embedder=embedder
        )
    except LLMConcurrencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # Log sizes and counts only — never questions, excerpts or answers.
    logger.info(
        "assistant ask: question_chars=%d answer_chars=%d evidence=%d",
        len(payload.question),
        len(response.answer),
        len(response.evidence),
    )
    return response


@router.post("/propose-action", response_model=ActionProposalResponse)
def propose_action(
    payload: ActionRequest,
    _limit: None = Depends(assistant_rate_limit),
    conn: sqlite3.Connection = Depends(get_db),
    assistant: Assistant = Depends(get_assistant),
    embedder: Embedder = Depends(get_embedder),
) -> ActionProposalResponse:
    """Propose a workflow action grounded in retrieved records, and persist it.

    Step 5.4/5.5 supports only `create_task` against an existing matter. This
    endpoint NEVER creates a task: it persists a `pending_approval` proposal
    (with a `proposed` audit event) and returns it. `proposal` is null when no
    safe action is warranted. 503 only if the assistant service is unavailable.
    429 when the per-user rate limit or the LLM concurrency gate is exceeded.
    """
    try:
        response = proposer.propose_action(
            payload.request, conn, assistant, embedder=embedder
        )
    except LLMConcurrencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # Log shape only — never request text, proposal titles or model output.
    logger.info(
        "assistant propose-action: request_chars=%d proposed=%s",
        len(payload.request),
        response.proposal is not None,
    )
    return response


def _proposal_record(row: sqlite3.Row) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=row["proposal_id"],
        action_type=row["action_type"],
        status=row["status"],
        proposed=json.loads(row["payload_json"]),
        evidence=[AssistantEvidence(**e) for e in json.loads(row["evidence_json"])],
        model=row["model"],
        created_task_id=row["created_task_id"],
        execution_error=row["execution_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        decided_at=row["decided_at"],
        decided_by_user_id=row["decided_by_user_id"],
        executed_at=row["executed_at"],
        notice=proposer.PROPOSAL_NOTICE,
    )


@router.get("/proposals/{proposal_id}", response_model=ProposalRecord)
def get_proposal(
    proposal_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> ProposalRecord:
    """Fetch a persisted proposal and its real status. 404 if unknown."""
    try:
        row = repository.get_proposal(conn, proposal_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found"
        ) from exc
    return _proposal_record(row)


@router.post("/proposals/{proposal_id}/approve", response_model=ProposalDecision)
def approve_proposal(
    proposal_id: str,
    context: SessionContext = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ProposalDecision:
    """Approve a persisted proposal, creating its task exactly once.

    Accepts NO client payload: the stored, hash-verified payload is used, and
    the accountable actor is the authenticated session user (`context.user_id`),
    never a body/query field. On success the task, the decided state and the
    `executed` audit event (with actor) commit together. A proposal that is
    already decided returns 409. Returns 404 if unknown.
    """
    try:
        new_status, created_task, error = approval.execute_proposal(
            conn, proposal_id, actor_user_id=context.user_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found"
        ) from exc
    except approval.ProposalConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except approval.ProposalBusy as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except approval.ProposalInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    logger.info(
        "assistant approve: proposal_id=%s status=%s task_id=%s",
        proposal_id,
        new_status,
        created_task.id if created_task else None,
    )
    if new_status == "executed":
        notice = approval.APPROVAL_NOTICE
    elif new_status == "rejected":
        notice = approval.REJECTION_NOTICE
    else:
        notice = approval.FAILED_NOTICE
    return ProposalDecision(
        proposal_id=proposal_id,
        status=new_status,
        created_task=created_task,
        execution_error=error,
        notice=notice,
    )


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalDecision)
def reject_proposal(
    proposal_id: str,
    context: SessionContext = Depends(require_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ProposalDecision:
    """Reject a pending proposal (no task created). 404 unknown, 409 decided.

    The accountable actor is the authenticated session user, never a body field.
    """
    try:
        new_status = approval.reject_proposal(
            conn, proposal_id, actor_user_id=context.user_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found"
        ) from exc
    except approval.ProposalConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except approval.ProposalBusy as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    logger.info("assistant reject: proposal_id=%s status=%s", proposal_id, new_status)
    return ProposalDecision(
        proposal_id=proposal_id,
        status=new_status,
        created_task=None,
        notice=approval.REJECTION_NOTICE,
    )
