"""
User Settings & Personas Dialog for NeonRoute.
Allows configuring user identity for Assistant mode and multiple RP Personas.
"""

import uuid
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QComboBox, QRadioButton, QButtonGroup,
    QTabWidget, QWidget, QFileDialog, QMessageBox, QSizePolicy,
    QListWidget, QListWidgetItem, QScrollArea
)

from config import CHARACTERS_DIR, DEFAULT_AUTO_GENERATE_ART
from services.session_manager import SessionManager
from utils.config_manager import ConfigManager
from ui.components.chat_bubble import render_circular_avatar

logger = logging.getLogger("NeonRoute.UserSettingsDialog")


class UserSettingsDialog(QDialog):
    profiles_updated = pyqtSignal()
    settings_applied = pyqtSignal(dict)

    def __init__(self, initial_tab: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Личные настройки профиля")
        self.setFixedSize(580, 720)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 10px;
            }
            QTabWidget::pane {
                border: 1px solid #1E273A;
                background-color: #121824;
                border-radius: 8px;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #0F1420;
                color: #94A3B8;
                padding: 8px 18px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: bold;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #121824;
                color: #00F0FF;
                border: 1px solid #1E273A;
                border-bottom: 1px solid #121824;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: bold;
            }
            QLineEdit, QTextEdit {
                background-color: #0A0D14;
                color: #F8FAFC;
                border: 1px solid #1E2B42;
                border-radius: 6px;
                padding: 6px;
                font-size: 12px;
            }
            QLineEdit:focus, QTextEdit:focus {
                border-color: #00F0FF;
            }
            QComboBox {
                background-color: #0A0D14;
                color: #F8FAFC;
                border: 1px solid #1E2B42;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QListWidget {
                background-color: #0A0D14;
                border: 1px solid #1E2B42;
                border-radius: 6px;
                color: #CBD5E1;
            }
            QListWidget::item {
                padding: 6px 10px;
                border-bottom: 1px solid #141C2B;
            }
            QListWidget::item:selected {
                background-color: #1C263B;
                color: #00F0FF;
            }
            QRadioButton {
                color: #CBD5E1;
                font-size: 12px;
                spacing: 6px;
            }
            QRadioButton::indicator:checked {
                background-color: #00F0FF;
                border: 2px solid #FFFFFF;
                border-radius: 6px;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_save_all {
                background-color: #004D54;
                border: 1px solid #00F0FF;
                color: #FFFFFF;
                font-weight: bold;
            }
            QPushButton#btn_save_all:hover {
                background-color: #00737D;
            }
        """)

        loaded_profiles = SessionManager.load_user_profiles()
        self.profiles: Dict[str, Any] = loaded_profiles if isinstance(loaded_profiles, dict) else {}

        asst_data = self.profiles.get("assistant_user", {})
        self.assistant_avatar_temp: Optional[str] = asst_data.get("avatar_path") if isinstance(asst_data, dict) else None
        self.rp_avatar_temp: Optional[str] = None
        self.active_persona_id: Optional[str] = None
        self.active_primary_persona_id: Optional[str] = None
        self._is_populating: bool = False

        self._setup_ui()
        self.tab_widget.setCurrentIndex(initial_tab)
        self._load_assistant_fields()
        self._load_rp_personas_list()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.addTab(self._create_assistant_tab(), "⚡ Ассистент")
        self.tab_widget.addTab(self._create_rp_tab(), "🎭 Ролевой чат")
        root.addWidget(self.tab_widget)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_cancel = QPushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save_all = QPushButton("💾 Сохранить всё", self)
        self.btn_save_all.setObjectName("btn_save_all")
        self.btn_save_all.clicked.connect(self._save_all_and_close)
        btn_box.addWidget(self.btn_save_all)

        root.addLayout(btn_box)

    def _create_assistant_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        self.asst_avatar_lbl = QLabel(widget)
        self.asst_avatar_lbl.setFixedSize(52, 52)
        top_row.addWidget(self.asst_avatar_lbl)

        btn_ch_avatar = QPushButton("🖼️ Выбрать аватар...", widget)
        btn_ch_avatar.clicked.connect(self._choose_assistant_avatar)
        top_row.addWidget(btn_ch_avatar)
        top_row.addStretch()
        layout.addLayout(top_row)

        layout.addWidget(QLabel("ИМЯ / ОБРАЩЕНИЕ К ВАМ:"))
        self.asst_name_input = QLineEdit(widget)
        self.asst_name_input.setPlaceholderText("Например: Пользователь, Шеф, Алексей")
        layout.addWidget(self.asst_name_input)

        layout.addWidget(QLabel("КОНТЕКСТ О ВАС (ПОМНИТЬ ВСЕГДА):"))
        self.asst_bio_input = QTextEdit(widget)
        self.asst_bio_input.setPlaceholderText("Краткая выжимка о вас. Например:\nРазработчик. Предпочитаю лаконичные ответы.")
        layout.addWidget(self.asst_bio_input)

        layout.addWidget(QLabel("МОДЕЛЬ ДЛЯ ЭЛАРИИ (LM STUDIO):"))
        self.combo_asst_model = QComboBox(widget)
        layout.addWidget(self.combo_asst_model)

        layout.addWidget(QLabel("ГЕНЕРАЦИЯ ИЛЛЮСТРАЦИЙ ЭЛАРИЕЙ:"))
        self.bg_art_mode = QButtonGroup(widget)
        self.rb_art_manual = QRadioButton("Вручную (кнопка подтверждения под сообщением)", widget)
        self.rb_art_auto = QRadioButton("Автоматически (сразу запускать SD Forge)", widget)
        self.bg_art_mode.addButton(self.rb_art_manual, 0)
        self.bg_art_mode.addButton(self.rb_art_auto, 1)
        self.rb_art_manual.setChecked(True)
        layout.addWidget(self.rb_art_manual)
        layout.addWidget(self.rb_art_auto)

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
            self.rb_art_auto.setChecked(True)
        else:
            self.rb_art_manual.setChecked(True)

        self.combo_asst_model.blockSignals(True)
        self.combo_asst_model.clear()

        parent_window = self.parent()
        parent_combo = getattr(parent_window, "lm_combo", None) if parent_window else None
        active_model = cfg.get("active_model", "")

        if parent_combo and hasattr(parent_combo, "count") and parent_combo.count() > 0:
            for i in range(parent_combo.count()):
                m_text = parent_combo.itemText(i).strip()
                if m_text and self.combo_asst_model.findText(m_text) == -1:
                    self.combo_asst_model.addItem(m_text)
            curr_parent_model = parent_combo.currentText().strip()
            if curr_parent_model:
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
        f, _ = QFileDialog.getOpenFileName(self, "Выберите аватар для Ассистента", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            dest = CHARACTERS_DIR / f"user_asst_{Path(f).name}"
            shutil.copy2(f, dest)
            self.assistant_avatar_temp = str(dest)
            self._update_assistant_avatar_preview()

    def _update_assistant_avatar_preview(self):
        p = Path(self.assistant_avatar_temp) if self.assistant_avatar_temp and Path(self.assistant_avatar_temp).exists() else None
        name = self.asst_name_input.text().strip() if hasattr(self, "asst_name_input") else "User"
        pix = render_circular_avatar(p, name or "User", size=52, is_user=True)
        self.asst_avatar_lbl.setPixmap(pix)

    def _create_rp_tab(self) -> QWidget:
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Personas selector list at top
        main_layout.addWidget(QLabel("ВАШИ ПЕРСОНАЖИ (ИГРОКИ ДЛЯ РП ЧАТА):"))
        list_row = QHBoxLayout()
        list_row.setSpacing(8)

        self.list_personas = QListWidget(widget)
        self.list_personas.setFixedHeight(95)
        self.list_personas.currentRowChanged.connect(self._on_persona_row_changed)
        list_row.addWidget(self.list_personas, stretch=1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        self.btn_new_persona = QPushButton("➕ Создать", widget)
        self.btn_new_persona.clicked.connect(self._create_new_persona)
        btn_col.addWidget(self.btn_new_persona)

        self.btn_set_primary = QPushButton("★ Основной", widget)
        self.btn_set_primary.clicked.connect(self._set_current_as_primary)
        btn_col.addWidget(self.btn_set_primary)

        self.btn_del_persona = QPushButton("🗑️ Удалить", widget)
        self.btn_del_persona.clicked.connect(self._delete_current_persona)
        btn_col.addWidget(self.btn_del_persona)

        list_row.addLayout(btn_col)
        main_layout.addLayout(list_row)

        # Scrollable form for detailed persona traits
        scroll = QScrollArea(widget)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #1E283D; background: transparent; border-radius: 6px; }")

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(8)

        # Avatar, Name and Gender row
        id_row = QHBoxLayout()
        id_row.setSpacing(10)
        self.rp_avatar_lbl = QLabel(scroll_content)
        self.rp_avatar_lbl.setFixedSize(50, 50)
        id_row.addWidget(self.rp_avatar_lbl)

        btn_ch_rp_avatar = QPushButton("🖼️ Аватар...", scroll_content)
        btn_ch_rp_avatar.clicked.connect(self._choose_rp_avatar)
        id_row.addWidget(btn_ch_rp_avatar)

        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        name_box.addWidget(QLabel("ИМЯ ПЕРСОНАЖА:"))
        self.rp_name_input = QLineEdit(scroll_content)
        self.rp_name_input.setPlaceholderText("Например: Дэниэл")
        self.rp_name_input.textChanged.connect(self._on_rp_name_changed)
        name_box.addWidget(self.rp_name_input)
        id_row.addLayout(name_box, stretch=1)

        gender_box = QVBoxLayout()
        gender_box.setSpacing(2)
        gender_box.addWidget(QLabel("ПОЛ:"))
        self.combo_rp_gender = QComboBox(scroll_content)
        self.combo_rp_gender.addItem("Мужской (he/him)")
        self.combo_rp_gender.addItem("Женский (she/her)")
        gender_box.addWidget(self.combo_rp_gender)
        id_row.addLayout(gender_box)

        scroll_layout.addLayout(id_row)

        # Personality & Backstory for LLM
        scroll_layout.addWidget(QLabel("ХАРАКТЕР И ПОВАДКИ:"))
        self.rp_personality_input = QTextEdit(scroll_content)
        self.rp_personality_input.setMaximumHeight(65)
        self.rp_personality_input.setPlaceholderText("Например: Хладнокровный, наблюдательный археолог. В стрессе говорит коротко.")
        scroll_layout.addWidget(self.rp_personality_input)

        scroll_layout.addWidget(QLabel("ПРЕДЫСТОРИЯ И ПРОИСХОЖДЕНИЕ:"))
        self.rp_backstory_input = QTextEdit(scroll_content)
        self.rp_backstory_input.setMaximumHeight(75)
        self.rp_backstory_input.setPlaceholderText("Например: Потомственный исследователь гробниц. Изучал древнеегипетские культы.")
        scroll_layout.addWidget(self.rp_backstory_input)

        scroll_layout.addWidget(QLabel("СТАРТОВЫЙ ИНВЕНТАРЬ (ЧЕРЕЗ ЗАПЯТУЮ):"))
        self.rp_inventory_input = QLineEdit(scroll_content)
        self.rp_inventory_input.setPlaceholderText("Например: компас, блокнот с записями, фонарь, альпинистский трос")
        scroll_layout.addWidget(self.rp_inventory_input)

        # SD Appearance & Outfit (Monospace style)
        dna_lbl = QLabel("🎨 ВНЕШНОСТЬ ДЛЯ АРТОВ (SD VISUAL DNA):")
        dna_lbl.setStyleSheet("color: #00F0FF; font-weight: bold;")
        scroll_layout.addWidget(dna_lbl)

        self.rp_visual_dna_input = QTextEdit(scroll_content)
        self.rp_visual_dna_input.setMaximumHeight(65)
        self.rp_visual_dna_input.setPlaceholderText("Английские теги: 1man, 28yo, athletic lean build, dark brown hair, stubble, hazel eyes")
        self.rp_visual_dna_input.setStyleSheet("border: 1px solid #1E3A5F; background-color: #080D18; color: #38BDF8; font-family: 'Consolas', monospace; font-size: 11px;")
        scroll_layout.addWidget(self.rp_visual_dna_input)

        outfit_lbl = QLabel("👗 СТАРТОВАЯ ОДЕЖДА ДЛЯ АРТОВ (SD OUTFIT):")
        outfit_lbl.setStyleSheet("color: #00F0FF; font-weight: bold;")
        scroll_layout.addWidget(outfit_lbl)

        self.rp_outfit_input = QLineEdit(scroll_content)
        self.rp_outfit_input.setPlaceholderText("Английские теги: linen explorer shirt, cargo pants, leather belt, combat boots")
        self.rp_outfit_input.setStyleSheet("border: 1px solid #1E3A5F; background-color: #080D18; color: #38BDF8; font-family: 'Consolas', monospace; font-size: 11px;")
        scroll_layout.addWidget(self.rp_outfit_input)

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, stretch=1)
        return widget

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
            personas["persona_default"] = {"name": "Игрок", "avatar_path": None, "bio": ""}
            rp_settings["active_persona_id"] = "persona_default"

        active_id = rp_settings.get("active_persona_id")
        if active_id not in personas:
            active_id = next(iter(personas.keys()))
            rp_settings["active_persona_id"] = active_id

        self.active_primary_persona_id = active_id
        self.active_persona_id = active_id

        self.list_personas.blockSignals(True)
        self.list_personas.clear()
        selected_row = 0
        for idx, (pid, pdata) in enumerate(personas.items()):
            p_name = pdata.get("name", "Персонаж")
            label = f"👤 {p_name} ★ [Основной]" if pid == self.active_primary_persona_id else f"👤 {p_name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.list_personas.addItem(item)
            if pid == active_id:
                selected_row = idx

        self.list_personas.setCurrentRow(selected_row)
        self.list_personas.blockSignals(False)

        self._populate_persona_fields(active_id)
        self._is_populating = False

    def _populate_persona_fields(self, pid: str):
        personas = (self.profiles.get("rp_settings") or {}).get("personas") or {}
        pdata = personas.get(pid, {}) if isinstance(personas, dict) else {}

        self.rp_name_input.blockSignals(True)
        self.rp_name_input.setText(pdata.get("name", ""))
        self.rp_name_input.blockSignals(False)

        gender = str(pdata.get("gender", "male")).lower()
        self.combo_rp_gender.blockSignals(True)
        self.combo_rp_gender.setCurrentIndex(1 if gender == "female" else 0)
        self.combo_rp_gender.blockSignals(False)

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
                    item.setText(f"👤 {display} ★ [Основной]" if is_pri else f"👤 {display}")
            self._update_rp_avatar_preview()

    def _sync_current_persona_fields(self):
        if self._is_populating or not getattr(self, "active_persona_id", None):
            return
        personas = self._get_personas_dict()
        if self.active_persona_id in personas:
            name = self.rp_name_input.text().strip() if hasattr(self, "rp_name_input") else ""
            gender = "female" if (hasattr(self, "combo_rp_gender") and self.combo_rp_gender.currentIndex() == 1) else "male"
            pers = self.rp_personality_input.toPlainText().strip() if hasattr(self, "rp_personality_input") else ""
            back = self.rp_backstory_input.toPlainText().strip() if hasattr(self, "rp_backstory_input") else ""
            inv = self.rp_inventory_input.text().strip() if hasattr(self, "rp_inventory_input") else ""
            dna = self.rp_visual_dna_input.toPlainText().strip() if hasattr(self, "rp_visual_dna_input") else ""
            outfit = self.rp_outfit_input.text().strip() if hasattr(self, "rp_outfit_input") else ""

            bio = f"{pers}\n{back}".strip() if (pers or back) else ""

            personas[self.active_persona_id]["name"] = name or "Персонаж"
            personas[self.active_persona_id]["gender"] = gender
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
                it.setText(f"👤 {name} ★ [Основной]" if is_pri else f"👤 {name}")

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

        for widget in (self.rp_name_input, self.rp_personality_input, self.rp_backstory_input, self.rp_inventory_input, self.rp_visual_dna_input, self.rp_outfit_input):
            widget.blockSignals(True)
            widget.clear()
            widget.blockSignals(False)

        self.rp_avatar_temp = None
        self._load_rp_personas_list()

    def _choose_rp_avatar(self):
        f, _ = QFileDialog.getOpenFileName(self, "Выберите аватар для RP персонажа", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            dest = CHARACTERS_DIR / f"user_rp_{Path(f).name}"
            shutil.copy2(f, dest)
            self.rp_avatar_temp = str(dest)
            self._update_rp_avatar_preview()

    def _update_rp_avatar_preview(self):
        p = Path(self.rp_avatar_temp) if self.rp_avatar_temp and Path(self.rp_avatar_temp).exists() else None
        name = self.rp_name_input.text().strip() if hasattr(self, "rp_name_input") else "Hero"
        pix = render_circular_avatar(p, name or "Hero", size=52, is_user=True)
        self.rp_avatar_lbl.setPixmap(pix)

    def _save_all_profiles(self):
        self.profiles["assistant_user"] = {
            "name": self.asst_name_input.text().strip() or "Пользователь",
            "avatar_path": self.assistant_avatar_temp,
            "bio": self.asst_bio_input.toPlainText().strip()
        }

        self._sync_current_persona_fields()

        rp_s = self.profiles.setdefault("rp_settings", {})
        rp_s["active_persona_id"] = getattr(self, "active_primary_persona_id", None) or self.active_persona_id

        SessionManager.save_user_profiles(self.profiles)

        cfg = ConfigManager.load_config()
        cfg["user_name"] = self.asst_name_input.text().strip() or "Пользователь"
        cfg["auto_generate_art"] = self.rb_art_auto.isChecked()
        selected_model = self.combo_asst_model.currentText().strip()
        if selected_model:
            cfg["active_model"] = selected_model
        ConfigManager.save_config(cfg)

        new_config = ConfigManager.load_config()
        self.settings_applied.emit(new_config)
        self.profiles_updated.emit()

    def _save_all_and_close(self):
        self._save_all_profiles()
        self.accept()