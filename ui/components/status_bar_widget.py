"""
Status Bar Component for NeonRoute.
Encapsulates hardware telemetry (VRAM/RAM HUD Chips), server process controls,
and service health indicators modernized with QFluentWidgets and Cyberpunk Glassmorphism.
"""

from typing import TYPE_CHECKING, Optional
from config import EMERGENCY_VRAM_THRESHOLD_MB, FORGE_LAUNCHER_PATH
import re
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import QStatusBar, QWidget, QHBoxLayout, QLabel, QFrame
from qfluentwidgets import (
    ProgressBar,
    PillPushButton,
    DotInfoBadge,
    InfoLevel,
    ToolTipFilter,
    ToolTipPosition,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ServiceStatusBadge(QWidget):
    """
    Индикатор статуса службы: микро-светодиод DotInfoBadge,
    лаконичная подпись и стилизация в виде аккуратной капсулы (Pill Badge).
    """

    def __init__(self, name: str, port: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.name = name
        self.port = port
        self.is_online = False
        self.setFixedHeight(24)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 10, 2)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Dot info badge (зеленый / красный)
        self.dot = DotInfoBadge(self)
        self.dot.setFixedSize(6, 6)
        self.dot.setLevel(InfoLevel.ERROR)
        self.dot.setCustomBackgroundColor("#EF4444", "#EF4444")
        self.dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.dot)

        # Имя сервиса и порт
        self.label = QLabel(f"{self.name} ({self.port})", self)
        self.label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.label)

        self.setStyleSheet("""
            ServiceStatusBadge {
                background-color: rgba(17, 24, 39, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            ServiceStatusBadge:hover {
                border-color: rgba(0, 240, 255, 0.35);
                background-color: rgba(17, 24, 39, 0.90);
            }
        """)

        self.setToolTip(f"{self.name} ({self.port}) — Офлайн: Сервис недоступен")
        self.installEventFilter(ToolTipFilter(self, showDelay=250, position=ToolTipPosition.TOP))

    def set_status(self, online: bool, message: str = ""):
        self.is_online = online
        if online:
            self.dot.setLevel(InfoLevel.SUCCESS)
            self.dot.setCustomBackgroundColor("#10B981", "#10B981")
            self.label.setStyleSheet("color: #E2E8F0; font-size: 11px; font-weight: 500;")
            self.setToolTip(f"{self.name} ({self.port}) — Онлайн ({message or 'Готов к работе'})")
        else:
            self.dot.setLevel(InfoLevel.ERROR)
            self.dot.setCustomBackgroundColor("#EF4444", "#EF4444")
            self.label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
            self.setToolTip(f"{self.name} ({self.port}) — Офлайн ({message or 'Сервис недоступен'})")


StatusPill = ServiceStatusBadge


class VramLabel(QLabel):
    """
    Метка телеметрии VRAM с двухсторонней синхронизацией со шкалой и динамической подсветкой капсулы.
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None, progress_bar: Optional[ProgressBar] = None, card: Optional[QFrame] = None):
        super().__init__(text, parent)
        self.progress_bar = progress_bar
        self.card = card
        self._current_color = "#10B981"

    def update_vram(self, v_used: int, v_total: int, v_pct: float, temp: Optional[int] = None):
        free_mb = max(0, v_total - v_used)
        if free_mb >= 1200:
            color = "#10B981"
        elif EMERGENCY_VRAM_THRESHOLD_MB <= free_mb < 1200:
            color = "#F59E0B"
        else:
            color = "#EF4444"

        self._current_color = color
        temp_str = f" • {temp}°C" if temp else ""
        super().setText(f"VRAM: {v_used} / {v_total} MB ({v_pct}%){temp_str}")
        super().setStyleSheet(f"color: {color}; font-weight: 600; font-size: 11px;")

        if self.card:
            self.card.setStyleSheet(f"""
                QFrame#VramCapsule {{
                    background-color: rgba(17, 24, 39, 0.75);
                    border: 1px solid {color}55;
                    border-radius: 12px;
                }}
            """)

        if self.progress_bar:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(round(v_pct)))
            self.progress_bar.setCustomBarColor(color, color)
            self.progress_bar.setToolTip(f"VRAM: {v_used} / {v_total} MB ({v_pct}%) | Свободно: {free_mb} MB{temp_str}")

    def setText(self, text: str):
        m = re.search(r"VRAM:\s*(\d+)\s*/\s*(\d+)\s*MB\s*\(([0-9.]+)%\)", text)
        if m:
            v_used = int(m.group(1))
            v_total = int(m.group(2))
            v_pct = float(m.group(3))
            m_temp = re.search(r"•\s*(\d+)\s*°C", text)
            temp = int(m_temp.group(1)) if m_temp else None
            self.update_vram(v_used, v_total, v_pct, temp)
        else:
            super().setText(text)

    def setStyleSheet(self, style: str):
        super().setStyleSheet(f"color: {self._current_color}; font-weight: 600; font-size: 11px;")


class RamLabel(QLabel):
    """
    Метка телеметрии системной RAM со шкалой.
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None, progress_bar: Optional[ProgressBar] = None, card: Optional[QFrame] = None):
        super().__init__(text, parent)
        self.progress_bar = progress_bar
        self.card = card

    def update_ram(self, r_used: float, r_total: float, r_pct: float):
        super().setText(f"RAM: {r_used:.1f} / {r_total:.1f} GB ({r_pct}%)")
        super().setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500;")

        if self.progress_bar:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(round(r_pct)))
            self.progress_bar.setCustomBarColor("#38BDF8", "#38BDF8")
            self.progress_bar.setToolTip(f"RAM: {r_used:.1f} / {r_total:.1f} GB ({r_pct}%)")

    def setText(self, text: str):
        m = re.search(r"RAM:\s*([0-9.]+)\s*/\s*([0-9.]+)\s*GB\s*\(([0-9.]+)%\)", text)
        if m:
            r_used = float(m.group(1))
            r_total = float(m.group(2))
            r_pct = float(m.group(3))
            self.update_ram(r_used, r_total, r_pct)
        else:
            super().setText(text)


class MainStatusBar(QStatusBar):
    """
    Неоновый Glassmorphism Status Bar для NeonRoute.
    Объединяет телеметрию VRAM/RAM в единые HUD-капсулы,
    кнопки серверов LLM/Forge и LED-индикаторы сервисов.
    """

    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.setSizeGripEnabled(False)
        self.setFixedHeight(34)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        # 1. VRAM HUD Capsule Chip
        self.vram_card = QFrame(self)
        self.vram_card.setObjectName("VramCapsule")
        self.vram_card.setFixedHeight(24)
        self.vram_card.setStyleSheet("""
            QFrame#VramCapsule {
                background-color: rgba(17, 24, 39, 0.75);
                border: 1px solid rgba(16, 185, 129, 0.30);
                border-radius: 12px;
            }
        """)
        vram_layout = QHBoxLayout(self.vram_card)
        vram_layout.setContentsMargins(10, 0, 10, 0)
        vram_layout.setSpacing(6)
        vram_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.bar_vram = ProgressBar(self.vram_card)
        self.bar_vram.setFixedWidth(64)
        self.bar_vram.setFixedHeight(5)
        self.bar_vram.setRange(0, 100)
        self.bar_vram.setValue(0)
        self.bar_vram.setToolTip("Загрузка VRAM")
        self.bar_vram.installEventFilter(ToolTipFilter(self.bar_vram, showDelay=250, position=ToolTipPosition.TOP))

        self.lbl_vram = VramLabel("VRAM: -- / 8192 MB (0%)", self.vram_card, progress_bar=self.bar_vram, card=self.vram_card)
        self.lbl_vram.setStyleSheet("color: #10B981; font-weight: 600; font-size: 11px;")
        vram_layout.addWidget(self.lbl_vram)
        vram_layout.addWidget(self.bar_vram)

        self.addPermanentWidget(self.vram_card)

        # 2. RAM HUD Capsule Chip
        self.ram_card = QFrame(self)
        self.ram_card.setObjectName("RamCapsule")
        self.ram_card.setFixedHeight(24)
        self.ram_card.setStyleSheet("""
            QFrame#RamCapsule {
                background-color: rgba(17, 24, 39, 0.75);
                border: 1px solid rgba(56, 189, 248, 0.30);
                border-radius: 12px;
            }
        """)
        ram_layout = QHBoxLayout(self.ram_card)
        ram_layout.setContentsMargins(10, 0, 10, 0)
        ram_layout.setSpacing(6)
        ram_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.bar_ram = ProgressBar(self.ram_card)
        self.bar_ram.setFixedWidth(56)
        self.bar_ram.setFixedHeight(5)
        self.bar_ram.setRange(0, 100)
        self.bar_ram.setValue(0)
        self.bar_ram.setToolTip("Загрузка RAM")
        self.bar_ram.installEventFilter(ToolTipFilter(self.bar_ram, showDelay=250, position=ToolTipPosition.TOP))

        self.lbl_ram = RamLabel("RAM: -- / 16.0 GB (0%)", self.ram_card, progress_bar=self.bar_ram, card=self.ram_card)
        self.lbl_ram.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500;")
        ram_layout.addWidget(self.lbl_ram)
        ram_layout.addWidget(self.bar_ram)

        self.addPermanentWidget(self.ram_card)

        # 3. LLM Process Controls & Status Badge
        self.btn_lms_toggle = PillPushButton("⚡ Старт LLM", self)
        self.btn_lms_toggle.setCheckable(False)
        self.btn_lms_toggle.setFixedHeight(24)
        self.btn_lms_toggle.setToolTip("Запустить / остановить локальный сервер llama-server.exe (порт 1234)")
        self.btn_lms_toggle.installEventFilter(ToolTipFilter(self.btn_lms_toggle, showDelay=250, position=ToolTipPosition.TOP))
        self.btn_lms_toggle.setStyleSheet("""
            PillPushButton, QPushButton {
                background-color: rgba(17, 26, 40, 0.85);
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.40);
                border-radius: 12px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            PillPushButton:hover, QPushButton:hover {
                border-color: #38BDF8;
                background-color: rgba(56, 189, 248, 0.18);
            }
        """)
        if hasattr(self.window, "_toggle_lms_server"):
            self.btn_lms_toggle.clicked.connect(self.window._toggle_lms_server)
        self.addPermanentWidget(self.btn_lms_toggle)

        self.pill_lm = ServiceStatusBadge("LLM Server", 1234, self)
        self.addPermanentWidget(self.pill_lm)

        # 4. SD WebUI Forge Process Controls & Status Badge
        self.btn_forge_toggle = PillPushButton("▶ Запустить Forge", self)
        self.btn_forge_toggle.setCheckable(False)
        self.btn_forge_toggle.setFixedHeight(24)
        self.btn_forge_toggle.setToolTip(f"Запустить SD WebUI Forge ({FORGE_LAUNCHER_PATH})")
        self.btn_forge_toggle.installEventFilter(ToolTipFilter(self.btn_forge_toggle, showDelay=250, position=ToolTipPosition.TOP))
        self.btn_forge_toggle.setStyleSheet("""
            PillPushButton, QPushButton {
                background-color: rgba(17, 26, 40, 0.85);
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.45);
                border-radius: 12px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            PillPushButton:hover, QPushButton:hover {
                background-color: #00F0FF;
                color: #050B14;
            }
        """)
        if hasattr(self.window, "_toggle_forge_server"):
            self.btn_forge_toggle.clicked.connect(self.window._toggle_forge_server)
        self.addPermanentWidget(self.btn_forge_toggle)

        self.pill_forge = ServiceStatusBadge("Forge", 7860, self)
        self.addPermanentWidget(self.pill_forge)

        # 5. F5-TTS Status Badge
        self.pill_tts = ServiceStatusBadge("F5-TTS", 5010, self)
        self.addPermanentWidget(self.pill_tts)

        self.showMessage("NeonRoute готов к работе.")

    def _connect_signals(self):
        if hasattr(self.window, "system_monitor") and self.window.system_monitor is not None:
            self.window.system_monitor.stats_updated.connect(self._on_hardware_stats)
        if hasattr(self.window, "health_worker") and self.window.health_worker is not None:
            self.window.health_worker.health_updated.connect(self._on_health_updated)

    @pyqtSlot(dict)
    def _on_hardware_stats(self, stats: dict):
        v_used = stats.get("vram_used_mb", 0)
        v_total = stats.get("vram_total_mb", 8192)
        v_pct = stats.get("vram_percent", 0.0)
        temp = stats.get("gpu_temp_c")

        if hasattr(self.lbl_vram, "update_vram"):
            self.lbl_vram.update_vram(v_used, v_total, v_pct, temp)

        r_used = stats.get("ram_used_gb", 0.0)
        r_total = stats.get("ram_total_gb", 16.0)
        r_pct = stats.get("ram_percent", 0.0)

        if hasattr(self.lbl_ram, "update_ram"):
            self.lbl_ram.update_ram(r_used, r_total, r_pct)

    @pyqtSlot(dict)
    def _on_health_updated(self, health: dict):
        lm_ok = health.get("lm_ok", False)
        lm_msg = health.get("lm_msg", "LM Studio Offline")
        self.pill_lm.set_status(lm_ok, lm_msg)

        forge_ok = health.get("forge_ok", False)
        forge_msg = health.get("forge_msg", "SD Forge Offline")
        self.pill_forge.set_status(forge_ok, forge_msg)

        tts_ok = health.get("tts_ok", False)
        tts_msg = health.get("tts_msg", "F5-TTS Offline")
        self.pill_tts.set_status(tts_ok, tts_msg)
