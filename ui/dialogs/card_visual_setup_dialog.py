"""
Card Visual & LoRA Setup Dialog for NeonRoute.
Prominently shown after a character card is imported and processed.
Allows reviewing extracted Visual DNA, searching Civitai, or picking local LoRAs.
"""

from typing import Optional, List
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QFrame
)
from services.tavern_parser import CharacterCard
from ui.components.chat_bubble import render_circular_avatar
from ui.dialogs.local_lora_dialog import LocalLoraDialog


class CardVisualSetupDialog(QDialog):
    def __init__(self, card: CharacterCard, parent=None):
        super().__init__(parent)
        self.card = card
        self.setWindowTitle(f"🎨 Настройка внешности и LoRA — {card.name}")
        self.setModal(True)
        self.resize(620, 440)

        self.current_dna = getattr(card, "visual_dna", "").strip()
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0E131F;
                color: #CBD5E1;
                border: 1px solid #1E273A;
                border-radius: 12px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: bold;
            }
            QTextEdit {
                background-color: #080D18;
                color: #38BDF8;
                border: 1px solid #1E3A5F;
                border-radius: 6px;
                padding: 8px;
                font-family: 'Consolas', monospace;
                font-size: 12px;
            }
            QTextEdit:focus {
                border-color: #00F0FF;
            }
            QPushButton {
                background-color: #172133;
                color: #E2E8F0;
                border: 1px solid #283954;
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #202D45;
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_save {
                background-color: #004D54;
                border: 1px solid #00F0FF;
                color: #00F0FF;
                font-weight: bold;
            }
            QPushButton#btn_save:hover {
                background-color: #00737D;
                color: #FFFFFF;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Header Info Banner
        header_frame = QFrame()
        header_frame.setStyleSheet("background-color: #111A28; border: 1px solid #1E2E48; border-radius: 8px; padding: 6px;")
        h_layout = QHBoxLayout(header_frame)
        h_layout.setContentsMargins(8, 4, 8, 4)

        avatar_lbl = QLabel()
        pix = render_circular_avatar(self.card.avatar_path, name=self.card.name, size=52, is_user=False)
        avatar_lbl.setPixmap(pix)
        h_layout.addWidget(avatar_lbl)

        info_box = QVBoxLayout()
        t_lbl = QLabel(f"✅ Карточка «{self.card.name}» успешно обработана!")
        t_lbl.setStyleSheet("color: #00F0FF; font-size: 13px; font-weight: bold;")
        d_lbl = QLabel("Проверьте черты внешности (Visual DNA) и выберите подходящую LoRA для точной генерации.")
        d_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: normal;")
        info_box.addWidget(t_lbl)
        info_box.addWidget(d_lbl)
        h_layout.addLayout(info_box, stretch=1)
        layout.addWidget(header_frame)

        # Visual DNA Editor
        layout.addWidget(QLabel("ТЕГИ ВНЕШНОСТИ (VISUAL DNA ДЛЯ STABLE DIFFUSION):"))
        self.input_dna = QTextEdit()
        self.input_dna.setPlainText(self.current_dna)
        self.input_dna.setPlaceholderText("Пример: 1girl, solo, short white hair, blindfold, black dress...")
        layout.addWidget(self.input_dna, stretch=1)

        # LoRA Action Buttons Row
        lora_row = QHBoxLayout()
        lora_row.setSpacing(8)

        btn_civitai = QPushButton("🔍 Найти LoRA на Civitai")
        btn_civitai.setToolTip("Найти и скачать модель персонажа из каталога Civitai")
        btn_civitai.clicked.connect(self._open_civitai_search)
        lora_row.addWidget(btn_civitai)

        btn_local = QPushButton("📁 Выбрать свою LoRA (из папки)")
        btn_local.setToolTip("Выбрать уже скачанный .safetensors файл из папки Forge models/Lora")
        btn_local.clicked.connect(self._open_local_lora_picker)
        lora_row.addWidget(btn_local)

        layout.addLayout(lora_row)

        # Bottom Buttons
        bottom_box = QHBoxLayout()
        bottom_box.addStretch()

        btn_done = QPushButton("💾 Готово (Применить)")
        btn_done.setObjectName("btn_save")
        btn_done.clicked.connect(self.accept)
        bottom_box.addWidget(btn_done)

        layout.addLayout(bottom_box)

    def _open_civitai_search(self):
        parent_gallery = self.parent()
        if parent_gallery and hasattr(parent_gallery, "_on_find_lora_clicked"):
            self.accept()
            parent_gallery._on_find_lora_clicked()

    def _open_local_lora_picker(self):
        dlg = LocalLoraDialog(character_name=self.card.name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            lora_tag, triggers = dlg.get_result()
            if lora_tag:
                current = self.input_dna.toPlainText().strip()
                add_str = f"{lora_tag}"
                if triggers:
                    add_str += f", {', '.join(triggers[:4])}"
                new_val = f"{add_str}, {current}".strip(" ,") if current else add_str
                self.input_dna.setPlainText(new_val)

    def get_visual_dna(self) -> str:
        return self.input_dna.toPlainText().strip()
