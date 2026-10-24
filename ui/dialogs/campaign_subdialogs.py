"""
Auxiliary dialogs for Campaign Info:
EntityConditionDialog, CastEditDialog, SelectLorebookDialog.
Modernized with QFluentWidgets and Glassmorphism styling under Agentrules.md.
"""

import os
import re
import json
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QWidget, QListWidget, QListWidgetItem, QMessageBox,
    QFileDialog
)
from qfluentwidgets import (
    ComboBox,
    LineEdit,
    TextEdit,
    PushButton,
    PrimaryPushButton,
    ToolTipFilter,
    ToolTipPosition,
    FluentIcon as FIF,
)

from config import CHARACTERS_DIR
try:
    from config import LOREBOOKS_DIR
except ImportError:
    LOREBOOKS_DIR = CHARACTERS_DIR.parent / "lorebooks"
LOREBOOKS_DIR.mkdir(parents=True, exist_ok=True)

__all__ = [
    "EntityConditionDialog",
    "CastEditDialog",
    "SelectLorebookDialog",
]


class EntityConditionDialog(QDialog):
    """Editor for Player or Companion condition, mutations, visual DNA and vital status."""
    def __init__(self, name: str, state: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Состояние: {name}")
        self.setFixedSize(470, 390)
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 14px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(QLabel("ЖИЗНЕННЫЙ СТАТУС:"))
        self.combo_vital = ComboBox(self)
        self.combo_vital.addItem("🟢 Жив(а)", userData="alive")
        self.combo_vital.addItem("🟡 Тяжело ранен / При смерти", userData="critical")
        self.combo_vital.addItem("🔴 Погиб(ла) (Мёртв)", userData="dead")
        cur_v = state.get("vital_status", "alive")
        idx = 0 if cur_v == "alive" else (1 if cur_v == "critical" else 2)
        self.combo_vital.setCurrentIndex(idx)
        layout.addWidget(self.combo_vital)

        layout.addWidget(QLabel("ФИЗИЧЕСКИЕ МУТАЦИИ / ШРАМЫ / СЛЕДЫ (ЧЕРЕЗ ЗАПЯТУЮ):"))
        self.muts_input = LineEdit(self)
        muts = state.get("mutations", [])
        self.muts_input.setText(", ".join(muts) if isinstance(muts, list) else str(muts or ""))
        self.muts_input.setPlaceholderText("шрам через левый глаз, ожог на плече, порез")
        layout.addWidget(self.muts_input)

        layout.addWidget(QLabel("ВИЗУАЛЬНАЯ ДНК ДЛЯ АРТА / SD FORGE (АНГЛ.):"))
        self.dna_input = LineEdit(self)
        self.dna_input.setText(state.get("visual_dna", ""))
        self.dna_input.setPlaceholderText("silver hair, violet eyes, athletic build, scar on cheek")
        layout.addWidget(self.dna_input)

        layout.addWidget(QLabel("ТЕКУЩИЙ НАРЯД / ОДЕЖДА (АНГЛ. SD-ТЕГИ):"))
        self.outfit_input = LineEdit(self)
        self.outfit_input.setText(state.get("current_outfit", ""))
        self.outfit_input.setPlaceholderText("heavy plate armor, dark blue tunic, fur cloak")
        layout.addWidget(self.outfit_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = PushButton("Отмена", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_save = PrimaryPushButton("Сохранить", self, icon=FIF.SAVE)
        btn_save.clicked.connect(self.accept)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def get_data(self) -> Dict[str, Any]:
        muts = [m.strip() for m in self.muts_input.text().split(",") if m.strip()]
        return {
            "vital_status": self.combo_vital.currentData(),
            "mutations": muts,
            "visual_dna": self.dna_input.text().strip(),
            "current_outfit": self.outfit_input.text().strip()
        }


class CastEditDialog(QDialog):
    """Editor for NPC details, vital status, presence and facts."""
    def __init__(self, npc_id: str = "", npc: Optional[Dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактирование NPC" if npc else "Новый персонаж (NPC)")
        self.setFixedSize(500, 480)
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 14px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        layout.addWidget(QLabel("ИМЯ ПЕРСОНАЖА (КАНОНИЧЕСКОЕ):"))
        self.name_input = LineEdit(self)
        self.name_input.setText(npc.get("canonical_name", npc_id) if npc else "")
        self.name_input.setPlaceholderText("Имя персонажа...")
        layout.addWidget(self.name_input)

        row_status = QHBoxLayout()
        row_status.setSpacing(12)
        v_pres = QVBoxLayout()
        v_pres.addWidget(QLabel("ПРИСУТСТВИЕ:"))
        self.combo_presence = ComboBox(self)
        self.combo_presence.addItem("⚔️ В сцене (физически рядом)", userData="physical")
        self.combo_presence.addItem("💭 В воспоминаниях / упоминание", userData="memory")
        cur_pres = npc.get("presence", "physical") if npc else "physical"
        self.combo_presence.setCurrentIndex(0 if cur_pres == "physical" else 1)
        v_pres.addWidget(self.combo_presence)
        row_status.addLayout(v_pres)

        v_vital = QVBoxLayout()
        v_vital.addWidget(QLabel("ЖИЗНЕННЫЙ СТАТУС:"))
        self.combo_vital = ComboBox(self)
        self.combo_vital.addItem("🟢 Жив", userData="alive")
        self.combo_vital.addItem("🟡 Тяжело ранен", userData="critical")
        self.combo_vital.addItem("💀 Погиб", userData="dead")
        cur_vital = npc.get("vital_status", "alive") if npc else "alive"
        self.combo_vital.setCurrentIndex(0 if cur_vital == "alive" else (1 if cur_vital == "critical" else 2))
        v_vital.addWidget(self.combo_vital)
        row_status.addLayout(v_vital)
        layout.addLayout(row_status)

        layout.addWidget(QLabel("ТЕГИ ВНЕШНОСТИ ДЛЯ АРТА / SD FORGE (VISUAL DNA):"))
        self.dna_input = LineEdit(self)
        self.dna_input.setText(npc.get("visual_dna", "") if npc else "")
        self.dna_input.setPlaceholderText("old merchant, gray beard, scar on forehead, leather apron")
        layout.addWidget(self.dna_input)

        layout.addWidget(QLabel("ФАКТЫ И ИСТОРИЯ (ПО ОДНОМУ НА СТРОКУ ИЛИ ЧЕРЕЗ ';'):"))
        self.facts_input = TextEdit(self)
        self.facts_input.setFixedHeight(85)
        self.facts_input.setPlaceholderText("Ключевые факты, биография, отношение к игроку...")
        cur_facts = npc.get("facts", []) if npc else []
        self.facts_input.setPlainText("\n".join(cur_facts) if isinstance(cur_facts, list) else str(cur_facts or ""))
        layout.addWidget(self.facts_input)

        layout.addWidget(QLabel("ПСЕВДОНИМЫ / АЛИАСЫ (ЧЕРЕЗ ЗАПЯТУЮ):"))
        self.aliases_input = LineEdit(self)
        aliases = npc.get("aliases", []) if npc else []
        self.aliases_input.setText(", ".join(aliases) if isinstance(aliases, list) else str(aliases or ""))
        self.aliases_input.setPlaceholderText("алиасы, прозвища...")
        layout.addWidget(self.aliases_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = PushButton("Отмена", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_save = PrimaryPushButton("Сохранить", self, icon=FIF.SAVE)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def _on_save(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Ошибка", "Укажите имя персонажа.")
            return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        raw_facts = self.facts_input.toPlainText()
        facts = [f.strip() for line in raw_facts.split("\n") for f in line.split(";") if f.strip()]
        aliases = [a.strip().lower() for a in self.aliases_input.text().split(",") if a.strip()]
        c_name = self.name_input.text().strip()
        if c_name.lower() not in aliases:
            aliases.insert(0, c_name.lower())
        return {
            "canonical_name": c_name,
            "aliases": aliases,
            "presence": self.combo_presence.currentData(),
            "vital_status": self.combo_vital.currentData(),
            "visual_dna": self.dna_input.text().strip(),
            "facts": facts,
            "ttl": 3 if self.combo_presence.currentData() == "physical" else 0
        }


class SelectLorebookDialog(QDialog):
    """Dialog for choosing or importing a standalone world info / lorebook module."""
    def __init__(self, current_lorebook: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор модуля правил / Лорбука")
        self.setFixedSize(540, 480)
        self.selected_lorebook: Optional[str] = current_lorebook
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 14px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }
            QListWidget {
                background-color: rgba(10, 15, 26, 0.75);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 6px;
            }
            QListWidget::item {
                padding: 8px 12px;
                border-radius: 6px;
                margin-bottom: 2px;
                border: 1px solid transparent;
            }
            QListWidget::item:hover {
                background-color: rgba(0, 240, 255, 0.08);
                color: #38BDF8;
            }
            QListWidget::item:selected {
                background-color: rgba(0, 240, 255, 0.18);
                color: #00F0FF;
                font-weight: bold;
                border: 1px solid rgba(0, 240, 255, 0.35);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(QLabel("ДОСТУПНЫЕ МОДУЛИ ПРАВИЛ И ЛОРБУКИ (lorebooks/):"))

        self.list_widget = QListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, 1)

        bar_actions = QHBoxLayout()
        bar_actions.setSpacing(8)
        btn_import = PushButton("Импорт .json", self, icon=FIF.DOWNLOAD)
        btn_import.setToolTip("Импортировать файл лорбука в каталог")
        btn_import.installEventFilter(ToolTipFilter(btn_import, showDelay=300, position=ToolTipPosition.TOP))
        btn_import.clicked.connect(self._import_lorebook)
        bar_actions.addWidget(btn_import)

        btn_folder = PushButton("Папка", self, icon=FIF.FOLDER)
        btn_folder.setToolTip("Открыть системную папку лорбуков")
        btn_folder.installEventFilter(ToolTipFilter(btn_folder, showDelay=300, position=ToolTipPosition.TOP))
        btn_folder.clicked.connect(lambda: os.startfile(str(LOREBOOKS_DIR)) if hasattr(os, "startfile") else None)
        bar_actions.addWidget(btn_folder)

        btn_delete = PushButton("Удалить", self, icon=FIF.DELETE)
        btn_delete.setToolTip("Удалить выбранный файл лорбука с диска")
        btn_delete.installEventFilter(ToolTipFilter(btn_delete, showDelay=300, position=ToolTipPosition.TOP))
        btn_delete.setStyleSheet("PushButton:hover { border-color: #EF4444; color: #EF4444; }")
        btn_delete.clicked.connect(self._delete_lorebook)
        bar_actions.addWidget(btn_delete)

        bar_actions.addStretch()
        layout.addLayout(bar_actions)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = PushButton("Отмена", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_apply = PrimaryPushButton("Выбрать", self, icon=FIF.ACCEPT)
        btn_apply.clicked.connect(self._apply_selection)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

        self._refresh_list()

    def _refresh_list(self):
        self.list_widget.clear()
        
        # Option 0: Disable
        it_none = QListWidgetItem("❌ Без лорбука (отключить модуль)")
        it_none.setData(Qt.ItemDataRole.UserRole, None)
        self.list_widget.addItem(it_none)

        sel_row = 0
        json_files = sorted(LOREBOOKS_DIR.glob("*.json"), key=lambda p: p.name.lower())
        for idx, p in enumerate(json_files, start=1):
            count = 0
            title = p.stem
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                title = data.get("name") or p.stem
                raw_entries = data.get("entries", {})
                count = len(raw_entries) if isinstance(raw_entries, (dict, list)) else 0
            except Exception:
                pass

            is_cur = (self.selected_lorebook == p.name)
            prefix = "✓ " if is_cur else "📚 "
            suffix = f" ({count} статей)" if count else ""
            item = QListWidgetItem(f"{prefix}{title}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, p.name)
            self.list_widget.addItem(item)
            if is_cur:
                sel_row = idx

        self.list_widget.setCurrentRow(sel_row)

    def _import_lorebook(self):
        f, _ = QFileDialog.getOpenFileName(self, "Импорт файла лорбука", "", "JSON Files (*.json)")
        if f:
            src = Path(f)
            dest = LOREBOOKS_DIR / src.name
            try:
                shutil.copy2(src, dest)
                self.selected_lorebook = dest.name
                self._refresh_list()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка импорта", f"Не удалось скопировать файл: {e}")

    def _delete_lorebook(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        lb_filename = item.data(Qt.ItemDataRole.UserRole)
        if not lb_filename:
            return  # Option 0 (None)
        
        reply = QMessageBox.question(
            self,
            "Удаление лорбука",
            f"Вы действительно хотите удалить файл лорбука '{lb_filename}' с диска?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            lb_file = LOREBOOKS_DIR / lb_filename
            try:
                if lb_file.exists():
                    lb_file.unlink(missing_ok=True)
                if self.selected_lorebook == lb_filename:
                    self.selected_lorebook = None
                self._refresh_list()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка удаления", f"Не удалось удалить файл: {e}")

    def _on_item_double_clicked(self, item):
        self._apply_selection()

    def _apply_selection(self):
        item = self.list_widget.currentItem()
        if item:
            self.selected_lorebook = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
