"""
Models Manager Dialog for NeonRoute.
Configures available Stable Diffusion Forge checkpoints, pipeline generation parameters,
and FastEmbed vector routing rules.
Allows scanning checkpoints from Forge, editing routing descriptions,
auto-configuring generation parameters via Civitai API with safe fallback, and live prompt testing.
"""

import logging
import uuid
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem, QCheckBox,
    QHeaderView, QMessageBox, QFrame, QWidget, QApplication,
    QSpinBox, QDoubleSpinBox, QGroupBox, QSplitter, QFormLayout, QScrollArea
)

from services.model_router import ModelRouter
from services.art_director_config import (
    load_art_director_models,
    save_art_director_models
)
from services.forge_client import ForgeClient
from services.forge_models import sync_registry_with_user_config

logger = logging.getLogger("NeonRoute.ModelsManager")

class RoutingTestWorker(QThread):
    result_ready = pyqtSignal(str, float, str, list)  # (best_id, score, reason, snapshot_profiles)
    error_occurred = pyqtSignal(str)

    def __init__(self, text: str, profiles: List[Dict[str, Any]], router: ModelRouter, parent=None):
        super().__init__(parent)
        self.text = text
        self.profiles = profiles
        self.router = router

    def run(self):
        try:
            winner_id, score, reason = self.router.match_prompt_detailed(self.text, profiles=self.profiles)
            if not winner_id:
                self.error_occurred.emit("Не удалось определить подходящую модель среди активных.")
                return
            self.result_ready.emit(winner_id, score, reason, self.profiles)
        except Exception as e:
            logger.error(f"[RoutingTestWorker] Ошибка тестирования маршрутизации: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


class ForgeScanWorker(QThread):
    scan_completed = pyqtSignal(list, list, list)  # (raw_models, installed_checkpoints, upscalers)
    scan_failed = pyqtSignal(str)

    def __init__(self, forge_client: ForgeClient, parent=None):
        super().__init__(parent)
        self.forge_client = forge_client

    def run(self):
        try:
            raw_models = self.forge_client.get_models() or []
        except Exception:
            raw_models = []

        try:
            installed = self.forge_client.get_installed_checkpoints() or []
        except Exception:
            installed = []

        try:
            upscalers = self.forge_client.get_upscalers() or []
        except Exception:
            upscalers = []

        self.scan_completed.emit(raw_models, installed, upscalers)


class CivitaiParamsWorker(QThread):
    params_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, checkpoint: str, parent=None):
        super().__init__(parent)
        self.checkpoint = checkpoint

    def run(self):
        try:
            from services.civitai_service import fetch_civitai_parameters
            params = fetch_civitai_parameters(self.checkpoint)
            self.params_ready.emit(params)
        except Exception as e:
            logger.error(f"[CivitaiParamsWorker] Ошибка запроса Civitai: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


class ModelsManagerDialog(QDialog):

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

    """
    Dialog for managing SD Forge models and vector routing descriptions.
    Includes Forge checkpoint scanning, inline editing of models/descriptions,
    semi-automatic Civitai API pipeline configuration,
    and real-time cosine similarity testing via FastEmbed CPU ONNX.
    """

    def __init__(
        self,
        forge_client: Optional[ForgeClient] = None,
        parent: Optional[QWidget] = None,
        art_director: Optional[Any] = None
    ):
        super().__init__(parent)
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
        self._test_worker: Optional[RoutingTestWorker] = None
        self._scan_worker: Optional[ForgeScanWorker] = None
        self._civitai_worker: Optional[CivitaiParamsWorker] = None

        self.setWindowTitle("🎨 Модели генерации, параметры пайплайна и роутинг — NeonRoute")
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)

        self._setup_ui()
        self._populate_table()

        # Неблокирующий фоновый опрос Forge сразу после отрисовки UI
        self._start_forge_scan(silent=True)

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0A0D14;
                color: #CBD5E1;
            }
            QLabel {
                color: #CBD5E1;
                font-size: 12px;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #121824;
                color: #F8FAFC;
                border: 1px solid #1E2B42;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                selection-background-color: #00F0FF;
                selection-color: #000000;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #00F0FF;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #121824;
                color: #E2E8F0;
                border: 1px solid #1E2B42;
                selection-background-color: #1E2B45;
                selection-color: #00F0FF;
            }
            QTableWidget {
                background-color: #0E1420;
                color: #E2E8F0;
                border: 1px solid #1E2B42;
                border-radius: 8px;
                gridline-color: #182236;
                font-size: 12px;
                selection-background-color: #1E293B;
                selection-color: #00F0FF;
            }
            QHeaderView::section {
                background-color: #162035;
                color: #94A3B8;
                font-weight: bold;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #1E2B42;
                border-right: 1px solid #1E2B42;
            }
            QCheckBox {
                color: #CBD5E1;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #263550;
                border-radius: 4px;
                background-color: #121824;
            }
            QCheckBox::indicator:checked {
                background-color: #00F0FF;
                border-color: #00F0FF;
            }
            QGroupBox {
                border: 1px solid #1E2B42;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                font-size: 12px;
                font-weight: bold;
                color: #38BDF8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QSplitter::handle {
                background-color: #1E2B42;
                width: 2px;
            }
            QPushButton {
                background-color: #1A2438;
                color: #E2E8F0;
                border: 1px solid #263550;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #24334E;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#PrimaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366F1, stop:1 #4F46E5);
                border: 1px solid #818CF8;
                color: #FFFFFF;
            }
            QPushButton#PrimaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7C3AED, stop:1 #6366F1);
                border-color: #00F0FF;
            }
            QPushButton#AutoFillBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10B981);
                border: 1px solid #34D399;
                color: #FFFFFF;
            }
            QPushButton#AutoFillBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10B981, stop:1 #34D399);
                border-color: #00F0FF;
            }
            QPushButton#TestBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:1 #2563EB);
                border: 1px solid #38BDF8;
                color: #FFFFFF;
            }
            QPushButton#TestBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0EA5E9, stop:1 #3B82F6);
                border-color: #00F0FF;
            }
            QPushButton#DeleteBtn {
                background-color: #2D1418;
                border: 1px solid #7F1D1D;
                color: #F87171;
                padding: 4px;
            }
            QPushButton#DeleteBtn:hover {
                background-color: #451A20;
                border-color: #EF4444;
                color: #EF4444;
            }
            QFrame#TestPanel {
                background-color: #0C101A;
                border: 1px solid #1E293B;
                border-radius: 8px;
            }
            QFrame#EditorPanel {
                background-color: #0B101D;
                border: 1px solid #1E2B42;
                border-radius: 8px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # 1. Header Banner
        hdr_layout = QVBoxLayout()
        hdr_layout.setSpacing(2)
        title_lbl = QLabel("🎨 УПРАВЛЕНИЕ МОДЕЛЯМИ, ПАРАМЕТРАМИ ГЕНЕРАЦИИ И РОУТИНГОМ", self)
        title_lbl.setStyleSheet("color: #00F0FF; font-size: 15px; font-weight: 900; letter-spacing: 0.5px;")
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

        # 2. Central Splitter (Left: Models Table | Right: Model Editor Form)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(4)

        # --- LEFT PANEL: Table of models ---
        left_widget = QWidget(self)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        tbl_bar = QHBoxLayout()
        tbl_bar.setSpacing(8)
        lbl_tbl = QLabel("<b>Зарегистрированные модели:</b>", self)
        tbl_bar.addWidget(lbl_tbl)
        tbl_bar.addStretch()

        self.btn_refresh_forge = QPushButton("🔄 Сканировать Forge", self)
        self.btn_refresh_forge.setToolTip("Запросить чекпоинты из Forge (get_models) и обнаружить новые файлы")
        self.btn_refresh_forge.clicked.connect(lambda: self._start_forge_scan(silent=False))
        tbl_bar.addWidget(self.btn_refresh_forge)

        self.btn_civitai = QPushButton("🌐 Каталог Civitai", self)
        self.btn_civitai.setToolTip("Открыть каталог Civitai / Civitai Red для поиска и скачивания моделей")
        self.btn_civitai.clicked.connect(self._open_civitai_catalog)
        tbl_bar.addWidget(self.btn_civitai)

        self.btn_add_model = QPushButton("➕ Добавить", self)
        self.btn_add_model.setToolTip("Добавить новую модель в таблицу")
        self.btn_add_model.clicked.connect(self._add_empty_model_row)
        tbl_bar.addWidget(self.btn_add_model)
        left_layout.addLayout(tbl_bar)

        self.table = QTableWidget(self)
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

        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(3, 38)

        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(self._on_table_cell_changed)
        left_layout.addWidget(self.table, stretch=1)

        splitter.addWidget(left_widget)

        # --- RIGHT PANEL: Form Editor ---
        right_scroll = QScrollArea(self)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)

        right_container = QFrame()
        right_container.setObjectName("EditorPanel")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(10)

        lbl_edit_title = QLabel("⚙️ <b>Редактор параметров модели</b>", self)
        lbl_edit_title.setStyleSheet("color: #00F0FF; font-size: 13px; font-weight: bold;")
        right_layout.addWidget(lbl_edit_title)

        # Model Info Form
        form_info = QFormLayout()
        form_info.setSpacing(8)
        form_info.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.input_name = QLineEdit(self)
        self.input_name.setPlaceholderText("Название модели")
        self.input_name.textChanged.connect(self._on_input_name_changed)
        form_info.addRow("Название:", self.input_name)

        # Checkpoint selection + Civitai Autofill Button
        ckpt_row = QHBoxLayout()
        ckpt_row.setSpacing(6)
        self.combo_checkpoint = QComboBox(self)
        self.combo_checkpoint.setEditable(True)
        self.combo_checkpoint.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for cp in self.available_checkpoints:
            self.combo_checkpoint.addItem(cp)
        self.combo_checkpoint.currentTextChanged.connect(self._on_combo_checkpoint_changed)
        ckpt_row.addWidget(self.combo_checkpoint, stretch=1)

        self.btn_autofill_params = QPushButton("🌐 Автонастройка параметров (Civitai)", self)
        self.btn_autofill_params.setObjectName("AutoFillBtn")
        self.btn_autofill_params.setToolTip("Запросить рекомендованные автором параметры с Civitai или применить Safe Defaults")
        self.btn_autofill_params.clicked.connect(self._on_autofill_params_clicked)
        ckpt_row.addWidget(self.btn_autofill_params)
        form_info.addRow("Чекпоинт:", ckpt_row)

        self.chk_is_default = QCheckBox("Использовать по умолчанию (для нейтральных запросов без указания стиля)", self)
        self.chk_is_default.toggled.connect(self._on_is_default_toggled)
        form_info.addRow("", self.chk_is_default)

        self.edit_style_triggers = QLineEdit(self)
        self.edit_style_triggers.setPlaceholderText("Например: 3d, pixar, мультфильм, cgi (через запятую)")
        form_info.addRow("Триггеры стиля (через запятую):", self.edit_style_triggers)

        right_layout.addLayout(form_info)

        # Group 1: Pipeline Parameters
        grp_pipeline = QGroupBox("⚙️ Параметры рендера (Pipeline)", self)
        grid_pipe = QFormLayout(grp_pipeline)
        grid_pipe.setSpacing(8)
        grid_pipe.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_sampler = QComboBox(self)
        self.combo_sampler.addItems(["DPM++ 2M Karras", "Euler a", "DPM++ SDE Karras", "Euler", "DDIM"])
        grid_pipe.addRow("Семплер:", self.combo_sampler)

        spin_row = QHBoxLayout()
        spin_row.setSpacing(10)

        spin_row.addWidget(QLabel("Шаги (Steps):", self))
        self.spin_steps = QSpinBox(self)
        self.spin_steps.setRange(10, 60)
        self.spin_steps.setValue(25)
        spin_row.addWidget(self.spin_steps)

        spin_row.addWidget(QLabel("CFG Scale:", self))
        self.spin_cfg = QDoubleSpinBox(self)
        self.spin_cfg.setRange(1.0, 15.0)
        self.spin_cfg.setSingleStep(0.5)
        self.spin_cfg.setDecimals(1)
        self.spin_cfg.setValue(6.0)
        spin_row.addWidget(self.spin_cfg)

        spin_row.addWidget(QLabel("Clip Skip:", self))
        self.spin_clip_skip = QSpinBox(self)
        self.spin_clip_skip.setRange(1, 2)
        self.spin_clip_skip.setValue(1)
        spin_row.addWidget(self.spin_clip_skip)

        grid_pipe.addRow("Инференс:", spin_row)

        # Быстрый статический список апскейлеров (без блокирующего HTTP-запроса к Forge при старте)
        base_upscalers = ["4x-UltraSharp", "R-ESRGAN 4x+ Anime6B", "DAT x2", "Lanczos"]
        self.combo_upscaler = QComboBox(self)
        for u in base_upscalers:
            self.combo_upscaler.addItem(u)
        grid_pipe.addRow("Hires.fix апскейлер:", self.combo_upscaler)

        self.spin_hires_denoise = QDoubleSpinBox(self)
        self.spin_hires_denoise.setRange(0.10, 0.70)
        self.spin_hires_denoise.setSingleStep(0.01)
        self.spin_hires_denoise.setDecimals(2)
        self.spin_hires_denoise.setValue(0.32)
        grid_pipe.addRow("Денойз Hires:", self.spin_hires_denoise)

        self.input_negative_prompt = QLineEdit(self)
        self.input_negative_prompt.setPlaceholderText("Рекомендуемый негативный промпт для этой модели...")
        grid_pipe.addRow("Негативный промпт:", self.input_negative_prompt)

        right_layout.addWidget(grp_pipeline)

        # Group 2: FastEmbed Vector Routing
        grp_routing = QGroupBox("🧠 Семантический роутинг (FastEmbed CPU)", self)
        layout_routing = QVBoxLayout(grp_routing)
        layout_routing.setSpacing(6)

        layout_routing.addWidget(QLabel("Описание эстетики / стилистики для FastEmbed:", self))
        self.input_desc = QTextEdit(self)
        self.input_desc.setPlaceholderText("Семантическое описание стилистики, объектов, эстетики для FastEmbed...")
        self.input_desc.setFixedHeight(64)
        layout_routing.addWidget(self.input_desc)

        layout_routing.addWidget(QLabel("Подсказка для Эларии (llm_guidance, макс. 160 симв. / 8 тегов):", self))
        self.input_guidance = QTextEdit(self)
        self.input_guidance.setPlaceholderText("Инструкция для Эларии: целевые теги, ограничения (макс. 160 символов, до 8 тегов)...")
        self.input_guidance.setFixedHeight(54)
        self.input_guidance.textChanged.connect(self._on_input_guidance_changed)
        layout_routing.addWidget(self.input_guidance)

        right_layout.addWidget(grp_routing)
        right_layout.addStretch()

        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, stretch=1)

        # 3. Live Vector Routing Test Panel
        test_panel = QFrame(self)
        test_panel.setObjectName("TestPanel")
        test_layout = QVBoxLayout(test_panel)
        test_layout.setContentsMargins(12, 8, 12, 8)
        test_layout.setSpacing(6)

        test_title = QLabel("🧪 <b>Тестовый запрос (проверка векторного сопоставления в реальном времени):</b>", self)
        test_title.setStyleSheet("color: #38BDF8; font-size: 12px;")
        test_layout.addWidget(test_title)

        test_input_row = QHBoxLayout()
        test_input_row.setSpacing(8)

        self.input_test_prompt = QLineEdit(self)
        self.input_test_prompt.setPlaceholderText("Введите текст сцены (например: 'cyberpunk girl on neon street' или 'портрет девушки в парке')...")
        self.input_test_prompt.returnPressed.connect(self._run_test_match)
        test_input_row.addWidget(self.input_test_prompt, stretch=1)

        self.btn_test_match = QPushButton("🎯 Проверить выбор", self)
        self.btn_test_match.setObjectName("TestBtn")
        self.btn_test_match.clicked.connect(self._run_test_match)
        test_input_row.addWidget(self.btn_test_match)

        test_layout.addLayout(test_input_row)

        self.lbl_test_result = QLabel("<i>Введите запрос и нажмите «Проверить выбор» для расчета косинусного сходства...</i>", self)
        self.lbl_test_result.setWordWrap(True)
        self.lbl_test_result.setStyleSheet("color: #94A3B8; font-size: 11px; padding-top: 2px;")
        test_layout.addWidget(self.lbl_test_result)
        self.test_result_label = self.lbl_test_result

        main_layout.addWidget(test_panel)

        # 4. Bottom Actions
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.btn_save = QPushButton("💾 Сохранить", self)
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.clicked.connect(self._save_all)
        actions_row.addWidget(self.btn_save)

        actions_row.addStretch()

        self.btn_close = QPushButton("✕ Закрыть", self)
        self.btn_close.clicked.connect(self.accept)
        actions_row.addWidget(self.btn_close)

        main_layout.addLayout(actions_row)

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

    def _on_table_cell_changed(self, cur_row: int, cur_col: int, prev_row: int, prev_col: int):
        if cur_row != prev_row and cur_row >= 0:
            if prev_row >= 0 and prev_row < len(self.row_widgets):
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
        r["negative_prompt"] = self.input_negative_prompt.text().strip()

        is_default = self.chk_is_default.isChecked()
        raw_triggers = [t.strip() for t in self.edit_style_triggers.text().split(",") if t.strip()]
        r["is_default"] = is_default
        r["style_triggers"] = raw_triggers

        if is_default:
            for idx, other_r in enumerate(self.row_widgets):
                if idx != row_idx:
                    other_r["is_default"] = False

        existing_pipe = dict(r.get("pipeline", {}))
        r["pipeline"] = {
            **existing_pipe,
            "sampler": self.combo_sampler.currentText(),
            "steps": self.spin_steps.value(),
            "cfg_scale": round(self.spin_cfg.value(), 1),
            "clip_skip": self.spin_clip_skip.value(),
            "hires_upscaler": self.combo_upscaler.currentText(),
            "hires_denoise": round(self.spin_hires_denoise.value(), 2),
            "negative_prompt": self.input_negative_prompt.text().strip()
        }

    def _load_model_to_form(self, row_idx: int):
        if row_idx < 0 or row_idx >= len(self.row_widgets):
            return
        r = self.row_widgets[row_idx]

        self.input_name.blockSignals(True)
        self.combo_checkpoint.blockSignals(True)
        self.combo_sampler.blockSignals(True)
        self.spin_steps.blockSignals(True)
        self.spin_cfg.blockSignals(True)
        self.spin_clip_skip.blockSignals(True)
        self.combo_upscaler.blockSignals(True)
        self.spin_hires_denoise.blockSignals(True)
        self.input_negative_prompt.blockSignals(True)
        self.input_desc.blockSignals(True)
        self.input_guidance.blockSignals(True)
        self.chk_is_default.blockSignals(True)
        self.edit_style_triggers.blockSignals(True)

        self.input_name.setText(r.get("name", ""))

        ckpt = r.get("checkpoint", "")
        idx_c = self.combo_checkpoint.findText(ckpt)
        if idx_c >= 0:
            self.combo_checkpoint.setCurrentIndex(idx_c)
        else:
            self.combo_checkpoint.setEditText(ckpt)

        pipe = r.get("pipeline", {})
        idx_s = self.combo_sampler.findText(pipe.get("sampler", "DPM++ 2M Karras"))
        if idx_s >= 0:
            self.combo_sampler.setCurrentIndex(idx_s)
        elif pipe.get("sampler"):
            self.combo_sampler.addItem(pipe["sampler"])
            self.combo_sampler.setCurrentText(pipe["sampler"])

        self.spin_steps.setValue(pipe.get("steps", 25))
        self.spin_cfg.setValue(pipe.get("cfg_scale", 6.0))
        self.spin_clip_skip.setValue(pipe.get("clip_skip", 1))

        idx_u = self.combo_upscaler.findText(pipe.get("hires_upscaler", "4x-UltraSharp"))
        if idx_u >= 0:
            self.combo_upscaler.setCurrentIndex(idx_u)
        elif pipe.get("hires_upscaler"):
            self.combo_upscaler.addItem(pipe["hires_upscaler"])
            self.combo_upscaler.setCurrentText(pipe["hires_upscaler"])

        self.spin_hires_denoise.setValue(pipe.get("hires_denoise", 0.32))

        neg = pipe.get("negative_prompt", r.get("negative_prompt", ""))
        self.input_negative_prompt.setText(neg)

        self.input_desc.setPlainText(r.get("routing_description", ""))
        self.input_guidance.setPlainText(r.get("llm_guidance", ""))

        self.chk_is_default.setChecked(bool(r.get("is_default", False)))
        triggers = r.get("style_triggers", [])
        self.edit_style_triggers.setText(", ".join(triggers) if isinstance(triggers, list) else str(triggers))

        self.input_name.blockSignals(False)
        self.combo_checkpoint.blockSignals(False)
        self.combo_sampler.blockSignals(False)
        self.spin_steps.blockSignals(False)
        self.spin_cfg.blockSignals(False)
        self.spin_clip_skip.blockSignals(False)
        self.combo_upscaler.blockSignals(False)
        self.spin_hires_denoise.blockSignals(False)
        self.input_negative_prompt.blockSignals(False)
        self.input_desc.blockSignals(False)
        self.input_guidance.blockSignals(False)
        self.chk_is_default.blockSignals(False)
        self.edit_style_triggers.blockSignals(False)

    def _on_autofill_params_clicked(self):
        """
        Запускается СТРОГО по клику пользователя.
        Запрашивает Civitai в неблокирующем QThread или выставляет Safe Defaults при отсутствии сети.
        """
        checkpoint = self.combo_checkpoint.currentText().strip()
        if not checkpoint:
            QMessageBox.warning(self, "Внимание", "Сначала выберите файл чекпоинта модели.")
            return

        if self._civitai_worker is not None and self._civitai_worker.isRunning():
            return

        self.btn_autofill_params.setEnabled(False)
        self.btn_autofill_params.setText("⏳ Опрос Civitai...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        self._civitai_worker = CivitaiParamsWorker(checkpoint, parent=self)
        self._civitai_worker.params_ready.connect(self._on_civitai_params_ready)
        self._civitai_worker.error_occurred.connect(self._on_civitai_params_error)
        self._civitai_worker.start()

    def _on_civitai_params_ready(self, params: dict):
        try:
            # 1. Заполняем поля интерфейса полученными или дефолтными значениями
            idx_s = self.combo_sampler.findText(params.get("sampler", "DPM++ 2M Karras"))
            if idx_s >= 0:
                self.combo_sampler.setCurrentIndex(idx_s)
            elif params.get("sampler"):
                self.combo_sampler.addItem(params["sampler"])
                self.combo_sampler.setCurrentText(params["sampler"])

            self.spin_steps.setValue(params.get("steps", 25))
            self.spin_cfg.setValue(params.get("cfg_scale", 6.0))
            self.spin_clip_skip.setValue(params.get("clip_skip", 1))

            idx_u = self.combo_upscaler.findText(params.get("hires_upscaler", "4x-UltraSharp"))
            if idx_u >= 0:
                self.combo_upscaler.setCurrentIndex(idx_u)
            elif params.get("hires_upscaler"):
                self.combo_upscaler.addItem(params["hires_upscaler"])
                self.combo_upscaler.setCurrentText(params["hires_upscaler"])

            self.spin_hires_denoise.setValue(params.get("hires_denoise", 0.32))
            self.input_negative_prompt.setText(params.get("negative_prompt", ""))

            # Синхронизируем изменения с текущей выбранной моделью
            if self.current_selected_row >= 0:
                self._save_form_to_model(self.current_selected_row)

            # 2. Информируем пользователя о статусе
            status = params.get("status", "default")
            msg = params.get("message", "Параметры обновлены.")
            
            if status == "success":
                QMessageBox.information(self, "Успех", f"✅ {msg}")
            else:
                # Ошибка сети / таймаут / 404 — предупреждаем о применении дефолтов
                QMessageBox.warning(self, "Внимание (Параметры по умолчанию)", f"⚠️ {msg}")

        except Exception as e:
            logger.error(f"[ModelsManagerDialog] Сбой автонастройки: {e}")
            QMessageBox.warning(self, "Ошибка", f"Произошла ошибка: {e}\nПрименены стандартные параметры.")
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_autofill_params.setEnabled(True)
            self.btn_autofill_params.setText("🌐 Автонастройка параметров (Civitai)")

    def _on_civitai_params_error(self, err_msg: str):
        try:
            logger.error(f"[ModelsManagerDialog] Сбой автонастройки: {err_msg}")
            QMessageBox.warning(self, "Ошибка", f"Произошла ошибка: {err_msg}\nПрименены стандартные параметры.")
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_autofill_params.setEnabled(True)
            self.btn_autofill_params.setText("🌐 Автонастройка параметров (Civitai)")

    def _start_forge_scan(self, silent: bool = False):
        """Запускает фоновое сканирование Forge без блокировки главного потока интерфейса."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return

        self._scan_silent = silent
        if not silent:
            self.btn_refresh_forge.setEnabled(False)
            self.btn_refresh_forge.setText("⏳ Опрос Forge...")

        self._scan_worker = ForgeScanWorker(self.forge_client, parent=self)
        self._scan_worker.scan_completed.connect(self._on_forge_scan_completed)
        self._scan_worker.start()

    def _on_forge_scan_completed(self, raw_models: list, installed_list: list, upscalers: list):
        if not getattr(self, "_scan_silent", True):
            self.btn_refresh_forge.setEnabled(True)
            self.btn_refresh_forge.setText("🔄 Сканировать Forge")

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
                if Path(cp).name.lower() not in existing_ckpts:
                    stem = Path(cp).stem
                    new_model_data = {
                        "id": f"auto_{re.sub(r'[^a-zA-Z0-9_]', '_', stem.lower()).strip('_')}",
                        "name": stem,
                        "checkpoint": cp,
                        "routing_description": "Новая модель (требуется настройка описания для роутинга)",
                        "llm_guidance": "",
                        "negative_prompt": self._get_default_negative(cp, stem),
                        "active": True,
                        "pipeline": {
                            "sampler": "DPM++ 2M Karras",
                            "steps": 25,
                            "cfg_scale": 6.0,
                            "clip_skip": 1,
                            "hires_upscaler": "4x-UltraSharp",
                            "hires_denoise": 0.32,
                            "negative_prompt": self._get_default_negative(cp, stem)
                        }
                    }
                    self._add_model_row(new_model_data)
                    existing_ckpts.add(Path(cp).name.lower())
                    new_added += 1

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
        dlg.exec()

    def _on_civitai_model_installed(self, model_info: dict):
        """Автоматически регистрирует скачанную с Civitai модель в списке роутера."""
        ckpt_name = model_info.get("filename", "")
        if ckpt_name and ckpt_name not in self.available_checkpoints:
            self.available_checkpoints.append(ckpt_name)
            self.combo_checkpoint.addItem(ckpt_name)

        new_data = {
            "id": f"civitai_{model_info.get('id', uuid.uuid4().hex[:6])}",
            "name": model_info.get("name", "Новая модель"),
            "checkpoint": ckpt_name,
            "routing_description": model_info.get("description", ""),
            "llm_guidance": model_info.get("guidance", ""),
            "negative_prompt": self._get_default_negative(ckpt_name, model_info.get("name", "")),
            "active": True,
            "triggers": model_info.get("triggers", []),
            "pipeline": {
                "sampler": model_info.get("sampler", "DPM++ 2M Karras"),
                "steps": model_info.get("steps", 25),
                "cfg_scale": model_info.get("cfg_scale", 6.0),
                "clip_skip": 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
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
        """Fills table with model rows and selects first row."""
        self.table.setRowCount(0)
        self.row_widgets.clear()
        for m in self.models:
            self._add_model_row(m)

        if self.table.rowCount() > 0:
            self.table.selectRow(0)
            self.current_selected_row = 0
            self._load_model_to_form(0)

    def _add_model_row(self, model_data: Dict[str, Any]):
        """Adds a single interactive model row with widgets to the table."""
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self.table.setRowHeight(row_idx, 38)

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
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk = QCheckBox()
        chk.setChecked(bool(is_active))
        chk.setToolTip("Включить модель в автоматический семантический роутер")
        chk_layout.addWidget(chk)
        self.table.setCellWidget(row_idx, 0, chk_container)

        # Col 1: Model Name
        item_name = QTableWidgetItem(name)
        item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row_idx, 1, item_name)

        # Col 2: Checkpoint
        item_ckpt = QTableWidgetItem(checkpoint)
        item_ckpt.setFlags(item_ckpt.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row_idx, 2, item_ckpt)

        # Col 3: Delete Button
        btn_del = QPushButton("🗑️")
        btn_del.setObjectName("DeleteBtn")
        btn_del.setToolTip("Удалить модель")
        btn_del.setFixedSize(28, 28)
        btn_del.clicked.connect(lambda _, r_id=model_id: self._remove_model_by_id(r_id))
        del_container = QWidget()
        del_layout = QHBoxLayout(del_container)
        del_layout.setContentsMargins(0, 0, 0, 0)
        del_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

        row_info = {
            "id": model_id,
            "chk": chk,
            "name": name,
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
        """Adds a new blank model row."""
        if self.current_selected_row >= 0:
            self._save_form_to_model(self.current_selected_row)

        new_ckpt = self.available_checkpoints[0] if self.available_checkpoints else ""
        new_data = {
            "id": f"model_{uuid.uuid4().hex[:6]}",
            "name": "Новая модель",
            "checkpoint": new_ckpt,
            "routing_description": "",
            "llm_guidance": "",
            "negative_prompt": self._get_default_negative(new_ckpt, ""),
            "is_default": False,
            "style_triggers": [],
            "active": True,
            "pipeline": {
                "sampler": "DPM++ 2M Karras",
                "steps": 25,
                "cfg_scale": 6.0,
                "clip_skip": 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
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
        """Removes a model row from the table."""
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
            self.lbl_test_result.setText("<span style='color:#F59E0B;'>⚠️ Введите текст сцены для проверки.</span>")
            return

        # Снимаем актуальный in-memory снимок всех моделей из таблицы и формы
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
            self.lbl_test_result.setText("<span style='color:#EF4444;'>❌ В таблице нет ни одной активной модели (отметьте галочками 'Вкл').</span>")
            return

        self.btn_test_match.setEnabled(False)
        self.btn_test_match.setText("⏳ Расчёт...")
        self.lbl_test_result.setText("<i>Вычисление семантического сходства FastEmbed...</i>")

        self._test_worker = RoutingTestWorker(test_text, current_profiles, self.router, parent=self)
        self._test_worker.result_ready.connect(self._on_test_match_success)
        self._test_worker.error_occurred.connect(self._on_test_match_failed)
        self._test_worker.start()

    def _on_test_match_success(self, winner_id: str, score: float, reason: str, profiles: list):
        self.btn_test_match.setEnabled(True)
        self.btn_test_match.setText("🎯 Проверить выбор")

        winner_name = winner_id
        winner_ckpt = ""
        for p in profiles:
            if p.get("id") == winner_id:
                winner_name = p.get("name") or p.get("id")
                winner_ckpt = p.get("checkpoint", "")
                break

        if reason == "trigger":
            explanation = "<span style='color:#34D399;'>(совпадение по триггеру стиля)</span>"
        elif reason == "default":
            explanation = f"<span style='color:#38BDF8;'>(по умолчанию для нейтральных запросов, сходство: {score:.2f})</span>"
        elif reason == "vector":
            explanation = f"<span style='color:#00F0FF;'>(семантическое сходство FastEmbed: {score:.2f})</span>"
        else:
            explanation = f"<span style='color:#94A3B8;'>(фоллбэк: {score:.2f})</span>"

        ckpt_info = f" • <code>{winner_ckpt}</code>" if winner_ckpt else ""
        self.lbl_test_result.setText(
            f"🎯 Выбрана модель: <b style='color:#FFFFFF;'>{winner_name}</b>{ckpt_info} {explanation}"
        )
        self._test_worker = None

    def _on_test_match_failed(self, err_msg: str):
        self.btn_test_match.setEnabled(True)
        self.btn_test_match.setText("🎯 Проверить выбор")
        self.lbl_test_result.setText(f"<span style='color:#EF4444;'>❌ Ошибка: {err_msg}</span>")
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

    def _save_all(self):
        """
        Persists all models to data/art_director_models.json and updates router embeddings.
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
                pipe = {
                    **pipe,
                    "sampler": self.combo_sampler.currentText(),
                    "steps": self.spin_steps.value(),
                    "cfg_scale": round(self.spin_cfg.value(), 1),
                    "clip_skip": self.spin_clip_skip.value(),
                    "hires_upscaler": self.combo_upscaler.currentText(),
                    "hires_denoise": round(self.spin_hires_denoise.value(), 2),
                    "negative_prompt": self.input_negative_prompt.text().strip()
                }

            neg = pipe.get("negative_prompt") or r.get("negative_prompt", "")

            model_item = {
                "id": m_id,
                "name": name,
                "checkpoint": ckpt,
                "routing_description": desc,
                "llm_guidance": guidance,
                "negative_prompt": neg,
                "is_default": bool(r.get("is_default", False)),
                "style_triggers": r.get("style_triggers", []),
                "active": is_active,
                "triggers": r.get("triggers", []),
                "pipeline": {
                    "sampler": pipe.get("sampler", self.combo_sampler.currentText()),
                    "steps": pipe.get("steps", self.spin_steps.value()),
                    "cfg_scale": round(pipe.get("cfg_scale", self.spin_cfg.value()), 1),
                    "clip_skip": pipe.get("clip_skip", self.spin_clip_skip.value()),
                    "hires_upscaler": pipe.get("hires_upscaler", self.combo_upscaler.currentText()),
                    "hires_denoise": round(pipe.get("hires_denoise", self.spin_hires_denoise.value()), 2),
                    "negative_prompt": neg
                }
            }
            models_to_save.append(model_item)

        if not models_to_save:
            QMessageBox.warning(self, "Ошибка", "Таблица моделей пуста.")
            return

        # 1. Save to data/art_director_models.json via router and config manager
        self.router.save_profiles(models_to_save)
        save_art_director_models(models_to_save)

        # 2. Update FastEmbed embeddings
        if self.art_director and hasattr(self.art_director, "router") and self.art_director.router:
            self.art_director.router.update_embeddings()
        else:
            self.router.update_embeddings()

        # 3. Synchronize Forge model registry
        sync_registry_with_user_config()

        self.models = models_to_save
        QMessageBox.information(
            self,
            "Сохранено",
            f"Успешно сохранено {len(models_to_save)} моделей в data/art_director_models.json.\n"
            "Векторные эмбеддинги роутера обновлены!"
        )

    def closeEvent(self, event):
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.wait(150)
        if self._test_worker and self._test_worker.isRunning():
            self._test_worker.wait(150)
        super().closeEvent(event)
