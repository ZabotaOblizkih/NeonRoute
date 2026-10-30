"""
Кнопка перевода со встроенным пошаговым индикатором прогресса
на базе компонентов PyQt-Fluent-Widgets (ProgressRing, ProgressBar, PushButton).
"""
from typing import Optional
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import pyqtSignal, QObject
from PyQt6.QtGui import QIcon
from qfluentwidgets import PushButton, ProgressRing, ProgressBar, FluentIcon as FIF


class CardProgressBridge(QObject):
    """Потокобезопасный мост для передачи сигналов прогресса из фонового воркера в GUI."""
    progress_changed = pyqtSignal(int, int, str)


class StepProgressButton(PushButton):
    """
    Кнопка Fluent Design со встроенным круговым кольцом прогресса (16x16)
    и нижней линией ProgressBar (3px), отображающая текущий слот карточки.
    """
    SLOT_TITLES = {
        "DESCRIPTION": "Описание",
        "PERSONALITY": "Личность",
        "SCENARIO": "Сценарий",
        "MES_EXAMPLE": "Диалоги",
        "FIRST_MES": "Приветствие",
    }

    def __init__(
        self,
        icon=FIF.LANGUAGE,
        text: str = "🌐 Перевести всё (RU)",
        parent: Optional[QWidget] = None
    ):
        # Инициализируем только parent, чтобы исключить рекурсию перегрузок @overload
        super().__init__(parent=parent)
        self.setText(text)
        self.setIcon(icon)
        self._default_icon = icon
        self._default_text = text
        self.setFixedHeight(32)
        self.setMinimumWidth(220)

        # 1. Круговой индикатор 16x16 слева
        self.ring = ProgressRing(self)
        self.ring.setTextVisible(False)
        self.ring.setFixedSize(16, 16)
        self.ring.setStrokeWidth(2.5)
        self.ring.hide()

        # 2. Микро-полоса ProgressBar (3px) по нижней кромке
        self.bar = ProgressBar(self)
        self.bar.setFixedHeight(3)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bar.setGeometry(0, self.height() - 3, self.width(), 3)
        self.ring.move(10, (self.height() - 16) // 2)

    def update_step_progress(self, current: int, total: int, slot_key: str):
        """Слот обновления прогресса: вызывается в главном GUI-потоке."""
        self.setEnabled(False)
        self.setIcon(QIcon())  # освобождаем место под ProgressRing
        self.setStyleSheet("text-align: left; padding-left: 34px;")
        self.ring.show()
        self.bar.show()

        if slot_key.startswith("ALT_GREETING_"):
            num = slot_key.split("_")[-1]
            title = f"Альт. приветствие {num}"
        else:
            title = self.SLOT_TITLES.get(slot_key.upper(), slot_key)

        percent = int((current / max(1, total)) * 100)
        self.ring.setValue(percent)
        self.bar.setValue(percent)
        self.setText(f"{title} ({current}/{total})...")

    def reset_state(self, success: bool = True):
        """Сбрасывает состояние кнопки после окончания конвейера."""
        self.ring.hide()
        self.bar.hide()
        self.setStyleSheet("")
        self.setEnabled(True)
        if success:
            self.setIcon(self._default_icon)
            self.setText(self._default_text)
        else:
            self.setIcon(FIF.CANCEL)
            self.setText("❌ Сбой перевода")
