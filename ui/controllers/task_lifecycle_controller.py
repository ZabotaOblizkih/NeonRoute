import os
import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from services.task_queue import InferenceTask
from services.session_manager import SessionManager
from services.memory_db import handle_memory_command
from services.tool_engine import ToolEngine
from ui.components.typing_loader import TypingLoaderWidget
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.TaskLifecycle")


class TaskLifecycleMixin:
    """Миксин для управления жизненным циклом и диспатчингом задач очереди TaskQueue."""

    def _enqueue_translation_task(self, text: str, target_message: ChatMessageWidget, is_greeting: bool = False):
        def _rollback_btn():
            if target_message and getattr(target_message, "btn_translate", None):
                target_message.btn_translate.setText("🌐 Перевести")

        if not text or not str(text).strip():
            _rollback_btn()
            return

        # Check if greeting translation is already cached: if present, use directly and skip queue
        char_data = self.current_session.get("character_data") if self.current_session else None
        char_first_mes = char_data.get("first_mes") if char_data else None
        if not is_greeting and char_first_mes and text == char_first_mes:
            is_greeting = True

        if is_greeting and char_data:
            cached_ru = char_data.get("first_mes_ru") or SessionManager.get_cached_greeting_ru(char_data.get("id"), char_data.get("name"))
            if cached_ru:
                char_data["first_mes_ru"] = cached_ru
                if target_message:
                    target_message.set_translation(cached_ru)
                    self._update_message_translation_in_session(target_message, cached_ru)
                logger.info("Using cached character greeting translation; skipped translation queue.")
                return

        # Code/scripts, system observations, tool outputs, and operational reports MUST NEVER be sent to translation
        has_code_markers = any(k in text for k in ("```", "def ", "class ", "import ", "print(", "return ", "<<TOOL:"))
        if has_code_markers:
            _rollback_btn()
            self.statusBar().showMessage("Код и служебные блоки не подлежат переводу.", 3000)
            return

        system_markers = [
            "[Системный результат",
            "[IMAGE_PROMPT",
            "[строки ",
            "[Код возврата:",
            "[Успешно]:",
            "STDERR:",
            "📖 Читаю файл",
            "📋 Копирую файл",
            "🔍 Ищу файлы",
            "⚡ Запуск скрипта",
            "🔧 Выполняю",
            "Консоль:"
        ]
        if ToolEngine.has_tool_call(text) or any(m in text for m in system_markers):
            _rollback_btn()
            self.statusBar().showMessage("Системные логи не подлежат переводу.", 3000)
            return

        latin_count = sum(1 for c in text if 'a' <= c.lower() <= 'z')
        cyrillic_count = sum(1 for c in text if 'а' <= c.lower() <= 'я')

        if cyrillic_count >= latin_count:
            task_title = "Перевод на английский"
            sys_prompt = (
                "You are a professional literary translator. Translate the text into natural English faithfully, "
                "preserving character voice, tone, formatting, and markdown. Respond ONLY with the translation."
            )
            user_prompt = f"Translate this text into natural English:\n\n{text}"
        else:
            task_title = "Перевод на русский"
            sys_prompt = (
                "Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.\n\n"
                "Правила перевода:\n"
                "1. Полнота: переведи весь предоставленный текст полностью от первого до последнего предложения, без сокращений и пересказов.\n"
                "2. Грамматика и род: строго сохраняй грамматический род участников сцены и персонажей по контексту повествования (не подменяй мужской род на женский и наоборот).\n"
                "3. Литературный слог: переводи выразительно и естественно, избегая калек машинного перевода.\n"
                "4. Форматирование: сохраняй исходные абзацы, кавычки, реплики и *курсив*. Выводи только готовый перевод без предисловий и комментариев."
            )
            user_prompt = f"Переведи следующий художественный текст на красивый русский литературный язык целиком без сокращений:\n\n{text}"

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt}
        ]

        model_name = (
            getattr(self.lm_client, "_last_loaded_model_id", None)
            or (self.lm_combo.currentText().strip() if hasattr(self, "lm_combo") and self.lm_combo else "")
            or getattr(self, "active_lms_model", "")
        )
        if model_name and model_name.startswith("default"):
            model_name = None

        from services.session_manager import get_mode_profile
        active_loaded_ctx = getattr(self.lm_client, "_current_loaded_ctx", None)
        if not active_loaded_ctx:
            active_loaded_ctx = get_mode_profile(self.current_mode).get("context_length", 24576)

        tr_cfg = self.app_config.get("translation_settings", {})
        tr_temp = float(tr_cfg.get("temperature", 0.20))
        max_limit = int(tr_cfg.get("max_tokens_limit", 4096))
        tr_budget = SessionManager.calculate_translation_budget(text, max_limit=max_limit)

        task = InferenceTask(
            name=task_title,
            task_type="translation",
            func=self.lm_client.chat_completion,
            kwargs={
                "messages": messages,
                "model": model_name,
                "temperature": tr_temp,
                "max_tokens": tr_budget,
                "context_length": int(active_loaded_ctx),
                "stream": False,
                "emit_signal": False,
                "is_roleplay": (self.current_mode == "roleplay")
            },
            abort_fn=self.lm_client.abort,
            metadata={
                "target_widget": target_message,
                "is_translation": True,
                "original_text": text,
                "is_greeting": is_greeting,
                "char_id": char_data.get("id") if char_data else None
            }
        )

        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _route_and_enqueue(
        self,
        prompt: str,
        attached_files: Optional[List[Path]] = None,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        user_query: Optional[str] = None,
        art_guidance: Optional[str] = None
    ):
        """Analyzes intent and enqueues sequential task."""
        # 0. Intercept Long-Term User Memory Commands (View, Delete by ID/Keyword, Clear All, Save)
        is_memory_cmd, reply_text = handle_memory_command(prompt)
        if is_memory_cmd:
            sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
            avatar = self.active_character.avatar_path if (self.current_mode == "roleplay" and self.active_character) else None

            self._append_message(sender_name, reply_text, is_user=False, avatar_path=avatar)
            self._sync_turn(role="assistant", content=reply_text, replace_last=False, trigger_memory=False)
            return

        # Интенты картинок проверяются СТРОГО по запросу пользователя, а не по телу файла
        query_text = user_query if user_query is not None else prompt
        lower = query_text.lower() if query_text else ""

        # If vision content is present, route to vision LLM
        if vision_content:
            self._enqueue_llm_task(is_roleplay=(self.current_mode == "roleplay"), vision_content=vision_content, art_guidance=art_guidance)
            return

        # Direct art trigger check (strictly for roleplay or assistant with engineer mode OFF)
        is_eng = self.is_engineer_mode_active()
        if not is_eng and self.current_mode in ("roleplay", "assistant"):
            direct_prefixes = ("!art ", "!арт ", "!img ", "!картинка ", "!art", "!арт", "!img", "!картинка")
            is_direct = lower.startswith(direct_prefixes)
            if is_direct:
                logger.info(f"[Арт-команда] Получен запрос прямого рендера: '{prompt.strip()}'")
                # 1. Strip trigger prefix from prompt
                trimmed_prompt = prompt
                for pfx in sorted(direct_prefixes, key=len, reverse=True):
                    if lower.startswith(pfx):
                        trimmed_prompt = prompt[len(pfx):].strip()
                        break

                if not trimmed_prompt:
                    user_msg = "⚠️ **Укажите описание изображения после триггера прямого рендера** (например: `!art киберпанк девушка` или `!img neon street`)."
                    sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
                    self._append_message(sender_name, user_msg, is_user=False, is_error=True)
                    return

                # 2. Check Forge status
                if not self.pill_forge.is_online:
                    logger.warning(f"[SD Forge] Отклонен прямой рендер '{trimmed_prompt}': сервер Forge офлайн (порт 7860 недоступен).")
                    user_msg = (
                        "⚠️ **SD WebUI Forge не запущен или еще загружается.**\n\n"
                        "Запустите Forge через кнопку в нижней панели и дождитесь зеленого индикатора."
                    )
                    sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
                    self._append_message(sender_name, user_msg, is_user=False, is_error=True)
                    self.statusBar().showMessage("Forge офлайн. Запустите сервер в нижней панели.", 6000)
                    return

                # 3 & 4. Build direct plan and directly enqueue task to TaskQueue
                self._enqueue_direct_image_task(trimmed_prompt)
                # 5. Return
                return

        # 1. Roleplay Mode: Изолированный контроллер ролевого режима
        if self.current_mode == "roleplay":
            if hasattr(self, "_enqueue_roleplay_llm_task"):
                self._enqueue_roleplay_llm_task(vision_content=vision_content, art_guidance=art_guidance)
            else:
                self._enqueue_llm_task(is_roleplay=True, vision_content=vision_content, art_guidance=art_guidance)
            return

        # 2. Engineer Mode: Приоритетный перехват активного инженерного контура
        is_eng = self.is_engineer_mode_active()
        if is_eng:
            if hasattr(self, "_dispatch_ephemeral_doc_worker"):
                if self._dispatch_ephemeral_doc_worker(task_prompt=prompt, attached_files=attached_files):
                    return

        # 3. Assistant Mode: Изолированный контроллер Ассистента (Элария)
        is_excel = any(k in lower for k in ["excel", "эксель", "таблиц", "смет", "xlsx", "расчет"])
        is_word = any(k in lower for k in ["word", "ворд", "docx", "документ", "отчет", "реферат", "стать"])

        if hasattr(self, "_enqueue_assistant_llm_task"):
            self._enqueue_assistant_llm_task(is_excel=is_excel, is_word=is_word, vision_content=vision_content, art_guidance=art_guidance)
        else:
            self._enqueue_llm_task(is_roleplay=False, is_excel=is_excel, is_word=is_word, vision_content=vision_content, art_guidance=art_guidance)

    def _enqueue_tts_task(self, text: str, target_message: ChatMessageWidget):
        task = InferenceTask(
            name="F5-TTS Озвучка",
            task_type="tts",
            func=self.tts_service.synthesize,
            kwargs={"text": text},
            abort_fn=self.tts_service.abort,
            metadata={"target_widget": target_message}
        )
        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _on_task_started(self, task_id: str, name: str, task_type: str):
        task = self._active_tasks.get(task_id) if hasattr(self, "_active_tasks") else None
        is_silent = bool(task and (task.metadata.get("silent", False) or getattr(task, "task_type", None) == "memory"))
        if is_silent or name == "Возврат LLM в VRAM" or task_type == "memory":
            return
        self.is_busy = True
        self.input_box.set_busy(True)
        self.statusBar().showMessage(f"Выполняется: {name}...")

        # Remove any existing typing loader
        self._remove_typing_loader()

        # Determine adaptive roleplay / assistant name
        if self.current_mode == "roleplay":
            if self.active_character:
                sender_name = self.active_character.name
                avatar_path = self.active_character.avatar_path
            else:
                sender_name = self.current_session.get("title") if self.current_session else "Персонаж"
                avatar_path = None
        else:
            sender_name = "Элария"
            avatar_path = self._get_current_avatar_path()

        if task_type == "translation":
            icon = "🌐"
            action_text = "Выполняется перевод"
        elif task_type in ("scene_art", "image"):
            icon = "🎨"
            action_text = "Генерация иллюстрации"
        elif task_type == "tts":
            icon = "🔊"
            action_text = "Озвучивание сообщения"
        elif task_type == "file":
            icon = "⏳"
            action_text = name
        else:
            if self.current_mode == "roleplay":
                icon = "💭"
                action_text = f"{sender_name} печатает"
            else:
                icon = "⚡"
                action_text = "Элария думает"

        # Update dynamic header typing status
        if hasattr(self, "header_controller"):
            self.header_controller.set_task_state(task_type, action_text)

        task = self._active_tasks.get(task_id)
        is_attached_to_widget = bool(task and task.metadata and task.metadata.get("target_widget"))
        if task_type in ("scene_art", "image") and is_attached_to_widget:
            # Не создаем TypingLoaderWidget внизу ленты, так как статус отображается прямо на кнопке-чипе сообщения!
            return

        self.typing_loader = TypingLoaderWidget(
            sender_name=sender_name,
            action_text=action_text,
            avatar_path=avatar_path,
            icon=icon,
            parent=self.chat_container
        )
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self.typing_loader, alignment=Qt.AlignmentFlag.AlignTop)
        self._scroll_to_bottom()

    def _remove_typing_loader(self):
        if self.typing_loader is not None:
            try:
                self.chat_layout.removeWidget(self.typing_loader)
                self.typing_loader.stop_animation()
                self.typing_loader.deleteLater()
            except Exception as e:
                logger.debug(f"Error removing typing loader: {e}")
            finally:
                self.typing_loader = None

    def _on_task_finished(self, task_id: str, result: any):
        """Делегирует завершение задачи в единый диспетчер ChatFlowControllerMixin."""
        super()._on_task_finished(task_id, result)
        if hasattr(self, "header_controller"):
            self.header_controller.clear_active_task()

    def _refresh_lm_models(self):
        """Обновляет список моделей LM Studio из CLI."""
        if hasattr(self, "_refresh_models"):
            self._refresh_models()

    def _on_task_failed(self, task_id: str, error: str):
        task = self._active_tasks.get(task_id)
        self._force_reset_busy_state(task_id)
        if hasattr(self, "header_controller"):
            self.header_controller.clear_active_task()
        self._update_header_for_current_state()

        # Откат состояния кнопки арт-сцены при сбое Forge
        if task and task.metadata and task.metadata.get("is_scene_art"):
            target_widget = task.metadata.get("target_widget")
            if target_widget and hasattr(target_widget, "set_scene_art_busy"):
                target_widget.set_scene_art_busy(False)

        # Откат состояния кнопки бабла при ошибке перевода
        if task and task.metadata and task.metadata.get("is_translation"):
            target_widget = task.metadata.get("target_widget")
            if target_widget and getattr(target_widget, "btn_translate", None):
                target_widget.btn_translate.setText("🌐 Перевести")
            self.statusBar().showMessage(f"Ошибка перевода: {error}", 4000)
            return

        error_msg = str(error)

        # Проверка на удаленную/отсутствующую модель
        is_model_missing = (
            "не найдена в хранилище" in error_msg
            or "No models loaded" in error_msg
            or "Model not found" in error_msg
            or "ModelNotFoundError" in error_msg
            or "не найдена на диске" in error_msg
        )

        if is_model_missing:
            # 1. Извлекаем имя сбойной модели
            missing_name = getattr(self.lm_combo, "currentText", lambda: "выбранная")().strip()
            if (not missing_name or missing_name.startswith("default")) and "Модель '" in error_msg:
                try:
                    import re
                    m = re.search(r"Модель '([^']+)' не найдена", error_msg)
                    if m:
                        missing_name = m.group(1)
                except Exception:
                    pass

            # 2. Разблокируем выбор модели для пользователя
            if hasattr(self, "lm_combo") and self.lm_combo:
                self.lm_combo.setEnabled(True)
                self.lm_combo.setToolTip("Выберите другую доступную модель")
            if getattr(self, "current_session", None):
                self.current_session["is_model_locked"] = False
                if hasattr(self, "_save_current_session"):
                    self._save_current_session()

            # 3. Обновляем список моделей из CLI
            if hasattr(self, "_refresh_lm_models"):
                self._refresh_lm_models()
            elif hasattr(self, "_refresh_models"):
                self._refresh_models()

            # 4. Выводим аккуратное сообщение пользователю в чат вместо модального креста
            friendly_text = f"⚠️ Модель «{missing_name}» не найдена на диске или была удалена.\nПожалуйста, выберите доступную модель в списке выше."
            sender_name = getattr(self.active_character, "name", "Элария") if hasattr(self, "active_character") and self.active_character else "Элария"

            if getattr(self, "_turn_bubble", None) is not None:
                try:
                    self._turn_bubble.set_text(friendly_text)
                except Exception:
                    self._append_message(sender_name, friendly_text, is_user=False)
                self._turn_bubble = None
            else:
                self._append_message(sender_name, friendly_text, is_user=False)

            self.statusBar().showMessage(f"Модель «{missing_name}» не найдена.", 5000)
            return

        # Проверка на ошибку шаблона при смене модели в активном диалоге
        is_model_switched_template_err = (
            "Вы сменили модель, начните новый диалог, чтобы продолжить" in error_msg
            or "Jinja Exception" in error_msg
            or "conversation roles must alternate" in error_msg
            or "Unable to generate parser for this template" in error_msg
        )
        if is_model_switched_template_err:
            friendly_switch_text = "Вы сменили модель, начните новый диалог, чтобы продолжить"
            logger.info(f"[NeonRoute.TaskLifecycle] Заглушка смены модели: {friendly_switch_text}")
            sender_name = (
                getattr(self.active_character, "name", "Элария")
                if hasattr(self, "active_character") and self.active_character
                else "Элария"
            )
            if getattr(self, "_turn_bubble", None) is not None:
                try:
                    self._turn_bubble.set_text(friendly_switch_text)
                except Exception:
                    self._append_message(sender_name, friendly_switch_text, is_user=False)
                self._turn_bubble = None
            else:
                self._append_message(sender_name, friendly_switch_text, is_user=False)

            self.statusBar().showMessage(friendly_switch_text, 6000)
            return

        # Для всех остальных непредвиденных ошибок сохраняется стандартный лог:
        logger.error(f"[NeonRoute.TaskQueue] Task failed: {task_id} - {error_msg}")
        if "ForgeOfflineError" in error_msg or "SD WebUI Forge не запущен" in error_msg:
            logger.warning(f"[SD Forge] Ошибка выполнения задачи (Forge офлайн): {error_msg}")
            user_msg = (
                "⚠️ **SD WebUI Forge не запущен или еще загружается.**\n\n"
                "Запустите Forge через кнопку в нижней панели и дождитесь зеленого индикатора."
            )
            sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
            self._append_message(sender_name, user_msg, is_user=False, is_error=True)
            self.statusBar().showMessage("SD WebUI Forge недоступен. Запустите Forge в нижней панели.", 6000)
        elif "не поддерживает распознавание изображений" in error_msg or "Vision-модель" in error_msg:
            self._append_message("Система (Vision)", error_msg, is_user=False, is_error=True)
            self.statusBar().showMessage("Выберите Vision-модель для анализа картинок.", 6000)
        else:
            err_display = f"❌ **Ошибка выполнения задачи:**\n\n```text\n{error_msg}\n```"
            self._append_message("Система (Ошибка)", err_display, is_user=False, is_error=True)
            self.statusBar().showMessage("Ошибка выполнения задачи.", 6000)

    def _on_task_aborted(self, task_id: str):
        task = self._active_tasks.get(task_id)
        self._force_reset_busy_state(task_id)
        if hasattr(self, "header_controller"):
            self.header_controller.clear_active_task()
        self._update_header_for_current_state()

        if task and task.metadata and task.metadata.get("is_scene_art"):
            target_widget = task.metadata.get("target_widget")
            if target_widget and hasattr(target_widget, "set_scene_art_busy"):
                target_widget.set_scene_art_busy(False)

        if task and task.metadata and task.metadata.get("is_translation"):
            target_widget = task.metadata.get("target_widget")
            if target_widget and getattr(target_widget, "btn_translate", None):
                target_widget.btn_translate.setText("🌐 Перевести")

        if getattr(self, "_antiloop_aborted", False):
            self._antiloop_aborted = False
            self.statusBar().showMessage("Генерация завершена (срез зацикливания текста).", 4000)
            return

        self._append_message("Система", "⏹️ Операция остановлена пользователем.", is_user=False)
        self.statusBar().showMessage("Операция прервана.", 4000)

    def _on_queue_empty(self):
        self._force_reset_busy_state()
        if hasattr(self, "header_controller"):
            self.header_controller.clear_active_task()
        self._update_header_for_current_state()

    def _on_stop_clicked(self):
        self.statusBar().showMessage("Остановка активной задачи...")
        if hasattr(self, "task_queue") and self.task_queue:
            self.task_queue.abort_current()
        if hasattr(self, "abort_generation"):
            self.abort_generation()
        self._force_reset_busy_state()


__all__ = ["TaskLifecycleMixin"]
