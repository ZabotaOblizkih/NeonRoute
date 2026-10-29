"""
Mode & Model Settings Dialog for NeonRoute.
Allows selecting and persisting dedicated LLM models for Assistant, Roleplay,
and autonomous Engineer Mode, with background hot-swap capabilities.
"""

import logging
from typing import Optional, List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QWidget
)

from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.ModeModelSettingsDialog")


class EngineerSettingsDialog(QDialog):
    """
    Универсальное модальное окно выбора моделей по режимам (NeonRoute glassmorphism стиль).
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Выбор моделей по режимам — NeonRoute")
        self.setFixedSize(540, 360)
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
            QLabel.row_label {
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 600;
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
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        # Заголовок
        self.lbl_title = QLabel("🎯 ВЫБОР МОДЕЛЕЙ ДЛЯ РЕЖИМОВ", self)
        self.lbl_title.setObjectName("header_title")
        layout.addWidget(self.lbl_title)

        # Подзаголовок
        self.lbl_desc = QLabel(
            "Назначьте постоянные модели для каждого рабочего контура",
            self
        )
        self.lbl_desc.setObjectName("header_sub")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        layout.addSpacing(4)

        # Селектор 1: Ассистент
        row_asst = QHBoxLayout()
        row_asst.setSpacing(10)
        lbl_asst = QLabel("💬 Ассистент:", self)
        lbl_asst.setFixedWidth(120)
        lbl_asst.setProperty("class", "row_label")
        self.combo_assistant = QComboBox(self)
        self.combo_assistant.setEditable(False)
        row_asst.addWidget(lbl_asst)
        row_asst.addWidget(self.combo_assistant, stretch=1)
        layout.addLayout(row_asst)

        # Селектор 2: РП Чат
        row_rp = QHBoxLayout()
        row_rp.setSpacing(10)
        lbl_rp = QLabel("🎭 РП Чат:", self)
        lbl_rp.setFixedWidth(120)
        lbl_rp.setProperty("class", "row_label")
        self.combo_rp = QComboBox(self)
        self.combo_rp.setEditable(False)
        row_rp.addWidget(lbl_rp)
        row_rp.addWidget(self.combo_rp, stretch=1)
        layout.addLayout(row_rp)

        # Селектор 3: Инженер
        row_eng = QHBoxLayout()
        row_eng.setSpacing(10)
        lbl_eng = QLabel("🛠️ Инженер:", self)
        lbl_eng.setFixedWidth(120)
        lbl_eng.setProperty("class", "row_label")
        self.combo_engineer = QComboBox(self)
        self.combo_engineer.setEditable(False)
        row_eng.addWidget(lbl_eng)
        row_eng.addWidget(self.combo_engineer, stretch=1)
        layout.addLayout(row_eng)

        # Алиас для обратной совместимости
        self.combo_models = self.combo_engineer

        layout.addStretch()

        # Нижняя панель кнопок: Обновить (слева), Отмена и Применить (справа)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_refresh = QPushButton("🔄 Обновить список", self)
        self.btn_refresh.setObjectName("btn_refresh")
        self.btn_refresh.setToolTip("Обновить список доступных моделей")
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch()

        self.btn_cancel = QPushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Применить", self)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

        # Начальная загрузка настроек
        self._load_models_and_settings()

    def _get_available_models(self) -> List[str]:
        """Получает список доступных .gguf моделей через ServerConfigResolver и lm_client."""
        models: List[str] = []
        try:
            from services.server_config import ServerConfigResolver
            discovered = ServerConfigResolver.discover_gguf_models()
            if discovered:
                models.extend(list(discovered.keys()))
        except Exception as e:
            logger.debug(f"Ошибка ServerConfigResolver.discover_gguf_models: {e}")

        parent_win = getattr(self, "_parent_ref", None) or self.parent()
        if parent_win and hasattr(parent_win, "lm_client") and parent_win.lm_client:
            try:
                client_models = parent_win.lm_client.get_models() or []
                for m in client_models:
                    if m not in models:
                        models.append(m)
            except Exception as e:
                logger.debug(f"Ошибка получения моделей от lm_client: {e}")

        if not models and parent_win and hasattr(parent_win, "lm_combo"):
            combo = parent_win.lm_combo
            if hasattr(combo, "count"):
                for i in range(combo.count()):
                    txt = combo.itemText(i).strip()
                    if txt and not txt.startswith("default") and txt not in models:
                        models.append(txt)

        return models

    def _populate_combo(self, combo: QComboBox, current_val: str, models: List[str]) -> None:
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            combo.addItem(m)
        if current_val:
            idx = combo.findText(current_val)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.addItem(current_val)
                combo.setCurrentIndex(combo.count() - 1)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _load_models_and_settings(self) -> None:
        """Считывает сохранённые модели из config.json и заполняет комбобоксы."""
        cfg = ConfigManager.load_config()
        asst_model = (cfg.get("assistant_model_name") or cfg.get("active_model", "")).strip()
        rp_model = (cfg.get("rp_model_name") or cfg.get("active_model", "")).strip()
        eng_model = (cfg.get("engineer_model_name") or cfg.get("engineer_model", "")).strip()

        models = self._get_available_models()

        self._populate_combo(self.combo_assistant, asst_model, models)
        self._populate_combo(self.combo_rp, rp_model, models)
        self._populate_combo(self.combo_engineer, eng_model, models)

    def _on_refresh_clicked(self) -> None:
        """Обновляет список моделей при нажатии на кнопку обновления."""
        asst_sel = self.combo_assistant.currentText().strip()
        rp_sel = self.combo_rp.currentText().strip()
        eng_sel = self.combo_engineer.currentText().strip()

        models = self._get_available_models()

        self._populate_combo(self.combo_assistant, asst_sel, models)
        self._populate_combo(self.combo_rp, rp_sel, models)
        self._populate_combo(self.combo_engineer, eng_sel, models)

    def _on_save_clicked(self) -> None:
        """
        Сохраняет выбранные модели для всех трех режимов в config.json.
        При необходимости инициирует фоновый hot-swap активной модели.
        """
        asst_model = self.combo_assistant.currentText().strip()
        rp_model = self.combo_rp.currentText().strip()
        eng_model = self.combo_engineer.currentText().strip()

        # 1. Запись в config.json
        cfg = ConfigManager.load_config()
        cfg["assistant_model_name"] = asst_model
        cfg["rp_model_name"] = rp_model
        cfg["engineer_model_name"] = eng_model
        cfg["engineer_model"] = eng_model
        ConfigManager.save_config(cfg)

        parent_win = getattr(self, "_parent_ref", None) or self.parent()
        if parent_win:
            if hasattr(parent_win, "app_config") and isinstance(parent_win.app_config, dict):
                parent_win.app_config["assistant_model_name"] = asst_model
                parent_win.app_config["rp_model_name"] = rp_model
                parent_win.app_config["engineer_model_name"] = eng_model
                parent_win.app_config["engineer_model"] = eng_model

            # Определение активного режима текущей сессии
            current_sess = getattr(parent_win, "current_session", None)
            active_mode = "assistant"
            if current_sess and isinstance(current_sess, dict):
                raw_mode = str(current_sess.get("mode") or "assistant").lower()
                raw_type = str(current_sess.get("type") or "").lower()
                if raw_type == "engineering" or raw_mode in ("engineer", "engineering"):
                    active_mode = "engineer"
                elif raw_mode in ("rp", "roleplay"):
                    active_mode = "rp"
                else:
                    active_mode = "assistant"
            elif hasattr(parent_win, "is_engineer_mode_active") and parent_win.is_engineer_mode_active():
                active_mode = "engineer"

            if active_mode == "engineer":
                target_model = eng_model
            elif active_mode == "rp":
                target_model = rp_model
            else:
                target_model = asst_model

            current_loaded = getattr(getattr(parent_win, "lm_client", None), "_last_loaded_model_id", None) or getattr(parent_win, "active_lms_model", "")
            if target_model:
                parent_win.active_lms_model = target_model
                if current_sess and isinstance(current_sess, dict) and not current_sess.get("is_model_locked", False):
                    current_sess["model_id"] = target_model
                    if hasattr(parent_win, "_save_current_session"):
                        try:
                            parent_win._save_current_session()
                        except Exception as e:
                            logger.warning(f"Ошибка сохранения сессии при смене модели: {e}")

                if hasattr(parent_win, "lm_combo") and parent_win.lm_combo:
                    idx = parent_win.lm_combo.findText(target_model)
                    if idx >= 0:
                        parent_win.lm_combo.setCurrentIndex(idx)
                    else:
                        parent_win.lm_combo.addItem(target_model)
                        parent_win.lm_combo.setCurrentIndex(parent_win.lm_combo.count() - 1)

                if current_loaded != target_model and hasattr(parent_win, "lm_client") and hasattr(parent_win.lm_client, "ensure_model_loaded"):
                    import threading
                    def _hot_swap():
                        try:
                            parent_win.lm_client.ensure_model_loaded(preferred_model=target_model)
                        except Exception as err:
                            logger.warning(f"[ModeModelSettingsDialog] Ошибка подгрузки модели: {err}")
                    threading.Thread(target=_hot_swap, daemon=True).start()

        self.accept()


ModeModelSettingsDialog = EngineerSettingsDialog
