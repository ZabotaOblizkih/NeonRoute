"""
ADetailer Settings Sub-Dialog for NeonRoute.
Modal dialog for configuring per-profile Face and Hand detection models,
thresholds, inpaint windows, and denoise parameters.
"""

from pathlib import Path
from typing import Dict, Any, List
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QFrame, QWidget
)
from qfluentwidgets import (
    ComboBox,
    SwitchButton,
    PrimaryPushButton,
    PushButton,
    DoubleSpinBox,
    SpinBox,
    Slider,
    setTheme,
    Theme
)

from services.asset_validator import MODELS_ADETAILER_DIR


class ADetailerSettingsDialog(QDialog):
    """Модальное окно точной настройки ADetailer для выбранной модели."""

    def __init__(self, current_pipeline_params: Dict[str, Any], is_xl: bool = False, parent: QWidget = None):
        super().__init__(parent)
        try:
            setTheme(Theme.DARK)
        except Exception:
            pass
        self.setWindowTitle("Настройки ADetailer — Лица и Руки")
        self.resize(560, 520)
        self.setModal(True)
        self.is_xl = is_xl
        self.params = dict(current_pipeline_params or {})
        self.result_params: Dict[str, Any] = dict(current_pipeline_params or {})

        self._scan_available_models()
        self._setup_ui()
        self._load_values()

    def _scan_available_models(self):
        """Сканирует доступные на диске .pt модели ADetailer."""
        installed = []
        if MODELS_ADETAILER_DIR.exists():
            installed = [f.name for f in MODELS_ADETAILER_DIR.glob("*.pt") if f.is_file()]

        # Безопасный фоллбэк, если папка пуста
        self.face_models = sorted(list(set([m for m in installed if "face" in m.lower()] or ["face_yolov8m.pt", "face_yolov8n.pt"])))
        self.hand_models = sorted(list(set([m for m in installed if "hand" in m.lower()] or ["hand_yolov9c.pt", "hand_yolov8s.pt", "hand_yolov8n.pt"])))

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.98);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 12px;
            }
            QLabel {
                color: #CBD5E1;
                font-size: 12px;
            }
            QFrame#Card {
                background-color: rgba(18, 24, 38, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                padding: 12px;
            }
            QToolTip {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.4);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        # ── 1. Главный переключатель ──
        master_card = QFrame(self)
        master_card.setObjectName("Card")
        master_layout = QHBoxLayout(master_card)
        
        lbl_master = QLabel("<b>Активация ADetailer для этой модели:</b>", master_card)
        self.switch_master = SwitchButton(master_card)
        self.switch_master.setOnText("ВКЛ")
        self.switch_master.setOffText("ВЫКЛ")
        self.switch_master.checkedChanged.connect(self._on_master_toggled)

        tip_master = (
            "<b>Главный выключатель ADetailer</b><br>"
            "Автоматически находит лица и руки после основной генерации "
            "и точечно перерисовывает их в высоком качестве, убирая дефекты и мыло."
        )
        lbl_master.setToolTip(tip_master)
        self.switch_master.setToolTip(tip_master)

        master_layout.addWidget(lbl_master)
        master_layout.addStretch()
        master_layout.addWidget(self.switch_master)
        layout.addWidget(master_card)

        # ── 2. Блок детекции Лица ──
        self.card_face = QFrame(self)
        self.card_face.setObjectName("Card")
        face_layout = QGridLayout(self.card_face)
        face_layout.setSpacing(10)

        lbl_face_title = QLabel("<b style='color:#00F0FF;'>Слот 1: Лицо (Face)</b>", self.card_face)
        face_layout.addWidget(lbl_face_title, 0, 0, 1, 2)

        face_layout.addWidget(QLabel("Модель детектора:"), 1, 0)
        self.combo_face_model = ComboBox(self.card_face)
        for m in self.face_models:
            self.combo_face_model.addItem(m)
        tip_face_model = (
            "<b>Детектор лица (YOLO)</b><br>"
            "Нейросеть, которая сканирует изображение и находит голову персонажа.<br>"
            "• <b>face_yolov8m.pt</b> — средняя модель, практически исключает ложные срабатывания (рекомендуется).<br>"
            "• <b>face_yolov8n.pt</b> — ультралегкая модель, быстрая, но может спутать кулак или тени с лицом."
        )
        self.combo_face_model.setToolTip(tip_face_model)
        face_layout.addWidget(self.combo_face_model, 1, 1)

        face_layout.addWidget(QLabel("Порог уверенности (Conf):"), 2, 0)
        self.spin_face_conf = DoubleSpinBox(self.card_face)
        self.spin_face_conf.setRange(0.20, 0.90)
        self.spin_face_conf.setSingleStep(0.02)
        self.spin_face_conf.setValue(0.44)
        tip_face_conf = (
            "<b>Порог уверенности (Confidence)</b><br>"
            "Насколько детектор должен быть уверен, что перед ним настоящее лицо.<br>"
            "• <b>Ниже (0.25–0.35):</b> найдет лицо даже в темноте или издалека, но может сработать на колени или костяшки пальцев.<br>"
            "• <b>Выше (0.42–0.50):</b> отсекает все ложные срабатывания, обрабатывая только реальную голову.<br>"
            "<i>Оптимально: 0.44</i>"
        )
        self.spin_face_conf.setToolTip(tip_face_conf)
        face_layout.addWidget(self.spin_face_conf, 2, 1)

        face_layout.addWidget(QLabel("Сила денойза (Denoise):"), 3, 0)
        self.spin_face_denoise = DoubleSpinBox(self.card_face)
        self.spin_face_denoise.setRange(0.15, 0.60)
        self.spin_face_denoise.setSingleStep(0.01)
        self.spin_face_denoise.setValue(0.35 if self.is_xl else 0.32)
        tip_face_denoise = (
            "<b>Сила перерисовки (Denoise)</b><br>"
            "Насколько сильно изменяются черты лица при исправлении.<br>"
            "• <b>0.20–0.28:</b> легкая косметика, резкость глаз и ресниц без изменения исходного лица.<br>"
            "• <b>0.32–0.36:</b> идеальный баланс: чинит косоглазие и артефакты, сохраняя типаж.<br>"
            "• <b>Выше 0.45:</b> может полностью заменить лицо на другого человека.<br>"
            "<i>Оптимально: 0.32 (SD 1.5) / 0.35 (SDXL)</i>"
        )
        self.spin_face_denoise.setToolTip(tip_face_denoise)
        face_layout.addWidget(self.spin_face_denoise, 3, 1)

        face_layout.addWidget(QLabel("Размер инпейнта (px):"), 4, 0)
        self.spin_face_inpaint = SpinBox(self.card_face)
        self.spin_face_inpaint.setRange(384, 1024)
        self.spin_face_inpaint.setSingleStep(64)
        self.spin_face_inpaint.setValue(768 if self.is_xl else 512)
        tip_face_inpaint = (
            "<b>Разрешение окна исправления</b><br>"
            "Размер квадратного кадра, в котором нейросеть прорисовывает лицо крупным планом.<br>"
            "• <b>512 px:</b> быстро, идеально подходит для моделей SD 1.5.<br>"
            "• <b>768 px:</b> максимальная резкость радужки глаз и текстуры кожи для SDXL.<br>"
            "<i>Оптимально: 512 для SD 1.5 / 768 для SDXL</i>"
        )
        self.spin_face_inpaint.setToolTip(tip_face_inpaint)
        face_layout.addWidget(self.spin_face_inpaint, 4, 1)

        layout.addWidget(self.card_face)

        # ── 3. Блок детекции Рук ──
        self.card_hands = QFrame(self)
        self.card_hands.setObjectName("Card")
        hands_layout = QGridLayout(self.card_hands)
        hands_layout.setSpacing(10)

        h_top = QHBoxLayout()
        h_top.addWidget(QLabel("<b style='color:#00F0FF;'>Слот 2: Кисти рук (Hands)</b>", self.card_hands))
        h_top.addStretch()
        self.switch_hands = SwitchButton(self.card_hands)
        self.switch_hands.setOnText("ВКЛ")
        self.switch_hands.setOffText("ВЫКЛ")
        self.switch_hands.checkedChanged.connect(self._on_hands_toggled)
        tip_hands_switch = (
            "<b>Исправление кистей рук</b><br>"
            "Позволяет устранять склеенные или лишние пальцы.<br>"
            "<i>Совет: если персонаж стоит спиной, руки в карманах или за кадром, "
            "выключите этот пункт, чтобы сэкономить 15–25 секунд рендера.</i>"
        )
        self.switch_hands.setToolTip(tip_hands_switch)
        h_top.addWidget(self.switch_hands)
        hands_layout.addLayout(h_top, 0, 0, 1, 2)

        hands_layout.addWidget(QLabel("Модель детектора:"), 1, 0)
        self.combo_hands_model = ComboBox(self.card_hands)
        for m in self.hand_models:
            self.combo_hands_model.addItem(m)
        tip_hands_model = (
            "<b>Детектор кистей рук</b><br>"
            "Нейросеть для поиска ладоней и пальцев.<br>"
            "• <b>hand_yolov9c.pt / yolov8s.pt:</b> современные точные детекторы, хорошо отличают пальцы от складок одежды.<br>"
            "• <b>hand_yolov8n.pt:</b> базовая легкая модель."
        )
        self.combo_hands_model.setToolTip(tip_hands_model)
        hands_layout.addWidget(self.combo_hands_model, 1, 1)

        hands_layout.addWidget(QLabel("Порог уверенности (Conf):"), 2, 0)
        self.spin_hands_conf = DoubleSpinBox(self.card_hands)
        self.spin_hands_conf.setRange(0.20, 0.90)
        self.spin_hands_conf.setSingleStep(0.02)
        self.spin_hands_conf.setValue(0.38)
        tip_hands_conf = (
            "<b>Порог распознавания руки</b><br>"
            "При низких значениях (до 0.30) складки ткани, шнурки кроссовок и рельеф мышц "
            "часто принимаются за пальцы и превращаются в кашу.<br>"
            "<i>Оптимально: 0.38–0.40</i>"
        )
        self.spin_hands_conf.setToolTip(tip_hands_conf)
        hands_layout.addWidget(self.spin_hands_conf, 2, 1)

        hands_layout.addWidget(QLabel("Сила денойза (Denoise):"), 3, 0)
        self.spin_hands_denoise = DoubleSpinBox(self.card_hands)
        self.spin_hands_denoise.setRange(0.20, 0.60)
        self.spin_hands_denoise.setSingleStep(0.01)
        self.spin_hands_denoise.setValue(0.38 if self.is_xl else 0.40)
        tip_hands_denoise = (
            "<b>Сила перерисовки пальцев</b><br>"
            "• <b>0.35–0.40:</b> аккуратно выравнивает пальцы, сохраняя хват оружия или предмета.<br>"
            "• <b>Выше 0.45:</b> перерисовывает кисть заново с нуля (может потерять предмет в руке).<br>"
            "<i>Оптимально: 0.38–0.40</i>"
        )
        self.spin_hands_denoise.setToolTip(tip_hands_denoise)
        hands_layout.addWidget(self.spin_hands_denoise, 3, 1)

        hands_layout.addWidget(QLabel("Размер инпейнта (px):"), 4, 0)
        self.spin_hands_inpaint = SpinBox(self.card_hands)
        self.spin_hands_inpaint.setRange(384, 768)
        self.spin_hands_inpaint.setSingleStep(64)
        self.spin_hands_inpaint.setValue(512)
        tip_hands_inpaint = (
            "<b>Разрешение окна исправления рук</b><br>"
            "Кисти рук меньше лица, поэтому для них не требуется огромного окна.<br>"
            "Значение <b>512 px</b> обеспечивает анатомическую четкость пальцев без риска переполнения VRAM.<br>"
            "<i>Оптимально: 512 px</i>"
        )
        self.spin_hands_inpaint.setToolTip(tip_hands_inpaint)
        hands_layout.addWidget(self.spin_hands_inpaint, 4, 1)

        layout.addWidget(self.card_hands)

        # ── 4. Кнопки сохранения ──
        btns_layout = QHBoxLayout()
        btns_layout.addStretch()
        self.btn_cancel = PushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = PrimaryPushButton("Применить", self)
        self.btn_save.clicked.connect(self._on_save_clicked)

        btns_layout.addWidget(self.btn_cancel)
        btns_layout.addWidget(self.btn_save)
        layout.addLayout(btns_layout)

    def _load_values(self):
        p = self.params
        self.switch_master.setChecked(bool(p.get("enable_adetailer", True)))

        face_m = p.get("adetailer_face_model", "face_yolov8m.pt")
        if self.combo_face_model.findText(face_m) >= 0:
            self.combo_face_model.setCurrentText(face_m)

        self.spin_face_conf.setValue(float(p.get("adetailer_face_confidence", 0.44)))
        self.spin_face_denoise.setValue(float(p.get("ad_denoising_strength", p.get("adetailer_face_denoise", 0.35 if self.is_xl else 0.32))))
        saved_face_sz = p.get("adetailer_face_inpaint_size", p.get("adetailer_inpaint_size", 768 if self.is_xl else 512))
        self.spin_face_inpaint.setValue(int(saved_face_sz))

        self.switch_hands.setChecked(bool(p.get("adetailer_hands_enabled", True)))
        hand_m = p.get("adetailer_hands_model", "hand_yolov9c.pt")
        if self.combo_hands_model.findText(hand_m) >= 0:
            self.combo_hands_model.setCurrentText(hand_m)

        self.spin_hands_conf.setValue(float(p.get("adetailer_hands_confidence", 0.38)))
        self.spin_hands_denoise.setValue(float(p.get("adetailer_hands_denoise", 0.38 if self.is_xl else 0.40)))
        self.spin_hands_inpaint.setValue(int(p.get("adetailer_hands_inpaint_size", 512)))

        self._on_master_toggled(self.switch_master.isChecked())
        self._on_hands_toggled(self.switch_hands.isChecked())

    def _on_master_toggled(self, checked: bool):
        self.card_face.setEnabled(checked)
        self.card_hands.setEnabled(checked)

    def _on_hands_toggled(self, checked: bool):
        self.combo_hands_model.setEnabled(checked)
        self.spin_hands_conf.setEnabled(checked)
        self.spin_hands_denoise.setEnabled(checked)
        self.spin_hands_inpaint.setEnabled(checked)

    def get_settings(self) -> Dict[str, Any]:
        """Возвращает технические настройки ADetailer без перекрытия основного промпта."""
        face_sz = int(self.spin_face_inpaint.value())
        return {
            "enable_adetailer": self.switch_master.isChecked(),
            "adetailer_face_model": self.combo_face_model.currentText(),
            "adetailer_face_confidence": round(self.spin_face_conf.value(), 2),
            "adetailer_face_denoise": round(self.spin_face_denoise.value(), 2),
            "adetailer_face_inpaint_size": face_sz,
            "adetailer_inpaint_size": face_sz,
            "adetailer_hands_enabled": self.switch_hands.isChecked(),
            "adetailer_hands_model": self.combo_hands_model.currentText(),
            "adetailer_hands_confidence": round(self.spin_hands_conf.value(), 2),
            "adetailer_hands_denoise": round(self.spin_hands_denoise.value(), 2),
            "adetailer_hands_inpaint_size": int(self.spin_hands_inpaint.value()),
            "adetailer_hands_mask_blur": int(self.params.get("adetailer_hands_mask_blur", 8)),
            "adetailer_hands_prompt": "",
            "adetailer_hands_negative_prompt": "",
        }

    def _on_save_clicked(self):
        self.result_params = self.get_settings()
        self.accept()
