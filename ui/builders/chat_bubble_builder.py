"""
Chat Bubble & Message Renderer (Mixin) for NeonRoute MainWindow.
Handles message bubble creation, formatting, rendering stored history,
and message interaction actions (translate, regenerate, delete).
"""

import os
import re
import html
import time
import json
import shutil
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Dict, Any, Union, Callable

from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QPixmap, QImage, QIcon, QColor, QFont, QCursor, QClipboard,
    QAction, QPainter, QPen, QBrush, QDesktopServices, QTextDocument, QTextCursor
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QTextEdit, QTextBrowser, QMenu, QApplication, QFileDialog,
    QScrollArea, QSizePolicy, QToolButton, QMessageBox
)

import config
from config import BASE_DIR, DATA_DIR
CHARACTERS_DIR = getattr(config, "CHARACTERS_DIR", Path("characters"))
ASSETS_DIR = getattr(config, "ASSETS_DIR", getattr(config, "BASE_DIR", Path(".")) / "assets")

from services.session_manager import SessionManager
from utils.config_manager import ConfigManager
from ui.dialogs.avatar_lightbox_dialog import AvatarLightboxDialog

# Импорты компонентов интерфейса
from ui.components.search_bar import SearchBarWidget
from ui.components.status_pill import StatusPill
from ui.components.input_box import ChatInputBox
from ui.components.typing_loader import TypingLoaderWidget
from ui.components.chat_list_item import ChatSessionItemWidget
from ui.components.chat_background import ChatBackgroundViewport
from ui.components.reminder_toast import ReminderToastWidget
from ui.components.chat_bubble import (
    ChatMessageWidget,
    render_circular_avatar,
    WorkspaceFileCardWidget,
    ConsoleOutputSpoilerWidget
)
from ui.components.assistant_chat_bubble import AssistantChatMessageWidget
from ui.components.rp.rp_chat_bubble import RPChatMessageWidget

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("NeonRoute.ChatBubbleBuilder")


class ChatBubbleBuilderMixin:
    """
    Миксин сборки и рендеринга сообщений для MainWindow.
    """

    def _load_previous_messages_chunk(self):
        """Бесшовно подгружает предыдущую пачку сообщений с сохранением скролла."""
        if not hasattr(self, "_all_session_messages") or not self._all_session_messages:
            return

        chunk_size = 25
        cur_offset = getattr(self, "_rendered_messages_offset", 0)
        if cur_offset <= 0:
            if self._btn_load_more_widget:
                self._btn_load_more_widget.deleteLater()
                self._btn_load_more_widget = None
            return

        new_offset = max(0, cur_offset - chunk_size)
        slice_to_insert = self._all_session_messages[new_offset:cur_offset]
        self._rendered_messages_offset = new_offset
        self._is_loading_more_history = True

        # Фиксируем текущий scroll bar через реальные свойства MainWindow
        v_bar = None
        if hasattr(self, "scroll_area") and self.scroll_area:
            v_bar = self.scroll_area.verticalScrollBar()
        elif hasattr(self, "chat_stream") and hasattr(self.chat_stream, "scroll_area") and self.chat_stream.scroll_area:
            v_bar = self.chat_stream.scroll_area.verticalScrollBar()

        old_val = v_bar.value() if v_bar else 0
        old_max = v_bar.maximum() if v_bar else 0

        # Временно блокируем перерисовку для исключения мерцания
        if hasattr(self, "chat_container"):
            self.chat_container.setUpdatesEnabled(False)

        try:
            # Находим индекс вставки (сразу после кнопки load_more)
            insert_idx = 1 if self._btn_load_more_widget else 0
            for msg in slice_to_insert:
                widget = self._render_stored_message(msg, target_index=insert_idx)
                insert_idx += 1

            if new_offset <= 0:
                if self._btn_load_more_widget:
                    self._btn_load_more_widget.deleteLater()
                    self._btn_load_more_widget = None
            else:
                if self._btn_load_more_widget:
                    self._btn_load_more_widget.setText(f"▲ Загрузить более ранние сообщения (осталось: {new_offset})")
        finally:
            if hasattr(self, "chat_container"):
                self.chat_container.setUpdatesEnabled(True)

            # Восстанавливаем позицию скролла, чтобы экран не прыгал
            if v_bar:
                from PyQt6.QtCore import QTimer
                def _restore_scroll():
                    new_max = v_bar.maximum()
                    diff = new_max - old_max
                    target_pos = old_val + diff
                    if hasattr(self, "_sync_scroll_to"):
                        self._sync_scroll_to(target_pos)
                    else:
                        v_bar.setValue(target_pos)
                    self._prev_scroll_max = new_max
                    self._is_loading_more_history = False
                QTimer.singleShot(20, _restore_scroll)

    def _render_stored_message(self, msg: dict, target_index: Optional[int] = None) -> ChatMessageWidget:
        sender = msg.get("sender", "NeonRoute")
        text = msg.get("text", "")
        # Если в истории остался пустой ответ ИИ, восстанавливаем видимый текст
        if not text and not msg.get("is_user") and not msg.get("image_path"):
            text = msg.get("translated_text") or "..." 
        is_user = msg.get("is_user", False)
        is_error = msg.get("is_error", False)
        img_p = Path(msg["image_path"]) if msg.get("image_path") else None
        if img_p and not img_p.exists():
            img_p = None
        audio_p = Path(msg["audio_path"]) if msg.get("audio_path") else None
        
        avatar_p = Path(msg["avatar_path"]) if msg.get("avatar_path") else None
        if not avatar_p or not avatar_p.exists():
            avatar_p = self._get_user_avatar_path() if is_user else self._get_current_avatar_path()

        trans_text = msg.get("translated_text")
        is_showing_trans = msg.get("is_showing_translation", False)
        timestamp = msg.get("timestamp")

        file_ps = []
        for p in msg.get("file_paths", []):
            try:
                pth = Path(p)
                if pth.exists():
                    file_ps.append(pth)
            except Exception:
                pass

        eff_mode = getattr(self, "active_mode", getattr(self, "current_mode", "assistant"))
        is_eng = (eff_mode in ("engineer", "engineering")) or (hasattr(self, "is_engineer_mode_active") and self.is_engineer_mode_active()) or (self.current_session.get("type") == "engineering" if getattr(self, "current_session", None) and isinstance(self.current_session, dict) else False)
        is_rp = (eff_mode == "roleplay") or bool(msg.get("is_roleplay", False))
        if is_rp:
            widget_cls = RPChatMessageWidget
        elif is_eng and not is_user:
            from ui.widgets.engineer_task_card import EngineerTaskCardWidget
            widget_cls = EngineerTaskCardWidget
        elif eff_mode == "assistant" and not is_user:
            widget_cls = AssistantChatMessageWidget
        else:
            widget_cls = ChatMessageWidget

        session_id_val = getattr(self, "current_session", {}).get("id") if hasattr(self, "current_session") and isinstance(self.current_session, dict) else None

        widget = widget_cls(
            sender=sender,
            text=text,
            is_user=is_user,
            is_error=is_error,
            image_path=img_p,
            file_paths=file_ps,
            audio_path=audio_p,
            avatar_path=avatar_p,
            translated_text=trans_text,
            is_showing_translation=is_showing_trans,
            font_size=getattr(self, "font_size", getattr(self, "chat_font_size", 13)),
            negative_prompt=msg.get("negative_prompt"),
            timestamp=timestamp,
            is_roleplay=is_rp,
            generation_params=msg.get("generation_params"),
            visual_intent=msg.get("visual_intent"),
            message_id=msg.get("id"),
            session_id=session_id_val,
            parent=getattr(self, "chat_scroll_content", getattr(self, "chat_container", None))
        )
        if msg.get("visual_intent"):
            widget.visual_intent = msg.get("visual_intent")
            if hasattr(widget, "set_scene_art_state"):
                cue_t = widget.visual_intent.get("prompt", "") if isinstance(widget.visual_intent, dict) else getattr(widget.visual_intent, "prompt", "")
                widget.set_scene_art_state(is_ready=True, cue_text=cue_t)
        if msg.get("knowledge_sources") and hasattr(widget, "set_knowledge_sources"):
            widget.set_knowledge_sources(msg.get("knowledge_sources"))
        if msg.get("thought") and hasattr(widget, "set_thought_data"):
            t = msg["thought"]
            widget.set_thought_data(
                text=t.get("content", ""),
                duration=float(t.get("duration", 0.0)),
                token_count=int(t.get("token_count", 0))
            )
        widget.tts_requested.connect(lambda txt, w=widget: self._enqueue_tts_task(txt, w))
        widget.translate_requested.connect(self._on_message_translate_requested)
        widget.regenerate_requested.connect(self._on_message_regenerate_requested)
        widget.delete_requested.connect(self._on_message_delete_requested)
        widget.scene_art_requested.connect(self._on_scene_art_requested)
        widget.run_script_requested.connect(self._execute_workspace_script)
        widget.choice_selected.connect(self._on_rpg_choice_selected)

        is_eng_canvas = (is_eng and not is_user) or widget.__class__.__name__ == "EngineerTaskCardWidget"
        align = Qt.AlignmentFlag(0) if is_eng_canvas else Qt.AlignmentFlag.AlignTop

        if target_index is not None:
            self.chat_layout.insertWidget(target_index, widget, alignment=align)
        else:
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget, alignment=align)
        return widget

    def _clear_chat_widgets(self):
        """Removes all chat bubble widgets from the view except bottom stretch."""
        if hasattr(self, "_remove_greeting_cycler_chip"):
            self._remove_greeting_cycler_chip()
        self._remove_typing_loader()
        self._rendered_messages_offset = 0
        self._all_session_messages = []
        self._btn_load_more_widget = None
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                if hasattr(item.widget(), "audio_widget") and item.widget().audio_widget:
                    item.widget().audio_widget.cleanup()
                item.widget().deleteLater()

    def _update_message_attachment(self, widget: ChatMessageWidget, file_path: Path, generation_params: Optional[dict] = None):
        """Appends generated document or image to the message in session storage."""
        if self.current_session and self.current_session.get("messages"):
            str_path = str(file_path)
            target_mid = getattr(widget, "message_id", None)
            target_msg = None
            messages = self.current_session["messages"]

            if target_mid:
                for m in reversed(messages):
                    if m.get("id") == target_mid:
                        target_msg = m
                        break

            if target_msg is None:
                for m in reversed(messages):
                    if m.get("text") == widget.original_text or m.get("text") == widget.raw_text:
                        target_msg = m
                        break

            if target_msg:
                if "file_paths" not in target_msg:
                    target_msg["file_paths"] = []
                if str_path not in target_msg["file_paths"]:
                    target_msg["file_paths"].append(str_path)
                if generation_params:
                    target_msg["generation_params"] = generation_params

            self._save_current_session()

    def _update_last_message_attachment(self, file_path: Path):
        """Appends generated document to the last message in session storage."""
        if self.current_session and self.current_session.get("messages"):
            last_msg = self.current_session["messages"][-1]
            if "file_paths" not in last_msg:
                last_msg["file_paths"] = []
            str_path = str(file_path)
            if str_path not in last_msg["file_paths"]:
                last_msg["file_paths"].append(str_path)
            self._save_current_session()

    def _on_message_translate_requested(self, text: str, widget: ChatMessageWidget):
        self._enqueue_translation_task(text, widget)

    def _update_message_translation_in_session(self, widget: ChatMessageWidget, translated_text: str):
        if not self.current_session or not self.current_session.get("messages"):
            return
        for m in self.current_session["messages"]:
            if m.get("text") == widget.original_text or m.get("text") == widget.raw_text:
                m["translated_text"] = translated_text
                m["is_showing_translation"] = widget.is_showing_translation
                break
        self._save_current_session()

    def _on_rpg_choice_selected(self, choice_text: str):
        if hasattr(self, "input_panel") and self.input_panel and hasattr(self.input_panel, "input_box"):
            self.input_panel.input_box.setPlainText(choice_text)
            if hasattr(self, "_on_send_clicked"):
                self._on_send_clicked()

    def _on_message_regenerate_requested(self, widget: ChatMessageWidget):
        if self.is_busy:
            self.statusBar().showMessage("Дождитесь завершения текущей задачи перед регенерацией.", 4000)
            return

        if not self.current_session or not self.current_session.get("messages"):
            return

        # Find preceding user prompt
        last_user_prompt = ""
        found_widget_idx = None
        target_mid = getattr(widget, "message_id", None)
        messages_list = self.current_session["messages"]

        if target_mid:
            for idx in range(len(messages_list) - 1, -1, -1):
                if messages_list[idx].get("id") == target_mid:
                    found_widget_idx = idx
                    break

        if found_widget_idx is None:
            for idx in range(len(messages_list) - 1, -1, -1):
                m = messages_list[idx]
                if m.get("is_user") == widget.is_user and m.get("text") in (widget.original_text, widget.raw_text):
                    found_widget_idx = idx
                    break

        if found_widget_idx is not None:
            for m in reversed(self.current_session["messages"][:found_widget_idx]):
                if m.get("is_user") and m.get("text"):
                    last_user_prompt = m["text"]
                    break

            # Remove this assistant message from session
            self.current_session["messages"].pop(found_widget_idx)
            if self.chat_history and self.chat_history[-1].get("role") == "assistant":
                self.chat_history.pop()

            self._save_current_session()

        self._remove_message_widget(widget)

        if last_user_prompt:
            self.statusBar().showMessage("Регенерация ответа...", 3000)
            self._route_and_enqueue(last_user_prompt)
        else:
            self.statusBar().showMessage("Предыдущий запрос пользователя не найден.", 4000)

    def _on_message_delete_requested(self, widget: ChatMessageWidget):
        if widget.is_user:
            confirm_msg = "Удалить ваше сообщение и связанный с ним ответ (откат диалога)?"
        else:
            confirm_msg = "Вы уверены, что хотите удалить это сообщение из диалога?"

        reply = QMessageBox.question(
            self,
            "Удаление сообщения",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 1. Update session messages & chat_history
        if self.current_session and self.current_session.get("messages"):
            idx_to_remove = None
            target_mid = getattr(widget, "message_id", None)
            messages_list = self.current_session["messages"]

            if target_mid:
                for idx in range(len(messages_list) - 1, -1, -1):
                    if messages_list[idx].get("id") == target_mid:
                        idx_to_remove = idx
                        break

            if idx_to_remove is None:
                for idx in range(len(messages_list) - 1, -1, -1):
                    m = messages_list[idx]
                    if m.get("is_user") == widget.is_user and m.get("text") in (widget.original_text, widget.raw_text):
                        idx_to_remove = idx
                        break

            if idx_to_remove is not None:
                if widget.is_user:
                    # Pop subsequent assistant message if present
                    if idx_to_remove + 1 < len(self.current_session["messages"]):
                        next_msg = self.current_session["messages"][idx_to_remove + 1]
                        if not next_msg.get("is_user"):
                            self.current_session["messages"].pop(idx_to_remove + 1)

                    # Pop user message
                    self.current_session["messages"].pop(idx_to_remove)
                else:
                    # Assistant message deletion
                    self.current_session["messages"].pop(idx_to_remove)

                # Детерминированная синхронизация chat_history из оставшихся сообщений сессии
                rebuilt_history = []
                for m in self.current_session.get("messages", []):
                    if m.get("is_error"):
                        continue
                    m_role = "user" if m.get("is_user") else "assistant"
                    m_text = (m.get("text") or "").strip()
                    if m_text and m_text != "🎨 Иллюстрация сцены готова:":
                        rebuilt_history.append({"role": m_role, "content": m_text})
                self.chat_history = rebuilt_history
                self.current_session["chat_history"] = list(self.chat_history)
                self._save_current_session()

        # 2. Update UI widgets
        if widget.is_user:
            w_idx = self.chat_layout.indexOf(widget)
            if w_idx >= 0:
                for idx in range(w_idx + 1, self.chat_layout.count()):
                    item = self.chat_layout.itemAt(idx)
                    if item and item.widget():
                        w = item.widget()
                        if isinstance(w, ChatMessageWidget):
                            if not w.is_user:
                                self._remove_message_widget(w)
                            break

        self._remove_message_widget(widget)
        self.statusBar().showMessage("Сообщение удалено.", 3000)

    def _remove_message_widget(self, widget: QWidget):
        if hasattr(widget, "audio_widget") and widget.audio_widget:
            widget.audio_widget.cleanup()
        self.chat_layout.removeWidget(widget)
        widget.deleteLater()

    def _append_message(
        self,
        sender: str,
        text: str,
        is_user: bool = False,
        is_error: bool = False,
        image_path: Optional[Path] = None,
        file_paths: Optional[List[Path]] = None,
        audio_path: Optional[Path] = None,
        avatar_path: Optional[Path] = None,
        translated_text: Optional[str] = None,
        is_showing_translation: bool = False,
        negative_prompt: Optional[str] = None,
        timestamp: Optional[str] = None,
        generation_params: Optional[dict] = None,
        is_roleplay: bool = False,
        visual_intent: Optional[Union[dict, Any]] = None,
        **kwargs
    ) -> ChatMessageWidget:
        # Resolve avatar automatically if not explicitly given
        resolved_avatar = avatar_path
        if not resolved_avatar:
            if is_user:
                resolved_avatar = self._get_user_avatar_path()
            else:
                resolved_avatar = self._get_current_avatar_path()

        eff_mode = getattr(self, "active_mode", getattr(self, "current_mode", "assistant"))
        is_eng = (eff_mode in ("engineer", "engineering")) or (hasattr(self, "is_engineer_mode_active") and self.is_engineer_mode_active())

        if eff_mode == "roleplay" or is_roleplay or (getattr(self, "current_mode", "") == "roleplay"):
            widget_cls = RPChatMessageWidget
        elif is_eng and not is_user:
            from ui.widgets.engineer_task_card import EngineerTaskCardWidget
            widget_cls = EngineerTaskCardWidget
        elif eff_mode == "assistant" and not is_user:
            widget_cls = AssistantChatMessageWidget
        else:
            widget_cls = ChatMessageWidget

        session_id_val = getattr(self, "current_session", {}).get("id") if hasattr(self, "current_session") and isinstance(self.current_session, dict) else None

        msg = widget_cls(
            sender=sender,
            text=text,
            is_user=is_user,
            is_error=is_error,
            image_path=image_path,
            file_paths=file_paths,
            audio_path=audio_path,
            avatar_path=resolved_avatar,
            translated_text=translated_text,
            is_showing_translation=is_showing_translation,
            font_size=getattr(self, "font_size", getattr(self, "chat_font_size", 13)),
            negative_prompt=negative_prompt,
            timestamp=timestamp,
            generation_params=generation_params,
            visual_intent=visual_intent,
            session_id=session_id_val,
            parent=getattr(self, "chat_scroll_content", getattr(self, "chat_container", None)),
            **kwargs
        )
        k_sources = kwargs.get("knowledge_sources") or (getattr(self, "_pending_knowledge_sources", None) if not is_user else None)
        if k_sources and hasattr(msg, "set_knowledge_sources"):
            msg.set_knowledge_sources(k_sources)
        msg.tts_requested.connect(lambda txt, w=msg: self._enqueue_tts_task(txt, w))
        msg.translate_requested.connect(self._on_message_translate_requested)
        msg.regenerate_requested.connect(self._on_message_regenerate_requested)
        msg.delete_requested.connect(self._on_message_delete_requested)
        msg.scene_art_requested.connect(self._on_scene_art_requested)
        msg.run_script_requested.connect(self._execute_workspace_script)
        msg.choice_selected.connect(self._on_rpg_choice_selected)

        is_eng_canvas = (is_eng and not is_user) or msg.__class__.__name__ == "EngineerTaskCardWidget"
        align = Qt.AlignmentFlag(0) if is_eng_canvas else Qt.AlignmentFlag.AlignTop

        # Insert before typing_loader if present, else before bottom stretch
        if self.typing_loader is not None:
            idx = self.chat_layout.indexOf(self.typing_loader)
            if idx >= 0:
                self.chat_layout.insertWidget(idx, msg, alignment=align)
            else:
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg, alignment=align)
        else:
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg, alignment=align)

        # Record in active session and auto-save to history
        if self.current_session is not None and not self._is_switching_session:
            msg_record = {
                "id": getattr(msg, "message_id", None),
                "sender": sender,
                "text": text,
                "is_user": is_user,
                "is_error": is_error,
                "image_path": str(image_path) if image_path else None,
                "file_paths": [str(p) for p in file_paths] if file_paths else [],
                "audio_path": str(audio_path) if audio_path else None,
                "avatar_path": str(resolved_avatar) if resolved_avatar else None,
                "translated_text": translated_text,
                "is_showing_translation": is_showing_translation,
                "negative_prompt": negative_prompt,
                "generation_params": generation_params,
                "timestamp": msg.timestamp,
            }
            if "messages" not in self.current_session:
                self.current_session["messages"] = []
            self.current_session["messages"].append(msg_record)
            self._save_current_session()

        self._scroll_to_bottom()
        return msg

    def _clear_chat(self):
        """Redirects to new session creation."""
        self._on_new_chat_clicked()

    def _get_chat_message_widgets(self) -> List[Any]:
        widgets = []
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if isinstance(w, ChatMessageWidget) or w.__class__.__name__ == "EngineerTaskCardWidget":
                    widgets.append(w)
        return widgets



# Псевдоним для совместимости
ChatBubbleBuilder = ChatBubbleBuilderMixin
