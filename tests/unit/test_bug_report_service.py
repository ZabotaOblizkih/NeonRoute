"""
tests/unit/test_bug_report_service.py
======================================
Изолированные модульные тесты для:
- маскирования путей пользователя, переменных окружения и токенов в sanitize_text();
- отказоустойчивости collect_environment_summary() при сбоях мониторов;
- сборки BugReportPayload с ограничением длины и хвостом логов;
- отправки send_bug_report() (201, 400/500, Timeout, ConnectionError) через моки requests.post;
- наличия и корректной структуры cloudflare_worker/src/index.js, wrangler.toml и README.md.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import config
from services.bug_report_service import (
    BugReportPayload,
    BugReportResult,
    DEFAULT_BUG_REPORT_ENDPOINT,
    MAX_TITLE_CHARS,
    TRUNCATION_MARKER,
    build_bug_report_payload,
    collect_environment_summary,
    get_bug_report_endpoint,
    sanitize_text,
    send_bug_report,
)


def test_sanitize_text_masks_windows_paths_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\AliceSmith")
    monkeypatch.setenv("USERNAME", "AliceSmith")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path / "NeonRouteApp")
    monkeypatch.setattr(config, "FORGE_ROOT_DIR", tmp_path / "ForgeDir")

    forge_backslash = str(config.FORGE_ROOT_DIR).replace("/", "\\")
    raw = (
        r"Error in C:\Users\AliceSmith\Documents\secret.py and "
        r"c:/users/bob_dev/AppData/Local/file.txt; "
        f"base={config.BASE_DIR}/main.py; "
        f"forge={forge_backslash}\\webui.bat; "
        "user=AliceSmith"
    )
    cleaned = sanitize_text(raw)

    assert "AliceSmith" not in cleaned
    assert "bob_dev" not in cleaned
    assert str(config.BASE_DIR) not in cleaned
    assert str(config.FORGE_ROOT_DIR) not in cleaned
    assert "<USER>" in cleaned
    assert "<USER_HOME>" in cleaned or "<USER>" in cleaned
    assert "<APP_DIR>" in cleaned
    assert "<FORGE_DIR>" in cleaned


def test_sanitize_text_masks_tokens_and_url_credentials():
    raw = (
        "ghp_1234567890abcdef123456 "
        "github_pat_11ABCDEF0123456789xyz "
        "hf_AbCdEfGhIjKlMnOpQrSt "
        "sk-proj1234567890abcdef "
        "https://myuser:supersecret@example.com/repo?token=abc123&api_key=xyz789 "
        "Authorization: Bearer my_bearer_secret_value "
        "GITHUB_TOKEN=secret_val_999"
    )
    cleaned = sanitize_text(raw)

    for leaked in (
        "ghp_1234567890abcdef123456",
        "github_pat_11ABCDEF0123456789xyz",
        "hf_AbCdEfGhIjKlMnOpQrSt",
        "sk-proj1234567890abcdef",
        "supersecret",
        "abc123",
        "xyz789",
        "my_bearer_secret_value",
        "secret_val_999",
    ):
        assert leaked not in cleaned

    assert "<REDACTED_TOKEN>" in cleaned
    assert "<REDACTED>" in cleaned


def test_collect_environment_summary_resilient_on_failures(monkeypatch):
    monkeypatch.delattr(config, "APP_VERSION", raising=False)

    with (
        patch("platform.system", side_effect=RuntimeError("os fail")),
        patch("platform.python_version", side_effect=RuntimeError("py fail")),
        patch("psutil.virtual_memory", side_effect=RuntimeError("ram fail")),
        patch("pynvml.nvmlInit", side_effect=RuntimeError("nvml fail")),
        patch("utils.system_monitor._detect_windows_gpu_via_registry", side_effect=RuntimeError("gpu fail")),
        patch("services.environment_doctor.EnvironmentDoctor.check_missing", side_effect=RuntimeError("doc fail")),
    ):
        summary = collect_environment_summary()

    assert isinstance(summary, dict)
    assert summary["app_version"] == "N/A"
    assert summary["os"] == "N/A"
    assert summary["python"] == "N/A"
    assert summary["gpu"] == "N/A"
    assert summary["vram_total_mb"] == "N/A"
    assert summary["ram_total_gb"] == "N/A"
    assert summary["missing_dependencies"] == "N/A"


def test_build_bug_report_payload_truncates_and_includes_logs(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "neonroute.log").write_text(
        "[2026-11-26 01:00:00] [INFO] [App]: Starting\n"
        r"[2026-11-26 01:00:01] [ERROR] [App]: Failed at C:\Users\TestUser\app.py" + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "LOGS_DIR", logs_dir)

    long_summary = "Crash " + ("X" * 500)
    long_tb = "Traceback:\n" + ("line\n" * 600)
    payload = build_bug_report_payload(
        error_summary=long_summary,
        traceback_text=long_tb,
        log_tail_lines=10,
        max_body_chars=900,
        source="unit_test",
    )

    assert isinstance(payload, BugReportPayload)
    assert len(payload.title) <= MAX_TITLE_CHARS
    assert len(payload.body) <= 900
    assert payload.body.endswith(TRUNCATION_MARKER)
    assert "TestUser" not in payload.body


def test_send_bug_report_success_201():
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "ok": True,
        "issue_number": 42,
        "issue_url": "https://github.com/ZabotaOblizkih/NeonRoute/issues/42",
    }

    with patch("services.bug_report_service.requests.post", return_value=mock_resp) as mock_post:
        res = send_bug_report(
            endpoint_url="https://worker.example.workers.dev",
            title="[Bug] Test",
            body="Details",
            app_version="0.9.8-beta",
            category="manual",
        )

    assert isinstance(res, BugReportResult)
    assert res.ok is True
    assert res.status_code == 201
    assert res.issue_number == 42
    assert res.issue_url == "https://github.com/ZabotaOblizkih/NeonRoute/issues/42"
    mock_post.assert_called_once()


@pytest.mark.parametrize("status_code", [400, 500])
def test_send_bug_report_http_errors(status_code):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"ok": False, "error": "Bad payload"}

    with patch("services.bug_report_service.requests.post", return_value=mock_resp):
        res = send_bug_report(
            endpoint_url="https://worker.example.workers.dev",
            title="[Bug] Test",
            body="Details",
        )

    assert res.ok is False
    assert res.status_code == status_code
    assert f"HTTP {status_code}" in res.message


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.Timeout("timed out"),
        requests.exceptions.ConnectionError("dns failed"),
    ],
)
def test_send_bug_report_network_exceptions(exc):
    with patch("services.bug_report_service.requests.post", side_effect=exc):
        res = send_bug_report(
            endpoint_url="https://worker.example.workers.dev",
            title="[Bug] Test",
            body="Details",
        )

    assert res.ok is False
    assert res.status_code is None
    assert res.message


def test_send_bug_report_rejects_empty_endpoint():
    res = send_bug_report(endpoint_url="", title="Title", body="Body")
    assert res.ok is False
    assert res.status_code is None


def test_get_bug_report_endpoint_default_and_env_override(monkeypatch):
    monkeypatch.delenv("NEONROUTE_BUG_REPORT_URL", raising=False)
    with patch("utils.config_manager.ConfigManager.load_config", return_value={}):
        assert get_bug_report_endpoint() == DEFAULT_BUG_REPORT_ENDPOINT
        assert DEFAULT_BUG_REPORT_ENDPOINT == "https://neonroute-bug-reporter.neonroute-logs-2026.workers.dev"

    monkeypatch.setenv("NEONROUTE_BUG_REPORT_URL", "https://custom.workers.dev")
    assert get_bug_report_endpoint() == "https://custom.workers.dev"


def test_cloudflare_worker_files_structure():
    root = Path(__file__).resolve().parents[2]
    worker_dir = root / "cloudflare_worker"
    index_js = worker_dir / "src" / "index.js"
    wrangler_toml = worker_dir / "wrangler.toml"
    readme_md = worker_dir / "README.md"

    assert index_js.is_file()
    assert wrangler_toml.is_file()
    assert readme_md.is_file()

    js_text = index_js.read_text(encoding="utf-8")
    assert "api.github.com/repos/" in js_text
    assert "GITHUB_TOKEN" in js_text
    assert "GITHUB_OWNER" in js_text
    assert "GITHUB_REPO" in js_text
    assert "X-GitHub-Api-Version" in js_text
    assert "NeonRoute-BugReporter" in js_text
    assert "OPTIONS" in js_text
    assert "405" in js_text
    assert "200" in js_text and "16000" in js_text

    toml_text = wrangler_toml.read_text(encoding="utf-8")
    assert "GITHUB_OWNER" in toml_text
    assert "GITHUB_REPO" in toml_text

    readme_text = readme_md.read_text(encoding="utf-8")
    assert "wrangler deploy" in readme_text
    assert "wrangler secret put GITHUB_TOKEN" in readme_text
