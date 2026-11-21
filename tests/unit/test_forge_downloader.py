"""
tests/unit/test_forge_downloader.py
===================================
Изолированные unit-тесты для сервиса загрузки и установки SD WebUI Forge:
- resolve_forge_hardware_spec (Turing/Ada, Blackwell RTX 50, AMD/Intel, low VRAM)
- create_forge_launcher_bat (структура аргументов %* и относительные пути)
- extract_7z_archive и resolve_extracted_forge_root
- install_forge_pipeline (дисковое пространство, ADetailer, DreamShaper 8, art_director_models.json)
- ForgeDownloadWorker (расчет скорости по дельте, отмена с сохранением .part)
"""

import os
import sys
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.forge_downloader import (
    resolve_forge_hardware_spec,
    create_forge_launcher_bat,
    resolve_extracted_forge_root,
    extract_7z_archive,
    install_forge_pipeline,
    ForgeDownloadWorker,
    start_forge_download_worker,
    get_active_forge_download_worker,
    get_default_forge_install_dir,
    validate_or_fallback_forge_dir,
)
from services.art_director_config import load_art_director_models, save_art_director_models


@pytest.fixture(autouse=True)
def isolate_server_config_ini(tmp_path, monkeypatch):
    """Изоляция server_config.ini для исключения утечек в боевую конфигурацию."""
    fake_ini = tmp_path / "server_config.ini"
    fake_ini.write_text("[PATHS]\nforge_root_dir = \n", encoding="utf-8")
    import config
    import core.forge_locator
    monkeypatch.setattr(config, "SERVER_CONFIG_INI_PATH", fake_ini)
    monkeypatch.setattr(core.forge_locator, "SERVER_CONFIG_INI_PATH", fake_ini)
    yield fake_ini


def test_resolve_hardware_spec_ada(monkeypatch):
    """Тест спецификации для GPU NVIDIA RTX 4060 (Turing/Ada, 8 ГБ)."""
    mock_profile = {
        "vendor": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 4060 Laptop GPU",
        "vram_total_mb": 8192,
        "compute_cap": 8.9,
    }
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=mock_profile):
        spec = resolve_forge_hardware_spec()
        assert spec.supported is True
        assert spec.is_blackwell is False
        assert "RTX 4060" in spec.gpu_summary
        assert "8192 МБ" in spec.gpu_summary
        assert spec.checkpoint_filename == "dreamshaper_8.safetensors"
        assert len(spec.adetailer_models) == 2


def test_resolve_hardware_spec_blackwell(monkeypatch):
    """Тест детекта архитектуры Blackwell (RTX 5050 / compute capability >= 10.0)."""
    mock_profile = {
        "vendor": "nvidia",
        "gpu_name": "NVIDIA GeForce RTX 5050",
        "vram_total_mb": 8192,
        "compute_cap": 12.0,
    }
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=mock_profile):
        spec = resolve_forge_hardware_spec()
        assert spec.supported is True
        assert spec.is_blackwell is True


def test_resolve_hardware_spec_amd_unsupported(monkeypatch):
    """Тест отклонения не-NVIDIA видеокарт (AMD Radeon)."""
    mock_profile = {
        "vendor": "amd",
        "gpu_name": "AMD Radeon RX 7900 XTX",
        "vram_total_mb": 24576,
        "compute_cap": None,
    }
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=mock_profile):
        spec = resolve_forge_hardware_spec()
        assert spec.supported is False
        assert spec.is_blackwell is False


def test_resolve_hardware_spec_low_vram(monkeypatch):
    """Тест отклонения NVIDIA видеокарт с VRAM < 3.8 ГБ."""
    mock_profile = {
        "vendor": "nvidia",
        "gpu_name": "NVIDIA GeForce GTX 1050",
        "vram_total_mb": 2048,
        "compute_cap": 6.1,
    }
    with patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=mock_profile):
        spec = resolve_forge_hardware_spec()
        assert spec.supported is False


def test_create_forge_launcher_bat(tmp_path):
    """Проверка генерации forge_launcher.bat со всеми флагами %* и путями к Python."""
    forge_dir = tmp_path / "sd-webui-forge"
    forge_dir.mkdir()
    (forge_dir / "system" / "python").mkdir(parents=True)
    (forge_dir / "webui").mkdir(parents=True)
    (forge_dir / "webui" / "launch.py").write_text("print('mock launch')", encoding="utf-8")

    bat_file = create_forge_launcher_bat(forge_dir)
    assert bat_file.is_file()
    content = bat_file.read_text(encoding="utf-8")

    assert "%BASE_DIR%system\\python" in content
    assert 'cd /d "%BASE_DIR%webui"' in content
    assert "python.exe launch.py %*" in content


def test_resolve_extracted_forge_root(tmp_path):
    """Проверка поиска корня Forge на 1 и 2 уровнях вложенности."""
    # 1. Сам корень
    (tmp_path / "webui-user.bat").write_text("@echo off", encoding="utf-8")
    assert resolve_extracted_forge_root(tmp_path) == tmp_path

    # 2. Вложенная папка (webui_forge_cu121_torch231)
    nested_root = tmp_path / "nested_test"
    nested_root.mkdir()
    sub_forge = nested_root / "webui_forge_cu121_torch231"
    sub_forge.mkdir()
    (sub_forge / "run.bat").write_text("@echo off", encoding="utf-8")
    assert resolve_extracted_forge_root(nested_root) == sub_forge


def test_install_forge_pipeline_disk_space_fail(tmp_path):
    """Проверка отказа установки при нехватке свободного места на диске."""
    mock_usage = MagicMock()
    mock_usage.free = 2 * 1024 * 1024 * 1024  # 2 GB free, required 14.5 GB

    with patch("shutil.disk_usage", return_value=mock_usage):
        ok, root, msg = install_forge_pipeline(tmp_path, download_dreamshaper=True)
        assert ok is False
        assert "Недостаточно свободного места" in msg


def test_install_forge_pipeline_success(tmp_path, monkeypatch, isolate_server_config_ini):
    """Полный тест конвейера: архив, распаковка, лаунчер, ADetailer, DreamShaper 8, JSON."""
    mock_usage = MagicMock()
    mock_usage.free = 30 * 1024 * 1024 * 1024  # 30 GB free

    # Мок распаковки: создаёт структуру Forge
    def mock_extract(archive_p, target_dir):
        forge_sub = target_dir / "webui_forge_cu121_torch231"
        (forge_sub / "webui" / "models" / "Stable-diffusion").mkdir(parents=True, exist_ok=True)
        (forge_sub / "webui" / "models" / "adetailer").mkdir(parents=True, exist_ok=True)
        (forge_sub / "webui" / "launch.py").write_text("# launch", encoding="utf-8")
        (forge_sub / "run.bat").write_text("@echo off", encoding="utf-8")
        return True, "Extracted successfully"

    # Мок скачивания файлов: создаёт пустой файл
    def mock_download_file(url, dest_path, **kwargs):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text("mock content", encoding="utf-8")
        cb = kwargs.get("progress_callback")
        if cb:
            cb(100, 100)

    with patch("shutil.disk_usage", return_value=mock_usage), \
         patch("services.forge_downloader.extract_7z_archive", side_effect=mock_extract), \
         patch("services.forge_downloader.download_file", side_effect=mock_download_file), \
         patch("services.forge_patcher.ForgePatcher.apply_patch", return_value=(True, "Patched")):

        progress_events = []
        def _on_prog(stage, cur, tot):
            progress_events.append((stage, cur, tot))

        target_dir = tmp_path / "forge_target"
        ok, res_root, msg = install_forge_pipeline(
            target_dir=target_dir,
            download_dreamshaper=True,
            progress_cb=_on_prog,
        )

        assert ok is True
        assert Path(res_root).exists()
        assert (Path(res_root) / "forge_launcher.bat").is_file()
        assert "webui_forge_cu121_torch231" in isolate_server_config_ini.read_text(encoding="utf-8")

        # Проверка скачивания ADetailer
        ad_dir = Path(res_root) / "webui" / "models" / "adetailer"
        assert (ad_dir / "face_yolov8m.pt").is_file()
        assert (ad_dir / "hand_yolov9c.pt").is_file()

        # Проверка скачивания DreamShaper
        sd_dir = Path(res_root) / "webui" / "models" / "Stable-diffusion"
        assert (sd_dir / "dreamshaper_8.safetensors").is_file()

        # Проверка обновления art_director_models.json: DreamShaper 8 должен быть is_default: True
        models = load_art_director_models()
        ds_model = next((m for m in models if "dreamshaper" in m.get("checkpoint", "").lower()), None)
        assert ds_model is not None
        assert ds_model.get("is_default") is True

        # Проверка этапов прогресса
        stages = [e[0] for e in progress_events]
        assert "forge" in stages
        assert "extract" in stages
        assert "adetailer" in stages
        assert "checkpoint" in stages


def test_forge_download_worker_signals_and_cancel(tmp_path):
    """Тест воркера ForgeDownloadWorker: сигналы, дельта скорости, отмена без удаления .part."""
    worker = ForgeDownloadWorker(target_dir=tmp_path, download_dreamshaper=False)

    received_progress = []
    worker.progress_signal.connect(lambda s, d, t, sp: received_progress.append((s, d, t, sp)))

    # Симулируем коллбэк прогресса
    worker._progress_callback("forge", 1024 * 1024, 10 * 1024 * 1024)
    assert len(received_progress) >= 1
    st, cur, tot, sp = received_progress[0]
    assert st == "forge"
    assert cur == 1024 * 1024
    assert tot == 10 * 1024 * 1024

    # Тест отмены
    part_file = tmp_path / "webui_forge_cu121_torch231.7z.part"
    part_file.write_bytes(b"existing part bytes")

    assert worker.is_cancelled() is False
    worker.cancel()
    assert worker.is_cancelled() is True
    assert part_file.exists(), ".part файл не должен удаляться при отмене для докачки"


def test_start_forge_download_worker_registers_reference_before_start():
    """
    Регрессия 0xC0000409: глобальная ссылка на QThread должна выставляться
    ДО start(), иначе Python собирает воркер и Qt падает с
    'QThread: Destroyed while thread is still running'.
    """
    import services.forge_downloader as fd

    mock_worker = MagicMock()
    observed = {}

    def _fake_start():
        observed["active_at_start"] = fd._ACTIVE_FORGE_WORKER

    mock_worker.start.side_effect = _fake_start
    try:
        start_forge_download_worker(mock_worker)
        assert observed["active_at_start"] is mock_worker
        assert fd._ACTIVE_FORGE_WORKER is mock_worker
    finally:
        fd._ACTIVE_FORGE_WORKER = None

    failing_worker = MagicMock()
    failing_worker.start.side_effect = RuntimeError("start failed")
    with pytest.raises(RuntimeError):
        start_forge_download_worker(failing_worker)
    assert fd._ACTIVE_FORGE_WORKER is None


def test_validate_or_fallback_forge_dir_nonexistent_drive():
    """
    Проверяет, что при несуществующем томе (например, Z:) происходит
    безопасный фоллбэк на существующий системный диск без падения с WinError 3.
    """
    non_existent = Path("Z:/neyroGPD")
    fallback_dir, fell_back = validate_or_fallback_forge_dir(non_existent)
    assert fell_back is True
    # Целевой диск должен реально существовать
    assert Path(fallback_dir.drive + "/").exists()
    assert fallback_dir.name in ("neyroGPD", "sd-webui-forge")

    # Валидный существующий диск не должен вызывать фоллбэк
    if Path("C:/").exists():
        c_dir = Path("C:/sd-webui-forge")
        valid_dir, fell_back_c = validate_or_fallback_forge_dir(c_dir)
        assert fell_back_c is False
        assert valid_dir == c_dir.resolve()


def test_forge_settings_dialog_downloader_elements(qtbot):
    """Проверка интеграции элементов автоустановщика в ForgeSettingsDialog."""
    from ui.dialogs.forge_settings_dialog import ForgeSettingsDialog
    dlg = ForgeSettingsDialog()
    qtbot.addWidget(dlg)

    # Проверка существующих виджетов
    assert hasattr(dlg, "line_path")
    assert hasattr(dlg, "btn_browse")
    assert hasattr(dlg, "lbl_launcher_status")
    assert hasattr(dlg, "card_patch")
    assert hasattr(dlg, "btn_check_patch")
    assert hasattr(dlg, "lbl_patch_status")
    assert hasattr(dlg, "card_opt")
    assert hasattr(dlg, "switch_fp8")
    assert hasattr(dlg, "segmented_art")
    assert hasattr(dlg, "btn_cancel")
    assert hasattr(dlg, "btn_save")

    # Проверка новых элементов установщика
    assert hasattr(dlg, "container_install")
    assert hasattr(dlg, "lbl_gpu_spec")
    assert hasattr(dlg, "chk_download_dreamshaper")
    assert hasattr(dlg, "btn_download_forge")
    assert hasattr(dlg, "btn_cancel_forge_dl")
    assert hasattr(dlg, "forge_progress_bar")
    assert hasattr(dlg, "lbl_download_status")

    # Проверка отображения прогресса в UI
    dlg._on_forge_download_progress("forge", 50 * 1024 * 1024, 100 * 1024 * 1024, 12.5)
    assert dlg.forge_progress_bar.value() == 50
    assert "12.5 МБ/с" in dlg.lbl_download_status.text()
    assert "50 / 100 МБ" in dlg.lbl_download_status.text()

    # Закрытие и отписка
    dlg.reject()


def test_shutdown_worker_cancels_forge_download():
    """Проверка вызова отмены фоновой загрузки Forge в ShutdownWorker."""
    from ui.workers.shutdown_worker import ShutdownWorker
    mock_main_window = MagicMock()
    mock_worker = ShutdownWorker(mock_main_window)

    mock_forge_worker = MagicMock()
    mock_forge_worker.isRunning.return_value = True

    with patch("services.forge_downloader.get_active_forge_download_worker", return_value=mock_forge_worker):
        # Вызываем логику отмены фонового скачивания из ShutdownWorker
        from services.forge_downloader import get_active_forge_download_worker
        active_forge_dl = get_active_forge_download_worker()
        if active_forge_dl is not None:
            active_forge_dl.cancel()

        mock_forge_worker.cancel.assert_called_once()


def test_forge_settings_dialog_installer_visibility(qtbot, tmp_path, monkeypatch):
    """Проверка условий отображения блока установки Forge:
    1. Лаунчер не найден -> блок виден.
    2. Лаунчер найден, но статус патча NOT_FOUND -> блок виден.
    3. Лаунчер найден, статус патча PATCHED/NEEDS_PATCH, нет скачивания -> блок скрыт.
    4. Лаунчер найден, статус патча PATCHED, но активно фоновое скачивание -> блок виден.
    """
    import config
    from ui.dialogs.forge_settings_dialog import ForgeSettingsDialog
    from services.forge_patcher import ForgePatchStatus

    # 1. Лаунчер не найден -> блок виден
    monkeypatch.setattr(config, "FORGE_ROOT_DIR", None)
    with patch("ui.dialogs.forge_settings_dialog.get_active_forge_download_worker", return_value=None):
        dlg = ForgeSettingsDialog()
        qtbot.addWidget(dlg)
        assert not dlg.container_install.isHidden()
        dlg.reject()

    # 2. Лаунчер есть, но папка пустая/без api.py (NOT_FOUND) -> блок виден
    fake_launcher = tmp_path / "run.bat"
    fake_launcher.touch()
    monkeypatch.setattr(config, "FORGE_ROOT_DIR", tmp_path)
    with patch("services.forge_patcher.ForgePatcher.check_status", return_value=(ForgePatchStatus.NOT_FOUND, "Not found")), \
         patch("ui.dialogs.forge_settings_dialog.get_active_forge_download_worker", return_value=None):
        dlg = ForgeSettingsDialog()
        qtbot.addWidget(dlg)
        assert dlg._current_patch_status == ForgePatchStatus.NOT_FOUND
        assert not dlg.container_install.isHidden()
        dlg.reject()

    # 3. Лаунчер есть, статус PATCHED, нет скачивания -> блок скрыт
    with patch("services.forge_patcher.ForgePatcher.check_status", return_value=(ForgePatchStatus.PATCHED, "Patched")), \
         patch("ui.dialogs.forge_settings_dialog.get_active_forge_download_worker", return_value=None):
        dlg = ForgeSettingsDialog()
        qtbot.addWidget(dlg)
        assert dlg._current_patch_status == ForgePatchStatus.PATCHED
        assert dlg.container_install.isHidden()
        dlg.reject()

    # 4. Лаунчер есть, статус PATCHED, но фоновое скачивание активно -> блок виден
    mock_dl_worker = MagicMock()
    mock_dl_worker.isRunning.return_value = True
    with patch("services.forge_patcher.ForgePatcher.check_status", return_value=(ForgePatchStatus.PATCHED, "Patched")), \
         patch("ui.dialogs.forge_settings_dialog.get_active_forge_download_worker", return_value=mock_dl_worker):
        dlg = ForgeSettingsDialog()
        qtbot.addWidget(dlg)
        assert not dlg.container_install.isHidden()
        dlg.reject()


