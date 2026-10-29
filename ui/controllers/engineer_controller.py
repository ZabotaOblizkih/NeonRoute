"""
Engineer Controller (Mixin) for NeonRoute MainWindow.
Encapsulates all logic, UI toggles, dispatching, and tool callbacks
for the autonomous Engineering Mode and Workspace Sandbox.
"""

import os
import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import QTimer
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine
from services.task_queue import InferenceTask
from services.session_manager import SessionManager
from services.workspace_launcher import WorkspaceLauncher
from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.EngineerController")

DOC_TASK_MARKERS = (
    ".xlsx", ".docx", ".xls", ".doc", ".py", ".pyw",
    "эксель", "excel", "таблиц", "ворд", "word", "отчет", "отчёт",
    "приказ", "договор", "контракт", "соглашени", "смета",
    "сформируй файл", "создай файл", "сделай файл", "создай документ",
    "сделай документ", "сформируй документ", "создай таблицу", "сделай таблицу",
    "выгрузи в файл", "сохрани в файл", "напиши скрипт", "создай скрипт",
    "сделай скрипт", "запусти скрипт", "напиши программу", "создай программу",
    "график", "диаграмм", "найди файл", "в каком файле", "поищи файл",
    "тетрис", "змейка", "сделай игру", "напиши игру", "создай игру", "код на питоне",
    "скрипт на питоне", "напиши код", "создай код", "поменяй в коде", "замени в файле", "исправь код", "исправь скрипт"
)


class EngineerControllerMixin:
    """Миксин для MainWindow, изолирующий все инженерные функции."""

    def is_engineer_mode_active(self) -> bool:
        """Возвращает True, если активен автономный контур Инженера."""
        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            raw_mode = str(self.current_session.get("mode") or "").lower()
            raw_type = str(self.current_session.get("type") or "").lower()
            return raw_mode in ("engineer", "engineering") or raw_type == "engineering"
        return bool(getattr(self, "_engineering_mode_active", False))

    def _activate_engineering_mode(self):
        self._engineering_mode_active = True
        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            self.current_session["mode"] = "assistant"
            self.current_session["type"] = "engineering"
            if hasattr(self, "_save_current_session"):
                self._save_current_session()
        logger.info("[NeonRoute] >>> РЕЖИМ 'ИНЖЕНЕР' АКТИВИРОВАН (Память LanceDB изолирована, ядро Эларии включено) <<<")

    def _deactivate_engineering_mode(self):
        self._engineering_mode_active = False
        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            self.current_session["mode"] = "assistant"
            self.current_session.pop("type", None)
            if hasattr(self, "_save_current_session"):
                self._save_current_session()
        logger.info("[NeonRoute] >>> РЕЖИМ 'ДИАЛОГ' АКТИВИРОВАН (Стандартный чат с памятью) <<<")

    def _create_engineering_chat(self):
        try:
            new_sess = SessionManager.create_session(
                mode="assistant",
                title="🛠️ Инженерная сессия"
            )
            new_sess["type"] = "engineering"
            SessionManager.save_session(new_sess)
            self._engineering_mode_active = True
            self._switch_to_session(new_sess["id"])
            logger.info(f"[NeonRoute] Создана инженерная сессия: {new_sess['id']}")
        except Exception as e:
            logger.error(f"[NeonRoute] Ошибка создания инженерной сессии: {e}", exc_info=True)

    def _is_engineering_session(self) -> bool:
        return self.is_engineer_mode_active()

    def _open_mode_model_settings_dialog(self):
        from ui.dialogs.engineer_settings_dialog import ModeModelSettingsDialog
        dlg = ModeModelSettingsDialog(parent=self)
        dlg.exec()

    def _open_engineer_settings_dialog(self):
        """Алиас для сохранения обратной совместимости."""
        self._open_mode_model_settings_dialog()

    def _switch_to_engineer_model(self, model_name: Optional[str] = None):
        eng_model = (model_name or self.app_config.get("engineer_model", "")).strip()
        if not eng_model:
            return

        def _worker():
            # 1. Выгрузка чекпоинта Forge через VRAMArbiter
            from services.vram_arbiter import VRAMArbiter
            VRAMArbiter.get_instance()._release_forge_vram()

            # 2. Чистая загрузка назначенной модели инженера (ensure_model_loaded проверяет под _reload_lock)
            if hasattr(self, "lm_client") and hasattr(self.lm_client, "ensure_model_loaded"):
                from services.subagent_engineer import SubagentEngineer
                try:
                    self.lm_client.ensure_model_loaded(
                        preferred_model=eng_model,
                        context_length=getattr(SubagentEngineer, "ENGINEER_CONTEXT_LENGTH", 28672),
                        gpu_ratio=getattr(SubagentEngineer, "ENGINEER_GPU_RATIO", "1.0")
                    )
                except Exception as e:
                    logger.warning(f"Ошибка загрузки модели инженера: {e}")

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _apply_engine_mode_styling(self, checked: bool):
        """Чистая отрисовка стилей кнопки инженера без сайд-эффектов."""
        if hasattr(self, "input_box") and self.input_box:
            self.input_box.set_engineer_mode(checked)
        if not hasattr(self, "btn_engine_mode") or not self.btn_engine_mode:
            return
        if checked:
            self.btn_engine_mode.setText("⚡ Инженер")
            self.btn_engine_mode.setToolTip("Режим «⚡ Инженер»: температура 0.15, точечные патчи и песочница")
            self.btn_engine_mode.setStyleSheet("""
                QPushButton#EngineModeButton {
                    border: 1px solid #00F0FF;
                    background: rgba(0, 240, 255, 0.15);
                    color: #00F0FF;
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QPushButton#EngineModeButton:hover {
                    background: rgba(0, 240, 255, 0.25);
                    border: 1px solid #38BDF8;
                    color: #FFFFFF;
                }
            """)
        else:
            self.btn_engine_mode.setText("💬 Диалог")
            self.btn_engine_mode.setToolTip("Режим «💬 Диалог»: свободное общение с памятью")
            self.btn_engine_mode.setStyleSheet("""
                QPushButton#EngineModeButton {
                    background: rgba(255, 255, 255, 0.05);
                    color: #94A3B8;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    padding: 4px 10px;
                    font-size: 12px;
                    font-weight: 500;
                }
                QPushButton#EngineModeButton:hover {
                    background: rgba(255, 255, 255, 0.10);
                    border: 1px solid rgba(255, 255, 255, 0.25);
                    color: #E2E8F0;
                }
            """)

    def _on_engine_mode_toggled(self, checked: bool):
        if getattr(self, "is_busy", False):
            if hasattr(self, "btn_engine_mode") and self.btn_engine_mode:
                self.btn_engine_mode.blockSignals(True)
                self.btn_engine_mode.setChecked(not checked)
                self.btn_engine_mode.blockSignals(False)
            if hasattr(self, "statusBar") and self.statusBar():
                self.statusBar().showMessage("Дождитесь завершения текущего ответа перед сменой режима.", 3500)
            return

        from services.mode_coordinator import AppMode
        target = AppMode.ENGINEER if checked else AppMode.ASSISTANT
        if hasattr(self, "mode_coordinator"):
            self.mode_coordinator.transition_to(target)
        else:
            self._apply_engine_mode_styling(checked)

        if checked:
            eng_model = getattr(self, "app_config", {}).get("engineer_model", "").strip()
            if eng_model:
                if hasattr(self, "lm_combo") and self.lm_combo:
                    self.lm_combo.blockSignals(True)
                    idx = self.lm_combo.findText(eng_model)
                    if idx >= 0:
                        self.lm_combo.setCurrentIndex(idx)
                    else:
                        self.lm_combo.addItem(eng_model)
                        self.lm_combo.setCurrentIndex(self.lm_combo.count() - 1)
                    self.lm_combo.blockSignals(False)
                self.active_lms_model = eng_model
                if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
                    self.current_session["model_id"] = eng_model
                    if hasattr(self, "_save_current_session"):
                        self._save_current_session()


    def _execute_workspace_script(self, script_path: str):
        inspector = getattr(self, "workspace_inspector", None)
        if inspector:
            inspector.mark_running(f"Запуск {Path(script_path).name}...")
        ok, msg = WorkspaceLauncher.execute_file(script_path)
        self.statusBar().showMessage(msg, 3500)
        if inspector:
            if ok:
                inspector.mark_success()
            else:
                line_num = 0
                m_line = re.search(r'line\s+(\d+)', msg, re.IGNORECASE)
                if m_line:
                    try:
                        line_num = int(m_line.group(1))
                    except ValueError:
                        line_num = 0
                inspector.show_error(Path(script_path).name, line_num, msg)

    def _is_ephemeral_doc_request(self, user_query: str, has_vision: bool = False) -> bool:
        if has_vision or getattr(self, "current_mode", "assistant") == "roleplay":
            return False
        lower_t = (user_query or "").lower().strip()
        if lower_t.startswith(('!art', '!арт', '!img', '!картинка')):
            return False
        from services.subagent_engineer import SubagentEngineer
        if SubagentEngineer._is_document_task(lower_t):
            return True
        return any(m in lower_t for m in DOC_TASK_MARKERS)

    def _dispatch_ephemeral_doc_worker(
        self,
        task_prompt: str,
        attached_files: Optional[List[Path]] = None,
        image_path: Optional[str] = None
    ) -> bool:
        from ui.controllers.chat_flow_controller import _resolve_target_image
        if not image_path:
            has_image_attached = bool(attached_files and any(Path(f).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') for f in attached_files))
            has_mockup_keyword = any(k in task_prompt.lower() for k in ("макет", "скриншот", "картинк", "рисунок", "по фото", "образц"))
            if has_image_attached or has_mockup_keyword:
                image_path = _resolve_target_image(task_prompt)

        context_hint = ""
        if attached_files:
            context_hint = Path(attached_files[-1]).name
        elif self.current_session and self.current_session.get("messages"):
            for m in reversed(self.current_session["messages"]):
                fps = m.get("file_paths", [])
                if fps:
                    context_hint = Path(fps[-1]).name
                    break

        if image_path and not context_hint:
            context_hint = f"Макет: {Path(image_path).name}"

        active_sid = self.current_session.get("id") if getattr(self, "current_session", None) else None
        # Не меняем глобальное состояние SandboxManager в UI-потоке до старта воркера
        ws_dir = SandboxManager.get_workspace_dir()
        target_path = None
        doc_name = ""
        action_name = ""
        is_analysis = False

        from services.subagent_engineer import SubagentEngineer
        is_doc_task = SubagentEngineer._is_document_task(task_prompt)
        lower_prompt = task_prompt.lower()

        # 1. Поиск вложенного изображения (макета)
        if image_path:
            action_name = f"🎨 Создание по макету «{Path(image_path).name}»"
            context_hint = f"Макет: {Path(image_path).name}"

        # 2. Приоритетная обработка генерации таблиц, отчетов и офисных документов (без перезаписи намерений)
        elif is_doc_task:
            m_file = re.search(r'\b([a-zA-Z0-9_\-а-яА-ЯёЁ]+\.(?:xlsx|docx|doc|pdf|csv))\b', task_prompt, re.I)
            if m_file:
                doc_name = m_file.group(1)
                if (ws_dir / doc_name).is_file():
                    target_path = ws_dir / doc_name

            target_exists = target_path is not None and target_path.is_file()
            is_modifying = any(k in lower_prompt for k in ("обнови", "добавь в", "измени", "исправь", "дополни", "переделай"))

            if target_exists and is_modifying:
                ext = target_path.suffix.lower()
                if ext in ('.xlsx', '.xls', '.csv'):
                    action_name = f"📊 Обновление таблицы «{doc_name}»"
                else:
                    action_name = f"📝 Модификация документа «{doc_name}»"
                context_hint = doc_name
            elif doc_name:
                ext = Path(doc_name).suffix.lower()
                if ext in ('.xlsx', '.xls', '.csv'):
                    action_name = f"📊 Создание таблицы «{doc_name}»"
                else:
                    action_name = f"📝 Создание документа «{doc_name}»"
                context_hint = doc_name
            else:
                if any(k in lower_prompt for k in (".xlsx", ".xls", "эксель", "excel", "таблиц", "смет", "ведомост")):
                    action_name = "📊 Создание таблицы (Excel)"
                elif any(k in lower_prompt for k in (".docx", ".doc", "ворд", "word", "приказ", "договор")):
                    action_name = "📝 Создание документа (Word)"
                elif any(k in lower_prompt for k in ("отчет", "отчёт")):
                    action_name = "📊 Формирование отчета"
                else:
                    action_name = "📄 Генерация документа"
                context_hint = ""
            is_analysis = False

        # 3. Анализ Traceback и ошибок рантайма (Zero-Guesswork)
        elif any(k in lower_prompt for k in ("traceback", "exception")) or (
            any(k in lower_prompt for k in ("error", "ошибк")) and re.search(r'File\s+["\'][^"\']+["\'],\s+line\s+\d+', task_prompt, re.I)
        ):
            m_tb = re.search(r'File\s+["\']([^"\']+\.(?:pyw?|py))["\'],\s+line\s+(\d+)', task_prompt, re.I)
            m_err = re.search(r'\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning))\b', task_prompt)
            err_type = m_err.group(1) if m_err else "Сбой"
            tb_file = Path(m_tb.group(1)).name if m_tb else ""
            if not tb_file:
                m_f = re.search(r'\b([a-zA-Z0-9_\-]+\.(?:pyw?|py|xlsx|docx))\b', task_prompt, re.I)
                tb_file = m_f.group(1) if m_f else ""

            if tb_file and (ws_dir / tb_file).is_file():
                target_path = ws_dir / tb_file
                doc_name = tb_file
                action_name = f"🐞 Отладка: {err_type} в {tb_file}"
                context_hint = tb_file
            else:
                action_name = f"🐞 Отладка: {err_type}"
                context_hint = tb_file or context_hint or ""

        # 4. Поиск файлов по содержимому (Discovery)
        elif any(dm in lower_prompt for dm in ("найди файл", "в каком файле", "поищи файл", "где упомина", "в каких документах")):
            action_name = "🔍 Поиск файлов по содержимому"
            context_hint = "РЕЖИМ ПОИСКА: найди файлы и выведи краткие совпадения."
            is_analysis = True

        # 5. Анализ существующего файла (Read-Only)
        elif any(av in lower_prompt for av in ("что делает", "проанализируй", "разбери код", "прочитай файл", "покажи содержимое")) and not any(cg in lower_prompt for cg in ("создай", "сделай", "сформируй", "напиши", "построй")):
            m_file = re.search(r'\b([a-zA-Z0-9_\-а-яА-ЯёЁ]+\.(?:xlsx|docx|doc|pdf|csv|py|pyw))\b', task_prompt, re.I)
            doc_name = m_file.group(1) if m_file else ""
            if doc_name and (ws_dir / doc_name).is_file():
                target_path = ws_dir / doc_name
            action_name = f"📖 Анализ данных «{doc_name or 'файла'}»"
            context_hint = f"{doc_name} (РЕЖИМ ЧТЕНИЯ)" if doc_name else ""
            is_analysis = True

        # 6. Стандартная работа с файлом или новый проект
        else:
            m_file = re.search(r'\b([a-zA-Z0-9_\-а-яА-ЯёЁ]+\.(?:xlsx|docx|doc|pdf|csv|py|pyw))\b', task_prompt, re.I)
            if m_file:
                doc_name = m_file.group(1)
                if (ws_dir / doc_name).is_file():
                    target_path = ws_dir / doc_name

            # Проверка существующих файлов в workspace строго по границам слова
            if not target_path and ws_dir.exists():
                for f in ws_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in ('.docx', '.xlsx', '.py', '.pyw'):
                        if re.search(r'\b' + re.escape(f.name) + r'\b', task_prompt, re.I) or \
                           (len(f.stem) >= 4 and re.search(r'\b' + re.escape(f.stem) + r'\b', task_prompt, re.I)):
                            target_path = f
                            doc_name = f.name
                            break

            target_exists = target_path is not None and target_path.is_file()

            if target_exists:
                ext = target_path.suffix.lower()
                if ext in ('.py', '.pyw'):
                    action_name = f"⚡ Доработка: «{doc_name}»"
                elif ext == '.xlsx':
                    action_name = f"📊 Обновление таблицы «{doc_name}»"
                else:
                    action_name = f"📝 Модификация «{doc_name}»"
                context_hint = doc_name
            elif doc_name:
                ext = Path(doc_name).suffix.lower()
                if ext in ('.py', '.pyw'):
                    action_name = f"🛠️ Создание скрипта «{doc_name}»"
                elif ext == '.xlsx':
                    action_name = f"📊 Создание таблицы «{doc_name}»"
                else:
                    action_name = f"📝 Создание документа «{doc_name}»"
                context_hint = doc_name
            else:
                first_sentence = re.split(r'[.!?\n]', task_prompt.strip())[0].strip()
                first_clean = re.sub(r'^[#*_\-\s]+', '', first_sentence).strip()
                if len(first_clean) > 35:
                    short_subj = first_clean[:35].rsplit(' ', 1)[0].rstrip(' ,:.-') + "..."
                else:
                    short_subj = first_clean.rstrip(' ,:.-')
                action_name = f"🛠️ Задача: «{short_subj or 'Новый проект'}»"
                context_hint = ""

        session_history = []
        if self.current_session and self.current_session.get("messages"):
            raw_msgs = self.current_session["messages"][-6:]
            for m in raw_msgs:
                role = "user" if m.get("is_user") or m.get("role") == "user" else "assistant"
                txt = str(m.get("text") or m.get("content") or "").strip()
                if txt.startswith(("<<TOOL:", "[Tool", "[Системный", "[код]", "РЕКОМЕНДАЦИИ")):
                    continue
                txt_clean = re.sub(r'```[\s\S]*?```', '', txt).strip()
                if len(txt_clean) > 250:
                    txt_clean = txt_clean[:250] + "..."
                if txt_clean and txt_clean != task_prompt.strip():
                    session_history.append({"role": role, "content": txt_clean})

        def _worker_fn(
            prompt: str,
            hint: str,
            is_analysis: bool = False,
            image_path: Optional[str] = None,
            session_history: Optional[List[Dict[str, str]]] = None,
            session_id: Optional[str] = None
        ) -> dict:
            from services.sandbox_manager import SandboxManager
            target_sid = session_id or (self.current_session.get("id") if getattr(self, "current_session", None) else None)
            try:
                on_msg_cb = lambda *args: getattr(self, "_sig_engineer_plan_card", None) and self._sig_engineer_plan_card.emit(args[-1])
                on_status_cb = lambda st, fn, ln, em: getattr(self, "_sig_inspector_status", None) and self._sig_inspector_status.emit(st, fn, ln, em)
                with SandboxManager.session_scope(target_sid):
                    sm = SessionManager(lm_client=getattr(self, "lm_client", None))
                    return sm.run_ephemeral_engineer_worker(
                        task_prompt=prompt,
                        context_hint=hint,
                        is_analysis=is_analysis,
                        image_path=image_path,
                        session_history=session_history,
                        on_message_callback=on_msg_cb,
                        on_status_callback=on_status_cb,
                        inspector=getattr(self, "workspace_inspector", None)
                    )
            except Exception as e:
                logger.error(f"Error in engineer worker: {e}", exc_info=True)
                return {
                    "success": False,
                    "artifact_path": None,
                    "file_name": "",
                    "stdout": "",
                    "stderr": str(e),
                    "summary": f"Исключение при выполнении задачи: {e}"
                }

        abort_cb = getattr(self.lm_client, "abort", None) if hasattr(self, "lm_client") and self.lm_client else None

        task = InferenceTask(
            name=action_name,
            task_type="file",
            func=_worker_fn,
            kwargs={
                "prompt": task_prompt,
                "hint": context_hint,
                "is_analysis": is_analysis,
                "image_path": image_path,
                "session_history": session_history,
                "session_id": active_sid
            },
            abort_fn=abort_cb,
            metadata={
                "is_ephemeral_doc": True,
                "is_analysis": is_analysis,
                "bubble": None,
                "task_prompt": task_prompt,
                "context_hint": context_hint,
                "image_path": image_path
            }
        )
        if hasattr(self, "workspace_inspector") and self.workspace_inspector:
            self.workspace_inspector.mark_running()
        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)
        self.statusBar().showMessage(f"Элария выполняет: {action_name}...", 4000)
        return True

    def _handle_tool_call_flow(self, text: str):
        from ui.workers.tool_execution_worker import ToolExecutionWorker
        self._tool_executed_this_turn = True
        self._tool_loop_count = getattr(self, "_tool_loop_count", 0) + 1
        if self._tool_loop_count > 5:
            logger.warning("Tool loop recursion limit (5) reached.")
            self._tool_loop_count = 0
            return

        tool_calls = ToolEngine.parse_tool_calls(text)
        if not tool_calls:
            return

        sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
        if getattr(self, "_turn_bubble", None) is None:
            self._turn_bubble = self._append_message(sender_name, "", is_user=False)
        else:
            try:
                self._turn_bubble.set_text("")
            except RuntimeError:
                self._turn_bubble = self._append_message(sender_name, "", is_user=False)

        primary_file = None
        for call in tool_calls:
            call_name = getattr(call, "name", "").lower()
            params = getattr(call, "params", {})
            if call_name in ("write_file", "patch_file"):
                raw_fn = params.get("filename") or params.get("path") or params.get("file") or "script.py"
                clean_fn = SandboxManager.clean_workspace_filename(raw_fn)
                primary_file = clean_fn
                f_path = SandboxManager.get_workspace_dir() / clean_fn
                self._turn_bubble.add_workspace_file_card(f_path, on_run=self._execute_workspace_script)
                self._update_message_attachment(self._turn_bubble, f_path)

        curr_sid = self.current_session.get("id") if self.current_session else None
        self._tool_worker = ToolExecutionWorker(
            tool_calls=tool_calls,
            target_widget=self._turn_bubble,
            session_id=curr_sid
        )
        self._tool_worker.finished.connect(self._on_tool_execution_finished)
        self._tool_worker.start()

    def _on_tool_execution_finished(self, data: Any):
        if not data:
            logger.warning("[EngineerController] Получен пустой ответ от ToolExecutionWorker")
            if hasattr(self, "_force_reset_busy_state"):
                self._force_reset_busy_state()
            return

        # Универсальное извлечение статуса успеха и параметров
        if isinstance(data, dict):
            success = data.get("overall_success", data.get("success", False))
            results = data.get("results", [])
            error_msg = data.get("error", "")
            tool_name = data.get("tool_name", "")
            artifacts = data.get("artifacts", [])
        else:
            success = getattr(data, "success", getattr(data, "overall_success", False))
            results = getattr(data, "results", [])
            error_msg = getattr(data, "error", "")
            tool_name = getattr(data, "tool_name", "")
            artifacts = getattr(data, "artifacts", [])

        try:
            self._tool_executed_this_turn = True
            if hasattr(self, "_remove_typing_loader"):
                self._remove_typing_loader()

            # Если передан единичный результат через ToolResult без списка results
            if not results:
                raw_out = getattr(data, "output", "") if not isinstance(data, dict) else data.get("result", "")
                results = [{
                    "tool_name": tool_name,
                    "result": error_msg if not success and error_msg else raw_out,
                    "success": success
                }]

            # Отображение карточек файлов для артефактов при их наличии
            if artifacts:
                for art in artifacts:
                    art_path = Path(art) if not isinstance(art, Path) else art
                    if art_path.exists() and hasattr(self, "_turn_bubble") and self._turn_bubble is not None:
                        if hasattr(self._turn_bubble, "add_workspace_file_card"):
                            self._turn_bubble.add_workspace_file_card(art_path, on_run=self._execute_workspace_script)
                        if hasattr(self, "_update_message_attachment"):
                            self._update_message_attachment(self._turn_bubble, art_path)

            single_artifact = getattr(data, "artifact_path", None) if not isinstance(data, dict) else data.get("artifact_path")
            if single_artifact:
                single_path = Path(single_artifact) if not isinstance(single_artifact, Path) else single_artifact
                if single_path.exists() and hasattr(self, "_turn_bubble") and self._turn_bubble is not None:
                    if hasattr(self._turn_bubble, "add_workspace_file_card"):
                        self._turn_bubble.add_workspace_file_card(single_path, on_run=self._execute_workspace_script)
                    if hasattr(self, "_update_message_attachment"):
                        self._update_message_attachment(self._turn_bubble, single_path)

            obs_parts = []
            for item in results:
                if isinstance(item, dict):
                    t_name = item.get("tool_name", "")
                    t_res = item.get("result", item.get("output", ""))
                else:
                    t_name = getattr(item, "tool_name", "")
                    t_res = getattr(item, "output", getattr(item, "result", ""))
                obs_parts.append(f"[{t_name}]: {str(t_res)}")

            obs_payload = "[Tool Observation:\n" + "\n\n".join(obs_parts) + "\n]\n\n[Подтверди пользователю результат выполненных действий.]"
            if hasattr(self, "chat_history") and isinstance(self.chat_history, list):
                self.chat_history.append({"role": "user", "content": obs_payload})

            self._is_post_tool_synthesis = True
            self._synthesis_first_chunk = True
            if hasattr(self, "_enqueue_llm_task"):
                self._enqueue_llm_task(is_roleplay=False)
        except Exception as e:
            logger.error(f"[EngineerController] Ошибка при обработке результатов инструмента: {e}", exc_info=True)
        finally:
            if hasattr(self, "_force_reset_busy_state"):
                self._force_reset_busy_state()

    def _display_engineer_plan_card(self, card_text: str):
        if getattr(self, "typing_loader", None) is not None:
            self._remove_typing_loader()
        avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
        bubble = self._append_message("Элария", card_text, is_user=False, avatar_path=avatar)
        if hasattr(self, "_active_tasks"):
            for t in self._active_tasks.values():
                if t.metadata and t.metadata.get("is_ephemeral_doc"):
                    t.metadata["bubble"] = bubble
                    break
        if hasattr(self, "_sync_turn"):
            self._sync_turn(role="assistant", content=card_text, replace_last=False)
        self._scroll_to_bottom()

    def _handle_engineer_task_finished(self, task: InferenceTask, result: Any):
        inspector = getattr(self, "workspace_inspector", None)
        bubble = task.metadata.get("bubble")
        res = result if isinstance(result, dict) else {}
        success = bool(res.get("success", False))
        artifact_path = res.get("artifact_path")
        file_name = res.get("file_name", "")
        stderr = res.get("stderr", "")
        summary = res.get("summary", "")
        is_analysis = bool(task.metadata.get("is_analysis", False) or res.get("is_analysis", False))

        if inspector:
            if success:
                inspector.mark_success()
            else:
                err_text = stderr or summary or "Скрипт завершился с ошибкой"
                line_num = 0
                m_line = re.search(r'line\s+(\d+)', err_text, re.IGNORECASE)
                if m_line:
                    try:
                        line_num = int(m_line.group(1))
                    except ValueError:
                        line_num = 0
                inspector.show_error(file_name, line_num, err_text)

        if artifact_path:
            artifact_path = Path(artifact_path)
            file_name = file_name or artifact_path.name
            fin_text = f"✅ Файл `{file_name}` успешно сохранён и готов к работе."
            if bubble is not None:
                try:
                    if hasattr(bubble, "add_workspace_file_card"):
                        bubble.add_workspace_file_card(artifact_path, on_run=self._execute_workspace_script)
                    if hasattr(self, "_update_message_attachment"):
                        self._update_message_attachment(bubble, artifact_path)
                    if res.get("stdout") and str(res["stdout"]).strip() and hasattr(bubble, "add_console_spoiler"):
                        bubble.add_console_spoiler(title=f"Консоль: {file_name}", output=res["stdout"], is_error=False)
                    if hasattr(bubble, "set_text"):
                        bubble.set_text(fin_text)
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
            self._sync_turn(role="assistant", content=fin_text, replace_last=True)
        elif is_analysis:
            if not success and (stderr or "Ошибка" in summary):
                err_output = res.get("stderr") or stderr or ""
                err_msg = summary or err_output or "Неизвестная ошибка"
                fin_text = f"⚠️ Ошибка выполнения: {err_msg}"
                spoiler_title = f"Консоль: {file_name}" if file_name else "Ошибка анализа"
                avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
                b = self._append_message("Элария", fin_text, is_user=False, avatar_path=avatar)
                if err_output and hasattr(b, "add_console_spoiler"):
                    b.add_console_spoiler(title=spoiler_title, output=err_output, is_error=True)
                self._sync_turn(role="assistant", content=fin_text, replace_last=True)
            else:
                avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
                self._append_message("Элария", summary, is_user=False, avatar_path=avatar)
                self._sync_turn(role="assistant", content=summary, replace_last=True)
        else:
            err_output = res.get("stderr") or stderr or ""
            fin_text = f"⚠️ Не удалось выполнить задачу: {summary or err_output}"
            avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
            self._append_message("Элария", fin_text, is_user=False, avatar_path=avatar)
            self._sync_turn(role="assistant", content=fin_text, replace_last=True)

        self._scroll_to_bottom()
