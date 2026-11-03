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

class SamplingSlider(Slider):
    """
    Кастомный слайдер для параметров рендера:
    - Плавное и естественное перетаскивание бегунка зажатой ЛКМ
    - Живой трекинг в реальном времени self.setTracking(True)
    - ClickFocus и отключение случайного wheelEvent
    """
    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent=parent)
        self.setTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.handle.installEventFilter(self)

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

    def wheelEvent(self, e):
        e.ignore()


class SamplingDoubleSpinBox(DoubleSpinBox):
    """
    Числовой счетчик с плавающей точкой (CFG Scale, Денойз Hires):
    - Стрелки с точным шагом
    - Отключение wheelEvent во избежание случайного скролла
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        try:
            self.downButton.clicked.disconnect()
        except Exception:
            pass
        self.downButton.clicked.connect(self.stepDown)
        try:
            self.upButton.clicked.disconnect()
        except Exception:
            pass
        self.upButton.clicked.connect(self.stepUp)

    def stepDown(self):
        new_val = max(self.minimum(), self.value() - self.singleStep())
        self.setValue(round(new_val, self.decimals()))

    def stepUp(self):
        new_val = min(self.maximum(), self.value() + self.singleStep())
        self.setValue(round(new_val, self.decimals()))

    def wheelEvent(self, e):
        e.ignore()


class SamplingSpinBox(SpinBox):
    """
    Целочисленный счетчик (Шаги, Clip Skip):
    - Стрелки с точным шагом
    - Отключение wheelEvent во избежание случайного скролла
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        try:
            self.downButton.clicked.disconnect()
        except Exception:
            pass
        self.downButton.clicked.connect(self.stepDown)
        try:
            self.upButton.clicked.disconnect()
        except Exception:
            pass
        self.upButton.clicked.connect(self.stepUp)

    def stepDown(self):
        new_val = max(self.minimum(), self.value() - self.singleStep())
        self.setValue(int(new_val))

    def stepUp(self):
        new_val = min(self.maximum(), self.value() + self.singleStep())
        self.setValue(int(new_val))

    def wheelEvent(self, e):
        e.ignore()

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
                background-color: rgba(0, 240, 255, 0.20);
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.50);
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
            border_color = "rgba(0, 240, 255, 0.40)"
            bg_color = "rgba(0, 240, 255, 0.08)"
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


