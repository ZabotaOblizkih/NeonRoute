"""
tests/unit/test_integrity_and_updates.py
========================================
Изолированные тесты для:
- генерации release_manifest.json без самохэширования и с детерминированным aggregate_hash;
- единого периметра целостности и исключений (config.json, server_config.ini, logs/, кэши);
- чтения hotfix.md без перезаписи;
- 3-way анализа обновлений (L==B, конфликт L!=B, B is None);
- выборочного применения обновлений из ZIP со снятием префикса NeonRoute_v{version}/,
  защитой от Zip Slip, созданием .bak и обработкой PermissionError / warn-only.
"""

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.build_release import (
    assemble_release,
    prompt_interactive_versioning,
    read_hotfix_section,
)
from services.integrity_service import (
    FileUpdateStatus,
    MANIFEST_FILENAME,
    analyze_three_way_updates,
    apply_selective_update_from_zip,
    build_release_manifest,
    compute_aggregate_hash,
    compute_file_sha256,
    is_path_in_integrity_perimeter,
    verify_local_integrity,
)


def test_aggregate_hash_deterministic_and_excludes_manifest():
    files_a = {
        "ui\\main_window.py": {"sha256": "aaaa", "size": 10},
        "main.py": {"sha256": "bbbb", "size": 20},
        MANIFEST_FILENAME: {"sha256": "cccc", "size": 99},
    }
    files_b = {
        "main.py": {"sha256": "bbbb", "size": 20},
        "ui/main_window.py": {"sha256": "aaaa", "size": 10},
    }
    assert compute_aggregate_hash(files_a) == compute_aggregate_hash(files_b)


def test_perimeter_exclusions():
    assert is_path_in_integrity_perimeter("main.py") is True
    assert is_path_in_integrity_perimeter("services/integrity_service.py") is True
    assert is_path_in_integrity_perimeter("data/model_presets_catalog.json") is True

    # Исключения
    assert is_path_in_integrity_perimeter("config.json") is False
    assert is_path_in_integrity_perimeter("server_config.ini") is False
    assert is_path_in_integrity_perimeter("release_manifest.json") is False
    assert is_path_in_integrity_perimeter("logs/neonroute.log") is False
    assert is_path_in_integrity_perimeter("data/memory.db") is False
    assert is_path_in_integrity_perimeter("data/cache/thumb.png") is False
    assert is_path_in_integrity_perimeter("data/user_personas.json") is False
    assert is_path_in_integrity_perimeter("core/__pycache__/x.pyc") is False
    assert is_path_in_integrity_perimeter("hotfix_v098/apply.bat") is False
    assert is_path_in_integrity_perimeter("../secret.txt") is False


def test_build_release_manifest_no_self_hash(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "a.py").write_text("x = 1\n", encoding="utf-8")
    # Пользовательские / исключённые файлы не должны попадать в манифест
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "server_config.ini").write_text("[PATHS]\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.log").write_text("log", encoding="utf-8")
    (tmp_path / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    manifest = build_release_manifest(tmp_path, app_version="0.9.8.9-beta")
    assert manifest["app_version"] == "0.9.8.9-beta"
    assert manifest["tag"] == "v0.9.8.9-beta"
    assert MANIFEST_FILENAME not in manifest["files"]
    assert "config.json" not in manifest["files"]
    assert "server_config.ini" not in manifest["files"]
    assert "logs/app.log" not in manifest["files"]
    assert "main.py" in manifest["files"]
    assert "services/a.py" in manifest["files"]


def test_hotfix_md_not_modified_and_section_read_only(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text('APP_VERSION = "0.9.8.8-beta"\n', encoding="utf-8")

    hotfix = tmp_path / "hotfix.md"
    original_hotfix = (
        "# История изменений\n\n"
        "## v0.9.8.9-beta (2026-11-25)\n\n- Добавлен контроль целостности\n- Исправлен баг\n\n"
        "## v0.9.8.8-beta (2026-11-20)\n\n- Старый релиз\n"
    )
    hotfix.write_text(original_hotfix, encoding="utf-8")

    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=["0.9.8.9-beta", "- test", ""]):
        new_ver, _ = prompt_interactive_versioning(tmp_path, non_interactive=False)

    assert new_ver == "0.9.8.9-beta"
    assert hotfix.read_text(encoding="utf-8") == original_hotfix

    section = read_hotfix_section(hotfix, "0.9.8.9-beta")
    assert "Добавлен контроль целостности" in section
    assert "Старый релиз" not in section
    assert read_hotfix_section(hotfix, "9.9.9") == ""
    assert read_hotfix_section(tmp_path / "missing.md", "0.9.8.9-beta") == ""


def test_warn_only_missing_manifest_and_mismatch(tmp_path, caplog):
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")

    # 1. Отсутствующий манифест -> не падает
    report_missing = verify_local_integrity(tmp_path, log_warnings=True)
    assert report_missing.manifest_present is False
    assert report_missing.mismatches == []

    # 2. Несовпадение хэша -> WARNING в лог, но без исключений
    manifest = build_release_manifest(tmp_path, app_version="0.9.8.9-beta")
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "main.py").write_text("print(2)\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        report_bad = verify_local_integrity(tmp_path, log_warnings=True)

    assert report_bad.manifest_present is True
    assert len(report_bad.mismatches) == 1
    assert report_bad.mismatches[0][0] == "main.py"
    assert "[HASH MISMATCH] main.py:" in caplog.text


def test_three_way_analysis_and_selective_apply(tmp_path):
    # Подготовка локальной базы (версия 0.9.8.8-beta)
    (tmp_path / "main.py").write_text("v1\n", encoding="utf-8")
    (tmp_path / "config.py").write_text('APP_VERSION = "0.9.8.8-beta"\n', encoding="utf-8")
    local_manifest = build_release_manifest(tmp_path, app_version="0.9.8.8-beta")
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(local_manifest), encoding="utf-8")

    # Пользователь локально изменил config.py
    (tmp_path / "config.py").write_text('APP_VERSION = "0.9.8.8-beta"\nCUSTOM = 1\n', encoding="utf-8")

    # Удалённый релиз 0.9.8.9-beta изменил оба файла + добавил utils/new_mod.py
    remote_root = tmp_path / "_remote_src"
    remote_root.mkdir()
    (remote_root / "main.py").write_text("v2\n", encoding="utf-8")
    (remote_root / "config.py").write_text('APP_VERSION = "0.9.8.9-beta"\n', encoding="utf-8")
    (remote_root / "utils").mkdir()
    (remote_root / "utils" / "new_mod.py").write_text("new = True\n", encoding="utf-8")
    remote_manifest = build_release_manifest(remote_root, app_version="0.9.8.9-beta")

    report = analyze_three_way_updates(tmp_path, remote_manifest, local_manifest=local_manifest)
    by_path = {e.rel_path: e for e in report.entries}

    assert by_path["main.py"].status == FileUpdateStatus.UPDATE_SAFE
    assert by_path["main.py"].default_checked is True

    assert by_path["utils/new_mod.py"].status == FileUpdateStatus.UPDATE_SAFE
    assert by_path["utils/new_mod.py"].default_checked is True

    assert by_path["config.py"].status == FileUpdateStatus.CONFLICT_LOCAL
    assert by_path["config.py"].default_checked is False

    # Сценарий B is None
    report_no_base = analyze_three_way_updates(tmp_path, remote_manifest, local_manifest=None)
    # force B=None by passing a dir without manifest
    no_base_dir = tmp_path / "_no_base"
    no_base_dir.mkdir()
    (no_base_dir / "main.py").write_text("custom\n", encoding="utf-8")
    r_nb = analyze_three_way_updates(no_base_dir, remote_manifest)
    nb_map = {e.rel_path: e for e in r_nb.entries}
    assert nb_map["main.py"].status == FileUpdateStatus.NO_BASE_DIFF
    assert nb_map["main.py"].default_checked is False

    # Собираем ZIP с префиксом NeonRoute_v0.9.8.9-beta/ и Zip Slip записью
    zip_path = tmp_path / "NeonRoute_v0.9.8.9-beta.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("NeonRoute_v0.9.8.9-beta/main.py", "v2\n")
        zf.writestr("NeonRoute_v0.9.8.9-beta/config.py", 'APP_VERSION = "0.9.8.9-beta"\n')
        zf.writestr("NeonRoute_v0.9.8.9-beta/utils/new_mod.py", "new = True\n")
        zf.writestr("../../evil.txt", "pwned")

    # Применяем только main.py и config.py (явно выбранный конфликт -> должен создать .bak)
    res = apply_selective_update_from_zip(
        zip_path=zip_path,
        base_dir=tmp_path,
        selected_files=["main.py", "config.py"],
        remote_manifest=remote_manifest,
        entry_map=by_path,
    )
    assert sorted(res.applied_files) == ["config.py", "main.py"]
    assert len(res.backup_files) == 1
    assert not (tmp_path.parent / "evil.txt").exists()
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "v2\n"
    assert res.manifest_updated is True


def test_selective_apply_handles_permission_error(tmp_path):
    (tmp_path / "main.py").write_text("v1\n", encoding="utf-8")
    (tmp_path / "config.py").write_text("c1\n", encoding="utf-8")

    remote_root = tmp_path / "_r"
    remote_root.mkdir()
    (remote_root / "main.py").write_text("v2\n", encoding="utf-8")
    (remote_root / "config.py").write_text("c2\n", encoding="utf-8")
    remote_manifest = build_release_manifest(remote_root, app_version="0.9.8.9-beta")

    zip_path = tmp_path / "upd.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("NeonRoute_v0.9.8.9-beta/main.py", "v2\n")
        zf.writestr("NeonRoute_v0.9.8.9-beta/config.py", "c2\n")

    orig_replace = __import__("os").replace

    def fake_replace(src, dst):
        if Path(dst).name == "main.py":
            raise PermissionError("WinError 32 mock")
        return orig_replace(src, dst)

    with patch("services.integrity_service.os.replace", side_effect=fake_replace):
        res = apply_selective_update_from_zip(
            zip_path=zip_path,
            base_dir=tmp_path,
            selected_files=["main.py", "config.py"],
            remote_manifest=remote_manifest,
        )

    assert res.locked_files == ["main.py"]
    assert res.applied_files == ["config.py"]
    assert (tmp_path / "config.py").read_text(encoding="utf-8") == "c2\n"
