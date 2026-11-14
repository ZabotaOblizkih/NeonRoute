"""
Session & History Controller (Mixin) for NeonRoute MainWindow.
Handles session creation, switching, persistence, sidebar list updates,
and chat message serialization.
"""

import os
import random
import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtWidgets import (
    QWidget, QMessageBox, QInputDialog, QListWidgetItem,
    QMenu, QApplication, QFileDialog, QHBoxLayout, QPushButton,
    QLabel, QSizePolicy
)
from PyQt6.QtGui import QIcon, QAction, QCursor
from services.session_manager import SessionManager
from services.tavern_parser import CharacterCard
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine
from ui.components.chat_list_item import ChatSessionItemWidget
from ui.components.chat_bubble import ChatMessageWidget


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


    def _load_saved_sessions(self):
        """Loads all sessions from history directory or creates a fresh one."""
        # Явно отключаем сигналы и удаляем существующие виджеты перед очисткой:
        for item, widget in self._session_items.values():
            try:
                widget.delete_requested.disconnect()
                widget.rename_requested.disconnect()
                if hasattr(widget, "change_persona_requested"):
                    widget.change_persona_requested.disconnect()
            except (TypeError, RuntimeError):
                pass
            widget.deleteLater()

        self.chat_list_widget.clear()
        self._session_items.clear()

        sessions = SessionManager.list_sessions()

        for s in sessions:
            sess_id = s["id"]
            title = s.get("title", "Диалог")
            mode = s.get("mode", "assistant")
            char_data = s.get("character_data")
            char_name = char_data.get("name") if char_data else None
            # Извлекаем аватар СТРОГО из данных конкретной сессии:
            avatar_path = (
                (char_data.get("avatar_path") if char_data else None)
                or s.get("assistant_avatar_path")
            )

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, sess_id)
            w = ChatSessionItemWidget(sess_id, title, mode, char_name, avatar_path=avatar_path, session_type=s.get("type"), parent=self)
            self._configure_session_item_widget(w, title)
            w.delete_requested.connect(self._on_delete_session_requested)
            w.rename_requested.connect(self._on_rename_session_requested)
            w.change_persona_requested.connect(self._on_change_persona_requested)
            item.setSizeHint(w.sizeHint())

            self.chat_list_widget.addItem(item)
            self.chat_list_widget.setItemWidget(item, w)
            self._session_items[sess_id] = (item, w)

        if sessions:
            # Switch to most recent session
            self._switch_to_session(sessions[0]["id"])
        else:
            # Fresh initial session
            self._create_new_session("assistant", is_initial=True)

    def create_chat(self, mode: str = "assistant"):
        """Creates a new chat session for roleplay, assistant, or engineer mode."""
        self._turn_bubble = None
        self._current_streaming_widget = None

        if mode in ("rp", "roleplay"):
            self._create_new_session(
                mode="roleplay",
                character_card=self.active_character,
                title=None
            )
        elif mode in ("engineer", "engineering"):
            self._create_new_session(
                mode="engineer",
                character_card=None,
                title="🛠️ Инженерная задача"
            )
            if self.current_session:
                self.current_session["type"] = "engineering"
                self.current_session["mode"] = "engineer"
                self._save_current_session()
        else:
            self._create_new_session(
                mode="assistant",
                character_card=None,
                title=None
            )

        if self.current_session:
            sess_id = self.current_session.get("id")
            if sess_id:
                self.on_active_chat_changed(sess_id)

    def _create_new_session(
        self,
        mode: str = "assistant",
        character_card: Optional[CharacterCard] = None,
        title: Optional[str] = None,
        is_initial: bool = False
    ):
        """Creates and activates a new chat session with locked/unlocked model tracking."""
        self._turn_bubble = None
        self._current_streaming_widget = None
        if self.current_session and not is_initial:
            self._save_current_session()

        mode_str = str(mode).lower()
        if mode_str in ("rp", "roleplay"):
            char_model = None
            if isinstance(character_card, dict):
                char_model = character_card.get("character_model") or character_card.get("model_id")
            elif character_card is not None:
                char_model = getattr(character_card, "character_model", None) or getattr(character_card, "model_id", None)

            current_model = (
                char_model
                or self.app_config.get("rp_model_name")
                or self.app_config.get("active_model", "")
            )
        elif mode_str in ("engineer", "engineering"):
            current_model = (
                self.app_config.get("engineer_model_name")
                or self.app_config.get("engineer_model")
                or self.app_config.get("active_model", "")
            )
        else:
            current_model = (
                self.app_config.get("assistant_model_name")
                or self.app_config.get("active_model", "")
            )

        if current_model and current_model.startswith("default"):
            current_model = ""

        new_sess = SessionManager.create_session(
            mode=mode,
            character_card=character_card,
            title=title,
            model_id=current_model,
            is_model_locked=False
        )
        if mode in ("engineer", "engineering") or title == "🛠️ Инженерная задача":
            new_sess["type"] = "engineering"
            new_sess["mode"] = "engineer"

        # 1. ЗАЩИТА: Сначала гарантированно сохраняем старый диалог на диск
        if getattr(self, "current_session", None):
            self._save_current_session()

        # 2. Мгновенно зачищаем старый контекст и виджеты ленты
        self.chat_history.clear()
        self._clear_chat_widgets()

        # 3. Переход координатора в целевой режим с уже чистым лейаутом
        from services.mode_coordinator import AppMode
        target_app_mode = AppMode.from_string(mode, is_engineer_checked=(mode in ("engineer", "engineering")))
        self.mode_coordinator.transition_to(target_app_mode, session_data=new_sess)

        effective_mode = "engineer" if (mode in ("engineer", "engineering") or new_sess.get("type") == "engineering") else mode
        cfg_key = f"default_sampling_{effective_mode}"
        from services.session_manager import get_default_sampling_params
        fallback_params = get_default_sampling_params(effective_mode)
        initial_sampling = dict(self.app_config.get(cfg_key, fallback_params))

        new_sess["sampling_params"] = initial_sampling
        new_sess["is_custom_sampling"] = False
        logger.info(f"[SessionManager] [NEW-CHAT] Создан диалог в режиме '{effective_mode}' с дефолтами из config.json: temp={initial_sampling.get('temperature')}")

        self.current_session = new_sess
        self.active_character = character_card if mode == "roleplay" else None

        self._update_mode_ui()
        self._unlock_mode_selector()
        self._apply_chat_background()

        # Unlock model selector for a fresh dialog
        if hasattr(self, "lm_combo") and self.lm_combo:
            self.lm_combo.setEnabled(True)
            self.lm_combo.setToolTip("Выберите модель LM Studio")

        # Insert new list item at top of sidebar
        sess_id = new_sess["id"]
        SandboxManager.set_active_session_id(sess_id)
        ToolEngine.set_active_session_id(sess_id)

        char_name = character_card.name if character_card else None
        avatar_path = str(character_card.avatar_path) if (character_card and character_card.avatar_path) else None
        item_title = new_sess["title"]

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, sess_id)
        w = ChatSessionItemWidget(sess_id, item_title, mode, char_name, avatar_path=avatar_path, session_type=new_sess.get("type"), parent=self)
        self._configure_session_item_widget(w, item_title)
        w.delete_requested.connect(self._on_delete_session_requested)
        w.rename_requested.connect(self._on_rename_session_requested)
        w.change_persona_requested.connect(self._on_change_persona_requested)
        item.setSizeHint(w.sizeHint())

        self.chat_list_widget.insertItem(0, item)
        self.chat_list_widget.setItemWidget(item, w)
        self._session_items[sess_id] = (item, w)
        self.chat_list_widget.setCurrentItem(item)
        if getattr(self, "_workspace_inspector", None) is not None:
            self._workspace_inspector.update_title(item_title)

        # Welcome / greeting message
        if mode == "assistant":
            self._add_assistant_welcome()
        elif mode == "roleplay" and character_card:
            self._remove_greeting_cycler_chip()
            if not getattr(character_card, "visual_dna", "") and getattr(character_card, "description", ""):
                from services.tavern_parser import TavernParser
                dna_reg = TavernParser.extract_physical_dna_regex(character_card.description)
                if dna_reg:
                    character_card.visual_dna = dna_reg
            from services.rp_cast_manager import RPCastManager
            user_persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=new_sess.get("user_persona_id"))
            cast_data = RPCastManager.load_cast(new_sess["id"], user_name=user_persona.get("name", "Игрок"), companion_name=character_card.name)

            if hasattr(character_card, "companions") and len(getattr(character_card, "companions", [])) > 1:
                RPCastManager.bootstrap_party_from_card(new_sess["id"], character_card)
                cast_data = RPCastManager.load_cast(new_sess["id"], user_name=user_persona.get("name", "Игрок"), companion_name=character_card.name)

            if character_card and getattr(character_card, "visual_dna", ""):
                cast_data.setdefault("companion_state", {})["visual_dna"] = character_card.visual_dna

            if character_card and getattr(character_card, "scenery_dna", "").strip():
                cast_data.setdefault("environment", {})["location_dna"] = character_card.scenery_dna.strip()

            if user_persona:
                if user_persona.get("visual_dna"):
                    cast_data.setdefault("player_state", {})["visual_dna"] = user_persona["visual_dna"]
                if user_persona.get("outfit"):
                    cast_data.setdefault("player_state", {})["current_outfit"] = user_persona["outfit"]
                if user_persona.get("inventory"):
                    raw_inv = user_persona["inventory"]
                    inv_items = [it.strip() for it in raw_inv.split(",") if it.strip()] if isinstance(raw_inv, str) else list(raw_inv)
                    existing_inv = cast_data.setdefault("inventory", [])
                    for it in inv_items:
                        if it not in existing_inv:
                            existing_inv.append(it)

            RPCastManager.save_cast(new_sess["id"], cast_data)

            # 1. Загрузка пула завязок
            pool = character_card.get_greetings_pool()
            total_greetings = len(pool)

            chosen_idx = getattr(character_card, "selected_greeting_idx", None)
            if chosen_idx is not None and 0 <= chosen_idx < total_greetings:
                active_greet_idx = chosen_idx
            elif total_greetings > 0:
                import random
                active_greet_idx = random.randint(0, total_greetings - 1)
            else:
                active_greet_idx = 0

            character_card.selected_greeting_idx = None

            if pool and active_greet_idx < len(pool):
                selected_item = pool[active_greet_idx]
                raw_orig = selected_item["orig"]
                cached_ru = selected_item["ru"]
                effective_raw = selected_item["effective"]
            else:
                raw_orig = getattr(character_card, "first_mes", "")
                cached_ru = getattr(character_card, "first_mes_ru", "")
                effective_raw = cached_ru or raw_orig

            from services.macro_engine import MacroEngine
            u_name = self._get_active_user_name() or "Игрок"

            if cached_ru:
                greeting = MacroEngine.process(cached_ru, user_name=u_name, char_name=character_card.name)
                msg_widget = self._append_message(
                    character_card.name,
                    greeting,
                    is_user=False,
                    avatar_path=character_card.avatar_path,
                    translated_text=None,
                    is_showing_translation=False
                )
            else:
                if effective_raw:
                    greeting = MacroEngine.process(effective_raw, user_name=u_name, char_name=character_card.name)
                else:
                    greeting = f"*{character_card.name} появляется перед вами и ждет ваших слов.*"

                latin_cnt = sum(1 for c in greeting if 'a' <= c.lower() <= 'z')
                cyr_cnt = sum(1 for c in greeting if 'а' <= c.lower() <= 'я')
                is_eng = latin_cnt > cyr_cnt and latin_cnt > 15

                msg_widget = self._append_message(
                    character_card.name,
                    greeting,
                    is_user=False,
                    avatar_path=character_card.avatar_path,
                    translated_text=None,
                    is_showing_translation=False
                )
                if self.auto_translate and is_eng:
                    self._enqueue_translation_task(greeting, msg_widget)

            # 2. Синхронизация завязки в память модели (Root Anchor)
            self.chat_history.append({"role": "assistant", "content": greeting})
            if self.current_session:
                self.current_session["chat_history"] = list(self.chat_history)
                self.current_session["active_greeting_idx"] = active_greet_idx

            # 3. Чип переключения завязок прямо в чате (если вариантов > 1)
            if total_greetings > 1:
                self._create_greeting_cycler_chip(msg_widget, character_card, active_greet_idx)

        self._save_current_session()
        self._unlock_mode_selector()
        if hasattr(self, "_sync_splitter_layout") and hasattr(self, "is_engineer_mode_active"):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._sync_splitter_layout(self.is_engineer_mode_active()))

    def _create_greeting_cycler_chip(self, msg_widget, character_card: CharacterCard, current_idx: int):
        """Создает интерактивный чип переключения альтернативных завязок под первым сообщением."""
        self._remove_greeting_cycler_chip()

        pool = character_card.get_greetings_pool()
        total = len(pool)
        if total <= 1:
            return

        cycler_widget = QWidget(self.chat_container)
        cycler_layout = QHBoxLayout(cycler_widget)
        cycler_layout.setContentsMargins(58, 2, 0, 8)
        cycler_layout.setSpacing(6)

        btn_cycle = QPushButton(f"🎲 Сменить завязку ({current_idx + 1}/{total})", cycler_widget)
        btn_cycle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cycle.setToolTip("Переключить на другую сюжетную завязку персонажа (пока вы не сделали первый ход)")
        btn_cycle.setStyleSheet("""
            QPushButton {
                background-color: rgba(14, 28, 48, 0.85);
                color: #38BDF8;
                border: 1px solid #1E3A5F;
                border-radius: 12px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 240, 255, 0.2);
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton:pressed {
                background-color: #0A101D;
            }
        """)

        def _on_cycle():
            nonlocal current_idx
            p = character_card.get_greetings_pool()
            if not p:
                return
            current_idx = (current_idx + 1) % len(p)
            item = p[current_idx]
            effective_raw = item.get("effective")
            cached_ru = item.get("ru")

            from services.macro_engine import MacroEngine
            u_name = self._get_active_user_name() or "Игрок"
            if cached_ru:
                new_text = MacroEngine.process(cached_ru, user_name=u_name, char_name=character_card.name)
                trans_text = None
            else:
                new_text = MacroEngine.process(effective_raw or "", user_name=u_name, char_name=character_card.name)
                trans_text = None

            if hasattr(msg_widget, "set_text"):
                msg_widget.set_text(new_text)
            if hasattr(msg_widget, "set_translation"):
                msg_widget.set_translation(trans_text)

            if self.chat_history:
                self.chat_history[0]["content"] = new_text

            if self.current_session:
                if self.current_session.get("chat_history"):
                    self.current_session["chat_history"][0]["content"] = new_text
                if self.current_session.get("messages"):
                    self.current_session["messages"][0]["text"] = new_text
                    self.current_session["messages"][0]["translated_text"] = trans_text
                self.current_session["active_greeting_idx"] = current_idx
                self._save_current_session()

            btn_cycle.setText(f"🎲 Сменить завязку ({current_idx + 1}/{len(p)})")
            self.statusBar().showMessage(f"Завязка переключена на вариант #{current_idx + 1}.", 3000)

        btn_cycle.clicked.connect(_on_cycle)
        cycler_layout.addWidget(btn_cycle)
        cycler_layout.addStretch()

        insert_idx = min(1, self.chat_layout.count())
        self.chat_layout.insertWidget(insert_idx, cycler_widget)
        self._greeting_cycler_widget = cycler_widget

    def _remove_greeting_cycler_chip(self):
        """Удаляет чип смены завязок после совершения первого хода или смены чата."""
        chip = getattr(self, "_greeting_cycler_widget", None)
        if chip is not None:
            self._greeting_cycler_widget = None
            try:
                chip.hide()
                if hasattr(self, "chat_layout") and self.chat_layout:
                    self.chat_layout.removeWidget(chip)
                chip.deleteLater()
            except Exception:
                pass

    def _on_chat_list_item_clicked(self, item: QListWidgetItem):
        """Handler when clicking a chat item in sidebar."""
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if not session_id:
            return
        if self.current_session and self.current_session.get("id") == session_id:
            return
        self._switch_to_session(session_id)

    def _load_chat_history(self, session_data: Dict[str, Any]):
        """Считывает метаданные и настройки сессии при загрузке диалога."""
        sess_meta = session_data.get("metadata", {}) if isinstance(session_data, dict) else {}
        self._active_assistant_skill = sess_meta.get("active_assistant_skill", "auto")
        self._active_engineer_skill = sess_meta.get("active_engineer_skill", "auto")

    def _switch_to_session(self, session_id: str):
        """Loads and activates a saved session by ID with instant rendering and isolated context."""
        self._remove_greeting_cycler_chip()
        if self._is_switching_session:
            return
        self._is_switching_session = True
        try:
            if self.current_session:
                self._save_current_session()

            # 1. Total context isolation: wipe in-memory history and existing widgets first
            self._turn_bubble = None
            self._current_streaming_widget = None
            self.chat_history.clear()
            self._clear_chat_widgets()

            session_data = SessionManager.load_session(session_id)
            if not session_data:
                return

            if getattr(self, "_workspace_inspector", None) is not None:
                sess_title = session_data.get("title", "Песочница")
                self._workspace_inspector.update_title(sess_title)

            from services.mode_coordinator import AppMode
            is_eng_sess = bool(
                session_data.get("type") in ("engineering", "engineer")
                or session_data.get("mode") in ("engineering", "engineer")
            )
            target_app_mode = AppMode.from_string(session_data.get("mode"), is_engineer_checked=is_eng_sess)
            self.mode_coordinator.transition_to(target_app_mode, session_data=session_data)

            self.current_session = session_data
            session_history = session_data.get("chat_history", [])
            if not session_history and session_data.get("messages"):
                session_history = SessionManager.reconstruct_chat_history_from_messages(
                    session_data["messages"],
                    mode=session_data.get("mode")
                )
                session_data["chat_history"] = list(session_history)
            self.chat_history = list(session_history)
            self._load_chat_history(session_data)
            mode = session_data.get("mode", "assistant")

            # Sync active session ID to SandboxManager & ToolEngine
            SandboxManager.set_active_session_id(session_id)
            ToolEngine.set_active_session_id(session_id)

            # Restore character card if roleplay
            char_data = session_data.get("character_data")
            if mode == "roleplay" and char_data:
                self.active_character = self._restore_roleplay_character_from_session(char_data)
            else:
                self.active_character = None

            self._update_header_for_current_state()

            # Restore model and lock status for this session WITHOUT triggering async weight reloads
            sess_model = session_data.get("model_id")
            if hasattr(self, "lm_combo") and self.lm_combo:
                self.lm_combo.blockSignals(True)
                try:
                    if sess_model and not sess_model.startswith("default"):
                        idx = self.lm_combo.findText(sess_model)
                        if idx >= 0:
                            if self.lm_combo.currentIndex() != idx:
                                self.lm_combo.setCurrentIndex(idx)
                        else:
                            self.lm_combo.addItem(sess_model)
                            self.lm_combo.setCurrentIndex(self.lm_combo.count() - 1)
                        self.active_lms_model = sess_model

                    is_locked = session_data.get("is_model_locked", False)
                    if is_locked:
                        self.lm_combo.setEnabled(False)
                        self.lm_combo.setToolTip("Модель зафиксирована для этого диалога")
                    else:
                        self.lm_combo.setEnabled(True)
                        self.lm_combo.setToolTip("Выберите модель LM Studio")
                finally:
                    self.lm_combo.blockSignals(False)
            else:
                if sess_model and not sess_model.startswith("default"):
                    self.active_lms_model = sess_model

            # Set current item in sidebar
            if session_id in self._session_items:
                it, _ = self._session_items[session_id]
                self.chat_list_widget.setCurrentItem(it)

            # Restore custom background if configured
            self._apply_chat_background()

            # Чанковый (прогрессивный) рендеринг истории сообщений
            self._all_session_messages = session_data.get("messages", []) or []
            total_msgs = len(self._all_session_messages)
            page_size = 35
            first_chunk_size = 8

            if total_msgs <= first_chunk_size:
                # Короткий диалог: все сообщения помещаются в первый экран
                self._rendered_messages_offset = 0
                first_chunk = self._all_session_messages
                earlier_chunk = []
                final_offset = 0
            else:
                # Чанк 1: последние 8 сообщений (мгновенное отображение без фриза)
                first_chunk_offset = total_msgs - first_chunk_size
                first_chunk = self._all_session_messages[first_chunk_offset:]
                self._rendered_messages_offset = first_chunk_offset

                # Чанк 2: оставшиеся более ранние сообщения текущей страницы
                final_offset = max(0, total_msgs - page_size)
                earlier_chunk = self._all_session_messages[final_offset:first_chunk_offset]

            def _render_earlier_chunk():
                try:
                    if not self.current_session or self.current_session.get("id") != session_id:
                        return
                    if getattr(self, "_rendered_messages_offset", None) != (total_msgs - first_chunk_size):
                        return
                    if not hasattr(self, "chat_layout") or self.chat_layout is None:
                        return

                    # Запоминаем текущую позицию скролла, чтобы при добавлении сообщений сверху экран не прыгал
                    v_bar = None
                    if hasattr(self, "scroll_area") and self.scroll_area:
                        v_bar = self.scroll_area.verticalScrollBar()
                    elif hasattr(self, "chat_stream") and hasattr(self.chat_stream, "scroll_area") and self.chat_stream.scroll_area:
                        v_bar = self.chat_stream.scroll_area.verticalScrollBar()

                    old_val = v_bar.value() if v_bar else 0
                    old_max = v_bar.maximum() if v_bar else 0

                    if hasattr(self, "chat_container") and self.chat_container:
                        self.chat_container.setUpdatesEnabled(False)
                    try:
                        self._rendered_messages_offset = final_offset
                        if final_offset > 0:
                            self._create_load_more_button()

                        insert_idx = 1 if getattr(self, "_btn_load_more_widget", None) else 0
                        self._render_session_messages(earlier_chunk, target_index=insert_idx)
                    finally:
                        if hasattr(self, "chat_container") and self.chat_container:
                            self.chat_container.setUpdatesEnabled(True)

                    if v_bar:
                        def _restore_scroll():
                            try:
                                new_max = v_bar.maximum()
                                diff = new_max - old_max
                                target_pos = old_val + diff
                                if hasattr(self, "_sync_scroll_to"):
                                    self._sync_scroll_to(target_pos)
                                else:
                                    v_bar.setValue(target_pos)
                            except (RuntimeError, AttributeError):
                                pass
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(20, _restore_scroll)
                except (RuntimeError, AttributeError):
                    return

            def _render_and_finalize_chat():
                if self.current_session and self.current_session.get("id") != session_id:
                    return

                if hasattr(self, "chat_container") and self.chat_container:
                    self.chat_container.setUpdatesEnabled(False)
                try:
                    self._render_session_messages(first_chunk)
                finally:
                    if hasattr(self, "chat_container") and self.chat_container:
                        self.chat_container.setUpdatesEnabled(True)

                # Реактивное восстановление Greeting Cycler при смене диалога до первого хода игрока
                all_msgs = session_data.get("messages", []) or []
                if (
                    mode == "roleplay"
                    and self.active_character
                    and len(all_msgs) == 1
                    and not all_msgs[0].get("is_user")
                    and len(self.active_character.get_greetings_pool()) > 1
                ):
                    first_widget = None
                    for i in range(self.chat_layout.count()):
                        w = self.chat_layout.itemAt(i).widget()
                        if isinstance(w, ChatMessageWidget):
                            first_widget = w
                            break
                    if first_widget:
                        curr_idx = session_data.get("active_greeting_idx", 0)
                        self._create_greeting_cycler_chip(first_widget, self.active_character, curr_idx)

                self._scroll_to_bottom()

                # Чанк 2: отложенная подгрузка более ранних сообщений через микро-паузу (50 мс)
                if earlier_chunk:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(50, _render_earlier_chunk)

            if getattr(self, "_is_initializing", False):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, _render_and_finalize_chat)
            else:
                _render_and_finalize_chat()

            self._check_mode_lock()
            self.on_active_chat_changed(session_id)
            self.statusBar().showMessage(f"Загружен диалог: {session_data.get('title', 'Диалог')}", 3000)
            # Гарантированное обновление визуального режима и сплиттера под загруженную сессию
            self._update_mode_ui()
        finally:
            self._is_switching_session = False

    def on_active_chat_changed(self, chat_id: str):
        chat = (
            self.current_session
            if (self.current_session and self.current_session.get("id") == chat_id)
            else SessionManager.load_session(chat_id)
        )
        if not chat:
            return

        raw_mode = str(chat.get("mode") or "assistant").lower()
        raw_type = str(chat.get("type") or "").lower()
        if raw_type == "engineering" or raw_mode in ("engineer", "engineering"):
            target_mode = "engineer"
        elif raw_mode in ("rp", "roleplay"):
            target_mode = "rp"
        else:
            target_mode = "assistant"

        self._engineering_mode_active = (target_mode == "engineer")
        if hasattr(self, "input_box") and self.input_box:
            self.input_box.set_engineer_mode(self._engineering_mode_active)

        if target_mode == "engineer":
            target_model = self.app_config.get("engineer_model_name") or self.app_config.get("engineer_model")
        elif target_mode == "rp":
            char_data = chat.get("character_data") or {}
            char_model = (
                char_data.get("character_model") if isinstance(char_data, dict)
                else getattr(char_data, "character_model", None)
            )
            target_model = (
                char_model
                or self.app_config.get("rp_model_name")
                or chat.get("model_id")
                or self.app_config.get("active_model")
            )
        elif target_mode == "assistant":
            target_model = self.app_config.get("assistant_model_name") or self.app_config.get("active_model")
        else:
            target_model = None

        current_model = getattr(self.lm_client, "_last_loaded_model_id", None) or getattr(self, "active_lms_model", "")
        if target_model and current_model != target_model:
            self.active_lms_model = target_model
            logger.info(f"[ModeSwitch] Запланировано переключение модели для '{target_mode}': '{current_model}' -> '{target_model}' (debounce 400ms)")
            self._pending_switch_model = target_model
            self._pending_switch_mode = target_mode
            self._model_switch_timer.start()

    def _execute_debounced_model_switch(self):
        """Выполняет переключение модели только после стабилизации выбора пользователя."""
        target_model = self._pending_switch_model
        target_mode = self._pending_switch_mode
        if not target_model:
            return

        if hasattr(self, "lm_client") and hasattr(self.lm_client, "ensure_model_loaded"):
            import threading
            def _switch_worker():
                try:
                    logger.info(f"[ModeSwitch] Старт загрузки модели: '{target_model}'")
                    self.lm_client.ensure_model_loaded(preferred_model=target_model)
                except Exception as err:
                    logger.warning(f"[ModeSwitch] Ошибка фонового переключения модели: {err}")

            t = threading.Thread(target=_switch_worker, name="ModelSwitchWorker", daemon=True)
            t.start()



    def _render_session_messages(self, messages: List[Dict[str, Any]], target_index: Optional[int] = None):
        """
        Рендеринг сохраненных сообщений сессии при переключении чата или подгрузке истории.
        Проверяет наличие прикрепленного изображения и корректно отрисовывает карточки арта
        с сохранением отправителя.
        """
        self._turn_bubble = None
        self._current_streaming_widget = None
        default_sender = getattr(self.active_character, "name", "Элария") if hasattr(self, "active_character") and self.active_character else "Элария"

        # Авто-консолидация старых разрозненных сообщений инженера в одну монолитную карточку
        processed_messages = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if (not m.get("is_user")) and ("### 📐 Архитектурный план" in m.get("text", "") or "### 🔍 Инспекция проекта" in m.get("text", "")):
                if i + 1 < len(messages) and not messages[i + 1].get("is_user"):
                    next_m = messages[i + 1]
                    if next_m.get("text", "").startswith(("✅ Файл", "⚠️")):
                        merged = dict(next_m)
                        merged["thought"] = {"content": m.get("text", ""), "duration": 0.0}
                        if m.get("file_paths") and not merged.get("file_paths"):
                            merged["file_paths"] = m.get("file_paths")
                        processed_messages.append(merged)
                        i += 2
                        continue
            processed_messages.append(m)
            i += 1

        for msg in processed_messages:
            img_path = msg.get("image_path")
            if not img_path and msg.get("file_paths"):
                for fp in msg.get("file_paths", []):
                    if str(fp).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        img_path = fp
                        break

            sender = msg.get("sender") or (default_sender if not msg.get("is_user") and msg.get("role") != "user" else "Вы")

            if img_path and Path(img_path).exists():
                # Отрисовать бабл с изображением с сохранением отправителя (msg.get("sender", "Элария"))
                msg_copy = dict(msg)
                msg_copy["sender"] = sender
                msg_copy["image_path"] = str(img_path)
                self._render_stored_message(msg_copy, target_index=target_index)
            else:
                # Стандартная отрисовка текстового бабла
                msg_copy = dict(msg)
                msg_copy["sender"] = sender
                if img_path and not Path(img_path).exists():
                    msg_copy["image_path"] = None
                self._render_stored_message(msg_copy, target_index=target_index)

            if target_index is not None:
                target_index += 1

    def _create_load_more_button(self):
        remaining = getattr(self, "_rendered_messages_offset", 0)
        if hasattr(self, "chat_stream") and self.chat_stream:
            self.chat_stream.remove_load_more_button()
        btn = self.chat_stream.create_load_more_button(remaining, on_click_callback=self._load_previous_messages_chunk)
        self._btn_load_more_widget = btn






    def _on_change_persona_requested(self, session_id: str, persona_id: str):
        """Persists chosen user persona for session and refreshes header if active."""
        SessionManager.set_session_user_persona(session_id, persona_id)
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session["user_persona_id"] = persona_id
            self._update_header_for_current_state()

        try:
            from services.rp_cast_manager import RPCastManager
            persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=persona_id)
            p_name = persona.get("name", "Игрок")
            p_dna = persona.get("visual_dna", "")
            cast_data = RPCastManager.load_cast(session_id, user_name=p_name)
            if "player_state" not in cast_data or not isinstance(cast_data["player_state"], dict):
                cast_data["player_state"] = {}
            cast_data["player_state"]["name"] = p_name
            cast_data["player_state"]["visual_dna"] = p_dna
            RPCastManager.save_cast(session_id, cast_data)
        except Exception as e:
            logger.warning(f"Ошибка синхронизации player_state в RPCastManager: {e}")


    def _check_mode_lock(self):
        """Locks mode switching if active session is roleplay with messages."""
        pass

    def _unlock_mode_selector(self):
        """Unlocks mode switching for a fresh session."""
        pass

    def _configure_session_item_widget(self, w: ChatSessionItemWidget, title: str):
        """Forwarding for backward compatibility with external coordinators."""
        w.update_title(title)

    def _get_active_user_name(self, is_roleplay: Optional[bool] = None) -> str:
        """Извлекает актуальное имя пользователя напрямую из SessionManager (user_profiles.json)."""
        try:
            rp_flag = (self.current_mode == "roleplay") if is_roleplay is None else bool(is_roleplay)
            sess_pid = self.current_session.get("user_persona_id") if getattr(self, "current_session", None) else None
            persona = SessionManager.get_active_user_persona(is_roleplay=rp_flag, session_persona_id=sess_pid)
            name = str(persona.get("name") or "").strip()
            if name and name not in ("User", "Пользователь"):
                return name
            return name or ("Игрок" if rp_flag else "Пользователь")
        except Exception as e:
            logger.debug(f"Error resolving active user name: {e}")
            return "Игрок" if (is_roleplay or getattr(self, "current_mode", "") == "roleplay") else "Пользователь"

    def _add_assistant_welcome(self):
        """Формирует естественное стартовое приветствие Эларии при открытии нового диалога."""
        import random
        user_name = self._get_active_user_name()

        if user_name:
            welcomes = [
                "Привет. С чего начнём?",
                f"Привет, {user_name}. Рассказывай, что придумал.",
                f"Привет, {user_name}. Готова к работе.",
                "Привет. Справимся с чем угодно. Поделись своими мыслями.",
                "Привет. Готова к любым экспериментам. Что на повестке?"
            ]
        else:
            welcomes = [
                "Привет. С чего начнём?",
                "Привет. Рассказывай, что придумал.",
                "Привет. Готова к работе.",
                "Привет. Справимся с чем угодно. Поделись своими мыслями.",
                "Привет. Готова к любым экспериментам. Что на повестке?"
            ]

        welcome_text = random.choice(welcomes)
        self._append_message("Элария", welcome_text, is_user=False)

    def _switch_mode(self, mode: str, update_session: bool = True):
        from services.mode_coordinator import AppMode
        target = AppMode.ROLEPLAY if mode == "roleplay" else AppMode.ASSISTANT
        self.mode_coordinator.transition_to(target)


