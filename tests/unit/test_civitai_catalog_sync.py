"""
tests/unit/test_civitai_catalog_sync.py
=======================================
Unit-тесты синхронизации каталога Civitai с SD Forge:
- Изоляция от бага Path('.') при вызове из ModelsManagerDialog
- Динамическое обновление путей скачивания при смене каталога Forge в рантайме
- Разрешение целевых подпапок по типу модели (Checkpoint, LORA, VAE, TextualInversion)
- Нормализация и очистка HTML-разметки, сущностей и Hangul filler (\u3164)
- Форматирование блоков параметров, имени файла и описания в UI
"""

from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from PyQt6.QtWidgets import QApplication

import config
from services.civitai_service import clean_civitai_description
from ui.dialogs.civitai_catalog_dialog import CivitaiCatalogDialog, resolve_civitai_target_dir
from ui.models_manager_dialog import ModelsManagerDialog


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_clean_civitai_description_html_and_special_chars():
    """Проверяет очистку сырых HTML-тегов, сущностей и невидимых символов."""
    raw_html = (
        "<p>This is a <strong>great</strong> model for <span style=\"color:#ff0000;\">anime</span> art.</p>"
        "<p>Visit <a href=\"https://civitai.com\">our site</a> for more.</p>"
        "<ul>"
        "  <li>Recommended CFG: <strong>6.5</strong></li>"
        "  <li>Steps: <code>25</code></li>"
        "</ul>"
        "ᅠ\n"  # Hangul Filler \u3164
        "<p>Enjoy &quot;special&quot; prompts &amp; &#39;tricks&#39;&nbsp;today!</p>"
    )

    cleaned = clean_civitai_description(raw_html)

    # 1. Отсутствие сырых тегов
    assert "<p>" not in cleaned
    assert "<strong>" not in cleaned
    assert "<span" not in cleaned
    assert "<a href" not in cleaned
    assert "<ul>" not in cleaned
    assert "<li>" not in cleaned
    assert "<code>" not in cleaned

    # 2. Превращение списков в маркеры
    assert "• Recommended CFG: 6.5" in cleaned
    assert "• Steps: 25" in cleaned

    # 3. Декодирование HTML-сущностей
    assert '"special"' in cleaned
    assert "'tricks'" in cleaned
    assert "&amp;" not in cleaned
    assert "&quot;" not in cleaned
    assert "&#39;" not in cleaned

    # 4. Удаление Hangul Filler (\u3164) и zero-width символов
    assert "\u3164" not in cleaned
    assert "\u200b" not in cleaned

    # 5. Пустой ввод
    assert clean_civitai_description(None) == ""
    assert clean_civitai_description("") == ""


def test_resolve_civitai_target_dir_by_model_type(tmp_path, monkeypatch):
    """Проверяет корректный выбор подпапки для разных типов моделей."""
    forge_root = tmp_path / "SD_Forge"
    sd_dir = forge_root / "models" / "Stable-diffusion"
    lora_dir = forge_root / "models" / "Lora"
    vae_dir = forge_root / "models" / "VAE"
    emb_dir = forge_root / "embeddings"

    for d in (sd_dir, lora_dir, vae_dir, emb_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr(config, "get_forge_models_dir", lambda: sd_dir)
    monkeypatch.setattr(config, "DEFAULT_FORGE_LORA_DIR", lora_dir)

    # 1. Чекпоинты
    ckpt_res = resolve_civitai_target_dir("Checkpoint")
    assert ckpt_res == sd_dir.resolve()

    # 2. LoRA
    lora_res = resolve_civitai_target_dir("LORA")
    assert lora_res == lora_dir.resolve()

    # 3. VAE
    vae_res = resolve_civitai_target_dir("VAE")
    assert vae_res == vae_dir.resolve()

    # 4. TextualInversion / Embeddings
    emb_res = resolve_civitai_target_dir("TextualInversion")
    assert emb_res in (emb_dir.resolve(), (forge_root / "models" / "embeddings").resolve())


def test_runtime_forge_dir_change_updates_download_dir(tmp_path, monkeypatch):
    """Проверяет обновление директории скачивания при смене каталога Forge в рантайме."""
    forge_1 = tmp_path / "Forge_First"
    sd_1 = forge_1 / "models" / "Stable-diffusion"
    sd_1.mkdir(parents=True)

    forge_2 = tmp_path / "Forge_Second"
    sd_2 = forge_2 / "models" / "Stable-diffusion"
    sd_2.mkdir(parents=True)

    monkeypatch.setattr(config, "FORGE_ROOT_DIR", forge_1)
    monkeypatch.setattr(config, "get_forge_models_dir", lambda: sd_1)

    assert resolve_civitai_target_dir("Checkpoint") == sd_1.resolve()

    # Имитация смены каталога Forge пользователем
    monkeypatch.setattr(config, "FORGE_ROOT_DIR", forge_2)
    monkeypatch.setattr(config, "get_forge_models_dir", lambda: sd_2)

    assert resolve_civitai_target_dir("Checkpoint") == sd_2.resolve()


def test_open_civitai_catalog_no_path_dot_bug(qapp, tmp_path, monkeypatch):
    """
    Проверяет, что вызов _open_civitai_catalog() из менеджера моделей
    не передает Path('.') и не разрешает целевой каталог в корень репозитория.
    """
    forge_root = tmp_path / "LiveForge"
    sd_dir = forge_root / "models" / "Stable-diffusion"
    sd_dir.mkdir(parents=True)

    monkeypatch.setattr(config, "FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr(config, "get_forge_models_dir", lambda: sd_dir)

    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        manager = ModelsManagerDialog()

    manager.available_checkpoints = ["v1-5-pruned.safetensors"]
    manager.forge_client.get_installed_checkpoints = MagicMock(return_value=["v1-5-pruned.safetensors"])

    with patch("ui.dialogs.civitai_catalog_dialog.CivitaiCatalogDialog") as mock_dialog_cls:
        mock_dlg_inst = MagicMock()
        mock_dialog_cls.return_value = mock_dlg_inst

        manager._open_civitai_catalog()

        assert mock_dialog_cls.called
        # Проверяем, что передан target_dir=None (не Path('.'))
        called_target = mock_dialog_cls.call_args.kwargs.get("target_dir")
        assert called_target is None

    manager.reject()


def test_civitai_catalog_card_description_clean_render(qapp, tmp_path, monkeypatch):
    """Проверяет формирование блоков параметров, имени файла и чистого описания в превью."""
    forge_root = tmp_path / "CleanForge"
    sd_dir = forge_root / "models" / "Stable-diffusion"
    sd_dir.mkdir(parents=True)

    monkeypatch.setattr(config, "FORGE_ROOT_DIR", forge_root)
    monkeypatch.setattr(config, "get_forge_models_dir", lambda: sd_dir)

    with patch.object(CivitaiCatalogDialog, "_on_search_clicked"):
        dialog = CivitaiCatalogDialog(target_dir=sd_dir)

    dialog.cards_data = [
        {
            "id": "12345",
            "name": "CyberRealistic Pro",
            "type": "Checkpoint",
            "version_name": "v3.0",
            "size_mb": 2048.0,
            "base_model": "SDXL",
            "trained_words": ["cyberpunk", "neon"],
            "filename": "cyberrealistic_v3.safetensors",
            "download_url": "https://civitai.com/api/download/models/12345",
            "meta": {
                "sampler": "Euler a",
                "steps": 30,
                "cfgScale": 7.0
            },
            "description": "<p>CyberRealistic <strong>v3</strong></p><p>Use <a href='#'>prompt</a></p>ᅠ"
        }
    ]

    dialog.list_results.addItem("CyberRealistic Pro")
    dialog.list_results.setCurrentRow(0)

    # Вызываем выбор карточки
    dialog._on_card_selected(dialog.list_results.item(0))

    preview_text = dialog.text_preview.toPlainText()

    # 1. Наличие обязательных блоков
    assert "⚙️ Рекомендованные автором параметры:" in preview_text
    assert "• Сэмплер: Euler a" in preview_text
    assert "• Шаги: 30" in preview_text
    assert "• CFG Scale: 7.0" in preview_text
    assert "📄 Имя файла: cyberrealistic_v3.safetensors" in preview_text
    assert "📖 Описание:" in preview_text

    # 2. Очистка от HTML и Hangul filler
    assert "<p>" not in preview_text
    assert "<strong>" not in preview_text
    assert "<a href" not in preview_text
    assert "\u3164" not in preview_text
    assert "CyberRealistic v3" in preview_text

    dialog.reject()
