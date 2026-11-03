"""
ui_context.py — Единый источник правды (SSOT) состояния интерфейса и диалогового цикла NeonRoute.
Инкапсулирует сессию, активный бабл стриминга и флаги занятости, устраняя дефект 'хрупкого базового класса'.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("NeonRoute.UIContext")

class UIContext:
    """Контейнер состояния интерфейса и диалогового цикла MainWindow."""

    def __init__(self, main_window: Optional[Any] = None):
        self._main_window = main_window
        self._current_session: Optional[Dict[str, Any]] = None
        self._turn_bubble: Optional[Any] = None
        self._is_busy: bool = False
        self._is_generating: bool = False

    @property
    def current_session(self) -> Optional[Dict[str, Any]]:
        return self._current_session

    @current_session.setter
    def current_session(self, value: Optional[Dict[str, Any]]) -> None:
        old_id = self._current_session.get("id") if isinstance(self._current_session, dict) else None
        new_id = value.get("id") if isinstance(value, dict) else None
        if old_id != new_id:
            logger.debug(f"[UIContext] Смена сессии: {old_id} -> {new_id}")
        self._current_session = value

    @property
    def turn_bubble(self) -> Optional[Any]:
        return self._turn_bubble

    @turn_bubble.setter
    def turn_bubble(self, widget: Optional[Any]) -> None:
        self._turn_bubble = widget

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @is_busy.setter
    def is_busy(self, val: bool) -> None:
        self._is_busy = bool(val)

    def begin_turn(self, bubble_widget: Any) -> None:
        """Атомарный старт хода генерации ответа."""
        self._turn_bubble = bubble_widget
        self._is_generating = True
        self._is_busy = True

    def finish_turn(self) -> None:
        """Атомарное завершение хода генерации ответа."""
        self._turn_bubble = None
        self._is_generating = False
        self._is_busy = False

    def get_session_id(self) -> Optional[str]:
        """Безопасное получение ID активной сессии."""
        if isinstance(self._current_session, dict):
            return self._current_session.get("id")
        return None

    def get_mode(self) -> Optional[str]:
        """Безопасное получение режима активной сессии."""
        if isinstance(self._current_session, dict):
            return self._current_session.get("mode")
        return None
