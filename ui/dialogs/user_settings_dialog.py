"""
UserSettingsDialog for NeonRoute.
Provides user identity configuration for Elaria Assistant mode and RPG Player Personas Manager.
Refactored strictly according to Windows 11 Fluent Design standard:
- Base class: FramelessDialog + FluentTitleBar (height 46px, min/max hidden, closeBtn connected to reject).
- Dynamic Adaptive Palette extracted from workspace wallpaper (Material You / Fluent Tint).
- Tab navigation using QFluentWidgets SegmentedWidget:
  • Tab 1: «⚡ Ассистент»
  • Tab 2: «🎭 Ролевой чат» (Player Personas Manager)
- Tab 1: Circular interactive user avatar with preview and file selection, LineEdit for user name,
  TextEdit for bio context (<UserInfo>), ComboBox for LM Studio model, SegmentedWidget for art generation mode.
- Tab 2: Two-panel persona manager:
  • Left panel: Persona list with [➕ Создать], [★ Сделать основным], [🗑️ Удалить]
  • Right panel: Detailed persona card (avatar, hero name, grammatical gender switch, traits, backstory,
    starting inventory, Visual DNA, SD outfit)
- Action bar: [ Сохранить изменения ] (PrimaryPushButton with FIF.ACCEPT) and [ Отмена ]
- Persistence to data/user_profiles.json, data/user_personas.json, and config.json
- Strict preservation of all signals: settings_applied(dict), profiles_updated()
"""

import uuid
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QRect, QRectF
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QPen, QPainterPath, QKeyEvent, QRadialGradient, QBrush
)
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QStackedWidget, QListWidget, QListWidgetItem,
    QScrollArea, QFileDialog, QMessageBox, QFrame, QSizePolicy
)

from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    SegmentedWidget,
    PushButton,
    PrimaryPushButton,
    TransparentPushButton,
    LineEdit,
    TextEdit,
    ComboBox,
    FluentIcon as FIF
)

from config import CHARACTERS_DIR, DATA_DIR, DEFAULT_AUTO_GENERATE_ART
from services.session_manager import SessionManager
from ui.common.palette_utils import extract_adaptive_palette
from utils.config_manager import ConfigManager
from utils.file_utils import atomic_write_json
from ui.components.chat_bubble import render_circular_avatar

logger = logging.getLogger("NeonRoute.UserSettingsDialog")


class InteractiveAvatarWidget(QWidget):
    """
    Интерактивный круглый аватар с мягким растворением контура (Seamless Edge Feathering),
    тактильной оптической глубиной при наведении (Ambient Depth / Hover Lift)
    и поддержкой прямого клика для смены изображения.
    """
    avatar_clicked = pyqtSignal()

    def __init__(self, size: int = 64, parent=None):
        super().__init__(parent)
        self.avatar_size = size
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pixmap = QPixmap()
        self._is_hovered = False
        self._accent_color = QColor(0, 240, 255)
        self.setToolTip("Нажмите, чтобы выбрать новое изображение")

    def set_accent_color(self, color: QColor):
        self._accent_color = color
        self.update()

    def setPixmap(self, pixmap: QPixmap):
        self._pixmap = pixmap
        self.update()

    def pixmap(self) -> QPixmap:
        return self._pixmap

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.avatar_clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w = float(self.width())
        h = float(self.height())
        center_x = w / 2.0
        center_y = h / 2.0

        # 1. Естественная мягкая тень глубины под аватаром (Ambient Drop Shadow)
        shadow_y_offset = 1.5 if self._is_hovered else 0.8
        shadow_expand = 1.0 if self._is_hovered else 0.0
        shadow_alpha = 110 if self._is_hovered else 45
        shadow_radius = (w / 2.0) - 1.0 + shadow_expand

        shadow_grad = QRadialGradient(center_x, center_y + shadow_y_offset, shadow_radius)
        shadow_grad.setColorAt(0.0, QColor(0, 0, 0, shadow_alpha))
        shadow_grad.setColorAt(0.72, QColor(0, 0, 0, int(shadow_alpha * 0.80)))
        shadow_grad.setColorAt(0.92, QColor(0, 0, 0, int(shadow_alpha * 0.30)))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setBrush(QBrush(shadow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(
            center_x - shadow_radius,
            center_y + shadow_y_offset - shadow_radius,
            shadow_radius * 2.0,
            shadow_radius * 2.0
        ))

        # 2. Отрисовка аватара с легким оптическим подъемом (Hover Lift)
        if not self._pixmap.isNull():
            if self._is_hovered:
                target_rect = QRectF(0.0, 0.0, w, h)
            else:
                target_rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0)

            source_rect = QRectF(0.0, 0.0, float(self._pixmap.width()), float(self._pixmap.height()))
            painter.drawPixmap(target_rect, self._pixmap, source_rect)
        else:
            # Деликатная подложка по умолчанию, если изображение еще не загружено
            bg_rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0) if not self._is_hovered else QRectF(0.0, 0.0, w, h)
            painter.setBrush(QColor(15, 23, 42))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(bg_rect)


class _TabCompatProxy:
    """Адаптер для обратной совместимости с self.tab_widget.setCurrentIndex()."""
    def __init__(self, dialog: 'UserSettingsDialog'):
        self._dlg = dialog

    def setCurrentIndex(self, idx: int):
        self._dlg.set_current_tab(idx)

    def currentIndex(self) -> int:
        return self._dlg.stacked_widget.currentIndex()


class _RadioCompat:
    """Адаптер для совместимости с радиокнопками rb_art_auto / rb_art_manual."""
    def __init__(self, segmented: SegmentedWidget, target_key: str):
        self._seg = segmented
        self._target_key = target_key

    def isChecked(self) -> bool:
        return self._seg.currentRouteKey() == self._target_key

    def setChecked(self, checked: bool):
        if checked:
            self._seg.setCurrentItem(self._target_key)


class _GenderComboCompat:
    """Адаптер для совместимости со старым self.combo_rp_gender."""
    def __init__(self, segmented: SegmentedWidget):
        self._seg = segmented

    def currentIndex(self) -> int:
        return 1 if self._seg.currentRouteKey() == "female" else 0

    def setCurrentIndex(self, idx: int):
        self._seg.setCurrentItem("female" if idx == 1 else "male")

    def blockSignals(self, b: bool):
        pass


class UserSettingsDialog(FramelessDialog):
    profiles_updated = pyqtSignal()
    settings_applied = pyqtSignal(dict)

    def __init__(self, initial_tab: int = 0, parent=None):
        super().__init__(parent=parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Настройки профиля и Эларии — NeonRoute")
        self.setObjectName("UserSettingsDialog")
        self.resize(840, 720)
        self.setMinimumSize(780, 640)
        self.setModal(True)

        # 1. Custom Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self._current_palette: Dict[str, str] = {}

        loaded_profiles = SessionManager.load_user_profiles()
        self.profiles: Dict[str, Any] = loaded_profiles if isinstance(loaded_profiles, dict) else {}

        asst_data = self.profiles.get("assistant_user", {})
        self.assistant_avatar_temp: Optional[str] = asst_data.get("avatar_path") if isinstance(asst_data, dict) else None
        self.rp_avatar_temp: Optional[str] = None
        self.active_persona_id: Optional[str] = None
        self.active_primary_persona_id: Optional[str] = None
        self._is_populating: bool = False

        self._setup_ui()

        # Backward compatibility proxy
        self.tab_widget = _TabCompatProxy(self)

        self.set_current_tab(initial_tab)
        self._load_assistant_fields()
        self._load_rp_personas_list()

        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон для адаптивной палитры."""
        parent_win = getattr(self, "_parent_ref", None) or self.parent()
        p = parent_win
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = getattr(p, "_parent_ref", None) or (p.parent() if hasattr(p, "parent") else None)
        return None

    def update_adaptive_palette(self):
        """Адаптирует цветовую палитру окна, карточек и кнопок под активные обои."""
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        if hasattr(self, "asst_avatar_lbl") and hasattr(self.asst_avatar_lbl, "set_accent_color"):
            self.asst_avatar_lbl.set_accent_color(QColor(palette["accent"]))
        if hasattr(self, "rp_avatar_lbl") and hasattr(self.rp_avatar_lbl, "set_accent_color"):
            self.rp_avatar_lbl.set_accent_color(QColor(palette["accent"]))

        self.setStyleSheet(f"""
            UserSettingsDialog#UserSettingsDialog,
            UserSettingsDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#UserSettingBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #UserSettingBodyWidget QWidget {{
                background-color: transparent;
            }}
            QLabel {{
                color: {palette['text_secondary']};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }}
            QLabel.section_title {{
                color: {palette['group_title']};
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.5px;
            }}
            QLabel.field_label {{
                color: {palette['text_secondary']};
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel.field_label_accent {{
                color: {palette['accent']};
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel.hint_sub {{
                color: {palette['text_secondary']};
                font-size: 11px;
                font-weight: normal;
            }}
            QFrame.glass_card {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame.glass_card:hover {{
                background-color: {palette['card_hover']};
            }}
            LineEdit {{
                background-color: {palette['bg']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            LineEdit:focus {{
                border: 1px solid {palette['accent']};
                background-color: {palette['card_hover']};
            }}
            TextEdit {{
                background-color: {palette['bg']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
            TextEdit:focus {{
                border: 1px solid {palette['accent']};
                background-color: {palette['card_hover']};
            }}
            ComboBox {{
                background-color: {palette['bg']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
                min-height: 28px;
            }}
            ComboBox:hover {{
                border-color: {palette['accent']};
                background-color: {palette['card_hover']};
            }}
            ComboBox:focus {{
                border-color: {palette['accent']};
            }}
            QListWidget {{
                background-color: {palette['bg']};
                border: 1px solid {palette['card_border']};
                border-radius: 8px;
                padding: 4px;
            }}
            QListWidget::item {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 8px 10px;
                margin-bottom: 4px;
                color: {palette['text_primary']};
                font-weight: 500;
            }}
            QListWidget::item:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QListWidget::item:selected {{
                background-color: {palette['card_hover']};
                border: 1px solid {palette['accent']};
                color: {palette['accent']};
                font-weight: bold;
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            PushButton, TransparentPushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 16px;
                min-height: 20px;
            }}
            PushButton:hover, TransparentPushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed, TransparentPushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-weight: 600;
                font-size: 12px;
                padding: 6px 20px;
                min-height: 20px;
            }}
            PrimaryPushButton:hover {{
                background-color: {palette['group_title']};
                border-color: {palette['group_title']};
            }}
        """)

    def _apply_glassmorphism_styles(self):
        """Алиас для обратной совместимости."""
        self.update_adaptive_palette()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_adaptive_palette()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("UserSettingBodyWidget")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(20, 10, 20, 16)
        body_layout.setSpacing(12)

        # 1. Top Navigation: SegmentedWidget
        self.nav_segmented = SegmentedWidget(self.body_widget)
        self.nav_segmented.addItem(
            routeKey="tab_asst",
            text="⚡ Ассистент",
            onClick=lambda: self.set_current_tab(0)
        )
        self.nav_segmented.addItem(
            routeKey="tab_rp",
            text="🎭 Ролевой чат",
            onClick=lambda: self.set_current_tab(1)
        )
        body_layout.addWidget(self.nav_segmented)

        # 2. Content: QStackedWidget
        self.stacked_widget = QStackedWidget(self.body_widget)
        self.tab_asst_widget = self._create_assistant_tab()
        self.tab_rp_widget = self._create_rp_tab()

        self.stacked_widget.addWidget(self.tab_asst_widget)
        self.stacked_widget.addWidget(self.tab_rp_widget)
        body_layout.addWidget(self.stacked_widget, 1)

        # 3. Bottom Action Bar
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_box.addStretch()

        self.btn_cancel = PushButton("Отмена", self.body_widget)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save_all = PrimaryPushButton(FIF.ACCEPT, "Сохранить изменения", self.body_widget)
        self.btn_save_all.setObjectName("btn_save_all")
        self.btn_save_all.clicked.connect(self._save_all_and_close)
        btn_box.addWidget(self.btn_save_all)

        body_layout.addLayout(btn_box)
        root.addWidget(self.body_widget)

    def set_current_tab(self, index: int):
        idx = max(0, min(1, index))
        self.stacked_widget.setCurrentIndex(idx)
        route_key = "tab_asst" if idx == 0 else "tab_rp"
        self.nav_segmented.setCurrentItem(route_key)

    # =========================================================================
    # Вкладка 1: «⚡ Ассистент»
    # =========================================================================
    def _create_assistant_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # 1. User Avatar Block
        avatar_card = QFrame(widget)
        avatar_card.setProperty("class", "glass_card")
        avatar_card_layout = QHBoxLayout(avatar_card)
        avatar_card_layout.setContentsMargins(14, 12, 14, 12)
        avatar_card_layout.setSpacing(14)

        self.asst_avatar_lbl = InteractiveAvatarWidget(size=64, parent=avatar_card)
        self.asst_avatar_lbl.avatar_clicked.connect(self._choose_assistant_avatar)
        avatar_card_layout.addWidget(self.asst_avatar_lbl)

        asst_avatar_info = QVBoxLayout()
        asst_avatar_info.setSpacing(4)
        asst_avatar_title = QLabel("АВАТАР ПОЛЬЗОВАТЕЛЯ (В ЧАТЕ С ЭЛАРИЕЙ)", avatar_card)
        asst_avatar_title.setProperty("class", "field_label")
        asst_avatar_info.addWidget(asst_avatar_title)

        asst_avatar_hint = QLabel("Кликните на аватар или выберите файл на диске для сообщений в чате", avatar_card)
        asst_avatar_hint.setProperty("class", "hint_sub")
        asst_avatar_info.addWidget(asst_avatar_hint)

        btn_ch_avatar = PushButton("🖼️ Выбрать файл...", avatar_card, icon=FIF.PHOTO)
        btn_ch_avatar.setFixedWidth(160)
        btn_ch_avatar.clicked.connect(self._choose_assistant_avatar)
        asst_avatar_info.addWidget(btn_ch_avatar)

        avatar_card_layout.addLayout(asst_avatar_info, 1)
        layout.addWidget(avatar_card)

        # 2. Name Field
        lbl_name = QLabel("ИМЯ / ОБРАЩЕНИЕ К ВАМ:")
        lbl_name.setProperty("class", "field_label")
        layout.addWidget(lbl_name)

        self.asst_name_input = LineEdit(widget)
        self.asst_name_input.setPlaceholderText("Например: Алексей, Шеф, Пользователь")
        self.asst_name_input.textChanged.connect(self._on_asst_name_changed)
        layout.addWidget(self.asst_name_input)

        # 3. Bio Context (<UserInfo>)
        lbl_bio = QLabel("КОНТЕКСТ О ВАС (ПОМНИТЬ ВСЕГДА):")
        lbl_bio.setProperty("class", "field_label")
        layout.addWidget(lbl_bio)

        self.asst_bio_input = TextEdit(widget)
        self.asst_bio_input.setFixedHeight(110)
        self.asst_bio_input.setPlaceholderText(
            "Постоянный био-контекст, который всегда инжектируется в системный блок <UserInfo>.\n"
            "Например:\n"
            "Разработчик Python/C++. Предпочитаю краткие ответы без лишней вводной вежливости."
        )
        layout.addWidget(self.asst_bio_input)

        # 4. Model Selection for Elaria
        lbl_model = QLabel("МОДЕЛЬ ДЛЯ ЭЛАРИИ (LM STUDIO):")
        lbl_model.setProperty("class", "field_label")
        layout.addWidget(lbl_model)

        self.combo_asst_model = ComboBox(widget)
        layout.addWidget(self.combo_asst_model)

        # 5. Art Generation Mode
        lbl_art = QLabel("ГЕНЕРАЦИЯ ИЛЛЮСТРАЦИЙ ЭЛАРИЕЙ:")
        lbl_art.setProperty("class", "field_label")
        layout.addWidget(lbl_art)

        self.art_mode_segmented = SegmentedWidget(widget)
        self.art_mode_segmented.addItem("manual", "✋ Вручную (по кнопке в сообщении)")
        self.art_mode_segmented.addItem("auto", "⚡ Автоматически (сразу в Forge)")
        self.art_mode_segmented.setCurrentItem("manual")
        layout.addWidget(self.art_mode_segmented)

        # Backward compatibility aliases
        self.rb_art_manual = _RadioCompat(self.art_mode_segmented, "manual")
        self.rb_art_auto = _RadioCompat(self.art_mode_segmented, "auto")

        layout.addStretch()
        return widget

    def _load_assistant_fields(self):
        asst = self.profiles.get("assistant_user", {})
        if not isinstance(asst, dict):
            asst = {}
        self.asst_name_input.setText(asst.get("name", "Пользователь"))
        self.asst_bio_input.setPlainText(asst.get("bio", ""))
        self._update_assistant_avatar_preview()

        cfg = ConfigManager.load_config()
        auto_art = cfg.get("auto_generate_art", DEFAULT_AUTO_GENERATE_ART)
        if auto_art:
            self.art_mode_segmented.setCurrentItem("auto")
        else:
            self.art_mode_segmented.setCurrentItem("manual")

        self.combo_asst_model.blockSignals(True)
        self.combo_asst_model.clear()

        parent_window = self._parent_ref or self.parent()
        parent_combo = getattr(parent_window, "lm_combo", None) if parent_window else None
        active_model = cfg.get("active_model", "")

        if parent_combo and hasattr(parent_combo, "count") and parent_combo.count() > 0:
            for i in range(parent_combo.count()):
                m_text = parent_combo.itemText(i).strip()
                if m_text and not m_text.startswith("default") and self.combo_asst_model.findText(m_text) == -1:
                    self.combo_asst_model.addItem(m_text)
            curr_parent_model = parent_combo.currentText().strip()
            if curr_parent_model and not curr_parent_model.startswith("default"):
                active_model = curr_parent_model

        if active_model:
            idx = self.combo_asst_model.findText(active_model)
            if idx >= 0:
                self.combo_asst_model.setCurrentIndex(idx)
            else:
                self.combo_asst_model.addItem(active_model)
                self.combo_asst_model.setCurrentIndex(self.combo_asst_model.count() - 1)

        self.combo_asst_model.blockSignals(False)

    def _choose_assistant_avatar(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Выберите аватар для Ассистента", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if f:
            try:
                CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
                dest = CHARACTERS_DIR / f"user_asst_{Path(f).name}"
                shutil.copy2(f, dest)
                self.assistant_avatar_temp = str(dest)
                self._update_assistant_avatar_preview()
            except Exception as e:
                logger.error(f"Ошибка сохранения аватара ассистента: {e}")

    def _update_assistant_avatar_preview(self):
        p = Path(self.assistant_avatar_temp) if self.assistant_avatar_temp and Path(self.assistant_avatar_temp).exists() else None
        name = self.asst_name_input.text().strip() if hasattr(self, "asst_name_input") else "User"
        pix = render_circular_avatar(p, name or "User", size=64, is_user=True)
        self.asst_avatar_lbl.setPixmap(pix)

    def _on_asst_name_changed(self, text: str):
        if not self.assistant_avatar_temp:
            self._update_assistant_avatar_preview()

    # =========================================================================
    # Вкладка 2: «🎭 Ролевой чат» (Менеджер персон игрока)
    # =========================================================================
    def _create_rp_tab(self) -> QWidget:
        widget = QWidget()
        main_layout = QHBoxLayout(widget)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(12)

        # ----------------- ЛЕВАЯ ПАНЕЛЬ: СПИСОК ПЕРСОН -----------------
        left_panel = QFrame(widget)
        left_panel.setProperty("class", "glass_card")
        left_panel.setFixedWidth(240)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(10, 12, 10, 12)
        left_layout.setSpacing(8)

        lbl_list_title = QLabel("ПЕРСОНЫ ИГРОКА:", left_panel)
        lbl_list_title.setProperty("class", "field_label")
        left_layout.addWidget(lbl_list_title)

        self.list_personas = QListWidget(left_panel)
        self.list_personas.currentRowChanged.connect(self._on_persona_row_changed)
        left_layout.addWidget(self.list_personas, 1)

        # Buttons under list
        self.btn_new_persona = PushButton("➕ Создать", left_panel)
        self.btn_new_persona.clicked.connect(self._create_new_persona)
        left_layout.addWidget(self.btn_new_persona)

        self.btn_set_primary = PushButton("★ Сделать основным", left_panel)
        self.btn_set_primary.clicked.connect(self._set_current_as_primary)
        left_layout.addWidget(self.btn_set_primary)

        self.btn_del_persona = PushButton("🗑️ Удалить", left_panel)
        self.btn_del_persona.clicked.connect(self._delete_current_persona)
        left_layout.addWidget(self.btn_del_persona)

        main_layout.addWidget(left_panel)

        # ----------------- ПРАВАЯ ПАНЕЛЬ: ДЕТАЛЬНАЯ КАРТОЧКА -----------------
        right_panel = QFrame(widget)
        right_panel.setProperty("class", "glass_card")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)

        scroll = QScrollArea(right_panel)
        scroll.setWidgetResizable(True)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(10, 8, 12, 8)
        scroll_layout.setSpacing(8)

        # 1. Avatar & Hero Name & Gender
        id_row = QHBoxLayout()
        id_row.setSpacing(12)

        self.rp_avatar_lbl = InteractiveAvatarWidget(size=64, parent=scroll_content)
        self.rp_avatar_lbl.avatar_clicked.connect(self._choose_rp_avatar)
        id_row.addWidget(self.rp_avatar_lbl)

        btn_ch_rp_avatar = PushButton("🖼️ Аватар...", scroll_content, icon=FIF.PHOTO)
        btn_ch_rp_avatar.clicked.connect(self._choose_rp_avatar)
        id_row.addWidget(btn_ch_rp_avatar)

        name_col = QVBoxLayout()
        name_col.setSpacing(3)
        lbl_hero_name = QLabel("ИМЯ ГЕРОЯ (МАКРОС {{user}}):", scroll_content)
        lbl_hero_name.setProperty("class", "field_label_accent")
        name_col.addWidget(lbl_hero_name)

        self.rp_name_input = LineEdit(scroll_content)
        self.rp_name_input.setPlaceholderText("Например: Дэниэл")
        self.rp_name_input.textChanged.connect(self._on_rp_name_changed)
        name_col.addWidget(self.rp_name_input)
        id_row.addLayout(name_col, 1)

        scroll_layout.addLayout(id_row)

        # Gender Selector (SegmentedWidget)
        gender_row = QHBoxLayout()
        gender_row.setSpacing(8)
        lbl_gender = QLabel("ГРАММАТИЧЕСКИЙ ПОЛ ГЕРОЯ:", scroll_content)
        lbl_gender.setProperty("class", "field_label")
        gender_row.addWidget(lbl_gender)

        self.sw_gender = SegmentedWidget(scroll_content)
        self.sw_gender.addItem("male", "♂ Мужской (he/him)", onClick=lambda: self._on_gender_changed("male"))
        self.sw_gender.addItem("female", "♀ Женский (she/her)", onClick=lambda: self._on_gender_changed("female"))
        self.sw_gender.setCurrentItem("male")
        gender_row.addWidget(self.sw_gender, 1)
        scroll_layout.addLayout(gender_row)

        # Backward compatibility alias
        self.combo_rp_gender = _GenderComboCompat(self.sw_gender)

        # 2. Traits & Personality
        lbl_pers = QLabel("ХАРАКТЕР И ПОВАДКИ (<PlayerCharacter>):", scroll_content)
        lbl_pers.setProperty("class", "field_label")
        scroll_layout.addWidget(lbl_pers)

        self.rp_personality_input = TextEdit(scroll_content)
        self.rp_personality_input.setFixedHeight(65)
        self.rp_personality_input.setPlaceholderText(
            "Например: Хладнокровный, наблюдательный наемник. В стрессе говорит коротко."
        )
        scroll_layout.addWidget(self.rp_personality_input)

        # 3. Backstory
        lbl_back = QLabel("ПРЕДЫСТОРИЯ И ПРОИСХОЖДЕНИЕ:", scroll_content)
        lbl_back.setProperty("class", "field_label")
        scroll_layout.addWidget(lbl_back)

        self.rp_backstory_input = TextEdit(scroll_content)
        self.rp_backstory_input.setFixedHeight(75)
        self.rp_backstory_input.setPlaceholderText(
            "Например: Бывший капитан стражи. Изучал древние культы и руины."
        )
        scroll_layout.addWidget(self.rp_backstory_input)

        # 4. Inventory
        lbl_inv = QLabel("СТАРТОВЫЙ ИНВЕНТАРЬ (ДЛЯ КАМПАНИИ):", scroll_content)
        lbl_inv.setProperty("class", "field_label")
        scroll_layout.addWidget(lbl_inv)

        self.rp_inventory_input = LineEdit(scroll_content)
        self.rp_inventory_input.setPlaceholderText("Например: компас, блокнот с записями, фонарь, альпинистский трос")
        scroll_layout.addWidget(self.rp_inventory_input)

        # 5. Visual DNA for SD Forge
        lbl_dna = QLabel("🎨 VISUAL DNA (АНГЛИЙСКИЕ ТЕГИ ВНЕШНОСТИ ДЛЯ SD FORGE):", scroll_content)
        lbl_dna.setProperty("class", "field_label_accent")
        scroll_layout.addWidget(lbl_dna)

        self.rp_visual_dna_input = TextEdit(scroll_content)
        self.rp_visual_dna_input.setFixedHeight(60)
        self.rp_visual_dna_input.setStyleSheet("""
            TextEdit {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
            }
        """)
        self.rp_visual_dna_input.setPlaceholderText(
            "1man, 28yo, athletic lean build, dark brown short hair, stubble, amber eyes"
        )
        scroll_layout.addWidget(self.rp_visual_dna_input)

        # 6. SD Outfit
        lbl_outfit = QLabel("👗 СТАРТОВАЯ ОДЕЖДА ДЛЯ АРТОВ (SD OUTFIT):", scroll_content)
        lbl_outfit.setProperty("class", "field_label_accent")
        scroll_layout.addWidget(lbl_outfit)

        self.rp_outfit_input = LineEdit(scroll_content)
        self.rp_outfit_input.setStyleSheet("""
            LineEdit {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
            }
        """)
        self.rp_outfit_input.setPlaceholderText("linen explorer shirt, cargo pants, leather belt, combat boots")
        scroll_layout.addWidget(self.rp_outfit_input)

        scroll.setWidget(scroll_content)
        right_layout.addWidget(scroll)
        main_layout.addWidget(right_panel, 1)

        return widget

    def _on_gender_changed(self, gender_key: str):
        if self._is_populating or not self.active_persona_id:
            return
        personas = self._get_personas_dict()
        if self.active_persona_id in personas:
            personas[self.active_persona_id]["gender"] = gender_key

    def _get_personas_dict(self) -> dict:
        rp_settings = self.profiles.get("rp_settings")
        if not isinstance(rp_settings, dict):
            self.profiles["rp_settings"] = rp_settings = {}
        personas = rp_settings.get("personas")
        if not isinstance(personas, dict):
            rp_settings["personas"] = personas = {}
        return personas

    def _load_rp_personas_list(self):
        self._is_populating = True
        rp_settings = self.profiles.get("rp_settings")
        if not isinstance(rp_settings, dict):
            self.profiles["rp_settings"] = rp_settings = {}
        personas = self._get_personas_dict()
        if not personas:
            personas["persona_default"] = {"name": "Игрок", "avatar_path": None, "bio": "", "gender": "male"}
            rp_settings["active_persona_id"] = "persona_default"

        active_id = rp_settings.get("active_persona_id")
        if active_id not in personas:
            active_id = next(iter(personas.keys()))
            rp_settings["active_persona_id"] = active_id

        self.active_primary_persona_id = active_id
        if not self.active_persona_id or self.active_persona_id not in personas:
            self.active_persona_id = active_id

        self.list_personas.blockSignals(True)
        self.list_personas.clear()
        selected_row = 0

        for idx, (pid, pdata) in enumerate(personas.items()):
            p_name = pdata.get("name", "Персонаж").strip() or "Персонаж"
            is_pri = (pid == self.active_primary_persona_id)
            label = f"★ {p_name} (Основной)" if is_pri else f"👤 {p_name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            if is_pri:
                item.setForeground(QColor("#00F0FF"))
            else:
                item.setForeground(QColor("#CBD5E1"))
            self.list_personas.addItem(item)
            if pid == self.active_persona_id:
                selected_row = idx

        self.list_personas.setCurrentRow(selected_row)
        self.list_personas.blockSignals(False)

        self._populate_persona_fields(self.active_persona_id)
        self._is_populating = False

    def _populate_persona_fields(self, pid: str):
        personas = self._get_personas_dict()
        pdata = personas.get(pid, {}) if isinstance(personas, dict) else {}

        self.rp_name_input.blockSignals(True)
        self.rp_name_input.setText(pdata.get("name", ""))
        self.rp_name_input.blockSignals(False)

        gender = str(pdata.get("gender", "male")).lower()
        self.sw_gender.setCurrentItem("female" if gender == "female" else "male")

        self.rp_personality_input.blockSignals(True)
        self.rp_personality_input.setPlainText(pdata.get("personality", ""))
        self.rp_personality_input.blockSignals(False)

        self.rp_backstory_input.blockSignals(True)
        self.rp_backstory_input.setPlainText(pdata.get("backstory", "") or pdata.get("bio", ""))
        self.rp_backstory_input.blockSignals(False)

        self.rp_inventory_input.blockSignals(True)
        self.rp_inventory_input.setText(pdata.get("inventory", ""))
        self.rp_inventory_input.blockSignals(False)

        self.rp_visual_dna_input.blockSignals(True)
        self.rp_visual_dna_input.setPlainText(pdata.get("visual_dna", ""))
        self.rp_visual_dna_input.blockSignals(False)

        self.rp_outfit_input.blockSignals(True)
        self.rp_outfit_input.setText(pdata.get("outfit", ""))
        self.rp_outfit_input.blockSignals(False)

        self.rp_avatar_temp = pdata.get("avatar_path")
        self._update_rp_avatar_preview()

    def _on_persona_row_changed(self, row: int):
        if self._is_populating or row < 0 or row >= self.list_personas.count():
            return
        item = self.list_personas.item(row)
        if not item:
            return
        new_pid = item.data(Qt.ItemDataRole.UserRole)
        if not new_pid or new_pid == self.active_persona_id:
            return

        self._sync_current_persona_fields()
        self.active_persona_id = new_pid
        self._populate_persona_fields(new_pid)

    def _on_rp_name_changed(self, text: str):
        if self._is_populating or not self.active_persona_id:
            return
        personas = self._get_personas_dict()
        if self.active_persona_id in personas:
            personas[self.active_persona_id]["name"] = text
            row = self.list_personas.currentRow()
            if row >= 0:
                item = self.list_personas.item(row)
                if item:
                    display = text.strip() or "Персонаж"
                    is_pri = (self.active_persona_id == getattr(self, "active_primary_persona_id", None))
                    item.setText(f"★ {display} (Основной)" if is_pri else f"👤 {display}")
            self._update_rp_avatar_preview()

    def _sync_current_persona_fields(self):
        if self._is_populating or not getattr(self, "active_persona_id", None):
            return
        personas = self._get_personas_dict()
        if self.active_persona_id in personas:
            name = self.rp_name_input.text().strip() if hasattr(self, "rp_name_input") else ""
            gender = self.sw_gender.currentRouteKey() if hasattr(self, "sw_gender") else "male"
            pers = self.rp_personality_input.toPlainText().strip() if hasattr(self, "rp_personality_input") else ""
            back = self.rp_backstory_input.toPlainText().strip() if hasattr(self, "rp_backstory_input") else ""
            inv = self.rp_inventory_input.text().strip() if hasattr(self, "rp_inventory_input") else ""
            dna = self.rp_visual_dna_input.toPlainText().strip() if hasattr(self, "rp_visual_dna_input") else ""
            outfit = self.rp_outfit_input.text().strip() if hasattr(self, "rp_outfit_input") else ""

            bio = f"{pers}\n{back}".strip() if (pers or back) else ""

            personas[self.active_persona_id]["name"] = name
            personas[self.active_persona_id]["gender"] = gender or "male"
            personas[self.active_persona_id]["personality"] = pers
            personas[self.active_persona_id]["backstory"] = back
            personas[self.active_persona_id]["bio"] = bio
            personas[self.active_persona_id]["inventory"] = inv
            personas[self.active_persona_id]["visual_dna"] = dna
            personas[self.active_persona_id]["outfit"] = outfit
            personas[self.active_persona_id]["avatar_path"] = self.rp_avatar_temp

    def _set_current_as_primary(self):
        if not self.active_persona_id:
            return
        self._sync_current_persona_fields()
        self.active_primary_persona_id = self.active_persona_id
        rp_settings = self.profiles.setdefault("rp_settings", {})
        rp_settings["active_persona_id"] = self.active_primary_persona_id
        self._load_rp_personas_list()

    def _create_new_persona(self):
        self._sync_current_persona_fields()
        new_id = f"persona_{uuid.uuid4().hex[:8]}"
        personas = self._get_personas_dict()
        personas[new_id] = {
            "name": f"Герой {len(personas) + 1}",
            "gender": "male",
            "personality": "",
            "backstory": "",
            "bio": "",
            "inventory": "",
            "visual_dna": "",
            "outfit": "",
            "avatar_path": None
        }
        self.active_persona_id = new_id
        self.rp_avatar_temp = None
        self._load_rp_personas_list()

    def _delete_current_persona(self):
        personas = self._get_personas_dict()
        if len(personas) <= 1:
            QMessageBox.information(
                self,
                "Удаление персоны",
                "Нельзя удалить единственную персону игрока!\nДолжен оставаться хотя бы один профиль."
            )
            return

        del_id = self.active_persona_id
        if not del_id:
            return

        p_name = personas.get(del_id, {}).get("name", "Персонаж")
        reply = QMessageBox.question(
            self,
            "Подтверждение удаления",
            f"Вы уверены, что хотите удалить персону «{p_name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._is_populating = True

        if del_id in personas:
            del personas[del_id]

        if self.active_primary_persona_id == del_id:
            self.active_primary_persona_id = next(iter(personas.keys()))
            rp_settings = self.profiles.get("rp_settings")
            if isinstance(rp_settings, dict):
                rp_settings["active_persona_id"] = self.active_primary_persona_id

        next_pid = self.active_primary_persona_id
        if next_pid not in personas:
            next_pid = next(iter(personas.keys()))
        self.active_persona_id = next_pid

        self.rp_avatar_temp = None
        self._load_rp_personas_list()

    def _choose_rp_avatar(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Выберите аватар для RP персонажа", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if f:
            try:
                CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
                dest = CHARACTERS_DIR / f"user_rp_{Path(f).name}"
                shutil.copy2(f, dest)
                self.rp_avatar_temp = str(dest)
                self._update_rp_avatar_preview()
            except Exception as e:
                logger.error(f"Ошибка сохранения аватара героя: {e}")

    def _update_rp_avatar_preview(self):
        p = Path(self.rp_avatar_temp) if self.rp_avatar_temp and Path(self.rp_avatar_temp).exists() else None
        name = self.rp_name_input.text().strip() if hasattr(self, "rp_name_input") else "Hero"
        pix = render_circular_avatar(p, name or "Hero", size=64, is_user=True)
        self.rp_avatar_lbl.setPixmap(pix)

    # =========================================================================
    # Валидация и Сохранение данных
    # =========================================================================
    def _validate_inputs(self) -> bool:
        """Валидирует обязательные поля перед сохранением профилей."""
        self._sync_current_persona_fields()

        # Валидация текущей персоны в редакторе
        if self.active_persona_id:
            personas = self._get_personas_dict()
            pdata = personas.get(self.active_persona_id, {})
            p_name = pdata.get("name", "").strip()
            if not p_name:
                QMessageBox.warning(
                    self,
                    "Валидация персоны",
                    "Имя героя не может быть пустым!\nПожалуйста, заполните поле «Имя героя»."
                )
                self.set_current_tab(1)
                self.rp_name_input.setFocus()
                return False

        return True

    def _save_all_profiles(self):
        """Сохраняет настройки профиля ассистента, персоны ролевого чата и параметры в config.json."""
        self.profiles["assistant_user"] = {
            "name": self.asst_name_input.text().strip() or "Пользователь",
            "avatar_path": self.assistant_avatar_temp,
            "bio": self.asst_bio_input.toPlainText().strip()
        }

        self._sync_current_persona_fields()

        rp_s = self.profiles.setdefault("rp_settings", {})
        rp_s["active_persona_id"] = getattr(self, "active_primary_persona_id", None) or self.active_persona_id

        # 1. Сохранение основного файла профилей
        SessionManager.save_user_profiles(self.profiles)

        # 2. Сохранение персон в data/user_personas.json
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            personas_file = DATA_DIR / "user_personas.json"
            atomic_write_json(personas_file, rp_s.get("personas", {}))
        except Exception as e:
            logger.warning(f"Не удалось записать user_personas.json: {e}")

        # 3. Сохранение настроек Эларии в config.json
        cfg = ConfigManager.load_config()
        cfg["user_name"] = self.asst_name_input.text().strip() or "Пользователь"
        is_auto = (self.art_mode_segmented.currentRouteKey() == "auto") if hasattr(self, "art_mode_segmented") else self.rb_art_auto.isChecked()
        cfg["auto_generate_art"] = is_auto
        selected_model = self.combo_asst_model.currentText().strip()
        if selected_model and not selected_model.startswith("default"):
            cfg["active_model"] = selected_model
        ConfigManager.save_config(cfg)

        new_config = ConfigManager.load_config()
        self.settings_applied.emit(new_config)
        self.profiles_updated.emit()

    def _save_all_and_close(self):
        if not self._validate_inputs():
            return
        self._save_all_profiles()
        self.accept()
