"""PS_ALLOWED_ORIGINS parsing and wiring (deployment hardening).

The allowlist feeds both credentialed CORS and the CSRF origin check, so it
must stay exact, validated and shared. Malformed configuration fails closed.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.main import (
    ALLOWED_ORIGINS,
    DEFAULT_ALLOWED_ORIGINS,
    _normalise_origin,
    configured_origins,
    parse_allowed_origins,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_are_localhost_when_unset() -> None:
    assert parse_allowed_origins(None) == DEFAULT_ALLOWED_ORIGINS
    assert parse_allowed_origins("   ") == DEFAULT_ALLOWED_ORIGINS
    assert ALLOWED_ORIGINS == DEFAULT_ALLOWED_ORIGINS


def test_trims_whitespace_trailing_slash_and_normalises_case() -> None:
    assert parse_allowed_origins(
        " https://Frontend.Example.test/ , http://127.0.0.1:5173/ "
    ) == ["https://frontend.example.test", "http://127.0.0.1:5173"]


def test_removes_default_ports_so_browser_origins_match() -> None:
    assert parse_allowed_origins("https://app.test:443") == ["https://app.test"]
    assert parse_allowed_origins("http://app.test:80") == ["http://app.test"]
    assert parse_allowed_origins("https://app.test:8443") == [
        "https://app.test:8443"
    ]


def test_deduplicates_preserving_order() -> None:
    assert parse_allowed_origins(
        "https://a.test, https://b.test, https://a.test/"
    ) == ["https://a.test", "https://b.test"]


def test_supports_ipv6_origin() -> None:
    assert _normalise_origin("http://[::1]:5173") == "http://[::1]:5173"


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "https://*.example.test",
        "example.test",
        "//example.test",
        "ftp://example.test",
        "https://example.test/path",
        "https://example.test?x=1",
        "https://example.test#frag",
        "https://user:pass@example.test",
        "https://example.test:not-a-port",
        "https://example.test:",
        "https://exa mple.test",
        "null",
        "https://",
        ",,,",
    ],
)
def test_rejects_malformed_or_wildcard_origins(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_allowed_origins(raw)


def test_configured_origins_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "PS_ALLOWED_ORIGINS", "https://frontend.test, https://admin.test/"
    )
    assert configured_origins() == ["https://frontend.test", "https://admin.test"]


def test_csrf_origin_check_uses_the_configured_allowlist(monkeypatch) -> None:
    from app import main, security

    monkeypatch.setattr(main, "ALLOWED_ORIGINS", ["https://frontend.test"])
    assert security._origin_is_allowed("https://frontend.test")
    assert not security._origin_is_allowed("https://evil.test")


def test_import_time_config_drives_cors_and_credentials() -> None:
    """A fresh process with PS_ALLOWED_ORIGINS set uses it for CORS."""
    origin = "https://frontend.test"
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import ALLOWED_ORIGINS, app\n"
        "client = TestClient(app)\n"
        "r = client.options('/clients', headers={\n"
        "    'Origin': %r,\n"
        "    'Access-Control-Request-Method': 'POST',\n"
        "})\n"
        "print(json.dumps({\n"
        "    'origins': ALLOWED_ORIGINS,\n"
        "    'origin': r.headers.get('access-control-allow-origin'),\n"
        "    'credentials': r.headers.get('access-control-allow-credentials'),\n"
        "}))\n" % origin
    )
    env = dict(os.environ)
    env["PS_ALLOWED_ORIGINS"] = f"{origin}/"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["origins"] == [origin]
    assert payload["origin"] == origin
    assert payload["credentials"] == "true"


def test_wildcard_import_fails_closed() -> None:
    env = dict(os.environ)
    env["PS_ALLOWED_ORIGINS"] = "*"
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "PS_ALLOWED_ORIGINS" in result.stderr
