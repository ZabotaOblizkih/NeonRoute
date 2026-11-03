"""
ui/dialogs/gallery/character_card_widget.py — Виджет карточки персонажа, делегаты и рендеринг аватаров.
"""
"""
Characters Gallery Dialog for NeonRoute.
Allows viewing, editing, creating, and importing character cards for Roleplay mode.
"""

import html
import hashlib
import re
import json
import shutil
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Union

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer, QRect, QRectF
from PyQt6.QtGui import (
    QIcon, QPixmap, QColor, QPainter, QPainterPath,
    QBrush, QPen, QLinearGradient, QRadialGradient, QFont, QImage
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QTextBrowser, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QWidget, QScrollArea, QSplitter,
    QFrame, QApplication, QCheckBox, QGraphicsDropShadowEffect,
    QStyledItemDelegate
)

from ui.components.step_progress_button import StepProgressButton
from qfluentwidgets import (
    SearchLineEdit,
    PushButton,
    PrimaryPushButton,
    TransparentPushButton,
    SmoothScrollArea,
    TextEdit,
    LineEdit,
    CheckBox,
    FluentIcon as FIF,
)

from config import CHARACTERS_DIR, DATA_DIR
from services.tavern_parser import TavernParser, CharacterCard
from services.token_budget_manager import TokenBudgetManager
from services.task_queue import InferenceTask
from services.chub_client import sanitize_card_description
from services.card_translator import (
    CardTranslator,
    mask_card_tags,
    unmask_card_tags,
    RE_TAG_PATTERN,
    RE_CJK_PATTERN
)
from ui.components.chat_bubble import render_circular_avatar
from ui.dialogs.avatar_crop_dialog import AvatarCropDialog
from ui.dialogs.local_lora_dialog import LocalLoraDialog
from ui.dialogs.card_visual_setup_dialog import CardVisualSetupDialog
from ui.dialogs.gallery import GalleryDnaPartyMixin, GalleryTranslationMixin

logger = logging.getLogger("NeonRoute.CharactersGallery")

GALLERY_THUMBS_DIR = DATA_DIR / "cache" / "gallery_thumbs"
GALLERY_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

# Псевдонимы функций маскирования для обратной совместимости на уровне модуля
_mask_card_tags = CardTranslator.mask_tags
_unmask_card_tags = CardTranslator.unmask_tags
_mask_tags = CardTranslator.mask_tags
_unmask_tags = CardTranslator.unmask_tags



class CardItemDelegate(QStyledItemDelegate):
    """
    Делегат списка карточек персонажей.
    Подавляет стандартную отрисовку нативного текста и иконки Qt под кастомным виджетом карточки,
    полностью исключая наложение и дублирование графики.
    """
    def paint(self, painter, option, index):
        # CharacterCardWidget берет визуальную отрисовку полностью на себя;
        # делегат ничего не рисует, исключая артефакты дублирования текста и иконки.
        pass


def render_seamless_avatar(
    avatar_path: Optional[Union[Path, str]] = None,
    name: str = "Assistant",
    size: int = 44
) -> QPixmap:
    """
    Рендерит круглый аватар без жестких рамок (border: none)
    с мягким растворением внешнего контура в фон (edge feathering via radial gradient alpha mask).
    """
    scale_factor = 3
    render_size = size * scale_factor
    center = float(render_size) / 2.0
    radius = float(render_size) / 2.0

    image = QImage(render_size, render_size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    p_path = Path(avatar_path) if avatar_path else None
    loaded = False

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    if p_path and p_path.exists():
        try:
            orig = QPixmap(str(p_path))
            if not orig.isNull():
                orig_w = orig.width()
                orig_h = orig.height()
                min_edge = min(orig_w, orig_h)
                if min_edge > 0:
                    crop_rect = QRect((orig_w - min_edge) // 2, (orig_h - min_edge) // 2, min_edge, min_edge)
                    sq = orig.copy(crop_rect)
                    scaled = sq.scaled(
                        render_size,
                        render_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    path = QPainterPath()
                    path.addEllipse(0.0, 0.0, float(render_size), float(render_size))
                    painter.setClipPath(path)
                    painter.drawPixmap(0, 0, scaled)
                    painter.setClipping(False)
                    loaded = True
        except Exception as e:
            logger.debug(f"Failed to load seamless avatar image {avatar_path}: {e}")
            loaded = False

    if not loaded:
        grad = QLinearGradient(0, 0, render_size, render_size)
        grad.setColorAt(0.0, QColor(14, 165, 233))
        grad.setColorAt(1.0, QColor(79, 70, 229))

        path = QPainterPath()
        path.addEllipse(0.0, 0.0, float(render_size), float(render_size))
        painter.fillPath(path, QBrush(grad))

        clean_name = re.sub(r'^[^\w\d]+', '', name.strip()) if name else ""
        letter = clean_name[0].upper() if clean_name else "N"
        font_size = max(10 * scale_factor, int(render_size * 0.42))
        font = QFont("Inter", font_size, QFont.Weight.Bold)
        font.setFamilies(["Inter", "Segoe UI", "sans-serif"])
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRectF(0, 0, float(render_size), float(render_size)), Qt.AlignmentFlag.AlignCenter, letter)

    # Мягкое сглаживание и растворение краев портрета в цвет подложки (бесшовный эффект глубины)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    radial_mask = QRadialGradient(center, center, radius)
    radial_mask.setColorAt(0.0, QColor(0, 0, 0, 255))
    radial_mask.setColorAt(0.82, QColor(0, 0, 0, 255))
    radial_mask.setColorAt(0.93, QColor(0, 0, 0, 170))
    radial_mask.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setBrush(QBrush(radial_mask))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRect(0, 0, render_size, render_size)

    painter.end()

    result_pixmap = QPixmap.fromImage(image)
    result_pixmap.setDevicePixelRatio(scale_factor)
    return result_pixmap


def get_cached_circular_avatar(avatar_path: Optional[Path], name: str, size: int = 44) -> QPixmap:
    """
    Возвращает бесшовный круглый аватар из дискового кэша.
    Если кэш устарел или отсутствует — рендерит через render_seamless_avatar и кэширует.
    """
    if not avatar_path or not Path(avatar_path).exists():
        return render_seamless_avatar(None, name=name, size=size)

    p = Path(avatar_path).resolve()
    try:
        mtime = int(p.stat().st_mtime)
        path_hash = hashlib.md5(str(p).encode("utf-8")).hexdigest()[:10]
        cache_file = GALLERY_THUMBS_DIR / f"{p.stem}_{path_hash}_{mtime}_{size}_feathered.png"

        if cache_file.exists():
            pix = QPixmap(str(cache_file))
            if not pix.isNull():
                pix.setDevicePixelRatio(3.0)
                return pix

        # Удаляем устаревшие файлы превью с прежним mtime перед генерацией нового
        for old_cache in GALLERY_THUMBS_DIR.glob(f"{p.stem}_{path_hash}_*_{size}*.png"):
            if old_cache != cache_file:
                old_cache.unlink(missing_ok=True)

        # Рендерим и сохраняем компактный кэш
        pix = render_seamless_avatar(p, name=name, size=size)
        if not pix.isNull():
            pix.save(str(cache_file), "PNG")
        return pix
    except Exception as e:
        logger.debug(f"Thumbnail cache fallback for {p.name}: {e}")
        return render_seamless_avatar(p, name=name, size=size)


class CharacterCardWidget(QFrame):
    """
    Soft Card виджет элемента списка персонажей:
    - Бесшовная аватарка 44x44 без рамок, растворяющаяся в фоне карточки
    - Имя персонажа и плашки (тип карточки, статус бюджета токенов)
    - Мягкая подсветка при наведении (hover)
    - Четкая визуальная фиксация выбранного героя
    """
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(
        self,
        card_name: str,
        avatar_pixmap: QPixmap,
        badge_text: str = "",
        type_badge: str = "",
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.setObjectName("CharacterCardWidget")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._is_selected = False
        self._name = card_name
        self._badge_text = badge_text.strip()
        self._type_badge = type_badge.strip()
        self._pixmap = avatar_pixmap

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self):
        self.setFixedHeight(62)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(10)

        # Avatar — бесшовный без рамок
        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setObjectName("CharacterCardAvatar")
        self.avatar_lbl.setFixedSize(44, 44)
        self.avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_lbl.setStyleSheet("border: none; background: transparent;")
        if not self._pixmap.isNull():
            self.avatar_lbl.setPixmap(self._pixmap)
        layout.addWidget(self.avatar_lbl)

        # Info Box
        info_box = QVBoxLayout()
        info_box.setContentsMargins(0, 2, 0, 2)
        info_box.setSpacing(4)

        self.name_lbl = QLabel(self._name, self)
        self.name_lbl.setObjectName("CharacterCardName")
        info_box.addWidget(self.name_lbl)

        self.badges_layout = QHBoxLayout()
        self.badges_layout.setContentsMargins(0, 0, 0, 0)
        self.badges_layout.setSpacing(6)

        self.type_lbl = QLabel(self._type_badge, self)
        self.type_lbl.setObjectName("CharacterCardTypeBadge")
        self.type_lbl.setVisible(bool(self._type_badge))
        self.badges_layout.addWidget(self.type_lbl)

        self.traffic_lbl = QLabel(self._badge_text, self)
        self.traffic_lbl.setObjectName("CharacterCardTrafficBadge")
        self.traffic_lbl.setVisible(bool(self._badge_text))
        self.badges_layout.addWidget(self.traffic_lbl)

        self.badges_layout.addStretch()
        info_box.addLayout(self.badges_layout)

        layout.addLayout(info_box, stretch=1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        p = self.parentWidget()
        while p and not isinstance(p, QListWidget):
            p = p.parentWidget()
        if p:
            p.customContextMenuRequested.emit(p.mapFromGlobal(event.globalPos()))
        else:
            super().contextMenuEvent(event)

    def set_selected(self, selected: bool):
        if self._is_selected != selected:
            self._is_selected = selected
            self._apply_style()

    def update_info(
        self,
        name: str,
        avatar_pixmap: Optional[QPixmap] = None,
        badge_text: Optional[str] = None,
        type_badge: Optional[str] = None
    ):
        self._name = name
        self.name_lbl.setText(name)
        if avatar_pixmap and not avatar_pixmap.isNull():
            self._pixmap = avatar_pixmap
            self.avatar_lbl.setPixmap(self._pixmap)
        if badge_text is not None:
            self._badge_text = badge_text.strip()
            self.traffic_lbl.setText(self._badge_text)
            self.traffic_lbl.setVisible(bool(self._badge_text))
        if type_badge is not None:
            self._type_badge = type_badge.strip()
            self.type_lbl.setText(self._type_badge)
            self.type_lbl.setVisible(bool(self._type_badge))

    def _apply_style(self):
        if self._is_selected:
            self.setStyleSheet("""
                QFrame#CharacterCardWidget {
                    background-color: rgba(0, 240, 255, 0.12);
                    border: 1px solid rgba(0, 240, 255, 0.70);
                    border-left: 3px solid #00F0FF;
                    border-radius: 10px;
                }
                QLabel#CharacterCardAvatar {
                    border: none;
                    background: transparent;
                }
                QLabel#CharacterCardName {
                    color: #00F0FF;
                    font-size: 13px;
                    font-weight: 700;
                    font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                    background: transparent;
                }
                QLabel#CharacterCardTypeBadge {
                    background-color: rgba(0, 240, 255, 0.18);
                    color: #38BDF8;
                    border: 1px solid rgba(0, 240, 255, 0.35);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: 600;
                }
                QLabel#CharacterCardTrafficBadge {
                    background-color: rgba(255, 255, 255, 0.08);
                    color: #E2E8F0;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: 600;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#CharacterCardWidget {
                    background-color: rgba(18, 24, 38, 0.70);
                    border: 1px solid rgba(255, 255, 255, 0.06);
                    border-left: 3px solid transparent;
                    border-radius: 10px;
                }
                QFrame#CharacterCardWidget:hover {
                    background-color: rgba(26, 36, 56, 0.85);
                    border: 1px solid rgba(0, 240, 255, 0.35);
                    border-left: 3px solid rgba(0, 240, 255, 0.45);
                }
                QLabel#CharacterCardAvatar {
                    border: none;
                    background: transparent;
                }
                QLabel#CharacterCardName {
                    color: #F1F5F9;
                    font-size: 13px;
                    font-weight: 600;
                    font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
                    background: transparent;
                }
                QLabel#CharacterCardTypeBadge {
                    background-color: rgba(0, 240, 255, 0.10);
                    color: #38BDF8;
                    border: 1px solid rgba(0, 240, 255, 0.20);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: 600;
                }
                QLabel#CharacterCardTrafficBadge {
                    background-color: rgba(255, 255, 255, 0.04);
                    color: #94A3B8;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: 600;
                }
            """)
