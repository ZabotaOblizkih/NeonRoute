"""
ui/common/palette_utils.py
===========================
Утилиты динамической адаптивной цветовой палитры (Material You / Fluent Tint)
для окон и диалогов NeonRoute.
Извлекает доминирующий оттенок (Hue) из активных обоев и генерирует
гармоничную темную сланцевую палитру для окон, карточек, кнопок и терминалов.
"""

from typing import Optional, Dict
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPixmap


def extract_adaptive_palette(pixmap: Optional[QPixmap] = None) -> Dict[str, str]:
    """
    Извлекает преобладающий цветовой тон (Hue) из обоев рабочего пространства
    и генерирует гармоничную палитру для окна, карточек, кнопок и терминала.
    """
    DEFAULT_PALETTE = {
        "bg": "#161B26",
        "nav": "#1C2333",
        "card": "#21293B",
        "card_hover": "#273144",
        "terminal_bg": "#0B0E14",
        "terminal_border": "#1E283D",
        "border": "#2D3748",
        "card_border": "#334155",
        "btn": "#2D3748",
        "btn_border": "#475569",
        "accent": "#6366F1",
        "btn_hover": "#6366F1",
        "btn_hover_text": "#FFFFFF",
        "group_title": "#818CF8",
        "text_primary": "#F8FAFC",
        "text_secondary": "#94A3B8",
    }
    if not pixmap or pixmap.isNull():
        return DEFAULT_PALETTE

    try:
        img = pixmap.toImage().scaled(
            QSize(12, 12),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        r_tot, g_tot, b_tot = 0, 0, 0
        cnt = max(1, img.width() * img.height())
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                r_tot += c.red()
                g_tot += c.green()
                b_tot += c.blue()

        avg_c = QColor(r_tot // cnt, g_tot // cnt, b_tot // cnt)
        h, s, v, _ = avg_c.getHsv()

        if s < 25 or v < 15:
            # Монохромные, слишком темные или серые обои
            return DEFAULT_PALETTE

        # Гармоничные уровни насыщенности и яркости для темного стекла / сланца
        bg = QColor.fromHsv(h, min(50, max(18, s // 4)), 23).name()
        nav = QColor.fromHsv(h, min(55, max(22, s // 3)), 29).name()
        card = QColor.fromHsv(h, min(50, max(20, s // 3)), 34).name()
        card_hover = QColor.fromHsv(h, min(55, max(25, s // 3)), 40).name()
        terminal_bg = QColor.fromHsv(h, min(40, max(14, s // 5)), 12).name()
        terminal_border = QColor.fromHsv(h, min(35, max(18, s // 4)), 28).name()
        border = QColor.fromHsv(h, min(40, max(15, s // 4)), 46).name()
        card_border = QColor.fromHsv(h, min(40, max(20, s // 4)), 52).name()
        btn = QColor.fromHsv(h, min(45, max(22, s // 4)), 44).name()
        btn_border = QColor.fromHsv(h, min(40, max(25, s // 4)), 62).name()
        accent = QColor.fromHsv(h, min(240, max(160, s)), 245).name()
        group_title = QColor.fromHsv(h, min(200, max(140, s)), 230).name()

        return {
            "bg": bg,
            "nav": nav,
            "card": card,
            "card_hover": card_hover,
            "terminal_bg": terminal_bg,
            "terminal_border": terminal_border,
            "border": border,
            "card_border": card_border,
            "btn": btn,
            "btn_border": btn_border,
            "accent": accent,
            "btn_hover": accent,
            "btn_hover_text": "#FFFFFF",
            "group_title": group_title,
            "text_primary": "#F8FAFC",
            "text_secondary": "#94A3B8",
        }
    except Exception:
        return DEFAULT_PALETTE
