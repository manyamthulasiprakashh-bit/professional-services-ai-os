"""Matter endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import repository
from ..db import get_db
from ..schemas import Matter, MatterCreate
from ..security import require_user

router = APIRouter(
    prefix="/matters", tags=["matters"], dependencies=[Depends(require_user)]
)


@router.get("", response_model=list[Matter])
def list_matters(
    client_id: int | None = Query(default=None, ge=1),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[Matter]:
    """List all matters, optionally filtered by client."""
    return [
        Matter.model_validate(repository.row_to_dict(row))
        for row in repository.list_matters(conn, client_id=client_id)
    ]


@router.post("", response_model=Matter, status_code=status.HTTP_201_CREATED)
def create_matter(
    payload: MatterCreate, conn: sqlite3.Connection = Depends(get_db)
) -> Matter:
    """Create a matter. 404 if the referenced client does not exist."""
    try:
        repository.get_client(conn, payload.client_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {payload.client_id} not found.",
        ) from exc
    try:
        row = repository.create_matter(conn, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {payload.client_id} not found.",
        ) from exc
    return Matter.model_validate(repository.row_to_dict(row))
