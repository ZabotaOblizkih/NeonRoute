from typing import Dict, Any
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QDoubleSpinBox, QSlider, QSpinBox, QPushButton
)

from services.session_manager import get_default_sampling_params


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
        self.setFixedWidth(460)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 10px;
            }
            QLabel {
                color: #CBD5E1;
                font-size: 12px;
            }
            QLabel#header_title {
                color: #00F0FF;
                font-size: 14px;
                font-weight: bold;
            }
            QLabel#header_sub {
                color: #64748B;
                font-size: 11px;
            }
            QDoubleSpinBox, QSpinBox {
                background-color: #121824;
                color: #00F0FF;
                border: 1px solid #1E2B42;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 12px;
                font-weight: bold;
                min-width: 72px;
            }
            QDoubleSpinBox:focus, QSpinBox:focus {
                border-color: #00F0FF;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #1C273C;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #00F0FF;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #00F0FF;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #00F0FF;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_save {
                background-color: #004D54;
                border: 1px solid #00F0FF;
                color: #FFFFFF;
                font-weight: bold;
            }
            QPushButton#btn_save:hover {
                background-color: #00737D;
            }
            QPushButton#btn_reset {
                background-color: #2D1A15;
                border: 1px solid #F97316;
                color: #FB923C;
            }
            QPushButton#btn_reset:hover {
                background-color: #4A2818;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

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
        sep.setStyleSheet("background-color: #1E273A;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        params_box = QVBoxLayout()
        params_box.setSpacing(12)

        # 1. Temperature
        temp_row = QVBoxLayout()
        temp_row.setSpacing(4)
        t_header = QHBoxLayout()
        lbl_t = QLabel("Температура (Temperature):", self)
        lbl_t.setStyleSheet("font-weight: bold;")
        self.spin_temp = QDoubleSpinBox(self)
        self.spin_temp.setRange(0.10, 2.00)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setDecimals(2)
        t_header.addWidget(lbl_t)
        t_header.addStretch()
        t_header.addWidget(self.spin_temp)
        temp_row.addLayout(t_header)

        self.slider_temp = QSlider(Qt.Orientation.Horizontal, self)
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
        lbl_mp.setStyleSheet("font-weight: bold;")
        self.spin_min_p = QDoubleSpinBox(self)
        self.spin_min_p.setRange(0.00, 0.20)
        self.spin_min_p.setSingleStep(0.01)
        self.spin_min_p.setDecimals(2)
        mp_header.addWidget(lbl_mp)
        mp_header.addStretch()
        mp_header.addWidget(self.spin_min_p)
        min_p_row.addLayout(mp_header)

        self.slider_min_p = QSlider(Qt.Orientation.Horizontal, self)
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
        lbl_rp.setStyleSheet("font-weight: bold;")
        self.spin_rep_pen = QDoubleSpinBox(self)
        self.spin_rep_pen.setRange(1.00, 1.30)
        self.spin_rep_pen.setSingleStep(0.01)
        self.spin_rep_pen.setDecimals(2)
        rp_header.addWidget(lbl_rp)
        rp_header.addStretch()
        rp_header.addWidget(self.spin_rep_pen)
        rep_row.addLayout(rp_header)

        self.slider_rep_pen = QSlider(Qt.Orientation.Horizontal, self)
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
        lbl_tok.setStyleSheet("font-weight: bold;")
        self.spin_max_tokens = QSpinBox(self)
        self.spin_max_tokens.setRange(256, 4096)
        self.spin_max_tokens.setSingleStep(64)
        tok_header.addWidget(lbl_tok)
        tok_header.addStretch()
        tok_header.addWidget(self.spin_max_tokens)
        tokens_row.addLayout(tok_header)

        self.slider_max_tokens = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_max_tokens.setRange(256, 4096)
        self.slider_max_tokens.setSingleStep(64)
        self.slider_max_tokens.setPageStep(256)
        tokens_row.addWidget(self.slider_max_tokens)
        params_box.addLayout(tokens_row)

        from PyQt6.QtWidgets import QCheckBox
        self.chk_dm_mode = QCheckBox("🎲 Режим Гейммастера (DM / Ведущий игры)", self)
        self.chk_dm_mode.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: bold; margin-top: 6px;")
        self.chk_dm_mode.setChecked(bool(current_params.get("is_dm_mode", False)))
        if self.mode == "roleplay":
            params_box.addWidget(self.chk_dm_mode)

        layout.addLayout(params_box)

        self.slider_temp.valueChanged.connect(self._on_temp_slider_changed)
        self.spin_temp.valueChanged.connect(self._on_temp_spin_changed)

        self.slider_min_p.valueChanged.connect(self._on_min_p_slider_changed)
        self.spin_min_p.valueChanged.connect(self._on_min_p_spin_changed)

        self.slider_rep_pen.valueChanged.connect(self._on_rep_pen_slider_changed)
        self.spin_rep_pen.valueChanged.connect(self._on_rep_pen_spin_changed)

        self.slider_max_tokens.valueChanged.connect(self._on_max_tokens_slider_changed)
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

        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)

        self.btn_reset = QPushButton("🔄 Сбросить по умолчанию", self)
        self.btn_reset.setObjectName("btn_reset")
        self.btn_reset.clicked.connect(self._reset_defaults)
        btn_box.addWidget(self.btn_reset)

        btn_box.addStretch()

        self.btn_cancel = QPushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("💾 Сохранить", self)
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
        self.spin_temp.blockSignals(True)
        self.spin_temp.setValue(val / 100.0)
        self.spin_temp.blockSignals(False)

    def _on_temp_spin_changed(self, val: float):
        self._on_user_input_changed()
        self.slider_temp.blockSignals(True)
        self.slider_temp.setValue(int(round(val * 100)))
        self.slider_temp.blockSignals(False)

    def _on_min_p_slider_changed(self, val: int):
        self._on_user_input_changed()
        self.spin_min_p.blockSignals(True)
        self.spin_min_p.setValue(val / 100.0)
        self.spin_min_p.blockSignals(False)

    def _on_min_p_spin_changed(self, val: float):
        self._on_user_input_changed()
        self.slider_min_p.blockSignals(True)
        self.slider_min_p.setValue(int(round(val * 100)))
        self.slider_min_p.blockSignals(False)

    def _on_rep_pen_slider_changed(self, val: int):
        self._on_user_input_changed()
        self.spin_rep_pen.blockSignals(True)
        self.spin_rep_pen.setValue(val / 100.0)
        self.spin_rep_pen.blockSignals(False)

    def _on_rep_pen_spin_changed(self, val: float):
        self._on_user_input_changed()
        self.slider_rep_pen.blockSignals(True)
        self.slider_rep_pen.setValue(int(round(val * 100)))
        self.slider_rep_pen.blockSignals(False)

    def _on_max_tokens_slider_changed(self, val: int):
        self._on_user_input_changed()
        snapped = round(val / 64) * 64
        snapped = max(256, min(4096, snapped))
        self.spin_max_tokens.blockSignals(True)
        self.spin_max_tokens.setValue(snapped)
        self.spin_max_tokens.blockSignals(False)

    def _on_max_tokens_spin_changed(self, val: int):
        self._on_user_input_changed()
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
