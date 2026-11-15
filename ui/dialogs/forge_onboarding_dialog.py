"""
ui/dialogs/forge_onboarding_dialog.py
=====================================
Fluent-диалог первичного онбординга SD WebUI Forge для систем с дискретным NVIDIA GPU (>= 3800 МБ).
Предлагает скачать Forge, указать существующую папку или остаться в текстовом режиме.
"""

import logging
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
    QFrame,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
    IconWidget,
    FluentTitleBar,
)

from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import toast_info

logger = logging.getLogger("NeonRoute.ForgeOnboardingDialog")

FORGE_GITHUB_URL = "https://github.com/lllyasviel/stable-diffusion-webui-forge/releases"


class ForgeOnboardingDialog(FramelessDialog):
    """
    Модальный Fluent-диалог умного онбординга SD WebUI Forge:
    - Информирует о наличии мощного GPU NVIDIA;
    - Показывает спец-подсказки для 4 ГБ VRAM или Blackwell RTX 50xx;
    - 3 действия: Скачать Forge / Указать папку / Только текстовый режим.
    """

    def __init__(
        self,
        gpu_name: str,
        vram_mb: int,
        compute_cap: str = "N/A",
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent=parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.gpu_name = gpu_name
        self.vram_mb = vram_mb
        self.compute_cap = compute_cap
        self.choice: str = "text_only"

        self.setWindowTitle("Обнаружена видеокарта NVIDIA — Подключение SD Forge")
        self.setObjectName("ForgeOnboardingDialog")
        self.resize(540, 420)
        self.setMinimumSize(480, 380)
        self.setModal(True)

        # TitleBar
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(44)
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
            self.titleBar.closeBtn.clicked.connect(self._on_close_clicked)

        self._init_ui()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
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
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)

        self.setStyleSheet(f"""
            ForgeOnboardingDialog#ForgeOnboardingDialog,
            ForgeOnboardingDialog {{
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
            QWidget#OnboardingBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #OnboardingBodyWidget QWidget {{
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
            PushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 14px;
                min-height: 24px;
            }}
            PushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 12px;
                padding: 6px 16px;
                min-height: 24px;
            }}
            PrimaryPushButton:hover {{
                background-color: {palette['group_title']};
                border-color: {palette['group_title']};
            }}
        """)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("OnboardingBodyWidget")
        main_layout = QVBoxLayout(self.body_widget)
        main_layout.setContentsMargins(24, 14, 24, 20)
        main_layout.setSpacing(12)

        # 1. Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        icon = IconWidget(FIF.PALETTE, self.body_widget)
        icon.setFixedSize(26, 26)
        header_layout.addWidget(icon)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("ГЕНЕРАЦИЯ ИЛЛЮСТРАЦИЙ И АВАТАРОВ", self.body_widget)
        title_lbl.setObjectName("dialogTitle")
        title_vbox.addWidget(title_lbl)

        sub_lbl = CaptionLabel("Подключение локального визуального движка SD WebUI Forge", self.body_widget)
        sub_lbl.setObjectName("dialogSubtitle")
        title_vbox.addWidget(sub_lbl)
        header_layout.addLayout(title_vbox)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # 2. Hardware info card
        hw_card = QFrame(self.body_widget)
        hw_card.setObjectName("hwCard")
        hw_box = QVBoxLayout(hw_card)
        hw_box.setContentsMargins(14, 12, 14, 12)
        hw_box.setSpacing(6)

        hw_top = QHBoxLayout()
        hw_top.setSpacing(8)
        gpu_icon = IconWidget(FIF.APPLICATION, hw_card)
        gpu_icon.setFixedSize(20, 20)
        hw_top.addWidget(gpu_icon)

        hw_name_lbl = BodyLabel(f"{self.gpu_name} ({self.vram_mb} МБ VRAM)", hw_card)
        hw_name_lbl.setStyleSheet("font-weight: bold; color: #10B981;")
        hw_top.addWidget(hw_name_lbl)
        hw_top.addStretch()
        hw_box.addLayout(hw_top)

        # Special hints for 4 GB or Blackwell CC >= 10.0
        extra_hints = []
        if 3800 <= self.vram_mb <= 5500:
            extra_hints.append(
                "💡 Режим 4 ГБ: NeonRoute автоматически применит экономичный профиль "
                "(резерв 2 ГБ + FP8) для стабильной генерации без WDDM paging."
            )

        try:
            cc_float = float(self.compute_cap) if self.compute_cap != "N/A" else 0.0
        except ValueError:
            cc_float = 0.0

        if cc_float >= 10.0 or "RTX 50" in self.gpu_name:
            extra_hints.append(
                "⚡ Архитектура Blackwell (Compute Capability 12.x / 10.x): "
                "рекомендуется сборка Forge с PyTorch 2.6+ и CUDA 12.8+."
            )

        info_text = (
            "Ваша видеокарта NVIDIA поддерживает аппаратную генерацию артов через SD WebUI Forge "
            "с бесшовным арбитражем видеопамяти (VRAM Arbiter). Вы можете подключить Forge прямо сейчас:"
        )
        body_text_lbl = CaptionLabel(info_text, hw_card)
        body_text_lbl.setWordWrap(True)
        body_text_lbl.setStyleSheet("color: #E2E8F0; font-size: 11px; line-height: 1.4;")
        hw_box.addWidget(body_text_lbl)

        for hint in extra_hints:
            h_lbl = CaptionLabel(hint, hw_card)
            h_lbl.setWordWrap(True)
            h_lbl.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 500;")
            hw_box.addWidget(h_lbl)

        main_layout.addWidget(hw_card)
        main_layout.addStretch()

        # 3. Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_text_only = PushButton("Только текстовый режим", self.body_widget)
        self.btn_text_only.clicked.connect(self._on_text_only_clicked)
        btn_layout.addWidget(self.btn_text_only)

        self.btn_locate = PushButton(FIF.FOLDER, "У меня уже есть Forge", self.body_widget)
        self.btn_locate.clicked.connect(self._on_locate_clicked)
        btn_layout.addWidget(self.btn_locate)

        self.btn_download = PrimaryPushButton(FIF.DOWNLOAD, "Скачать Forge (GitHub)", self.body_widget)
        self.btn_download.clicked.connect(self._on_download_clicked)
        btn_layout.addWidget(self.btn_download)

        main_layout.addLayout(btn_layout)
        root_layout.addWidget(self.body_widget)

    def _on_download_clicked(self):
        self.choice = "download"
        QDesktopServices.openUrl(QUrl(FORGE_GITHUB_URL))
        self.accept()

    def _on_locate_clicked(self):
        self.choice = "locate"
        self.accept()

    def _on_text_only_clicked(self):
        self.choice = "text_only"
        self.accept()

    def _on_close_clicked(self):
        self.choice = "text_only"
        self.reject()
