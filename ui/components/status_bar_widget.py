"""
Status Bar Component for NeonRoute.
Encapsulates hardware metric labels (VRAM/RAM), server toggles, and status pills.
"""

from typing import TYPE_CHECKING
from PyQt6.QtWidgets import QStatusBar, QLabel, QPushButton
from ui.components.status_pill import StatusPill

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class MainStatusBar(QStatusBar):
    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.setSizeGripEnabled(False)
        self._setup_ui()

    def _setup_ui(self):
        # 1. Hardware Metrics
        self.lbl_vram = QLabel("VRAM: -- / 8192 MB (0%)", self)
        self.lbl_vram.setStyleSheet("color: #00F0FF; font-weight: bold; margin-right: 15px;")
        self.addPermanentWidget(self.lbl_vram)

        self.lbl_ram = QLabel("RAM: -- / 16.0 GB (0%)", self)
        self.lbl_ram.setStyleSheet("color: #94A3B8; margin-right: 20px;")
        self.addPermanentWidget(self.lbl_ram)

        # 2. LM Studio Process Controls & Status Pill
        self.btn_lms_toggle = QPushButton("⚡ Запустить LMS", self)
        self.btn_lms_toggle.setToolTip("Запустить сервер LM Studio (lms server start)")
        self.btn_lms_toggle.setStyleSheet("""
            QPushButton {
                background-color: #111A28;
                color: #38BDF8;
                border: 1px solid #1E3A5F;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                border-color: #38BDF8;
                background-color: #17253B;
            }
        """)
        self.btn_lms_toggle.clicked.connect(self.window._toggle_lms_server)
        self.addPermanentWidget(self.btn_lms_toggle)

        self.pill_lm = StatusPill("LM Studio", 1234, self)
        self.addPermanentWidget(self.pill_lm)

        # 3. SD WebUI Forge Process Controls & Status Pill
        self.btn_forge_toggle = QPushButton("▶ Запустить Forge", self)
        self.btn_forge_toggle.setToolTip("Запустить SD WebUI Forge (C:\\neyroGPD\\forge_launcher.bat)")
        self.btn_forge_toggle.setStyleSheet("""
            QPushButton {
                background-color: #111A28;
                color: #00F0FF;
                border: 1px solid #00F0FF;
                border-radius: 4px;
                padding: 2px 9px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #00F0FF;
                color: #050B14;
            }
        """)
        self.btn_forge_toggle.clicked.connect(self.window._toggle_forge_server)
        self.addPermanentWidget(self.btn_forge_toggle)

        self.pill_forge = StatusPill("Forge", 7860, self)
        self.addPermanentWidget(self.pill_forge)

        # 4. F5-TTS Status Pill
        self.pill_tts = StatusPill("F5-TTS", 5010, self)
        self.addPermanentWidget(self.pill_tts)

        self.showMessage("NeonRoute готов к работе.")
