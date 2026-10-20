"""Tests for the read-only assistant endpoint (Step 5.1).

Unit tests use an injected fake provider and monkeypatched httpx — no running
Ollama server is required.
"""

import pytest
from app.llm import (
    MAX_ANSWER_CHARS,
    AssistantOutput,
    LLMUnavailableError,
)

QUESTION = "What does this prototype currently support?"


class FakeAssistant:
    """Provider double — records the prompts it receives."""

    assistant_model = "fake-model"

    def __init__(self) -> None:
        self.received: list[str] = []

    def answer(self, prompt: str) -> AssistantOutput:
        self.received.append(prompt)
        return AssistantOutput(
            text="It supports clients, matters, tasks and documents.",
            model=self.assistant_model,
        )


class BrokenAssistant:
    assistant_model = "broken-model"

    def answer(self, question: str) -> AssistantOutput:
        raise LLMUnavailableError("Could not reach the assistant service.")


@pytest.fixture()
def fake() -> FakeAssistant:
    return FakeAssistant()


def _override(client, fake: FakeAssistant):
    from app.main import app
    from app.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.get_assistant] = lambda: fake
    return app


def test_ask_returns_contract(client, fake) -> None:
    _override(client, fake)
    try:
        response = client.post("/assistant/ask", json={"question": QUESTION})
    finally:
        from app.main import app

        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "It supports clients, matters, tasks and documents."
    assert body["evidence"] == []
    assert body["evidence_sufficient"] is False
    assert body["machine_generated"] is True
    assert body["review_required"] is True
    assert body["model"] == "fake-model"
    # The question reached the provider inside the composed prompt.
    assert len(fake.received) == 1
    assert QUESTION in fake.received[0]


def test_ask_strips_whitespace(client, fake) -> None:
    _override(client, fake)
    try:
        response = client.post("/assistant/ask", json={"question": f"  {QUESTION}  "})
    finally:
        from app.main import app

        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert QUESTION in fake.received[0]


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_ask_rejects_blank_questions(client, question) -> None:
    assert client.post("/assistant/ask", json={"question": question}).status_code == 422


def test_ask_rejects_overlong_questions(client) -> None:
    response = client.post("/assistant/ask", json={"question": "x" * 2001})
    assert response.status_code == 422


def test_ask_accepts_exactly_max_length(client, fake) -> None:
    _override(client, fake)
    try:
        response = client.post("/assistant/ask", json={"question": "x" * 2000})
    finally:
        from app.main import app

        app.dependency_overrides.clear()
    assert response.status_code == 200


def test_ask_returns_503_when_provider_unavailable(client) -> None:
    _override(client, BrokenAssistant())
    try:
        response = client.post("/assistant/ask", json={"question": QUESTION})
    finally:
        from app.main import app

        app.dependency_overrides.clear()

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "Could not reach" in detail
    # Honest error only — no stack traces, paths or env values.
    assert "Traceback" not in detail
    assert "/" not in detail


def test_ask_missing_body_422(client) -> None:
    assert client.post("/assistant/ask", json={}).status_code == 422


# --- LocalOllamaAssistant parsing (deterministic, httpx monkeypatched) ------


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, bad_json: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def test_local_assistant_parses_plain_text(monkeypatch) -> None:
    from app import llm

    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(200, {"response": "  Hello. "})
    )
    output = llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
    assert output.text == "Hello."
    assert output.model == "m"


def test_local_assistant_truncates_huge_answers(monkeypatch) -> None:
    from app import llm

    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *a, **k: _FakeResponse(200, {"response": "y" * (MAX_ANSWER_CHARS + 500)}),
    )
    output = llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
    assert len(output.text) == MAX_ANSWER_CHARS


def test_local_assistant_rejects_empty_response(monkeypatch) -> None:
    from app import llm

    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(200, {"response": "   "})
    )
    with pytest.raises(llm.LLMUnavailableError, match="empty response"):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")


def test_local_assistant_rejects_missing_response_key(monkeypatch) -> None:
    from app import llm

    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(200, {"other": 1})
    )
    with pytest.raises(llm.LLMUnavailableError, match="empty response"):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")


def test_local_assistant_rejects_non_200(monkeypatch) -> None:
    from app import llm

    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(500, {"response": "x"})
    )
    with pytest.raises(llm.LLMUnavailableError, match="HTTP 500"):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")


def test_local_assistant_rejects_bad_json(monkeypatch) -> None:
    from app import llm

    monkeypatch.setattr(
        llm.httpx, "post", lambda *a, **k: _FakeResponse(200, bad_json=True)
    )
    with pytest.raises(llm.LLMUnavailableError, match="unreadable"):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")


def test_local_assistant_wraps_network_errors(monkeypatch) -> None:
    import httpx

    from app import llm

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm.httpx, "post", boom)
    with pytest.raises(llm.LLMUnavailableError, match="Could not reach"):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
