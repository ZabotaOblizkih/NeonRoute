"""
Models Manager Dialog for NeonRoute.
Configures available Stable Diffusion Forge checkpoints, pipeline generation parameters,
and FastEmbed vector routing rules in modern Windows 11 / Glassmorphism aesthetic.
"""

import os
import subprocess
import logging

logger = logging.getLogger("NeonRoute.ModelsManagerDialog")

import uuid
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

_GLOBAL_TORCH_PYTHON: Optional[str] = None

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QHeaderView, QMessageBox, QFrame, QWidget, QApplication,
    QSplitter, QFormLayout, QScrollArea
)
from qfluentwidgets import (
    LineEdit,
    SearchLineEdit,
    TextEdit,
    ComboBox,
    EditableComboBox,
    CheckBox,
    Slider,
    SpinBox,
    DoubleSpinBox,
    PushButton,
    PrimaryPushButton,
    TransparentToolButton,
    SwitchButton,
    ProgressBar,
    InfoBar,
    InfoBarPosition,
    TableWidget,
    FluentIcon as FIF,
    setTheme,
    Theme
)

from services.model_router import ModelRouter
from services.art_director_config import (
    load_art_director_models,
    save_art_director_models
)
from services.art_director.geometry import is_xl_model
from services.forge_client import ForgeClient
from services.forge_models import sync_registry_with_user_config
# === ВЫДЕЛЕННЫЕ ВИДЖЕТЫ И ЭЛЕМЕНТЫ УПРАВЛЕНИЯ ===
from ui.models_manager.widgets import (
    SamplingSlider,
    SamplingDoubleSpinBox,
    SamplingSpinBox,
    FluentEditableComboBox,
    FluentTextEdit,
    RoutingResultBadge,
)

# === ВЫДЕЛЕННЫЕ ФОНОВЫЕ ВОРКЕРЫ QThread ===
from ui.models_manager.workers import (
    RoutingTestWorker,
    ForgeScanWorker,
    CivitaiParamsWorker,
    FP8ConvertWorker,
)

class ModelsManagerDialog(QDialog):
    """
    Центральный пульт управления чекпоинтами SD Forge, пайплайном рендера
    и семантическим роутингом FastEmbed CPU ONNX в эстетике Glassmorphism.
    """

    @staticmethod
    def _normalize_checkpoint_stem(filename: str) -> str:
        """Очищает имя чекпоинта от суффиксов квантования для безопасного сопоставления."""
        if not filename:
            return ""
        stem = Path(filename).stem.lower()
        suffixes = (
            "_fp8e4", "_fp16e4", "_fp8_e4m3fn", "_e4m3fn", "_e5m2",
            "_fp8", "_fp16", "_bf16", "_pruned", "-fp8", "-fp16"
        )
        changed = True
        while changed:
            changed = False
            for sfx in suffixes:
                if stem.endswith(sfx):
                    stem = stem[:-len(sfx)]
                    changed = True
                    break
        return stem.strip(" _-")

    @staticmethod
    def _get_default_negative(checkpoint_name: str = "", model_id: str = "") -> str:
        """Интеллектуальная подстановка рекомендованного негатива по типу модели."""
        s = f"{checkpoint_name} {model_id}".lower()
        if any(k in s for k in ("anime", "hentai", "waifu", "manga", "meina")):
            return "worst quality, low quality, blurry, bad anatomy, bad hands, deformed limbs, photorealistic, realistic, 3d, text, watermark"
        elif any(k in s for k in ("dreamshaper", "artistic", "fantasy", "paint", "drawing", "illustration")):
            return "ugly, deformed, noisy, blurry, low contrast"
        elif any(k in s for k in ("cyber", "cyberrealistic", "neon", "scifi", "future")):
            return "worst quality, low quality, blurry, bad anatomy, bad proportions, deformed, extra limbs, plastic skin, doll, cgi, 2d, illustration, badhandv4"
        else:
            return "worst quality, low quality, blurry, bad anatomy, bad proportions, deformed, disfigured, extra limbs, badhandv4, text"

    def __init__(
        self,
        forge_client: Optional[ForgeClient] = None,
        parent: Optional[QWidget] = None,
        art_director: Optional[Any] = None
    ):
        super().__init__(parent)
        setTheme(Theme.DARK)

        self.forge_client = forge_client or ForgeClient()
        self.art_director = art_director or getattr(parent, "art_director", None)

        if self.art_director and hasattr(self.art_director, "router") and self.art_director.router:
            self.router: ModelRouter = self.art_director.router
        else:
            self.router = ModelRouter(auto_load=True)

        self.models: List[Dict[str, Any]] = self.router.load_profiles()
        self.available_checkpoints: List[str] = list({
            m.get("checkpoint", "").strip() 
            for m in self.models 
            if m.get("checkpoint", "").strip()
        })
        if not self.available_checkpoints:
            self.available_checkpoints = [
                "realisticVisionV60B1_v51HyperVAE_pruned_fp16.safetensors",
                "cyberrealistic_classic.safetensors",
                "dreamshaper_8.safetensors",
                "meinahentai_v5Final.safetensors"
            ]
        self.row_widgets: List[Dict[str, Any]] = []
        self.current_selected_row: int = -1
        self._autofill_target_row: int = -1
        self._test_worker: Optional[RoutingTestWorker] = None
        self._scan_worker: Optional[ForgeScanWorker] = None
        self._civitai_worker: Optional[CivitaiParamsWorker] = None
        self._fp8_worker: Optional[FP8ConvertWorker] = None
        self._cached_torch_python: Optional[str] = None
        self._fp8_target_row: int = -1
        self._fp8_source_path: Optional[Path] = None
        self._fp8_source_name: str = ""

        self.setObjectName("ModelsManagerDialog")
        self.setWindowTitle("🎨 Модели генерации, параметры пайплайна и роутинг — NeonRoute")
        self.resize(1260, 780)
        self.setMinimumSize(1020, 680)
        self.setModal(True)

        self._setup_ui()
        self._populate_table()

        # Неблокирующий фоновый опрос Forge сразу после отрисовки интерфейса
        self._start_forge_scan(silent=True)

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog#ModelsManagerDialog {
                background-color: rgba(10, 13, 20, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.22);
                border-radius: 14px;
            }
            QLabel {
                color: #CBD5E1;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
            }
            QFrame#LeftPanel, QFrame#EditorPanel, QFrame#TestPanel {
                background-color: rgba(14, 19, 31, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 12px;
            }
            QFrame#LeftPanel:hover, QFrame#EditorPanel:hover, QFrame#TestPanel:hover {
                border-color: rgba(0, 240, 255, 0.18);
            }
            QFrame#ParamCard {
                background-color: rgba(18, 24, 38, 0.60);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 8px;
                padding: 10px;
            }
            QSplitter::handle {
                background-color: rgba(255, 255, 255, 0.06);
                width: 3px;
            }
            QSplitter::handle:hover {
                background-color: #00F0FF;
            }
            TableWidget {
                background-color: rgba(11, 15, 25, 0.90);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                color: #E2E8F0;
                gridline-color: rgba(255, 255, 255, 0.04);
                selection-background-color: rgba(0, 240, 255, 0.15);
                selection-color: #00F0FF;
                font-size: 12px;
            }
            TableWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            }
            TableWidget::item:selected {
                background-color: rgba(0, 240, 255, 0.15);
                color: #00F0FF;
                font-weight: 600;
            }
            TableWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.04);
            }
            QHeaderView::section {
                background-color: rgba(18, 24, 38, 0.95);
                color: #94A3B8;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                border-right: 1px solid rgba(255, 255, 255, 0.04);
            }
            PrimaryPushButton#PrimaryBtn {
                background-color: #00F0FF;
                color: #000000;
                font-weight: 700;
                border: 1px solid #00F0FF;
                border-radius: 6px;
            }
            PrimaryPushButton#PrimaryBtn:hover {
                background-color: #38BDF8;
                border-color: #38BDF8;
                color: #000000;
            }
            PrimaryPushButton#PrimaryBtn:pressed {
                background-color: #0284C7;
                border-color: #0284C7;
                color: #FFFFFF;
            }
            PrimaryPushButton#TestBtn {
                background-color: #0284C7;
                color: #FFFFFF;
                font-weight: 600;
                border: 1px solid #38BDF8;
                border-radius: 6px;
            }
            PrimaryPushButton#TestBtn:hover {
                background-color: #0369A1;
                border-color: #00F0FF;
            }
            PushButton#AutoFillBtn {
                background-color: rgba(16, 185, 129, 0.15);
                border: 1px solid rgba(16, 185, 129, 0.40);
                color: #34D399;
                font-weight: 600;
            }
            PushButton#AutoFillBtn:hover {
                background-color: rgba(16, 185, 129, 0.25);
                border-color: #34D399;
                color: #6EE7B7;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # 1. Header Banner
        hdr_layout = QVBoxLayout()
        hdr_layout.setSpacing(3)
        title_lbl = QLabel("🎨 Управление моделями, параметрами генерации и роутингом", self)
        title_lbl.setStyleSheet("color: #00F0FF; font-size: 15px; font-weight: 800; letter-spacing: 0.5px;")
        hdr_layout.addWidget(title_lbl)

        sub_lbl = QLabel(
            "Индивидуальные параметры генерации чекпоинтов SD (семплер, шаги, CFG, Hires, денойз) с автонастройкой по Civitai "
            "и семантическое сопоставление промптов через FastEmbed CPU ONNX.",
            self
        )
        sub_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
        sub_lbl.setWordWrap(True)
        hdr_layout.addWidget(sub_lbl)
        main_layout.addLayout(hdr_layout)

        # 2. Central Splitter (Left: Table | Right: Editor)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(4)

        # --- LEFT PANEL: Table of models ---
        left_widget = QFrame(self)
        left_widget.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        tbl_bar = QHBoxLayout()
        tbl_bar.setSpacing(8)
        lbl_tbl = QLabel("<b>Зарегистрированные модели:</b>", left_widget)
        lbl_tbl.setStyleSheet("color: #38BDF8; font-size: 12px;")
        tbl_bar.addWidget(lbl_tbl)
        tbl_bar.addStretch()

        self.btn_refresh_forge = PushButton("Сканировать Forge", left_widget, icon=FIF.SYNC)
        self.btn_refresh_forge.setToolTip("Запросить чекпоинты из Forge (get_models) и обнаружить новые файлы")
        self.btn_refresh_forge.clicked.connect(lambda: self._start_forge_scan(silent=False))
        tbl_bar.addWidget(self.btn_refresh_forge)

        self.btn_civitai = PushButton("Каталог Civitai", left_widget, icon=FIF.GLOBE)
        self.btn_civitai.setToolTip("Открыть каталог Civitai / Civitai Red для поиска и скачивания моделей")
        self.btn_civitai.clicked.connect(self._open_civitai_catalog)
        tbl_bar.addWidget(self.btn_civitai)

        self.btn_add_model = PushButton("Добавить", left_widget, icon=FIF.ADD)
        self.btn_add_model.setToolTip("Добавить новую модель в таблицу")
        self.btn_add_model.clicked.connect(self._add_empty_model_row)
        tbl_bar.addWidget(self.btn_add_model)
        left_layout.addLayout(tbl_bar)

        self.table = TableWidget(left_widget)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            "Вкл",
            "Название модели",
            "Чекпоинт Forge",
            ""
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)

        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(3, 44)

        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(self._on_table_cell_changed)
        left_layout.addWidget(self.table, stretch=1)

        splitter.addWidget(left_widget)

        # --- RIGHT PANEL: Form Editor inside ScrollArea ---
        right_scroll = QScrollArea(self)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        if right_scroll.viewport():
            right_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            right_scroll.viewport().setStyleSheet("background: transparent;")

        right_container = QFrame()
        right_container.setObjectName("EditorPanel")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(12)

        lbl_edit_title = QLabel("⚙️ <b>Редактор параметров модели</b>", right_container)
        lbl_edit_title.setStyleSheet("color: #00F0FF; font-size: 13px; font-weight: bold;")
        right_layout.addWidget(lbl_edit_title)

        # 1. Model Info Form (Checkpoint, Name, Triggers, Default)
        form_info = QFormLayout()
        form_info.setSpacing(10)
        form_info.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.input_name = LineEdit(right_container)
        self.input_name.setPlaceholderText("Пользовательское название модели")
        self.input_name.textChanged.connect(self._on_input_name_changed)
        form_info.addRow("Название:", self.input_name)

        ckpt_row = QHBoxLayout()
        ckpt_row.setSpacing(8)
        self.combo_checkpoint = FluentEditableComboBox(right_container)
        for cp in self.available_checkpoints:
            self.combo_checkpoint.addItem(cp)
        self.combo_checkpoint.currentTextChanged.connect(self._on_combo_checkpoint_changed)
        ckpt_row.addWidget(self.combo_checkpoint, stretch=1)

        self.btn_autofill_params = PushButton("Автонастройка (Civitai)", right_container, icon=FIF.CLOUD_DOWNLOAD)
        self.btn_autofill_params.setObjectName("AutoFillBtn")
        self.btn_autofill_params.setToolTip("Запросить рекомендованные автором параметры с Civitai или применить Safe Defaults")
        self.btn_autofill_params.clicked.connect(self._on_autofill_params_clicked)
        ckpt_row.addWidget(self.btn_autofill_params)
        form_info.addRow("Чекпоинт Forge:", ckpt_row)

        fp8_row = QHBoxLayout()
        fp8_row.setSpacing(10)
        self.btn_convert_fp8 = PushButton("⚡ Сжать в FP8 (e4m3fn)", right_container)
        self.btn_convert_fp8.setObjectName("FP8ConvertBtn")
        self.btn_convert_fp8.setToolTip("Конвертирует UNet модели SDXL в нативный FP8. Ускоряет загрузку с 90с до 2с и освобождает 4 ГБ VRAM.")
        self.btn_convert_fp8.clicked.connect(self._on_start_fp8_conversion)
        fp8_row.addWidget(self.btn_convert_fp8)

        self.chk_delete_source = CheckBox("Удалить исходный FP16 после успешного сжатия", right_container)
        self.chk_delete_source.setChecked(True)
        self.chk_delete_source.setStyleSheet("color: #94A3B8; font-size: 11px;")
        fp8_row.addWidget(self.chk_delete_source)
        fp8_row.addStretch()
        form_info.addRow("", fp8_row)

        self.progress_fp8 = ProgressBar(right_container)
        self.progress_fp8.setFixedHeight(4)
        self.progress_fp8.setVisible(False)
        self.lbl_fp8_status = QLabel("", right_container)
        self.lbl_fp8_status.setStyleSheet("color: #38BDF8; font-size: 11px;")
        self.lbl_fp8_status.setVisible(False)

        fp8_progress_layout = QVBoxLayout()
        fp8_progress_layout.setSpacing(3)
        fp8_progress_layout.addWidget(self.progress_fp8)
        fp8_progress_layout.addWidget(self.lbl_fp8_status)
        form_info.addRow("", fp8_progress_layout)

        self.chk_is_default = CheckBox("Использовать по умолчанию (для нейтральных запросов без указания стиля)", right_container)
        self.chk_is_default.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: 500;")
        self.chk_is_default.toggled.connect(self._on_is_default_toggled)
        form_info.addRow("", self.chk_is_default)

        self.edit_style_triggers = LineEdit(right_container)
        self.edit_style_triggers.setPlaceholderText("Например: киберпанк, неон, sci-fi, латекс (через запятую)")
        form_info.addRow("Триггеры стиля:", self.edit_style_triggers)

        right_layout.addLayout(form_info)

        # 2. Pipeline Parameters Card
        card_pipeline = QFrame(right_container)
        card_pipeline.setObjectName("ParamCard")
        pipe_layout = QVBoxLayout(card_pipeline)
        pipe_layout.setContentsMargins(12, 10, 12, 10)
        pipe_layout.setSpacing(10)

        lbl_pipe_hdr = QLabel("⚙️ <b>Параметры рендера (Pipeline)</b>", card_pipeline)
        lbl_pipe_hdr.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: bold;")
        pipe_layout.addWidget(lbl_pipe_hdr)

        # Preset selection row
        preset_box = QVBoxLayout()
        preset_box.setSpacing(3)
        preset_box.addWidget(QLabel("Быстрый пресет:", card_pipeline))
        self.combo_presets = ComboBox(card_pipeline)
        self.combo_presets.addItems([
            "Пользовательский",
            "⚡ SDXL Turbo / DMD2 (LCM, 10 шагов, CFG 1.3)",
            "🎨 SDXL Classic / Pony (DPM++ 2M, 28 шагов, CFG 5.0)",
            "📷 SD 1.5 Photoreal (DPM++ SDE, 25 шагов, Hires 1.5x)"
        ])
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        preset_box.addWidget(self.combo_presets)
        pipe_layout.addLayout(preset_box)

        # Architecture & Scheduler row
        arch_sched_row = QHBoxLayout()
        arch_sched_row.setSpacing(14)

        arch_box = QVBoxLayout()
        arch_box.setSpacing(3)
        arch_box.addWidget(QLabel("Архитектура:", card_pipeline))
        self.combo_arch = ComboBox(card_pipeline)
        self.combo_arch.addItems(["SDXL / Pony", "SD 1.5"])
        self.combo_arch.currentIndexChanged.connect(self._on_param_modified)
        arch_box.addWidget(self.combo_arch)
        arch_sched_row.addLayout(arch_box, stretch=1)

        scheduler_box = QVBoxLayout()
        scheduler_box.setSpacing(3)
        scheduler_box.addWidget(QLabel("Планировщик (Scheduler):", card_pipeline))
        self.combo_scheduler = ComboBox(card_pipeline)
        self.combo_scheduler.addItems(["Karras", "SGM Uniform", "Exponential", "Normal", "Simple"])
        self.combo_scheduler.currentIndexChanged.connect(self._on_param_modified)
        scheduler_box.addWidget(self.combo_scheduler)
        arch_sched_row.addLayout(scheduler_box, stretch=1)

        pipe_layout.addLayout(arch_sched_row)

        # Sampler & Upscaler row
        combos_row = QHBoxLayout()
        combos_row.setSpacing(14)

        sampler_box = QVBoxLayout()
        sampler_box.setSpacing(3)
        sampler_box.addWidget(QLabel("Семплер:", card_pipeline))
        self.combo_sampler = ComboBox(card_pipeline)
        self.combo_sampler.addItems([
            "DPM++ 2M Karras",
            "LCM",
            "DPM++ 2M",
            "Euler a",
            "DPM++ SDE Karras",
            "Euler",
            "DDIM"
        ])
        self.combo_sampler.currentIndexChanged.connect(self._on_param_modified)
        sampler_box.addWidget(self.combo_sampler)
        combos_row.addLayout(sampler_box, stretch=1)

        upscaler_box = QVBoxLayout()
        upscaler_box.setSpacing(3)
        upscaler_box.addWidget(QLabel("Hires.fix апскейлер:", card_pipeline))
        self.combo_upscaler = ComboBox(card_pipeline)
        base_upscalers = ["4x-UltraSharp", "R-ESRGAN 4x+ Anime6B", "DAT x2", "Lanczos"]
        for u in base_upscalers:
            self.combo_upscaler.addItem(u)
        upscaler_box.addWidget(self.combo_upscaler)
        combos_row.addLayout(upscaler_box, stretch=1)

        pipe_layout.addLayout(combos_row)

        # Switches row (Hires.fix & ADetailer)
        switches_row = QHBoxLayout()
        switches_row.setSpacing(24)

        hr_box = QHBoxLayout()
        hr_box.setSpacing(8)
        lbl_hr = QLabel("Hires.fix апскейл:", card_pipeline)
        self.switch_enable_hr = SwitchButton(card_pipeline)
        self.switch_enable_hr.setOnText("ВКЛ")
        self.switch_enable_hr.setOffText("ВЫКЛ")
        self.switch_enable_hr.checkedChanged.connect(self._on_param_modified)
        hr_box.addWidget(lbl_hr)
        hr_box.addWidget(self.switch_enable_hr)
        switches_row.addLayout(hr_box)

        ad_box = QHBoxLayout()
        ad_box.setSpacing(8)
        lbl_ad = QLabel("ADetailer (Лица/Руки):", card_pipeline)
        self.switch_enable_ad = SwitchButton(card_pipeline)
        self.switch_enable_ad.setOnText("ВКЛ")
        self.switch_enable_ad.setOffText("ВЫКЛ")
        self.switch_enable_ad.checkedChanged.connect(self._on_param_modified)
        ad_box.addWidget(lbl_ad)
        ad_box.addWidget(self.switch_enable_ad)
        switches_row.addLayout(ad_box)

        switches_row.addStretch()
        pipe_layout.addLayout(switches_row)

        # Sliders grid: Steps & CFG Scale | Denoise & Clip Skip
        grid_sliders = QGridLayout()
        grid_sliders.setHorizontalSpacing(16)
        grid_sliders.setVerticalSpacing(8)

        # Steps
        steps_box = QVBoxLayout()
        steps_box.setSpacing(2)
        h_steps = QHBoxLayout()
        h_steps.addWidget(QLabel("Шаги (Steps):", card_pipeline))
        h_steps.addStretch()
        self.spin_steps = SamplingSpinBox(card_pipeline)
        self.spin_steps.setRange(4, 60)
        self.spin_steps.setValue(25)
        h_steps.addWidget(self.spin_steps)
        steps_box.addLayout(h_steps)

        self.slider_steps = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_steps.setRange(4, 60)
        self.slider_steps.setValue(25)
        self.slider_steps.valueChanged.connect(self._on_steps_slider_changed)
        self.slider_steps.sliderMoved.connect(self._on_steps_slider_changed)
        self.spin_steps.valueChanged.connect(self._on_steps_spin_changed)
        steps_box.addWidget(self.slider_steps)
        grid_sliders.addLayout(steps_box, 0, 0)

        # CFG Scale
        cfg_box = QVBoxLayout()
        cfg_box.setSpacing(2)
        h_cfg = QHBoxLayout()
        h_cfg.addWidget(QLabel("CFG Scale:", card_pipeline))
        h_cfg.addStretch()
        self.spin_cfg = SamplingDoubleSpinBox(card_pipeline)
        self.spin_cfg.setRange(1.0, 15.0)
        self.spin_cfg.setSingleStep(0.5)
        self.spin_cfg.setDecimals(1)
        self.spin_cfg.setValue(6.0)
        h_cfg.addWidget(self.spin_cfg)
        cfg_box.addLayout(h_cfg)

        self.slider_cfg = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_cfg.setRange(10, 150)
        self.slider_cfg.setSingleStep(5)
        self.slider_cfg.setValue(60)
        self.slider_cfg.valueChanged.connect(self._on_cfg_slider_changed)
        self.slider_cfg.sliderMoved.connect(self._on_cfg_slider_changed)
        self.spin_cfg.valueChanged.connect(self._on_cfg_spin_changed)
        cfg_box.addWidget(self.slider_cfg)
        grid_sliders.addLayout(cfg_box, 0, 1)

        # Denoise
        denoise_box = QVBoxLayout()
        denoise_box.setSpacing(2)
        h_denoise = QHBoxLayout()
        h_denoise.addWidget(QLabel("Денойз Hires:", card_pipeline))
        h_denoise.addStretch()
        self.spin_hires_denoise = SamplingDoubleSpinBox(card_pipeline)
        self.spin_hires_denoise.setRange(0.10, 0.70)
        self.spin_hires_denoise.setSingleStep(0.01)
        self.spin_hires_denoise.setDecimals(2)
        self.spin_hires_denoise.setValue(0.32)
        h_denoise.addWidget(self.spin_hires_denoise)
        denoise_box.addLayout(h_denoise)

        self.slider_hires_denoise = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_hires_denoise.setRange(10, 70)
        self.slider_hires_denoise.setSingleStep(1)
        self.slider_hires_denoise.setValue(32)
        self.slider_hires_denoise.valueChanged.connect(self._on_hires_denoise_slider_changed)
        self.slider_hires_denoise.sliderMoved.connect(self._on_hires_denoise_slider_changed)
        self.spin_hires_denoise.valueChanged.connect(self._on_hires_denoise_spin_changed)
        denoise_box.addWidget(self.slider_hires_denoise)
        grid_sliders.addLayout(denoise_box, 1, 0)

        # Clip Skip
        clip_box = QVBoxLayout()
        clip_box.setSpacing(2)
        h_clip = QHBoxLayout()
        h_clip.addWidget(QLabel("Clip Skip:", card_pipeline))
        h_clip.addStretch()
        self.spin_clip_skip = SamplingSpinBox(card_pipeline)
        self.spin_clip_skip.setRange(1, 4)
        self.spin_clip_skip.setValue(1)
        h_clip.addWidget(self.spin_clip_skip)
        clip_box.addLayout(h_clip)

        self.slider_clip_skip = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_clip_skip.setRange(1, 4)
        self.slider_clip_skip.setValue(1)
        self.slider_clip_skip.valueChanged.connect(self._on_clip_skip_slider_changed)
        self.slider_clip_skip.sliderMoved.connect(self._on_clip_skip_slider_changed)
        self.spin_clip_skip.valueChanged.connect(self._on_clip_skip_spin_changed)
        clip_box.addWidget(self.slider_clip_skip)
        grid_sliders.addLayout(clip_box, 1, 1)

        pipe_layout.addLayout(grid_sliders)

        # Negative prompt TextEdit
        pipe_layout.addWidget(QLabel("Негативный промпт по умолчанию:", card_pipeline))
        self.input_negative_prompt = FluentTextEdit(card_pipeline)
        self.input_negative_prompt.setPlaceholderText("worst quality, low quality, bad anatomy, deformed limbs...")
        self.input_negative_prompt.setFixedHeight(54)
        pipe_layout.addWidget(self.input_negative_prompt)

        right_layout.addWidget(card_pipeline)

        # 3. Vector Routing Card
        card_routing = QFrame(right_container)
        card_routing.setObjectName("ParamCard")
        rout_layout = QVBoxLayout(card_routing)
        rout_layout.setContentsMargins(12, 10, 12, 10)
        rout_layout.setSpacing(8)

        lbl_rout_hdr = QLabel("🧠 <b>Семантический роутинг (FastEmbed CPU)</b>", card_routing)
        lbl_rout_hdr.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: bold;")
        rout_layout.addWidget(lbl_rout_hdr)

        rout_layout.addWidget(QLabel("Описание эстетики / стилистики для FastEmbed:", card_routing))
        self.input_desc = FluentTextEdit(card_routing)
        self.input_desc.setPlaceholderText("Семантическое описание стилистики, объектов, эстетики для FastEmbed...")
        self.input_desc.setFixedHeight(64)
        rout_layout.addWidget(self.input_desc)

        rout_layout.addWidget(QLabel("Подсказка для Эларии (llm_guidance, макс. 160 симв. / 8 тегов):", card_routing))
        self.input_guidance = FluentTextEdit(card_routing)
        self.input_guidance.setPlaceholderText("Инструкция для Эларии: целевые теги, ограничения (макс. 160 символов, до 8 тегов)...")
        self.input_guidance.setFixedHeight(54)
        self.input_guidance.textChanged.connect(self._on_input_guidance_changed)
        rout_layout.addWidget(self.input_guidance)

        right_layout.addWidget(card_routing)
        right_layout.addStretch()

        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, stretch=1)

        # 4. Live Vector Routing Test Panel
        test_panel = QFrame(self)
        test_panel.setObjectName("TestPanel")
        test_layout = QVBoxLayout(test_panel)
        test_layout.setContentsMargins(14, 10, 14, 10)
        test_layout.setSpacing(8)

        test_title = QLabel("🧪 <b>Тестовый запрос (проверка векторного сопоставления в реальном времени):</b>", test_panel)
        test_title.setStyleSheet("color: #38BDF8; font-size: 12px;")
        test_layout.addWidget(test_title)

        test_input_row = QHBoxLayout()
        test_input_row.setSpacing(10)

        self.input_test_prompt = SearchLineEdit(test_panel)
        self.input_test_prompt.setPlaceholderText("Введите текст сцены (например: 'cyberpunk girl on neon street' или 'портрет девушки в парке')...")
        self.input_test_prompt.setClearButtonEnabled(True)
        self.input_test_prompt.returnPressed.connect(self._run_test_match)
        self.input_test_prompt.searchSignal.connect(self._run_test_match)
        test_input_row.addWidget(self.input_test_prompt, stretch=1)

        self.btn_test_match = PrimaryPushButton("Проверить выбор", test_panel, icon=FIF.SEARCH)
        self.btn_test_match.setObjectName("TestBtn")
        self.btn_test_match.clicked.connect(self._run_test_match)
        test_input_row.addWidget(self.btn_test_match)

        test_layout.addLayout(test_input_row)

        self.result_badge = RoutingResultBadge(test_panel)
        test_layout.addWidget(self.result_badge)
        self.lbl_test_result = self.result_badge
        self.test_result_label = self.result_badge

        main_layout.addWidget(test_panel)

        # 5. Bottom Actions
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.btn_save = PrimaryPushButton("Сохранить", self, icon=FIF.SAVE)
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.setFixedHeight(34)
        self.btn_save.clicked.connect(self._save_all)
        actions_row.addWidget(self.btn_save)

        actions_row.addStretch()

        self.btn_close = PushButton("Закрыть", self, icon=FIF.CLOSE)
        self.btn_close.setObjectName("CloseBtn")
        self.btn_close.setFixedHeight(34)
        self.btn_close.clicked.connect(self.accept)
        actions_row.addWidget(self.btn_close)

        main_layout.addLayout(actions_row)
        self._update_fp8_button_state()

    # ---------------- Synchronized Slider & SpinBox Slots ---------------- #
    def _on_steps_slider_changed(self, val: int):
        if self.spin_steps.value() != val:
            self.spin_steps.blockSignals(True)
            self.spin_steps.setValue(val)
            self.spin_steps.blockSignals(False)

    def _on_steps_spin_changed(self, val: int):
        if self.slider_steps.value() != val:
            self.slider_steps.blockSignals(True)
            self.slider_steps.setValue(val)
            self.slider_steps.blockSignals(False)

    def _on_cfg_slider_changed(self, val: int):
        target = round(val / 10.0, 1)
        if abs(self.spin_cfg.value() - target) > 0.01:
            self.spin_cfg.blockSignals(True)
            self.spin_cfg.setValue(target)
            self.spin_cfg.blockSignals(False)

    def _on_cfg_spin_changed(self, val: float):
        target = int(round(val * 10))
        if self.slider_cfg.value() != target:
            self.slider_cfg.blockSignals(True)
            self.slider_cfg.setValue(target)
            self.slider_cfg.blockSignals(False)

    def _on_hires_denoise_slider_changed(self, val: int):
        target = round(val / 100.0, 2)
        if abs(self.spin_hires_denoise.value() - target) > 0.001:
            self.spin_hires_denoise.blockSignals(True)
            self.spin_hires_denoise.setValue(target)
            self.spin_hires_denoise.blockSignals(False)

    def _on_hires_denoise_spin_changed(self, val: float):
        target = int(round(val * 100))
        if self.slider_hires_denoise.value() != target:
            self.slider_hires_denoise.blockSignals(True)
            self.slider_hires_denoise.setValue(target)
            self.slider_hires_denoise.blockSignals(False)

    def _on_clip_skip_slider_changed(self, val: int):
        if self.spin_clip_skip.value() != val:
            self.spin_clip_skip.blockSignals(True)
            self.spin_clip_skip.setValue(val)
            self.spin_clip_skip.blockSignals(False)

    def _on_clip_skip_spin_changed(self, val: int):
        if self.slider_clip_skip.value() != val:
            self.slider_clip_skip.blockSignals(True)
            self.slider_clip_skip.setValue(val)
            self.slider_clip_skip.blockSignals(False)

    # ---------------- UI Event Handlers ---------------- #
    def _on_param_modified(self):
        """Сбрасывает выпадающий список пресетов в 'Пользовательский' при ручном изменении настроек."""
        if hasattr(self, "combo_presets") and self.combo_presets.currentIndex() != 0:
            self.combo_presets.blockSignals(True)
            self.combo_presets.setCurrentIndex(0)
            self.combo_presets.blockSignals(False)

    def _on_preset_selected(self, index: int):
        """
        Реактивный слот пресетов генерации в один клик.
        Обновляет параметры формы и синхронизирует их с текущей моделью.
        """
        if index <= 0:
            return

        preset_text = self.combo_presets.currentText()

        if "SDXL Turbo" in preset_text:
            arch = "SDXL / Pony"
            sampler = "LCM"
            scheduler = "Karras"
            steps = 10
            cfg = 1.3
            clip_skip = 2
            enable_hr = False
            enable_ad = False
        elif "SDXL Classic" in preset_text:
            arch = "SDXL / Pony"
            sampler = "DPM++ 2M"
            scheduler = "Karras"
            steps = 28
            cfg = 5.0
            clip_skip = 2
            enable_hr = False
            enable_ad = False
        elif "SD 1.5 Photoreal" in preset_text:
            arch = "SD 1.5"
            sampler = "DPM++ SDE Karras"
            scheduler = "Karras"
            steps = 25
            cfg = 5.5
            clip_skip = 1
            enable_hr = True
            enable_ad = True
        else:
            return

        # Защита сигналов Qt при обновлении формы
        self.combo_arch.blockSignals(True)
        self.combo_sampler.blockSignals(True)
        self.combo_scheduler.blockSignals(True)
        self.spin_steps.blockSignals(True)
        self.slider_steps.blockSignals(True)
        self.spin_cfg.blockSignals(True)
        self.slider_cfg.blockSignals(True)
        self.spin_clip_skip.blockSignals(True)
        self.slider_clip_skip.blockSignals(True)
        self.switch_enable_hr.blockSignals(True)
        self.switch_enable_ad.blockSignals(True)

        idx_arch = self.combo_arch.findText(arch)
        if idx_arch >= 0:
            self.combo_arch.setCurrentIndex(idx_arch)

        idx_s = self.combo_sampler.findText(sampler)
        if idx_s >= 0:
            self.combo_sampler.setCurrentIndex(idx_s)
        else:
            self.combo_sampler.addItem(sampler)
            self.combo_sampler.setCurrentText(sampler)

        idx_sc = self.combo_scheduler.findText(scheduler)
        if idx_sc >= 0:
            self.combo_scheduler.setCurrentIndex(idx_sc)
        else:
            self.combo_scheduler.addItem(scheduler)
            self.combo_scheduler.setCurrentText(scheduler)

        self.spin_steps.setValue(steps)
        self.slider_steps.setValue(steps)

        self.spin_cfg.setValue(cfg)
        self.slider_cfg.setValue(int(round(cfg * 10)))

        self.spin_clip_skip.setValue(clip_skip)
        self.slider_clip_skip.setValue(clip_skip)

        self.switch_enable_hr.setChecked(enable_hr)
        self.switch_enable_ad.setChecked(enable_ad)

        self.combo_arch.blockSignals(False)
        self.combo_sampler.blockSignals(False)
        self.combo_scheduler.blockSignals(False)
        self.spin_steps.blockSignals(False)
        self.slider_steps.blockSignals(False)
        self.spin_cfg.blockSignals(False)
        self.slider_cfg.blockSignals(False)
        self.spin_clip_skip.blockSignals(False)
        self.slider_clip_skip.blockSignals(False)
        self.switch_enable_hr.blockSignals(False)
        self.switch_enable_ad.blockSignals(False)

        if 0 <= self.current_selected_row < len(self.row_widgets):
            self._save_form_to_model(self.current_selected_row)

    def _on_input_name_changed(self, text: str):
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self.row_widgets[self.current_selected_row]["name"] = text
            item = self.table.item(self.current_selected_row, 1)
            if item:
                item.setText(text)

    def _on_combo_checkpoint_changed(self, text: str):
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self.row_widgets[self.current_selected_row]["checkpoint"] = text
            item = self.table.item(self.current_selected_row, 2)
            if item:
                item.setText(text)
        self._update_fp8_button_state()

    def _on_table_cell_changed(self, cur_row: int, cur_col: int, prev_row: int, prev_col: int):
        if cur_row != prev_row and cur_row >= 0:
            if 0 <= prev_row < len(self.row_widgets):
                self._save_form_to_model(prev_row)
            self.current_selected_row = cur_row
            self._load_model_to_form(cur_row)

    def _on_is_default_toggled(self, checked: bool):
        if checked and 0 <= self.current_selected_row < len(self.row_widgets):
            for idx, other_r in enumerate(self.row_widgets):
                if idx != self.current_selected_row:
                    other_r["is_default"] = False

    def _on_input_guidance_changed(self):
        text = self.input_guidance.toPlainText()
        max_len = 160
        max_tags = 8
        trimmed = False

        if len(text) > max_len:
            text = text[:max_len]
            trimmed = True

        if "," in text:
            parts = text.split(",")
            non_empty_tags = [p for p in parts if p.strip()]
            if len(non_empty_tags) > max_tags:
                count = 0
                idx_cut = len(parts)
                for i, p in enumerate(parts):
                    if p.strip():
                        count += 1
                        if count == max_tags:
                            idx_cut = i + 1
                            break
                text = ",".join(parts[:idx_cut])
                trimmed = True

        if trimmed:
            cursor = self.input_guidance.textCursor()
            pos = cursor.position()
            self.input_guidance.blockSignals(True)
            self.input_guidance.setPlainText(text)
            cursor.setPosition(min(pos, len(text)))
            self.input_guidance.setTextCursor(cursor)
            self.input_guidance.blockSignals(False)

    def _save_form_to_model(self, row_idx: int):
        if row_idx < 0 or row_idx >= len(self.row_widgets):
            return
        r = self.row_widgets[row_idx]
        r["name"] = self.input_name.text().strip()
        r["checkpoint"] = self.combo_checkpoint.currentText().strip()
        r["routing_description"] = self.input_desc.toPlainText().strip()
        guidance_val = self.input_guidance.toPlainText().strip()[:160]
        if "," in guidance_val:
            g_parts = [t.strip() for t in guidance_val.split(",") if t.strip()][:8]
            guidance_val = ", ".join(g_parts)
        r["llm_guidance"] = guidance_val
        r["negative_prompt"] = self.input_negative_prompt.toPlainText().strip()

        is_default = self.chk_is_default.isChecked()
        raw_triggers = [t.strip() for t in self.edit_style_triggers.text().split(",") if t.strip()]
        r["is_default"] = is_default
        r["style_triggers"] = raw_triggers

        if is_default:
            for idx, other_r in enumerate(self.row_widgets):
                if idx != row_idx:
                    other_r["is_default"] = False

        arch = "sdxl" if self.combo_arch.currentText().startswith("SDXL") else "sd15"
        r["architecture"] = arch

        existing_pipe = dict(r.get("pipeline", {}))
        r["pipeline"] = {
            **existing_pipe,
            "architecture": arch,
            "sampler": self.combo_sampler.currentText(),
            "scheduler": self.combo_scheduler.currentText(),
            "steps": self.spin_steps.value(),
            "cfg_scale": round(self.spin_cfg.value(), 1),
            "clip_skip": self.spin_clip_skip.value(),
            "hires_upscaler": self.combo_upscaler.currentText(),
            "hires_denoise": round(self.spin_hires_denoise.value(), 2),
            "enable_hr": self.switch_enable_hr.isChecked(),
            "enable_adetailer": self.switch_enable_ad.isChecked(),
            "negative_prompt": self.input_negative_prompt.toPlainText().strip()
        }

    def _load_model_to_form(self, row_idx: int):
        if row_idx < 0 or row_idx >= len(self.row_widgets):
            return
        r = self.row_widgets[row_idx]

        self.input_name.blockSignals(True)
        self.combo_checkpoint.blockSignals(True)
        self.combo_arch.blockSignals(True)
        self.combo_scheduler.blockSignals(True)
        self.combo_presets.blockSignals(True)
        self.combo_sampler.blockSignals(True)
        self.spin_steps.blockSignals(True)
        self.slider_steps.blockSignals(True)
        self.spin_cfg.blockSignals(True)
        self.slider_cfg.blockSignals(True)
        self.spin_clip_skip.blockSignals(True)
        self.slider_clip_skip.blockSignals(True)
        self.combo_upscaler.blockSignals(True)
        self.spin_hires_denoise.blockSignals(True)
        self.slider_hires_denoise.blockSignals(True)
        self.switch_enable_hr.blockSignals(True)
        self.switch_enable_ad.blockSignals(True)
        self.input_negative_prompt.blockSignals(True)
        self.input_desc.blockSignals(True)
        self.input_guidance.blockSignals(True)
        self.chk_is_default.blockSignals(True)
        self.edit_style_triggers.blockSignals(True)

        self.input_name.setText(r.get("name", ""))

        ckpt = r.get("checkpoint", "").strip()
        idx_c = self.combo_checkpoint.findText(ckpt)

        # Фоллбэк 1: поиск эквивалентного чекпоинта по нормализованному стэму (например, FP8-версии)
        if idx_c == -1 and ckpt:
            target_norm = self._normalize_checkpoint_stem(ckpt)
            for i in range(self.combo_checkpoint.count()):
                item_norm = self._normalize_checkpoint_stem(self.combo_checkpoint.itemText(i))
                if target_norm and target_norm == item_norm:
                    idx_c = i
                    break

        # Фоллбэк 2: если файла нет в списке Forge, принудительно добавляем его,
        # чтобы currentIndex гарантированно указывал на текущую модель, а не на предыдущую
        if idx_c == -1 and ckpt:
            self.combo_checkpoint.addItem(ckpt)
            idx_c = self.combo_checkpoint.findText(ckpt)

        if idx_c >= 0:
            self.combo_checkpoint.setCurrentIndex(idx_c)
        else:
            self.combo_checkpoint.setEditText(ckpt)

        pipe = r.get("pipeline", {})

        # Архитектура с фоллбэком на эвристику is_xl_model
        arch_raw = r.get("architecture") or pipe.get("architecture") or ("sdxl" if is_xl_model(r) else "sd15")
        if str(arch_raw).lower().startswith("sdxl") or "pony" in str(arch_raw).lower() or (not r.get("architecture") and is_xl_model(r)):
            idx_arch = self.combo_arch.findText("SDXL / Pony")
        else:
            idx_arch = self.combo_arch.findText("SD 1.5")
        if idx_arch >= 0:
            self.combo_arch.setCurrentIndex(idx_arch)

        # Планировщик
        sched_val = pipe.get("scheduler", "Karras")
        idx_sc = self.combo_scheduler.findText(sched_val)
        if idx_sc >= 0:
            self.combo_scheduler.setCurrentIndex(idx_sc)
        elif sched_val:
            self.combo_scheduler.addItem(sched_val)
            self.combo_scheduler.setCurrentText(sched_val)

        # Семплер
        idx_s = self.combo_sampler.findText(pipe.get("sampler", "DPM++ 2M Karras"))
        if idx_s >= 0:
            self.combo_sampler.setCurrentIndex(idx_s)
        elif pipe.get("sampler"):
            self.combo_sampler.addItem(pipe["sampler"])
            self.combo_sampler.setCurrentText(pipe["sampler"])

        steps_val = pipe.get("steps", 25)
        self.spin_steps.setValue(steps_val)
        self.slider_steps.setValue(steps_val)

        cfg_val = pipe.get("cfg_scale", 6.0)
        self.spin_cfg.setValue(cfg_val)
        self.slider_cfg.setValue(int(round(cfg_val * 10)))

        clip_val = pipe.get("clip_skip", 1)
        self.spin_clip_skip.setValue(clip_val)
        self.slider_clip_skip.setValue(clip_val)

        idx_u = self.combo_upscaler.findText(pipe.get("hires_upscaler", "4x-UltraSharp"))
        if idx_u >= 0:
            self.combo_upscaler.setCurrentIndex(idx_u)
        elif pipe.get("hires_upscaler"):
            self.combo_upscaler.addItem(pipe["hires_upscaler"])
            self.combo_upscaler.setCurrentText(pipe["hires_upscaler"])

        denoise_val = pipe.get("hires_denoise", 0.32)
        self.spin_hires_denoise.setValue(denoise_val)
        self.slider_hires_denoise.setValue(int(round(denoise_val * 100)))

        # Тумблеры Hires.fix и ADetailer
        enable_hr = bool(pipe.get("enable_hr", False))
        self.switch_enable_hr.setChecked(enable_hr)

        enable_ad = bool(pipe.get("enable_adetailer", False))
        self.switch_enable_ad.setChecked(enable_ad)

        # Определение соответствия пресету
        curr_arch = self.combo_arch.currentText()
        curr_samp = self.combo_sampler.currentText()
        curr_sched = self.combo_scheduler.currentText()
        if (curr_arch.startswith("SDXL") and
            curr_samp == "LCM" and
            curr_sched == "Karras" and
            steps_val == 10 and
            abs(cfg_val - 1.3) < 0.05 and
            clip_val == 2 and
            not enable_hr and
            not enable_ad):
            self.combo_presets.setCurrentIndex(1)
        elif (curr_arch.startswith("SDXL") and
              curr_samp == "DPM++ 2M" and
              curr_sched == "Karras" and
              steps_val == 28 and
              abs(cfg_val - 5.0) < 0.05 and
              clip_val == 2 and
              not enable_hr and
              not enable_ad):
            self.combo_presets.setCurrentIndex(2)
        elif (not curr_arch.startswith("SDXL") and
              curr_samp == "DPM++ SDE Karras" and
              curr_sched == "Karras" and
              steps_val == 25 and
              abs(cfg_val - 5.5) < 0.05 and
              clip_val == 1 and
              enable_hr and
              enable_ad):
            self.combo_presets.setCurrentIndex(3)
        else:
            self.combo_presets.setCurrentIndex(0)

        neg = pipe.get("negative_prompt", r.get("negative_prompt", ""))
        self.input_negative_prompt.setPlainText(neg)

        self.input_desc.setPlainText(r.get("routing_description", ""))
        self.input_guidance.setPlainText(r.get("llm_guidance", ""))

        self.chk_is_default.setChecked(bool(r.get("is_default", False)))
        triggers = r.get("style_triggers", [])
        self.edit_style_triggers.setText(", ".join(triggers) if isinstance(triggers, list) else str(triggers))

        self.input_name.blockSignals(False)
        self.combo_checkpoint.blockSignals(False)
        self.combo_arch.blockSignals(False)
        self.combo_scheduler.blockSignals(False)
        self.combo_presets.blockSignals(False)
        self.combo_sampler.blockSignals(False)
        self.spin_steps.blockSignals(False)
        self.slider_steps.blockSignals(False)
        self.spin_cfg.blockSignals(False)
        self.slider_cfg.blockSignals(False)
        self.spin_clip_skip.blockSignals(False)
        self.slider_clip_skip.blockSignals(False)
        self.combo_upscaler.blockSignals(False)
        self.spin_hires_denoise.blockSignals(False)
        self.slider_hires_denoise.blockSignals(False)
        self.switch_enable_hr.blockSignals(False)
        self.switch_enable_ad.blockSignals(False)
        self.input_negative_prompt.blockSignals(False)
        self.input_desc.blockSignals(False)
        self.input_guidance.blockSignals(False)
        self.chk_is_default.blockSignals(False)
        self.edit_style_triggers.blockSignals(False)
        self._update_fp8_button_state()

    def _on_autofill_params_clicked(self):
        """
        Запрашивает параметры Civitai в неблокирующем QThread или выставляет Safe Defaults.
        """
        if self.current_selected_row < 0 or self.current_selected_row >= len(self.row_widgets):
            QMessageBox.warning(self, "Внимание", "Сначала выберите модель в списке.")
            return

        checkpoint = self.combo_checkpoint.currentText().strip()
        if not checkpoint:
            QMessageBox.warning(self, "Внимание", "Сначала выберите файл чекпоинта модели.")
            return

        # Фиксируем целевую строку для защиты от рассинхрона при асинхронном ответе
        self._autofill_target_row = self.current_selected_row

        self.btn_autofill_params.setEnabled(False)
        self.btn_autofill_params.setText("Опрос Civitai...")
        self.btn_autofill_params.setIcon(FIF.SYNC)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        self._civitai_worker = CivitaiParamsWorker(checkpoint, parent=self)
        self._civitai_worker.params_ready.connect(self._on_civitai_params_ready)
        self._civitai_worker.error_occurred.connect(self._on_civitai_params_error)
        self._civitai_worker.start()

    def _on_civitai_params_ready(self, params: dict):
        try:
            target_row = getattr(self, "_autofill_target_row", self.current_selected_row)
            if target_row < 0 or target_row >= len(self.row_widgets):
                return

            is_active = (target_row == self.current_selected_row)

            if is_active:
                # 1. Блокируем сигналы, предотвращая каскадный сброс пресетов и дребезг таблицы
                widgets_to_block = [
                    self.combo_sampler, self.spin_steps, self.slider_steps,
                    self.spin_cfg, self.slider_cfg, self.spin_clip_skip,
                    self.slider_clip_skip, self.combo_upscaler,
                    self.spin_hires_denoise, self.slider_hires_denoise,
                    self.input_negative_prompt
                ]
                for w in widgets_to_block:
                    w.blockSignals(True)

                idx_s = self.combo_sampler.findText(params.get("sampler", "DPM++ 2M Karras"))
                if idx_s >= 0:
                    self.combo_sampler.setCurrentIndex(idx_s)
                elif params.get("sampler"):
                    self.combo_sampler.addItem(params["sampler"])
                    self.combo_sampler.setCurrentText(params["sampler"])

                steps_val = params.get("steps", 25)
                self.spin_steps.setValue(steps_val)
                self.slider_steps.setValue(steps_val)

                cfg_val = params.get("cfg_scale", 6.0)
                self.spin_cfg.setValue(cfg_val)
                self.slider_cfg.setValue(int(round(cfg_val * 10)))

                clip_val = params.get("clip_skip", 1)
                self.spin_clip_skip.setValue(clip_val)
                self.slider_clip_skip.setValue(clip_val)

                idx_u = self.combo_upscaler.findText(params.get("hires_upscaler", "4x-UltraSharp"))
                if idx_u >= 0:
                    self.combo_upscaler.setCurrentIndex(idx_u)
                elif params.get("hires_upscaler"):
                    self.combo_upscaler.addItem(params["hires_upscaler"])
                    self.combo_upscaler.setCurrentText(params["hires_upscaler"])

                denoise_val = params.get("hires_denoise", 0.32)
                self.spin_hires_denoise.setValue(denoise_val)
                self.slider_hires_denoise.setValue(int(round(denoise_val * 100)))

                if params.get("negative_prompt"):
                    self.input_negative_prompt.setPlainText(params["negative_prompt"])

                for w in widgets_to_block:
                    w.blockSignals(False)

                # 2. Сохраняем в модель строки через существующий метод
                self._save_form_to_model(target_row)
            else:
                # Если фокус успел сместиться, обновляем словарь параметров напрямую без затрагивания UI
                r = self.row_widgets[target_row]
                pipe = r.setdefault("pipeline", {})
                for k in ("sampler", "steps", "cfg_scale", "clip_skip", "hires_upscaler", "hires_denoise"):
                    if k in params:
                        pipe[k] = params[k]
                if params.get("negative_prompt"):
                    pipe["negative_prompt"] = params["negative_prompt"]
                    r["negative_prompt"] = params["negative_prompt"]

            # 3. Фиксируем изменения в JSON через существующий метод сохранения
            self._save_models_to_json()

            status = params.get("status", "default")
            msg = params.get("message", "Параметры обновлены.")
            if status == "success":
                QMessageBox.information(self, "Успех", f"✅ {msg}")
            else:
                QMessageBox.warning(self, "Внимание (Параметры по умолчанию)", f"⚠️ {msg}")

        except Exception as e:
            logger.error(f"[ModelsManagerDialog] Сбой автонастройки: {e}", exc_info=True)
            QMessageBox.warning(self, "Ошибка", f"Произошла ошибка: {e}\nПрименены стандартные параметры.")
        finally:
            self._civitai_worker = None
            QApplication.restoreOverrideCursor()
            self.btn_autofill_params.setEnabled(True)
            self.btn_autofill_params.setText("Автонастройка (Civitai)")
            self.btn_autofill_params.setIcon(FIF.CLOUD_DOWNLOAD)

    def _on_civitai_params_error(self, err_msg: str):
        try:
            logger.error(f"[ModelsManagerDialog] Сбой автонастройки: {err_msg}")
            QMessageBox.warning(self, "Ошибка", f"Произошла ошибка: {err_msg}\nПрименены стандартные параметры.")
        finally:
            self._civitai_worker = None
            QApplication.restoreOverrideCursor()
            self.btn_autofill_params.setEnabled(True)
            self.btn_autofill_params.setText("Автонастройка (Civitai)")
            self.btn_autofill_params.setIcon(FIF.CLOUD_DOWNLOAD)

    # ---------------- FP8 Conversion & Helpers ---------------- #
    def _resolve_converter_script(self) -> Optional[Path]:
        """
        Ищет скрипт конвертации SDXL в FP8 по кандидатам относительно корня проекта.
        """
        from config import BASE_DIR
        candidates = [
            BASE_DIR / "models" / "convert_sdxl_to_fp8.py",
            BASE_DIR / "scripts" / "convert_sdxl_to_fp8.py"
        ]
        for c in candidates:
            if c.is_file():
                return c.resolve()
        return None

    @staticmethod
    def _verify_python(py_path: str) -> bool:
        if not py_path or not Path(py_path).is_file():
            return False
        try:
            res = subprocess.run(
                [str(py_path), "-c", "import torch, safetensors; print('OK')"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return res.returncode == 0 and "OK" in res.stdout
        except Exception:
            return False

    def _resolve_torch_python(self) -> Optional[str]:
        if getattr(self, "_cached_torch_python", None) and Path(self._cached_torch_python).is_file():
            return self._cached_torch_python
        """
        Динамически определяет путь к интерпретатору с PyTorch и safetensors:
        - Проверяет forge_launcher.bat / launcher из настроек Forge.
        - Проверяет sys.executable.
        - Если не найдено — запрашивает через QFileDialog.
        """
        import sys
        from config import FORGE_LAUNCHER_PATH, FORGE_ROOT_DIR

        # 1. Поиск в окружении Forge
        candidates: List[Path] = []
        if FORGE_LAUNCHER_PATH.exists():
            launcher_dir = FORGE_LAUNCHER_PATH.parent
            candidates.extend([
                launcher_dir / "webui_forge_cu121_torch231" / "system" / "python" / "python.exe",
                launcher_dir / "system" / "python" / "python.exe",
                launcher_dir / "python" / "python.exe",
                launcher_dir / "venv" / "Scripts" / "python.exe",
            ])
            try:
                content = FORGE_LAUNCHER_PATH.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    if "set PYTHON=" in line:
                        val = line.split("set PYTHON=", 1)[1].strip().strip('"')
                        val_resolved = val.replace("%SYSTEM%", str(launcher_dir / "webui_forge_cu121_torch231" / "system"))
                        val_resolved = val_resolved.replace("%FORGE%", str(launcher_dir / "webui_forge_cu121_torch231"))
                        val_resolved = val_resolved.replace("%ROOT%", str(launcher_dir))
                        val_resolved = val_resolved.replace("%~dp0", str(launcher_dir) + "\\")
                        candidates.insert(0, Path(val_resolved))
            except Exception as e:
                logger.debug(f"Не удалось распарсить {FORGE_LAUNCHER_PATH}: {e}")

        candidates.extend([
            FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "system" / "python" / "python.exe",
            FORGE_ROOT_DIR / "system" / "python" / "python.exe",
            FORGE_ROOT_DIR / "python" / "python.exe",
            FORGE_ROOT_DIR / "venv" / "Scripts" / "python.exe",
        ])

        for c in candidates:
            if c.is_file() and self._verify_python(str(c)):
                self._cached_torch_python = str(c.resolve())
                return self._cached_torch_python

        # 2. Проверка текущего sys.executable
        if self._verify_python(sys.executable):
            return sys.executable

        # 3. Диалог ручного выбора пользователем
        from PyQt6.QtWidgets import QFileDialog
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите интерпретатор Python с PyTorch и safetensors",
            str(FORGE_ROOT_DIR if FORGE_ROOT_DIR.exists() else Path.home()),
            "Python Executable (python.exe);;All Files (*.*)"
        )
        if chosen and self._verify_python(chosen):
            return chosen

        return None

    def _resolve_selected_checkpoint_path(self, ckpt_name: str) -> Optional[Path]:
        """
        Находит полный путь к чекпоинту: сначала мгновенно на локальном диске (0 мс),
        затем через кеш installed_checkpoints без блокировки GUI.
        """
        if not ckpt_name:
            return None

        p_raw = Path(ckpt_name)
        if p_raw.is_absolute() and p_raw.is_file():
            return p_raw.resolve()

        clean_name = p_raw.name.lower()

        # 0. Мгновенная локальная проверка на диске (0 мс, исключает сетевые задержки HTTP)
        from config import FORGE_ROOT_DIR
        local_dirs = [
            FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion",
            FORGE_ROOT_DIR / "webui" / "models" / "Stable-diffusion",
            FORGE_ROOT_DIR / "models" / "Stable-diffusion",
            Path("C:/neyroGPD/webui_forge_cu121_torch231/webui/models/Stable-diffusion")
        ]
        for ld in local_dirs:
            if ld and ld.is_dir():
                direct_file = ld / p_raw.name
                if direct_file.is_file():
                    return direct_file.resolve()

        # 1. Запрос списка установленных чекпоинтов через ForgeClient
        try:
            installed = self.forge_client.get_installed_checkpoints() or []
        except Exception:
            installed = []

        for item in installed:
            p = Path(item)
            if p.name.lower() == clean_name:
                if p.is_absolute() and p.is_file():
                    return p
                elif p.is_file():
                    return p.resolve()

        # 2. Поиск в моделях Forge API (get_models)
        try:
            raw_models = self.forge_client.get_models() or []
        except Exception:
            raw_models = []

        for item in raw_models:
            if isinstance(item, dict):
                fn = item.get("filename")
                if fn:
                    p = Path(fn)
                    if p.name.lower() == clean_name and p.is_file():
                        return p

        # 3. Поиск по локальным каталогам Forge
        from config import FORGE_ROOT_DIR, BASE_DIR
        local_dirs = [
            FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion",
            FORGE_ROOT_DIR / "webui" / "models" / "Stable-diffusion",
            FORGE_ROOT_DIR / "models" / "Stable-diffusion",
            BASE_DIR / "models"
        ]
        for d in local_dirs:
            candidate = d / Path(ckpt_name).name
            if candidate.is_file():
                return candidate.resolve()

        # 4. Если сам ckpt_name является валидным файлом
        p_raw = Path(ckpt_name)
        if p_raw.is_absolute() and p_raw.is_file():
            return p_raw.resolve()

        return None

    def _update_fp8_button_state(self):
        """
        Обновляет доступность кнопки конвертации FP8 (O(1) без блокирующего сетевого и дискового I/O).
        Кнопка активна, только если выбранный чекпоинт является файлом .safetensors
        и еще не содержит явных маркеров квантования.
        """
        if not hasattr(self, "btn_convert_fp8"):
            return
        if self._fp8_worker is not None and self._fp8_worker.isRunning():
            self.btn_convert_fp8.setEnabled(False)
            return

        ckpt = self.combo_checkpoint.currentText().strip()
        is_safetensors = ckpt.lower().endswith(".safetensors")
        is_already_fp8 = any(k in ckpt.lower() for k in ("_fp8", "-fp8", "e4m3fn", "_quant"))

        self.btn_convert_fp8.setEnabled(bool(is_safetensors and not is_already_fp8))
        self.btn_convert_fp8.setToolTip("Конвертирует UNet модели SDXL в нативный FP8. Освобождает 4 ГБ VRAM.")

    def _on_start_fp8_conversion(self):
        """
        Запускает асинхронное сжатие выбранного чекпоинта SDXL в нативный FP8.
        """
        if self._fp8_worker is not None and self._fp8_worker.isRunning():
            return

        ckpt_name = self.combo_checkpoint.currentText().strip()
        if not ckpt_name:
            InfoBar.warning("Внимание", "Сначала выберите файл чекпоинта.", parent=self, position=InfoBarPosition.TOP)
            return

        # 1. Разрешение скрипта конвертера
        script_path = self._resolve_converter_script()
        if not script_path:
            InfoBar.error("Ошибка", "Скрипт convert_sdxl_to_fp8.py не найден в папках models/ или scripts/.", parent=self, position=InfoBarPosition.TOP)
            return

        # 2. Разрешение пути к исходному файлу чекпоинта
        ckpt_path = self._resolve_selected_checkpoint_path(ckpt_name)
        if not ckpt_path or not ckpt_path.is_file():
            InfoBar.error("Ошибка", f"Файл чекпоинта '{ckpt_name}' не найден на диске.", parent=self, position=InfoBarPosition.TOP)
            return

        # Pre-flight Size Guard: проверка на случай случайного повторного сжатия уже компактной модели
        try:
            file_size_gb = ckpt_path.stat().st_size / (1024 ** 3)
            if file_size_gb < 4.5:
                reply = QMessageBox.question(
                    self,
                    "Модель уже оптимизирована",
                    f"Чекпоинт '{ckpt_name}' уже имеет компактный размер ({file_size_gb:.1f} ГБ, типичный SDXL FP16 ~6.6 ГБ).\n\n"
                    f"Похоже, модель уже сжата в FP8.\nВы действительно хотите запустить сжатие повторно?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        except Exception as e:
            logger.debug(f"Pre-flight size check error: {e}")

        # 3. Разрешение интерпретатора Python с PyTorch
        python_exe = self._resolve_torch_python()
        if not python_exe:
            InfoBar.warning("Внимание", "Не найден интерпретатор Python с модулями PyTorch и safetensors.", parent=self, position=InfoBarPosition.TOP)
            return

        # Фиксация контекста задачи конвертации
        self._fp8_target_row = self.current_selected_row
        self._fp8_source_path = ckpt_path
        self._fp8_source_name = ckpt_name

        # Блокировка UI для исключения гонок фокуса и случайных переключений
        self.btn_convert_fp8.setEnabled(False)
        self.btn_convert_fp8.setText("⚡ Сжатие в процессе...")
        self.combo_checkpoint.setEnabled(False)
        self.table.setEnabled(False)
        if hasattr(self, "btn_add_model"):
            self.btn_add_model.setEnabled(False)
        if hasattr(self, "btn_remove_model"):
            self.btn_remove_model.setEnabled(False)
        self.progress_fp8.setVisible(True)
        self.progress_fp8.setRange(0, 0)
        self.lbl_fp8_status.setVisible(True)
        self.lbl_fp8_status.setText("Запуск конвертера FP8 (e4m3fn)...")
        QApplication.processEvents()

        self._fp8_worker = FP8ConvertWorker(python_exe, script_path, ckpt_path, parent=self)
        self._fp8_worker.progress_updated.connect(self._on_fp8_progress)
        self._fp8_worker.conversion_finished.connect(self._on_fp8_finished)
        self._fp8_worker.conversion_failed.connect(self._on_fp8_failed)
        self._fp8_worker.start()

    def _on_fp8_progress(self, msg: str):
        """Отображает текущий прогресс конвертации в UI."""
        self.lbl_fp8_status.setText(msg)
        match = re.search(r"(\d+)/(\d+)", msg)
        if match:
            cur, total = int(match.group(1)), int(match.group(2))
            self.progress_fp8.setRange(0, total)
            self.progress_fp8.setValue(cur)

    def _on_fp8_finished(self, new_file_path: str):
        """Обрабатывает успешное завершение конвертации в FP8 с поддержкой Atomic In-Place Swap."""
        new_path = Path(new_file_path)
        new_name = new_path.name

        source_path = getattr(self, "_fp8_source_path", None)
        target_row = getattr(self, "_fp8_target_row", self.current_selected_row)
        if not source_path:
            orig_name = getattr(self, "_fp8_source_name", "") or self.combo_checkpoint.currentText().strip()
            source_path = self._resolve_selected_checkpoint_path(orig_name)

        # 1. Валидация целостности созданного FP8 файла
        if not (new_path.is_file() and new_path.stat().st_size > 0):
            logger.error(f"[FP8] Сжатый файл отсутствует или пуст: {new_path}")
            self._on_fp8_failed("Созданный файл поврежден или имеет нулевой размер.")
            return

        final_checkpoint_name = new_name

        # 2. Обработка режима перезаписи (Atomic In-Place Swap) или сохранения исходника
        if self.chk_delete_source.isChecked() and source_path and source_path.is_file():
            # Security Guard: Проверка родства имен перед удалением (Name Affinity Guard)
            src_norm = self._normalize_checkpoint_stem(source_path.name)
            new_norm = self._normalize_checkpoint_stem(new_path.name)
            if src_norm != new_norm:
                logger.critical(
                    f"[FP8 Security Guard] ЗАПРЕЩЕНО удаление/замена: имена стэмов не совпадают! "
                    f"Исходник: '{source_path.name}' ({src_norm}) != Новый: '{new_path.name}' ({new_norm})"
                )
                InfoBar.error(
                    "Ошибка безопасности",
                    "Обнаружено несоответствие имен исходного и сжатого файла. Исходник сохранен.",
                    parent=self,
                    position=InfoBarPosition.TOP
                )
            else:
                # Безопасный Atomic In-Place Swap: удаляем старый и ставим новый на его место с исходным именем
                try:
                    deleted = False
                    try:
                        import send2trash
                        send2trash.send2trash(str(source_path))
                        deleted = True
                    except (ImportError, Exception):
                        try:
                            os.remove(source_path)
                            deleted = True
                        except Exception as e:
                            logger.warning(f"Не удалось удалить исходный файл {source_path}: {e}")

                    if deleted:
                        if new_path.resolve() != source_path.resolve():
                            import os as _os
                            _os.replace(new_path, source_path)
                            final_checkpoint_name = source_path.name
                            logger.info(f"[FP8] Файл успешно заменен с сохранением исходного имени: {final_checkpoint_name}")
                except Exception as e:
                    logger.error(f"[FP8] Ошибка при замене исходного файла: {e}", exc_info=True)

        # 3. Вызов refresh_checkpoints в Forge
        try:
            self.forge_client.refresh_checkpoints()
        except Exception as e:
            logger.debug(f"refresh_checkpoints error: {e}")

        # 4. Обновление списков доступных чекпоинтов
        if final_checkpoint_name not in self.available_checkpoints:
            self.available_checkpoints.append(final_checkpoint_name)

        self.combo_checkpoint.blockSignals(True)
        if self.combo_checkpoint.findText(final_checkpoint_name) == -1:
            self.combo_checkpoint.addItem(final_checkpoint_name)
        idx_final = self.combo_checkpoint.findText(final_checkpoint_name)
        if idx_final >= 0:
            self.combo_checkpoint.setCurrentIndex(idx_final)
        else:
            self.combo_checkpoint.setEditText(final_checkpoint_name)
        self.combo_checkpoint.blockSignals(False)

        # 5. Обновление записи строго в строке target_row (защита от рассинхрона фокуса)
        if 0 <= target_row < len(self.row_widgets):
            r = self.row_widgets[target_row]
            r["checkpoint"] = final_checkpoint_name
            item = self.table.item(target_row, 2)
            if item:
                item.setText(final_checkpoint_name)

        # Фиксация изменений в data/art_director_models.json
        if hasattr(self, "_save_models_to_json"):
            self._save_models_to_json()

        # 6. Автоматическое применение пресета SDXL (Turbo или Classic)
        turbo_markers = ("turbo", "dmd", "lightning", "hyper", "lcm")
        is_turbo = any(m in final_checkpoint_name.lower() or m in self.input_name.text().lower() for m in turbo_markers)
        if is_turbo:
            self.combo_presets.setCurrentIndex(1)
        else:
            self.combo_presets.setCurrentIndex(2)

        # 7. Восстановление состояния UI и разблокировка контролов
        self.table.setEnabled(True)
        self.combo_checkpoint.setEnabled(True)
        if hasattr(self, "btn_add_model"):
            self.btn_add_model.setEnabled(True)
        if hasattr(self, "btn_remove_model"):
            self.btn_remove_model.setEnabled(True)
        self.btn_convert_fp8.setText("⚡ Сжать в FP8 (e4m3fn)")
        self._update_fp8_button_state()
        self.progress_fp8.setVisible(False)
        self.lbl_fp8_status.setVisible(False)
        self._fp8_worker = None
        self._fp8_source_path = None
        self._fp8_source_name = ""
        self._fp8_target_row = -1

        InfoBar.success(
            "Конвертация завершена",
            f"Чекпоинт успешно оптимизирован в FP8: {final_checkpoint_name}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000
        )

    def _on_fp8_failed(self, err_msg: str):
        """Обрабатывает сбой конвертации в FP8 с полной разблокировкой UI."""
        self.table.setEnabled(True)
        self.combo_checkpoint.setEnabled(True)
        if hasattr(self, "btn_add_model"):
            self.btn_add_model.setEnabled(True)
        if hasattr(self, "btn_remove_model"):
            self.btn_remove_model.setEnabled(True)
        self.btn_convert_fp8.setText("⚡ Сжать в FP8 (e4m3fn)")
        self._update_fp8_button_state()
        self.progress_fp8.setVisible(False)
        self.lbl_fp8_status.setVisible(False)
        self._fp8_worker = None
        self._fp8_source_path = None
        self._fp8_source_name = ""
        self._fp8_target_row = -1

        logger.error(f"[ModelsManagerDialog] Сбой сжатия FP8:\n{err_msg}")
        try:
            from qfluentwidgets import MessageBox
            w = MessageBox(
                "Сбой сжатия FP8",
                f"Не удалось сжать чекпоинт в FP8.\n\nДетали ошибки:\n{err_msg}\n\n(Полный лог сохранен в logs/neonroute.log)",
                self
            )
            w.yesButton.setText("Закрыть")
            w.cancelButton.setVisible(False)
            w.exec()
        except Exception:
            InfoBar.error(
                "Сбой сжатия FP8",
                f"Ошибка: {err_msg}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=8000
            )

    def _start_forge_scan(self, silent: bool = False):
        """Запускает фоновое сканирование Forge без блокировки главного потока интерфейса."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return

        self._scan_silent = silent
        if not silent:
            self.btn_refresh_forge.setEnabled(False)
            self.btn_refresh_forge.setText("Опрос Forge...")
            self.btn_refresh_forge.setIcon(FIF.SYNC)

        self._scan_worker = ForgeScanWorker(self.forge_client, parent=self)
        self._scan_worker.scan_completed.connect(self._on_forge_scan_completed)
        self._scan_worker.start()

    def _on_forge_scan_completed(self, raw_models: list, installed_list: list, upscalers: list):
        if not getattr(self, "_scan_silent", True):
            self.btn_refresh_forge.setEnabled(True)
            self.btn_refresh_forge.setText("Сканировать Forge")
            self.btn_refresh_forge.setIcon(FIF.SYNC)

        # 1. Обновление списка апскейлеров новыми данными из Forge
        if upscalers:
            curr_up = self.combo_upscaler.currentText()
            self.combo_upscaler.blockSignals(True)
            for u in upscalers:
                if self.combo_upscaler.findText(u) == -1:
                    self.combo_upscaler.addItem(u)
            idx_up = self.combo_upscaler.findText(curr_up)
            if idx_up >= 0:
                self.combo_upscaler.setCurrentIndex(idx_up)
            self.combo_upscaler.blockSignals(False)

        # 2. Сбор и сопоставление чекпоинтов
        found_checkpoints: List[str] = []
        if raw_models:
            for item in raw_models:
                if isinstance(item, dict):
                    fn = item.get("filename")
                    title = item.get("title")
                    model_name = item.get("model_name")
                    name = Path(fn).name if fn else (title or model_name)
                    if name and name not in found_checkpoints:
                        found_checkpoints.append(name)

        if installed_list:
            for cp in installed_list:
                cp_name = Path(cp).name
                if cp_name and cp_name not in found_checkpoints:
                    found_checkpoints.append(cp_name)

        if not found_checkpoints:
            found_checkpoints = list(self.available_checkpoints)

        self.available_checkpoints = found_checkpoints

        # 3. Обновление комбобокса чекпоинтов в форме редактора
        curr = self.combo_checkpoint.currentText().strip()
        self.combo_checkpoint.blockSignals(True)
        self.combo_checkpoint.clear()
        for cp in self.available_checkpoints:
            self.combo_checkpoint.addItem(cp)
        if curr and self.combo_checkpoint.findText(curr) == -1:
            self.combo_checkpoint.addItem(curr)
        idx = self.combo_checkpoint.findText(curr)
        if idx >= 0:
            self.combo_checkpoint.setCurrentIndex(idx)
        elif curr:
            self.combo_checkpoint.setEditText(curr)
        self.combo_checkpoint.blockSignals(False)

        # 4. Авторегистрация новых чекпоинтов
        existing_ckpts = {Path(r.get("checkpoint", "")).name.lower() for r in self.row_widgets}
        new_added = 0
        if raw_models or installed_list:
            for cp in found_checkpoints:
                cp_norm = self._normalize_checkpoint_stem(cp)

                # Проверяем, есть ли уже модель с таким нормализованным именем в существующих карточках
                matched_row = None
                for r in self.row_widgets:
                    existing_norm = self._normalize_checkpoint_stem(r.get("checkpoint", ""))
                    existing_id_norm = self._normalize_checkpoint_stem(r.get("id", "").replace("auto_", ""))
                    if cp_norm in (existing_norm, existing_id_norm):
                        matched_row = r
                        break

                if matched_row is not None:
                    # Модель уже существует в базе. Если на диске появился FP8 или актуальный файл,
                    # тихо обновляем путь к чекпоинту без сброса пользовательских настроек (шаги, CFG, промпты)
                    if matched_row.get("checkpoint") != cp:
                        # Обновляем только если текущий файл физически отсутствует или cp является fp8-версией
                        matched_row["checkpoint"] = cp
                    continue

                if Path(cp).name.lower() not in existing_ckpts:
                    stem = Path(cp).stem
                    is_xl = is_xl_model(cp)
                    if is_xl:
                        new_model_data = {
                            "id": f"auto_{re.sub(r'[^a-zA-Z0-9_]', '_', stem.lower()).strip('_')}",
                            "name": stem,
                            "architecture": "sdxl",
                            "checkpoint": cp,
                            "routing_description": "Новая SDXL модель (требуется настройка описания для роутинга)",
                            "llm_guidance": "",
                            "negative_prompt": self._get_default_negative(cp, stem),
                            "active": True,
                            "pipeline": {
                                "architecture": "sdxl",
                                "sampler": "LCM",
                                "scheduler": "Karras",
                                "steps": 10,
                                "cfg_scale": 1.3,
                                "clip_skip": 2,
                                "hires_upscaler": "4x-UltraSharp",
                                "hires_denoise": 0.32,
                                "enable_hr": False,
                                "enable_adetailer": False,
                                "negative_prompt": self._get_default_negative(cp, stem)
                            }
                        }
                    else:
                        new_model_data = {
                            "id": f"auto_{re.sub(r'[^a-zA-Z0-9_]', '_', stem.lower()).strip('_')}",
                            "name": stem,
                            "architecture": "sd15",
                            "checkpoint": cp,
                            "routing_description": "Новая модель (требуется настройка описания для роутинга)",
                            "llm_guidance": "",
                            "negative_prompt": self._get_default_negative(cp, stem),
                            "active": True,
                            "pipeline": {
                                "architecture": "sd15",
                                "sampler": "DPM++ 2M Karras",
                                "scheduler": "Karras",
                                "steps": 25,
                                "cfg_scale": 6.0,
                                "clip_skip": 1,
                                "hires_upscaler": "4x-UltraSharp",
                                "hires_denoise": 0.32,
                                "enable_hr": False,
                                "enable_adetailer": False,
                                "negative_prompt": self._get_default_negative(cp, stem)
                            }
                        }
                    self._add_model_row(new_model_data)
                    existing_ckpts.add(Path(cp).name.lower())
                    new_added += 1

        if new_added > 0:
            self._save_models_to_json()

        if not getattr(self, "_scan_silent", True):
            msg = f"Успешно получено {len(found_checkpoints)} чекпоинтов из SD Forge."
            if new_added > 0:
                msg += f"\n\nАвтоматически добавлено новых моделей: {new_added}."
            QMessageBox.information(self, "Сканирование чекпоинтов Forge", msg)

        self._scan_worker = None

    def _scan_forge_checkpoints(self, silent: bool = False):
        """Псевдоним для обратной совместимости."""
        self._start_forge_scan(silent=silent)

    def _open_civitai_catalog(self):
        """Открывает нативный браузер каталога Civitai / Civitai Red."""
        from ui.dialogs.civitai_catalog_dialog import CivitaiCatalogDialog
        target_dir = None
        if self.available_checkpoints:
            installed_list = self.forge_client.get_installed_checkpoints()
            if installed_list:
                target_dir = Path(installed_list[0]).parent
        dlg = CivitaiCatalogDialog(target_dir=target_dir, parent=self)
        dlg.model_installed.connect(self._on_civitai_model_installed)
        try:
            dlg.exec()
        finally:
            try:
                dlg.model_installed.disconnect(self._on_civitai_model_installed)
            except Exception:
                pass

    def _on_civitai_model_installed(self, model_info: dict):
        """Автоматически регистрирует скачанную с Civitai модель в списке роутера."""
        ckpt_name = model_info.get("filename", "")
        if ckpt_name and ckpt_name not in self.available_checkpoints:
            self.available_checkpoints.append(ckpt_name)
            self.combo_checkpoint.addItem(ckpt_name)

        is_xl = is_xl_model(ckpt_name)
        arch = "sdxl" if is_xl else "sd15"
        new_data = {
            "id": f"civitai_{model_info.get('id', uuid.uuid4().hex[:6])}",
            "name": model_info.get("name", "Новая модель"),
            "architecture": arch,
            "checkpoint": ckpt_name,
            "routing_description": model_info.get("description", ""),
            "llm_guidance": model_info.get("guidance", ""),
            "negative_prompt": self._get_default_negative(ckpt_name, model_info.get("name", "")),
            "active": True,
            "triggers": model_info.get("triggers", []),
            "pipeline": {
                "architecture": arch,
                "sampler": model_info.get("sampler", "LCM" if is_xl else "DPM++ 2M Karras"),
                "scheduler": model_info.get("scheduler", "Karras"),
                "steps": model_info.get("steps", 10 if is_xl else 25),
                "cfg_scale": model_info.get("cfg_scale", 1.3 if is_xl else 6.0),
                "clip_skip": model_info.get("clip_skip", 2 if is_xl else 1),
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "enable_hr": False,
                "enable_adetailer": False,
                "negative_prompt": self._get_default_negative(ckpt_name, model_info.get("name", ""))
            }
        }
        self._add_model_row(new_data)
        new_row = self.table.rowCount() - 1
        self.table.selectRow(new_row)
        self.current_selected_row = new_row
        self._load_model_to_form(new_row)
        self.table.scrollToBottom()

    def _populate_table(self):
        """Заполняет таблицу моделями и активирует первую строку."""
        self.table.setRowCount(0)
        self.row_widgets.clear()
        for m in self.models:
            self._add_model_row(m)

        if self.table.rowCount() > 0:
            self.table.selectRow(0)
            self.current_selected_row = 0
            self._load_model_to_form(0)

    def _add_model_row(self, model_data: Dict[str, Any]):
        """Добавляет строку модели с современными элементами управления."""
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self.table.setRowHeight(row_idx, 40)

        model_id = model_data.get("id") or f"model_{uuid.uuid4().hex[:6]}"
        is_active = model_data.get("active", model_data.get("is_active", True))
        name = model_data.get("name", "")
        checkpoint = model_data.get("checkpoint", "")
        desc = model_data.get("routing_description") or model_data.get("description", "")
        guidance = model_data.get("llm_guidance", "")
        neg_val = model_data.get("negative_prompt", "").strip() or self._get_default_negative(checkpoint, model_id)

        pipeline_data = model_data.get("pipeline")
        if not pipeline_data or not isinstance(pipeline_data, dict):
            pipeline_data = {
                "sampler": "DPM++ 2M Karras",
                "steps": 25,
                "cfg_scale": 6.0,
                "clip_skip": 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "negative_prompt": neg_val
            }

        # Col 0: Active in auto-router CheckBox
        chk_container = QWidget()
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(6, 0, 6, 0)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk = CheckBox(chk_container)
        chk.setChecked(bool(is_active))
        chk.setToolTip("Включить модель в автоматический семантический роутер")
        chk_layout.addWidget(chk)
        self.table.setCellWidget(row_idx, 0, chk_container)

        # Col 1: Model Name
        from PyQt6.QtWidgets import QTableWidgetItem
        item_name = QTableWidgetItem(name)
        item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row_idx, 1, item_name)

        # Col 2: Checkpoint
        item_ckpt = QTableWidgetItem(checkpoint)
        item_ckpt.setFlags(item_ckpt.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row_idx, 2, item_ckpt)

        # Col 3: Delete Button (TransparentToolButton with red hover)
        del_container = QWidget()
        del_layout = QHBoxLayout(del_container)
        del_layout.setContentsMargins(4, 0, 4, 0)
        del_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_del = TransparentToolButton(FIF.DELETE, del_container)
        btn_del.setToolTip("Удалить модель из реестра")
        btn_del.setFixedSize(28, 28)
        btn_del.setStyleSheet("""
            TransparentToolButton {
                border-radius: 4px;
                padding: 2px;
            }
            TransparentToolButton:hover {
                background-color: rgba(239, 68, 68, 0.25);
            }
        """)
        btn_del.clicked.connect(lambda _, r_id=model_id: self._remove_model_by_id(r_id))
        del_layout.addWidget(btn_del)
        self.table.setCellWidget(row_idx, 3, del_container)

        is_default = bool(model_data.get("is_default", False))
        style_triggers = model_data.get("style_triggers", [])
        if isinstance(style_triggers, str):
            style_triggers = [t.strip() for t in style_triggers.split(",") if t.strip()]
        elif isinstance(style_triggers, list):
            style_triggers = [str(t).strip() for t in style_triggers if str(t).strip()]
        else:
            style_triggers = []

        arch = model_data.get("architecture") or pipeline_data.get("architecture") or ("sdxl" if is_xl_model(model_data) else "sd15")
        if "architecture" not in pipeline_data:
            pipeline_data["architecture"] = arch

        row_info = {
            "id": model_id,
            "chk": chk,
            "name": name,
            "architecture": arch,
            "checkpoint": checkpoint,
            "routing_description": desc,
            "llm_guidance": guidance,
            "negative_prompt": neg_val,
            "is_default": is_default,
            "style_triggers": style_triggers,
            "pipeline": pipeline_data,
            "triggers": model_data.get("triggers", []),
            "item_name": item_name,
            "item_ckpt": item_ckpt
        }
        self.row_widgets.append(row_info)

    def _add_empty_model_row(self):
        """Добавляет пустой шаблон новой модели."""
        if self.current_selected_row >= 0:
            self._save_form_to_model(self.current_selected_row)

        new_ckpt = self.available_checkpoints[0] if self.available_checkpoints else ""
        is_xl = is_xl_model(new_ckpt) if new_ckpt else False
        arch = "sdxl" if is_xl else "sd15"
        new_data = {
            "id": f"model_{uuid.uuid4().hex[:6]}",
            "name": "Новая модель",
            "architecture": arch,
            "checkpoint": new_ckpt,
            "routing_description": "",
            "llm_guidance": "",
            "negative_prompt": self._get_default_negative(new_ckpt, ""),
            "is_default": False,
            "style_triggers": [],
            "active": True,
            "pipeline": {
                "architecture": arch,
                "sampler": "LCM" if is_xl else "DPM++ 2M Karras",
                "scheduler": "Karras",
                "steps": 10 if is_xl else 25,
                "cfg_scale": 1.3 if is_xl else 6.0,
                "clip_skip": 2 if is_xl else 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "enable_hr": False,
                "enable_adetailer": False,
                "negative_prompt": self._get_default_negative(new_ckpt, "")
            }
        }
        self._add_model_row(new_data)
        new_row = self.table.rowCount() - 1
        self.table.selectRow(new_row)
        self.current_selected_row = new_row
        self._load_model_to_form(new_row)
        self.table.scrollToBottom()

    def _remove_model_by_id(self, model_id: str):
        """Удаляет модель из таблицы."""
        idx = next((i for i, r in enumerate(self.row_widgets) if r["id"] == model_id), -1)
        if idx == -1:
            return

        model_name = self.row_widgets[idx].get("name") or model_id
        reply = QMessageBox.question(
            self,
            "Удаление модели",
            f"Вы уверены, что хотите удалить модель '{model_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.table.removeRow(idx)
            self.row_widgets.pop(idx)
            if self.row_widgets:
                next_row = min(idx, len(self.row_widgets) - 1)
                self.table.selectRow(next_row)
                self.current_selected_row = next_row
                self._load_model_to_form(next_row)
            else:
                self.current_selected_row = -1

    def _run_test_match(self):
        """Запускает неблокирующее тестирование маршрутизации тестового запроса."""
        if self._test_worker is not None and self._test_worker.isRunning():
            return

        if self.current_selected_row >= 0:
            self._save_form_to_model(self.current_selected_row)

        test_text = self.input_test_prompt.text().strip()
        if not test_text:
            self.result_badge.set_error("⚠️ Введите текст сцены для проверки.")
            return

        current_profiles = []
        for r in self.row_widgets:
            current_profiles.append({
                "id": r["id"],
                "name": r.get("name", "").strip() or r["id"],
                "checkpoint": r.get("checkpoint", "").strip(),
                "routing_description": r.get("routing_description", "").strip(),
                "llm_guidance": r.get("llm_guidance", "").strip(),
                "negative_prompt": r.get("negative_prompt", "").strip(),
                "is_default": bool(r.get("is_default", False)),
                "style_triggers": r.get("style_triggers", []),
                "active": r["chk"].isChecked(),
                "pipeline": r.get("pipeline", {})
            })

        active_count = sum(1 for p in current_profiles if p.get("active"))
        if active_count == 0:
            self.result_badge.set_error("❌ В таблице нет ни одной активной модели (отметьте галочками 'Вкл').")
            return

        self.btn_test_match.setEnabled(False)
        self.btn_test_match.setText("Расчёт...")
        self.result_badge.set_loading()

        self._test_worker = RoutingTestWorker(test_text, current_profiles, self.router, parent=self)
        self._test_worker.result_ready.connect(self._on_test_match_success)
        self._test_worker.error_occurred.connect(self._on_test_match_failed)
        self._test_worker.start()

    def _on_test_match_success(self, winner_id: str, score: float, reason: str, profiles: list):
        self.btn_test_match.setEnabled(True)
        self.btn_test_match.setText("Проверить выбор")

        winner_name = winner_id
        winner_ckpt = ""
        for p in profiles:
            if p.get("id") == winner_id:
                winner_name = p.get("name") or p.get("id")
                winner_ckpt = p.get("checkpoint", "")
                break

        self.result_badge.set_success(winner_name, winner_ckpt, reason, score)
        self._test_worker = None

    def _on_test_match_failed(self, err_msg: str):
        self.btn_test_match.setEnabled(True)
        self.btn_test_match.setText("Проверить выбор")
        self.result_badge.set_error(f"Ошибка роутинга: {err_msg}")
        self._test_worker = None

    def _on_test_prompt_clicked(self):
        """Псевдоним для обратной совместимости."""
        self._run_test_match()

    def _on_test_routing_clicked(self):
        """Псевдоним для _on_test_prompt_clicked."""
        return self._on_test_prompt_clicked()

    def _run_test_query(self):
        """Псевдоним для _on_test_prompt_clicked."""
        return self._on_test_prompt_clicked()

    def _save_models_to_json(self):
        """
        Сохраняет текущие профили моделей в data/art_director_models.json без модальных окон.
        """
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self._save_form_to_model(self.current_selected_row)

        models_to_save: List[Dict[str, Any]] = []

        for idx, r in enumerate(self.row_widgets):
            m_id = r["id"]
            name = r.get("name", "").strip()
            ckpt = r.get("checkpoint", "").strip()
            desc = r.get("routing_description", "").strip()
            guidance = r.get("llm_guidance", "").strip()
            chk = r.get("chk")
            is_active = chk.isChecked() if (chk is not None and hasattr(chk, "isChecked")) else bool(r.get("active", True))

            if not name and not ckpt:
                continue

            if not name:
                name = Path(ckpt).stem if ckpt else m_id

            pipe = dict(r.get("pipeline", {}))
            neg = pipe.get("negative_prompt") or r.get("negative_prompt", "")
            arch = r.get("architecture") or pipe.get("architecture") or ("sdxl" if is_xl_model(r) else "sd15")

            model_item = {
                "id": m_id,
                "name": name,
                "architecture": arch,
                "checkpoint": ckpt,
                "routing_description": desc,
                "llm_guidance": guidance,
                "negative_prompt": neg,
                "is_default": bool(r.get("is_default", False)),
                "style_triggers": r.get("style_triggers", []),
                "active": is_active,
                "triggers": r.get("triggers", []),
                "pipeline": {
                    "architecture": arch,
                    "sampler": pipe.get("sampler", "DPM++ 2M Karras"),
                    "scheduler": pipe.get("scheduler", "Karras"),
                    "steps": pipe.get("steps", 25),
                    "cfg_scale": round(pipe.get("cfg_scale", 6.0), 1),
                    "clip_skip": pipe.get("clip_skip", 1),
                    "hires_upscaler": pipe.get("hires_upscaler", "4x-UltraSharp"),
                    "hires_denoise": round(pipe.get("hires_denoise", 0.32), 2),
                    "enable_hr": pipe.get("enable_hr", False),
                    "enable_adetailer": pipe.get("enable_adetailer", False),
                    "negative_prompt": neg
                }
            }
            models_to_save.append(model_item)

        if not models_to_save:
            return

        if hasattr(self, "router") and self.router:
            self.router.save_profiles(models_to_save)
        save_art_director_models(models_to_save)
        self.models = models_to_save

    def _save_all(self):
        """
        Сохраняет модели в data/art_director_models.json и синхронизирует кэш FastEmbed.
        """
        if self.current_selected_row >= 0:
            self._save_form_to_model(self.current_selected_row)

        models_to_save: List[Dict[str, Any]] = []

        for idx, r in enumerate(self.row_widgets):
            m_id = r["id"]
            name = r.get("name", "").strip()
            ckpt = r.get("checkpoint", "").strip()
            desc = r.get("routing_description", "").strip()
            guidance = r.get("llm_guidance", "").strip()
            is_active = r["chk"].isChecked()

            if not name and not ckpt:
                continue

            if not name:
                name = Path(ckpt).stem if ckpt else m_id

            pipe = dict(r.get("pipeline", {}))
            if idx == self.current_selected_row:
                arch_cur = "sdxl" if self.combo_arch.currentText().startswith("SDXL") else "sd15"
                pipe = {
                    **pipe,
                    "architecture": arch_cur,
                    "sampler": self.combo_sampler.currentText(),
                    "scheduler": self.combo_scheduler.currentText(),
                    "steps": self.spin_steps.value(),
                    "cfg_scale": round(self.spin_cfg.value(), 1),
                    "clip_skip": self.spin_clip_skip.value(),
                    "hires_upscaler": self.combo_upscaler.currentText(),
                    "hires_denoise": round(self.spin_hires_denoise.value(), 2),
                    "enable_hr": self.switch_enable_hr.isChecked(),
                    "enable_adetailer": self.switch_enable_ad.isChecked(),
                    "negative_prompt": self.input_negative_prompt.toPlainText().strip()
                }

            neg = pipe.get("negative_prompt") or r.get("negative_prompt", "")
            arch = r.get("architecture") or pipe.get("architecture") or ("sdxl" if is_xl_model(r) else "sd15")

            model_item = {
                "id": m_id,
                "name": name,
                "architecture": arch,
                "checkpoint": ckpt,
                "routing_description": desc,
                "llm_guidance": guidance,
                "negative_prompt": neg,
                "is_default": bool(r.get("is_default", False)),
                "style_triggers": r.get("style_triggers", []),
                "active": is_active,
                "triggers": r.get("triggers", []),
                "pipeline": {
                    "architecture": arch,
                    "sampler": pipe.get("sampler", self.combo_sampler.currentText()),
                    "scheduler": pipe.get("scheduler", self.combo_scheduler.currentText()),
                    "steps": pipe.get("steps", self.spin_steps.value()),
                    "cfg_scale": round(pipe.get("cfg_scale", self.spin_cfg.value()), 1),
                    "clip_skip": pipe.get("clip_skip", self.spin_clip_skip.value()),
                    "hires_upscaler": pipe.get("hires_upscaler", self.combo_upscaler.currentText()),
                    "hires_denoise": round(pipe.get("hires_denoise", self.spin_hires_denoise.value()), 2),
                    "enable_hr": pipe.get("enable_hr", self.switch_enable_hr.isChecked()),
                    "enable_adetailer": pipe.get("enable_adetailer", self.switch_enable_ad.isChecked()),
                    "negative_prompt": neg
                }
            }
            models_to_save.append(model_item)

        if not models_to_save:
            QMessageBox.warning(self, "Ошибка", "Таблица моделей пуста.")
            return

        # 1. Запись в data/art_director_models.json
        self.router.save_profiles(models_to_save)
        save_art_director_models(models_to_save)

        # 2. Обновление FastEmbed эмбеддингов
        if self.art_director and hasattr(self.art_director, "router") and self.art_director.router:
            self.art_director.router.update_embeddings()
        else:
            self.router.update_embeddings()

        # 3. Синхронизация реестра моделей Forge
        sync_registry_with_user_config()

        self.models = models_to_save
        QMessageBox.information(
            self,
            "Сохранено",
            f"Успешно сохранено {len(models_to_save)} моделей в data/art_director_models.json.\n"
            "Векторные эмбеддинги роутера обновлены!"
        )

    def _stop_all_workers(self):
        """Безопасная остановка всех фоновых потоков менеджера моделей."""
        workers = [self._scan_worker, self._test_worker, self._civitai_worker, self._fp8_worker]
        for worker in workers:
            if worker is None:
                continue

            # 1) Отключение сигналов от слотов диалога
            for sig_name in (
                "scan_completed", "scan_failed", "result_ready", "error_occurred",
                "params_ready", "progress_updated", "conversion_finished", "conversion_failed"
            ):
                sig = getattr(worker, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except Exception:
                        pass

            # 2) Если поддерживается — вызов stop()/abort() или requestInterruption()
            if hasattr(worker, "abort"):
                try:
                    worker.abort()
                except Exception:
                    pass
            elif hasattr(worker, "stop"):
                try:
                    worker.stop()
                except Exception:
                    pass

            try:
                worker.requestInterruption()
            except Exception:
                pass

            # 3) Вызов thread.quit()
            try:
                worker.quit()
            except Exception:
                pass

            # 4) Вызов thread.wait(200)
            try:
                if worker.isRunning():
                    worker.wait(200)
            except Exception:
                pass

            # 5) Если thread.isRunning() — сделай thread.setParent(None), чтобы сборщик мусора окна не триггерил abort Qt
            try:
                if worker.isRunning():
                    worker.setParent(None)
                    _MODELS_MANAGER_ORPHANED_WORKERS.append(worker)
                    worker.finished.connect(
                        lambda w=worker: _MODELS_MANAGER_ORPHANED_WORKERS.remove(w) if w in _MODELS_MANAGER_ORPHANED_WORKERS else None
                    )
            except Exception:
                pass

        self._scan_worker = None
        self._test_worker = None
        self._civitai_worker = None
        self._fp8_worker = None

    def closeEvent(self, event):
        self._stop_all_workers()
        super().closeEvent(event)

    def reject(self):
        self._stop_all_workers()
        super().reject()

    def accept(self):
        self._stop_all_workers()
        super().accept()
