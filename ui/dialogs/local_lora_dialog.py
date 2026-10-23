"""
Local LoRA Picker Dialog for NeonRoute.
Scans SD WebUI Forge models/Lora directory, provides quick filtering,
weight tuning, and trigger tags injection.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QDoubleSpinBox,
    QFileDialog, QMessageBox, QFrame
)

from config import DATA_DIR, BASE_DIR, FORGE_LORA_DIRS, DEFAULT_FORGE_LORA_DIR, LORA_REGISTRY_PATH

logger = logging.getLogger("NeonRoute.LocalLoraDialog")


def get_forge_lora_dirs() -> List[Path]:
    """Возвращает список существующих путей поиска моделей LoRA в Forge."""
    existing = [p.resolve() for p in FORGE_LORA_DIRS if p.exists() and p.is_dir()]
    if not existing:
        DEFAULT_FORGE_LORA_DIR.mkdir(parents=True, exist_ok=True)
        existing.append(DEFAULT_FORGE_LORA_DIR.resolve())
    return existing


class LocalLoraDialog(QDialog):
    def __init__(self, character_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор локальной LoRA — NeonRoute")
        self.setModal(True)
        self.resize(580, 480)
        self.character_name = character_name.strip()
        self.selected_lora_stem: Optional[str] = None
        self.selected_triggers: List[str] = []

        self._lora_registry = self._load_registry()
        self._all_lora_files: List[Path] = []

        self._setup_ui()
        self._scan_loras()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 10px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: bold;
            }
            QLineEdit, QDoubleSpinBox {
                background-color: #0A0D14;
                color: #F8FAFC;
                border: 1px solid #1E2B42;
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QDoubleSpinBox:focus {
                border: 1px solid #00F0FF;
            }
            QListWidget {
                background-color: #0A0D15;
                border: 1px solid #1E283D;
                border-radius: 6px;
                padding: 4px;
                color: #E2E8F0;
            }
            QListWidget::item {
                background-color: #121826;
                border: 1px solid #1E293B;
                border-radius: 6px;
                padding: 8px 10px;
                margin-bottom: 4px;
            }
            QListWidget::item:hover {
                border-color: #00F0FF;
                background-color: #182236;
            }
            QListWidget::item:selected {
                background-color: #16253D;
                border: 1px solid #00F0FF;
                color: #00F0FF;
                font-weight: bold;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_primary {
                background-color: #004D54;
                border: 1px solid #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_primary:hover {
                background-color: #00737D;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header
        hdr = QLabel(f"📁 ЛОКАЛЬНЫЕ LoRA ФАЙЛЫ (SD WebUI Forge)")
        hdr.setStyleSheet("color: #00F0FF; font-size: 14px; font-weight: bold;")
        layout.addWidget(hdr)

        # Search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Быстрый поиск по имени файла...")
        self.search_input.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_input)

        # List
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget, stretch=1)

        # Triggers Preview
        self.lbl_triggers = QLabel("Активационные слова: (не заданы)")
        self.lbl_triggers.setStyleSheet("color: #38BDF8; font-size: 11px;")
        layout.addWidget(self.lbl_triggers)

        # Weight Controls & Custom File
        row_controls = QHBoxLayout()
        row_controls.addWidget(QLabel("Сила LoRA (вес):"))

        self.spin_weight = QDoubleSpinBox()
        self.spin_weight.setRange(0.10, 1.50)
        self.spin_weight.setSingleStep(0.05)
        self.spin_weight.setValue(0.85)
        row_controls.addWidget(self.spin_weight)

        btn_browse = QPushButton("📂 Выбрать другой файл...")
        btn_browse.clicked.connect(self._browse_custom_file)
        row_controls.addWidget(btn_browse)

        row_controls.addStretch()
        layout.addLayout(row_controls)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_apply = QPushButton("Привязать LoRA")
        self.btn_apply.setObjectName("btn_primary")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_apply)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        layout.addLayout(btn_box)

    def _load_registry(self) -> Dict[str, Any]:
        reg_path = LORA_REGISTRY_PATH
        if reg_path.exists():
            try:
                with open(reg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Failed to read lora_registry: {e}")
        return {}

    def _scan_loras(self):
        self._all_lora_files.clear()
        dirs = get_forge_lora_dirs()
        for d in dirs:
            for f in d.rglob("*.safetensors"):
                if f.is_file() and f not in self._all_lora_files:
                    self._all_lora_files.append(f)

        # Сортировка: подходящие под имя персонажа — наверх
        name_clean = "".join(c for c in self.character_name.lower() if c.isalnum())
        def _sort_key(p: Path):
            stem = p.stem.lower()
            is_match = bool(name_clean and name_clean in "".join(c for c in stem if c.isalnum()))
            return (0 if is_match else 1, stem)

        self._all_lora_files.sort(key=_sort_key)
        self._filter_list(self.search_input.text())

    def _filter_list(self, query: str):
        self.list_widget.clear()
        q = query.strip().lower()
        name_clean = "".join(c for c in self.character_name.lower() if c.isalnum())

        for p in self._all_lora_files:
            stem = p.stem
            if q and q not in stem.lower():
                continue

            size_mb = p.stat().st_size / (1024 * 1024)
            is_match = bool(name_clean and name_clean in "".join(c for c in stem.lower() if c.isalnum()))

            prefix = "⭐ [Рекомендуется] " if is_match else "📦 "
            item = QListWidgetItem(f"{prefix}{stem}  ({size_mb:.1f} MB)")
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            if is_match:
                item.setForeground(Qt.GlobalColor.cyan)
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _on_item_changed(self, current: Optional[QListWidgetItem], previous=None):
        if not current:
            self.btn_apply.setEnabled(False)
            self.lbl_triggers.setText("Активационные слова: (не заданы)")
            return

        p_str = current.data(Qt.ItemDataRole.UserRole)
        p = Path(p_str)
        self.selected_lora_stem = p.stem
        self.btn_apply.setEnabled(True)

        # Поиск триггеров в реестре
        reg_info = self._lora_registry.get(p.stem, {})
        triggers = reg_info.get("trained_words", []) or reg_info.get("activation_tags", [])
        self.selected_triggers = list(triggers)

        if triggers:
            self.lbl_triggers.setText(f"Активационные слова: {', '.join(triggers[:5])}")
        else:
            self.lbl_triggers.setText("Активационные слова: (в реестре не найдены)")

    def _on_item_double_clicked(self, item: QListWidgetItem):
        self._on_item_changed(item)
        if self.selected_lora_stem:
            self.accept()

    def _browse_custom_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл LoRA", "", "LoRA (*.safetensors *.pt *.ckpt)"
        )
        if not file_path:
            return
        p = Path(file_path)
        self.selected_lora_stem = p.stem
        self.selected_triggers = []
        self.accept()

    def get_result(self) -> Tuple[str, List[str]]:
        """Возвращает строку инъекции тега LoRA и список триггеров."""
        stem = self.selected_lora_stem or ""
        weight = self.spin_weight.value()
        lora_tag = f"<lora:{stem}:{weight:.2f}>"
        return lora_tag, self.selected_triggers
