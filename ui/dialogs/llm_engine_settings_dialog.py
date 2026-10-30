"""
ui/dialogs/llm_engine_settings_dialog.py
Диалог настройки движка LLM (llama-server.exe) в стиле Cyberpunk Glassmorphism / Fluent Dark.
Позволяет настраивать квантование KV-кэша (Q8_0 / Q4_0 / FP16) и Flash Attention (-fa on/off)
с реактивной карточкой подсказок и аппаратным аудитом GPU.
"""

import logging
from typing import Optional, Dict, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
    QFrame,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    SwitchButton,
    SimpleCardWidget,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
    IconWidget,
    InfoBar,
    InfoBarPosition,
)

from services.layer_budgeter import LayerBudgeter
from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.LLMEngineSettingsDialog")


class LLMEngineSettingsDialog(QDialog):
    """
    Лаконичный модальный диалог на базе PyQt-Fluent-Widgets:
    - Аппаратный статус GPU и Compute Capability
    - Селектор типа KV-кэша с реактивной подсказкой
    - Переключатель Flash Attention
    - Сохранение и горячий перезапуск сервера
    """

    KV_OPTIONS = [
        ("q8_0", "Q8_0 (Рекомендуется / Баланс)"),
        ("q4_0", "Q4_0 (Экстремальная экономия VRAM)"),
        ("f16", "FP16 (Без сжатия / Эталонная точность)"),
    ]

    HINTS_DATA = {
        "q8_0": {
            "title": "🟢 Q8_0 — Оптимальный баланс (Рекомендуется)",
            "title_color": "#10B981",
            "border_color": "rgba(16, 185, 129, 0.35)",
            "bg_color": "rgba(16, 185, 129, 0.06)",
            "budget": "~64 КБ на токен | Экономия VRAM: 50%",
            "effect": "Сжимает контекст вдвое без потери качества (деградация <0.05%). Идеально для диалогов 24.5K на 8 ГБ VRAM.",
            "side_effect": "Побочные эффекты отсутствуют. Рекомендуется для всех видеокарт поколения Turing и новее.",
            "side_color": "#10B981",
        },
        "q4_0": {
            "title": "🟡 Q4_0 — Экстремальная экономия памяти",
            "title_color": "#F59E0B",
            "border_color": "rgba(245, 158, 11, 0.35)",
            "bg_color": "rgba(245, 158, 11, 0.06)",
            "budget": "~36 КБ на токен | Экономия VRAM: 75%",
            "effect": "Буфер в 4 раза легче, высвобождает до 1.5 ГБ VRAM под веса тяжелых моделей.",
            "side_effect": "В сверхдлинных чатах (>15K токенов) возможна потеря связности и мелких деталей сюжета/кода.",
            "side_color": "#FB923C",
        },
        "f16": {
            "title": "⚪ FP16 — Без сжатия (Студийная точность)",
            "title_color": "#CBD5E1",
            "border_color": "rgba(148, 163, 184, 0.30)",
            "bg_color": "rgba(148, 163, 184, 0.05)",
            "budget": "~128 КБ на токен | Экономия VRAM: 0%",
            "effect": "Математически точный расчет внимания без квантования.",
            "side_effect": "На контексте 24K токенов кэш займет более 3.2 ГБ VRAM, что вызовет вытеснение слоев модели на медленный CPU.",
            "side_color": "#F87171",
        },
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self._parent_ref = parent
        self.setWindowTitle("Настройки движка LLM (llama-server)")
        self.setFixedSize(580, 570)
        self.setModal(True)

        self._load_current_settings()
        self._init_ui()
        self._apply_theme()
        self._sync_with_current_config()

    def _load_current_settings(self):
        """Загрузка профиля оборудования и сохраненной конфигурации."""
        self.hw_profile = LayerBudgeter.detect_hardware_kv_profile()
        self.app_cfg = ConfigManager.load_config()
        self.current_kv = self.app_cfg.get("kv_cache_type", "q8_0").lower()
        if self.current_kv in ("fp16", "f16"):
            self.current_kv = "f16"
        elif "q4" in self.current_kv:
            self.current_kv = "q4_0"
        else:
            self.current_kv = "q8_0"

        self.current_fa = bool(self.app_cfg.get("flash_attention_enabled", True))

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(14)

        # 1. Заголовок диалога
        header_layout = QHBoxLayout()
        header_icon = IconWidget(FIF.DEVELOPER_TOOLS, self)
        header_icon.setFixedSize(22, 22)
        header_layout.addWidget(header_icon)

        title_label = QLabel("Настройки движка LLM (llama-server)", self)
        title_label.setObjectName("dialogTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        subtitle = CaptionLabel("Оптимизация распределения памяти и аппаратного ускорения контекста", self)
        subtitle.setObjectName("dialogSubtitle")
        main_layout.addWidget(subtitle)

        # 2. Карточка аппаратного статуса GPU
        self.hw_card = SimpleCardWidget(self)
        self.hw_card.setObjectName("hwCard")
        hw_layout = QHBoxLayout(self.hw_card)
        hw_layout.setContentsMargins(16, 12, 16, 12)
        hw_layout.setSpacing(12)

        chip_icon = IconWidget(FIF.APPLICATION, self.hw_card)
        chip_icon.setFixedSize(28, 28)
        hw_layout.addWidget(chip_icon)

        hw_text_layout = QVBoxLayout()
        hw_text_layout.setSpacing(2)

        gpu_name = self.hw_profile.get("gpu_name", "NVIDIA GPU")
        cc_str = self.hw_profile.get("compute_cap", "N/A")
        fa_supported = self.hw_profile.get("supported", False)

        hw_title = BodyLabel(f"Видеокарта: {gpu_name}", self.hw_card)
        hw_title.setStyleSheet("font-weight: bold; color: #E2E8F0;")
        hw_text_layout.addWidget(hw_title)

        status_text = (
            f"Compute Capability {cc_str} • Аппаратный Flash Attention поддерживается (Turing+)"
            if fa_supported
            else f"Compute Capability {cc_str} • Flash Attention не поддерживается архитектурой чипа"
        )
        hw_desc = CaptionLabel(status_text, self.hw_card)
        hw_desc.setStyleSheet(f"color: {'#10B981' if fa_supported else '#94A3B8'};")
        hw_text_layout.addWidget(hw_desc)

        hw_layout.addLayout(hw_text_layout)
        hw_layout.addStretch()
        main_layout.addWidget(self.hw_card)

        # 3. Блок выбора квантования KV-кэша
        kv_section_label = BodyLabel("Тип буфера KV-кэша (контекста):", self)
        kv_section_label.setStyleSheet("font-weight: 600; color: #CBD5E1;")
        main_layout.addWidget(kv_section_label)

        self.kv_combo = ComboBox(self)
        for _, label in self.KV_OPTIONS:
            self.kv_combo.addItem(label)
        self.kv_combo.currentIndexChanged.connect(self._on_kv_selection_changed)
        main_layout.addWidget(self.kv_combo)

        # 4. Реактивная карточка-подсказка (SimpleCardWidget)
        self.hint_card = SimpleCardWidget(self)
        self.hint_card.setObjectName("hintCard")
        hint_layout = QVBoxLayout(self.hint_card)
        hint_layout.setContentsMargins(16, 14, 16, 14)
        hint_layout.setSpacing(8)

        self.hint_title_lbl = BodyLabel("", self.hint_card)
        self.hint_title_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        hint_layout.addWidget(self.hint_title_lbl)

        self.hint_budget_lbl = CaptionLabel("", self.hint_card)
        self.hint_budget_lbl.setStyleSheet("color: #00F0FF; font-weight: 600; font-size: 11px;")
        hint_layout.addWidget(self.hint_budget_lbl)

        self.hint_effect_lbl = CaptionLabel("", self.hint_card)
        self.hint_effect_lbl.setWordWrap(True)
        self.hint_effect_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; line-height: 1.4;")
        hint_layout.addWidget(self.hint_effect_lbl)

        self.hint_side_lbl = CaptionLabel("", self.hint_card)
        self.hint_side_lbl.setWordWrap(True)
        self.hint_side_lbl.setStyleSheet("font-size: 11px; line-height: 1.4;")
        hint_layout.addWidget(self.hint_side_lbl)

        main_layout.addWidget(self.hint_card)

        # 5. Переключатель Flash Attention
        fa_frame = QFrame(self)
        fa_frame.setObjectName("faFrame")
        fa_layout = QHBoxLayout(fa_frame)
        fa_layout.setContentsMargins(12, 10, 12, 10)
        fa_layout.setSpacing(12)

        fa_text_layout = QVBoxLayout()
        fa_text_layout.setSpacing(2)
        fa_title = BodyLabel("Аппаратное ускорение Flash Attention (-fa on)", fa_frame)
        fa_title.setStyleSheet("font-weight: 600; color: #E2E8F0;")
        fa_text_layout.addWidget(fa_title)

        fa_desc = CaptionLabel("Выполняет расчет внимания через быстрые SRAM-тензорные ядра чипа.", fa_frame)
        fa_desc.setStyleSheet("color: #94A3B8; font-size: 11px;")
        fa_text_layout.addWidget(fa_desc)
        fa_layout.addLayout(fa_text_layout)
        fa_layout.addStretch()

        self.switch_fa = SwitchButton(fa_frame)
        self.switch_fa.setOnText("ВКЛ")
        self.switch_fa.setOffText("ВЫКЛ")
        self.switch_fa.setToolTip(
            "Выполняет расчет внимания через быстрые SRAM-тензорные ядра. "
            "Ускоряет генерацию длинных ответов в 2–3 раза. Требует GPU Turing+ (CC >= 7.5)."
        )
        fa_layout.addWidget(self.switch_fa)
        main_layout.addWidget(fa_frame)

        main_layout.addStretch()

        # 6. Кнопки управления (Сохранить и перезапустить / Отмена)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.btn_cancel = PushButton("Отмена", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = PrimaryPushButton("Сохранить и перезапустить", self)
        self.btn_save.clicked.connect(self._on_save_clicked)
        btn_layout.addWidget(self.btn_save)

        main_layout.addLayout(btn_layout)

    def _apply_theme(self):
        """Fluent Dark / Cyberpunk Glassmorphism стилизация."""
        self.setStyleSheet("""
            QDialog {
                background-color: #0B0F19;
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.20);
                border-radius: 12px;
            }
            QLabel#dialogTitle {
                color: #00F0FF;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel#dialogSubtitle {
                color: #64748B;
                font-size: 11px;
            }
            QFrame#hwCard {
                background-color: rgba(18, 24, 38, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QFrame#faFrame {
                background-color: rgba(18, 24, 38, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)

    def _sync_with_current_config(self):
        """Устанавливает текущие значения из конфига в элементы управления."""
        # KV Combo
        matched_idx = 0
        for i, (k, _) in enumerate(self.KV_OPTIONS):
            if k == self.current_kv:
                matched_idx = i
                break
        self.kv_combo.setCurrentIndex(matched_idx)
        self._update_hint_card(self.current_kv)

        # Flash Attention Switch
        self.switch_fa.setChecked(self.current_fa)

    def _on_kv_selection_changed(self, index: int):
        """Слот изменения выбранного типа KV-кэша."""
        if 0 <= index < len(self.KV_OPTIONS):
            kv_key = self.KV_OPTIONS[index][0]
            self._update_hint_card(kv_key)

    def _update_hint_card(self, kv_key: str):
        """Реактивное обновление текстов и цветов карточки-подсказки."""
        data = self.HINTS_DATA.get(kv_key, self.HINTS_DATA["q8_0"])

        self.hint_title_lbl.setText(data["title"])
        self.hint_title_lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {data['title_color']};")

        self.hint_budget_lbl.setText(data["budget"])

        self.hint_effect_lbl.setText(data["effect"])

        self.hint_side_lbl.setText(data["side_effect"])
        self.hint_side_lbl.setStyleSheet(f"font-size: 11px; line-height: 1.4; color: {data['side_color']};")

        # Плавное обновление карточки
        self.hint_card.setStyleSheet(f"""
            QFrame#hintCard {{
                background-color: {data['bg_color']};
                border: 1px solid {data['border_color']};
                border-radius: 8px;
            }}
        """)

    def _on_save_clicked(self):
        """Сохранение выбранных параметров и мягкий перезапуск llama-server."""
        selected_idx = self.kv_combo.currentIndex()
        selected_kv = self.KV_OPTIONS[selected_idx][0] if 0 <= selected_idx < len(self.KV_OPTIONS) else "q8_0"
        fa_enabled = self.switch_fa.isChecked()

        try:
            cfg = ConfigManager.load_config()
            cfg["kv_cache_type"] = selected_kv
            cfg["flash_attention_enabled"] = fa_enabled
            ConfigManager.save_config(cfg)
            logger.info(f"[LLMEngineSettingsDialog] Сохранено: kv_cache_type={selected_kv}, flash_attention={fa_enabled}")

            # Инициируем мягкий перезапуск сервера, если он активен
            parent_win = self._parent_ref or self.parent()
            pm = getattr(parent_win, "process_manager", None)
            if pm and hasattr(pm, "restart_llama_server_async"):
                if pm.is_lm_studio_running():
                    pm.restart_llama_server_async()
                    logger.info("[LLMEngineSettingsDialog] Отправлена команда перезапуска llama-server.")

            self.accept()
        except Exception as e:
            logger.error(f"[LLMEngineSettingsDialog] Ошибка сохранения параметров: {e}")
            self.reject()
