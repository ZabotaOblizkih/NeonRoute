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
from PyQt6.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QFont,
    QLinearGradient, QRadialGradient, QBrush, QPen, QImage
)

logger = logging.getLogger("NeonRoute.AvatarRenderer")

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
    """
    Рендерит круглый аватар без жестких неоновых рамок с мягким растворением внешнего
    контура в интерфейс (edge feathering via radial gradient alpha mask).
    Идентичен визуальному стилю галереи персонажей.
    """
    if image_path is not None:
        avatar_path = image_path
    if text is not None:
        name = text

    scale_factor = 3
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
                    painter.setClipPath(path)
                    painter.drawPixmap(0, 0, scaled_pixmap)
                    painter.setClipping(False)
                    loaded = True
        except Exception as e:
            logger.debug(f"Failed to load seamless avatar image: {e}")
            loaded = False

    if not loaded:
        grad = QLinearGradient(0, 0, render_size, render_size)
        if is_error:
            grad.setColorAt(0.0, QColor(220, 38, 38))
            grad.setColorAt(1.0, QColor(153, 27, 27))
        elif is_user:
            grad.setColorAt(0.0, QColor(99, 102, 241))
            grad.setColorAt(1.0, QColor(67, 56, 202))
        else:
            grad.setColorAt(0.0, QColor(14, 165, 233))
            grad.setColorAt(1.0, QColor(79, 70, 229))

        path = QPainterPath()
        path.addEllipse(0.0, 0.0, float(render_size), float(render_size))
        painter.fillPath(path, QBrush(grad))

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
    return target
