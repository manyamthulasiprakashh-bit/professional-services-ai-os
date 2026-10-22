"""OpenRouter provider tests (network-free, mocked HTTP).

The provider is exercised through the same ``Summarizer`` and ``Assistant``
protocols as Ollama. No test contacts OpenRouter or needs a real API key.
"""

import json
import logging

import httpx
import pytest

from app import llm
from app.extraction import ExtractedChunk

API_KEY = "sk-or-test-KEY-SENTINEL-9f3a"
PROMPT_SENTINEL = "ZQ-OPENROUTER-PROMPT-SENTINEL-9f3a"
MODEL = "test/model-available-now"


def _use_openrouter(monkeypatch, **overrides) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    monkeypatch.setenv("OPENROUTER_API_KEY", API_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.test/api/v1/")
    monkeypatch.setenv("OPENROUTER_TIMEOUT", "12.5")
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)


def _response(
    payload: object = None,
    status: int = 200,
    raw: bytes | None = None,
) -> httpx.Response:
    if raw is not None:
        return httpx.Response(status, content=raw)
    if payload is None:
        payload = {"choices": [{"message": {"content": "ok"}}]}
    return httpx.Response(status, json=payload)


def _capture(monkeypatch, response: httpx.Response) -> dict:
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    return captured


def _assistant_reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_ollama_is_still_the_default(monkeypatch) -> None:
    monkeypatch.delenv("PS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("PS_EMBED_PROVIDER", raising=False)
    # With fail-closed enforcement, defaults are not safe; must be explicit
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.llm_provider_name()
    monkeypatch.delenv("PS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.get_summarizer()
    monkeypatch.delenv("PS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.get_assistant()
    monkeypatch.delenv("PS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("PS_EMBED_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.validate_llm_configuration()


def test_openrouter_configuration_selects_hosted_implementations(
    monkeypatch,
) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()

    assert llm.llm_provider_name() == "openrouter"
    assert isinstance(llm.get_summarizer(), llm.OpenRouterSummarizer)
    assert isinstance(llm.get_assistant(), llm.OpenRouterAssistant)
    assert isinstance(llm.get_embedder(), llm.DisabledEmbedder)
    assert llm.openrouter_base_url() == "https://openrouter.test/api/v1"
    assert llm.openrouter_timeout() == 12.5


def test_openrouter_defaults_to_https_base_and_180s_timeout(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    monkeypatch.setenv("OPENROUTER_API_KEY", API_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)

    assert llm.openrouter_base_url() == llm.DEFAULT_OPENROUTER_BASE_URL
    assert llm.openrouter_timeout() == llm.DEFAULT_OPENROUTER_TIMEOUT_SECONDS
    assert llm.validate_llm_configuration() == "openrouter"


@pytest.mark.parametrize(
    "timeout",
    ["0", "-1", "600.1", "nan", "infinity", "not-a-number"],
)
def test_openrouter_timeout_is_bounded(monkeypatch, timeout: str) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("OPENROUTER_TIMEOUT", timeout)

    with pytest.raises(ValueError, match="OPENROUTER_TIMEOUT"):
        llm.openrouter_timeout()


def test_openrouter_base_url_rejects_plain_http_outside_loopback(
    monkeypatch,
) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://openrouter.test/api/v1")

    with pytest.raises(ValueError, match="HTTPS"):
        llm.openrouter_base_url()


def test_openrouter_base_url_allows_loopback_for_tests(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9999/api/v1")

    assert llm.openrouter_base_url() == "http://127.0.0.1:9999/api/v1"


def test_unsupported_provider_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "some-other-provider")

    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.llm_provider_name()
    monkeypatch.setenv("PS_LLM_PROVIDER", "some-other-provider")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.get_assistant()
    monkeypatch.setenv("PS_LLM_PROVIDER", "some-other-provider")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.validate_llm_configuration()


def test_ollama_provider_rejected_everywhere(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.llm_provider_name()
    monkeypatch.setenv("PS_LLM_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.get_assistant()
    monkeypatch.setenv("PS_LLM_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.validate_llm_configuration()


def test_missing_provider_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("PS_LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.llm_provider_name()
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.get_summarizer()
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.get_assistant()
    with pytest.raises(ValueError, match="PS_LLM_PROVIDER"):
        llm.validate_llm_configuration()


def test_missing_embed_provider_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", API_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)
    monkeypatch.delenv("PS_EMBED_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="PS_EMBED_PROVIDER"):
        llm.validate_llm_configuration()
    with pytest.raises(ValueError, match="PS_EMBED_PROVIDER"):
        llm.get_embedder()


def test_missing_api_key_fails_clearly_without_leaking_a_value(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY") as exc_info:
        llm.get_assistant()
    assert API_KEY not in str(exc_info.value)


def test_missing_model_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    monkeypatch.setenv("OPENROUTER_API_KEY", API_KEY)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    with pytest.raises(ValueError, match="OPENROUTER_MODEL"):
        llm.get_summarizer()


def test_assistant_request_has_expected_shape(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    captured = _capture(monkeypatch, _response(_assistant_reply("  Answer. ")))

    output = llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert output.text == "Answer."
    assert output.model == MODEL
    assert captured["url"] == "https://openrouter.test/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert captured["json"] == {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT_SENTINEL}],
        "temperature": 0.2,
        "max_tokens": llm.DEFAULT_OPENROUTER_MAX_TOKENS,
        "stream": False,
    }
    assert captured["timeout"] == 12.5


def test_summarizer_request_uses_existing_prompt_and_interface(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    content = json.dumps(
        {"summary": "A short summary.", "key_points": ["Point one"]}
    )
    captured = _capture(monkeypatch, _response(_assistant_reply(content)))

    output = llm.OpenRouterSummarizer().summarize(
        "Memo", [ExtractedChunk(page_number=1, content="The body text.")]
    )

    assert output.summary == "A short summary."
    assert output.key_points == ["Point one"]
    assert output.model == MODEL
    assert captured["url"] == "https://openrouter.test/api/v1/chat/completions"
    prompt = captured["json"]["messages"][0]["content"]
    assert "Document title: Memo" in prompt
    assert "The body text." in prompt
    assert captured["json"]["max_tokens"] == llm.DEFAULT_OPENROUTER_MAX_TOKENS


def test_assistant_answer_is_truncated_to_existing_output_bound(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    _capture(
        monkeypatch,
        _response(_assistant_reply("y" * (llm.MAX_ANSWER_CHARS + 500))),
    )

    output = llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert len(output.text) == llm.MAX_ANSWER_CHARS


def test_openrouter_respects_the_shared_generation_gate(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _response(_assistant_reply("ok"))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    gate = llm.generation_gate()
    gate.acquire()
    try:
        with pytest.raises(llm.LLMConcurrencyError):
            llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)
    finally:
        gate.release()

    assert calls == []


def test_openrouter_failure_does_not_fall_back_to_ollama(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _response(status=500, payload={"error": {"message": "down"}})

    monkeypatch.setattr(llm.httpx, "post", fake_post)

    with pytest.raises(llm.LLMUnavailableError):
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert calls == ["https://openrouter.test/api/v1/chat/completions"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "authentication"),
        (403, "authentication"),
        (429, "rate limited"),
        (500, "temporarily unavailable"),
        (502, "temporarily unavailable"),
        (418, "HTTP 418"),
    ],
)
def test_http_errors_are_safe_and_do_not_echo_provider_body(
    monkeypatch, status, expected
) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    _capture(
        monkeypatch,
        _response(
            status=status,
            payload={
                "error": {"message": f"{PROMPT_SENTINEL} {API_KEY}"},
            },
        ),
    )

    with pytest.raises(llm.LLMUnavailableError, match=expected) as exc_info:
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert PROMPT_SENTINEL not in str(exc_info.value)
    assert API_KEY not in str(exc_info.value)


def test_connection_errors_are_safe(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()

    def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(llm.httpx, "post", boom)

    with pytest.raises(llm.LLMUnavailableError, match="Could not reach") as exc_info:
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert PROMPT_SENTINEL not in str(exc_info.value)
    assert API_KEY not in str(exc_info.value)


def test_timeout_errors_are_reported_explicitly_and_safely(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()

    def boom(*args, **kwargs):
        raise httpx.ReadTimeout(f"timed out {API_KEY} {PROMPT_SENTINEL}")

    monkeypatch.setattr(llm.httpx, "post", boom)

    with pytest.raises(llm.LLMUnavailableError, match="timed out") as exc_info:
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert PROMPT_SENTINEL not in str(exc_info.value)
    assert API_KEY not in str(exc_info.value)


def test_malformed_json_is_safe(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    _capture(monkeypatch, _response(raw=b"not json"))

    with pytest.raises(llm.LLMUnavailableError, match="unreadable"):
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {"content": 123}}]},
        {"choices": "not-a-list"},
    ],
)
def test_missing_choices_and_empty_content_are_safe(monkeypatch, payload) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    _capture(monkeypatch, _response(payload))

    with pytest.raises(llm.LLMUnavailableError, match="empty response"):
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)


def test_length_finish_reason_with_empty_content_is_explained_safely(
    monkeypatch,
) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    _capture(
        monkeypatch,
        _response(
            {
                "choices": [
                    {
                        "message": {"content": None},
                        "finish_reason": "length",
                    }
                ]
            }
        ),
    )

    with pytest.raises(llm.LLMUnavailableError, match="response limit") as exc_info:
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)
    assert API_KEY not in str(exc_info.value)
    assert PROMPT_SENTINEL not in str(exc_info.value)


def test_http_200_error_body_is_safe(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    _capture(
        monkeypatch,
        _response({"error": {"message": f"{API_KEY} {PROMPT_SENTINEL}"}}),
    )

    with pytest.raises(llm.LLMUnavailableError, match="error response") as exc_info:
        llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)
    assert API_KEY not in str(exc_info.value)
    assert PROMPT_SENTINEL not in str(exc_info.value)


def test_openrouter_max_tokens_is_configurable(monkeypatch) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "512")
    captured = _capture(monkeypatch, _response(_assistant_reply("ok")))

    llm.OpenRouterAssistant().answer(PROMPT_SENTINEL)

    assert captured["json"]["max_tokens"] == 512


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "8193", "not-a-number"],
)
def test_openrouter_max_tokens_is_bounded(monkeypatch, value: str) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", value)

    with pytest.raises(ValueError, match="OPENROUTER_MAX_TOKENS"):
        llm.openrouter_max_tokens()


def test_ask_endpoint_uses_openrouter_and_returns_expected_content(
    client, monkeypatch
) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    captured = _capture(
        monkeypatch,
        _response(_assistant_reply("The workspace has one matter.")),
    )

    response = client.post("/assistant/ask", json={"question": "What exists?"})

    assert response.status_code == 200
    assert response.json()["answer"] == "The workspace has one matter."
    assert response.json()["model"] == MODEL
    assert captured["headers"]["Authorization"] == f"Bearer {API_KEY}"


def test_endpoint_does_not_leak_key_prompt_or_provider_body(
    client, monkeypatch, caplog
) -> None:
    _use_openrouter(monkeypatch)
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    llm.validate_llm_configuration()
    caplog.set_level(logging.DEBUG)

    def boom(*args, **kwargs):
        raise httpx.ConnectError(f"failed with {API_KEY} and {PROMPT_SENTINEL}")

    monkeypatch.setattr(llm.httpx, "post", boom)

    response = client.post("/assistant/ask", json={"question": PROMPT_SENTINEL})

    assert response.status_code == 503
    assert PROMPT_SENTINEL not in response.text
    assert API_KEY not in response.text
    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app" or record.name.startswith("app.")
    )
    assert PROMPT_SENTINEL not in application_logs
    assert API_KEY not in application_logs


def test_disabled_embedder_returns_safe_error(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PS_EMBED_PROVIDER", "none")
    monkeypatch.setenv("OPENROUTER_API_KEY", API_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)
    llm.validate_llm_configuration()
    embedder = llm.get_embedder()
    with pytest.raises(llm.LLMUnavailableError, match="disabled"):
        embedder.embed(["hello"])
