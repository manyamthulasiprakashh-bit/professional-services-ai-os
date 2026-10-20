"""Client endpoints."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import repository
from ..db import get_db
from ..schemas import Client, ClientCreate
from ..security import require_user

router = APIRouter(
    prefix="/clients", tags=["clients"], dependencies=[Depends(require_user)]
)


@router.get("", response_model=list[Client])
def list_clients(conn: sqlite3.Connection = Depends(get_db)) -> list[Client]:
    """List all clients."""
    return [
        Client.model_validate(repository.row_to_dict(row))
        for row in repository.list_clients(conn)
    ]


@router.post("", response_model=Client, status_code=status.HTTP_201_CREATED)
def create_client(
    payload: ClientCreate, conn: sqlite3.Connection = Depends(get_db)
) -> Client:
    """Create a client. Rejects duplicate names with 409."""
    try:
        row = repository.create_client(conn, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A client named '{payload.name}' already exists.",
        ) from exc
    return Client.model_validate(repository.row_to_dict(row))
