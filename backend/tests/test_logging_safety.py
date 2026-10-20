"""Sensitive-data logging regression tests (Step 7.2B).

The application deliberately logs operational SHAPE only (ids, sizes, counts),
never secrets or content. These tests exercise representative paths with
distinctive sentinel strings and assert no application log record contains them.

Scope is deliberately precise: only records emitted by application loggers
(``app.*``) are inspected, so TestClient/uvicorn/httpx framework diagnostics
cannot create false positives. We do not assert logs are empty — safe
operational events are expected.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.ratelimit_config import failed_login_limiter, login_limiter
from app.security import CSRF_HEADER_NAME
from conftest import TEST_PASSWORD, TEST_USERNAME, login_client, make_user

PASSWORD_SENTINEL = "ZQ-PASSWORD-SENTINEL-9f3a"
SESSION_SENTINEL = "ZQ-SESSIONTOKEN-SENTINEL-9f3a"
CSRF_SENTINEL = "ZQ-CSRF-SENTINEL-9f3a"
QUESTION_SENTINEL = "ZQ-QUESTION-SENTINEL-9f3a"
MODEL_ANSWER_SENTINEL = "ZQ-MODELANSWER-SENTINEL-9f3a"
DOCTYPE_SENTINEL = "ZQ-DOCTEXT-SENTINEL-9f3a"

SENTINELS = (
    PASSWORD_SENTINEL,
    SESSION_SENTINEL,
    CSRF_SENTINEL,
    QUESTION_SENTINEL,
    MODEL_ANSWER_SENTINEL,
    DOCTYPE_SENTINEL,
)


def _app_log_text(caplog) -> str:
    """Concatenated messages from application loggers only."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app" or record.name.startswith("app.")
    )


def _assert_no_sentinels(caplog, *extra: str) -> None:
    text = _app_log_text(caplog)
    for sentinel in (*SENTINELS, *extra):
        assert sentinel not in text, f"sentinel leaked to logs: {sentinel}"


def _login(anon: TestClient, username: str, password: str):
    token = anon.get("/auth/csrf").json()["csrf_token"]
    return anon.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers={CSRF_HEADER_NAME: token},
    )


def _seed_matter(client) -> int:
    client_id = client.post("/clients", json={"name": "Acme Legal"}).json()["id"]
    return client.post(
        "/matters", json={"client_id": client_id, "title": "Logging matter"}
    ).json()["id"]


def test_failed_login_never_logs_submitted_password(anon_client, caplog) -> None:
    caplog.set_level(logging.INFO, logger="app")
    response = _login(anon_client, TEST_USERNAME, PASSWORD_SENTINEL)
    assert response.status_code in (401, 429)
    _assert_no_sentinels(caplog)


def test_successful_login_logs_shape_not_secrets(anon_client, conn, caplog) -> None:
    make_user(conn)
    caplog.set_level(logging.INFO, logger="app")
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    assert response.status_code == 200
    # A safe operational record is expected, and it must not carry the secret.
    assert any(r.name.startswith("app") for r in caplog.records)
    _assert_no_sentinels(caplog, TEST_PASSWORD)
    raw_token = anon_client.cookies.get("ps_session")
    assert raw_token not in _app_log_text(caplog)


def test_blocked_login_never_logs_submitted_password(anon_client, conn, caplog) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    for _ in range(failed_login_limiter.max_events):
        _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")

    caplog.set_level(logging.INFO, logger="app")
    blocked = _login(anon_client, TEST_USERNAME, PASSWORD_SENTINEL)
    assert blocked.status_code == 429
    _assert_no_sentinels(caplog)


def test_assistant_ask_never_logs_question_or_model_output(client, caplog) -> None:
    from app.llm import AssistantOutput
    from app.routers import assistant as assistant_router

    class RecordingAssistant:
        assistant_model = "fake-model"

        def answer(self, prompt: str) -> AssistantOutput:
            return AssistantOutput(text=MODEL_ANSWER_SENTINEL, model=self.assistant_model)

    app.dependency_overrides[assistant_router.get_assistant] = (
        lambda: RecordingAssistant()
    )
    try:
        caplog.set_level(logging.INFO, logger="app")
        response = client.post("/assistant/ask", json={"question": QUESTION_SENTINEL})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["answer"] == MODEL_ANSWER_SENTINEL
    _assert_no_sentinels(caplog)


def test_assistant_propose_action_never_logs_request_or_output(client, caplog) -> None:
    from app.llm import AssistantOutput
    from app.routers import assistant as assistant_router

    class RecordingAssistant:
        assistant_model = "fake-model"

        def answer(self, prompt: str) -> AssistantOutput:
            return AssistantOutput(text="no safe action", model=self.assistant_model)

    app.dependency_overrides[assistant_router.get_assistant] = (
        lambda: RecordingAssistant()
    )
    try:
        caplog.set_level(logging.INFO, logger="app")
        response = client.post(
            "/assistant/propose-action", json={"request": QUESTION_SENTINEL}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    _assert_no_sentinels(caplog)


def test_document_upload_never_logs_document_text(client, docs_dir, caplog) -> None:
    matter_id = _seed_matter(client)
    caplog.set_level(logging.INFO, logger="app")
    response = client.post(
        "/documents",
        data={"matter_id": str(matter_id)},
        files={"file": ("memo.txt", DOCTYPE_SENTINEL.encode("utf-8"))},
    )
    assert response.status_code == 201
    _assert_no_sentinels(caplog)


def test_logging_paths_do_not_log_raw_session_or_csrf_tokens(client, conn, caplog) -> None:
    """Even with active traffic, no raw session/CSRF token hashes appear."""
    caplog.set_level(logging.DEBUG, logger="app")
    # Force the session/index/assistant logging paths to run.
    _seed_matter(client)
    client.get("/documents/index")
    _assert_no_sentinels(caplog)
    for row in conn.execute("SELECT token_hash, csrf_token_hash FROM sessions"):
        assert row["token_hash"] not in _app_log_text(caplog)
        assert row["csrf_token_hash"] not in _app_log_text(caplog)
