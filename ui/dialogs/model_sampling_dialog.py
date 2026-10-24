"""
Model Sampling Parameters Dialog for NeonRoute.
Modal dialog in Glassmorphism style for configuring LLM sampling parameters:
Temperature, Min-P, Repetition Penalty, Max Tokens, and DM Mode toggle.
Modernized with QFluentWidgets components under Agentrules.md.
"""

from typing import Dict, Any
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QDoubleSpinBox, QSpinBox
)
from qfluentwidgets import (
    Slider,
    DoubleSpinBox,
    SpinBox,
    CheckBox,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
)

from services.session_manager import get_default_sampling_params


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

    def wheelEvent(self, e):
        # Отключаем случайное изменение значений колесиком мыши
        e.ignore()


class SamplingDoubleSpinBox(DoubleSpinBox):
    """
    Поле числового ввода с плавающей точкой (Temperature, Min-P, Repetition Penalty):
    - Стрелки вверх/вниз гарантированно инкрементируют/декрементируют значение с точным шагом
    - Границы minimum / singleStep строго соблюдаются
    - Отключение wheelEvent во избежание случайного скролла значений
    - ClickFocus
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
    Поле целочисленного ввода (Max Tokens):
    - Стрелки вверх/вниз гарантированно инкрементируют/декрементируют значение с точным шагом (64)
    - Нижняя граница minimum (256) и верхняя maximum (4096) строго соблюдаются
    - Отключение wheelEvent
    - ClickFocus
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


class ModelSamplingDialog(QDialog):
    """
    Modal dialog in NeonRoute glassmorphism style for configuring individual LLM sampling parameters:
    - Temperature (0.10 - 2.00, step 0.05)
    - Min-P Sampling (0.00 - 0.20, step 0.01)
    - Repetition Penalty (1.00 - 1.30, step 0.01)
    - Max Tokens (256 - 4096, step 64)
    Provides default reset according to active session mode (roleplay vs assistant).
    """
    def __init__(
        self,
        current_params: Dict[str, Any],
        mode: str = "assistant",
        is_engineer_mode: bool = False,
        is_global_defaults: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.mode = mode
        self.is_engineer_mode = is_engineer_mode
        self.is_global_defaults = is_global_defaults
        self.current_params = dict(current_params or {})
        self.was_reset_to_defaults = False
        self._is_resetting = False

        title_prefix = "🎛️ Глобальные параметры по умолчанию" if is_global_defaults else "⚙️ Настройки модели"
        self.setWindowTitle(f"{title_prefix} — Сэмплинг")
        self.setFixedWidth(480)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 14px;
            }
            QLabel {
                color: #CBD5E1;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
            }
            QLabel#header_title {
                color: #00F0FF;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#header_sub {
                color: #94A3B8;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 11px;
            }
            QLabel.param_label {
                color: #E2E8F0;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 600;
            }
            DoubleSpinBox, SpinBox, SamplingDoubleSpinBox, SamplingSpinBox, QDoubleSpinBox, QSpinBox {
                background-color: rgba(22, 32, 50, 0.75);
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.30);
                border-radius: 6px;
                padding: 3px 8px;
                font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
                font-weight: 600;
                min-width: 90px;
            }
            DoubleSpinBox:focus, SpinBox:focus, SamplingDoubleSpinBox:focus, SamplingSpinBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
                border-color: #00F0FF;
                background-color: rgba(28, 40, 64, 0.85);
            }
            DoubleSpinBox:disabled, SpinBox:disabled, SamplingDoubleSpinBox:disabled, SamplingSpinBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
                background-color: rgba(15, 23, 42, 0.40);
                border-color: rgba(148, 163, 184, 0.20);
                color: #64748B;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        # Header Title & Subtitle
        header_box = QVBoxLayout()
        header_box.setSpacing(3)
        mode_desc = "🎭 Ролевой чат" if self.mode == "roleplay" else ("⚡ Инженер" if self.is_engineer_mode else "💬 Ассистент")
        main_title = f"Параметры по умолчанию ({mode_desc})" if self.is_global_defaults else f"Параметры сэмплинга ({mode_desc})"
        lbl_title = QLabel(main_title, self)
        lbl_title.setObjectName("header_title")
        header_box.addWidget(lbl_title)

        sub_msg = (
            "Будут применяться ко всем новым создаваемым диалогам в этом режиме."
            if self.is_global_defaults else
            "Индивидуальная тонкая настройка генерации для активного диалога."
        )
        lbl_sub = QLabel(sub_msg, self)
        lbl_sub.setObjectName("header_sub")
        header_box.addWidget(lbl_sub)
        layout.addLayout(header_box)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        params_box = QVBoxLayout()
        params_box.setSpacing(14)

        # 1. Temperature
        temp_row = QVBoxLayout()
        temp_row.setSpacing(4)
        t_header = QHBoxLayout()
        lbl_t = QLabel("Температура (Temperature):", self)
        lbl_t.setProperty("class", "param_label")
        self.spin_temp = SamplingDoubleSpinBox(self)
        self.spin_temp.setRange(0.10, 2.00)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setDecimals(2)
        t_header.addWidget(lbl_t)
        t_header.addStretch()
        t_header.addWidget(self.spin_temp)
        temp_row.addLayout(t_header)

        self.slider_temp = SamplingSlider(Qt.Orientation.Horizontal, self)
        self.slider_temp.setRange(10, 200)
        self.slider_temp.setSingleStep(5)
        self.slider_temp.setPageStep(10)
        temp_row.addWidget(self.slider_temp)
        params_box.addLayout(temp_row)

        # 2. Min-P
        min_p_row = QVBoxLayout()
        min_p_row.setSpacing(4)
        mp_header = QHBoxLayout()
        lbl_mp = QLabel("Min-P Sampling:", self)
        lbl_mp.setProperty("class", "param_label")
        self.spin_min_p = SamplingDoubleSpinBox(self)
        self.spin_min_p.setRange(0.00, 0.20)
        self.spin_min_p.setSingleStep(0.01)
        self.spin_min_p.setDecimals(2)
        mp_header.addWidget(lbl_mp)
        mp_header.addStretch()
        mp_header.addWidget(self.spin_min_p)
        min_p_row.addLayout(mp_header)

        self.slider_min_p = SamplingSlider(Qt.Orientation.Horizontal, self)
        self.slider_min_p.setRange(0, 20)
        self.slider_min_p.setSingleStep(1)
        self.slider_min_p.setPageStep(2)
        min_p_row.addWidget(self.slider_min_p)
        params_box.addLayout(min_p_row)

        # 3. Repetition Penalty
        rep_row = QVBoxLayout()
        rep_row.setSpacing(4)
        rp_header = QHBoxLayout()
        lbl_rp = QLabel("Штраф за повторы (Repetition Penalty):", self)
        lbl_rp.setProperty("class", "param_label")
        self.spin_rep_pen = SamplingDoubleSpinBox(self)
        self.spin_rep_pen.setRange(1.00, 1.30)
        self.spin_rep_pen.setSingleStep(0.01)
        self.spin_rep_pen.setDecimals(2)
        rp_header.addWidget(lbl_rp)
        rp_header.addStretch()
        rp_header.addWidget(self.spin_rep_pen)
        rep_row.addLayout(rp_header)

        self.slider_rep_pen = SamplingSlider(Qt.Orientation.Horizontal, self)
        self.slider_rep_pen.setRange(100, 130)
        self.slider_rep_pen.setSingleStep(1)
        self.slider_rep_pen.setPageStep(5)
        rep_row.addWidget(self.slider_rep_pen)
        params_box.addLayout(rep_row)

        # 4. Max Tokens (Ограничено мастер-предохранителем 4096)
        tokens_row = QVBoxLayout()
        tokens_row.setSpacing(4)
        tok_header = QHBoxLayout()
        lbl_tok = QLabel("Макс. токенов (Max Tokens):", self)
        lbl_tok.setProperty("class", "param_label")
        self.spin_max_tokens = SamplingSpinBox(self)
        self.spin_max_tokens.setRange(256, 4096)
        self.spin_max_tokens.setSingleStep(64)
        tok_header.addWidget(lbl_tok)
        tok_header.addStretch()
        tok_header.addWidget(self.spin_max_tokens)
        tokens_row.addLayout(tok_header)

        self.slider_max_tokens = SamplingSlider(Qt.Orientation.Horizontal, self)
        self.slider_max_tokens.setRange(256, 4096)
        self.slider_max_tokens.setSingleStep(64)
        self.slider_max_tokens.setPageStep(256)
        tokens_row.addWidget(self.slider_max_tokens)
        params_box.addLayout(tokens_row)

        # 5. Checkbox DM Mode (Roleplay only)
        self.chk_dm_mode = CheckBox("🎲 Режим Гейммастера (DM / Ведущий игры)", self)
        self.chk_dm_mode.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: 600; margin-top: 4px;")
        self.chk_dm_mode.setChecked(bool(current_params.get("is_dm_mode", False)))
        if self.mode == "roleplay":
            params_box.addWidget(self.chk_dm_mode)

        layout.addLayout(params_box)

        # Connect signals for real-time dynamic synchronization on the fly
        self.slider_temp.valueChanged.connect(self._on_temp_slider_changed)
        self.slider_temp.sliderMoved.connect(self._on_temp_slider_changed)
        self.spin_temp.valueChanged.connect(self._on_temp_spin_changed)

        self.slider_min_p.valueChanged.connect(self._on_min_p_slider_changed)
        self.slider_min_p.sliderMoved.connect(self._on_min_p_slider_changed)
        self.spin_min_p.valueChanged.connect(self._on_min_p_spin_changed)

        self.slider_rep_pen.valueChanged.connect(self._on_rep_pen_slider_changed)
        self.slider_rep_pen.sliderMoved.connect(self._on_rep_pen_slider_changed)
        self.spin_rep_pen.valueChanged.connect(self._on_rep_pen_spin_changed)

        self.slider_max_tokens.valueChanged.connect(self._on_max_tokens_slider_changed)
        self.slider_max_tokens.sliderMoved.connect(self._on_max_tokens_slider_changed)
        self.spin_max_tokens.valueChanged.connect(self._on_max_tokens_spin_changed)
        self.chk_dm_mode.toggled.connect(lambda _: self._on_user_input_changed())

        active_mode = "engineer" if self.is_engineer_mode else self.mode
        ref_params = get_default_sampling_params(active_mode)
        ref_temp = ref_params["temperature"]
        ref_min_p = ref_params["min_p"]
        ref_rep_pen = ref_params["repeat_penalty"]
        ref_max_tok = ref_params["max_tokens"]

        init_temp = float(current_params.get("temperature", ref_temp))
        init_min_p = float(current_params.get("min_p", ref_min_p))
        init_rep_pen = float(current_params.get("repeat_penalty", ref_rep_pen))
        init_max_tokens = int(current_params.get("max_tokens", ref_max_tok))

        if self.is_engineer_mode:
            self.spin_temp.setValue(0.15)
            self._on_temp_spin_changed(0.15)
            self.spin_temp.setEnabled(False)
            self.slider_temp.setEnabled(False)
            lbl_t.setText("Температура: 🔒 0.15 (Режим «⚡ Инженер»)")
            lbl_t.setStyleSheet("color: #00F0FF; font-weight: bold;")
            tip = "В режиме «⚡ Инженер» температура зафиксирована на 0.15 для стабильности синтаксиса и аргументов API."
            self.spin_temp.setToolTip(tip)
            self.slider_temp.setToolTip(tip)
        else:
            self.spin_temp.setValue(init_temp)
            self._on_temp_spin_changed(init_temp)

        self.spin_min_p.setValue(init_min_p)
        self._on_min_p_spin_changed(init_min_p)

        self.spin_rep_pen.setValue(init_rep_pen)
        self._on_rep_pen_spin_changed(init_rep_pen)

        self.spin_max_tokens.setValue(init_max_tokens)
        self._on_max_tokens_spin_changed(init_max_tokens)

        # Control Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self.btn_reset = PushButton(FIF.SYNC, "Сбросить по умолчанию", self)
        self.btn_reset.setObjectName("btn_reset")
        self.btn_reset.clicked.connect(self._reset_defaults)
        btn_box.addWidget(self.btn_reset)

        btn_box.addStretch()

        self.btn_cancel = PushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton("Сохранить", self)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_save)

        layout.addSpacing(6)
        layout.addLayout(btn_box)

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
            from services.session_manager import get_default_sampling_params
            defaults = get_default_sampling_params(active_mode)

            if self.is_engineer_mode:
                self.spin_temp.setValue(0.15)
                self.slider_temp.setValue(15)
            else:
                self.spin_temp.setValue(defaults["temperature"])
                self.slider_temp.setValue(int(round(defaults["temperature"] * 100)))
            self.spin_min_p.setValue(defaults["min_p"])
            self.slider_min_p.setValue(int(round(defaults["min_p"] * 100)))
            self.spin_rep_pen.setValue(defaults["repeat_penalty"])
            self.slider_rep_pen.setValue(int(round(defaults["repeat_penalty"] * 100)))
            self.spin_max_tokens.setValue(defaults["max_tokens"])
            self.slider_max_tokens.setValue(defaults["max_tokens"])

            if "top_p" in defaults:
                self.current_params["top_p"] = defaults["top_p"]

            if hasattr(self, "chk_dm_mode") and self.mode == "roleplay":
                self.chk_dm_mode.setChecked(bool(defaults.get("is_dm_mode", False)))
        finally:
            self._is_resetting = False
            self.was_reset_to_defaults = True

    def get_sampling_params(self) -> Dict[str, Any]:
        params = {
            "temperature": round(self.spin_temp.value(), 2),
            "min_p": round(self.spin_min_p.value(), 2),
            "repeat_penalty": round(self.spin_rep_pen.value(), 2),
            "max_tokens": int(self.spin_max_tokens.value())
        }
        if "top_p" in getattr(self, "current_params", {}):
            params["top_p"] = self.current_params["top_p"]
        if self.is_engineer_mode:
            params["temperature"] = 0.15
        if hasattr(self, "chk_dm_mode") and self.mode == "roleplay":
            params["is_dm_mode"] = self.chk_dm_mode.isChecked()
        return params
