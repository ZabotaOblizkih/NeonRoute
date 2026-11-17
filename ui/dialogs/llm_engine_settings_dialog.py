"""
ui/dialogs/llm_engine_settings_dialog.py
========================================
Диалог настройки движка LLM (llama-server.exe) в стиле Windows 11 Fluent Design
(FramelessDialog, FluentTitleBar, Dynamic Adaptive Palette).
Позволяет настраивать квантование KV-кэша (Q8_0 / Q4_0 / FP16) и Flash Attention (-fa on/off)
с реактивной карточкой подсказок и аппаратным аудитом GPU.
"""

import logging
from typing import Optional, Dict, Any
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
    QFrame,
    QFileDialog,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    SwitchButton,
    SegmentedWidget,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
    IconWidget,
    FluentTitleBar,
)

import config
from services.layer_budgeter import LayerBudgeter
from services.server_config import ServerConfigResolver
from services.llama_downloader import is_llama_server_installed, download_and_extract_llama_server
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import toast_success, toast_error, toast_info
from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.LLMEngineSettingsDialog")


class _LlamaDownloadThread(QThread):
    progress_signal = pyqtSignal(str, int, int)
    finished_signal = pyqtSignal(bool, str)

    def run(self):
        def _prog(msg, cur, tot):
            self.progress_signal.emit(msg, cur, tot)

        ok, res_str = download_and_extract_llama_server(progress_callback=_prog)
        self.finished_signal.emit(ok, res_str)


class LLMEngineSettingsDialog(FramelessDialog):
    """
    Лаконичный модальный диалог на базе PyQt-Fluent-Widgets и FramelessDialog:
    - Аппаратный статус GPU и Compute Capability
    - Селектор типа KV-кэша с реактивной подсказкой
    - Переключатель Flash Attention
    - Сохранение и горячий перезапуск сервера
    - Динамическая адаптивная палитра под цвет активных обоев
    """

    KV_OPTIONS = [
        ("q8_0", "Q8_0 (Рекомендуется / Баланс)"),
        ("q4_0", "Q4_0 (Экстремальная экономия VRAM)"),
        ("f16", "FP16 (Без сжатия / Эталонная точность)"),
    ]

    HINTS_DATA = {
        "q8_0": {
            "title": "🟢 Q8_0 — Оптимальный баланс (Рекомендуется)",
            "title_color": "#10B981",
            "border_color": "rgba(16, 185, 129, 0.35)",
            "bg_color": "rgba(16, 185, 129, 0.08)",
            "budget": "~64 КБ на токен | Экономия VRAM: 50%",
            "effect": "Сжимает контекст вдвое без потери качества (деградация <0.05%). Идеально для диалогов 24.5K на 8 ГБ VRAM.",
            "side_effect": "Побочные эффекты отсутствуют. Рекомендуется для всех видеокарт поколения Turing и новее.",
            "side_color": "#10B981",
        },
        "q4_0": {
            "title": "🟡 Q4_0 — Экстремальная экономия памяти",
            "title_color": "#F59E0B",
            "border_color": "rgba(245, 158, 11, 0.35)",
            "bg_color": "rgba(245, 158, 11, 0.08)",
            "budget": "~36 КБ на токен | Экономия VRAM: 75%",
            "effect": "Буфер в 4 раза легче, высвобождает до 1.5 ГБ VRAM под веса тяжелых моделей.",
            "side_effect": "В сверхдлинных чатах (>15K токенов) возможна потеря связности и мелких деталей сюжета/кода.",
            "side_color": "#FB923C",
        },
        "f16": {
            "title": "⚪ FP16 — Без сжатия (Студийная точность)",
            "title_color": "#CBD5E1",
            "border_color": "rgba(148, 163, 184, 0.30)",
            "bg_color": "rgba(148, 163, 184, 0.06)",
            "budget": "~128 КБ на токен | Экономия VRAM: 0%",
            "effect": "Математически точный расчет внимания без квантования.",
            "side_effect": "На контексте 24K токенов кэш займет более 3.2 ГБ VRAM, что вызовет вытеснение слоев модели на медленный CPU.",
            "side_color": "#F87171",
        },
    }

    THINKING_HINTS = {
        "auto": "Включается в режиме Инженера для сложного кода; отключается в диалогах, RP и при генерации арт-промптов.",
        "on": "Модель всегда рассуждает перед ответом (кроме генерации SDXL-промптов).",
        "off": "Мгновенные ответы без блока мыслей <think> и лишнего расхода токенов.",
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Настройки движка LLM (llama-server) — NeonRoute")
        self.setObjectName("LLMEngineSettingsDialog")
        self.resize(600, 720)
        self.setMinimumSize(540, 640)
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
        self._dl_thread: Optional[_LlamaDownloadThread] = None

        self._load_current_settings()
        self._init_ui()
        self.update_adaptive_palette()
        self._sync_with_current_config()
        self._refresh_engine_status()

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
            LLMEngineSettingsDialog#LLMEngineSettingsDialog,
            LLMEngineSettingsDialog {{
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
            QWidget#LLMEngineBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #LLMEngineBodyWidget QWidget {{
                background-color: transparent;
            }}
            QLabel#dialogTitle {{
                color: {palette['group_title']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#dialogSubtitle {{
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }}
            QFrame#hwCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame#hwCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QFrame#engineCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame#engineCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QFrame#faFrame {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame#faFrame:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QFrame#thinkingFrame {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
            }}
            QFrame#thinkingFrame:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QLabel.section_label {{
                color: {palette['text_primary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 12px;
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

        # Обновляем оформление карточки-подсказки с текущим KV
        if hasattr(self, "current_kv"):
            self._update_hint_card(self.current_kv)

    def _apply_theme(self):
        """Fluent Dark / Adaptive Palette стилизация (обратная совместимость)."""
        self.update_adaptive_palette()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_adaptive_palette()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _load_current_settings(self):
        """Загрузка профиля оборудования и сохраненной конфигурации."""
        self.hw_profile = LayerBudgeter.detect_hardware_kv_profile()
        self.app_cfg = ConfigManager.load_config()
        self.current_kv = self.app_cfg.get("kv_cache_type", "q8_0").lower()
        if self.current_kv in ("fp16", "f16"):
            self.current_kv = "f16"
        elif "q4" in self.current_kv:
            self.current_kv = "q4_0"
        else:
            self.current_kv = "q8_0"

        self.current_fa = bool(self.app_cfg.get("flash_attention_enabled", True))
        self.current_thinking = str(self.app_cfg.get("thinking_mode", "auto")).lower()
        if self.current_thinking not in ("auto", "on", "off"):
            self.current_thinking = "auto"

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("LLMEngineBodyWidget")
        main_layout = QVBoxLayout(self.body_widget)
        main_layout.setContentsMargins(24, 14, 24, 20)
        main_layout.setSpacing(12)

        # 1. Заголовок диалога
        header_box = QVBoxLayout()
        header_box.setSpacing(3)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        header_icon = IconWidget(FIF.DEVELOPER_TOOLS, self.body_widget)
        header_icon.setFixedSize(22, 22)
        header_layout.addWidget(header_icon)

        title_label = QLabel("⚡ НАСТРОЙКИ ДВИЖКА LLM", self.body_widget)
        title_label.setObjectName("dialogTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_box.addLayout(header_layout)

        subtitle = CaptionLabel("Оптимизация распределения памяти и аппаратного ускорения контекста", self.body_widget)
        subtitle.setObjectName("dialogSubtitle")
        header_box.addWidget(subtitle)
        main_layout.addLayout(header_box)

        # Разделитель
        sep = QFrame(self.body_widget)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        sep.setFixedHeight(1)
        main_layout.addWidget(sep)

        # 2. Карточка аппаратного статуса GPU
        self.hw_card = QFrame(self.body_widget)
        self.hw_card.setObjectName("hwCard")
        hw_layout = QHBoxLayout(self.hw_card)
        hw_layout.setContentsMargins(16, 12, 16, 12)
        hw_layout.setSpacing(12)

        chip_icon = IconWidget(FIF.APPLICATION, self.hw_card)
        chip_icon.setFixedSize(28, 28)
        hw_layout.addWidget(chip_icon)

        hw_text_layout = QVBoxLayout()
        hw_text_layout.setSpacing(2)

        gpu_name = self.hw_profile.get("gpu_name", "Unknown GPU")
        vendor = str(self.hw_profile.get("vendor", "unknown")).lower()
        vram_mb = self.hw_profile.get("vram_total_mb", 0)
        cc_str = self.hw_profile.get("compute_cap", "N/A")
        fa_supported = self.hw_profile.get("supported", False)

        vram_display = f" ({vram_mb} МБ)" if vram_mb > 0 else ""
        if vendor == "nvidia":
            hw_title = BodyLabel(f"Видеокарта: {gpu_name}{vram_display}", self.hw_card)
            status_text = (
                f"NVIDIA Compute Capability {cc_str} • Аппаратный Flash Attention поддерживается (Turing+)"
                if fa_supported
                else f"NVIDIA Compute Capability {cc_str} • Flash Attention не поддерживается архитектурой"
            )
            status_color = "#10B981" if fa_supported else "#94A3B8"
        elif vendor == "amd":
            hw_title = BodyLabel(f"Видеокарта: {gpu_name}{vram_display}", self.hw_card)
            status_text = "Архитектура AMD Radeon • Ускорение через Vulkan / DirectML (Flash Attention недоступен)"
            status_color = "#38BDF8"
        elif vendor == "intel":
            hw_title = BodyLabel(f"Видеокарта: {gpu_name}{vram_display}", self.hw_card)
            status_text = "Архитектура Intel • Ускорение через Vulkan / OpenVINO (Flash Attention недоступен)"
            status_color = "#38BDF8"
        else:
            hw_title = BodyLabel("Видеокарта: Дискретный GPU не обнаружен (CPU-режим)", self.hw_card)
            status_text = "Вычисления выполняются на центральном процессоре (AVX2 / CPU)"
            status_color = "#94A3B8"

        hw_title.setStyleSheet("font-weight: bold; color: #E2E8F0;")
        hw_text_layout.addWidget(hw_title)

        hw_desc = CaptionLabel(status_text, self.hw_card)
        hw_desc.setStyleSheet(f"color: {status_color};")
        hw_text_layout.addWidget(hw_desc)

        hw_layout.addLayout(hw_text_layout)
        hw_layout.addStretch()
        main_layout.addWidget(self.hw_card)

        # 3. Карточка «Движок llama-server и папка моделей»
        self.engine_card = QFrame(self.body_widget)
        self.engine_card.setObjectName("engineCard")
        engine_layout = QVBoxLayout(self.engine_card)
        engine_layout.setContentsMargins(16, 12, 16, 12)
        engine_layout.setSpacing(10)

        # Ряд 1: статус llama-server и кнопка загрузки
        row_server = QHBoxLayout()
        row_server.setSpacing(10)
        server_icon = IconWidget(FIF.DEVELOPER_TOOLS, self.engine_card)
        server_icon.setFixedSize(22, 22)
        row_server.addWidget(server_icon)

        self.lbl_server_status = BodyLabel("", self.engine_card)
        self.lbl_server_status.setStyleSheet("font-weight: 600;")
        row_server.addWidget(self.lbl_server_status)
        row_server.addStretch()

        self.btn_download_server = PushButton(FIF.DOWNLOAD, "Скачать / обновить llama-server", self.engine_card)
        self.btn_download_server.clicked.connect(self._on_download_server_clicked)
        row_server.addWidget(self.btn_download_server)
        engine_layout.addLayout(row_server)

        # Разделитель внутри карточки
        sub_sep = QFrame(self.engine_card)
        sub_sep.setFrameShape(QFrame.Shape.HLine)
        sub_sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.05);")
        sub_sep.setFixedHeight(1)
        engine_layout.addWidget(sub_sep)

        # Ряд 2: папка моделей и кнопка Обзор
        row_models = QHBoxLayout()
        row_models.setSpacing(10)
        models_icon = IconWidget(FIF.FOLDER, self.engine_card)
        models_icon.setFixedSize(22, 22)
        row_models.addWidget(models_icon)

        models_vbox = QVBoxLayout()
        models_vbox.setSpacing(2)
        models_title = CaptionLabel("Папка .gguf моделей:", self.engine_card)
        models_title.setStyleSheet("color: #94A3B8; font-size: 11px;")
        models_vbox.addWidget(models_title)

        self.lbl_models_path = CaptionLabel("", self.engine_card)
        self.lbl_models_path.setStyleSheet("color: #CBD5E1; font-weight: 500; font-size: 11px;")
        models_vbox.addWidget(self.lbl_models_path)
        row_models.addLayout(models_vbox)
        row_models.addStretch()

        self.btn_browse_models = PushButton(FIF.FOLDER, "Обзор...", self.engine_card)
        self.btn_browse_models.clicked.connect(self._on_browse_models_clicked)
        row_models.addWidget(self.btn_browse_models)
        engine_layout.addLayout(row_models)

        main_layout.addWidget(self.engine_card)

        # 3. Блок выбора квантования KV-кэша
        kv_section_label = BodyLabel("Тип буфера KV-кэша (контекста):", self.body_widget)
        kv_section_label.setProperty("class", "section_label")
        kv_section_label.setStyleSheet("font-weight: 600; color: #CBD5E1;")
        main_layout.addWidget(kv_section_label)

        self.kv_combo = ComboBox(self.body_widget)
        for _, label in self.KV_OPTIONS:
            self.kv_combo.addItem(label)
        self.kv_combo.currentIndexChanged.connect(self._on_kv_selection_changed)
        main_layout.addWidget(self.kv_combo)

        # 4. Реактивная карточка-подсказка
        self.hint_card = QFrame(self.body_widget)
        self.hint_card.setObjectName("hintCard")
        hint_layout = QVBoxLayout(self.hint_card)
        hint_layout.setContentsMargins(16, 14, 16, 14)
        hint_layout.setSpacing(8)

        self.hint_title_lbl = BodyLabel("", self.hint_card)
        self.hint_title_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        hint_layout.addWidget(self.hint_title_lbl)

        self.hint_budget_lbl = CaptionLabel("", self.hint_card)
        self.hint_budget_lbl.setStyleSheet("color: #00F0FF; font-weight: 600; font-size: 11px;")
        hint_layout.addWidget(self.hint_budget_lbl)

        self.hint_effect_lbl = CaptionLabel("", self.hint_card)
        self.hint_effect_lbl.setWordWrap(True)
        self.hint_effect_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; line-height: 1.4;")
        hint_layout.addWidget(self.hint_effect_lbl)

        self.hint_side_lbl = CaptionLabel("", self.hint_card)
        self.hint_side_lbl.setWordWrap(True)
        self.hint_side_lbl.setStyleSheet("font-size: 11px; line-height: 1.4;")
        hint_layout.addWidget(self.hint_side_lbl)

        main_layout.addWidget(self.hint_card)

        # 5. Переключатель Flash Attention
        self.fa_frame = QFrame(self.body_widget)
        self.fa_frame.setObjectName("faFrame")
        fa_layout = QHBoxLayout(self.fa_frame)
        fa_layout.setContentsMargins(14, 10, 14, 10)
        fa_layout.setSpacing(12)

        fa_text_layout = QVBoxLayout()
        fa_text_layout.setSpacing(2)
        fa_title = BodyLabel("Аппаратное ускорение Flash Attention (-fa on)", self.fa_frame)
        fa_title.setStyleSheet("font-weight: 600; color: #E2E8F0;")
        fa_text_layout.addWidget(fa_title)

        fa_desc = CaptionLabel("Выполняет расчет внимания через быстрые SRAM-тензорные ядра чипа.", self.fa_frame)
        fa_desc.setStyleSheet("color: #94A3B8; font-size: 11px;")
        fa_text_layout.addWidget(fa_desc)
        fa_layout.addLayout(fa_text_layout)
        fa_layout.addStretch()

        self.switch_fa = SwitchButton(self.fa_frame)
        self.switch_fa.setOnText("ВКЛ")
        self.switch_fa.setOffText("ВЫКЛ")
        self.switch_fa.setToolTip(
            "Выполняет расчет внимания через быстрые SRAM-тензорные ядра. "
            "Ускоряет генерацию длинных ответов в 2–3 раза. Требует GPU Turing+ (CC >= 7.5)."
        )
        fa_layout.addWidget(self.switch_fa)
        main_layout.addWidget(self.fa_frame)

        # 6. Карточка «Режим размышлений (Thinking)»
        self.thinking_frame = QFrame(self.body_widget)
        self.thinking_frame.setObjectName("thinkingFrame")
        th_layout = QVBoxLayout(self.thinking_frame)
        th_layout.setContentsMargins(14, 10, 14, 10)
        th_layout.setSpacing(6)

        th_header = QHBoxLayout()
        th_header.setSpacing(12)

        th_title = BodyLabel("Режим размышлений (Thinking / Reasoning)", self.thinking_frame)
        th_title.setStyleSheet("font-weight: 600; color: #E2E8F0;")
        th_header.addWidget(th_title)
        th_header.addStretch()

        self.segmented_thinking = SegmentedWidget(self.thinking_frame)
        self.segmented_thinking.addItem("auto", "Авто")
        self.segmented_thinking.addItem("on", "Вкл")
        self.segmented_thinking.addItem("off", "Выкл")
        self.segmented_thinking.currentItemChanged.connect(self._on_thinking_selection_changed)
        th_header.addWidget(self.segmented_thinking)
        th_layout.addLayout(th_header)

        self.lbl_thinking_hint = CaptionLabel("", self.thinking_frame)
        self.lbl_thinking_hint.setWordWrap(True)
        self.lbl_thinking_hint.setStyleSheet("color: #94A3B8; font-size: 11px; line-height: 1.3;")
        th_layout.addWidget(self.lbl_thinking_hint)

        main_layout.addWidget(self.thinking_frame)

        main_layout.addStretch()

        # 7. Кнопки управления (Сохранить и перезапустить / Отмена)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.btn_cancel = PushButton("Отмена", self.body_widget)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton(FIF.ACCEPT, "Сохранить и перезапустить", self.body_widget)
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)
        root_layout.addWidget(self.body_widget)

    def _sync_with_current_config(self):
        """Устанавливает текущие значения из конфига в элементы управления."""
        # KV Combo
        matched_idx = 0
        for i, (k, _) in enumerate(self.KV_OPTIONS):
            if k == self.current_kv:
                matched_idx = i
                break
        self.kv_combo.setCurrentIndex(matched_idx)
        self._update_hint_card(self.current_kv)

        # Flash Attention Switch
        self.switch_fa.setChecked(self.current_fa)

        # Thinking Mode Segmented
        self.segmented_thinking.setCurrentItem(self.current_thinking)
        self._on_thinking_selection_changed(self.current_thinking)

    def _on_thinking_selection_changed(self, route_key: str):
        """Слот изменения выбранного режима размышлений."""
        if route_key in self.THINKING_HINTS:
            self.current_thinking = route_key
            self.lbl_thinking_hint.setText(self.THINKING_HINTS[route_key])

    def _on_kv_selection_changed(self, index: int):
        """Слот изменения выбранного типа KV-кэша."""
        if 0 <= index < len(self.KV_OPTIONS):
            kv_key = self.KV_OPTIONS[index][0]
            self.current_kv = kv_key
            self._update_hint_card(kv_key)

    def _update_hint_card(self, kv_key: str):
        """Реактивное обновление текстов и цветов карточки-подсказки."""
        data = self.HINTS_DATA.get(kv_key, self.HINTS_DATA["q8_0"])

        self.hint_title_lbl.setText(data["title"])
        self.hint_title_lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {data['title_color']};")

        self.hint_budget_lbl.setText(data["budget"])

        self.hint_effect_lbl.setText(data["effect"])

        self.hint_side_lbl.setText(data["side_effect"])
        self.hint_side_lbl.setStyleSheet(f"font-size: 11px; line-height: 1.4; color: {data['side_color']};")

        # Плавное обновление карточки с акцентными границами и фоном
        self.hint_card.setStyleSheet(f"""
            QFrame#hintCard {{
                background-color: {data['bg_color']};
                border: 1px solid {data['border_color']};
                border-radius: 10px;
            }}
        """)

    def _on_save_clicked(self):
        """Сохранение выбранных параметров и мягкий перезапуск llama-server при необходимости."""
        selected_idx = self.kv_combo.currentIndex()
        selected_kv = self.KV_OPTIONS[selected_idx][0] if 0 <= selected_idx < len(self.KV_OPTIONS) else "q8_0"
        fa_enabled = self.switch_fa.isChecked()
        selected_thinking = self.segmented_thinking.currentRouteKey() or self.current_thinking

        kv_changed = (selected_kv != self.current_kv)
        fa_changed = (fa_enabled != self.current_fa)

        try:
            cfg = ConfigManager.load_config()
            cfg["kv_cache_type"] = selected_kv
            cfg["flash_attention_enabled"] = fa_enabled
            cfg["thinking_mode"] = selected_thinking
            ConfigManager.save_config(cfg)
            self.app_cfg = cfg
            self.current_kv = selected_kv
            self.current_fa = fa_enabled
            self.current_thinking = selected_thinking

            parent_win = self._parent_ref or self.parent()
            top_win = parent_win.window() if (parent_win and hasattr(parent_win, "window")) else None
            for win in (parent_win, top_win):
                if win is not None and isinstance(getattr(win, "app_config", None), dict):
                    win.app_config["kv_cache_type"] = selected_kv
                    win.app_config["flash_attention_enabled"] = fa_enabled
                    win.app_config["thinking_mode"] = selected_thinking

            logger.info(
                f"[LLMEngineSettingsDialog] Сохранено: kv_cache_type={selected_kv}, "
                f"flash_attention={fa_enabled}, thinking_mode={selected_thinking}"
            )

            # Перезапуск llama-server ТОЛЬКО если изменились аппаратные параметры KV или FA
            if kv_changed or fa_changed:
                parent_win = self._parent_ref or self.parent()
                pm = getattr(parent_win, "process_manager", None)
                if pm and hasattr(pm, "restart_llama_server_async"):
                    if pm.is_lm_studio_running():
                        pm.restart_llama_server_async()
                        logger.info("[LLMEngineSettingsDialog] Отправлена команда перезапуска llama-server (изменились KV/FA).")
            else:
                logger.info("[LLMEngineSettingsDialog] Перезапуск llama-server не требуется (изменился только thinking_mode).")

            self.accept()
        except Exception as e:
            logger.error(f"[LLMEngineSettingsDialog] Ошибка сохранения параметров: {e}")
            self.reject()

    def _refresh_engine_status(self):
        """Обновляет индикатор статуса llama-server.exe и путь к папке моделей."""
        installed = is_llama_server_installed()
        if installed:
            self.lbl_server_status.setText("🟢 Движок llama-server установлен в bin/")
            self.lbl_server_status.setStyleSheet("color: #10B981; font-weight: 600;")
        else:
            self.lbl_server_status.setText("🟡 Движок llama-server отсутствует в bin/")
            self.lbl_server_status.setStyleSheet("color: #F59E0B; font-weight: 600;")

        ini_cfg = ServerConfigResolver.load_ini()
        custom_dir = ini_cfg.get("models_dir", "").strip()
        if custom_dir:
            self.lbl_models_path.setText(f"{custom_dir} (пользовательская)")
        else:
            self.lbl_models_path.setText(f"{config.LOCAL_MODELS_DIR} (стандартная)")

    def _on_browse_models_clicked(self):
        """Открывает диалог выбора внешней папки для .gguf моделей."""
        ini_cfg = ServerConfigResolver.load_ini()
        cur_dir = ini_cfg.get("models_dir", "").strip() or str(config.LOCAL_MODELS_DIR)
        selected_dir = QFileDialog.getExistingDirectory(self, "Выберите папку с .gguf моделями", cur_dir)
        if selected_dir:
            ok = ServerConfigResolver.update_server_config_models_dir(selected_dir)
            if ok:
                self._refresh_engine_status()
                toast_success(self, "Папка моделей", f"Внешняя папка моделей обновлена:\n{selected_dir}")
                parent_win = self._parent_ref or self.parent()
                if parent_win and hasattr(parent_win, "_refresh_models"):
                    parent_win._refresh_models()

    def _on_download_server_clicked(self):
        """Запускает фоновое скачивание и распаковку подходящего llama-server."""
        if self._dl_thread and self._dl_thread.isRunning():
            return

        self.btn_download_server.setEnabled(False)
        self.btn_download_server.setText("Скачивание...")
        self.lbl_server_status.setText("⏳ Загрузка llama-server с GitHub...")
        self.lbl_server_status.setStyleSheet("color: #38BDF8; font-weight: 600;")

        self._dl_thread = _LlamaDownloadThread(self)
        self._dl_thread.progress_signal.connect(self._on_download_progress)
        self._dl_thread.finished_signal.connect(self._on_download_finished)
        self._dl_thread.start()

    def _on_download_progress(self, msg: str, cur: int, tot: int):
        if tot > 0:
            pct = (cur / tot) * 100
            self.lbl_server_status.setText(f"⏳ {msg} ({pct:.0f}%)")
        else:
            self.lbl_server_status.setText(f"⏳ {msg}")

    def _on_download_finished(self, success: bool, msg: str):
        self.btn_download_server.setEnabled(True)
        self.btn_download_server.setText("Скачать / обновить llama-server")
        self._refresh_engine_status()
        if success:
            toast_success(self, "llama-server", msg)
        else:
            toast_error(self, "Ошибка загрузки", msg)
