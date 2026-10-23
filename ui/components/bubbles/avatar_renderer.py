"""
Avatar and art pixmap rendering with rounded and circular masks and caching.
"""

import re
import logging
import threading
from pathlib import Path
from typing import Optional, Union

from PyQt6.QtCore import Qt, QRect, QRectF
from PyQt6.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QFont,
    QLinearGradient, QBrush, QPen
)

logger = logging.getLogger("NeonRoute.AvatarRenderer")

# Глобальный LRU-кэш для скругленных превью изображений
_IMAGE_CACHE_LOCK = threading.Lock()
_IMAGE_PREVIEW_CACHE: dict[tuple[str, int], QPixmap] = {}


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
    if image_path is not None:
        avatar_path = image_path
    if text is not None:
        name = text

    scale_factor = 3
    render_size = size * scale_factor

    target = QPixmap(render_size, render_size)
    target.fill(Qt.GlobalColor.transparent)

    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if hasattr(QPainter.RenderHint, "LosslessImageRendering"):
        painter.setRenderHint(QPainter.RenderHint.LosslessImageRendering, True)

    p_path = Path(avatar_path) if avatar_path else None
    loaded = False
    if p_path and p_path.exists():
        try:
            orig_pixmap = QPixmap(str(p_path))
            if not orig_pixmap.isNull():
                orig_w = orig_pixmap.width()
                orig_h = orig_pixmap.height()
                min_edge = min(orig_w, orig_h)
                if min_edge > 0:
                    crop_rect = QRect((orig_w - min_edge) // 2, (orig_h - min_edge) // 2, min_edge, min_edge)
                    square_pixmap = orig_pixmap.copy(crop_rect)
                    scaled_pixmap = square_pixmap.scaled(
                        render_size,
                        render_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )

                    path = QPainterPath()
                    path.addEllipse(0.0, 0.0, float(render_size), float(render_size))
                    painter.fillPath(path, QBrush(scaled_pixmap))

                    border_width = 2.0 * scale_factor
                    half_border = border_width / 2.0
                    rim_rect = QRectF(half_border, half_border, float(render_size) - border_width, float(render_size) - border_width)
                    if is_error:
                        rim_pen = QPen(QColor(239, 68, 68, 180), border_width)
                    elif is_user:
                        rim_pen = QPen(QColor(255, 255, 255, 80), border_width)
                    else:
                        rim_pen = QPen(QColor(0, 240, 255, 140), border_width)

                    painter.setPen(rim_pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(rim_rect)
                    loaded = True
        except Exception as e:
            logger.debug(f"Failed to load avatar image: {e}")
            loaded = False

    if not loaded:
        grad = QLinearGradient(0, 0, render_size, render_size)
        if is_error:
            grad.setColorAt(0.0, QColor(220, 38, 38))
            grad.setColorAt(1.0, QColor(153, 27, 27))
            rim_col = QColor(239, 68, 68, 180)
        elif is_user:
            grad.setColorAt(0.0, QColor(99, 102, 241))
            grad.setColorAt(1.0, QColor(67, 56, 202))
            rim_col = QColor(255, 255, 255, 80)
        else:
            grad.setColorAt(0.0, QColor(14, 165, 233))
            grad.setColorAt(1.0, QColor(79, 70, 229))
            rim_col = QColor(0, 240, 255, 140)

        border_width = 2.0 * scale_factor
        half_border = border_width / 2.0
        rim_rect = QRectF(half_border, half_border, float(render_size) - border_width, float(render_size) - border_width)

        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(rim_col, border_width))
        painter.drawEllipse(rim_rect)

        clean_name = re.sub(r'^[^\w\d]+', '', name.strip()) if name else ""
        if not clean_name:
            letter = "U" if is_user else ("!" if is_error else "N")
        else:
            letter = clean_name[0].upper()

        font_size = max(10 * scale_factor, int(render_size * 0.42))
        font = QFont("Inter", font_size, QFont.Weight.Bold)
        font.setFamilies(["Inter", "Segoe UI", "sans-serif"])
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRectF(0, 0, float(render_size), float(render_size)), Qt.AlignmentFlag.AlignCenter, letter)

    painter.end()
    target.setDevicePixelRatio(scale_factor)
    return target
