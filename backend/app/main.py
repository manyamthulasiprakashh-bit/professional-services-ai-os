"""Professional Services AI OS backend application.

Milestone 1 exposes only lightweight discovery and health endpoints so that
the frontend and future services have a stable contract to depend on.
"""

import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import auth as auth_module
from .db import connect, resolve_db_path
from .llm import validate_llm_configuration
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
    validate_llm_configuration()
    conn = connect(resolve_db_path())
    try:
        auth_module.verify_single_user_policy(conn)
    finally:
        conn.close()
    yield

# Local Vite development server origins, used when PS_ALLOWED_ORIGINS is not
# set. Credentialed CORS (cookies) is enabled below, so origins must be exact
# and are never wildcarded.
DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _normalise_origin(raw: str) -> str:
    """Validate one origin and return its canonical form.

    Accepts only an exact ``http(s)://host[:port]`` origin. Paths, queries,
    fragments, userinfo, whitespace, wildcards and non-HTTP schemes are
    rejected. Trailing slashes are tolerated and removed.
    """
    candidate = raw.strip().rstrip("/")
    if not candidate or "*" in candidate or any(c.isspace() for c in candidate):
        raise ValueError("PS_ALLOWED_ORIGINS contains an invalid origin")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PS_ALLOWED_ORIGINS origins must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PS_ALLOWED_ORIGINS origins must not contain userinfo")
    if parsed.netloc.endswith(":"):
        raise ValueError("PS_ALLOWED_ORIGINS contains an invalid port")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError(
            "PS_ALLOWED_ORIGINS origins must not contain a path, query or fragment"
        )
    if not parsed.hostname:
        raise ValueError("PS_ALLOWED_ORIGINS contains an invalid origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("PS_ALLOWED_ORIGINS contains an invalid port") from exc

    host = parsed.hostname.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{host}{suffix}"


def parse_allowed_origins(raw: str | None) -> list[str]:
    """Parse, validate and de-duplicate a comma-separated origin allowlist.

    Missing or blank configuration falls back to the localhost development
    origins. Any invalid entry fails closed instead of being ignored.
    """
    if raw is None or not raw.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)

    origins: list[str] = []
    for entry in raw.split(","):
        origin = _normalise_origin(entry)
        if origin not in origins:
            origins.append(origin)
    if not origins:
        raise ValueError("PS_ALLOWED_ORIGINS must contain at least one origin")
    return origins


def configured_origins() -> list[str]:
    """Exact allowed browser origins from PS_ALLOWED_ORIGINS (read at startup)."""
    return parse_allowed_origins(os.environ.get("PS_ALLOWED_ORIGINS"))


# Evaluated once at import so CORS and the CSRF origin check share one list.
ALLOWED_ORIGINS = configured_origins()

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
