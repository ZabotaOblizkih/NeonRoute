import re
from pathlib import Path

print("\n=== ВНЕДРЕНИЕ ПАТЧА: PERSISTENCE & HISTORY CONSOLIDATION ===")

# 1. ui/widgets/engineer_task_card.py
card_p = Path("ui/widgets/engineer_task_card.py")
card_txt = card_p.read_text(encoding="utf-8")

# Конструктор принимает is_active
old_init_def = '''    def __init__(
        self,
        sender: str,
        text: str = "",
        avatar_path: Optional[Path] = None,
        session_id: Optional[str] = None,
        parent=None,
        **kwargs
    ):'''

new_init_def = '''    def __init__(
        self,
        sender: str,
        text: str = "",
        avatar_path: Optional[Path] = None,
        session_id: Optional[str] = None,
        parent=None,
        is_active: bool = True,
        **kwargs
    ):'''

if old_init_def in card_txt:
    card_txt = card_txt.replace(old_init_def, new_init_def)
    print("✓ [ui/widgets/engineer_task_card.py] Конструктор расширен параметром is_active=True.")

old_init_flags = '''        self._start_time: float = time.time()
        self._is_active_task: bool = True'''

new_init_flags = '''        self._start_time: float = time.time()
        self._is_active_task: bool = is_active
        self._saved_elapsed_time: str = str(kwargs.get("elapsed_time") or "").strip()'''

if old_init_flags in card_txt:
    card_txt = card_txt.replace(old_init_flags, new_init_flags)
    print("✓ [ui/widgets/engineer_task_card.py] Добавлена поддержка флага is_active и saved_elapsed_time.")

old_timer_start = '''        # Таймер реального времени выполнения
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start()'''

new_timer_start = '''        # Таймер реального времени выполнения
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_timer_tick)
        if self._is_active_task:
            self._timer.start()
        else:
            self.mark_completed()
            if self._saved_elapsed_time:
                self.timer_lbl.setText(self._saved_elapsed_time)'''

if old_timer_start in card_txt:
    card_txt = card_txt.replace(old_timer_start, new_timer_start)
    print("✓ [ui/widgets/engineer_task_card.py] Старт таймера ограничен только живыми активными задачами.")

card_p.write_text(card_txt, encoding="utf-8")

# 2. ui/builders/chat_bubble_builder.py
bb_p = Path("ui/builders/chat_bubble_builder.py")
bb_txt = bb_p.read_text(encoding="utf-8")

old_render_call = '''        widget = widget_cls(
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
        )'''

new_render_call = '''        extra_kw = {}
        if widget_cls.__name__ == "EngineerTaskCardWidget":
            extra_kw["is_active"] = False
            if msg.get("elapsed_time"):
                extra_kw["elapsed_time"] = msg.get("elapsed_time")

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
            parent=getattr(self, "chat_scroll_content", getattr(self, "chat_container", None)),
            **extra_kw
        )'''

if old_render_call in bb_txt:
    bb_txt = bb_txt.replace(old_render_call, new_render_call)
    print("✓ [ui/builders/chat_bubble_builder.py] Архивные карточки инженера рендерятся с is_active=False.")

bb_p.write_text(bb_txt, encoding="utf-8")

# 3. ui/main_window.py
mw_p = Path("ui/main_window.py")
mw_txt = mw_p.read_text(encoding="utf-8")

old_render_loop = '''    def _render_session_messages(self, messages: List[Dict[str, Any]], target_index: Optional[int] = None):
        """
        Рендеринг сохраненных сообщений сессии при переключении чата или подгрузке истории.
        Проверяет наличие прикрепленного изображения и корректно отрисовывает карточки арта
        с сохранением отправителя.
        """
        self._turn_bubble = None
        self._current_streaming_widget = None
        default_sender = getattr(self.active_character, "name", "Элария") if hasattr(self, "active_character") and self.active_character else "Элария"
        for msg in messages:'''

new_render_loop = '''    def _render_session_messages(self, messages: List[Dict[str, Any]], target_index: Optional[int] = None):
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

        for msg in processed_messages:'''

if old_render_loop in mw_txt:
    mw_txt = mw_txt.replace(old_render_loop, new_render_loop)
    print("✓ [ui/main_window.py] _render_session_messages оснащён авто-консолидацией разрозненных сообщений.")

mw_p.write_text(mw_txt, encoding="utf-8")

# 4. ui/controllers/engineer_controller.py
ec_p = Path("ui/controllers/engineer_controller.py")
ec_txt = ec_p.read_text(encoding="utf-8")

old_plan_sync = '''        if existing_bubble is None:
            avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
            bubble = self._append_message("Элария", card_text, is_user=False, avatar_path=avatar)
            if active_task:
                active_task.metadata["bubble"] = bubble
            if hasattr(self, "_sync_turn"):
                self._sync_turn(role="assistant", content=card_text, replace_last=False)'''

new_plan_sync = '''        if existing_bubble is None:
            avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
            bubble = self._append_message("Элария", card_text, is_user=False, avatar_path=avatar)
            if active_task:
                active_task.metadata["bubble"] = bubble
                active_task.metadata["plan_text"] = card_text
                active_task.metadata["message_id"] = getattr(bubble, "message_id", None)'''

if old_plan_sync in ec_txt:
    ec_txt = ec_txt.replace(old_plan_sync, new_plan_sync)
    print("✓ [ui/controllers/engineer_controller.py] Устранён паразитный дублирующий вызов _sync_turn.")

old_fin_sync = '''                    if hasattr(bubble, "_set_summary_text"):
                        bubble._set_summary_text(fin_text)
                    if hasattr(bubble, "mark_completed"):
                        bubble.mark_completed()
                except RuntimeError:
                    bubble = None
            if bubble is None:
                avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
                bubble = self._append_message("Элария", fin_text, is_user=False, avatar_path=avatar)
                if hasattr(bubble, "add_workspace_file_card"):
                    bubble.add_workspace_file_card(artifact_path, on_run=self._execute_workspace_script)
                if hasattr(self, "_update_message_attachment"):
                    self._update_message_attachment(bubble, artifact_path)
                if res.get("stdout") and str(res["stdout"]).strip() and hasattr(bubble, "add_console_spoiler"):
                    bubble.add_console_spoiler(title=f"Консоль: {file_name}", output=res["stdout"], is_error=False)
            self._sync_turn(role="assistant", content=fin_text, replace_last=True)'''

new_fin_sync = '''                    if hasattr(bubble, "_set_summary_text"):
                        bubble._set_summary_text(fin_text)
                    if hasattr(bubble, "mark_completed"):
                        bubble.mark_completed()
                except RuntimeError:
                    bubble = None
            if bubble is None:
                avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
                bubble = self._append_message("Элария", fin_text, is_user=False, avatar_path=avatar)
                if hasattr(bubble, "add_workspace_file_card"):
                    bubble.add_workspace_file_card(artifact_path, on_run=self._execute_workspace_script)
                if hasattr(self, "_update_message_attachment"):
                    self._update_message_attachment(bubble, artifact_path)
                if res.get("stdout") and str(res["stdout"]).strip() and hasattr(bubble, "add_console_spoiler"):
                    bubble.add_console_spoiler(title=f"Консоль: {file_name}", output=res["stdout"], is_error=False)

            # Синхронизация единственной монолитной записи задачи в session.json
            if getattr(self, "current_session", None) and isinstance(self.current_session.get("messages"), list):
                target_msg = None
                msg_id = getattr(bubble, "message_id", None) if bubble else task.metadata.get("message_id")
                if msg_id:
                    for m in self.current_session["messages"]:
                        if m.get("id") == msg_id:
                            target_msg = m
                            break
                if not target_msg:
                    for m in reversed(self.current_session["messages"]):
                        if not m.get("is_user", False):
                            target_msg = m
                            break
                if target_msg:
                    target_msg["text"] = fin_text
                    if artifact_path:
                        target_msg["file_paths"] = [str(artifact_path)]
                    plan_t = getattr(bubble, "_captured_plan", "") or task.metadata.get("plan_text", "")
                    if plan_t:
                        target_msg["thought"] = {
                            "content": plan_t,
                            "duration": 0.0
                        }
                    if bubble and hasattr(bubble, "timer_lbl"):
                        target_msg["elapsed_time"] = bubble.timer_lbl.text()
                    if hasattr(self, "_save_current_session"):
                        self._save_current_session()'''

if old_fin_sync in ec_txt:
    ec_txt = ec_txt.replace(old_fin_sync, new_fin_sync)
    print("✓ [ui/controllers/engineer_controller.py] Финализация задачи переведена на обновление единственной монолитной записи.")

ec_p.write_text(ec_txt, encoding="utf-8")

print("\n>>> ВСЕ ПРАВКИ УСПЕШНО ПРИМЕНЕНЫ! ЗАПУСКАЕМ ПРОВЕРКУ... <<<")
