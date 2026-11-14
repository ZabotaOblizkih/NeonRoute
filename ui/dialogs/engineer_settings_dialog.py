"""
Mode & Model Settings Dialog for NeonRoute.
Allows selecting and persisting dedicated LLM models for Assistant, Roleplay,
and autonomous Engineer Mode, with background hot-swap capabilities.
Redesigned to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

import logging
from typing import Optional, List, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QWidget, QFrame
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    ComboBox,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
)

from ui.common.palette_utils import extract_adaptive_palette
from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.ModeModelSettingsDialog")


class EngineerSettingsDialog(FramelessDialog):
    """
    Модальное окно выбора моделей по режимам в стиле Windows 11 Fluent Design.
    Позволяет настраивать постоянные GGUF-модели отдельно для:
    - 💬 Ассистент (Элария)
    - 🎭 Ролевой чат (Tavern / RP)
    - 🛠️ Инженер песочницы (Agentic Coding)
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Выбор моделей по режимам — NeonRoute")
        self.setObjectName("ModeModelSettingsDialog")
        self.resize(580, 480)
        self.setMinimumSize(520, 420)
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

        self._setup_ui()
        self.update_adaptive_palette()

        # Начальная загрузка настроек
        self._load_models_and_settings()

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

        self.setStyleSheet(f"""
            EngineerSettingsDialog#ModeModelSettingsDialog,
            EngineerSettingsDialog {{
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
            QWidget#ModeModelBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #ModeModelBodyWidget QWidget {{
                background-color: transparent;
            }}
            QFrame.ModeCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame.ModeCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QLabel#header_title {{
                color: {palette['group_title']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#header_sub {{
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }}
            QLabel.card_title {{
                color: {palette['text_primary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel.card_hint {{
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }}
            ComboBox {{
                background-color: {palette['bg']};
                color: {palette['text_primary']};
                border: 1px solid {palette['card_border']};
                border-radius: 6px;
                padding: 5px 12px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 500;
                min-height: 28px;
            }}
            ComboBox:hover {{
                border-color: {palette['accent']};
                background-color: {palette['card_hover']};
            }}
            ComboBox:focus {{
                border-color: {palette['accent']};
            }}
            PushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 16px;
                min-height: 20px;
            }}
            PushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-family: 'Segoe UI', -apple-system, sans-serif;
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

    def showEvent(self, event):
        super().showEvent(event)
        self.update_adaptive_palette()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("ModeModelBodyWidget")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(24, 12, 24, 20)
        body_layout.setSpacing(12)

        # Header Title & Subtitle
        header_box = QVBoxLayout()
        header_box.setSpacing(3)
        self.lbl_title = QLabel("🎯 ВЫБОР МОДЕЛЕЙ ДЛЯ РЕЖИМОВ", self.body_widget)
        self.lbl_title.setObjectName("header_title")
        header_box.addWidget(self.lbl_title)

        self.lbl_desc = QLabel(
            "Назначьте постоянные модели для каждого рабочего контура",
            self.body_widget
        )
        self.lbl_desc.setObjectName("header_sub")
        self.lbl_desc.setWordWrap(True)
        header_box.addWidget(self.lbl_desc)
        body_layout.addLayout(header_box)

        # Разделитель
        sep = QFrame(self.body_widget)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        sep.setFixedHeight(1)
        body_layout.addWidget(sep)

        # Селектор 1: Ассистент
        self.card_asst = QFrame(self.body_widget)
        self.card_asst.setProperty("class", "ModeCard")
        lay_asst = QVBoxLayout(self.card_asst)
        lay_asst.setContentsMargins(14, 10, 14, 10)
        lay_asst.setSpacing(6)

        hdr_asst = QHBoxLayout()
        lbl_asst_title = QLabel("💬 Ассистент (Элария)", self.card_asst)
        lbl_asst_title.setProperty("class", "card_title")
        hdr_asst.addWidget(lbl_asst_title)
        hdr_asst.addStretch()
        lay_asst.addLayout(hdr_asst)

        lbl_asst_hint = QLabel("Основной диалог, общие вопросы, анализ и рассуждения", self.card_asst)
        lbl_asst_hint.setProperty("class", "card_hint")
        lay_asst.addWidget(lbl_asst_hint)

        self.combo_assistant = ComboBox(self.card_asst)
        self.combo_assistant.setPlaceholderText("Выберите модель для Ассистента...")
        lay_asst.addWidget(self.combo_assistant)

        body_layout.addWidget(self.card_asst)

        # Селектор 2: РП Чат
        self.card_rp = QFrame(self.body_widget)
        self.card_rp.setProperty("class", "ModeCard")
        lay_rp = QVBoxLayout(self.card_rp)
        lay_rp.setContentsMargins(14, 10, 14, 10)
        lay_rp.setSpacing(6)

        hdr_rp = QHBoxLayout()
        lbl_rp_title = QLabel("🎭 Ролевой чат (Tavern / RP)", self.card_rp)
        lbl_rp_title.setProperty("class", "card_title")
        hdr_rp.addWidget(lbl_rp_title)
        hdr_rp.addStretch()
        lay_rp.addLayout(hdr_rp)

        lbl_rp_hint = QLabel("Отыгрыш персонажей, креативный нарратив, симуляции и диалоги", self.card_rp)
        lbl_rp_hint.setProperty("class", "card_hint")
        lay_rp.addWidget(lbl_rp_hint)

        self.combo_rp = ComboBox(self.card_rp)
        self.combo_rp.setPlaceholderText("Выберите модель для Ролевого чата...")
        lay_rp.addWidget(self.combo_rp)

        body_layout.addWidget(self.card_rp)

        # Селектор 3: Инженер
        self.card_eng = QFrame(self.body_widget)
        self.card_eng.setProperty("class", "ModeCard")
        lay_eng = QVBoxLayout(self.card_eng)
        lay_eng.setContentsMargins(14, 10, 14, 10)
        lay_eng.setSpacing(6)

        hdr_eng = QHBoxLayout()
        lbl_eng_title = QLabel("🛠️ Инженер песочницы (Agentic Coding)", self.card_eng)
        lbl_eng_title.setProperty("class", "card_title")
        hdr_eng.addWidget(lbl_eng_title)
        hdr_eng.addStretch()
        lay_eng.addLayout(hdr_eng)

        lbl_eng_hint = QLabel("Автономная разработка, запуск тестов, терминал и работа с кодом", self.card_eng)
        lbl_eng_hint.setProperty("class", "card_hint")
        lay_eng.addWidget(lbl_eng_hint)

        self.combo_engineer = ComboBox(self.card_eng)
        self.combo_engineer.setPlaceholderText("Выберите модель для Инженера...")
        lay_eng.addWidget(self.combo_engineer)

        # Алиас для обратной совместимости
        self.combo_models = self.combo_engineer

        body_layout.addWidget(self.card_eng)

        body_layout.addStretch()

        # Нижняя панель кнопок: Обновить (слева), Отмена и Применить (справа)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_refresh = PushButton(FIF.SYNC, "Обновить список", self.body_widget)
        self.btn_refresh.setObjectName("btn_refresh")
        self.btn_refresh.setToolTip("Обновить список доступных моделей из каталогов и сервера")
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch()

        self.btn_cancel = PushButton("Отмена", self.body_widget)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton(FIF.ACCEPT, "Применить", self.body_widget)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.btn_save)

        body_layout.addLayout(btn_row)
        root_layout.addWidget(self.body_widget)

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

    def _populate_combo(self, combo: ComboBox, current_val: str, models: List[str]) -> None:
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
