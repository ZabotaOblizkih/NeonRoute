"""
ui/common/toast_helper.py
=========================
Единый хелпер показа Fluent-плашек (InfoBar) в стиле Windows 11 Fluent Design.
Обеспечивает унификацию оформления, безопасное определение родительского виджета,
стандартное позиционирование и предсказуемые таймауты показа.
"""

import logging
from typing import Optional, Any
from PyQt6.QtWidgets import QWidget, QApplication
from qfluentwidgets import InfoBar, InfoBarPosition

logger = logging.getLogger("NeonRoute.ToastHelper")

# Стандартные таймауты показа (в миллисекундах)
DEFAULT_DURATIONS = {
    "success": 2500,
    "info": 3500,
    "warning": 4500,
    "error": 5500,
}

# Единая позиция по умолчанию для десктопа и диалогов
DEFAULT_POSITION = InfoBarPosition.TOP_RIGHT


def _resolve_parent(parent: Optional[QWidget] = None) -> Optional[QWidget]:
    """Безопасно определяет родительский виджет для плашки."""
    if parent is not None and isinstance(parent, QWidget):
        return parent
    app = QApplication.instance()
    if app:
        active = app.activeWindow()
        if active and isinstance(active, QWidget):
            return active
    return None


def show_toast(
    parent: Optional[QWidget],
    title: str,
    content: str = "",
    kind: str = "info",
    duration: Optional[int] = None,
    position: Optional[InfoBarPosition] = None,
    is_closable: bool = True,
    info_bar_cls: Any = None,
) -> Optional[Any]:
    """
    Отображает унифицированную всплывающую плашку InfoBar.

    :param parent: Родительский виджет (если None, берется activeWindow).
    :param title: Заголовок уведомления.
    :param content: Описание / подробный текст (опционально).
    :param kind: Тип плашки ('success', 'info', 'warning', 'error').
    :param duration: Таймаут в мс (если None, берется стандартный по типу).
    :param position: Позиция всплытия (по умолчанию TOP_RIGHT).
    :param is_closable: Возможность закрыть кликом на крестик.
    :param info_bar_cls: Кастомный класс InfoBar (для моков в тестах).
    """
    norm_kind = (kind or "info").strip().lower()
    bar_cls = info_bar_cls or InfoBar
    target_parent = _resolve_parent(parent)

    pos = position or DEFAULT_POSITION
    dur = duration if duration is not None else DEFAULT_DURATIONS.get(norm_kind, 3500)

    dispatch_map = {
        "success": getattr(bar_cls, "success", None),
        "info": getattr(bar_cls, "info", None),
        "warning": getattr(bar_cls, "warning", None),
        "error": getattr(bar_cls, "error", None),
    }

    method = dispatch_map.get(norm_kind, getattr(bar_cls, "info", None))
    if not method:
        logger.warning(f"[ToastHelper] Не найден метод InfoBar для типа '{norm_kind}'")
        return None

    try:
        kwargs = {
            "title": str(title or ""),
            "content": str(content or ""),
            "isClosable": bool(is_closable),
            "position": pos,
            "duration": dur,
        }
        if target_parent is not None:
            kwargs["parent"] = target_parent

        return method(**kwargs)
    except Exception as e:
        logger.debug(f"[ToastHelper] Ошибка отображения InfoBar.{norm_kind}: {e}")
        return None


def toast_success(
    parent: Optional[QWidget],
    title: str,
    content: str = "",
    duration: Optional[int] = None,
    position: Optional[InfoBarPosition] = None,
    is_closable: bool = True,
    info_bar_cls: Any = None,
) -> Optional[Any]:
    """Шорткат для успешного уведомления (зелёная плашка, 2500 мс)."""
    return show_toast(
        parent=parent,
        title=title,
        content=content,
        kind="success",
        duration=duration,
        position=position,
        is_closable=is_closable,
        info_bar_cls=info_bar_cls,
    )


def toast_info(
    parent: Optional[QWidget],
    title: str,
    content: str = "",
    duration: Optional[int] = None,
    position: Optional[InfoBarPosition] = None,
    is_closable: bool = True,
    info_bar_cls: Any = None,
) -> Optional[Any]:
    """Шорткат для информационного уведомления (синяя плашка, 3500 мс)."""
    return show_toast(
        parent=parent,
        title=title,
        content=content,
        kind="info",
        duration=duration,
        position=position,
        is_closable=is_closable,
        info_bar_cls=info_bar_cls,
    )


def toast_warning(
    parent: Optional[QWidget],
    title: str,
    content: str = "",
    duration: Optional[int] = None,
    position: Optional[InfoBarPosition] = None,
    is_closable: bool = True,
    info_bar_cls: Any = None,
) -> Optional[Any]:
    """Шорткат для предупреждения (жёлтая плашка, 4500 мс)."""
    return show_toast(
        parent=parent,
        title=title,
        content=content,
        kind="warning",
        duration=duration,
        position=position,
        is_closable=is_closable,
        info_bar_cls=info_bar_cls,
    )


def toast_error(
    parent: Optional[QWidget],
    title: str,
    content: str = "",
    duration: Optional[int] = None,
    position: Optional[InfoBarPosition] = None,
    is_closable: bool = True,
    info_bar_cls: Any = None,
) -> Optional[Any]:
    """Шорткат для ошибки (красная плашка, 5500 мс)."""
    return show_toast(
        parent=parent,
        title=title,
        content=content,
        kind="error",
        duration=duration,
        position=position,
        is_closable=is_closable,
        info_bar_cls=info_bar_cls,
    )
