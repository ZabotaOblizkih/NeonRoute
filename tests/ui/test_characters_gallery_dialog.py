"""
Unit tests for CharactersGalleryDialog in NeonRoute.
Verifies FramelessDialog inheritance, FluentTitleBar integration (46px height, white closeBtn, hidden min/max),
adaptive palette integration with fallback, transparent backgrounds, no duplicate borders,
contrast backgrounds and borders for input fields, line wrap mode for multiline fields,
dark slim scrollbar styling, and dialog lifecycle management.
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent, QTextOption, QPalette
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QScrollArea, QTextEdit, QWidget, QSizePolicy
from qframelesswindow import FramelessDialog

from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog
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
def make_gallery_dialog(qapp, dummy_window):
    created = []

    def _factory(*args, **kwargs):
        if "parent" not in kwargs and not args:
            kwargs["parent"] = dummy_window
        dlg = CharactersGalleryDialog(*args, **kwargs)
        created.append(dlg)
        return dlg

    yield _factory

    for dlg in created:
        try:
            dlg.close()
            dlg.deleteLater()
        except Exception:
            pass


def test_gallery_dialog_initialization_frameless_and_geometry(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    assert isinstance(dialog, FramelessDialog)
    assert dialog.objectName() == "CharactersGalleryDialog"
    assert dialog.minimumWidth() == 980
    assert dialog.minimumHeight() == 626

    # Кастомная шапка FluentTitleBar (46px)
    assert hasattr(dialog, "titleBar") and dialog.titleBar is not None
    assert dialog.titleBar.height() == 46
    assert not dialog.titleBar.closeBtn.isHidden()
    assert dialog.titleBar.minBtn.isHidden() is True
    assert dialog.titleBar.maxBtn.isHidden() is True

    # Проверка наличия и структуры BodyWidget
    assert hasattr(dialog, "body_widget")
    assert dialog.body_widget.objectName() == "GalleryBodyWidget"
    assert hasattr(dialog, "splitter")
    assert hasattr(dialog, "list_widget")
    assert hasattr(dialog, "search_bar")
    assert hasattr(dialog, "btn_create")
    assert hasattr(dialog, "btn_save")
    assert hasattr(dialog, "btn_start_dialog")


def test_gallery_dialog_adaptive_palette_and_fallback(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    # Проверка палитры при инициализации (fallback)
    assert isinstance(dialog._current_palette, dict)
    assert "bg" in dialog._current_palette
    assert "border" in dialog._current_palette
    assert "accent" in dialog._current_palette

    # Проверка обновления палитры с цветным изображением
    pixmap = QPixmap(100, 100)
    pixmap.fill(QColor("#0F2027"))
    dialog.parent().background_pixmap = pixmap

    dialog.update_adaptive_palette()
    assert dialog._current_palette is not None
    assert dialog._current_palette["bg"].startswith("#")

    # Проверка палитры при null pixmap (безопасный fallback)
    dialog.parent().background_pixmap = QPixmap()
    dialog.update_adaptive_palette()
    assert "bg" in dialog._current_palette
    assert "text_primary" in dialog._current_palette


def test_gallery_dialog_stylesheet_tokens_and_borders(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    qss = dialog.styleSheet()
    # 1. Корневой диалог имеет рамку 1px solid
    assert "CharactersGalleryDialog#CharactersGalleryDialog" in qss
    assert "border: 1px solid" in qss

    # 2. BodyWidget не дублирует рамку (border: none)
    assert "QWidget#GalleryBodyWidget" in qss
    assert "border: none;" in qss

    # 3. FluentTitleBar имеет такой же bg
    assert "FluentTitleBar" in qss

    # 4. Прозрачные подложки QLabel и QScrollArea viewport
    assert "background: transparent;" in qss
    assert "QLabel" in qss
    assert "QScrollArea" in qss


def test_gallery_dialog_escape_key_rejects(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    rejected = False

    def on_reject():
        nonlocal rejected
        rejected = True

    dialog.rejected.connect(on_reject)

    # Эмулируем нажатие клавиши Escape
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dialog.keyPressEvent(event)

    assert rejected is True


def test_gallery_dialog_context_budget_palette_integration(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    assert hasattr(dialog, "traffic_light_frame")
    assert dialog.traffic_light_frame.objectName() == "TrafficLightFrame"

    # Вызываем расчет бюджета
    dialog._update_context_budget()
    tl_qss = dialog.traffic_light_frame.styleSheet()
    term_bg = dialog._current_palette.get("terminal_bg", "#0A0E18")
    assert f"background-color: {term_bg}" in tl_qss


def test_gallery_dialog_input_fields_styling_and_contrast_background(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    palette = dialog._current_palette
    term_bg = palette.get("terminal_bg", "#0B0E14")
    border = palette.get("border", "#2D3748")
    accent = palette.get("accent", "#6366F1")

    qss = dialog.styleSheet()

    # 1. Проверяем наличие селекторов всех полей ввода
    assert "#InputName" in qss
    assert "#InputVisualDna" in qss
    assert "#InputDesc" in qss
    assert "#InputPersonality" in qss
    assert "#InputScenario" in qss
    assert "#InputSystemPrompt" in qss
    assert "#InputMesExample" in qss
    assert "#InputFirstMes" in qss
    assert "#InputLorePreview" in qss

    # 2. Проверяем контрастный темный фон terminal_bg и рамку border
    assert f"background-color: {term_bg};" in qss
    assert f"border: 1px solid {border};" in qss
    assert "border-radius: 8px;" in qss
    assert "padding: 8px 10px;" in qss

    # 3. Проверяем акцентную подсветку фокуса (:focus)
    assert f"border: 1px solid {accent};" in qss

    # 4. Проверяем, что поля ввода имеют правильные objectName
    assert dialog.input_name.objectName() == "InputName"
    assert dialog.input_visual_dna.objectName() == "InputVisualDna"
    assert dialog.input_desc.objectName() == "InputDesc"
    assert dialog.input_personality.objectName() == "InputPersonality"
    assert dialog.input_scenario.objectName() == "InputScenario"
    assert dialog.input_system_prompt.objectName() == "InputSystemPrompt"
    assert dialog.input_mes_example.objectName() == "InputMesExample"
    assert dialog.input_first_mes.objectName() == "InputFirstMes"
    assert dialog.input_lore_preview.objectName() == "InputLorePreview"


def test_gallery_dialog_multiline_fields_word_wrap(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    multiline_edits = [
        dialog.input_visual_dna,
        dialog.input_desc,
        dialog.input_personality,
        dialog.input_scenario,
        dialog.input_system_prompt,
        dialog.input_mes_example,
        dialog.input_first_mes,
        dialog.input_lore_preview,
    ]

    for edit in multiline_edits:
        assert edit.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth
        assert edit.wordWrapMode() == QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        assert edit.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert edit.minimumWidth() == 0
        assert edit.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored


def test_gallery_dialog_scroll_area_no_horizontal_scroll_and_size_policy(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    scroll = dialog.findChild(QScrollArea, "EditorScrollArea")
    assert scroll is not None
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    scroll_content = dialog.findChild(QWidget, "EditorScrollContent")
    assert scroll_content is not None
    assert scroll_content.minimumWidth() == 0
    assert scroll_content.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored

    assert dialog.lbl_author_info.minimumWidth() == 0
    assert dialog.lbl_author_info.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored

    assert dialog.input_name.minimumWidth() == 0
    assert dialog.input_name.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored

    assert dialog.traffic_light_frame.minimumWidth() == 0
    assert dialog.traffic_light_frame.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
    assert dialog.lbl_traffic_light.wordWrap() is True


def test_gallery_dialog_scrollbars_styling(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    palette = dialog._current_palette
    border = palette.get("border", "#2D3748")
    accent = palette.get("accent", "#6366F1")

    qss = dialog.styleSheet()

    # 1. Тонкая полоса 6-8px и прозрачный трек
    assert "QScrollBar:vertical" in qss
    assert "width: 6px;" in qss
    assert "QScrollBar:horizontal" in qss
    assert "height: 6px;" in qss

    # 2. Ползунок в тонах palette['border']
    assert f"background-color: {border};" in qss
    assert "border-radius: 3px;" in qss

    # 3. Подсветка ползунка акцентом при наведении
    assert f"background-color: {accent};" in qss

    # 4. Подавление системных кнопок и трека
    assert "QScrollBar::add-line:vertical" in qss
    assert "QScrollBar::add-page:vertical" in qss


def test_gallery_dialog_visual_dna_and_section_headers_layout(qapp, make_gallery_dialog):
    dialog = make_gallery_dialog()
    dialog.show()

    # 1. Заголовок секции SD отображается в одну строку, не сжат и не имеет Policy.Ignored
    assert dialog.lbl_dna_title.wordWrap() is False
    assert dialog.lbl_dna_title.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Ignored

    # 2. Только длинная метка автора рядом с именем имеет Policy.Ignored
    assert dialog.lbl_author_info.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored

    # 3. input_visual_dna компактный (64px)
    assert dialog.input_visual_dna.minimumHeight() == 64
    assert dialog.input_visual_dna.maximumHeight() == 64

    # 4. Основной текст в input_visual_dna — text_primary, а placeholder — text_secondary
    qss = dialog.styleSheet()
    palette = dialog._current_palette
    text_primary = palette.get("text_primary", "#F8FAFC")
    text_secondary = palette.get("text_secondary", "#94A3B8")

    assert f"color: {text_primary};" in qss
    placeholder_color = dialog.input_visual_dna.palette().color(QPalette.ColorRole.PlaceholderText).name().upper()
    expected_placeholder = QColor(text_secondary).name().upper()
    assert placeholder_color == expected_placeholder
