"""
Engineer Settings Dialog for NeonRoute.
Allows selecting and persisting a dedicated LLM model for the autonomous Engineer Mode,
with immediate model switching and VRAM reload when Engineer Mode is active.
"""

import logging
from typing import Optional, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QWidget
)

from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.EngineerSettingsDialog")


class EngineerSettingsDialog(QDialog):
    """
    Модальное окно настройки выделенной модели для режима «Инженер» (NeonRoute glassmorphism стиль).
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Настройки: Режим Инженер — NeonRoute")
        self.setFixedSize(500, 240)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 10px;
            }
            QLabel {
                color: #CBD5E1;
                font-size: 12px;
            }
            QLabel#header_title {
                color: #00F0FF;
                font-size: 14px;
                font-weight: bold;
            }
            QLabel#header_sub {
                color: #94A3B8;
                font-size: 11px;
                line-height: 1.4;
            }
            QComboBox {
                background-color: #121824;
                color: #00F0FF;
                border: 1px solid #1E2B42;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                font-weight: 500;
            }
            QComboBox:focus {
                border: 1px solid #00F0FF;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 25px;
                border-left-width: 0px;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QComboBox QAbstractItemView {
                background-color: #121824;
                color: #E2E8F0;
                selection-background-color: #1C273C;
                selection-color: #00F0FF;
                border: 1px solid #1E283D;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 7px 16px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_save {
                background-color: #004D54;
                border: 1px solid #00F0FF;
                color: #FFFFFF;
                font-weight: bold;
            }
            QPushButton#btn_save:hover {
                background-color: #00737D;
            }
            QPushButton#btn_refresh {
                padding: 6px 12px;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        # Заголовок
        self.lbl_title = QLabel("🛠️ НАСТРОЙКИ РЕЖИМА «ИНЖЕНЕР»", self)
        self.lbl_title.setObjectName("header_title")
        layout.addWidget(self.lbl_title)

        # Поясняющий текст
        self.lbl_desc = QLabel(
            "Выберите модель, которая будет гарантированно активироваться при включении режима «Инженер»",
            self
        )
        self.lbl_desc.setObjectName("header_sub")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        # Поле выбора модели и кнопка обновления
        model_row = QHBoxLayout()
        model_row.setSpacing(8)

        self.combo_models = QComboBox(self)
        self.combo_models.setEditable(False)
        model_row.addWidget(self.combo_models, stretch=1)

        self.btn_refresh = QPushButton("🔄", self)
        self.btn_refresh.setObjectName("btn_refresh")
        self.btn_refresh.setToolTip("Обновить список моделей через LM Studio")
        self.btn_refresh.setFixedWidth(40)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        model_row.addWidget(self.btn_refresh)

        layout.addLayout(model_row)

        layout.addStretch()

        # Нижняя панель кнопок: Сохранить и Отмена
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.btn_cancel = QPushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("💾 Сохранить", self)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

        # Начальная загрузка настроек
        self._load_models_and_settings()

    def _get_available_models(self) -> List[str]:
        """Получает список доступных моделей через parent.lm_client.get_models() с fallback на lm_combo."""
        models: List[str] = []
        parent_win = getattr(self, "_parent_ref", None) or self.parent()

        if parent_win and hasattr(parent_win, "lm_client") and parent_win.lm_client:
            try:
                models = parent_win.lm_client.get_models() or []
            except Exception as e:
                logger.warning(f"Ошибка получения моделей от lm_client: {e}")

        # Fallback на варианты из parent.lm_combo, если lm_client пуст
        if not models and parent_win and hasattr(parent_win, "lm_combo"):
            combo = parent_win.lm_combo
            if hasattr(combo, "count"):
                for i in range(combo.count()):
                    txt = combo.itemText(i).strip()
                    if txt and not txt.startswith("default") and txt not in models:
                        models.append(txt)

        return models

    def _load_models_and_settings(self) -> None:
        """Считывает сохранённую модель из config.json и заполняет список."""
        cfg = ConfigManager.load_config()
        saved_model = (cfg.get("engineer_model_name") or cfg.get("engineer_model", "")).strip()

        models = self._get_available_models()

        self.combo_models.blockSignals(True)
        self.combo_models.clear()

        for m in models:
            self.combo_models.addItem(m)

        if saved_model:
            idx = self.combo_models.findText(saved_model)
            if idx >= 0:
                self.combo_models.setCurrentIndex(idx)
            else:
                self.combo_models.addItem(saved_model)
                self.combo_models.setCurrentIndex(self.combo_models.count() - 1)
        elif self.combo_models.count() > 0:
            self.combo_models.setCurrentIndex(0)

        self.combo_models.blockSignals(False)

    def _on_refresh_clicked(self) -> None:
        """Обновляет список моделей при нажатии на 🔄."""
        curr_selected = self.combo_models.currentText().strip()
        models = self._get_available_models()

        self.combo_models.blockSignals(True)
        self.combo_models.clear()

        for m in models:
            self.combo_models.addItem(m)

        if curr_selected:
            idx = self.combo_models.findText(curr_selected)
            if idx >= 0:
                self.combo_models.setCurrentIndex(idx)
            else:
                self.combo_models.addItem(curr_selected)
                self.combo_models.setCurrentIndex(self.combo_models.count() - 1)
        elif self.combo_models.count() > 0:
            self.combo_models.setCurrentIndex(0)

        self.combo_models.blockSignals(False)

    def _on_save_clicked(self) -> None:
        """
        Сохраняет выбранную модель в config.json под ключом 'engineer_model'.
        При активном режиме «Инженер» немедленно переключает модель и производит сброс/загрузку VRAM.
        """
        selected_model = self.combo_models.currentText().strip()

        # 1. Запись в config.json
        cfg = ConfigManager.load_config()
        cfg["engineer_model_name"] = selected_model
        cfg["engineer_model"] = selected_model
        ConfigManager.save_config(cfg)

        parent_win = getattr(self, "_parent_ref", None) or self.parent()
        if parent_win:
            if hasattr(parent_win, "app_config") and isinstance(parent_win.app_config, dict):
                parent_win.app_config["engineer_model_name"] = selected_model
                parent_win.app_config["engineer_model"] = selected_model

            # 2. Проверка активности режима «Инженер»
            is_eng_active = False
            if hasattr(parent_win, "is_engineer_mode_active"):
                try:
                    is_eng_active = bool(parent_win.is_engineer_mode_active())
                except Exception as e:
                    logger.debug(f"is_engineer_mode_active check: {e}")

            if is_eng_active and selected_model:
                # Переключение parent_win.lm_combo
                if hasattr(parent_win, "lm_combo"):
                    idx = parent_win.lm_combo.findText(selected_model)
                    if idx >= 0:
                        parent_win.lm_combo.setCurrentIndex(idx)
                    else:
                        parent_win.lm_combo.addItem(selected_model)
                        parent_win.lm_combo.setCurrentIndex(parent_win.lm_combo.count() - 1)

                # Обновление активной модели окна
                parent_win.active_lms_model = selected_model

                # Обновление model_id текущей сессии
                if getattr(parent_win, "current_session", None):
                    parent_win.current_session["model_id"] = selected_model
                    if hasattr(parent_win, "_save_current_session"):
                        try:
                            parent_win._save_current_session()
                        except Exception as e:
                            logger.warning(f"Ошибка сохранения сессии при смене модели инженера: {e}")



        self.accept()
