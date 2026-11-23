"""
ui/models_manager/widgets.py — Кастомные виджеты и элементы управления диалога менеджера моделей.
"""
"""
Models Manager Dialog for NeonRoute.
Configures available Stable Diffusion Forge checkpoints, pipeline generation parameters,
and FastEmbed vector routing rules in modern Windows 11 / Glassmorphism aesthetic.
"""

import os
import subprocess
import logging
import uuid
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent
from PyQt6.QtGui import QMouseEvent, QColor
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
    CompactSpinBox,
    CompactDoubleSpinBox,
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

from ui.common.spinbox_utils import (
    CompactSamplingDoubleSpinBox,
    CompactSamplingSpinBox,
    sync_spinbox_lineedit_geometry,
)

from services.model_router import ModelRouter
from services.art_director_config import (
    load_art_director_models,
    save_art_director_models
)
from services.art_director.geometry import is_xl_model
from services.forge_client import ForgeClient
from services.forge_models import sync_registry_with_user_config

class SamplingSlider(Slider):
    """
    Кастомный слайдер для параметров рендера:
    - Плавное и естественное перетаскивание бегунка зажатой ЛКМ
    - Живой трекинг в реальном времени self.setTracking(True)
    - ClickFocus и отключение случайного wheelEvent
    """
    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None, accent_color: Optional[str] = None):
        super().__init__(orientation, parent=parent)
        self.setTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.handle.installEventFilter(self)
        c = QColor(accent_color or "#6366F1")
        self.setThemeColor(c, c)

    def eventFilter(self, watched, event):
        if watched == self.handle:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() != Qt.MouseButton.LeftButton:
                    return True
                self._pressedPos = self.handle.mapToParent(event.pos())
            elif event.type() == QEvent.Type.MouseMove:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    slider_pos = self.handle.mapToParent(event.pos())
                    self.setValue(self._posToValue(slider_pos))
                    self._pressedPos = slider_pos
                    self.sliderMoved.emit(self.value())
                    return True
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() != Qt.MouseButton.LeftButton:
                    return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            e.ignore()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        super().mouseReleaseEvent(e)

    def setValue(self, value: int):
        super().setValue(value)
        self._adjustHandlePos()

    def setRange(self, min_val: int, max_val: int):
        super().setRange(min_val, max_val)
        self._adjustHandlePos()

    def showEvent(self, event):
        super().showEvent(event)
        self._adjustHandlePos()

    def wheelEvent(self, e):
        e.ignore()


class SamplingDoubleSpinBox(CompactSamplingDoubleSpinBox):
    """
    Числовой счетчик с плавающей точкой (CFG Scale, Денойз Hires):
    - Компактные вертикальные стрелки
    - Белые цифры
    - Защита от сжатия lineEdit до 1 px (авто-синхронизация геометрии)
    - Отключение wheelEvent во избежание случайного скролла
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            SamplingDoubleSpinBox, CompactDoubleSpinBox {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 5px;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: 500;
                padding: 0px 24px 0 6px;
            }
            SamplingDoubleSpinBox:hover, CompactDoubleSpinBox:hover {
                background-color: rgba(255, 255, 255, 0.09);
                border-color: rgba(255, 255, 255, 0.22);
            }
            SamplingDoubleSpinBox:focus, CompactDoubleSpinBox:focus {
                border-color: rgba(99, 102, 241, 0.6);
                background-color: rgba(20, 24, 35, 0.95);
                color: #FFFFFF;
            }
            SamplingDoubleSpinBox QLineEdit, CompactDoubleSpinBox QLineEdit {
                color: #FFFFFF;
                background: transparent;
                border: none;
            }
        """)


class SamplingSpinBox(CompactSamplingSpinBox):
    """
    Целочисленный счетчик (Шаги, Clip Skip):
    - Компактные вертикальные стрелки
    - Белые цифры
    - Защита от сжатия lineEdit до 1 px (авто-синхронизация геометрии)
    - Отключение wheelEvent во избежание случайного скролла
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            SamplingSpinBox, CompactSpinBox {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 5px;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: 500;
                padding: 0px 24px 0 6px;
            }
            SamplingSpinBox:hover, CompactSpinBox:hover {
                background-color: rgba(255, 255, 255, 0.09);
                border-color: rgba(255, 255, 255, 0.22);
            }
            SamplingSpinBox:focus, CompactSpinBox:focus {
                border-color: rgba(99, 102, 241, 0.6);
                background-color: rgba(20, 24, 35, 0.95);
                color: #FFFFFF;
            }
            SamplingSpinBox QLineEdit, CompactSpinBox QLineEdit {
                color: #FFFFFF;
                background: transparent;
                border: none;
            }
        """)

logger = logging.getLogger("NeonRoute.ModelsManager")


class FluentEditableComboBox(EditableComboBox):
    """
    EditableComboBox с поддержкой setEditText для бесшовной обратной совместимости.
    """
    def setEditText(self, text: str):
        self.setText(text or "")


class FluentTextEdit(TextEdit):
    """
    TextEdit с методами text() и setText() для строковой совместимости.
    """
    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str):
        self.setPlainText(text or "")


class RoutingResultBadge(QFrame):
    """
    Стилизованный цветной бейдж для отображения результатов сопоставления модели:
    - Мгновенное совпадение по триггеру (изумрудный акцент)
    - Векторное сходство FastEmbed (неоновый циан)
    - Выбор по умолчанию для нейтральных запросов (лазурный)
    - Аварийный фоллбэк (сланцевый)
    - Ошибки сопоставления (кораллово-красный)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RoutingResultBadge")
        self._current_text = ""
        self._setup_ui()
        self.set_idle()

    def _setup_ui(self):
        self.setFixedHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        self.icon_lbl = QLabel(self)
        self.icon_lbl.setFixedWidth(24)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_lbl)

        self.text_lbl = QLabel(self)
        self.text_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.text_lbl.setWordWrap(True)
        layout.addWidget(self.text_lbl, stretch=1)

        self.tag_lbl = QLabel(self)
        self.tag_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tag_lbl.setVisible(False)
        layout.addWidget(self.tag_lbl)

    def set_idle(self):
        self._apply_style("idle")
        self.icon_lbl.setText("💡")
        self.text_lbl.setText(
            "<span style='color: #94A3B8; font-size: 11px;'><i>"
            "Введите запрос сцены и нажмите «Проверить выбор» для расчета векторного соответствия..."
            "</i></span>"
        )
        self.tag_lbl.setVisible(False)
        self._current_text = "Введите запрос сцены и нажмите «Проверить выбор» для расчета векторного соответствия..."

    def set_loading(self):
        self._apply_style("loading")
        self.icon_lbl.setText("⏳")
        self.text_lbl.setText(
            "<span style='color: #F59E0B; font-size: 11px;'><i>"
            "Вычисление семантического сходства FastEmbed на CPU..."
            "</i></span>"
        )
        self.tag_lbl.setVisible(False)
        self._current_text = "Вычисление семантического сходства FastEmbed на CPU..."

    def set_success(self, winner_name: str, winner_ckpt: str, reason: str, score: float):
        ckpt_str = f" • <span style='color: #64748B; font-family: Consolas, monospace;'>{winner_ckpt}</span>" if winner_ckpt else ""
        self.icon_lbl.setText("🎯")
        self.text_lbl.setText(
            f"<b style='color: #FFFFFF; font-size: 12px;'>{winner_name}</b>{ckpt_str}"
        )
        self.tag_lbl.setVisible(True)

        if reason == "trigger":
            self._apply_style("trigger")
            self.tag_lbl.setText("⚡ ТРИГГЕР СТИЛЯ")
            self.tag_lbl.setStyleSheet("""
                background-color: rgba(16, 185, 129, 0.20);
                color: #34D399;
                border: 1px solid rgba(16, 185, 129, 0.50);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
            """)
        elif reason == "default":
            self._apply_style("default")
            self.tag_lbl.setText(f"🔷 ПО УМОЛЧАНИЮ ({score:.2f})")
            self.tag_lbl.setStyleSheet("""
                background-color: rgba(56, 189, 248, 0.20);
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.50);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
            """)
        elif reason == "vector":
            self._apply_style("vector")
            self.tag_lbl.setText(f"🧠 FASTEMBED ({score:.2f})")
            self.tag_lbl.setStyleSheet("""
                background-color: rgba(99, 102, 241, 0.20);
                color: #A5B4FC;
                border: 1px solid rgba(99, 102, 241, 0.50);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
            """)
        else:
            self._apply_style("fallback")
            self.tag_lbl.setText(f"⚪ ФОЛЛБЭК ({score:.2f})")
            self.tag_lbl.setStyleSheet("""
                background-color: rgba(148, 163, 184, 0.20);
                color: #CBD5E1;
                border: 1px solid rgba(148, 163, 184, 0.50);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
            """)
        self._current_text = f"Выбрана модель: {winner_name} • {winner_ckpt} ({reason})"

    def set_error(self, message: str):
        self._apply_style("error")
        self.icon_lbl.setText("❌")
        self.text_lbl.setText(f"<span style='color: #F87171; font-size: 11px;'>{message}</span>")
        self.tag_lbl.setVisible(False)
        self._current_text = message

    def _apply_style(self, mode: str):
        border_color = "rgba(255, 255, 255, 0.08)"
        bg_color = "rgba(18, 24, 38, 0.65)"
        if mode == "trigger":
            border_color = "rgba(16, 185, 129, 0.40)"
            bg_color = "rgba(16, 185, 129, 0.08)"
        elif mode == "vector":
            border_color = "rgba(99, 102, 241, 0.40)"
            bg_color = "rgba(99, 102, 241, 0.08)"
        elif mode == "default":
            border_color = "rgba(56, 189, 248, 0.40)"
            bg_color = "rgba(56, 189, 248, 0.08)"
        elif mode == "error":
            border_color = "rgba(239, 68, 68, 0.40)"
            bg_color = "rgba(239, 68, 68, 0.08)"
        elif mode == "loading":
            border_color = "rgba(245, 158, 11, 0.40)"
            bg_color = "rgba(245, 158, 11, 0.08)"

        self.setStyleSheet(f"""
            QFrame#RoutingResultBadge {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
        """)

    def setText(self, text: str):
        self._current_text = text
        self.text_lbl.setText(text)

    def text(self) -> str:
        return self._current_text




# ============================================================================
# Пресеты параметров пайплайна генерации
# ============================================================================

MODEL_PRESETS = {
    "SDXL Turbo": {
        "architecture": "SDXL / Pony",
        "sampler": "LCM",
        "scheduler": "Karras",
        "steps": 10,
        "cfg_scale": 1.3,
        "clip_skip": 2,
        "enable_hr": False,
        "enable_adetailer": False,
    },
    "SDXL Classic": {
        "architecture": "SDXL / Pony",
        "sampler": "DPM++ 2M",
        "scheduler": "Karras",
        "steps": 28,
        "cfg_scale": 5.0,
        "clip_skip": 2,
        "enable_hr": False,
        "enable_adetailer": False,
    },
    "SD 1.5 Photoreal": {
        "architecture": "SD 1.5",
        "sampler": "DPM++ SDE Karras",
        "scheduler": "Karras",
        "steps": 25,
        "cfg_scale": 5.5,
        "clip_skip": 1,
        "enable_hr": True,
        "enable_adetailer": True,
    },
}


# ============================================================================
# Нормализаторы имен и вспомогательные функции
# ============================================================================

def normalize_checkpoint_stem(filename: str) -> str:
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


def get_default_negative_prompt(checkpoint_name: str = "", model_id: str = "") -> str:
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


# ============================================================================
# Фабричные методы ячеек таблицы моделей
# ============================================================================

def create_active_checkbox_cell(parent_widget=None, is_active: bool = True):
    """Создает контейнер с выровненным чекбоксом активности в роутере для ячейки таблицы."""
    chk_container = QWidget(parent_widget)
    chk_layout = QHBoxLayout(chk_container)
    chk_layout.setContentsMargins(6, 0, 6, 0)
    chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    chk = CheckBox(chk_container)
    chk.setChecked(bool(is_active))
    chk.setToolTip("Включить модель в автоматический семантический роутер")
    chk_layout.addWidget(chk)
    return chk_container, chk


def create_delete_button_cell(parent_widget=None, on_delete=None):
    """Создает контейнер со стилизованной кнопкой удаления для ячейки таблицы."""
    del_container = QWidget(parent_widget)
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
    if on_delete is not None:
        btn_del.clicked.connect(on_delete)
    del_layout.addWidget(btn_del)
    return del_container, btn_del
