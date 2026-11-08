"""
Models Manager Dialog for NeonRoute.
Configures available Stable Diffusion Forge checkpoints, pipeline generation parameters,
and FastEmbed vector routing rules in modern Windows 11 / Glassmorphism aesthetic.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("NeonRoute.ModelsManagerDialog")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QHeaderView, QMessageBox, QFrame, QWidget, QApplication,
    QSplitter, QFormLayout, QScrollArea
)
from qfluentwidgets import (
    LineEdit,
    SearchLineEdit,
    ComboBox,
    CheckBox,
    ProgressBar,
    PushButton,
    PrimaryPushButton,
    TransparentToolButton,
    SwitchButton,
    TableWidget,
    FluentIcon as FIF,
    setTheme,
    Theme,
    InfoBar,
    InfoBarPosition,
    MessageBox,
)

from services.model_router import ModelRouter
from services.art_director_config import (
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
    normalize_checkpoint_stem,
    get_default_negative_prompt,
)

# === ВЫДЕЛЕННЫЕ ФОНОВЫЕ ВОРКЕРЫ QThread ===
from ui.models_manager.workers import (
    RoutingTestWorker,
    ForgeScanWorker,
    CivitaiParamsWorker,
    FP8ConvertWorker,
    register_orphaned_worker,
)

# === МОДУЛЬНЫЕ КОНТРОЛЛЕР-МИКСИНЫ ===
from ui.models_manager.mixins import (
    ModelPipelineFormMixin,
    ForgeCivitaiSyncMixin,
    FP8ConversionMixin,
)


class ModelsManagerDialog(
    QDialog,
    ModelPipelineFormMixin,
    ForgeCivitaiSyncMixin,
    FP8ConversionMixin,
):
    """
    Центральный пульт управления чекпоинтами SD Forge, пайплайном рендера
    и семантическим роутингом FastEmbed CPU ONNX в эстетике Glassmorphism.
    """

    @staticmethod
    def _normalize_checkpoint_stem(filename: str) -> str:
        """Очищает имя чекпоинта от суффиксов квантования для безопасного сопоставления."""
        return normalize_checkpoint_stem(filename)

    @staticmethod
    def _get_default_negative(checkpoint_name: str = "", model_id: str = "") -> str:
        """Интеллектуальная подстановка рекомендованного негатива по типу модели."""
        return get_default_negative_prompt(checkpoint_name, model_id)

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
            }
            PushButton#CloseBtn {
                background-color: rgba(255, 255, 255, 0.05);
                color: #94A3B8;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
            }
            PushButton#CloseBtn:hover {
                background-color: rgba(239, 68, 68, 0.20);
                color: #F87171;
                border-color: rgba(239, 68, 68, 0.40);
            }
            PushButton#AutoFillBtn {
                background-color: rgba(56, 189, 248, 0.15);
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 6px;
                font-weight: 600;
            }
            PushButton#AutoFillBtn:hover {
                background-color: rgba(56, 189, 248, 0.28);
                border-color: #38BDF8;
                color: #FFFFFF;
            }
            PushButton#ConvertFP8Btn {
                background-color: rgba(168, 85, 247, 0.15);
                color: #C084FC;
                border: 1px solid rgba(168, 85, 247, 0.35);
                border-radius: 6px;
                font-weight: 600;
            }
            PushButton#ConvertFP8Btn:hover {
                background-color: rgba(168, 85, 247, 0.28);
                border-color: #C084FC;
                color: #FFFFFF;
            }
            PushButton#ConvertFP8Btn:disabled {
                background-color: rgba(255, 255, 255, 0.03);
                color: #64748B;
                border-color: rgba(255, 255, 255, 0.06);
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # 1. Header
        hdr_layout = QVBoxLayout()
        hdr_layout.setSpacing(3)
        title_lbl = QLabel("🎨 МОДЕЛИ ГЕНЕРАЦИИ, ПАРАМЕТРЫ ПАЙПЛАЙНА И РОУТИНГ", self)
        title_lbl.setStyleSheet("""
            font-size: 15px;
            font-weight: 800;
            letter-spacing: 1.2px;
            color: #00F0FF;
            font-family: 'Inter', 'Segoe UI', sans-serif;
        """)
        hdr_layout.addWidget(title_lbl)

        sub_lbl = QLabel(
            "Управление моделями Stable Diffusion Forge, точная настройка параметров генерации "
            "и правила семантического векторного роутинга FastEmbed CPU ONNX.",
            self
        )
        sub_lbl.setStyleSheet("font-size: 11px; color: #94A3B8;")
        hdr_layout.addWidget(sub_lbl)
        main_layout.addLayout(hdr_layout)

        # 2. Main 2-column Splitter (Table | Editor)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        # --- LEFT PANEL: Table of models ---
        left_widget = QFrame(self)
        left_widget.setObjectName("LeftPanel")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        tbl_bar = QHBoxLayout()
        tbl_bar.setSpacing(8)

        self.btn_refresh_forge = PushButton("Сканировать Forge", left_widget, icon=FIF.SYNC)
        self.btn_refresh_forge.setToolTip("Опросить работающий сервер SD Forge и обнаружить новые чекпоинты")
        self.btn_refresh_forge.clicked.connect(lambda: self._start_forge_scan(silent=False))
        tbl_bar.addWidget(self.btn_refresh_forge)

        self.btn_civitai_catalog = PushButton("Каталог Civitai", left_widget, icon=FIF.GLOBE)
        self.btn_civitai_catalog.setToolTip("Открыть каталог и загрузчик моделей Civitai с автосохранением в Forge")
        self.btn_civitai_catalog.clicked.connect(self._open_civitai_catalog)
        tbl_bar.addWidget(self.btn_civitai_catalog)

        tbl_bar.addStretch()

        self.btn_add_model = PrimaryPushButton("Добавить", left_widget, icon=FIF.ADD)
        self.btn_add_model.setFixedHeight(30)
        self.btn_add_model.clicked.connect(self._add_empty_model_row)
        tbl_bar.addWidget(self.btn_add_model)

        left_layout.addLayout(tbl_bar)

        self.table = TableWidget(left_widget)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Вкл", "Модель", "Чекпоинт Forge", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 48)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 150)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 40)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)

        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table.cellChanged.connect(self._on_table_cell_changed)

        left_layout.addWidget(self.table, stretch=1)
        splitter.addWidget(left_widget)

        # --- RIGHT PANEL: Form Editor inside ScrollArea ---
        right_scroll = QScrollArea(self)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)

        right_container = QFrame()
        right_container.setObjectName("EditorPanel")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(12)

        lbl_edit_title = QLabel("⚙️ ПАРАМЕТРЫ МОДЕЛИ И ПАЙПЛАЙНА", right_container)
        lbl_edit_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #FFFFFF;")
        right_layout.addWidget(lbl_edit_title)

        form_info = QFormLayout()
        form_info.setSpacing(8)

        self.input_name = LineEdit(right_container)
        self.input_name.setPlaceholderText("Понятное имя модели (например: Realistic Vision V6.0)")
        self.input_name.textChanged.connect(self._on_input_name_changed)
        form_info.addRow("Отображаемое имя:", self.input_name)

        ckpt_box = QHBoxLayout()
        ckpt_box.setSpacing(6)
        self.combo_checkpoint = FluentEditableComboBox(right_container)
        self.combo_checkpoint.setPlaceholderText("Выберите или укажите имя .safetensors")
        for cp in self.available_checkpoints:
            self.combo_checkpoint.addItem(cp)
        self.combo_checkpoint.currentTextChanged.connect(self._on_combo_checkpoint_changed)
        ckpt_box.addWidget(self.combo_checkpoint, stretch=1)

        self.btn_autofill_params = PushButton("Автонастройка (Civitai)", right_container, icon=FIF.CLOUD_DOWNLOAD)
        self.btn_autofill_params.setObjectName("AutoFillBtn")
        self.btn_autofill_params.setToolTip(
            "Запросить у Civitai рекомендуемые сэмплер, шаги, CFG и негативный промпт по хэшу файла"
        )
        self.btn_autofill_params.clicked.connect(self._on_autofill_params_clicked)
        ckpt_box.addWidget(self.btn_autofill_params)

        self.btn_convert_fp8 = PushButton("⚡ Сжать в FP8 (e4m3fn)", right_container, icon=FIF.SEND)
        self.btn_convert_fp8.setObjectName("ConvertFP8Btn")
        self.btn_convert_fp8.setToolTip("Конвертирует модель SDXL в нативный FP8. Освобождает 4 ГБ VRAM.")
        self.btn_convert_fp8.clicked.connect(self._on_start_fp8_conversion)
        ckpt_box.addWidget(self.btn_convert_fp8)

        form_info.addRow("Файл чекпоинта Forge:", ckpt_box)

        # Чекбокс и прогресс FP8
        fp8_opts_layout = QHBoxLayout()
        fp8_opts_layout.setSpacing(10)

        self.chk_delete_source = CheckBox("Заменять исходник (экономия места на диске)", right_container)
        self.chk_delete_source.setChecked(True)
        self.chk_delete_source.setToolTip("После успешного сжатия заменяет исходный файл новым FP8 вариантом")
        fp8_opts_layout.addWidget(self.chk_delete_source)
        fp8_opts_layout.addStretch()
        form_info.addRow("", fp8_opts_layout)

        self.progress_fp8 = ProgressBar(right_container)
        self.progress_fp8.setVisible(False)
        self.lbl_fp8_status = QLabel("", right_container)
        self.lbl_fp8_status.setStyleSheet("color: #A855F7; font-size: 11px; font-weight: 600;")
        self.lbl_fp8_status.setVisible(False)

        fp8_progress_layout = QVBoxLayout()
        fp8_progress_layout.setSpacing(3)
        fp8_progress_layout.addWidget(self.progress_fp8)
        fp8_progress_layout.addWidget(self.lbl_fp8_status)
        form_info.addRow("", fp8_progress_layout)

        self.edit_style_triggers = LineEdit(right_container)
        self.edit_style_triggers.setPlaceholderText("cyberpunk, neon, anime, photoreal (через запятую)")
        self.edit_style_triggers.textChanged.connect(self._on_param_modified)
        form_info.addRow("Триггеры стиля:", self.edit_style_triggers)

        self.chk_is_default = CheckBox("Модель по умолчанию (для запросов без явного стиля)", right_container)
        self.chk_is_default.toggled.connect(self._on_is_default_toggled)
        form_info.addRow("", self.chk_is_default)

        right_layout.addLayout(form_info)

        # Card: Пайплайн генерации
        card_pipeline = QFrame(right_container)
        card_pipeline.setObjectName("ParamCard")
        pipe_layout = QVBoxLayout(card_pipeline)
        pipe_layout.setContentsMargins(12, 10, 12, 10)
        pipe_layout.setSpacing(10)

        lbl_pipe_hdr = QLabel("🎛️ ПАРАМЕТРЫ РЕНДЕРА SD WEBUI FORGE", card_pipeline)
        lbl_pipe_hdr.setStyleSheet("font-size: 11px; font-weight: 700; color: #38BDF8; letter-spacing: 0.8px;")
        pipe_layout.addWidget(lbl_pipe_hdr)

        preset_box = QHBoxLayout()
        preset_box.setSpacing(8)
        preset_box.addWidget(QLabel("Быстрый пресет настроек:", card_pipeline))

        self.combo_presets = ComboBox(card_pipeline)
        self.combo_presets.addItems([
            "Кастомный / Выбрать...",
            "⚡ SDXL Turbo (LCM, 10 шагов, CFG 1.3, Skip 2, без HR)",
            "🎯 SDXL Classic (DPM++ 2M, 28 шагов, CFG 5.0, Skip 2)",
            "📸 SD 1.5 Photoreal (DPM++ SDE, 25 шагов, CFG 5.5, Skip 1 + Hires)"
        ])
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        preset_box.addWidget(self.combo_presets, stretch=1)
        pipe_layout.addLayout(preset_box)

        # Архитектура и Планировщик
        arch_sched_row = QHBoxLayout()
        arch_sched_row.setSpacing(12)

        arch_row = QHBoxLayout()
        arch_row.addWidget(QLabel("Архитектура:", card_pipeline))
        self.combo_arch = ComboBox(card_pipeline)
        self.combo_arch.addItems(["SDXL / Pony", "SD 1.5"])
        self.combo_arch.currentTextChanged.connect(self._on_param_modified)
        arch_row.addWidget(self.combo_arch, stretch=1)
        arch_sched_row.addLayout(arch_row, stretch=1)

        sched_row = QHBoxLayout()
        sched_row.addWidget(QLabel("Планировщик:", card_pipeline))
        self.combo_scheduler = FluentEditableComboBox(card_pipeline)
        self.combo_scheduler.addItems([
            "Karras", "Exponential", "SGM Uniform", "Simple", "Normal", "Align Your Steps", "Turbo"
        ])
        self.combo_scheduler.currentTextChanged.connect(self._on_param_modified)
        sched_row.addWidget(self.combo_scheduler, stretch=1)
        arch_sched_row.addLayout(sched_row, stretch=1)

        pipe_layout.addLayout(arch_sched_row)

        # Семплер и Апскейлер
        combos_row = QHBoxLayout()
        combos_row.setSpacing(12)

        samp_row = QHBoxLayout()
        samp_row.addWidget(QLabel("Семплер:", card_pipeline))
        self.combo_sampler = FluentEditableComboBox(card_pipeline)
        self.combo_sampler.addItems([
            "DPM++ 2M Karras", "DPM++ SDE Karras", "Euler a", "Euler",
            "LCM", "DPM++ 2M", "DPM++ 2M SDE", "UniPC", "DDIM"
        ])
        self.combo_sampler.currentTextChanged.connect(self._on_param_modified)
        samp_row.addWidget(self.combo_sampler, stretch=1)
        combos_row.addLayout(samp_row, stretch=1)

        up_row = QHBoxLayout()
        up_row.addWidget(QLabel("Апскейлер Hires:", card_pipeline))
        self.combo_upscaler = FluentEditableComboBox(card_pipeline)
        self.combo_upscaler.addItems([
            "4x-UltraSharp", "R-ESRGAN 4x+", "R-ESRGAN 4x+ Anime6B",
            "4x_NMKD-Superscale-SP_178000_G", "Latent (bicubic antialiased)", "None"
        ])
        self.combo_upscaler.currentTextChanged.connect(self._on_param_modified)
        up_row.addWidget(self.combo_upscaler, stretch=1)
        combos_row.addLayout(up_row, stretch=1)

        pipe_layout.addLayout(combos_row)

        # Переключатели: Hires.fix и ADetailer
        switches_row = QHBoxLayout()
        switches_row.setSpacing(16)

        hr_box = QHBoxLayout()
        self.switch_enable_hr = SwitchButton(card_pipeline)
        self.switch_enable_hr.checkedChanged.connect(self._on_param_modified)
        hr_box.addWidget(self.switch_enable_hr)
        hr_box.addWidget(QLabel("Hires.fix (Увеличение разрешения)", card_pipeline))
        switches_row.addLayout(hr_box)

        switches_row.addSpacing(16)

        ad_box = QHBoxLayout()
        ad_box.setSpacing(8)
        self.btn_adetailer_config = PushButton("Настроить ADetailer", card_pipeline, icon=FIF.SETTING)
        self.btn_adetailer_config.clicked.connect(self._open_adetailer_dialog)
        ad_box.addWidget(self.btn_adetailer_config)

        self.lbl_ad_status = QLabel("ВКЛ [v8m/v9c]", card_pipeline)
        self.lbl_ad_status.setStyleSheet("color: #00F0FF; font-weight: bold;")
        ad_box.addWidget(self.lbl_ad_status)
        ad_box.addStretch()

        switches_row.addLayout(ad_box)
        switches_row.addStretch()
        pipe_layout.addLayout(switches_row)

        # Сетка слайдеров
        grid_sliders = QGridLayout()
        grid_sliders.setSpacing(8)
        grid_sliders.setColumnStretch(1, 1)

        grid_sliders.addWidget(QLabel("Шаги сэмплирования:", card_pipeline), 0, 0)
        self.slider_steps = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_steps.setRange(5, 60)
        self.slider_steps.setValue(25)
        self.slider_steps.valueChanged.connect(self._on_steps_slider_changed)
        grid_sliders.addWidget(self.slider_steps, 0, 1)
        self.spin_steps = SamplingSpinBox(card_pipeline)
        self.spin_steps.setRange(5, 60)
        self.spin_steps.setValue(25)
        self.spin_steps.setFixedWidth(82)
        self.spin_steps.valueChanged.connect(self._on_steps_spin_changed)
        grid_sliders.addWidget(self.spin_steps, 0, 2)

        grid_sliders.addWidget(QLabel("CFG Scale:", card_pipeline), 1, 0)
        self.slider_cfg = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_cfg.setRange(10, 150)
        self.slider_cfg.setValue(60)
        self.slider_cfg.valueChanged.connect(self._on_cfg_slider_changed)
        grid_sliders.addWidget(self.slider_cfg, 1, 1)
        self.spin_cfg = SamplingDoubleSpinBox(card_pipeline)
        self.spin_cfg.setRange(1.0, 15.0)
        self.spin_cfg.setDecimals(1)
        self.spin_cfg.setSingleStep(0.1)
        self.spin_cfg.setValue(6.0)
        self.spin_cfg.setFixedWidth(82)
        self.spin_cfg.valueChanged.connect(self._on_cfg_spin_changed)
        grid_sliders.addWidget(self.spin_cfg, 1, 2)

        grid_sliders.addWidget(QLabel("Денойз Hires:", card_pipeline), 2, 0)
        self.slider_hires_denoise = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_hires_denoise.setRange(10, 90)
        self.slider_hires_denoise.setValue(32)
        self.slider_hires_denoise.valueChanged.connect(self._on_hires_denoise_slider_changed)
        grid_sliders.addWidget(self.slider_hires_denoise, 2, 1)
        self.spin_hires_denoise = SamplingDoubleSpinBox(card_pipeline)
        self.spin_hires_denoise.setRange(0.10, 0.90)
        self.spin_hires_denoise.setDecimals(2)
        self.spin_hires_denoise.setSingleStep(0.01)
        self.spin_hires_denoise.setValue(0.32)
        self.spin_hires_denoise.setFixedWidth(82)
        self.spin_hires_denoise.valueChanged.connect(self._on_hires_denoise_spin_changed)
        grid_sliders.addWidget(self.spin_hires_denoise, 2, 2)

        grid_sliders.addWidget(QLabel("Clip Skip:", card_pipeline), 3, 0)
        self.slider_clip_skip = SamplingSlider(Qt.Orientation.Horizontal, card_pipeline)
        self.slider_clip_skip.setRange(1, 4)
        self.slider_clip_skip.setValue(1)
        self.slider_clip_skip.valueChanged.connect(self._on_clip_skip_slider_changed)
        grid_sliders.addWidget(self.slider_clip_skip, 3, 1)
        self.spin_clip_skip = SamplingSpinBox(card_pipeline)
        self.spin_clip_skip.setRange(1, 4)
        self.spin_clip_skip.setValue(1)
        self.spin_clip_skip.setFixedWidth(82)
        self.spin_clip_skip.valueChanged.connect(self._on_clip_skip_spin_changed)
        grid_sliders.addWidget(self.spin_clip_skip, 3, 2)

        pipe_layout.addLayout(grid_sliders)

        pipe_layout.addWidget(QLabel("Базовый негативный промпт модели:", card_pipeline))
        self.input_negative_prompt = FluentTextEdit(card_pipeline)
        self.input_negative_prompt.setFixedHeight(55)
        self.input_negative_prompt.textChanged.connect(self._on_param_modified)
        pipe_layout.addWidget(self.input_negative_prompt)

        right_layout.addWidget(card_pipeline)

        # Card: Семантический роутинг FastEmbed
        card_routing = QFrame(right_container)
        card_routing.setObjectName("ParamCard")
        rout_layout = QVBoxLayout(card_routing)
        rout_layout.setContentsMargins(12, 10, 12, 10)
        rout_layout.setSpacing(8)

        lbl_rout_hdr = QLabel("🧠 СЕМАНТИЧЕСКИЙ РОУТИНГ И ПОДСКАЗКИ ДЛЯ LLM", card_routing)
        lbl_rout_hdr.setStyleSheet("font-size: 11px; font-weight: 700; color: #00F0FF; letter-spacing: 0.8px;")
        rout_layout.addWidget(lbl_rout_hdr)

        rout_layout.addWidget(QLabel("Описание стиля / ключевые темы FastEmbed:", card_routing))
        self.input_desc = FluentTextEdit(card_routing)
        self.input_desc.setFixedHeight(60)
        self.input_desc.textChanged.connect(self._on_param_modified)
        rout_layout.addWidget(self.input_desc)

        rout_layout.addWidget(QLabel("Обязательные теги для промпта (Textual Inversion, макс. 8 штук):", card_routing))
        self.input_guidance = FluentTextEdit(card_routing)
        self.input_guidance.setFixedHeight(45)
        self.input_guidance.textChanged.connect(self._on_input_guidance_changed)
        self.input_guidance.textChanged.connect(self._on_param_modified)
        rout_layout.addWidget(self.input_guidance)

        right_layout.addWidget(card_routing)
        right_layout.addStretch()

        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, stretch=1)

        # 4. Bottom Test Panel
        test_panel = QFrame(self)
        test_panel.setObjectName("TestPanel")
        test_layout = QVBoxLayout(test_panel)
        test_layout.setContentsMargins(14, 10, 14, 10)
        test_layout.setSpacing(8)

        test_title = QLabel("🧪 ПРОВЕРКА РОУТИНГА FASTEMBED НА CPU (СЕМАНТИЧЕСКИЙ ТЕСТ)", test_panel)
        test_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #A855F7; letter-spacing: 0.8px;")
        test_layout.addWidget(test_title)

        test_input_row = QHBoxLayout()
        test_input_row.setSpacing(8)

        self.input_test_prompt = SearchLineEdit(test_panel)
        self.input_test_prompt.setPlaceholderText("Введите описание сцены для теста (например: киберпанк девушка на фоне неонового дождя)...")
        self.input_test_prompt.returnPressed.connect(self._run_test_match)
        test_input_row.addWidget(self.input_test_prompt, stretch=1)

        self.btn_test_match = PushButton("Проверить выбор", test_panel, icon=FIF.PLAY)
        self.btn_test_match.setFixedWidth(150)
        self.btn_test_match.clicked.connect(self._run_test_match)
        test_input_row.addWidget(self.btn_test_match)

        test_layout.addLayout(test_input_row)

        self.result_badge = RoutingResultBadge(test_panel)
        test_layout.addWidget(self.result_badge)

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

    def _on_table_selection_changed(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            new_row = selected_rows[0].row()
            if new_row != self.current_selected_row:
                if 0 <= self.current_selected_row < len(self.row_widgets):
                    self._save_form_to_model(self.current_selected_row)
                self.current_selected_row = new_row
                self._load_model_to_form(new_row)

    # ---------------- Semantic Routing Test Slots ---------------- #
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

    # ---------------- Persistence & Synchronization ---------------- #
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
                    "enable_adetailer": pipe.get("enable_adetailer", True),
                    "adetailer_face_model": pipe.get("adetailer_face_model", "face_yolov8m.pt"),
                    "adetailer_face_confidence": pipe.get("adetailer_face_confidence", 0.44),
                    "adetailer_face_denoise": pipe.get("adetailer_face_denoise", 0.32),
                    "adetailer_face_inpaint_size": pipe.get("adetailer_face_inpaint_size", pipe.get("adetailer_inpaint_size", 768)),
                    "adetailer_inpaint_size": pipe.get("adetailer_face_inpaint_size", pipe.get("adetailer_inpaint_size", 768)),
                    "adetailer_hands_enabled": pipe.get("adetailer_hands_enabled", True),
                    "adetailer_hands_model": pipe.get("adetailer_hands_model", "hand_yolov9c.pt"),
                    "adetailer_hands_confidence": pipe.get("adetailer_hands_confidence", 0.38),
                    "adetailer_hands_denoise": pipe.get("adetailer_hands_denoise", 0.40),
                    "adetailer_hands_inpaint_size": pipe.get("adetailer_hands_inpaint_size", 512),
                    "adetailer_hands_mask_blur": pipe.get("adetailer_hands_mask_blur", 8),
                    "adetailer_hands_prompt": pipe.get("adetailer_hands_prompt", ""),
                    "adetailer_hands_negative_prompt": pipe.get("adetailer_hands_negative_prompt", ""),
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
        self._save_models_to_json()
        if not self.models:
            QMessageBox.warning(self, "Ошибка", "Таблица моделей пуста.")
            return

        # 2. Обновление FastEmbed эмбеддингов
        if self.art_director and hasattr(self.art_director, "router") and self.art_director.router:
            self.art_director.router.update_embeddings()
        else:
            self.router.update_embeddings()

        # 3. Синхронизация реестра моделей Forge
        sync_registry_with_user_config()

        QMessageBox.information(
            self,
            "Сохранено",
            f"Успешно сохранено {len(self.models)} моделей в data/art_director_models.json.\n"
            "Векторные эмбеддинги роутера обновлены!"
        )

    # ---------------- Lifecycle & Thread Safety ---------------- #
    def _stop_all_workers(self):
        """Безопасная остановка всех фоновых потоков менеджера моделей."""
        workers = [self._scan_worker, self._test_worker, self._civitai_worker, self._fp8_worker]
        for worker in workers:
            if worker is None:
                continue

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

            try:
                worker.quit()
            except Exception:
                pass

            try:
                if worker.isRunning():
                    worker.wait(200)
            except Exception:
                pass

            # Безопасная изоляция еще активных потоков без NameError
            register_orphaned_worker(worker)

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

    _on_save_clicked = _save_all
