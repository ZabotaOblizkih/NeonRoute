"""
UserSettingsDialog for NeonRoute.
Provides user identity configuration for Elaria Assistant mode and RPG Player Personas Manager.
Refactored strictly according to Section 9 of neonroute_ui_spec.md and Agentrules.md:
- Glassmorphism dark aesthetic: background rgba(10, 13, 20, 0.95), border-radius 14px, thin border rgba(255, 255, 255, 0.08)
- Tab navigation using QFluentWidgets SegmentedWidget:
  • Tab 1: «⚡ Ассистент»
  • Tab 2: «🎭 Ролевой чат» (Player Personas Manager)
- Tab 1: Circular interactive user avatar with preview and file selection, LineEdit for user name,
  matte TextEdit for bio context (<UserInfo>), ComboBox for LM Studio model, SegmentedWidget for art generation mode.
- Tab 2: Two-panel persona manager:
  • Left panel: Persona list with [➕ Создать], [★ Сделать основным], [🗑️ Удалить]
  • Right panel: Detailed persona card (avatar, hero name, grammatical gender switch, traits, backstory,
    starting inventory, Visual DNA, SD outfit)
- Action bar: [ 💾 Сохранить изменения ] (#00F0FF / Neon Cyan) and [ ✕ Отмена ]
- Persistence to data/user_profiles.json, data/user_personas.json, and config.json
"""

import uuid
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QRect
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QPen, QPainterPath
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QStackedWidget, QListWidget, QListWidgetItem,
    QScrollArea, QFileDialog, QMessageBox, QFrame, QSizePolicy
)

from qfluentwidgets import (
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
from utils.config_manager import ConfigManager
from utils.file_utils import atomic_write_json
from ui.components.chat_bubble import render_circular_avatar

logger = logging.getLogger("NeonRoute.UserSettingsDialog")


class InteractiveAvatarWidget(QWidget):
    """
    Интерактивный круглый аватар со сглаживанием, подсветкой при наведении
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
        self.setToolTip("Нажмите, чтобы выбрать новое изображение")

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

        rect_f = self.rect().adjusted(1, 1, -1, -1)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.avatar_size, self.avatar_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            # Center in widget
            dx = (self.width() - scaled.width()) // 2
            dy = (self.height() - scaled.height()) // 2
            painter.drawPixmap(dx, dy, scaled)
        else:
            painter.setBrush(QColor(15, 23, 42))
            painter.setPen(QPen(QColor(0, 240, 255, 60), 1.0))
            painter.drawEllipse(rect_f)

        # Soft ring / glow border
        if self._is_hovered:
            painter.setBrush(QColor(0, 240, 255, 25))
            painter.setPen(QPen(QColor(0, 240, 255, 220), 2.0))
            painter.drawEllipse(rect_f)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 30), 1.2))
            painter.drawEllipse(rect_f)


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


class UserSettingsDialog(QDialog):
    profiles_updated = pyqtSignal()
    settings_applied = pyqtSignal(dict)

    def __init__(self, initial_tab: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Личные настройки профиля — NeonRoute")
        self.resize(820, 700)
        self.setMinimumSize(780, 640)
        self.setModal(True)

        self._apply_glassmorphism_styles()

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

    def _apply_glassmorphism_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(10, 13, 20, 0.95);
                color: #CBD5E1;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }
            QLabel.section_title {
                color: #00F0FF;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.5px;
            }
            QLabel.field_label {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel.field_label_accent {
                color: #00F0FF;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel.hint_sub {
                color: #64748B;
                font-size: 11px;
                font-weight: normal;
            }
            QFrame.glass_card {
                background-color: rgba(14, 19, 31, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }
            LineEdit {
                background-color: rgba(14, 19, 31, 0.85);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            LineEdit:focus {
                border: 1px solid #00F0FF;
                background-color: rgba(16, 24, 40, 0.95);
            }
            TextEdit {
                background-color: rgba(8, 12, 20, 0.92);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }
            TextEdit:focus {
                border: 1px solid #00F0FF;
                background-color: rgba(10, 15, 26, 0.96);
            }
            ComboBox {
                background-color: rgba(14, 19, 31, 0.85);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
            }
            ComboBox:focus {
                border: 1px solid #00F0FF;
            }
            QListWidget {
                background-color: rgba(8, 12, 20, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 4px;
            }
            QListWidget::item {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 6px;
                padding: 8px 10px;
                margin-bottom: 4px;
                color: #CBD5E1;
                font-weight: 500;
            }
            QListWidget::item:hover {
                background-color: rgba(0, 240, 255, 0.08);
                border-color: rgba(0, 240, 255, 0.3);
            }
            QListWidget::item:selected {
                background-color: rgba(0, 240, 255, 0.16);
                border: 1px solid #00F0FF;
                color: #00F0FF;
                font-weight: bold;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            PrimaryPushButton {
                background-color: #00F0FF;
                color: #0A0D14;
                font-weight: bold;
                font-size: 12px;
                border: 1px solid #00F0FF;
                border-radius: 6px;
                padding: 7px 18px;
            }
            PrimaryPushButton:hover {
                background-color: #38BDF8;
                border-color: #38BDF8;
                color: #0A0D14;
            }
            PrimaryPushButton:pressed {
                background-color: #00B4D8;
                border-color: #00B4D8;
                color: #000000;
            }
        """)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # 1. Top Navigation: SegmentedWidget
        self.nav_segmented = SegmentedWidget(self)
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
        root.addWidget(self.nav_segmented)

        # 2. Content: QStackedWidget
        self.stacked_widget = QStackedWidget(self)
        self.tab_asst_widget = self._create_assistant_tab()
        self.tab_rp_widget = self._create_rp_tab()

        self.stacked_widget.addWidget(self.tab_asst_widget)
        self.stacked_widget.addWidget(self.tab_rp_widget)
        root.addWidget(self.stacked_widget, 1)

        # 3. Bottom Action Bar
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_box.addStretch()

        self.btn_cancel = TransparentPushButton("✕ Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save_all = PrimaryPushButton("💾 Сохранить изменения", self)
        self.btn_save_all.setObjectName("btn_save_all")
        self.btn_save_all.clicked.connect(self._save_all_and_close)
        btn_box.addWidget(self.btn_save_all)

        root.addLayout(btn_box)

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

        parent_window = self.parent()
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
                color: #38BDF8;
                background-color: rgba(6, 10, 18, 0.95);
                border: 1px solid rgba(0, 240, 255, 0.25);
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
                color: #38BDF8;
                background-color: rgba(6, 10, 18, 0.95);
                border: 1px solid rgba(0, 240, 255, 0.25);
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
        if not getattr(self, "active_persona_id", None):
            return
        self.active_primary_persona_id = self.active_persona_id
        personas = self._get_personas_dict()
        rp_settings = self.profiles.get("rp_settings")
        if isinstance(rp_settings, dict):
            rp_settings["active_persona_id"] = self.active_primary_persona_id

        for i in range(self.list_personas.count()):
            it = self.list_personas.item(i)
            if it:
                pid = it.data(Qt.ItemDataRole.UserRole)
                name = (personas.get(pid) or {}).get("name", "Персонаж")
                is_pri = (pid == self.active_primary_persona_id)
                it.setText(f"★ {name} (Основной)" if is_pri else f"👤 {name}")
                if is_pri:
                    it.setForeground(QColor("#00F0FF"))
                else:
                    it.setForeground(QColor("#CBD5E1"))

    def _create_new_persona(self):
        self._sync_current_persona_fields()
        new_id = f"persona_{uuid.uuid4().hex[:6]}"
        personas = self._get_personas_dict()
        personas[new_id] = {
            "name": f"Персонаж {len(personas) + 1}",
            "gender": "male",
            "avatar_path": None,
            "bio": "",
            "personality": "",
            "backstory": "",
            "inventory": "",
            "visual_dna": "",
            "outfit": ""
        }
        self.active_persona_id = new_id
        self._load_rp_personas_list()
        self.rp_name_input.setFocus()
        self.rp_name_input.selectAll()

    def _delete_current_persona(self):
        if not getattr(self, "active_persona_id", None):
            return
        personas = self._get_personas_dict()
        if len(personas) <= 1:
            QMessageBox.information(self, "Внимание", "Нельзя удалить единственного персонажа.")
            return

        del_id = self.active_persona_id
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
