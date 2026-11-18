"""
Thought Spoiler Widget for NeonRoute Assistant (Elaria).
Cyberpunk Glassmorphism styling:
- Translucent dark slate background (rgba(15, 23, 42, 0.65))
- Delicate neon Cyan/SkyBlue border (rgba(56, 189, 248, 0.25))
- Collapsible content area with monospaced muted text (#94A3B8)
- Real-time token streaming and status/duration metrics
"""

import re
from typing import Optional, Union
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QTextBrowser, QSizePolicy, QLayout
)
from qfluentwidgets import (
    FluentIcon as FIF,
    TransparentToolButton,
    StrongBodyLabel,
    CaptionLabel,
    ToolTipFilter,
    ToolTipPosition
)
from services.thought_styles import ThoughtStyleConfig, resolve_thought_style


def _color_to_border_rgba(color_str: str, alpha: float = 0.25) -> str:
    """Конвертирует hex или rgb строку цвета в rgba с заданной прозрачностью."""
    s = str(color_str or "").strip()
    if s.startswith("#"):
        c = s.lstrip("#")
        if len(c) == 6:
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            return f"rgba({r}, {g}, {b}, {alpha})"
        elif len(c) == 3:
            r, g, b = int(c[0]*2, 16), int(c[1]*2, 16), int(c[2]*2, 16)
            return f"rgba({r}, {g}, {b}, {alpha})"
    elif s.startswith("rgba"):
        return s
    elif s.startswith("rgb"):
        nums = re.findall(r'\d+', s)
        if len(nums) >= 3:
            return f"rgba({nums[0]}, {nums[1]}, {nums[2]}, {alpha})"
    return f"rgba(56, 189, 248, {alpha})"


def clean_thought_chunk(chunk: str) -> str:
    """Вырезает служебные теги инструментов из потока мыслей модели."""
    # Удаляем <<TOOL:write_file ...>> и <</TOOL>>
    cleaned = re.sub(r'<<?/?(?:TOOL|tool)(?::\w+)?(?:\s+[^>\r\n]*?)?>>?', '', chunk)
    # Удаляем маркеры Markdown-блоков кода и имена файлов
    cleaned = re.sub(r'`{3}(?:python|py)?(?:\s+filename=[^\r\n]+)?', '', cleaned)
    cleaned = re.sub(r'`{3}', '', cleaned)
    return cleaned


class ThoughtHeaderWidget(QWidget):
    """Кликабельная шапка спойлера с иконкой, заголовком и метриками."""
    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class ThoughtSpoilerWidget(QFrame):
    """
    Сворачиваемый виджет «Ход мыслей» в стилистике NeonRoute Glassmorphism.
    Размещается в бабле ассистента над основным текстом ответа.
    """

    toggled = pyqtSignal(bool)  # is_collapsed

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("ThoughtSpoilerWidget")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self._thought_text: str = ""
        self._is_collapsed: bool = True
        self._is_streaming: bool = True
        self._duration: float = 0.0
        self._token_count: int = 0
        self._style: ThoughtStyleConfig = resolve_thought_style(None)

        self._setup_ui()
        self.apply_style(self._style)
        self.set_collapsed(True)

    def _setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. Шапка спойлера
        self.header_widget = ThoughtHeaderWidget(self)
        self.header_layout = QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(10, 6, 8, 6)
        self.header_layout.setSpacing(8)

        # Иконка
        self.icon_lbl = QLabel(self._style.icon, self.header_widget)
        self.icon_lbl.setStyleSheet("font-size: 13px; background: transparent;")
        self.header_layout.addWidget(self.icon_lbl)

        # Заголовок
        self.title_lbl = StrongBodyLabel(self._style.collapsed_label, self.header_widget)
        self.title_lbl.setStyleSheet("color: #E2E8F0; font-size: 12px; font-weight: 600; background: transparent;")
        self.header_layout.addWidget(self.title_lbl)

        # Метрики генерации / бейдж статуса
        self.metrics_label = CaptionLabel(self._style.active_label, self.header_widget)
        self.metrics_label.setStyleSheet(f"color: {self._style.accent_color}; font-size: 11px; background: transparent;")
        self.header_layout.addWidget(self.metrics_label)

        self.header_layout.addStretch()

        # Кнопка сворачивания/разворачивания
        self.chevron_btn = TransparentToolButton(FIF.CHEVRON_RIGHT, self.header_widget)
        self.chevron_btn.setFixedSize(24, 24)
        self.chevron_btn.setToolTip("Развернуть ход мыслей")
        self.chevron_btn.installEventFilter(ToolTipFilter(self.chevron_btn, showDelay=150, position=ToolTipPosition.TOP))
        self.chevron_btn.clicked.connect(self.toggle_collapsed)
        self.header_layout.addWidget(self.chevron_btn)

        self.header_widget.clicked.connect(self.toggle_collapsed)
        self.main_layout.addWidget(self.header_widget)

        # 2. Область содержимого мыслей
        self.content_area = QFrame(self)
        self.content_area.setObjectName("ThoughtContentArea")
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(10, 0, 10, 8)
        self.content_layout.setSpacing(0)

        self.text_browser = QTextBrowser(self.content_area)
        self.text_browser.setObjectName("ThoughtTextBrowser")
        self.text_browser.setReadOnly(True)
        self.text_browser.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_browser.setMaximumHeight(260)
        self.text_browser.setMinimumHeight(40)
        self.text_browser.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        self.content_layout.addWidget(self.text_browser)
        self.main_layout.addWidget(self.content_area)

    def apply_style(self, style: ThoughtStyleConfig) -> None:
        """Применяет визуальный стиль блока рассуждений (Ассистент vs РП-архетипы)."""
        self._style = style
        if hasattr(self, "icon_lbl"):
            self.icon_lbl.setText(style.icon)
        if hasattr(self, "title_lbl"):
            self.title_lbl.setText(style.collapsed_label)

        if hasattr(self, "metrics_label"):
            if self._is_streaming:
                self.metrics_label.setText(style.active_label)
                self.metrics_label.setStyleSheet(f"color: {style.accent_color}; font-size: 11px; background: transparent;")
                self.metrics_label.setVisible(True)
            else:
                if style.is_rp:
                    self.metrics_label.setText("")
                    self.metrics_label.setVisible(False)
                    dur_text = f"{self._duration:.1f}".replace(",", ".")
                    self.setToolTip(f"{self._token_count} токенов • {dur_text} сек")
                else:
                    dur_text = f"{self._duration:.1f}".replace(",", ".")
                    self.metrics_label.setText(f"{self._token_count} токенов • {dur_text} сек")
                    self.metrics_label.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")
                    self.metrics_label.setVisible(True)
                    self.setToolTip("")

        self._apply_styles()

    def _apply_styles(self):
        style = getattr(self, "_style", None)
        if style is None:
            accent_color = "#38BDF8"
            border_rgba = "rgba(56, 189, 248, 0.25)"
            is_rp = False
        else:
            accent_color = style.accent_color
            border_rgba = _color_to_border_rgba(style.accent_color, 0.25)
            is_rp = style.is_rp

        if is_rp:
            text_browser_style = """
                #ThoughtTextBrowser {
                    background: transparent;
                    border: none;
                    color: #CBD5E1;
                    font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
                    font-style: italic;
                    font-size: 11px;
                    line-height: 1.45;
                    padding: 4px;
                }
            """
        else:
            text_browser_style = """
                #ThoughtTextBrowser {
                    background: transparent;
                    border: none;
                    color: #94A3B8;
                    font-family: 'Consolas', 'Fira Code', 'Cascadia Code', 'Segoe UI', monospace;
                    font-style: normal;
                    font-size: 11px;
                    line-height: 1.45;
                    padding: 4px;
                }
            """

        self.setStyleSheet(f"""
            #ThoughtSpoilerWidget {{
                background-color: rgba(15, 23, 42, 0.65);
                border: 1px solid {border_rgba};
                border-radius: 8px;
            }}
            #ThoughtContentArea {{
                background: transparent;
                border: none;
            }}
            {text_browser_style}
        """)

    @property
    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def get_thought_text(self) -> str:
        return self._thought_text

    def append_thought_chunk(self, chunk: str):
        """
        Дописывание входящего потока токенов рассуждений.
        Во время стриминга отображается активный статус.
        В режиме Ассистента спойлер разворачивается автоматически,
        в режиме РП остаётся аккуратно свёрнутым.
        """
        if not chunk:
            return

        cleaned = clean_thought_chunk(chunk)
        if not cleaned:
            return

        self._thought_text += cleaned
        self.text_browser.setPlainText(self._thought_text)

        # Автопрокрутка вниз
        sb = self.text_browser.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

        if self._is_streaming:
            self.metrics_label.setText(self._style.active_label)
            self.metrics_label.setStyleSheet(f"color: {self._style.accent_color}; font-size: 11px; background: transparent;")
            self.metrics_label.setVisible(True)
            if not self._style.is_rp:
                if self._is_collapsed:
                    self.set_collapsed(False)

    def finalize_thought(self, duration_sec: float = 0.0, token_count: Union[int, str] = 0):
        """
        Фиксация таймера мыслей, смена бейджа на результат
        и автоматическое аккуратное сворачивание спойлера по умолчанию.
        """
        self._is_streaming = False
        self._duration = max(0.0, float(duration_sec))
        if isinstance(token_count, str) and not token_count.isdigit():
            self._thought_text = clean_thought_chunk(token_count)
            self.text_browser.setPlainText(self._thought_text)
            self._token_count = max(1, len(self._thought_text.strip()) // 4)
        else:
            self._token_count = int(token_count)
            if self._token_count <= 0 and self._thought_text:
                self._token_count = max(1, len(self._thought_text.strip()) // 4)

        dur_text = f"{self._duration:.1f}".replace(",", ".")

        if self._style.is_rp:
            self.metrics_label.setText("")
            self.metrics_label.setVisible(False)
            if "·" not in self.title_lbl.text():
                self.title_lbl.setText(self._style.collapsed_label)
            self.setToolTip(f"{self._token_count} токенов • {dur_text} сек")
        else:
            self.metrics_label.setText(f"{self._token_count} токенов • {dur_text} сек")
            self.metrics_label.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")
            self.metrics_label.setVisible(True)
            if "·" not in self.title_lbl.text():
                self.title_lbl.setText(self._style.collapsed_label)

        # Автоматическое сворачивание после завершения
        self.set_collapsed(True)

    def set_thought_data(self, text: str, duration: float = 0.0, token_count: int = 0):
        """Восстановление мыслей из истории (в свёрнутом виде)."""
        clean_text = clean_thought_chunk(str(text or "")).strip()
        if not clean_text:
            self.setVisible(False)
            self.hide()
            return

        self._thought_text = clean_text
        self._is_streaming = False
        self._duration = max(0.0, float(duration))
        self._token_count = int(token_count)
        if self._token_count <= 0:
            self._token_count = max(1, len(clean_text) // 4)

        self.text_browser.setPlainText(clean_text)
        dur_text = f"{self._duration:.1f}".replace(",", ".")

        if self._style.is_rp:
            self.metrics_label.setText("")
            self.metrics_label.setVisible(False)
            if "·" not in self.title_lbl.text():
                self.title_lbl.setText(self._style.collapsed_label)
            self.setToolTip(f"{self._token_count} токенов • {dur_text} сек")
        else:
            self.metrics_label.setText(f"{self._token_count} токенов • {dur_text} сек")
            self.metrics_label.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")
            self.metrics_label.setVisible(True)
            if "·" not in self.title_lbl.text():
                self.title_lbl.setText(self._style.collapsed_label)

        self.set_collapsed(True)
        self.setVisible(True)
        self.show()

    def toggle_collapsed(self):
        """Переключение состояния спойлера (развернуть/свернуть)."""
        self.set_collapsed(not self._is_collapsed)

    def set_collapsed(self, collapsed: bool):
        """Установка состояния свёрнутости."""
        self._is_collapsed = collapsed
        self.content_area.setVisible(not collapsed)
        if collapsed:
            self.chevron_btn.setIcon(FIF.CHEVRON_RIGHT)
            self.chevron_btn.setToolTip("Развернуть ход мыслей")
        else:
            self.chevron_btn.setIcon(FIF.CHEVRON_DOWN_MED)
            self.chevron_btn.setToolTip("Свернуть ход мыслей")

        self.toggled.emit(self._is_collapsed)
        # Уведомляем ближайшего предка (ChatMessageWidget) об изменении геометрии
        parent = self.parentWidget()
        while parent is not None:
            if hasattr(parent, "_update_bubble_geometry"):
                parent._update_bubble_geometry()
                break
            parent = parent.parentWidget()
