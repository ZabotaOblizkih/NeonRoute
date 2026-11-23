"""
Model Sampling Parameters Dialog for NeonRoute.
Configures LLM sampling parameters:
Temperature, Min-P, Top-P, Repetition Penalty, DRY, XTC, and Max Tokens.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

from typing import Dict, Any, Optional
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QApplication,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    Slider,
    DoubleSpinBox,
    SpinBox,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
)

from services.session_manager import get_default_sampling_params
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.spinbox_utils import (
    SamplingDoubleSpinBox,
    SamplingSpinBox,
    sync_spinbox_lineedit_geometry,
)


class SamplingSlider(Slider):
    """
    Кастомный слайдер для параметров сэмплинга:
    - Плавное и естественное перетаскивание бегунка зажатой ЛКМ (нативная логика Slider)
    - Включен трекинг реального времени self.setTracking(True)
    - Защита от смещения значения при простом движении мыши (hover) без нажатия
    - ClickFocus и отключение wheelEvent
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
        # Реагируем на перемещение СТРОГО при зажатой левой кнопке мыши
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
        # Отключаем случайное изменение значений колесиком мыши
        e.ignore()


class ModelSamplingDialog(FramelessDialog):
    """
    Диалоговое окно настройки параметров сэмплинга текстовой модели NeonRoute
    в стиле Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette):
    - Температура (Temperature: 0.10 - 2.00, шаг 0.05)
    - Min-P Sampling (0.00 - 0.20, шаг 0.01)
    - Ядерная выборка (Top-P: 0.10 - 1.00, шаг 0.01)
    - Штраф за повторы (Repetition Penalty: 1.00 - 1.30, шаг 0.01)
    - Штраф шаблонов DRY (0.00 - 1.50, шаг 0.05)
    - Срез клише XTC (Вероятность: 0.00 - 1.00, шаг 0.05)
    - Порог уверенности XTC (0.00 - 0.50, шаг 0.01)
    - Макс. токенов (Max Tokens: 256 - 4096, шаг 64)
    """

    def __init__(
        self,
        current_params: Dict[str, Any],
        mode: str = "assistant",
        is_engineer_mode: bool = False,
        is_global_defaults: bool = False,
        parent=None
    ):
        super().__init__(parent=parent)
        self.window = parent
        self.mode = mode
        self.is_engineer_mode = is_engineer_mode
        self.is_global_defaults = is_global_defaults
        self.current_params = dict(current_params or {})
        self.was_reset_to_defaults = False
        self._is_resetting = False
        self._current_palette: Dict[str, str] = {}

        title_prefix = "🎛️ Глобальные параметры по умолчанию" if is_global_defaults else "⚙️ Настройки модели"
        self.setWindowTitle(f"{title_prefix} — Сэмплинг")
        self.resize(540, 680)
        self.setMinimumSize(480, 560)
        self.setModal(True)
        self.setObjectName("ModelSamplingDialog")

        # 1. Custom Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
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
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self._setup_ui()
        self._init_values_and_locks()
        self._connect_signals()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон для адаптивной палитры."""
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent()
        return None

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, карточек параметров и кнопок под активные обои.
        """
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            ModelSamplingDialog#ModelSamplingDialog {{
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
            QWidget#SamplingBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #ModelSamplingDialog QWidget {{
                background-color: transparent;
                color: #CBD5E1;
            }}
            #ModelSamplingDialog QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QFrame.ParamCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 8px;
                padding: 10px 14px;
            }}
            QFrame.ParamCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['card_border']};
            }}
            QLabel#header_title {{
                color: {palette['group_title']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#header_sub {{
                color: {palette['text_secondary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }}
            QLabel.param_label {{
                color: {palette['text_primary']};
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 600;
            }}
            DoubleSpinBox, SpinBox, SamplingDoubleSpinBox, SamplingSpinBox {{
                background-color: {palette['btn']};
                color: {palette['accent']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                padding: 3px 8px;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 600;
                min-width: 105px;
            }}
            DoubleSpinBox:focus, SpinBox:focus, SamplingDoubleSpinBox:focus, SamplingSpinBox:focus {{
                border-color: {palette['accent']};
                background-color: {palette['card_hover']};
            }}
            DoubleSpinBox:disabled, SpinBox:disabled, SamplingDoubleSpinBox:disabled, SamplingSpinBox:disabled {{
                background-color: rgba(15, 23, 42, 0.40);
                border-color: rgba(148, 163, 184, 0.20);
                color: #64748B;
            }}
            PushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 16px;
            }}
            PushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 12px;
                padding: 6px 20px;
            }}
            PrimaryPushButton:hover {{
                background-color: {palette['group_title']};
                border-color: {palette['group_title']};
            }}
            #ModelSamplingDialog QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 0;
            }}
            #ModelSamplingDialog QScrollBar::handle:vertical {{
                background: {palette['border']};
                min-height: 24px;
                border-radius: 3px;
            }}
            #ModelSamplingDialog QScrollBar::handle:vertical:hover {{
                background: {palette['accent']};
            }}
            #ModelSamplingDialog QScrollBar::add-line:vertical,
            #ModelSamplingDialog QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body_widget = QWidget(self)
        body_widget.setObjectName("SamplingBodyWidget")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(20, 14, 20, 16)
        body_layout.setSpacing(12)

        # Header Title & Subtitle
        header_box = QVBoxLayout()
        header_box.setSpacing(3)
        mode_desc = "🎭 Ролевой чат" if self.mode == "roleplay" else ("⚡ Инженер" if self.is_engineer_mode else "💬 Ассистент")
        main_title = f"Параметры по умолчанию ({mode_desc})" if self.is_global_defaults else f"Параметры сэмплинга ({mode_desc})"
        self.lbl_title = QLabel(main_title, body_widget)
        self.lbl_title.setObjectName("header_title")
        header_box.addWidget(self.lbl_title)

        sub_msg = (
            "Будут применяться ко всем новым создаваемым диалогам в этом режиме."
            if self.is_global_defaults else
            "Индивидуальная тонкая настройка генерации для активного диалога."
        )
        self.lbl_sub = QLabel(sub_msg, body_widget)
        self.lbl_sub.setObjectName("header_sub")
        header_box.addWidget(self.lbl_sub)
        body_layout.addLayout(header_box)

        sep = QFrame(body_widget)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        sep.setFixedHeight(1)
        body_layout.addWidget(sep)

        # Scroll Area for Parameter Cards
        scroll = QScrollArea(body_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 4, 10, 4)
        scroll_layout.setSpacing(8)

        # 1. Temperature Card
        card_temp = QFrame(scroll_content)
        card_temp.setProperty("class", "ParamCard")
        lay_temp = QVBoxLayout(card_temp)
        lay_temp.setContentsMargins(12, 10, 12, 10)
        lay_temp.setSpacing(6)

        hdr_temp = QHBoxLayout()
        self.lbl_temp = QLabel("Температура (Temperature):", card_temp)
        self.lbl_temp.setProperty("class", "param_label")
        self.spin_temp = SamplingDoubleSpinBox(card_temp)
        self.spin_temp.setRange(0.10, 2.00)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setDecimals(2)
        hdr_temp.addWidget(self.lbl_temp)
        hdr_temp.addStretch()
        hdr_temp.addWidget(self.spin_temp)
        lay_temp.addLayout(hdr_temp)

        self.slider_temp = SamplingSlider(Qt.Orientation.Horizontal, card_temp)
        self.slider_temp.setRange(10, 200)
        self.slider_temp.setSingleStep(5)
        self.slider_temp.setPageStep(10)
        lay_temp.addWidget(self.slider_temp)
        scroll_layout.addWidget(card_temp)

        # 2. Min-P Card
        card_min_p = QFrame(scroll_content)
        card_min_p.setProperty("class", "ParamCard")
        lay_min_p = QVBoxLayout(card_min_p)
        lay_min_p.setContentsMargins(12, 10, 12, 10)
        lay_min_p.setSpacing(6)

        hdr_min_p = QHBoxLayout()
        self.lbl_min_p = QLabel("Min-P Sampling:", card_min_p)
        self.lbl_min_p.setProperty("class", "param_label")
        self.spin_min_p = SamplingDoubleSpinBox(card_min_p)
        self.spin_min_p.setRange(0.00, 0.20)
        self.spin_min_p.setSingleStep(0.01)
        self.spin_min_p.setDecimals(2)
        hdr_min_p.addWidget(self.lbl_min_p)
        hdr_min_p.addStretch()
        hdr_min_p.addWidget(self.spin_min_p)
        lay_min_p.addLayout(hdr_min_p)

        self.slider_min_p = SamplingSlider(Qt.Orientation.Horizontal, card_min_p)
        self.slider_min_p.setRange(0, 20)
        self.slider_min_p.setSingleStep(1)
        self.slider_min_p.setPageStep(2)
        lay_min_p.addWidget(self.slider_min_p)
        scroll_layout.addWidget(card_min_p)

        # 3. Top-P Card
        card_top_p = QFrame(scroll_content)
        card_top_p.setProperty("class", "ParamCard")
        lay_top_p = QVBoxLayout(card_top_p)
        lay_top_p.setContentsMargins(12, 10, 12, 10)
        lay_top_p.setSpacing(6)

        hdr_top_p = QHBoxLayout()
        self.lbl_top_p = QLabel("Ядерная выборка (Top-P):", card_top_p)
        self.lbl_top_p.setProperty("class", "param_label")
        top_p_tip = "Отсекает длинный хвост маловероятных токенов. 1.00 — выключен (полный охват), 0.90–0.95 — мягкая фильтрация шума."
        self.lbl_top_p.setToolTip(top_p_tip)

        self.spin_top_p = SamplingDoubleSpinBox(card_top_p)
        self.spin_top_p.setRange(0.10, 1.00)
        self.spin_top_p.setSingleStep(0.01)
        self.spin_top_p.setDecimals(2)
        self.spin_top_p.setToolTip(top_p_tip)
        hdr_top_p.addWidget(self.lbl_top_p)
        hdr_top_p.addStretch()
        hdr_top_p.addWidget(self.spin_top_p)
        lay_top_p.addLayout(hdr_top_p)

        self.slider_top_p = SamplingSlider(Qt.Orientation.Horizontal, card_top_p)
        self.slider_top_p.setRange(10, 100)
        self.slider_top_p.setSingleStep(1)
        self.slider_top_p.setPageStep(5)
        self.slider_top_p.setToolTip(top_p_tip)
        lay_top_p.addWidget(self.slider_top_p)
        scroll_layout.addWidget(card_top_p)

        # 4. Repetition Penalty Card
        card_rep = QFrame(scroll_content)
        card_rep.setProperty("class", "ParamCard")
        lay_rep = QVBoxLayout(card_rep)
        lay_rep.setContentsMargins(12, 10, 12, 10)
        lay_rep.setSpacing(6)

        hdr_rep = QHBoxLayout()
        self.lbl_rep_pen = QLabel("Штраф за повторы (Repetition Penalty):", card_rep)
        self.lbl_rep_pen.setProperty("class", "param_label")
        self.spin_rep_pen = SamplingDoubleSpinBox(card_rep)
        self.spin_rep_pen.setRange(1.00, 1.30)
        self.spin_rep_pen.setSingleStep(0.01)
        self.spin_rep_pen.setDecimals(2)
        hdr_rep.addWidget(self.lbl_rep_pen)
        hdr_rep.addStretch()
        hdr_rep.addWidget(self.spin_rep_pen)
        lay_rep.addLayout(hdr_rep)

        self.slider_rep_pen = SamplingSlider(Qt.Orientation.Horizontal, card_rep)
        self.slider_rep_pen.setRange(100, 130)
        self.slider_rep_pen.setSingleStep(1)
        self.slider_rep_pen.setPageStep(5)
        lay_rep.addWidget(self.slider_rep_pen)
        scroll_layout.addWidget(card_rep)

        # 5. DRY Multiplier Card
        card_dry = QFrame(scroll_content)
        card_dry.setProperty("class", "ParamCard")
        lay_dry = QVBoxLayout(card_dry)
        lay_dry.setContentsMargins(12, 10, 12, 10)
        lay_dry.setSpacing(6)

        hdr_dry = QHBoxLayout()
        self.lbl_dry = QLabel("Штраф шаблонов DRY (Don't Repeat Yourself):", card_dry)
        self.lbl_dry.setProperty("class", "param_label")
        dry_tip = "Подавляет зацикливание фраз, паразитных оборотов и повторяющихся шаблонов предложений без деградации словарного запаса. 0.0 — выключен."
        self.lbl_dry.setToolTip(dry_tip)

        self.spin_dry = SamplingDoubleSpinBox(card_dry)
        self.spin_dry.setRange(0.00, 1.50)
        self.spin_dry.setSingleStep(0.05)
        self.spin_dry.setDecimals(2)
        self.spin_dry.setToolTip(dry_tip)
        hdr_dry.addWidget(self.lbl_dry)
        hdr_dry.addStretch()
        hdr_dry.addWidget(self.spin_dry)
        lay_dry.addLayout(hdr_dry)

        self.slider_dry = SamplingSlider(Qt.Orientation.Horizontal, card_dry)
        self.slider_dry.setRange(0, 150)
        self.slider_dry.setSingleStep(5)
        self.slider_dry.setPageStep(10)
        self.slider_dry.setToolTip(dry_tip)
        lay_dry.addWidget(self.slider_dry)
        scroll_layout.addWidget(card_dry)

        # 6. XTC Probability Card
        card_xtc_prob = QFrame(scroll_content)
        card_xtc_prob.setProperty("class", "ParamCard")
        lay_xtc_prob = QVBoxLayout(card_xtc_prob)
        lay_xtc_prob.setContentsMargins(12, 10, 12, 10)
        lay_xtc_prob.setSpacing(6)

        hdr_xtc_prob = QHBoxLayout()
        self.lbl_xtc_prob = QLabel("Срез клише XTC (Вероятность):", card_xtc_prob)
        self.lbl_xtc_prob.setProperty("class", "param_label")
        xtc_prob_tip = "Вероятность исключения самого очевидного шаблонного токена, если он не доминирует абсолютно. 0.00 — выключен, 0.20-0.30 — живая речь."
        self.lbl_xtc_prob.setToolTip(xtc_prob_tip)

        self.spin_xtc_prob = SamplingDoubleSpinBox(card_xtc_prob)
        self.spin_xtc_prob.setRange(0.00, 1.00)
        self.spin_xtc_prob.setSingleStep(0.05)
        self.spin_xtc_prob.setDecimals(2)
        self.spin_xtc_prob.setToolTip(xtc_prob_tip)
        hdr_xtc_prob.addWidget(self.lbl_xtc_prob)
        hdr_xtc_prob.addStretch()
        hdr_xtc_prob.addWidget(self.spin_xtc_prob)
        lay_xtc_prob.addLayout(hdr_xtc_prob)

        self.slider_xtc_prob = SamplingSlider(Qt.Orientation.Horizontal, card_xtc_prob)
        self.slider_xtc_prob.setRange(0, 100)
        self.slider_xtc_prob.setSingleStep(5)
        self.slider_xtc_prob.setPageStep(10)
        self.slider_xtc_prob.setToolTip(xtc_prob_tip)
        lay_xtc_prob.addWidget(self.slider_xtc_prob)
        scroll_layout.addWidget(card_xtc_prob)

        # 7. XTC Threshold Card
        card_xtc_thresh = QFrame(scroll_content)
        card_xtc_thresh.setProperty("class", "ParamCard")
        lay_xtc_thresh = QVBoxLayout(card_xtc_thresh)
        lay_xtc_thresh.setContentsMargins(12, 10, 12, 10)
        lay_xtc_thresh.setSpacing(6)

        hdr_xtc_thresh = QHBoxLayout()
        self.lbl_xtc_thresh = QLabel("Порог уверенности XTC:", card_xtc_thresh)
        self.lbl_xtc_thresh.setProperty("class", "param_label")
        xtc_thresh_tip = "Порог отсечения: если токен увереннее (1.0 - порог), XTC его сохраняет. Дефолт 0.10 (порог 90%)."
        self.lbl_xtc_thresh.setToolTip(xtc_thresh_tip)

        self.spin_xtc_thresh = SamplingDoubleSpinBox(card_xtc_thresh)
        self.spin_xtc_thresh.setRange(0.00, 0.50)
        self.spin_xtc_thresh.setSingleStep(0.01)
        self.spin_xtc_thresh.setDecimals(2)
        self.spin_xtc_thresh.setToolTip(xtc_thresh_tip)
        hdr_xtc_thresh.addWidget(self.lbl_xtc_thresh)
        hdr_xtc_thresh.addStretch()
        hdr_xtc_thresh.addWidget(self.spin_xtc_thresh)
        lay_xtc_thresh.addLayout(hdr_xtc_thresh)

        self.slider_xtc_thresh = SamplingSlider(Qt.Orientation.Horizontal, card_xtc_thresh)
        self.slider_xtc_thresh.setRange(0, 50)
        self.slider_xtc_thresh.setSingleStep(1)
        self.slider_xtc_thresh.setPageStep(5)
        self.slider_xtc_thresh.setToolTip(xtc_thresh_tip)
        lay_xtc_thresh.addWidget(self.slider_xtc_thresh)
        scroll_layout.addWidget(card_xtc_thresh)

        # 8. Max Tokens Card
        card_tokens = QFrame(scroll_content)
        card_tokens.setProperty("class", "ParamCard")
        lay_tokens = QVBoxLayout(card_tokens)
        lay_tokens.setContentsMargins(12, 10, 12, 10)
        lay_tokens.setSpacing(6)

        hdr_tok = QHBoxLayout()
        self.lbl_tok = QLabel("Макс. токенов (Max Tokens):", card_tokens)
        self.lbl_tok.setProperty("class", "param_label")
        self.spin_max_tokens = SamplingSpinBox(card_tokens)
        self.spin_max_tokens.setRange(256, 4096)
        self.spin_max_tokens.setSingleStep(64)
        hdr_tok.addWidget(self.lbl_tok)
        hdr_tok.addStretch()
        hdr_tok.addWidget(self.spin_max_tokens)
        lay_tokens.addLayout(hdr_tok)

        self.slider_max_tokens = SamplingSlider(Qt.Orientation.Horizontal, card_tokens)
        self.slider_max_tokens.setRange(256, 4096)
        self.slider_max_tokens.setSingleStep(64)
        self.slider_max_tokens.setPageStep(256)
        lay_tokens.addWidget(self.slider_max_tokens)
        scroll_layout.addWidget(card_tokens)

        scroll.setWidget(scroll_content)
        body_layout.addWidget(scroll, 1)

        # Bottom Control Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self.btn_reset = PushButton(FIF.SYNC, "Сбросить по умолчанию", body_widget)
        self.btn_reset.setObjectName("btn_reset")
        self.btn_reset.clicked.connect(self._reset_defaults)
        btn_box.addWidget(self.btn_reset)

        btn_box.addStretch()

        self.btn_cancel = PushButton("Отмена", body_widget)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton("Сохранить", body_widget)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_save)

        body_layout.addLayout(btn_box)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

    def _init_values_and_locks(self):
        active_mode = "engineer" if self.is_engineer_mode else self.mode
        ref_params = get_default_sampling_params(active_mode)
        ref_temp = ref_params["temperature"]
        ref_min_p = ref_params["min_p"]
        ref_top_p = ref_params.get("top_p", 1.00 if self.is_engineer_mode else 0.95)
        ref_rep_pen = ref_params["repeat_penalty"]
        ref_dry = ref_params.get("dry_multiplier", 0.00 if self.is_engineer_mode else 0.80)
        ref_xtc_prob = ref_params.get("xtc_probability", 0.00 if self.is_engineer_mode else 0.20)
        ref_xtc_thresh = ref_params.get("xtc_threshold", 0.10)
        ref_max_tok = ref_params["max_tokens"]

        init_temp = float(self.current_params.get("temperature", ref_temp))
        init_min_p = float(self.current_params.get("min_p", ref_min_p))
        init_top_p = float(self.current_params.get("top_p", ref_top_p))
        init_rep_pen = float(self.current_params.get("repeat_penalty", ref_rep_pen))
        init_dry = float(self.current_params.get("dry_multiplier", ref_dry))
        init_xtc_prob = float(self.current_params.get("xtc_probability", ref_xtc_prob))
        init_xtc_thresh = float(self.current_params.get("xtc_threshold", ref_xtc_thresh))
        init_max_tokens = int(self.current_params.get("max_tokens", ref_max_tok))

        self.spin_temp.setValue(init_temp)
        self._on_temp_spin_changed(init_temp)

        if self.is_engineer_mode:
            # Температура разблокирована для свободного управления креативностью генерации кода
            self.spin_temp.setEnabled(True)
            self.slider_temp.setEnabled(True)

            self.spin_dry.setValue(0.00)
            self._on_dry_spin_changed(0.00)
            self.spin_dry.setEnabled(False)
            self.slider_dry.setEnabled(False)
            self.lbl_dry.setText("Штраф шаблонов DRY: 🔒 0.00 (Режим «⚡ Инженер»)")
            self.lbl_dry.setStyleSheet("color: #00F0FF; font-weight: bold;")
            dry_eng_tip = "В режиме «⚡ Инженер» сэмплер DRY зафиксирован на 0.00 (выключен) во избежание поломки повторяющихся синтаксических конструкций кода."
            self.spin_dry.setToolTip(dry_eng_tip)
            self.slider_dry.setToolTip(dry_eng_tip)
            self.lbl_dry.setToolTip(dry_eng_tip)

            self.spin_xtc_prob.setValue(0.00)
            self._on_xtc_prob_spin_changed(0.00)
            self.spin_xtc_prob.setEnabled(False)
            self.slider_xtc_prob.setEnabled(False)
            self.lbl_xtc_prob.setText("Срез клише XTC: 🔒 0.00 (Режим «⚡ Инженер»)")
            self.lbl_xtc_prob.setStyleSheet("color: #00F0FF; font-weight: bold;")
            xtc_eng_tip = "В режиме «⚡ Инженер» сэмплер XTC зафиксирован на 0.00 (выключен) во избежание искажения синтаксиса и точных ключевых слов кода."
            self.spin_xtc_prob.setToolTip(xtc_eng_tip)
            self.slider_xtc_prob.setToolTip(xtc_eng_tip)
            self.lbl_xtc_prob.setToolTip(xtc_eng_tip)
        else:
            self.spin_dry.setValue(init_dry)
            self._on_dry_spin_changed(init_dry)
            self.spin_xtc_prob.setValue(init_xtc_prob)
            self._on_xtc_prob_spin_changed(init_xtc_prob)

        self.spin_xtc_thresh.setValue(init_xtc_thresh)
        self._on_xtc_thresh_spin_changed(init_xtc_thresh)

        self.spin_min_p.setValue(init_min_p)
        self._on_min_p_spin_changed(init_min_p)

        self.spin_top_p.setValue(init_top_p)
        self._on_top_p_spin_changed(init_top_p)

        self.spin_rep_pen.setValue(init_rep_pen)
        self._on_rep_pen_spin_changed(init_rep_pen)

        self.spin_max_tokens.setValue(init_max_tokens)
        self._on_max_tokens_spin_changed(init_max_tokens)

    def _connect_signals(self):
        """Connect signals for real-time dynamic synchronization on the fly."""
        self.slider_temp.valueChanged.connect(self._on_temp_slider_changed)
        self.slider_temp.sliderMoved.connect(self._on_temp_slider_changed)
        self.spin_temp.valueChanged.connect(self._on_temp_spin_changed)

        self.slider_min_p.valueChanged.connect(self._on_min_p_slider_changed)
        self.slider_min_p.sliderMoved.connect(self._on_min_p_slider_changed)
        self.spin_min_p.valueChanged.connect(self._on_min_p_spin_changed)

        self.slider_top_p.valueChanged.connect(self._on_top_p_slider_changed)
        self.slider_top_p.sliderMoved.connect(self._on_top_p_slider_changed)
        self.spin_top_p.valueChanged.connect(self._on_top_p_spin_changed)

        self.slider_rep_pen.valueChanged.connect(self._on_rep_pen_slider_changed)
        self.slider_rep_pen.sliderMoved.connect(self._on_rep_pen_slider_changed)
        self.spin_rep_pen.valueChanged.connect(self._on_rep_pen_spin_changed)

        self.slider_dry.valueChanged.connect(self._on_dry_slider_changed)
        self.slider_dry.sliderMoved.connect(self._on_dry_slider_changed)
        self.spin_dry.valueChanged.connect(self._on_dry_spin_changed)

        self.slider_xtc_prob.valueChanged.connect(self._on_xtc_prob_slider_changed)
        self.slider_xtc_prob.sliderMoved.connect(self._on_xtc_prob_slider_changed)
        self.spin_xtc_prob.valueChanged.connect(self._on_xtc_prob_spin_changed)

        self.slider_xtc_thresh.valueChanged.connect(self._on_xtc_thresh_slider_changed)
        self.slider_xtc_thresh.sliderMoved.connect(self._on_xtc_thresh_slider_changed)
        self.spin_xtc_thresh.valueChanged.connect(self._on_xtc_thresh_spin_changed)

        self.slider_max_tokens.valueChanged.connect(self._on_max_tokens_slider_changed)
        self.slider_max_tokens.sliderMoved.connect(self._on_max_tokens_slider_changed)
        self.spin_max_tokens.valueChanged.connect(self._on_max_tokens_spin_changed)

    def _on_user_input_changed(self):
        if not getattr(self, "_is_resetting", False):
            self.was_reset_to_defaults = False

    def _on_temp_slider_changed(self, val: int):
        self._on_user_input_changed()
        target = round(val / 100.0, 2)
        if abs(self.spin_temp.value() - target) > 0.001:
            self.spin_temp.blockSignals(True)
            self.spin_temp.setValue(target)
            self.spin_temp.blockSignals(False)

    def _on_temp_spin_changed(self, val: float):
        self._on_user_input_changed()
        target = int(round(val * 100))
        if self.slider_temp.value() != target:
            self.slider_temp.blockSignals(True)
            self.slider_temp.setValue(target)
            self.slider_temp.blockSignals(False)

    def _on_min_p_slider_changed(self, val: int):
        self._on_user_input_changed()
        target = round(val / 100.0, 2)
        if abs(self.spin_min_p.value() - target) > 0.001:
            self.spin_min_p.blockSignals(True)
            self.spin_min_p.setValue(target)
            self.spin_min_p.blockSignals(False)

    def _on_min_p_spin_changed(self, val: float):
        self._on_user_input_changed()
        target = int(round(val * 100))
        if self.slider_min_p.value() != target:
            self.slider_min_p.blockSignals(True)
            self.slider_min_p.setValue(target)
            self.slider_min_p.blockSignals(False)

    def _on_top_p_slider_changed(self, val: int):
        target = round(val / 100.0, 2)
        if abs(self.spin_top_p.value() - target) > 0.001:
            self.spin_top_p.blockSignals(True)
            self.spin_top_p.setValue(target)
            self.spin_top_p.blockSignals(False)
        self._on_user_input_changed()

    def _on_top_p_spin_changed(self, val: float):
        target = int(round(val * 100))
        if self.slider_top_p.value() != target:
            self.slider_top_p.blockSignals(True)
            self.slider_top_p.setValue(target)
            self.slider_top_p.blockSignals(False)
        self._on_user_input_changed()

    def _on_rep_pen_slider_changed(self, val: int):
        self._on_user_input_changed()
        target = round(val / 100.0, 2)
        if abs(self.spin_rep_pen.value() - target) > 0.001:
            self.spin_rep_pen.blockSignals(True)
            self.spin_rep_pen.setValue(target)
            self.spin_rep_pen.blockSignals(False)

    def _on_rep_pen_spin_changed(self, val: float):
        self._on_user_input_changed()
        target = int(round(val * 100))
        if self.slider_rep_pen.value() != target:
            self.slider_rep_pen.blockSignals(True)
            self.slider_rep_pen.setValue(target)
            self.slider_rep_pen.blockSignals(False)

    def _on_dry_slider_changed(self, val: int):
        self._on_user_input_changed()
        target = round(val / 100.0, 2)
        if abs(self.spin_dry.value() - target) > 0.001:
            self.spin_dry.blockSignals(True)
            self.spin_dry.setValue(target)
            self.spin_dry.blockSignals(False)

    def _on_dry_spin_changed(self, val: float):
        self._on_user_input_changed()
        target = int(round(val * 100))
        if self.slider_dry.value() != target:
            self.slider_dry.blockSignals(True)
            self.slider_dry.setValue(target)
            self.slider_dry.blockSignals(False)

    def _on_xtc_prob_slider_changed(self, val: int):
        self._on_user_input_changed()
        target = round(val / 100.0, 2)
        if abs(self.spin_xtc_prob.value() - target) > 0.001:
            self.spin_xtc_prob.blockSignals(True)
            self.spin_xtc_prob.setValue(target)
            self.spin_xtc_prob.blockSignals(False)

    def _on_xtc_prob_spin_changed(self, val: float):
        self._on_user_input_changed()
        target = int(round(val * 100))
        if self.slider_xtc_prob.value() != target:
            self.slider_xtc_prob.blockSignals(True)
            self.slider_xtc_prob.setValue(target)
            self.slider_xtc_prob.blockSignals(False)

    def _on_xtc_thresh_slider_changed(self, val: int):
        self._on_user_input_changed()
        target = round(val / 100.0, 2)
        if abs(self.spin_xtc_thresh.value() - target) > 0.001:
            self.spin_xtc_thresh.blockSignals(True)
            self.spin_xtc_thresh.setValue(target)
            self.spin_xtc_thresh.blockSignals(False)

    def _on_xtc_thresh_spin_changed(self, val: float):
        self._on_user_input_changed()
        target = int(round(val * 100))
        if self.slider_xtc_thresh.value() != target:
            self.slider_xtc_thresh.blockSignals(True)
            self.slider_xtc_thresh.setValue(target)
            self.slider_xtc_thresh.blockSignals(False)

    def _on_max_tokens_slider_changed(self, val: int):
        self._on_user_input_changed()
        snapped = round(val / 64) * 64
        snapped = max(256, min(4096, snapped))
        if self.spin_max_tokens.value() != snapped:
            self.spin_max_tokens.blockSignals(True)
            self.spin_max_tokens.setValue(snapped)
            self.spin_max_tokens.blockSignals(False)

    def _on_max_tokens_spin_changed(self, val: int):
        self._on_user_input_changed()
        if self.slider_max_tokens.value() != val:
            self.slider_max_tokens.blockSignals(True)
            self.slider_max_tokens.setValue(val)
            self.slider_max_tokens.blockSignals(False)

    def _reset_defaults(self):
        self._is_resetting = True
        try:
            active_mode = "engineer" if self.is_engineer_mode else self.mode
            defaults = get_default_sampling_params(active_mode)

            if self.is_engineer_mode:
                self.spin_temp.setValue(defaults["temperature"])
                self.slider_temp.setValue(int(round(defaults["temperature"] * 100)))
                self.spin_dry.setValue(0.00)
                self.slider_dry.setValue(0)
                self.spin_xtc_prob.setValue(0.00)
                self.slider_xtc_prob.setValue(0)
            else:
                self.spin_temp.setValue(defaults["temperature"])
                self.slider_temp.setValue(int(round(defaults["temperature"] * 100)))
                dry_val = float(defaults.get("dry_multiplier", 0.80 if not self.is_engineer_mode else 0.00))
                self.spin_dry.setValue(dry_val)
                self.slider_dry.setValue(int(round(dry_val * 100)))
                xtc_p_val = float(defaults.get("xtc_probability", 0.20 if self.mode == "assistant" else 0.30))
                self.spin_xtc_prob.setValue(xtc_p_val)
                self.slider_xtc_prob.setValue(int(round(xtc_p_val * 100)))

            self.spin_min_p.setValue(defaults["min_p"])
            self.slider_min_p.setValue(int(round(defaults["min_p"] * 100)))
            top_p_def = float(defaults.get("top_p", 1.00 if self.is_engineer_mode else 0.95))
            self.spin_top_p.setValue(top_p_def)
            self.slider_top_p.setValue(int(round(top_p_def * 100)))

            xtc_t_val = float(defaults.get("xtc_threshold", 0.10))
            self.spin_xtc_thresh.setValue(xtc_t_val)
            self.slider_xtc_thresh.setValue(int(round(xtc_t_val * 100)))

            self.spin_rep_pen.setValue(defaults["repeat_penalty"])
            self.slider_rep_pen.setValue(int(round(defaults["repeat_penalty"] * 100)))
            self.spin_max_tokens.setValue(defaults["max_tokens"])
            self.slider_max_tokens.setValue(defaults["max_tokens"])

            self.current_params["top_p"] = top_p_def
        finally:
            self._is_resetting = False
            self.was_reset_to_defaults = True

    _on_reset_clicked = _reset_defaults

    def get_sampling_params(self) -> Dict[str, Any]:
        params = {
            "temperature": round(self.spin_temp.value(), 2),
            "min_p": round(self.spin_min_p.value(), 2),
            "top_p": round(self.spin_top_p.value(), 2),
            "repeat_penalty": round(self.spin_rep_pen.value(), 2),
            "dry_multiplier": round(self.spin_dry.value(), 2),
            "xtc_probability": 0.00 if self.is_engineer_mode else round(self.spin_xtc_prob.value(), 2),
            "xtc_threshold": round(self.spin_xtc_thresh.value(), 2),
            "max_tokens": int(self.spin_max_tokens.value())
        }
        if self.is_engineer_mode:
            params["dry_multiplier"] = 0.00
            params["xtc_probability"] = 0.00
        # Аккуратно сохраняем флаг is_dm_mode, если он уже присутствовал в параметрах
        if "is_dm_mode" in self.current_params:
            params["is_dm_mode"] = self.current_params["is_dm_mode"]
        return params

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parent()
        if parent and hasattr(parent, "geometry"):
            geo = parent.geometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )
