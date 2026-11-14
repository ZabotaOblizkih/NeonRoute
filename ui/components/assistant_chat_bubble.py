"""
Assistant Chat Message Bubble Component for NeonRoute.
Specialized bubble for Elaria with interactive neon art proposal chips
and CyberpunkArtPlaceholderWidget loader lifecycle.
Isolated from roleplay mechanics and widgets.
"""

import math
import logging
from pathlib import Path
from typing import Optional, List, Union, Any

from PyQt6.QtCore import (
    Qt, QTimer, QRectF,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
)
from PyQt6.QtGui import (
    QCursor, QPainter, QColor, QPen, QBrush,
    QLinearGradient, QRadialGradient, QFont
)
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QSizePolicy, QGraphicsOpacityEffect
)
from qfluentwidgets import ToolTipFilter, ToolTipPosition

from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.AssistantChatBubble")


class CyberpunkArtPlaceholderWidget(QWidget):
    """
    Киберпанк-лоадер / плейсхолдер ожидания кадра для NeonRoute.
    Glassmorphism, обсидиановый фон #0A0D14, акценты Cyan #00F0FF / Sky Blue #38BDF8.
    Мягкая пульсация неонового свечения и технологичных угловых меток.
    """
    TARGET_HEIGHT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CyberpunkArtPlaceholder")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(360)
        self.setMaximumWidth(480)
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.TARGET_HEIGHT)

        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(35)  # ~28 FPS

    def _on_tick(self):
        self._phase += 0.07
        if self._phase > 2 * math.pi:
            self._phase -= 2 * math.pi
        self.update()

    def stop(self):
        if hasattr(self, "_timer") and self._timer.isActive():
            self._timer.stop()

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)

    def cleanup(self):
        self.stop()

    def showEvent(self, event):
        if not self._timer.isActive():
            self._timer.start(35)
        super().showEvent(event)

    def paintEvent(self, event):
        h = float(self.height())
        if h <= 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w = float(self.width())
        rect = QRectF(1.5, 1.5, w - 3.0, h - 3.0)
        radius = min(14.0, h / 2.0)

        pulse = (math.sin(self._phase) + 1.0) / 2.0  # 0.0 .. 1.0

        # 1. Обсидиановый фон с деликатным вертикальным градиентом
        bg_gradient = QLinearGradient(0, 0, 0, h)
        bg_gradient.setColorAt(0.0, QColor(14, 18, 28, 240))
        bg_gradient.setColorAt(0.5, QColor(10, 13, 20, 245))
        bg_gradient.setColorAt(1.0, QColor(7, 9, 14, 250))
        painter.setBrush(QBrush(bg_gradient))

        # 2. Неоновая пульсирующая рамка (Cyan #00F0FF -> Sky Blue #38BDF8)
        border_r = int(0 * (1.0 - pulse) + 56 * pulse)
        border_g = int(240 * (1.0 - pulse) + 189 * pulse)
        border_b = int(255 * (1.0 - pulse) + 248 * pulse)
        border_a = int(60 + 110 * pulse)
        border_pen = QPen(QColor(border_r, border_g, border_b, border_a), 1.2)
        painter.setPen(border_pen)
        painter.drawRoundedRect(rect, radius, radius)

        # 3. Внутреннее мягкое радиальное свечение (Ambient holographic glow)
        glow_gradient = QRadialGradient(w / 2.0, h / 2.0, w * 0.45)
        glow_gradient.setColorAt(0.0, QColor(0, 240, 255, int(12 + 18 * pulse)))
        glow_gradient.setColorAt(0.7, QColor(56, 189, 248, int(4 + 8 * pulse)))
        glow_gradient.setColorAt(1.0, QColor(10, 13, 20, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow_gradient))
        painter.drawRoundedRect(rect, radius, radius)

        # При маленькой высоте обрезаем внутреннее содержимое
        if h < 50:
            return

        # 4. Киберпанк-угловые метки видоискателя (HUD Corner brackets)
        corner_pen = QPen(QColor(0, 240, 255, int(140 + 115 * pulse)), 1.8)
        painter.setPen(corner_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        clen = 14.0
        pad = 8.0

        # Верх-лево
        painter.drawLine(int(rect.left() + pad), int(rect.top() + pad), int(rect.left() + pad + clen), int(rect.top() + pad))
        painter.drawLine(int(rect.left() + pad), int(rect.top() + pad), int(rect.left() + pad), int(rect.top() + pad + clen))
        # Верх-право
        painter.drawLine(int(rect.right() - pad), int(rect.top() + pad), int(rect.right() - pad - clen), int(rect.top() + pad))
        painter.drawLine(int(rect.right() - pad), int(rect.top() + pad), int(rect.right() - pad), int(rect.top() + pad + clen))
        # Низ-лево
        painter.drawLine(int(rect.left() + pad), int(rect.bottom() - pad), int(rect.left() + pad + clen), int(rect.bottom() - pad))
        painter.drawLine(int(rect.left() + pad), int(rect.bottom() - pad), int(rect.left() + pad), int(rect.bottom() - pad - clen))
        # Низ-право
        painter.drawLine(int(rect.right() - pad), int(rect.bottom() - pad), int(rect.right() - pad - clen), int(rect.bottom() - pad))
        painter.drawLine(int(rect.right() - pad), int(rect.bottom() - pad), int(rect.right() - pad), int(rect.bottom() - pad - clen))

        # 5. Центральный голографический видоискатель (Viewfinder icon)
        cx = w / 2.0
        cy = h / 2.0 - 20.0
        vf_size = 38.0
        vf_rect = QRectF(cx - vf_size / 2.0, cy - vf_size / 2.0, vf_size, vf_size)

        vf_pen = QPen(QColor(56, 189, 248, int(80 + 90 * pulse)), 1.2)
        painter.setPen(vf_pen)
        painter.drawRoundedRect(vf_rect, 6.0, 6.0)

        core_alpha = int(170 + 85 * pulse)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 240, 255, core_alpha)))
        dot_r = 3.0 + 1.2 * pulse
        painter.drawEllipse(QRectF(cx - dot_r, cy - dot_r, dot_r * 2.0, dot_r * 2.0))

        ch_pen = QPen(QColor(0, 240, 255, int(120 + 80 * pulse)), 1.0)
        painter.setPen(ch_pen)
        ch_len = 5.0
        painter.drawLine(int(cx - ch_len), int(cy), int(cx - 2), int(cy))
        painter.drawLine(int(cx + 2), int(cy), int(cx + ch_len), int(cy))
        painter.drawLine(int(cx), int(cy - ch_len), int(cx), int(cy - 2))
        painter.drawLine(int(cx), int(cy + 2), int(cx), int(cy + ch_len))

        if h < 90:
            return

        # 6. Типографика NeonRoute
        title_font = QFont("Inter")
        title_font.setStyleHint(QFont.StyleHint.SansSerif)
        title_font.setPixelSize(11)
        title_font.setBold(True)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        painter.setFont(title_font)

        title_alpha = int(210 + 45 * pulse)
        painter.setPen(QColor(0, 240, 255, title_alpha))
        title_rect = QRectF(10, cy + 28, w - 20, 18)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, "СИНТЕЗ КАДРА // GENERATING")

        sub_font = QFont("Inter")
        sub_font.setStyleHint(QFont.StyleHint.SansSerif)
        sub_font.setPixelSize(10)
        painter.setFont(sub_font)
        painter.setPen(QColor(148, 163, 184, 180))
        sub_rect = QRectF(10, cy + 48, w - 20, 16)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, "SD WebUI Forge • Рендеринг сцены...")

        # 7. Нижняя неоновая световая нить
        line_w = 90.0 + 50.0 * pulse
        line_x = (w - line_w) / 2.0
        line_y = h - 18.0

        trace_grad = QLinearGradient(line_x, line_y, line_x + line_w, line_y)
        trace_grad.setColorAt(0.0, QColor(0, 240, 255, 0))
        trace_grad.setColorAt(0.5, QColor(0, 240, 255, int(160 * pulse)))
        trace_grad.setColorAt(1.0, QColor(56, 189, 248, 0))

        trace_pen = QPen(QBrush(trace_grad), 1.6)
        painter.setPen(trace_pen)
        painter.drawLine(int(line_x), int(line_y), int(line_x + line_w), int(line_y))


class AssistantChatMessageWidget(ChatMessageWidget):
    """
    Специализированный бабл сообщений Эларии.
    Изолирует логику интерактивных неоновых арт-чипов и киберпанк-лоадера от базового и ролевого чатов.
    """
    def __init__(
        self,
        sender: str,
        text: str,
        is_user: bool = False,
        is_error: bool = False,
        image_path: Optional[Path] = None,
        file_paths: Optional[List[Path]] = None,
        audio_path: Optional[Path] = None,
        avatar_path: Optional[Path] = None,
        translated_text: Optional[str] = None,
        is_showing_translation: bool = False,
        font_size: int = 13,
        negative_prompt: Optional[str] = None,
        timestamp: Optional[str] = None,
        is_roleplay: bool = False,
        generation_params: Optional[dict] = None,
        message_id: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        visual_intent: Optional[Union[dict, Any]] = None,
        parent=None,
        **kwargs
    ):
        kwargs.pop("is_roleplay", None)
        super().__init__(
            sender=sender,
            text=text,
            is_user=is_user,
            is_error=is_error,
            image_path=image_path,
            file_paths=file_paths,
            audio_path=audio_path,
            avatar_path=avatar_path,
            translated_text=translated_text,
            is_showing_translation=is_showing_translation,
            font_size=font_size,
            negative_prompt=negative_prompt,
            timestamp=timestamp,
            is_roleplay=False,
            generation_params=generation_params,
            message_id=message_id,
            reply_to_id=reply_to_id,
            visual_intent=visual_intent,
            parent=parent,
            **kwargs
        )
        self.art_proposal_chip: Optional[QPushButton] = None
        self.art_placeholder: Optional[CyberpunkArtPlaceholderWidget] = None
        self._placeholder_anim_group: Optional[QParallelAnimationGroup] = None

    def show_art_loading_placeholder(self):
        """Отображает киберпанк-лоадер синтеза кадра в теле бабла с плавной анимацией разворачивания."""
        if getattr(self, "art_placeholder", None) is not None:
            return

        if getattr(self, "art_proposal_chip", None) is not None:
            self.art_proposal_chip.setVisible(False)

        if not hasattr(self, "bubble_container") or not self.bubble_container:
            return

        # 1. Мгновенное скрытие пустого текста без остаточных отступов
        if not self.raw_text or not self.raw_text.strip():
            if hasattr(self, "text_browser") and self.text_browser:
                self.text_browser.setFixedHeight(0)
                self.text_browser.setVisible(False)
                self.text_browser.hide()

        # 2. Создание плейсхолдера с анимацией разворачивания по высоте и прозрачности
        target_h = CyberpunkArtPlaceholderWidget.TARGET_HEIGHT
        placeholder = CyberpunkArtPlaceholderWidget(self.bubble_container)
        self.art_placeholder = placeholder
        placeholder.setMinimumHeight(0)
        placeholder.setMaximumHeight(0)

        opacity_effect = QGraphicsOpacityEffect(placeholder)
        opacity_effect.setOpacity(0.0)
        placeholder.setGraphicsEffect(opacity_effect)

        layout = self.bubble_container.layout()
        if layout:
            layout.addWidget(placeholder)

        if self.bubble_container:
            self.bubble_container.setMinimumWidth(max(self.bubble_container.minimumWidth(), 380))

        anim_group = QParallelAnimationGroup(self)

        height_anim = QPropertyAnimation(placeholder, b"maximumHeight", anim_group)
        height_anim.setDuration(300)
        height_anim.setStartValue(0)
        height_anim.setEndValue(target_h)
        height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        opacity_anim = QPropertyAnimation(opacity_effect, b"opacity", anim_group)
        opacity_anim.setDuration(300)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim_group.addAnimation(height_anim)
        anim_group.addAnimation(opacity_anim)

        def _on_anim_step():
            self._update_bubble_geometry()
            p = self.parentWidget()
            if p and hasattr(p, "layout") and p.layout():
                p.layout().activate()

        height_anim.valueChanged.connect(lambda _: _on_anim_step())

        def _on_anim_finished():
            placeholder.setFixedHeight(target_h)
            placeholder.setGraphicsEffect(None)
            self._update_bubble_geometry()
            self._adjust_text_height()

        anim_group.finished.connect(_on_anim_finished)
        self._placeholder_anim_group = anim_group
        anim_group.start()

    def hide_art_loading_placeholder(self):
        """Плавно завершает и удаляет плейсхолдер синтеза кадра."""
        if hasattr(self, "_placeholder_anim_group") and self._placeholder_anim_group:
            try:
                self._placeholder_anim_group.stop()
            except Exception:
                pass
            self._placeholder_anim_group = None

        placeholder = getattr(self, "art_placeholder", None)
        if placeholder is not None:
            try:
                placeholder.stop()
                placeholder.hide()
                placeholder.deleteLater()
            except Exception:
                pass
            self.art_placeholder = None
            self._update_bubble_geometry()
            self._adjust_text_height()

    def set_image(self, img_path: Union[str, Path]):
        """Бесшовный монтаж изображения с автоматическим снятием плейсхолдера."""
        if getattr(self, "art_placeholder", None) is not None:
            self.hide_art_loading_placeholder()
        super().set_image(img_path)

    def attach_file(self, file_path: Path, generation_params: Optional[dict] = None):
        """Бесшовное прикрепление файла с автоматическим снятием плейсхолдера."""
        if getattr(self, "art_placeholder", None) is not None:
            self.hide_art_loading_placeholder()
        super().attach_file(file_path, generation_params=generation_params)

    def add_art_proposal_chip(self, prompt: str, on_generate: Optional[callable] = None):
        """Монтирует интерактивный неоновый чип генерации под текстом ответа."""
        if not self.bubble_container:
            return

        if getattr(self, "art_proposal_chip", None) is not None:
            try:
                self.art_proposal_chip.deleteLater()
            except Exception:
                pass
            self.art_proposal_chip = None

        chip_btn = QPushButton("🎨 Сгенерировать иллюстрацию", self.bubble_container)
        chip_btn.setObjectName("ArtProposalChip")
        chip_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        chip_btn.setToolTip(f"Запустить генерацию кадра в Forge:\n{prompt}")
        chip_btn.installEventFilter(ToolTipFilter(chip_btn, 300, ToolTipPosition.BOTTOM))
        chip_btn.setStyleSheet("""
            QPushButton#ArtProposalChip {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 240, 255, 0.12),
                    stop:1 rgba(14, 165, 233, 0.18));
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.45);
                border-radius: 14px;
                padding: 6px 16px;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
                margin-top: 4px;
                margin-bottom: 3px;
                text-align: left;
            }
            QPushButton#ArtProposalChip:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 240, 255, 0.28),
                    stop:1 rgba(14, 165, 233, 0.36));
                border-color: #00F0FF;
                color: #FFFFFF;
            }
            QPushButton#ArtProposalChip:pressed {
                background-color: rgba(0, 240, 255, 0.08);
            }
            QPushButton#ArtProposalChip:disabled {
                background-color: rgba(30, 41, 59, 0.5);
                border-color: rgba(148, 163, 184, 0.25);
                color: #94A3B8;
            }
        """)

        def _safe_generate():
            chip_btn.setEnabled(False)
            chip_btn.setText("⏳ Запуск генерации...")
            if on_generate:
                try:
                    on_generate()
                except Exception as ex:
                    logger.error(f"Art generation invocation failed: {ex}")
                    chip_btn.setEnabled(True)
                    chip_btn.setText("🎨 Сгенерировать иллюстрацию")

        chip_btn.clicked.connect(_safe_generate)

        self.art_proposal_chip = chip_btn

        layout = self.bubble_container.layout()
        if layout:
            if hasattr(self, "footer_layout") and self.footer_layout:
                idx = layout.indexOf(self.footer_layout)
                if idx >= 0:
                    layout.insertWidget(idx, chip_btn)
                elif layout.count() > 1:
                    layout.insertWidget(layout.count() - 1, chip_btn)
                else:
                    layout.addWidget(chip_btn)
            elif layout.count() > 1:
                layout.insertWidget(layout.count() - 1, chip_btn)
            else:
                layout.addWidget(chip_btn)

        self._update_bubble_geometry()
        self._adjust_text_height()

    def set_scene_art_busy(self, busy: bool, text: Optional[str] = None):
        """Переключает чип Эларии в состояние ожидания при рендере."""
        chip = getattr(self, "art_proposal_chip", None)
        if chip is not None:
            try:
                chip.setEnabled(not busy)
                if busy:
                    chip.setText(text or "⏳ Генерация иллюстрации...")
                else:
                    chip.setText("🎨 Сгенерировать иллюстрацию")
            except RuntimeError:
                pass
        if busy:
            self.show_art_loading_placeholder()
        else:
            self.hide_art_loading_placeholder()
        self._update_bubble_geometry()

    def cleanup(self):
        """Гарантированно останавливает фоновые анимации и лоадеры перед удалением."""
        self.hide_art_loading_placeholder()
        super().cleanup()
