"""Task endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import repository
from ..db import get_db
from ..schemas import Task, TaskCreate, TaskStatus
from ..security import require_user

router = APIRouter(
    prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_user)]
)


@router.get("", response_model=list[Task])
def list_tasks(
    matter_id: int | None = Query(default=None, ge=1),
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[Task]:
    """List tasks, optionally filtered by matter or status."""
    return [
        Task.model_validate(repository.row_to_dict(row))
        for row in repository.list_tasks(
            conn, matter_id=matter_id, status=status_filter
        )
    ]


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate, conn: sqlite3.Connection = Depends(get_db)
) -> Task:
    """Create a task. 404 if the referenced matter does not exist."""
    try:
        repository.get_matter(conn, payload.matter_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Matter {payload.matter_id} not found.",
        ) from exc
    try:
        row = repository.create_task(conn, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Matter {payload.matter_id} not found.",
        ) from exc
    return Task.model_validate(repository.row_to_dict(row))
