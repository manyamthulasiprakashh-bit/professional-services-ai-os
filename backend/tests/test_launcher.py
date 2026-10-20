"""The maintained launcher must always bind to loopback.

Asserts the value actually handed to uvicorn (not a README string), so an
accidental switch to an externally reachable bind fails the suite.
"""

import app.launcher as launcher


def test_resolve_bind_defaults_to_loopback() -> None:
    assert launcher.resolve_bind() == ("127.0.0.1", 8000)


def test_resolve_bind_never_returns_non_loopback_host() -> None:
    for host in ("0.0.0.0", "::", "192.168.1.10", None, "example.com"):
        resolved_host, _ = launcher.resolve_bind(host=host)
        assert resolved_host == "127.0.0.1"


def test_run_binds_uvicorn_to_loopback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: str, *, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(launcher.uvicorn, "run", fake_run)
    launcher.run()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert captured["app"] == "app.main:app"
