"""
Session & History Controller (Mixin) for NeonRoute MainWindow.
Handles session creation, switching, persistence, sidebar list updates,
and chat message serialization.
"""

import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtWidgets import (
    QWidget, QMessageBox, QInputDialog, QListWidgetItem,
    QMenu, QApplication, QFileDialog
)
from PyQt6.QtGui import QIcon, QAction
from services.session_manager import SessionManager


if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("NeonRoute.SessionController")


class SessionControllerMixin:
    """
    Миксин управления сессиями и историей диалогов для MainWindow.
    """

    def _on_new_chat_clicked(self):
        """Handler for '+ Новый' button in sidebar."""
        self._create_new_session(
            mode=self.current_mode,
            character_card=self.active_character if self.current_mode == "roleplay" else None,
            title=None
        )
        self.statusBar().showMessage("Создан новый диалог.", 4000)

    def _save_current_session(self, trigger_memory: bool = False):
        """Persists active session to disk."""
        if not self.current_session:
            return
        self.current_session["chat_history"] = list(self.chat_history)
        self.current_session["mode"] = self.current_mode
        if hasattr(self, "is_engineer_mode_active") and self.is_engineer_mode_active():
            self.current_session["type"] = "engineering"
        else:
            self.current_session.pop("type", None)
        SessionManager.save_session(self.current_session, trigger_memory=trigger_memory)

    def _on_rename_session_requested(self, session_id: str):
        """Opens rename dialog for the session."""
        current_title = "Диалог"
        if self.current_session and self.current_session.get("id") == session_id:
            current_title = self.current_session.get("title", current_title)
        elif session_id in self._session_items:
            _, w = self._session_items[session_id]
            current_title = w.title_lbl.text()

        new_title, ok = QInputDialog.getText(
            self,
            "Переименование диалога",
            "Введите новое название диалога:",
            text=current_title
        )
        if ok and new_title.strip():
            clean_title = new_title.strip()
            SessionManager.rename_session(session_id, clean_title)
            if self.current_session and self.current_session.get("id") == session_id:
                self.current_session["title"] = clean_title
            if session_id in self._session_items:
                _, w = self._session_items[session_id]
                w.update_title(clean_title)
            if hasattr(self, "workspace_inspector"):
                self.workspace_inspector.update_title(clean_title)
            self.statusBar().showMessage(f"Диалог переименован: '{clean_title}'", 3000)

    def _on_delete_session_requested(self, session_id: str):
        """Deletes a session from history and removes from sidebar with WinError 32 guard."""
        reply = QMessageBox.question(
            self,
            "Удаление диалога",
            "Вы действительно хотите удалить этот диалог из истории?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 1. Если удаляется активная сессия, останавливаем активные задачи очереди и переключаем UI
        is_active_session = bool(self.current_session and self.current_session.get("id") == session_id)
        if is_active_session:
            if getattr(self, "is_busy", False):
                self.task_queue.abort_current()

            # Освобождаем виджеты текущего чата и дескрипторы аудио/изображений
            self._clear_chat_widgets()

            next_id = None
            # Сначала ищем сессию того же режима (self.current_mode)
            target_m = getattr(self, "current_mode", "assistant")
            for i in range(self.chat_list_widget.count()):
                cand_item = self.chat_list_widget.item(i)
                cid = cand_item.data(Qt.ItemDataRole.UserRole)
                if cid and cid != session_id:
                    cand_w = self.chat_list_widget.itemWidget(cand_item)
                    cand_mode = getattr(cand_w, "mode", None) if cand_w else None
                    if cand_mode == target_m:
                        next_id = cid
                        break

            # Если сессии того же режима нет, ищем любую доступную
            if not next_id:
                for i in range(self.chat_list_widget.count()):
                    cand_item = self.chat_list_widget.item(i)
                    cid = cand_item.data(Qt.ItemDataRole.UserRole)
                    if cid and cid != session_id:
                        next_id = cid
                        break

            self.current_session = None
            if next_id:
                self._switch_to_session(next_id)
            else:
                self._create_new_session(self.current_mode or "assistant")

        # 2. Удаляем сессию из списка UI
        if session_id in self._session_items:
            item, _ = self._session_items.pop(session_id)
            row = self.chat_list_widget.row(item)
            if row >= 0:
                self.chat_list_widget.takeItem(row)

        # 3. Полное удаление истории чата и папки outputs/sessions/<session_id>/
        SessionManager.delete_session(session_id)

