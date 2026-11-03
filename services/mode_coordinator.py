from enum import Enum
import gc
import logging
from typing import Optional, Any, Dict

logger = logging.getLogger("NeonRoute.ModeCoordinator")

class AppMode(str, Enum):
    ASSISTANT = "assistant"
    ROLEPLAY = "roleplay"
    ENGINEER = "engineer"

    @classmethod
    def from_string(cls, val: Optional[str], is_engineer_checked: bool = False) -> 'AppMode':
        raw = str(val or "").strip().lower()
        if raw == "roleplay":
            return cls.ROLEPLAY
        if raw in ("engineer", "engineering") or is_engineer_checked:
            return cls.ENGINEER
        return cls.ASSISTANT


class ModeCoordinator:
    """
    Централизованный конечный автомат переключения режимов NeonRoute.
    Гарантирует детерминированный жизненный цикл: Teardown -> Switch -> Boot -> Mount.
    Обеспечивает полную изоляцию контекста (Mode Isolation) без утечки сообщений,
    системных промптов и служебных тегов между ролями.
    """
    def __init__(self, main_window: Any):
        self.mw = main_window
        self._current_mode: AppMode = AppMode.ASSISTANT
        self._is_transitioning: bool = False

    @property
    def current_mode(self) -> AppMode:
        return self._current_mode

    def transition_to(self, target_mode: AppMode, session_data: Optional[dict] = None) -> bool:
        """
        Главная точка входа смены режима.
        Выполняет полную изоляцию, зачистку воркеров и детерминированный запуск целевого режима.
        """
        if self._is_transitioning:
            logger.warning(f"[ModeCoordinator] Переход уже выполняется. Запрос {target_mode.value} отклонен во избежание гонки потоков.")
            return False

        if self._current_mode == target_mode and session_data is None:
            curr_sess = getattr(self.mw, "current_session", None)
            if curr_sess:
                s_mode = str(curr_sess.get("mode") or "").lower()
                s_type = str(curr_sess.get("type") or "").lower()
                is_eng = (s_type == "engineering" or s_mode in ("engineer", "engineering"))
                matches = (
                    (target_mode == AppMode.ENGINEER and is_eng)
                    or (target_mode == AppMode.ROLEPLAY and s_mode == "roleplay")
                    or (target_mode == AppMode.ASSISTANT and s_mode == "assistant" and not is_eng)
                )
                if matches:
                    logger.debug(f"[ModeCoordinator] Уже в режиме {target_mode.value} с соответствующей сессией.")
                    return True

        logger.info(f"[ModeCoordinator] Инициация перехода: {self._current_mode.value} -> {target_mode.value}")

        self._is_transitioning = True
        was_switching = getattr(self.mw, "_is_switching_session", False)
        self.mw._is_switching_session = True
        try:
            # ── 1. ФАЗА TEARDOWN (Остановка и очистка уходящего режима) ──
            self._teardown_current_mode()

            # ── 2. ФАЗА SWITCH (Фиксация состояния) ──
            self._current_mode = target_mode
            if target_mode == AppMode.ENGINEER:
                self.mw.current_mode = "assistant"
                self.mw._engineering_mode_active = True
            elif target_mode == AppMode.ROLEPLAY:
                self.mw.current_mode = "roleplay"
                self.mw._engineering_mode_active = False
            else:
                self.mw.current_mode = "assistant"
                self.mw._engineering_mode_active = False

            # Очистка оперативного списка сообщений (Context Isolation)
            if hasattr(self.mw, "chat_history") and isinstance(self.mw.chat_history, list):
                self.mw.chat_history.clear()

            # Если конкретная сессия не передана, находим последнюю или создаем новую изолированную
            is_new = False
            coordinator_selected_session = False
            if session_data is None:
                coordinator_selected_session = True
                # Очистка графической области сообщений
                if hasattr(self.mw, "clear_chat_view"):
                    self.mw.clear_chat_view()
                elif hasattr(self.mw, "_clear_chat_widgets"):
                    self.mw._clear_chat_widgets()

                session_data = self._find_latest_session_for_mode(target_mode)
                if session_data is None:
                    session_data = self._create_isolated_session_for_mode(target_mode)
                    is_new = True

            # ── 3. ФАЗА BOOT (Инициализация и запуск целевого режима) ──
            self._boot_target_mode(target_mode, session_data)

            # Если сессия подобрана или создана координатором (session_data изначально был None), монтируем ее в UI
            if coordinator_selected_session and session_data is not None:
                self._mount_session(target_mode, session_data, is_new=is_new)

            logger.info(f"[ModeCoordinator] Переход в режим '{target_mode.value}' успешно завершен.")
            return True
        finally:
            self.mw._is_switching_session = was_switching
            self._is_transitioning = False

    def _teardown_current_mode(self):
        """Гарантированная остановка активных процессов уходящего режима и изоляция стейта."""
        # 1. Безопасная остановка генерации ответа нейросетью
        if hasattr(self.mw, "abort_generation"):
            self.mw.abort_generation()
        elif hasattr(self.mw, "task_queue") and self.mw.task_queue:
            self.mw.task_queue.abort_current()

        if hasattr(self.mw, "lm_client") and hasattr(self.mw.lm_client, "abort"):
            try:
                self.mw.lm_client.abort()
            except Exception:
                pass

        # 2. Изоляция словаря активных задач
        if hasattr(self.mw, "_active_tasks") and isinstance(self.mw._active_tasks, dict):
            self.mw._active_tasks.clear()

        # 3. Обязательное сохранение активной сессии перед переключением
        if getattr(self.mw, "current_session", None):
            if hasattr(self.mw, "chat_history") and isinstance(self.mw.chat_history, list):
                self.mw.current_session["chat_history"] = list(self.mw.chat_history)
            curr_sess = self.mw.current_session
            current_session_id = curr_sess.get("id")
            from services.session_manager import SessionManager
            SessionManager.set_active_session(curr_sess)
            if current_session_id:
                SessionManager.get_instance().save_session(current_session_id)
            else:
                SessionManager.get_instance().save_session(curr_sess)

        # 4. Адресный Teardown для специфичных контроллеров
        if self._current_mode == AppMode.ENGINEER:
            self._teardown_engineer_subsystem()
        elif self._current_mode == AppMode.ROLEPLAY:
            self._teardown_roleplay_subsystem()

        # 5. Остановка воркера инструментов песочницы инженера
        if hasattr(self.mw, "_tool_worker") and self.mw._tool_worker is not None:
            worker = self.mw._tool_worker
            self.mw._tool_worker = None
            try:
                worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            if hasattr(worker, "requestInterruption"):
                worker.requestInterruption()
            if worker.isRunning():
                worker.wait(500)

        # 6. Отмена таймеров перезагрузки LM Studio и буферов стриминга
        if hasattr(self.mw, "_cancel_lms_reload"):
            self.mw._cancel_lms_reload()
        if hasattr(self.mw, "_stream_flush_timer") and self.mw._stream_flush_timer is not None:
            self.mw._stream_flush_timer.stop()
        self.mw._stream_token_buffer = ""

        # 7. Зачистка временных UI-указателей текущего хода
        self.mw._turn_bubble = None
        self.mw._current_streaming_widget = None
        if hasattr(self.mw, "_remove_typing_loader"):
            self.mw._remove_typing_loader()

        # 8. Принудительная сборка мусора (освобождение RAM/VRAM-ссылок)
        gc.collect()

    def _teardown_engineer_subsystem(self):
        """Сброс временных состояний инженера."""
        if hasattr(self.mw, "_ephemeral_doc_worker") and self.mw._ephemeral_doc_worker is not None:
            worker = self.mw._ephemeral_doc_worker
            self.mw._ephemeral_doc_worker = None
            try:
                worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            if hasattr(worker, "requestInterruption"):
                worker.requestInterruption()
            if worker.isRunning():
                worker.wait(500)
        self.mw._last_has_vision_flag = False
        self.mw._pending_workspace_file = None
        self.mw._script_executed_this_turn = False
        self.mw._tool_executed_this_turn = False
        self.mw._doc_created_this_turn = False
        self.mw._tool_loop_count = 0
        self.mw._last_tool_bubble = None
        self.mw._turn_assistant_widget = None
        self.mw._is_post_tool_synthesis = False
        self.mw._synthesis_first_chunk = False

    def _teardown_roleplay_subsystem(self):
        """Сброс временных состояний ролевого режима."""
        if hasattr(self.mw, "_cast_extractor_worker") and self.mw._cast_extractor_worker is not None:
            worker = self.mw._cast_extractor_worker
            self.mw._cast_extractor_worker = None
            try:
                worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            if hasattr(worker, "requestInterruption"):
                worker.requestInterruption()
            if worker.isRunning():
                worker.wait(500)
        self.mw.active_character = None
        self.mw._pending_scene_art_request = None
        if hasattr(self.mw, "_remove_greeting_cycler_chip"):
            self.mw._remove_greeting_cycler_chip()

    def _find_latest_session_for_mode(self, target_mode: AppMode) -> Optional[dict]:
        """Поиск последней активной сохраненной сессии для целевого режима."""
        from services.session_manager import SessionManager
        try:
            sessions = SessionManager.list_sessions()
            for s in sessions:
                s_id = s.get("id")
                if not s_id:
                    continue
                s_mode = str(s.get("mode") or "assistant").lower()
                s_type = str(s.get("type") or "").lower()
                is_eng = (s_type == "engineering" or s_mode in ("engineer", "engineering"))

                if target_mode == AppMode.ENGINEER and is_eng:
                    return SessionManager.load_session(s_id)
                elif target_mode == AppMode.ROLEPLAY and s_mode == "roleplay":
                    return SessionManager.load_session(s_id)
                elif target_mode == AppMode.ASSISTANT and s_mode == "assistant" and not is_eng:
                    return SessionManager.load_session(s_id)
        except Exception as e:
            logger.error(f"[ModeCoordinator] Ошибка поиска сессии для режима {target_mode.value}: {e}")
        return None

    def _create_isolated_session_for_mode(self, target_mode: AppMode) -> dict:
        """Создание новой изолированной сессии для целевого режима."""
        from services.session_manager import SessionManager, get_default_sampling_params

        mode_val = "assistant" if target_mode == AppMode.ENGINEER else target_mode.value
        title = "🛠️ Инженерная задача" if target_mode == AppMode.ENGINEER else ("🎭 Ролевой диалог" if target_mode == AppMode.ROLEPLAY else "💬 Новый диалог")

        current_model = ""
        if hasattr(self.mw, "lm_combo") and self.mw.lm_combo:
            current_model = self.mw.lm_combo.currentText().strip()
            if current_model.startswith("default"):
                current_model = ""

        new_sess = SessionManager.get_instance().create_session(
            mode=mode_val,
            title=title,
            model_id=current_model,
            is_model_locked=False
        )

        if target_mode == AppMode.ENGINEER:
            new_sess["type"] = "engineering"
            new_sess["mode"] = "assistant"

        effective_mode = target_mode.value
        cfg_key = f"default_sampling_{effective_mode}"
        fallback_params = get_default_sampling_params(effective_mode)
        app_cfg = getattr(self.mw, "app_config", {})
        initial_sampling = dict(app_cfg.get(cfg_key, fallback_params))
        new_sess["sampling_params"] = initial_sampling
        new_sess["is_custom_sampling"] = False

        SessionManager.get_instance().save_session(new_sess)
        return new_sess

    def _add_session_item_to_sidebar(self, session_data: dict, target_mode: AppMode):
        """Добавляет новую сессию в список сессий боковой панели."""
        if not hasattr(self.mw, "chat_list_widget") or not hasattr(self.mw, "_session_items"):
            return
        sess_id = session_data.get("id")
        if not sess_id or sess_id in self.mw._session_items:
            return
        try:
            from PyQt6.QtWidgets import QListWidgetItem
            from PyQt6.QtCore import Qt
            from ui.components.chat_list_item import ChatSessionItemWidget

            item_title = session_data.get("title", "Диалог")
            mode_str = "roleplay" if target_mode == AppMode.ROLEPLAY else ("engineer" if target_mode == AppMode.ENGINEER else "assistant")
            char_data = session_data.get("character_data")
            char_name = char_data.get("name") if char_data else None
            avatar_path = (char_data.get("avatar_path") if char_data else None) or session_data.get("assistant_avatar_path")
            s_type = session_data.get("type")

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, sess_id)
            w = ChatSessionItemWidget(sess_id, item_title, mode_str, char_name, avatar_path=avatar_path, session_type=s_type, parent=self.mw)
            if hasattr(self.mw, "_configure_session_item_widget"):
                self.mw._configure_session_item_widget(w, item_title)
            w.delete_requested.connect(self.mw._on_delete_session_requested)
            w.rename_requested.connect(self.mw._on_rename_session_requested)
            if hasattr(w, "change_persona_requested"):
                w.change_persona_requested.connect(self.mw._on_change_persona_requested)
            item.setSizeHint(w.sizeHint())

            self.mw.chat_list_widget.insertItem(0, item)
            self.mw.chat_list_widget.setItemWidget(item, w)
            self.mw._session_items[sess_id] = (item, w)
            self.mw.chat_list_widget.setCurrentItem(item)
        except Exception as e:
            logger.warning(f"[ModeCoordinator] Ошибка добавления сессии в сайдбар: {e}")

    def _mount_session(self, target_mode: AppMode, session_data: dict, is_new: bool = False):
        """Монтирует данные сессии в UI и структуры MainWindow."""
        sess_id = session_data.get("id")
        self.mw.current_session = session_data
        self.mw.chat_history = list(session_data.get("chat_history", []))

        # 1. Синхронизация песочницы и инструментов (только для инженерных сессий)
        is_eng = (target_mode == AppMode.ENGINEER)
        if sess_id and is_eng:
            try:
                from services.sandbox_manager import SandboxManager
                SandboxManager.set_active_session_id(sess_id)
            except Exception:
                pass
            try:
                from services.tool_engine import ToolEngine
                ToolEngine.set_active_session_id(sess_id)
            except Exception:
                pass
        else:
            try:
                from services.sandbox_manager import SandboxManager
                SandboxManager.set_active_session_id(None)
            except Exception:
                pass

        # 2. Восстановление активного персонажа (только для Roleplay)
        char_data = session_data.get("character_data")
        if target_mode == AppMode.ROLEPLAY and char_data:
            if hasattr(self.mw, "_restore_roleplay_character_from_session"):
                self.mw.active_character = self.mw._restore_roleplay_character_from_session(char_data)
            else:
                from services.tavern_parser import CharacterCard
                self.mw.active_character = CharacterCard.from_dict(char_data) if isinstance(char_data, dict) else char_data
        else:
            self.mw.active_character = None

        # 3. Восстановление модели и блокировки селектора
        sess_model = session_data.get("model_id")
        if hasattr(self.mw, "lm_combo") and self.mw.lm_combo:
            self.mw.lm_combo.blockSignals(True)
            try:
                if sess_model and not sess_model.startswith("default"):
                    idx = self.mw.lm_combo.findText(sess_model)
                    if isinstance(idx, int) and idx >= 0:
                        if self.mw.lm_combo.currentIndex() != idx:
                            self.mw.lm_combo.setCurrentIndex(idx)
                    elif isinstance(idx, int):
                        self.mw.lm_combo.addItem(sess_model)
                        self.mw.lm_combo.setCurrentIndex(self.mw.lm_combo.count() - 1)
                    self.mw.active_lms_model = sess_model

                is_locked = session_data.get("is_model_locked", False)
                self.mw.lm_combo.setEnabled(not is_locked)
                self.mw.lm_combo.setToolTip("Модель зафиксирована для этого диалога" if is_locked else "Выберите модель LM Studio")
            finally:
                self.mw.lm_combo.blockSignals(False)

        # 4. Выбор или добавление в сайдбар
        if sess_id and hasattr(self.mw, "_session_items"):
            if sess_id in self.mw._session_items:
                it, _ = self.mw._session_items[sess_id]
                if hasattr(self.mw, "chat_list_widget"):
                    self.mw.chat_list_widget.setCurrentItem(it)
            else:
                self._add_session_item_to_sidebar(session_data, target_mode)

        # 5. Очистка и отрисовка сообщений
        if hasattr(self.mw, "_clear_chat_widgets"):
            self.mw._clear_chat_widgets()
        elif hasattr(self.mw, "clear_chat_view"):
            self.mw.clear_chat_view()

        if hasattr(self.mw, "_apply_chat_background"):
            self.mw._apply_chat_background()

        messages = session_data.get("messages", []) or []
        self.mw._all_session_messages = messages

        if hasattr(self.mw, "chat_container"):
            self.mw.chat_container.setUpdatesEnabled(False)
        try:
            page_size = 35
            total_msgs = len(messages)
            if hasattr(self.mw, "_render_session_messages"):
                if total_msgs <= page_size:
                    self.mw._rendered_messages_offset = 0
                    self.mw._render_session_messages(messages)
                else:
                    self.mw._rendered_messages_offset = total_msgs - page_size
                    if hasattr(self.mw, "_create_load_more_button"):
                        self.mw._create_load_more_button()
                    self.mw._render_session_messages(messages[self.mw._rendered_messages_offset:])
        finally:
            if hasattr(self.mw, "chat_container"):
                self.mw.chat_container.setUpdatesEnabled(True)

        # 6. Приветствие для ассистента или Greeting Cycler для ролевого
        if target_mode == AppMode.ASSISTANT and not messages:
            if hasattr(self.mw, "_add_assistant_welcome"):
                self.mw._add_assistant_welcome()
        elif (
            target_mode == AppMode.ROLEPLAY
            and self.mw.active_character
            and len(messages) == 1
            and not messages[0].get("is_user")
            and len(self.mw.active_character.get_greetings_pool()) > 1
        ):
            from ui.components.chat_bubble import ChatMessageWidget
            first_widget = None
            if hasattr(self.mw, "chat_layout") and self.mw.chat_layout:
                for i in range(self.mw.chat_layout.count()):
                    w = self.mw.chat_layout.itemAt(i).widget()
                    if isinstance(w, ChatMessageWidget):
                        first_widget = w
                        break
            if first_widget and hasattr(self.mw, "_create_greeting_cycler_chip"):
                curr_idx = session_data.get("active_greeting_idx", 0)
                self.mw._create_greeting_cycler_chip(first_widget, self.mw.active_character, curr_idx)

        if hasattr(self.mw, "_scroll_to_bottom"):
            self.mw._scroll_to_bottom()
        if hasattr(self.mw, "_check_mode_lock"):
            self.mw._check_mode_lock()

    def _boot_target_mode(self, mode: AppMode, session_data: Optional[dict] = None):
        """Монтаж окружения и UI целевого режима."""
        is_eng = (mode == AppMode.ENGINEER)
        if hasattr(self.mw, "_engineering_mode_active"):
            self.mw._engineering_mode_active = is_eng
        if hasattr(self.mw, "input_box") and self.mw.input_box:
            self.mw.input_box.set_engineer_mode(is_eng)

        # Синхронизация панелей (карточка персонажа, фон сцены, кнопка Forge, песочница)
        is_rp = (mode == AppMode.ROLEPLAY)
        if hasattr(self.mw, "char_card_widget"):
            self.mw.char_card_widget.setVisible(is_rp)
        if hasattr(self.mw, "btn_scene_bg"):
            self.mw.btn_scene_bg.setVisible(is_rp)
        if hasattr(self.mw, "btn_forge_toggle"):
            self.mw.btn_forge_toggle.setVisible(mode != AppMode.ENGINEER)
        if hasattr(self.mw, "btn_toggle_inspector"):
            self.mw.btn_toggle_inspector.setVisible(is_eng)

        # Синхронизация инспектора песочницы и размеров сплиттера
        if hasattr(self.mw, "_sync_splitter_layout"):
            self.mw._sync_splitter_layout(is_eng)
        else:
            if hasattr(self.mw, "workspace_inspector"):
                self.mw.workspace_inspector.setVisible(is_eng)
                if is_eng:
                    self.mw.workspace_inspector.refresh_files()

            if hasattr(self.mw, "main_splitter"):
                if is_eng:
                    self.mw.main_splitter.setSizes([260, 560, 380])
                else:
                    self.mw.main_splitter.setSizes([280, 920, 0])

        # 4. Обновление шапки
        self.mw._update_header_for_current_state()
