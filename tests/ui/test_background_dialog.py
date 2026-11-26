"""
Unit tests for BackgroundSettingsDialog in NeonRoute.
Verifies geometry preservation, adaptive palette integration, safe fallback,
slider value/signal conversion, and button interactions.
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import QApplication, QMainWindow
from qframelesswindow import FramelessDialog

from ui.dialogs.background_dialog import BackgroundSettingsDialog
from ui.common.palette_utils import extract_adaptive_palette


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def dummy_window(qapp):
    win = QMainWindow()
    yield win
    win.deleteLater()


@pytest.fixture
def make_dialog(qapp, dummy_window):
    created = []

    def _factory(*args, **kwargs):
        if "parent" not in kwargs and len(args) < 3:
            kwargs["parent"] = dummy_window
        dlg = BackgroundSettingsDialog(*args, **kwargs)
        created.append(dlg)
        return dlg

    yield _factory

    for dlg in created:
        try:
            dlg.close()
            dlg.deleteLater()
        except Exception:
            pass


def test_dialog_initialization_and_geometry(qapp, make_dialog):
    dialog = make_dialog(current_path=None, current_dim_alpha=140)
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert dialog.objectName() == "BackgroundSettingsDialog"
    assert dialog.width() == 380
    assert dialog.height() == 292
    assert dialog.maximumSize().width() == 380
    assert dialog.maximumSize().height() == 292

    # Проверка кастомной шапки FluentTitleBar
    assert hasattr(dialog, "titleBar") and dialog.titleBar is not None
    assert dialog.titleBar.height() == 32
    assert dialog.titleBar.closeBtn.isVisible() is True
    assert dialog.titleBar.minBtn.isVisible() is False
    assert dialog.titleBar.maxBtn.isVisible() is False

    # Проверка наличия виджетов
    assert hasattr(dialog, "btn_change")
    assert hasattr(dialog, "lbl_info")
    assert hasattr(dialog, "lbl_dim")
    assert hasattr(dialog, "slider_dim")
    assert hasattr(dialog, "btn_reset")
    assert hasattr(dialog, "btn_close")

    # Начальный расчет процентов (140 / 255.0 * 100 ~ 55%)
    expected_pct = int(round((140 / 255.0) * 100))
    assert dialog.slider_dim.value() == expected_pct
    assert f"{expected_pct}%" in dialog.lbl_dim.text()


def test_slider_clamping_ranges(qapp, make_dialog):
    # Тест на 0%
    dlg_min = make_dialog(current_dim_alpha=0)
    assert dlg_min.slider_dim.value() == 0

    # Тест на 100% альфы (должно быть ограничено макс 90%)
    dlg_max = make_dialog(current_dim_alpha=255)
    assert dlg_max.slider_dim.value() == 90


def test_safe_fallback_when_pixmap_is_null(qapp, make_dialog):
    """Проверка безопасного fallback на дефолтную палитру при отсутствии или null pixmap."""
    dialog = make_dialog(current_path="non_existent_file_xyz.png")
    
    # Искусственно сбрасываем _resolve_pixmap на None
    dialog._resolve_pixmap = lambda: None
    dialog.update_adaptive_palette()

    assert dialog._current_palette is not None
    assert "bg" in dialog._current_palette
    assert "border" in dialog._current_palette
    assert "accent" in dialog._current_palette

    style = dialog.styleSheet()
    assert "BackgroundSettingsDialog#BackgroundSettingsDialog" in style
    assert "border: 1px solid" in style
    assert "border-radius: 12px" in style
    assert "FluentTitleBar" in style
    assert "background: transparent" in style

    # Проверяем отсутствие дублирования border на BackgroundBodyWidget
    body_style = style.split("QWidget#BackgroundBodyWidget")[1].split("}")[0]
    assert "border: none" in body_style


def test_adaptive_palette_with_pixmap(qapp, make_dialog):
    """Проверка обновления палитры под доминирующий цвет обоев."""
    dialog = make_dialog()
    
    # Создаем тестовый пиксмап с выраженным циановым оттенком
    cyan_pix = QPixmap(64, 64)
    cyan_pix.fill(QColor("#00E5FF"))

    dialog._resolve_pixmap = lambda: cyan_pix
    dialog.update_adaptive_palette()

    assert dialog._current_palette is not None
    # При циановом фоне акцент и рамки адаптируются
    assert dialog._current_palette["bg"] != ""
    assert dialog._current_palette["accent"] != ""


def test_slider_value_changed_signal(qapp, make_dialog):
    """Проверка расчета alpha при движении слайдера и испускания dimming_changed."""
    dialog = make_dialog(current_dim_alpha=50)

    received_alphas = []
    dialog.dimming_changed.connect(received_alphas.append)

    # Меняем значение на 40%
    dialog.slider_dim.setValue(40)
    assert len(received_alphas) == 1
    expected_alpha = int(round((40 / 100.0) * 255))
    assert received_alphas[0] == expected_alpha
    assert "40%" in dialog.lbl_dim.text()


def test_reset_background_signal_and_palette(qapp, make_dialog):
    """Проверка сброса фона и испускания сигнала background_reset."""
    dialog = make_dialog(current_path="custom_bg.jpg")
    reset_emitted = []
    dialog.background_reset.connect(lambda: reset_emitted.append(True))

    dialog._reset_background()

    assert len(reset_emitted) == 1
    assert dialog._current_path is None
    assert "Стандартный (NeonRoute)" in dialog.lbl_info.text()


def test_choose_image_signal(qapp, make_dialog, monkeypatch, tmp_path):
    """Проверка выбора нового файла фона через QFileDialog."""
    fake_img = tmp_path / "test_wallpaper.jpg"
    fake_img.write_bytes(b"FAKE_IMG")

    monkeypatch.setattr(
        "PyQt6.QtWidgets.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(fake_img), "Images")
    )

    dialog = make_dialog()
    changed_paths = []
    dialog.background_changed.connect(changed_paths.append)

    dialog._choose_image()

    assert len(changed_paths) == 1
    assert changed_paths[0] == str(fake_img)
    assert dialog._current_path == str(fake_img)
    assert "test_wallpaper.jpg" in dialog.lbl_info.text()
