import logging
from typing import Optional, Dict, Any
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger("NeonRoute.HeaderController")


class HeaderStateController(QObject):
    """
    Управляет состоянием шапки чата (конечный автомат: имя, статус, неоновый цвет, аватар).
    Изолирует логику представления от главного окна MainWindow.
    """
    # title, status_text, status_color_hex, avatar_path
    state_updated = pyqtSignal(str, str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_mode = "assistant"
        self._active_character: Optional[Any] = None
        self._current_session: Optional[Dict[str, Any]] = None
        self._active_task_status: Optional[str] = None
        self._active_task_color: str = "#00F0FF"

    def sync_context(
        self,
        mode: str,
        character: Optional[Any],
        session: Optional[Dict[str, Any]]
    ):
        """Синхронизирует базовый контекст диалога (режим, персонаж, сессия)."""
        self._current_mode = mode
        self._active_character = character
        self._current_session = session
        self.refresh()

    def set_forge_progress(self, stage_name: str, percent: int):
        """Обновляет статус выполнения этапов SD WebUI Forge."""
        if percent > 0:
            self._active_task_status = f"{stage_name}: {percent}%"
        else:
            self._active_task_status = f"{stage_name}..."
        self._active_task_color = "#00F0FF"
        self.refresh()

    def set_task_state(self, task_type: str, action_text: str):
        """Устанавливает статус выполнения фоновой задачи."""
        self._active_task_status = f"{action_text}..."
        if task_type in ("scene_art", "image"):
            self._active_task_color = "#00F0FF"
        elif task_type == "llm":
            self._active_task_color = "#00F0FF"
        elif task_type == "file":
            self._active_task_color = "#FBBF24"
        elif task_type == "translation":
            self._active_task_color = "#38BDF8"
        else:
            self._active_task_color = "#00F0FF"
        self.refresh()

    def clear_active_task(self):
        """Сбрасывает состояние активной задачи при завершении или отмене."""
        self._active_task_status = None
        self.refresh()

    def refresh(self):
        """Вычисляет результирующее состояние и передает его в UI."""
        if self._active_task_status:
            title = self._get_base_title()
            avatar_path = self._get_avatar_path()
            self.state_updated.emit(title, self._active_task_status, self._active_task_color, avatar_path)
            return

        if self._current_mode == "roleplay":
            if self._active_character and getattr(self._active_character, "name", None):
                char_name = self._active_character.name
                user_name = "Игрок"
                if self._current_session:
                    from services.session_manager import SessionManager
                    sess_persona_id = self._current_session.get("user_persona_id")
                    persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)
                    user_name = persona.get("name", "Игрок")

                status = f"🎭 {char_name} • Вы: {user_name}"
                color = "#A78BFA"
                # ВАЖНО: берем аватар через _get_avatar_path(), который учитывает сессионный оверрайд!
                avatar_path = self._get_avatar_path()
                self.state_updated.emit(char_name, status, color, avatar_path)
            else:
                self.state_updated.emit(
                    "Ролевой чат (Персонаж не выбран)",
                    "Загрузите карточку (.json)",
                    "#94A3B8",
                    None
                )
        else:
            # Ассистент: отображает аватар текущей сессии (или None для стандартного бейджа)
            self.state_updated.emit(
                "Элария",
                "Элария • В сети",
                "#38BDF8",
                self._get_avatar_path()
            )

    def _get_base_title(self) -> str:
        if self._current_mode == "roleplay" and self._active_character:
            return getattr(self._active_character, "name", "Персонаж")
        return "Элария"

    def _get_avatar_path(self) -> Optional[Any]:
        if self._current_mode == "roleplay":
            # 1. Индивидуальный аватар конкретного чата
            if self._current_session and self._current_session.get("character_data"):
                sess_av = self._current_session["character_data"].get("avatar_path")
                if sess_av and Path(sess_av).exists():
                    return Path(sess_av)
            # 2. Исходный аватар карточки персонажа из галереи
            if self._active_character and getattr(self._active_character, "avatar_path", None):
                p = Path(self._active_character.avatar_path)
                if p.exists():
                    return p
            return None

        # Режим ассистента: СТРОГО аватар текущей сессии. Если не задан — None (чистый бейдж)
        if self._current_session:
            sess_av = self._current_session.get("assistant_avatar_path")
            if sess_av and Path(sess_av).exists():
                return Path(sess_av)
        return None
