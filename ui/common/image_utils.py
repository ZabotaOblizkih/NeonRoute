"""
ui/common/image_utils.py
========================
High-performance, aliasing-free wallpaper processing and High-DPI buffer utilities
for NeonRoute.

Provides:
- load_display_master_pixmap: loads image, downscales 4K+/8K masters via PIL Lanczos
  to eliminate aliasing at source and prevent excessive memory use.
- progressive_scale_pixmap: progressive half-step downsampling for QPixmap to eliminate
  Nyquist aliasing staircases when downscaling by > 2x.
- create_hidpi_background_buffer: devicePixelRatio-aware buffer creation with 1:1 hardware
  pixel mapping, centering, darkening tint, and smooth transform hints.
"""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPainter, QPixmap

logger = logging.getLogger("NeonRoute.ImageUtils")


def load_display_master_pixmap(file_path: str, max_dimension: int = 3840) -> Optional[QPixmap]:
    """
    Loads an image file as a display master QPixmap.
    If the image exceeds max_dimension (e.g. 4K 3840px), downscales it using PIL
    with high-fidelity Lanczos resampling to prevent memory exhaustion and eliminate
    aliasing at the root source.
    """
    path = Path(file_path)
    if not path.is_file():
        return None

    try:
        with Image.open(path) as img:
            w, h = img.size
            if w > max_dimension or h > max_dimension:
                ratio = min(max_dimension / w, max_dimension / h)
                new_w = max(1, int(round(w * ratio)))
                new_h = max(1, int(round(h * ratio)))
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

                buf = BytesIO()
                resized_img.save(buf, format="PNG")
                pixmap = QPixmap()
                pixmap.loadFromData(buf.getvalue())
                if not pixmap.isNull():
                    return pixmap
    except Exception as e:
        logger.warning(f"PIL display master load failed for {file_path}: {e}, falling back to QPixmap")

    pixmap = QPixmap(str(path))
    return pixmap if not pixmap.isNull() else None


def progressive_scale_pixmap(
    pixmap: QPixmap,
    target_size: QSize,
    aspect_mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatioByExpanding
) -> QPixmap:
    """
    Scales a QPixmap to target_size using progressive half-step downsampling
    to prevent pixel skipping, moiré, and aliasing staircases when downscaling > 2x.
    """
    if not pixmap or pixmap.isNull() or target_size.isEmpty():
        return pixmap

    target_w = target_size.width()
    target_h = target_size.height()

    if pixmap.width() > target_w * 2 or pixmap.height() > target_h * 2:
        img = pixmap.toImage()
        curr_w = img.width()
        curr_h = img.height()

        # Step down in halves until within 2x of target
        while curr_w > target_w * 2 and curr_h > target_h * 2:
            curr_w = max(target_w * 2, curr_w // 2)
            curr_h = max(target_h * 2, curr_h // 2)
            img = img.scaled(
                curr_w,
                curr_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

        img = img.scaled(
            target_size,
            aspect_mode,
            Qt.TransformationMode.SmoothTransformation
        )
        return QPixmap.fromImage(img)
    else:
        return pixmap.scaled(
            target_size,
            aspect_mode,
            Qt.TransformationMode.SmoothTransformation
        )


def create_hidpi_background_buffer(
    pixmap: QPixmap,
    logical_size: QSize,
    dpr: float = 1.0,
    dim_alpha: int = 140,
    bg_tint: QColor = QColor(0, 0, 0),
    aspect_mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatioByExpanding
) -> Optional[QPixmap]:
    """
    Creates a pre-rendered, pre-dimmed High-DPI QPixmap buffer matching physical
    display pixels with 1:1 hardware mapping. Eliminates Windows DPI stretch aliasing.
    """
    if not pixmap or pixmap.isNull() or logical_size.isEmpty():
        return None

    dpr = max(1.0, float(dpr))
    phys_w = max(1, int(round(logical_size.width() * dpr)))
    phys_h = max(1, int(round(logical_size.height() * dpr)))
    phys_size = QSize(phys_w, phys_h)

    scaled = progressive_scale_pixmap(pixmap, phys_size, aspect_mode)
    if scaled.isNull():
        return None

    buffer = QPixmap(phys_w, phys_h)
    buffer.fill(Qt.GlobalColor.transparent)

    p = QPainter(buffer)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    x = (phys_w - scaled.width()) // 2
    y = (phys_h - scaled.height()) // 2
    p.drawPixmap(x, y, scaled)

    if dim_alpha > 0:
        tint = QColor(bg_tint)
        tint.setAlpha(max(0, min(255, dim_alpha)))
        p.fillRect(buffer.rect(), tint)
    p.end()

    buffer.setDevicePixelRatio(dpr)
    return buffer
