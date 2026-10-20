"""Professional Services AI OS backend application.

Milestone 1 exposes only lightweight discovery and health endpoints so that
the frontend and future services have a stable contract to depend on.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import auth as auth_module
from .db import connect, resolve_db_path
from .routers import assistant, auth, clients, documents, matters, ollama, tasks

APP_NAME = "Professional Services AI OS"
APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Enforce the single-user demo policy at startup.

    Opens the configured database (applying the idempotent schema, which never
    mutates data) and fails closed if more than one active account exists. Zero
    accounts is allowed so first-time setup can still bootstrap a user.
    """
    conn = connect(resolve_db_path())
    try:
        auth_module.verify_single_user_policy(conn)
    finally:
        conn.close()
    yield

# Local Vite development server origins only. Exact origins, never a wildcard:
# credentialed CORS (cookies) is enabled below and must never be combined with
# a wildcard origin.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# Smallest explicit set of request headers the frontend actually sends:
# `Content-Type` for JSON bodies and `X-CSRF-Token` for the session-bound
# double-submit token on unsafe methods. Multipart uploads set Content-Type
# themselves. No wildcard: keeping this explicit avoids accidentally
# reflecting arbitrary client headers.
ALLOWED_HEADERS = [
    "Content-Type",
    "X-CSRF-Token",
]

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Backend foundation for an AI-powered operating system aimed at "
        "professional services businesses. This is an independent prototype."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=ALLOWED_HEADERS,
)

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(matters.router)
app.include_router(tasks.router)
app.include_router(documents.router)
app.include_router(ollama.router)
app.include_router(assistant.router)


class HealthResponse(BaseModel):
    """Response contract for GET /health."""

    status: str = Field(..., description="Service health status")
    application: str = Field(..., description="Application name")
    version: str = Field(..., description="Application version")


class RootResponse(BaseModel):
    """Response contract for GET /."""

    application: str = Field(..., description="Application name")
    description: str = Field(..., description="Short description of the application")
    version: str = Field(..., description="Application version")
    docs: str = Field(..., description="Interactive API documentation URL")
    health: str = Field(..., description="Health check URL")


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Return the service health status."""
    return HealthResponse(
        status="healthy", application=APP_NAME, version=APP_VERSION
    )


@app.get("/", response_model=RootResponse, tags=["system"])
async def root() -> RootResponse:
    """Describe the application and link to the API documentation."""
    return RootResponse(
        application=APP_NAME,
        description=(
            "AI-powered operating system prototype for professional services "
            "businesses: business data, documents, workflows, and AI assistance "
            "will be integrated here in later milestones."
        ),
        version=APP_VERSION,
        docs="/docs",
        health="/health",
    )
