"""
Avatar and art pixmap rendering with rounded and circular masks and caching.
Provides seamless edge feathering via radial gradient alpha masks (matching Characters Gallery aesthetic).
"""

import re
import logging
import threading
from pathlib import Path
from typing import Optional, Union

from PyQt6.QtCore import Qt, QRect, QRectF
from qfluentwidgets import FluentIcon as FIF
from PyQt6.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QFont,
    QLinearGradient, QRadialGradient, QBrush, QPen, QImage
)

logger = logging.getLogger("NeonRoute.AvatarRenderer")

_IMAGE_CACHE_LOCK = threading.Lock()
_IMAGE_PREVIEW_CACHE: dict[tuple[str, int], QPixmap] = {}
_DEFAULT_AVATAR_IMAGE_CACHE: dict[tuple[str, int], QImage] = {}


def get_default_avatar_path(name: str = "") -> Optional[Path]:
    """
    Разрешает дефолтный путь к аватарке для соответствующего режима (Инженер, Ассистент, РП).
    Возвращает Path, если файл существует, иначе None.
    """
    try:
        from config import DEFAULT_ENGINEER_AVATAR, DEFAULT_ASSISTANT_AVATAR, DEFAULT_ROLEPLAY_AVATAR
    except Exception:
        return None

    n_lower = (name or "").lower()
    if "инженер" in n_lower or "engineer" in n_lower or "код" in n_lower:
        cand = DEFAULT_ENGINEER_AVATAR
    elif any(k in n_lower for k in ["элария", "ассистент", "assistant", "elaria"]):
        cand = DEFAULT_ASSISTANT_AVATAR
    else:
        # Ролевой чат / персонаж без собственной аватарки
        cand = DEFAULT_ROLEPLAY_AVATAR

    if cand and cand.exists():
        return cand
    return None


def is_default_avatar_path(p: Path) -> bool:
    """Проверяет, является ли путь одним из дефолтных аватаров режимов."""
    try:
        from config import DEFAULT_ENGINEER_AVATAR, DEFAULT_ASSISTANT_AVATAR, DEFAULT_ROLEPLAY_AVATAR
        res = p.resolve()
        return res in {DEFAULT_ENGINEER_AVATAR.resolve(), DEFAULT_ASSISTANT_AVATAR.resolve(), DEFAULT_ROLEPLAY_AVATAR.resolve()}
    except Exception:
        return False


def render_rounded_art_pixmap(source_pix: QPixmap, radius: float = 14.0) -> QPixmap:
    if source_pix.isNull():
        return source_pix

    w = source_pix.width()
    h = source_pix.height()
    target = QPixmap(w, h)
    target.fill(Qt.GlobalColor.transparent)

    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, float(w), float(h)), radius, radius)

    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source_pix)

    painter.setClipping(False)
    painter.setPen(QPen(QColor(255, 255, 255, 38), 1.0))
    painter.drawRoundedRect(QRectF(0.5, 0.5, float(w) - 1.0, float(h) - 1.0), radius, radius)
    painter.end()

    return target


def get_cached_art_pixmap(image_path: Path | str, target_size: int = 380, radius: float = 14.0) -> QPixmap:
    key = (str(image_path), target_size)
    with _IMAGE_CACHE_LOCK:
        if key in _IMAGE_PREVIEW_CACHE:
            return _IMAGE_PREVIEW_CACHE[key]

    p = Path(image_path)
    if not p.exists():
        return QPixmap()

    pix = QPixmap(str(p))
    if pix.isNull():
        return pix

    scaled = pix.scaled(
        target_size, target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )
    rounded = render_rounded_art_pixmap(scaled, radius=radius)
    with _IMAGE_CACHE_LOCK:
        if len(_IMAGE_PREVIEW_CACHE) > 60:
            _IMAGE_PREVIEW_CACHE.pop(next(iter(_IMAGE_PREVIEW_CACHE)))
        _IMAGE_PREVIEW_CACHE[key] = rounded
    return rounded


def render_circular_avatar(
    avatar_path: Optional[Path | str] = None,
    name: str = "Assistant",
    size: int = 64,
    is_user: bool = False,
    is_error: bool = False,
    image_path: Optional[Path | str] = None,
    text: Optional[str] = None
) -> QPixmap:
    """
    Рендерит круглый аватар без жестких неоновых рамок с мягким растворением внешнего
    контура в интерфейс (edge feathering via radial gradient alpha mask).
    Идентичен визуальному стилю галереи персонажей.
    """
    if image_path is not None:
        avatar_path = image_path
    if text is not None:
        name = text

    p_path = Path(avatar_path) if avatar_path else None
    if (not p_path or not p_path.exists()) and not is_user and not is_error:
        p_path = get_default_avatar_path(name)

    scale_factor = 3
    is_default = False
    cache_key = None
    if p_path and p_path.exists() and is_default_avatar_path(p_path):
        is_default = True
        cache_key = (str(p_path.resolve()), size)
        with _IMAGE_CACHE_LOCK:
            if cache_key in _DEFAULT_AVATAR_IMAGE_CACHE:
                target = QPixmap.fromImage(_DEFAULT_AVATAR_IMAGE_CACHE[cache_key])
                target.setDevicePixelRatio(scale_factor)
                return target

    render_size = size * scale_factor
    center = float(render_size) / 2.0
    radius = float(render_size) / 2.0

    image = QImage(render_size, render_size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if hasattr(QPainter.RenderHint, "LosslessImageRendering"):
        painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering, True)

    loaded = False

    if p_path and p_path.exists():
        try:
            orig_image = QImage(str(p_path))
            if not orig_image.isNull():
                orig_w = orig_image.width()
                orig_h = orig_image.height()
                min_edge = min(orig_w, orig_h)
                if min_edge > 0:
                    crop_rect = QRect((orig_w - min_edge) // 2, (orig_h - min_edge) // 2, min_edge, min_edge)
                    square_image = orig_image.copy(crop_rect)
                    scaled_image = square_image.scaled(
                        render_size,
                        render_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )

                    path = QPainterPath()
                    path.addEllipse(0.0, 0.0, float(render_size), float(render_size))
                    painter.setClipPath(path)
                    painter.drawImage(0, 0, scaled_image)
                    painter.setClipping(False)
                    loaded = True
        except Exception as e:
            logger.debug(f"Failed to load seamless avatar image: {e}")
            loaded = False

    if not loaded:
        n_lower = (name or "").lower()
        if is_error:
            grad_colors = (QColor("#DC2626"), QColor("#7F1D1D"))
            icon_def = FIF.INFO
        elif is_user:
            grad_colors = (QColor("#6366F1"), QColor("#3730A3"))
            icon_def = FIF.PEOPLE
        elif "инженер" in n_lower or "engineer" in n_lower or "код" in n_lower:
            grad_colors = (QColor("#D97706"), QColor("#78350F"))
            icon_def = FIF.DEVELOPER_TOOLS
        elif any(k in n_lower for k in ["элария", "ассистент", "assistant", "elaria"]):
            grad_colors = (QColor("#0EA5E9"), QColor("#312E81"))
            icon_def = FIF.ROBOT
        else:
            # Ролевой персонаж / компаньон
            grad_colors = (QColor("#C026D3"), QColor("#701A75"))
            icon_def = FIF.GAME

        grad = QLinearGradient(0, 0, render_size, render_size)
        grad.setColorAt(0.0, grad_colors[0])
        grad.setColorAt(1.0, grad_colors[1])
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(0.0, 0.0, float(render_size), float(render_size)))

        icon_sz = int(render_size * 0.50)
        ico = icon_def.icon(color=QColor(255, 255, 255, 240))
        ico_px = ico.pixmap(icon_sz, icon_sz)
        x = int((render_size - icon_sz) / 2.0)
        y = int((render_size - icon_sz) / 2.0)
        painter.drawPixmap(x, y, ico_px)

    # Мягкое бесшовное растворение контура портрета в цвет подложки (Seamless Edge Feathering)
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

    target = QPixmap.fromImage(image)
    target.setDevicePixelRatio(scale_factor)

    if is_default and cache_key and loaded:
        with _IMAGE_CACHE_LOCK:
            if len(_DEFAULT_AVATAR_IMAGE_CACHE) > 50:
                _DEFAULT_AVATAR_IMAGE_CACHE.pop(next(iter(_DEFAULT_AVATAR_IMAGE_CACHE)))
            _DEFAULT_AVATAR_IMAGE_CACHE[cache_key] = image.copy()

    return target
