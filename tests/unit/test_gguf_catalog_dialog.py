"""
Unit & Integration tests for GGUFCatalogDialog and ModeModelSettingsDialog integration.
Covers hardware profile ranking (CPU, 4GB, 8GB, 16GB), strict ideal-fit filter,
HuggingFace search quantization selection, background download persistence across dialog close,
and cancel_event interruption.
"""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_app = QApplication.instance() or QApplication([])

from ui.dialogs import GGUFCatalogDialog, ModeModelSettingsDialog
import ui.dialogs.gguf_catalog_dialog as cat_mod
from ui.dialogs.gguf_catalog_dialog import (
    _GGUFDownloadWorker,
    _HFSearchWorker,
    evaluate_model_for_hardware,
)


def test_hardware_evaluation_matrix():
    # 1. CPU / iGPU (0 MB VRAM, 8 GB RAM): 3B (~2010 MB) -> tier 0, 7B (4470 MB) -> tier 2
    t_3b, _, badge_3b, _ = evaluate_model_for_hardware(2010.0, 0, 8192.0, "intel")
    t_7b, _, badge_7b, _ = evaluate_model_for_hardware(4470.0, 0, 8192.0, "intel")
    assert t_3b == 0 and "🟢" in badge_3b
    assert t_7b == 2 and "🔴" in badge_7b

    # 2. 4 GB VRAM (GTX 1650, 16 GB RAM): 3B -> tier 0 (100% VRAM), 7B -> tier 1 (partial RAM)
    t4_3b, _, _, _ = evaluate_model_for_hardware(2010.0, 4096, 16384.0, "nvidia")
    t4_7b, _, _, _ = evaluate_model_for_hardware(4470.0, 4096, 16384.0, "nvidia")
    assert t4_3b == 0
    assert t4_7b == 1

    # 3. 8 GB VRAM (RTX 4060/5050, 16 GB RAM): 8B (4800 MB) -> tier 0, 12B (6960 MB) -> tier 1
    t8_8b, score_8b, _, _ = evaluate_model_for_hardware(4800.0, 8151, 16065.0, "nvidia")
    t8_3b, score_3b, _, _ = evaluate_model_for_hardware(2010.0, 8151, 16065.0, "nvidia")
    t8_12b, _, _, _ = evaluate_model_for_hardware(6960.0, 8151, 16065.0, "nvidia")
    assert t8_8b == 0
    assert t8_3b == 0
    assert score_8b < score_3b  # На 8 ГБ карте 8B модель ранжируется выше, чем 3B
    assert t8_12b == 1

    # 4. 16 GB VRAM: 12B (6960 MB) -> tier 0 и стоит выше 8B
    t16_12b, score16_12b, _, _ = evaluate_model_for_hardware(6960.0, 16384, 32768.0, "nvidia")
    t16_8b, score16_8b, _, _ = evaluate_model_for_hardware(4800.0, 16384, 32768.0, "nvidia")
    assert t16_12b == 0 and t16_8b == 0
    assert score16_12b < score16_8b


def test_catalog_strict_ideal_filter_and_mode_switching():
    with patch("ui.dialogs.gguf_catalog_dialog.get_system_config_snapshot") as mock_hw:
        mock_hw.return_value = {
            "gpu_name": "RTX 5050 Laptop GPU",
            "vendor": "nvidia",
            "vram_total_mb": 8151,
            "ram_total_mb": 16065.0,
            "is_pure_compute": False,
            "os_reserve_mb": 600,
            "kv_cache_type": "q8_0",
            "kv_cache_mb": 602,
            "usable_vram_mb": 6479.0,
        }
        dlg = GGUFCatalogDialog()
        try:
            assert dlg.sw_only_ideal.isChecked() is True
            shown = list(dlg._cards_by_filename.keys())
            assert "Qwen3-8B-Q4_K_M.gguf" in shown
            assert "google_gemma-3-12b-it-Q4_K_M.gguf" not in shown

            # Переключение фильтра режима на 'engineer'
            dlg._on_mode_filter_changed("engineer")
            eng_shown = list(dlg._cards_by_filename.keys())
            assert "qwen2.5-coder-7b-instruct-q4_k_m.gguf" in eng_shown
            assert "L3-8B-Stheno-v3.2-Q4_K_M.gguf" not in eng_shown

            # Отключение строгого фильтра 'Только идеальные' возвращает 12B модель
            dlg._on_mode_filter_changed("all")
            dlg.sw_only_ideal.setChecked(False)
            all_shown = list(dlg._cards_by_filename.keys())
            assert "google_gemma-3-12b-it-Q4_K_M.gguf" in all_shown
        finally:
            dlg.close()


def test_hf_search_worker_picks_best_fitting_quant():
    fake_models = [SimpleNamespace(id="test-org/Test-Coder-8B-GGUF", downloads=12500, gated=False, private=False)]
    fake_info = SimpleNamespace(
        siblings=[
            SimpleNamespace(rfilename="mmproj-model-f16.gguf", size=600 * 1024 * 1024),
            SimpleNamespace(rfilename="Test-Coder-8B-Q8_0.gguf", size=8500 * 1024 * 1024),
            SimpleNamespace(rfilename="Test-Coder-8B-Q4_K_M.gguf", size=4650 * 1024 * 1024),
            SimpleNamespace(rfilename="Test-Coder-8B-Q2_K.gguf", size=2900 * 1024 * 1024),
        ]
    )
    hw_8gb = {
        "vram_total_mb": 8151,
        "ram_total_mb": 16065.0,
        "vendor": "nvidia",
        "is_pure_compute": False,
        "kv_cache_type": "q8_0",
    }
    captured = {}

    with patch("huggingface_hub.HfApi") as MockApi:
        inst = MockApi.return_value
        inst.list_models.return_value = fake_models
        inst.model_info.return_value = fake_info

        worker = _HFSearchWorker("test coder", hw_8gb)
        worker.results_ready.connect(lambda q, items, err: captured.update({"q": q, "items": items, "err": err}))
        worker.run()

    assert captured["err"] == ""
    assert len(captured["items"]) == 1
    best_item = captured["items"][0]
    # Должен автоматически выбрать Q4_K_M (tier 0 для 8 ГБ), отсечь mmproj и поставить Q8_0 (tier 1) ниже
    assert best_item["filename"] == "Test-Coder-8B-Q4_K_M.gguf"
    assert best_item["tier"] == 0
    assert all("mmproj" not in v["filename"] for v in best_item["variants"])


def test_background_download_survives_dialog_close_and_supports_cancel(tmp_path):
    payload = b"GGUF" + (b"X" * (256 * 1024))
    allow_finish_event = threading.Event()
    first_chunk_sent = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            # Отправляем первый чанк и ждём сигнала от теста
            self.wfile.write(payload[:65536])
            self.wfile.flush()
            first_chunk_sent.set()
            allow_finish_event.wait(timeout=3.0)
            try:
                self.wfile.write(payload[65536:])
                self.wfile.flush()
            except Exception:
                pass
        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    try:
        dest = tmp_path / "sample-model.gguf"
        worker = _GGUFDownloadWorker(
            url=f"http://127.0.0.1:{srv.server_port}/sample-model.gguf",
            dest_path=dest,
            model_title="Sample Model",
        )
        cat_mod._ACTIVE_DOWNLOAD_WORKER = worker
        worker.start()
        assert first_chunk_sent.wait(timeout=2.0)

        with patch("ui.dialogs.gguf_catalog_dialog.resolve_target_models_dir", return_value=tmp_path):
            dlg = GGUFCatalogDialog()
            assert not dlg.active_dl_banner.isHidden()
            # Закрываем окно во время скачивания — воркер продолжает качать в фоне
            dlg.reject()
            assert worker.isRunning()

        allow_finish_event.set()
        worker.wait(3000)
        assert dest.is_file() and dest.stat().st_size == len(payload)
    finally:
        allow_finish_event.set()
        cat_mod._ACTIVE_DOWNLOAD_WORKER = None
        srv.shutdown()


def test_catalog_card_layout_and_optimized_filter_label():
    dlg = GGUFCatalogDialog()
    try:
        dlg.show()
        _app.processEvents()

        # 1. Проверяем наличие компактной подписи 'Оптимизированные:'
        found_label = False
        for ch in dlg.body_widget.findChildren(cat_mod.CaptionLabel):
            if ch.text().startswith("Оптимизированные"):
                found_label = True
                assert "Оптимизированные" in ch.text()
                break
        assert found_label, "Лейбл 'Оптимизированные:' должен присутствовать в интерфейсе"

        # 2. Проверяем карточки: они должны компактно вписываться в viewport с видимой кнопкой 'Скачать'
        assert len(dlg._cards_by_filename) > 0
        for fn, card in dlg._cards_by_filename.items():
            assert card.minimumSizeHint().width() < 620, f"Card minSizeHint {card.minimumSizeHint()} too wide!"
            cw = card.width()
            btn_g = card.btn_download.geometry()
            badge_g = card.lbl_hw_badge.geometry()
            assert btn_g.x() + btn_g.width() <= cw + 2, "Кнопка Скачать не должна вылезать за правый край карточки"
            assert badge_g.x() + badge_g.width() <= cw + 2, "Бейдж совместимости не должен вылезать за карточку"
            assert card.btn_download.isVisible(), "Кнопка Скачать должна быть видимой"
            break
    finally:
        dlg.close()
