"""Route-level rate-limit and LLM-concurrency integration tests (Step 6.3C).

Deterministic: no sleeps. Concurrency is exercised with injected fakes and
thread barriers. The shared limiter state is reset before each test by the
autouse `_reset_rate_limits` fixture in conftest.
"""

import threading

import pytest

from app import auth, llm
from app.llm import (
    AssistantOutput,
    LLMConcurrencyError,
    LLMUnavailableError,
    _ConcurrencyGate,
)
from app.main import app
from app.ratelimit import RateLimiter
from app.ratelimit_config import (
    assistant_limiter,
    document_limiter,
    failed_login_limiter,
    login_limiter,
)
from app.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from conftest import TEST_PASSWORD, TEST_USERNAME, make_user


# --- helpers ----------------------------------------------------------------


def _csrf(anon) -> str:
    return anon.get("/auth/csrf").json()["csrf_token"]


def _login(anon, username=TEST_USERNAME, password=TEST_PASSWORD):
    token = _csrf(anon)
    return anon.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers={CSRF_HEADER_NAME: token},
    )


class RecordingAssistant:
    """Assistant double that records how many times it is actually called."""

    assistant_model = "recording-model"

    def __init__(self) -> None:
        self.calls = 0

    def answer(self, prompt: str) -> AssistantOutput:
        self.calls += 1
        return AssistantOutput(text="ok", model=self.assistant_model)


class RecordingEmbedder:
    embed_model = "recording-embed"
    dimension = None

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        self.dimension = 3
        return [[0.1, 0.2, 0.3] for _ in texts]


def _override_assistant(fake):
    from app.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.get_assistant] = lambda: fake
    return app


def _override_embedder(fake):
    from app.routers import documents as documents_router

    app.dependency_overrides[documents_router.get_embedder] = lambda: fake
    return app


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- Part A/B: configuration validation -------------------------------------


def test_int_env_rejects_malformed_value(monkeypatch) -> None:
    from app import ratelimit_config as rc

    monkeypatch.setenv("PS_RL_ASSISTANT_MAX", "abc")
    with pytest.raises(ValueError):
        rc._int_env("PS_RL_ASSISTANT_MAX", 10)


def test_int_env_rejects_zero_and_negative(monkeypatch) -> None:
    from app import ratelimit_config as rc

    monkeypatch.setenv("PS_RL_TEST", "0")
    with pytest.raises(ValueError):
        rc._int_env("PS_RL_TEST", 10)
    monkeypatch.setenv("PS_RL_TEST", "-3")
    with pytest.raises(ValueError):
        rc._int_env("PS_RL_TEST", 10)


def test_float_env_rejects_malformed_and_non_positive(monkeypatch) -> None:
    from app import ratelimit_config as rc

    monkeypatch.setenv("PS_RL_TEST", "nope")
    with pytest.raises(ValueError):
        rc._float_env("PS_RL_TEST", 5.0)
    monkeypatch.setenv("PS_RL_TEST", "0")
    with pytest.raises(ValueError):
        rc._float_env("PS_RL_TEST", 5.0)


def test_int_env_uses_default_when_unset(monkeypatch) -> None:
    from app import ratelimit_config as rc

    monkeypatch.delenv("PS_RL_TEST", raising=False)
    assert rc._int_env("PS_RL_TEST", 42) == 42


def test_llm_concurrency_rejects_invalid(monkeypatch) -> None:
    monkeypatch.setenv("PS_LLM_MAX_CONCURRENCY", "0")
    with pytest.raises(ValueError):
        llm.llm_max_concurrency()
    monkeypatch.setenv("PS_LLM_MAX_CONCURRENCY", "x")
    with pytest.raises(ValueError):
        llm.llm_max_concurrency()


# --- Part C: login semantics ------------------------------------------------


def test_overall_login_limit_returns_429_with_retry_after(anon_client, conn) -> None:
    make_user(conn)
    login_limiter.reset()
    # Drive the overall limit to zero remaining, then one more attempt.
    for _ in range(login_limiter.max_events):
        _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_failed_login_limit_returns_429_with_retry_after(anon_client, conn) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    for _ in range(failed_login_limiter.max_events):
        r = _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")
        assert r.status_code == 401
    response = _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_successful_login_does_not_count_as_failure(anon_client, conn) -> None:
    make_user(conn)
    failed_login_limiter.reset()
    # A wrong attempt, then a success, then check the success didn't add a
    # second failure: after one more wrong attempt we should still be at 2.
    assert _login(anon_client, TEST_USERNAME, "wrong-password-xxxx").status_code == 401
    assert _login(anon_client, TEST_USERNAME, TEST_PASSWORD).status_code == 200
    assert _login(anon_client, TEST_USERNAME, "wrong-password-xxxx").status_code == 401
    # Failures attributable to this key == 2, comfortably below the limit.
    assert failed_login_limiter.max_events >= 5


def test_failed_login_limit_is_case_insensitive(anon_client, conn) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    # Spell the same account with different casing across attempts.
    spellings = [TEST_USERNAME, TEST_USERNAME.upper(), TEST_USERNAME.title(),
                 TEST_USERNAME.upper(), TEST_USERNAME.lower()]
    for spelling in spellings[: failed_login_limiter.max_events]:
        assert _login(anon_client, spelling, "wrong").status_code == 401
    # Changing spelling must not reset the counter.
    final = _login(anon_client, TEST_USERNAME.upper(), "wrong")
    assert final.status_code == 429


def test_login_throttle_does_not_reveal_account_existence(
    anon_client, conn
) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    n = failed_login_limiter.max_events
    # Hammer BOTH an existing account and a non-existent one the same number
    # of times. Exhaustion behaves identically regardless of existence, so the
    # throttle leaks nothing about whether the account is real.
    for _ in range(n):
        _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")
        _login(anon_client, "no-such-user-at-all", "wrong-password-xxxx")
    known = _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")
    unknown = _login(anon_client, "no-such-user-at-all", "wrong-password-xxxx")
    assert known.status_code == 429
    assert unknown.status_code == 429
    # Same generic text shape; neither reveals existence.
    assert known.json()["detail"] == unknown.json()["detail"]


def test_login_success_still_works_within_limits(anon_client, conn) -> None:
    make_user(conn)
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    assert response.status_code == 200
    assert response.json() == {"id": 1, "username": TEST_USERNAME, "role": "user"}


# --- Part D: assistant limits -----------------------------------------------


def test_assistant_limit_is_per_user_and_denies_before_work(client) -> None:
    fake = RecordingAssistant()
    _override_assistant(fake)
    assistant_limiter.reset()
    for _ in range(assistant_limiter.max_events):
        r = client.post("/assistant/ask", json={"question": "hello"})
        assert r.status_code == 200
    calls_before = fake.calls
    denied = client.post("/assistant/ask", json={"question": "hello"})
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) >= 1
    # The expensive handler must NOT have been invoked after denial.
    assert fake.calls == calls_before


def test_propose_action_limit_returns_429(client) -> None:
    fake = RecordingAssistant()
    _override_assistant(fake)
    assistant_limiter.reset()
    for _ in range(assistant_limiter.max_events):
        client.post("/assistant/propose-action", json={"request": "create a task"})
    denied = client.post("/assistant/propose-action", json={"request": "create a task"})
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) >= 1


def test_sequence_leader_waiting_tolerance() -> None:
    # Sanity: the limiter used by the route forbids the (N+1)th event.
    limiter = RateLimiter(2, 60.0)
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed is False


# --- Part D: document limits ------------------------------------------------


def _make_matter(client) -> int:
    r = client.post("/clients", json={"name": "Acme"})
    assert r.status_code == 201
    client_id = r.json()["id"]
    r = client.post("/matters", json={"client_id": client_id, "title": "Matter"})
    assert r.status_code == 201
    return r.json()["id"]


def test_document_upload_limit_denies_before_work(client, docs_dir) -> None:
    matter_id = _make_matter(client)
    embedder = RecordingEmbedder()
    _override_embedder(embedder)
    document_limiter.reset()
    for i in range(document_limiter.max_events):
        r = client.post(
            "/documents",
            data={"matter_id": matter_id},
            files={"file": (f"doc{i}.txt", f"hello world {i}".encode(), "text/plain")},
        )
        assert r.status_code == 201, r.text
    calls_before = embedder.calls
    denied = client.post(
        "/documents",
        data={"matter_id": matter_id},
        files={"file": ("over.txt", b"blocked content", "text/plain")},
    )
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) >= 1
    # Denied upload must not have reached extraction/indexing.
    assert embedder.calls == calls_before


def test_reextract_limit_returns_429(client, docs_dir) -> None:
    matter_id = _make_matter(client)
    embedder = RecordingEmbedder()
    _override_embedder(embedder)
    r = client.post(
        "/documents",
        data={"matter_id": matter_id},
        files={"file": ("a.txt", b"content here", "text/plain")},
    )
    assert r.status_code == 201
    document_id = r.json()["id"]
    document_limiter.reset()
    for _ in range(document_limiter.max_events):
        assert client.post(f"/documents/{document_id}/extract").status_code == 200
    denied = client.post(f"/documents/{document_id}/extract")
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) >= 1


# --- Part D: auth/CSRF still enforced ---------------------------------------


def test_denied_route_requires_auth_first(anon_client) -> None:
    # No session: still 401, not 429 — auth precedes limiting.
    assert anon_client.post("/assistant/ask", json={"question": "q"}).status_code == 401
    assert anon_client.post("/documents").status_code == 401


def test_rate_limited_route_still_requires_csrf(client) -> None:
    saved = client.headers.get(CSRF_HEADER_NAME)
    try:
        del client.headers[CSRF_HEADER_NAME]
        r = client.post("/assistant/ask", json={"question": "q"})
        assert r.status_code == 403
    finally:
        client.headers[CSRF_HEADER_NAME] = saved


# --- Part E: LLM concurrency gate -------------------------------------------


def test_gate_denies_over_capacity() -> None:
    gate = _ConcurrencyGate(1)
    gate.acquire()  # takes the only slot
    with pytest.raises(LLMConcurrencyError):
        gate.acquire()
    gate.release()
    gate.acquire()  # capacity restored
    gate.release()


def test_gate_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError):
        _ConcurrencyGate(0)
    with pytest.raises(ValueError):
        _ConcurrencyGate(-1)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"response": "hi"}

    def json(self):
        return self._payload


def test_gate_released_after_success(monkeypatch) -> None:
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _FakeResponse())
    gate = llm.generation_gate()
    gate.acquire()
    gate.release()
    llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
    # If the call leaked a slot, this would raise.
    gate.acquire()
    gate.release()


def test_gate_released_after_unavailable(monkeypatch) -> None:
    def boom(*a, **k):
        raise llm.httpx.HTTPError("down")

    monkeypatch.setattr(llm.httpx, "post", boom)
    gate = llm.generation_gate()
    with pytest.raises(LLMUnavailableError):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
    gate.acquire()
    gate.release()


def test_gate_released_after_unexpected_error(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(llm.httpx, "post", boom)
    gate = llm.generation_gate()
    with pytest.raises(RuntimeError):
        llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
    gate.acquire()
    gate.release()


def test_saturated_gate_blocks_generation(monkeypatch) -> None:
    called = {"n": 0}

    def counting_post(*a, **k):
        called["n"] += 1
        return _FakeResponse()

    monkeypatch.setattr(llm.httpx, "post", counting_post)
    gate = llm.generation_gate()
    gate.acquire()  # occupy the single slot
    try:
        with pytest.raises(LLMConcurrencyError):
            llm.LocalOllamaAssistant(model="m", timeout=1).answer("q")
    finally:
        gate.release()
    assert called["n"] == 0


def test_concurrent_generation_respects_capacity() -> None:
    gate = _ConcurrencyGate(1)
    started = threading.Event()
    release = threading.Event()
    in_flight = {"max": 0, "current": 0}
    lock = threading.Lock()

    def worker():
        gate.acquire()
        with lock:
            in_flight["current"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["current"])
        started.set()
        release.wait(timeout=2)
        with lock:
            in_flight["current"] -= 1
        gate.release()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    started.wait(timeout=2)
    t2.start()
    # t2 cannot get the slot while t1 holds it.
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert in_flight["max"] == 1


# --- Step 6.3D: failed-login pre-check avoids password hashing --------------


def test_blocked_login_skips_password_verification(anon_client, conn, monkeypatch) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    for _ in range(failed_login_limiter.max_events):
        assert _login(anon_client, TEST_USERNAME, "wrong-password-xxxx").status_code == 401

    calls = {"n": 0}
    real = auth.verify_password

    def counting(password, encoded):
        calls["n"] += 1
        return real(password, encoded)

    monkeypatch.setattr(auth, "verify_password", counting)
    blocked = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    # The pre-check must refuse BEFORE any PBKDF2 work.
    assert calls["n"] == 0


def test_precheck_does_not_reveal_account_existence(anon_client, conn) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    # Block both keys equally; response for a real vs fake account is identical.
    n = failed_login_limiter.max_events
    for _ in range(n):
        _login(anon_client, TEST_USERNAME, "wrong-password-xxxx")
        _login(anon_client, "ghost-user-xyz", "wrong-password-xxxx")
    real = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    fake = _login(anon_client, "ghost-user-xyz", TEST_PASSWORD)
    assert real.status_code == fake.status_code == 429
    assert real.json()["detail"] == fake.json()["detail"]


def test_successful_login_within_failed_budget_still_succeeds(anon_client, conn) -> None:
    make_user(conn)
    login_limiter.reset()
    failed_login_limiter.reset()
    # One failure, then correct credentials: must still log in and not be
    # blocked by the pre-check (budget not exhausted).
    assert _login(anon_client, TEST_USERNAME, "wrong-password-xxxx").status_code == 401
    assert _login(anon_client, TEST_USERNAME, TEST_PASSWORD).status_code == 200
    # The success must not have consumed failed-attempt budget: starting from
    # the one prior failure, exactly `n - 1` more failures are allowed before
    # the window blocks.
    n = failed_login_limiter.max_events
    for _ in range(n - 1):
        assert _login(anon_client, TEST_USERNAME, "wrong-password-xxxx").status_code == 401
    assert _login(anon_client, TEST_USERNAME, "wrong-password-xxxx").status_code == 429


def test_concurrent_failed_logins_respect_limit() -> None:
    # Route-level concurrency cannot use one shared TestClient across threads
    # (the sqlite connection is thread-bound), so the atomicity guarantee is
    # verified directly on the limiter used by the login route.
    limiter = RateLimiter(5, 900.0)
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def attempt():
        barrier.wait(timeout=2)
        r = limiter.check("login-fail:digest:testclient")
        with lock:
            results.append(r.allowed)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)
    assert results.count(True) == 5
    assert results.count(False) == 5


# --- Step 6.3D: document summarization under LLM saturation -----------------


class BlockedSummarizer:
    summary_model = "blocked-model"

    def summarize(self, title, chunks):
        raise LLMConcurrencyError("The AI service is busy; please retry shortly.")


def test_summarize_returns_429_when_generation_gate_saturated(
    client, docs_dir
) -> None:
    matter_id = _make_matter(client)
    r = client.post(
        "/documents",
        data={"matter_id": matter_id},
        files={"file": ("memo.txt", b"financial summary of harbor", "text/plain")},
    )
    assert r.status_code == 201
    document_id = r.json()["id"]

    from app.routers import documents as documents_router

    app.dependency_overrides[documents_router.get_summarizer] = lambda: BlockedSummarizer()
    try:
        response = client.post(f"/documents/{document_id}/summarize")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    # Internal details are not leaked in a traceback.
    assert response.json()["detail"] == "The AI service is busy; please retry shortly."
