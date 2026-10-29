"""
Chat Flow & Generation Controller (Mixin) for NeonRoute MainWindow.
Handles message dispatch, context & system prompt formulation, streaming chunk processing,
TaskQueue background coordination, Forge image triggers, and tool execution callbacks.
"""

import os
import re
import time
import json
import uuid
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List, Dict, Any, Union, Callable

from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QMessageBox, QApplication, QFileDialog
)
from PyQt6.QtGui import QIcon

import config
from config import BASE_DIR, DATA_DIR
CHARACTERS_DIR = getattr(config, "CHARACTERS_DIR", Path("characters"))
ASSETS_DIR = getattr(config, "ASSETS_DIR", getattr(config, "BASE_DIR", Path(".")) / "assets")

from services.session_manager import SessionManager
from services.roleplay_state_manager import RoleplayStateManager
from config import WORKSPACE_DIR
from utils.config_manager import ConfigManager

logger = logging.getLogger("NeonRoute.ChatFlowController")

try:
    from services.document_parser import DocumentParser
except ImportError:
    DocumentParser = None

try:
    from services.sandbox_manager import SandboxManager
except ImportError:
    SandboxManager = None

try:
    from services.tool_engine import ToolEngine
except ImportError:
    ToolEngine = None

from services.task_queue import InferenceTask

if TYPE_CHECKING:
    from ui.main_window import MainWindow

from services.art_director import GenerationPlan, VisualIntent, TargetSubject
from ui.components.chat_bubble import ChatMessageWidget
import base64

RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)


def _resolve_target_image(prompt: str) -> Optional[str]:
    from services.sandbox_manager import SandboxManager
    prompt_str = prompt or ""
    prompt_lower = prompt_str.lower()

    # 0. Проверка явного пути к файлу в тексте запроса
    m_path = re.search(r'([a-zA-Z]:[\\/][^\s<>"\'\n\r\t]+?\.(?:png|jpg|jpeg|webp))', prompt_str, re.IGNORECASE)
    if m_path:
        cand_p = Path(m_path.group(1).strip())
        if cand_p.is_file():
            return str(cand_p.resolve())

    workspace_dir = SandboxManager.get_workspace_dir()
    if not workspace_dir.exists():
        return None

    image_extensions = ('.png', '.jpg', '.jpeg', '.webp')
    image_files = [f for f in workspace_dir.iterdir() if f.is_file() and f.suffix.lower() in image_extensions]
    if not image_files:
        return None

    # 1. Поиск по прямому совпадению имени или stem в тексте запроса
    for img in image_files:
        if img.name.lower() in prompt_lower or img.stem.lower() in prompt_lower:
            return str(img.resolve())

    # 2. Если в запросе есть маркеры работы с изображением, берем самый свежий файл изображения
    vision_markers = ("макет", "скриншот", "снимок", "изображен", "картинк", "фото", "рисунк", "график")
    if any(m in prompt_lower for m in vision_markers):
        # Сортировка по времени последней модификации (самый свежий — первым)
        image_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return str(image_files[0].resolve())

    return None


class ChatFlowControllerMixin:
    app_config: Dict[str, Any] = {}

    def update_config(self, new_config: dict):
        self.app_config = new_config
        logger.info("[ChatFlowController] Конфигурация успешно обновлена на лету (Hot Reload).")

    # ── СИСТЕМА ДИНАМИЧЕСКИХ ДИРЕКТИВ ──

    @staticmethod
    def _get_ephemeral_art_directive(target_model_id: str = "realistic", target_model_desc: str = "", is_roleplay: bool = False) -> str:
        if not target_model_desc and target_model_id:
            try:
                from services.model_router import ModelRouter
                router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()
                for m in getattr(router, "profiles", []):
                    if m.get("id") == target_model_id:
                        guidance = (m.get("llm_guidance") or "").strip()
                        target_model_desc = guidance if guidance else (m.get("routing_description") or m.get("name") or "")
                        break
            except Exception:
                pass
        model_hint = f"{target_model_id} ({target_model_desc})" if target_model_desc else target_model_id

        return (
            f"\n\n[СИСТЕМНАЯ ДИРЕКТИВА: Генерация изображения для SD 1.5]\n"
            f"Пользователь запросил визуализацию. Выступи в роли эксперта промпт-инжиниринга для чекпоинтов Stable Diffusion 1.5.\n"
            f"Целевая модель / эмбеддинги стиля: {model_hint}.\n\n"
            f"Правила составления промпта для SD 1.5:\n"
            f"1. Язык тегов: ИСКЛЮЧИТЕЛЬНО английский.\n"
            f"2. Структура: плотный список ключевых слов и коротких фраз через запятую (БЕЗ полных предложений и вводных слов).\n"
            f"3. Приоритет порядка слов (токены в начале имеют максимальный вес):\n"
            f"   [Главный объект и действие] -> [Окружение и локация] -> [Освещение и атмосфера] -> [Стиль и медиум] -> [Ракурс / план камеры] -> [Модификаторы качества].\n"
            f"4. Плотность: 40–70 ёмких токенов без словесного мусора.\n"
            f"5. Веса токенов: используй синтаксис Automatic1111/Forge умеренно, например (masterpiece:1.2), (photorealistic:1.1).\n"
            f"6. Негативный промпт писать НЕ НУЖНО (он инжектируется системой автоматически).\n\n"
            f"Формат ответа:\n"
            f"1. Ответь пользователю кратко и доброжелательно на русском языке (1-2 живых предложения).\n"
            f"2. В самом конце ответа с новой строки выведи команду генерации строго в формате:\n"
            f"[IMAGE_PROMPT: <comma-separated SD 1.5 positive tags> --model {target_model_id}]\n\n"
            f"Пример окончания ответа:\n"
            f"Секунду, перевожу образ в кадр...\n"
            f"[IMAGE_PROMPT: 1girl, female samurai, holding katana, detailed traditional armor with cyberpunk accents, standing in neo-tokyo street, night, heavy rain, wet pavement, puddles with neon reflections, cinematic lighting, dramatic rim light, upper body shot, photorealistic, 8k uhd, highly detailed, sharp focus --model {target_model_id}]"
        )

    @staticmethod
    def _resolve_target_image(prompt: str) -> Optional[str]:
        return _resolve_target_image(prompt)

    def is_engineer_mode_active(self) -> bool:
        """Определяет активность режима Инженера строго по текущей сессии."""
        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            raw_mode = str(self.current_session.get("mode") or "").lower()
            raw_type = str(self.current_session.get("type") or "").lower()
            return raw_mode in ("engineer", "engineering") or raw_type == "engineering"
        return bool(getattr(self, "_engineering_mode_active", False))

    def get_effective_mode(self) -> str:
        """Возвращает нормализованный строковый ключ активного режима (SSOT)."""
        if hasattr(self, "mode_coordinator") and getattr(self.mode_coordinator, "current_mode", None):
            return self.mode_coordinator.current_mode.value
        is_eng = self.is_engineer_mode_active()
        if is_eng:
            return "engineer"
        if getattr(self, "current_mode", None) == "roleplay":
            return "roleplay"
        return "assistant"

    def _force_reset_busy_state(self, task_id: Optional[str] = None):
        """Гарантированно возвращает поле ввода и кнопки в активное состояние."""
        try:
            # 1. Удаляем конкретную завершенную/упавшую задачу из учета
            if task_id and hasattr(self, "_active_tasks") and isinstance(self._active_tasks, dict):
                self._active_tasks.pop(task_id, None)

            # 2. Сбрасываем флаг занятости контроллера
            self.is_busy = False

            # 3. Разблокируем компонент ввода
            if hasattr(self, "input_box") and self.input_box:
                self.input_box.set_busy(False)
                if hasattr(self.input_box, "setEnabled"):
                    self.input_box.setEnabled(True)
                if hasattr(self.input_box, "text_input") and self.input_box.text_input:
                    self.input_box.text_input.setReadOnly(False)
                    self.input_box.text_input.setFocus()
                elif hasattr(self.input_box, "setReadOnly"):
                    self.input_box.setReadOnly(False)
                    self.input_box.setFocus()

            # 4. Проверяем кнопку отправки на случай раздельного управления
            if hasattr(self, "btn_send") and self.btn_send:
                self.btn_send.setEnabled(True)

            # 5. Скрываем индикатор набора/статуса
            if hasattr(self, "typing_indicator") and self.typing_indicator:
                self.typing_indicator.stop()
            if hasattr(self, "_remove_typing_loader"):
                self._remove_typing_loader()
        except Exception as e:
            logger.error(f"[ChatFlowController] Ошибка при принудительном сбросе UI: {e}")

    def _on_task_failed(self, task_id: str, error: str):
        """Гарантированно сбрасывает состояние занятости при сбое задачи."""
        self._force_reset_busy_state(task_id)

    def _on_task_aborted(self, task_id: str):
        """Гарантированно сбрасывает состояние занятости при отмене задачи."""
        self._force_reset_busy_state(task_id)

    def abort_generation(self) -> None:
        """Безопасно прерывает текущую генерацию нейросети и сбрасывает стриминговые буферы."""
        try:
            if hasattr(self, "task_queue") and self.task_queue:
                self.task_queue.abort_current()
        except Exception as e:
            logger.debug(f"[ChatFlowController] Ошибка task_queue.abort_current: {e}")

        try:
            if hasattr(self, "lm_client") and hasattr(self.lm_client, "abort"):
                self.lm_client.abort()
        except Exception as e:
            logger.debug(f"[ChatFlowController] Ошибка lm_client.abort: {e}")

        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer is not None:
            try:
                self._stream_flush_timer.stop()
            except Exception:
                pass

        self._stream_token_buffer = ""
        self._turn_bubble = None
        self._current_streaming_widget = None

        self._force_reset_busy_state()

    def clear_chat_view(self) -> None:
        """Очищает графическую область сообщений и сбрасывает оперативные указатели хода."""
        self._turn_bubble = None
        self._current_streaming_widget = None
        self._stream_token_buffer = ""
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer is not None:
            try:
                self._stream_flush_timer.stop()
            except Exception:
                pass
        if hasattr(self, "_clear_chat_widgets"):
            self._clear_chat_widgets()



    def _sync_turn(
        self,
        role: str = "assistant",
        content: str = "",
        replace_last: bool = True,
        trigger_memory: bool = False,
        image_path: Optional[Union[str, Path]] = None,
        file_paths: Optional[List[Union[str, Path]]] = None,
        knowledge_sources: Optional[List[Dict[str, Any]]] = None,
        thought: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Единый адаптер синхронизации состояния диалога (Single Source of Truth).
        Синхронизирует chat_history (формат LLM) и current_session['messages'] (UI/JSON),
        предотвращая рассинхрон состояния диалога и потерю сообщений.
        Сохраняет knowledge_sources и thought для персистентности в session.json.
        """
        is_user = (role.lower() in ("user", "вы", "пользователь"))
        clean_content = str(content or "").strip()
        str_img = str(image_path) if image_path else None
        str_files = [str(p) for p in file_paths] if file_paths else []
        sources = knowledge_sources if knowledge_sources is not None else getattr(self, "_pending_knowledge_sources", None)
        t_data = thought if thought is not None else getattr(self, "_pending_thought_data", None)

        # 1. Синхронизация chat_history (OpenAI формат для модели с сохранением паттерна <think>)
        llm_content = clean_content
        if not is_user and t_data and isinstance(t_data, dict):
            thought_body = str(t_data.get("content") or "").strip()
            if thought_body and "<think>" not in clean_content:
                llm_content = f"<think>\n{thought_body}\n</think>\n\n{clean_content}"

        if hasattr(self, "chat_history") and isinstance(self.chat_history, list):
            if replace_last and self.chat_history and self.chat_history[-1].get("role") == role:
                self.chat_history[-1]["content"] = llm_content
            else:
                self.chat_history.append({"role": role, "content": llm_content})

        # 2. Синхронизация current_session['messages'] (JSON для диска и UI)
        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            if "messages" not in self.current_session or not isinstance(self.current_session["messages"], list):
                self.current_session["messages"] = []

            messages = self.current_session["messages"]
            updated = False

            if replace_last:
                for m in reversed(messages):
                    if m.get("is_user", False) == is_user or m.get("role") == role:
                        m["text"] = clean_content
                        if str_img:
                            m["image_path"] = str_img
                        if str_files:
                            m["file_paths"] = str_files
                        # Сброс устаревшего visual_intent при перегенерации текстового ответа
                        if not is_user:
                            m.pop("visual_intent", None)
                            if sources:
                                m["knowledge_sources"] = list(sources)
                            if t_data:
                                m["thought"] = dict(t_data)
                        updated = True
                        break

            if not updated:
                sender_name = "Вы" if is_user else (
                    getattr(self.active_character, "name", "Элария")
                    if hasattr(self, "active_character") and self.active_character
                    else "Элария"
                )
                msg_entry = {
                    "role": role,
                    "sender": sender_name,
                    "text": clean_content,
                    "is_user": is_user,
                    "timestamp": datetime.now().isoformat()
                }
                if str_img:
                    msg_entry["image_path"] = str_img
                if str_files:
                    msg_entry["file_paths"] = str_files
                if not is_user:
                    if sources:
                        msg_entry["knowledge_sources"] = list(sources)
                    if t_data:
                        msg_entry["thought"] = dict(t_data)
                messages.append(msg_entry)

            # Гарантируем персистентность структуры OpenAI-истории для LLM
            if hasattr(self, "chat_history") and isinstance(self.chat_history, list):
                self.current_session["chat_history"] = list(self.chat_history)

            # 3. Детерминированное сохранение на диск
            SessionManager.save_session(self.current_session, trigger_memory=trigger_memory)

    def _trigger_memory_extraction(self):
        """
        Ленивая экстракция долговременной памяти (Lazy Memory Extraction).
        Исполняется строго в конце завершённого хода диалога после ответа ассистента.
        """
        # Защита от преждевременного извлечения памяти
        if getattr(self, "_is_initializing", False) or getattr(self, "_is_switching_session", False):
            logger.debug("[Memory] Экстракция памяти пропущена: идет инициализация или смена сессии.")
            return

        # Защита от извлечения в инженерном режиме (изоляция LanceDB)
        if (hasattr(self, "is_engineer_mode_active") and self.is_engineer_mode_active()) or getattr(self, "_engineering_mode_active", False):
            return

        # Проверка наличия сообщений: извлекать факты можно только если есть реальный диалог пользователя
        if not getattr(self, "chat_history", None) or len(self.chat_history) < 2:
            return

        messages = self.current_session.get("messages", []) if getattr(self, "current_session", None) else []
        if not messages or len(messages) < 2:
            return

        # 1. Режим Ассистента: долгосрочные факты о пользователе
        if getattr(self, "current_mode", None) == "assistant":
            from services.memory_extractor import MemoryExtractor
            MemoryExtractor.extract_facts_async(messages, context=self)

        # 2. Режим Ролевого чата: NPC и факты сцены (history/cast_<id>.json)
        elif getattr(self, "current_mode", None) == "roleplay":
            if hasattr(self, "_trigger_roleplay_memory_extraction"):
                self._trigger_roleplay_memory_extraction()

    def append_message(self, role: str, content: str) -> Any:
        """Выводит сообщение в чат по роли (user / assistant)."""
        is_user = (role.lower() in ("user", "вы", "пользователь"))
        sender_name = "Вы" if is_user else (self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария")
        avatar = None
        if not is_user and hasattr(self, "_get_current_avatar_path"):
            avatar = self._get_current_avatar_path()
        return self._append_message(sender_name, content, is_user=is_user, avatar_path=avatar)
    def _detect_repetition_loop(self, text: str, min_len: int = 35, max_len: int = 400) -> tuple[bool, str]:
        n = len(text)
        if n < min_len * 2:
            return False, text

        # 1. Быстрая эвристика: проверка повторения по последнему законченному предложению
        last_punct = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
        if last_punct > min_len:
            tail = text[last_punct + 2:].strip()
            if len(tail) >= min_len:
                prev_pos = text.rfind(tail, 0, last_punct)
                if prev_pos != -1 and (last_punct - prev_pos) <= len(tail) + 4:
                    logger.warning(f"[NeonRoute.AntiLoop] Обнаружен повтор фразы ({len(tail)} симв.). Срез.")
                    return True, text[:last_punct + 2].rstrip()

        # 2. Прореженный перебор с шагом 8 (снижение нагрузки на CPU в 8 раз)
        limit = min(max_len, n // 2) + 1
        for length in range(min_len, limit, 8):
            chunk = text[-length:]
            if len(set(chunk.strip())) < 6:
                continue
            if chunk == text[-2 * length : -length]:
                logger.warning(
                    f"[NeonRoute.AntiLoop] Обнаружен цикл повторения токенов ({length} симв.). Прерывание стрима."
                )
                return True, text[:-length].rstrip()

        return False, text

    def _handle_user_submit(self, text: Optional[str] = None, attached_files: Optional[List[Path]] = None):
        """
        Точка входа обработки пользовательского ввода (или вызова из маршрутизации).
        """
        if text is not None and hasattr(self, "input_box") and self.input_box:
            self.input_box.setPlainText(text)
        return self._on_send_clicked()

    def _on_send_clicked(self):
        if hasattr(self, "_cancel_lms_reload"):
            self._cancel_lms_reload()

        text = self.input_box.toPlainText().strip()
        if not text and not self.attached_files:
            return

        self._last_llm_msg_text = None

        # Гарантированный детерминированный перехват установки напоминаний
        if re.search(r'^\s*(?:элария,?\s*)?напомни\b', text, re.IGNORECASE):
            from services.tool_engine import ToolEngine
            from datetime import datetime
            import dateparser

            raw_payload = re.sub(r'^\s*(?:элария,?\s*)?напомни(?:\s+мне)?\s+', '', text, flags=re.IGNORECASE).strip()
            target_dt = None
            matched_time_str = ""
            action_text = ""
            now = datetime.now()

            # Регулярное выражение для точного захвата временной конструкции
            time_pattern = (
                r'('
                r'\d{1,2}\.\d{1,2}(?:\.\d{2,4})?(?:\s+(?:в\s+)?\d{1,2}:\d{2}(?::\d{2})?)?'
                r'|\d{1,2}\s+[а-яё]+(?:\s+\d{4}(?:\s*года?)?)?(?:\s+(?:в\s+)?\d{1,2}:\d{2}(?::\d{2})?)?'
                r'|(?:сегодня|завтра|послезавтра)(?:\s+(?:в\s+)?(?:\d{1,2}:\d{2}(?::\d{2})?|полдень|полночь|вечер\w*|утр\w*))?'
                r'|через\s+[^\s,]+(?:\s+[^\s,]+)?'
                r'|\bв\s+\d{1,2}:\d{2}(?::\d{2})?'
                r')'
            )

            m_time = re.search(time_pattern, raw_payload, re.IGNORECASE)
            dp_settings = {
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': now,
                'DATE_ORDER': 'DMY',
                'RETURN_AS_TIMEZONE_AWARE': False
            }

            if m_time:
                matched_time_str = m_time.group(1).strip()
                # Нормализация для dateparser: замена "полдень" на 12:00, "полночь" на 00:00
                parseable_str = re.sub(r'\bполдень\b', '12:00', matched_time_str, flags=re.IGNORECASE)
                parseable_str = re.sub(r'\bполночь\b', '00:00', parseable_str, flags=re.IGNORECASE)
                parseable_str = re.sub(r'\b(?:в|at|к)\s+', '', parseable_str, flags=re.IGNORECASE).strip()

                target_dt = dateparser.parse(parseable_str, languages=['ru', 'en'], settings=dp_settings)
                if target_dt:
                    action_candidate = raw_payload.replace(matched_time_str, '', 1).strip()
                    action_candidate = re.sub(
                        r'^(?:[,\s\-—:]+)*(?:пожалуйста,?\s*)?(?:о\s+том,?\s*)?(?:чтобы|что бы|что)?\s*(?:я\s+)?',
                        '', action_candidate, flags=re.IGNORECASE
                    ).strip(' ,:;-—.?!\t\n')
                    action_text = action_candidate or "напоминание"

            if not target_dt:
                # Фоллбэк на fuzzy search_dates, если конструкция нестандартная
                try:
                    from dateparser.search import search_dates
                    found = search_dates(raw_payload, languages=['ru'], settings=dp_settings)
                    if found:
                        matched_time_str, target_dt = found[0]
                        action_candidate = raw_payload.replace(matched_time_str, '', 1).strip()
                        action_candidate = re.sub(
                            r'^(?:[,\s\-—:]+)*(?:пожалуйста,?\s*)?(?:о\s+том,?\s*)?(?:чтобы|что бы|что)?\s*(?:я\s+)?',
                            '', action_candidate, flags=re.IGNORECASE
                        ).strip(' ,:;-—.?!\t\n')
                        action_text = action_candidate or "напоминание"
                except Exception:
                    pass

            if target_dt:
                time_arg = target_dt.strftime("%Y-%m-%d %H:%M:%S")
                final_action = action_text if action_text else "напоминание"
                res_str = ToolEngine.set_reminder(final_action, time_arg)

                self.input_box.clear()
                self._append_message("Вы", text, is_user=True)

                if res_str.startswith("[Ошибка"):
                    reply_text = f"Не смогла установить напоминание: {res_str}"
                else:
                    friendly_time = target_dt.strftime("%d.%m.%Y в %H:%M")
                    reply_text = f"Договорились, напомню {friendly_time}: «{final_action}»."

                self._append_message("Элария", reply_text, is_user=False)
                self.chat_history.append({"role": "user", "content": text})
                # Сохраняем сессию тихо без запуска экстрактора памяти
                self._sync_turn(role="assistant", content=reply_text, replace_last=True, trigger_memory=False)
                return

        self.input_box.clear()
        attached_copy = list(self.attached_files)
        if hasattr(self, "input_panel") and hasattr(self.input_panel, "clear_attachments"):
            self.input_panel.clear_attachments()
        else:
            self.attached_files.clear()
            self._update_attachments_ui()

        user_text = text
        self._current_user_request = (user_text or "").strip()
        lower_text = user_text.lower()

        # Выбор стиля арта полностью делегирован LLM через протокол --model
        guidance = None
        self._pre_routed_art_style = None
        self._pre_routed_art_guidance = None

        from services.sandbox_manager import SandboxManager
        from services.vision_utils import VisionUtils

        image_exts = {".png", ".jpg", ".jpeg", ".webp"}
        image_attachments = [p for p in attached_copy if p.suffix.lower() in image_exts and p.exists()]

        resolved_image_path = _resolve_target_image(user_text)
        if not resolved_image_path and image_attachments:
            resolved_image_path = str(image_attachments[0].resolve())

        doc_creation_verbs = ("создай", "сделай", "сверстай", "построй", "перенеси", "таблиц", "документ", "excel", "word", "docx", "xlsx")
        is_doc_task = bool(resolved_image_path and any(v in user_text.lower() for v in doc_creation_verbs))

        if is_doc_task and resolved_image_path:
            # Отображение сообщения в UI и отправка в инженерный воркер
            img_path = Path(resolved_image_path)
            display_text = user_text or f"🖼️ [Макет: {img_path.name}]"
            self._append_message("Вы", display_text, is_user=True, image_path=img_path, file_paths=attached_copy)

            if getattr(self, "current_session", None) and not self.current_session.get("is_custom_titled", False):
                char_name = getattr(self.active_character, "name", "") if hasattr(self, "active_character") and self.active_character else ""
                new_title = SessionManager.generate_smart_title(
                    prompt=user_text or getattr(self, "_current_user_request", ""),
                    mode=getattr(self, "current_mode", "assistant"),
                    character_name=char_name
                )
                self.current_session["title"] = new_title
                self.current_session["is_custom_titled"] = True
                SessionManager.save_session(self.current_session)

                sess_id = self.current_session.get("id")
                if sess_id and hasattr(self, "_session_items") and sess_id in self._session_items:
                    _, it_w = self._session_items[sess_id]
                    it_w.update_title(new_title)

            self.chat_history.append({"role": "user", "content": user_text or ""})
            self._save_current_session()
            self._scroll_to_bottom()

            if self._dispatch_ephemeral_doc_worker(task_prompt=user_text, attached_files=attached_copy, image_path=resolved_image_path):
                return

        # 3. ИНАЧЕ (обычный диалоговый режим чата, например вопросы "что на картинке?", "опиши скриншот"):
        vision_content: Optional[List[Dict[str, Any]]] = None
        primary_user_image: Optional[Path] = None

        if resolved_image_path and Path(resolved_image_path).exists():
            image_payload = VisionUtils.prepare_image_payload(resolved_image_path, max_dim=768)
            content = [
                {"type": "text", "text": user_text},
                image_payload
            ]
            vision_content = content
            primary_user_image = Path(resolved_image_path)
            logger.info(f"Vision payload: изображение {primary_user_image.name} подготовлено через VisionUtils (max_dim=768)")
        else:
            content = user_text
            vision_content = None

        # Add user message to UI and history (displaying user attached image thumbnail in bubble)
        display_text = user_text or ("🖼️ [Прикреплено изображение]" if primary_user_image else "📄 [Прикреплен файл]")
        self._append_message("Вы", display_text, is_user=True, image_path=primary_user_image, file_paths=attached_copy)

        if getattr(self, "current_session", None) and not self.current_session.get("is_custom_titled", False):
            char_name = getattr(self.active_character, "name", "") if hasattr(self, "active_character") and self.active_character else ""
            new_title = SessionManager.generate_smart_title(
                prompt=user_text or getattr(self, "_current_user_request", ""),
                mode=getattr(self, "current_mode", "assistant"),
                character_name=char_name
            )
            self.current_session["title"] = new_title
            self.current_session["is_custom_titled"] = True
            SessionManager.save_session(self.current_session)

            sess_id = self.current_session.get("id")
            if sess_id and hasattr(self, "_session_items") and sess_id in self._session_items:
                _, it_w = self._session_items[sess_id]
                it_w.update_title(new_title)

        # В историю для LLM кладем сформированное сообщение
        self.chat_history.append({"role": "user", "content": content})
        if hasattr(self, "btn_load_character") and self.current_mode == "roleplay":
            self.btn_load_character.setEnabled(False)
            self.btn_load_character.setText("🔒 Компаньон зафиксирован")
            self.btn_load_character.setToolTip("Смена персонажа заблокирована после старта диалога. Для выбора другого начните новый чат (+ Новый).")
        if hasattr(self, "_check_mode_lock"):
            self._check_mode_lock()

        # Интеллектуальное чтение документов и вложений через DocumentParser
        from services.document_parser import DocumentParser

        attached_texts = []
        docs_meta = []
        is_eng_active = self.is_engineer_mode_active() if hasattr(self, "is_engineer_mode_active") else False
        is_asst_mode = (getattr(self, "current_mode", "assistant") == "assistant") and not is_eng_active

        for p in attached_copy:
            if DocumentParser.is_supported(p) and p.exists():
                try:
                    if is_asst_mode:
                        parsed_doc = DocumentParser.parse_file(p)
                        if parsed_doc:
                            from services.assistant_prompt_builder import AssistantPromptBuilder
                            prefix = "\n\n" + AssistantPromptBuilder.build_document_context(parsed_doc)
                            attached_texts.append(prefix)
                            docs_meta.append({
                                "filename": parsed_doc.filename,
                                "extension": parsed_doc.extension,
                                "summary": parsed_doc.summary_header,
                                "is_truncated": parsed_doc.is_truncated,
                                "size_kb": parsed_doc.size_kb,
                                "page_count": parsed_doc.page_count
                            })
                            logger.info(f"Документ {p.name} разобран через parse_file ({len(parsed_doc.content)} симв.)")
                    else:
                        f_content = DocumentParser.extract_text(p)
                        if f_content and f_content.strip():
                            if is_eng_active:
                                prefix = f"\n\n[ВХОДЯЩИЙ МАТЕРИАЛ ДЛЯ ОЗНАКОМЛЕНИЯ В ОЗУ: Файл '{p.name}']\n(Справочный документ. Это не запрос на изменение диска за пределами workspace):\n\"\"\"\n{f_content}\n\"\"\""
                            else:
                                prefix = f"\n\n### Содержимое прикрепленного файла [{p.name}]:\n\"\"\"\n{f_content}\n\"\"\""
                            attached_texts.append(prefix)
                            logger.info(f"Документ {p.name} успешно прочитан ({len(f_content)} симв.)")
                except Exception as e:
                    logger.warning(f"Ошибка чтения {p.name}: {e}")

        # Сохранение метаданных прикрепленного документа в сессию для истории
        if docs_meta and getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            messages = self.current_session.get("messages", [])
            if messages:
                for m in reversed(messages):
                    if m.get("is_user") or m.get("role") == "user":
                        m["attached_documents"] = docs_meta
                        break

        user_query = user_text.strip()
        if attached_texts:
            base_q = user_query if user_query else "Изучи прикрепленный файл и предоставь краткую выжимку его сути и содержания."
            full_prompt_for_llm = (base_q + "".join(attached_texts)).strip()
        else:
            full_prompt_for_llm = user_query or "Опиши изображение"

        if self.chat_history and self.chat_history[-1]["role"] == "user":
            if vision_content is not None:
                if attached_texts:
                    content[0]["text"] = full_prompt_for_llm
                self.chat_history[-1]["content"] = content
            else:
                self.chat_history[-1]["content"] = full_prompt_for_llm

        # Lock model on first message in this chat
        if self.current_session:
            self.current_session["is_model_locked"] = True
            if hasattr(self, "lm_combo") and self.lm_combo:
                self.lm_combo.setEnabled(False)
                self.lm_combo.setToolTip("Модель зафиксирована для этого диалога")

        self._save_current_session()
        self._tool_loop_count = 0
        self._tool_executed_this_turn = False
        self._turn_bubble = None
        self._is_post_tool_synthesis = False
        self._synthesis_first_chunk = False
        self._turn_assistant_widget = None
        self._pending_workspace_file = None
        self._last_tool_bubble = None
        self._script_executed_this_turn = False
        # Сброс буфера потокового вывода
        self._stream_token_buffer = ""
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
        self._scroll_to_bottom()

        # Проверка возврата модели из RAM при переходе от !art к обычному диалогу (выполняется в воркере)
        lower_t = user_query.lower()
        is_art_cmd = lower_t.startswith(('!art', '!арт', '!img', '!картинка'))
        if not is_art_cmd and getattr(self, 'current_mode_is_art', False):
            self.current_mode_is_art = False

        # Приоритетная маршрутизация: Режим «Инженер» или запрос на работу с документами/скриптами
        is_eng_active = self.is_engineer_mode_active()
        if is_eng_active or self._is_ephemeral_doc_request(user_query, has_vision=bool(vision_content)):
            if self.chat_history and self.chat_history[-1]["role"] == "user":
                self.chat_history[-1]["content"] = user_text or user_query
            if self._dispatch_ephemeral_doc_worker(task_prompt=user_text or user_query, attached_files=attached_copy):
                return

        # Если сформирован мультимодальный контент (Vision анализ скриншота/изображения), 
        # передаем задачу с сохранением активного режима (RP vs Assistant)
        self._last_has_vision_flag = bool(vision_content)
        if vision_content is not None:
            is_rp = (getattr(self, "current_mode", "assistant") == "roleplay")
            self._enqueue_llm_task(is_roleplay=is_rp, vision_content=vision_content, art_guidance=None)
            return

        # Передаем полный промпт для модели, но исходный user_query для проверки намерений
        self._route_and_enqueue(full_prompt_for_llm, attached_files=attached_copy, vision_content=None, user_query=user_query, art_guidance=None)

    def _enqueue_llm_task(
        self,
        is_roleplay: bool = False,
        is_excel: bool = False,
        is_word: bool = False,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        art_guidance: Optional[str] = None
    ):
        if is_roleplay:
            if hasattr(self, "_enqueue_roleplay_llm_task"):
                return self._enqueue_roleplay_llm_task(vision_content=vision_content, art_guidance=art_guidance)
        else:
            is_eng_mode = self.is_engineer_mode_active() if hasattr(self, "is_engineer_mode_active") else False
            if not is_eng_mode and hasattr(self, "_enqueue_assistant_llm_task"):
                return self._enqueue_assistant_llm_task(
                    is_excel=is_excel,
                    is_word=is_word,
                    vision_content=vision_content,
                    art_guidance=art_guidance
                )

        self._requested_word_export = is_word
        self._requested_excel_export = is_excel

        if art_guidance is None:
            art_guidance = getattr(self, "_pre_routed_art_guidance", None)


        model_name = None
        if hasattr(self, "lm_combo") and self.lm_combo:
            model_name = self.lm_combo.currentText().strip()
        if not model_name:
            model_name = (
                getattr(self, "active_lms_model", "")
                or (self.current_session.get("model_id") if getattr(self, "current_session", None) else "")
                or getattr(self.lm_client, "_last_loaded_model_id", None)
                or ""
            )
        if model_name.startswith("default"):
            model_name = None

        is_eng = self.is_engineer_mode_active() if hasattr(self, "is_engineer_mode_active") else False

        # ── ПРОВЕРКА ТРИГГЕРА АРТА ДО СБОРКИ КОНТЕКСТА ──
        _req = getattr(self, "_current_user_request", "")
        if not _req and hasattr(self, "chat_history") and self.chat_history:
            for _h in reversed(self.chat_history):
                if _h.get("role") == "user":
                    _req = str(_h.get("content", ""))
                    break

        from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin
        _should_art = (not is_roleplay and not is_eng and not vision_content and AssistantFlowControllerMixin._should_trigger_assistant_art(_req))

        active_mode = "engineer" if is_eng else ("roleplay" if is_roleplay else "assistant")
        from services.session_manager import get_mode_profile
        from services.token_budget_manager import TokenBudgetManager
        from services.assistant_prompt_builder import AssistantPromptBuilder
        from services.roleplay_prompt_builder import RoleplayPromptBuilder
        mode_profile = get_mode_profile(active_mode)
        base_history_limit = mode_profile.get("history_limit", 35)

        # ── ДИНАМИЧЕСКИЙ РАСЧЕТ БЮДЖЕТА ПАМЯТИ И ТОКЕНОВ (ДВУХКОНТУРНАЯ МОДЕЛЬ) ──
        if is_eng:
            # РЕЖИМ «ИНЖЕНЕР»: Никаких урезаний! Максимум возможностей модели для кода и инструментов
            effective_history_limit = base_history_limit
            effective_max_tokens = mode_profile.get("sampling", {}).get("max_tokens", 2048)
            history_slice = self.chat_history
        elif _should_art:
            # ЗАПРОС НА АРТ: Ультра-компактный бюджет (мгновенный Prefill за 1-2 сек)
            effective_history_limit = 6
            effective_max_tokens = 512
            history_slice = AssistantPromptBuilder.slice_history(self.chat_history, max_history_messages=effective_history_limit)
        else:
            # ОБЫЧНЫЙ ДИАЛОГ: Двухконтурная модель управления контекстом (Token Budget + Emergency VRAM Guard)
            free_vram = None
            if hasattr(self, "system_monitor") and self.system_monitor:
                try:
                    free_vram = self.system_monitor.get_free_vram_mb()
                except Exception:
                    free_vram = None

            prompt_tokens = TokenBudgetManager.estimate_messages_tokens(self.chat_history)
            if _req and (not self.chat_history or self.chat_history[-1].get("content") != _req):
                prompt_tokens += TokenBudgetManager.estimate_text_tokens(_req) + 5

            decision = TokenBudgetManager.calculate_context_budget(
                current_prompt_tokens=prompt_tokens,
                mode=active_mode,
                free_vram_mb=free_vram
            )
            effective_history_limit = decision.history_limit
            effective_max_tokens = decision.max_tokens

            if is_roleplay:
                history_slice = RoleplayPromptBuilder.slice_history(self.chat_history, max_history_messages=effective_history_limit)
            else:
                history_slice = AssistantPromptBuilder.slice_history(self.chat_history, max_history_messages=effective_history_limit)

        active_cast_block = ""
        recalled_lore_block = ""
        meta_context_block = ""
        effective_world_facts = None

        # Разрешение имени пользователя строго по типу режима
        curr_sess_id = self.current_session.get("id") if self.current_session else None
        sess_persona_id = self.current_session.get("user_persona_id") if self.current_session else None
        active_p = SessionManager.get_active_user_persona(is_roleplay=is_roleplay, session_persona_id=sess_persona_id)
        effective_user_name = active_p.get("name") or "Пользователь"
        is_dm = bool(self.current_session.get("is_dm_mode", False)) if self.current_session else False

        messages = SessionManager.get_lm_studio_messages(
            history=history_slice,
            is_roleplay=False,
            character_card=None,
            is_excel=is_excel,
            is_word=is_word,
            vision_content=vision_content,
            history_limit=effective_history_limit,
            user_name=effective_user_name,
            session_id=curr_sess_id,
            session_persona_id=sess_persona_id,
            is_engineer=is_eng,
            world_facts=None,
            art_guidance=art_guidance,
            active_cast_block="",
            recalled_lore_block="",
            meta_context_block="",
            is_dm_mode=is_dm,
            user_query=_req,
            task_text=_req
        )

        current_session = self.current_session if getattr(self, "current_session", None) else {}

        # 1. Свежие глобальные настройки
        global_sampling = self.app_config.get("llm_sampling", {})
        # 2. Индивидуальные параметры текущей сессии/персонажа (если переопределены)
        session_sampling = current_session.get("sampling_overrides", {})
        if not session_sampling:
            if is_roleplay and getattr(self, "active_character", None):
                session_sampling = getattr(self.active_character, "sampling_overrides", {}) or getattr(self.active_character, "sampling_params", {}) or {}
            elif current_session.get("is_custom_sampling", False):
                session_sampling = current_session.get("sampling_params", {})
        # 3. Итоговый рабочий словарь (сессия приоритетнее глобальных)
        effective_sampling = {**global_sampling, **session_sampling}

        cfg_key = "default_sampling_engineer" if is_eng else f"default_sampling_{self.current_mode}"
        cfg_defaults = getattr(self, "app_config", {}).get(cfg_key, {})
        if is_eng and not cfg_defaults:
            cfg_defaults = getattr(self, "app_config", {}).get("default_sampling_engineering", {})

        prof_sampling = mode_profile.get("sampling", {})
        default_temp = cfg_defaults.get("temperature", prof_sampling.get("temperature", 0.70))
        default_min_p = cfg_defaults.get("min_p", prof_sampling.get("min_p", 0.05))
        default_rep_pen = cfg_defaults.get("repeat_penalty", prof_sampling.get("repeat_penalty", 1.05))
        default_max_tok = cfg_defaults.get("max_tokens", prof_sampling.get("max_tokens", 1024))
        default_top_p = cfg_defaults.get("top_p", prof_sampling.get("top_p", 0.95))
        default_dry = 0.00 if is_eng else cfg_defaults.get("dry_multiplier", prof_sampling.get("dry_multiplier", 0.80))
        default_xtc_prob = 0.00 if is_eng else cfg_defaults.get("xtc_probability", prof_sampling.get("xtc_probability", 0.30 if self.current_mode == "roleplay" else 0.20))
        default_xtc_thresh = cfg_defaults.get("xtc_threshold", prof_sampling.get("xtc_threshold", 0.10))

        temp = float(effective_sampling.get("temperature", default_temp))
        min_p = float(effective_sampling.get("min_p", default_min_p))
        rep_pen = float(effective_sampling.get("repeat_penalty", effective_sampling.get("repetition_penalty", default_rep_pen)))
        top_p_val = effective_sampling.get("top_p", default_top_p)
        top_p = float(top_p_val) if top_p_val is not None else 0.95
        dry_mult = 0.00 if is_eng else float(effective_sampling.get("dry_multiplier", default_dry))
        xtc_prob = 0.00 if is_eng else float(effective_sampling.get("xtc_probability", default_xtc_prob))
        xtc_thresh = float(effective_sampling.get("xtc_threshold", default_xtc_thresh))

        has_custom_max_tok = ("max_tokens" in effective_sampling)
        user_max_tokens = effective_sampling.get("max_tokens")
        if _should_art:
            target_max_tokens = 512
        elif decision.is_emergency:
            # Аварийный аппаратный предохранитель: принудительно Ступень 3 (512 токенов)
            target_max_tokens = 512
        elif has_custom_max_tok and user_max_tokens:
            # Пользовательские настройки (глобальные или сессионные)
            target_max_tokens = int(user_max_tokens)
        else:
            # Штатная динамическая подстановка ступени двухконтурной модели (1024, 768, 512)
            target_max_tokens = effective_max_tokens

        effective_temp = 0.15 if is_eng else temp
        stop_tokens = None if is_roleplay else ["\n\nПользователь:", "\n\nUser:"]

        target_ctx_len = mode_profile.get("context_length", 8192)

        llm_kwargs = {
            "messages": messages,
            "model": model_name,
            "temperature": effective_temp,
            "stop": stop_tokens,
            "is_roleplay": is_roleplay,
            "is_engineer": is_eng,
            "context_length": target_ctx_len,
            "max_tokens": target_max_tokens,
            "min_p": min_p,
            "top_p": top_p,
            "repetition_penalty": rep_pen,
            "repeat_penalty": rep_pen,
            "dry_multiplier": dry_mult,
            "dry_base": 1.75,
            "dry_allowed_length": 2,
            "dry_penalty_last_n": 0,
            "xtc_probability": xtc_prob,
            "xtc_threshold": xtc_thresh,
            "stream": True
        }

        task = InferenceTask(
            name="LLM Ответ",
            task_type="llm",
            func=self.lm_client.chat_completion,
            kwargs=llm_kwargs,
            abort_fn=self.lm_client.abort,
            metadata={"is_excel": is_excel, "is_word": is_word, "is_roleplay": is_roleplay, "has_vision": bool(vision_content)}
        )

        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _flush_stream_buffer(self, force: bool = False):
        """Сбрасывает накопленный буфер токенов в бабл текущего хода с частотой ~28 FPS с подавлением служебных тегов."""
        if not hasattr(self, "_stream_token_buffer") or not self._stream_token_buffer:
            if not force and hasattr(self, "_stream_flush_timer") and self._stream_flush_timer.isActive():
                if getattr(self, "_current_streaming_widget", None) is None:
                    self._stream_flush_timer.stop()
            return

        buf = self._stream_token_buffer
        bubble = getattr(self, "_turn_bubble", None)

        # Регулярные выражения для потокового подавления арт-триггеров
        re_gallery_full = re.compile(r'\[(?:GALERY|GALLERY|ГАЛЕРЕЯ|ГАЛЕРИЯ|ГАЛЛЕРЕЯ|ГАЛЛЕРИЯ)\]', re.IGNORECASE)
        re_gallery_prefix = re.compile(r'\[(?:[gGгГ][a-zA-Zа-яА-ЯёЁ]{0,9})?$', re.IGNORECASE)

        # 1. Мгновенная активация киберпанк-лоадера и вырезание готового тега из потока
        if re_gallery_full.search(buf):
            if bubble is not None and hasattr(bubble, "show_art_loading_placeholder"):
                bubble.show_art_loading_placeholder()
            buf = re_gallery_full.sub('', buf)

        # 2. Скользящее окно: удержание незавершенного префикса маркера от вывода на экран
        chunk_to_render = buf
        if not force:
            partial_match = re_gallery_prefix.search(buf)
            if partial_match:
                split_idx = partial_match.start()
                chunk_to_render = buf[:split_idx]
                self._stream_token_buffer = buf[split_idx:]
            else:
                self._stream_token_buffer = ""
        else:
            self._stream_token_buffer = ""

        if not chunk_to_render:
            return

        if bubble is not None:
            try:
                bubble.append_stream_chunk(chunk_to_render)
                self._scroll_to_bottom()

                # Анти-зацикливание (Anti-Loop Guard): активно только в диалогах, в коде инженера отключено
                raw = getattr(bubble, "raw_text", "")
                is_eng = hasattr(self, "is_engineer_mode_active") and self.is_engineer_mode_active()
                if not is_eng and len(raw) > 250:
                    is_loop, clean_text = self._detect_repetition_loop(raw)
                    if is_loop:
                        self._antiloop_aborted = True
                        if hasattr(self, "task_queue") and hasattr(self.task_queue, "abort_current"):
                            self.task_queue.abort_current()
                        elif hasattr(self, "lm_client") and hasattr(self.lm_client, "abort"):
                            self.lm_client.abort()
                        bubble.set_text(clean_text)
                        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer.isActive():
                            self._stream_flush_timer.stop()
            except (RuntimeError, AttributeError):
                self._turn_bubble = None
                self._current_streaming_widget = None

    def _on_token_streamed(self, chunk: str):
        """Потоковый прием токенов с неблокирующим буферизированным батчингом."""
        if not chunk:
            return

        # Изоляция контекста: сброс в буфер обмена запрещен, если задача отменена или идет смена режима
        if not getattr(self, "is_busy", False) or getattr(self, "_is_switching_session", False):
            return

        # Создаем бабл только тогда, когда пришел первый видимый печатный символ
        if getattr(self, "_turn_bubble", None) is None:
            if not chunk.strip():
                return
            if self.typing_loader is not None:
                self._remove_typing_loader()

            sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
            avatar = self.active_character.avatar_path if (self.current_mode == "roleplay" and self.active_character) else self._get_current_avatar_path()

            self._turn_bubble = self._append_message(
                sender_name, "", is_user=False, avatar_path=avatar
            )
            if hasattr(self._turn_bubble, "set_knowledge_sources"):
                p_sources = getattr(self, "_pending_knowledge_sources", None)
                if p_sources:
                    self._turn_bubble.set_knowledge_sources(p_sources)
        elif self.typing_loader is not None:
            self._remove_typing_loader()
            # Гарантированный сброс устаревшего visual_intent у нового бабла
            if hasattr(self._turn_bubble, "visual_intent"):
                self._turn_bubble.visual_intent = None
            if getattr(self._turn_bubble, "art_proposal_chip", None) is not None:
                try:
                    self._turn_bubble.art_proposal_chip.deleteLater()
                    self._turn_bubble.art_proposal_chip = None
                except Exception:
                    pass

        self._current_streaming_widget = self._turn_bubble

        # Если идет итоговый синтез после выполнения инструментов, очищаем статус при первом токене
        if getattr(self, "_is_post_tool_synthesis", False) and getattr(self, "_synthesis_first_chunk", False):
            self._synthesis_first_chunk = False
            try:
                if self._turn_bubble is not None:
                    self._turn_bubble.set_text("")
            except (RuntimeError, AttributeError):
                self._turn_bubble = None
                self._current_streaming_widget = None

        # Маршрутизация токенов через ThoughtStreamParser (если активен)
        if getattr(self, "_thought_stream_parser", None) is not None:
            if not getattr(self, "_diag_chunk_logged", False):
                self._diag_chunk_logged = True
                print(f"[DIAG_THINK][2. STREAM FIRST CHUNKS] First chunk received: {repr(chunk)}")
            events = self._thought_stream_parser.feed(chunk)
            for ev_type, ev_data in events:
                if ev_type == "start_think":
                    if self._turn_bubble and hasattr(self._turn_bubble, "get_or_create_thought_spoiler"):
                        self._turn_bubble.get_or_create_thought_spoiler()
                elif ev_type == "thought_chunk":
                    if self._turn_bubble and hasattr(self._turn_bubble, "append_thought_chunk"):
                        self._turn_bubble.append_thought_chunk(ev_data)
                elif ev_type == "end_think":
                    if self._turn_bubble and hasattr(self._turn_bubble, "finalize_thought"):
                        dur = getattr(self._thought_stream_parser, "duration", 0.0)
                        tc = getattr(self._thought_stream_parser, "token_count", 0)
                        self._turn_bubble.finalize_thought(dur, tc)
                elif ev_type == "normal_chunk":
                    if ev_data:
                        if not hasattr(self, "_stream_token_buffer"):
                            self._stream_token_buffer = ""
                        self._stream_token_buffer += ev_data
        else:
            # Накопление токенов в буфер
            if not hasattr(self, "_stream_token_buffer"):
                self._stream_token_buffer = ""
            self._stream_token_buffer += chunk

        # Высокоточный таймер сброса (интервал 35 мс ≈ 28 FPS)
        if not hasattr(self, "_stream_flush_timer") or self._stream_flush_timer is None:
            self._stream_flush_timer = QTimer(self)
            self._stream_flush_timer.setInterval(35)
            self._stream_flush_timer.timeout.connect(self._flush_stream_buffer)

        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _extract_generation_params(self, result: any) -> Optional[Dict[str, Any]]:
        """Extracts generation metadata dictionary from Art Director / Forge result."""
        if not isinstance(result, dict):
            return None
        plan = result.get("plan")
        checkpoint = result.get("checkpoint")
        if not checkpoint and plan:
            checkpoint = getattr(plan, "target_checkpoint", None) or getattr(plan, "model_title", None)
        if not checkpoint:
            checkpoint = "SD Forge"

        forge_info = getattr(self.forge_client, "last_generation_info", {}) or {}
        seed = forge_info.get("seed")
        if seed is None and plan:
            seed = getattr(plan, "seed", -1)
        if seed is None:
            seed = -1

        pos_prompt = ""
        neg_prompt = ""
        steps = 28
        sampler = "DPM++ 2M"
        cfg = 6.0
        denoise = 0.35

        if plan:
            pos_prompt = getattr(plan, "positive_prompt", "") or ""
            neg_prompt = getattr(plan, "negative_prompt", "") or ""
            steps = getattr(plan, "steps", 28)
            sampler = getattr(plan, "sampler_name", "DPM++ 2M")
            cfg = getattr(plan, "cfg_scale", 6.0)
            denoise = getattr(plan, "denoising_strength", 0.35)

        if not pos_prompt and "prompt" in forge_info:
            pos_prompt = forge_info.get("prompt", "")
        if not neg_prompt and "negative_prompt" in forge_info:
            neg_prompt = forge_info.get("negative_prompt", "")
        if "steps" in forge_info:
            steps = forge_info.get("steps", steps)
        if "sampler_name" in forge_info:
            sampler = forge_info.get("sampler_name", sampler)
        if "cfg_scale" in forge_info:
            cfg = forge_info.get("cfg_scale", cfg)
        if "denoising_strength" in forge_info:
            denoise = forge_info.get("denoising_strength", denoise)

        return {
            "checkpoint": str(checkpoint),
            "seed": seed,
            "positive_prompt": pos_prompt,
            "negative_prompt": neg_prompt,
            "steps": steps,
            "sampler_name": sampler,
            "cfg_scale": cfg,
            "denoising_strength": denoise,
        }

    def _on_task_finished(self, task_id: str, result: any):
        self._remove_typing_loader()
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
        self._flush_stream_buffer(force=True)
        self.statusBar().showMessage("Задача успешно завершена.", 4000)
        self._update_header_for_current_state()

        task = self._active_tasks.get(task_id) if hasattr(self, "_active_tasks") and isinstance(self._active_tasks, dict) else None
        is_background = bool(task and (task.metadata.get("silent", False) or getattr(task, "task_type", None) == "memory"))
        if not is_background:
            self._force_reset_busy_state(task_id)
        elif task_id and hasattr(self, "_active_tasks") and isinstance(self._active_tasks, dict):
            self._active_tasks.pop(task_id, None)

        # 0. Ambiguity Gate: образ не распознан
        if isinstance(result, dict) and result.get("is_ambiguous"):
            plan = result.get("plan")
            clarification = getattr(plan, "clarification_message", "Не удалось распознать образ для генерации.")
            sender_name = getattr(self.active_character, "name", "Элария") if hasattr(self, "active_character") and self.active_character else "Элария"
            
            target_widget = task.metadata.get("target_widget") if (task and task.metadata) else None
            if target_widget and hasattr(target_widget, "set_text"):
                try:
                    target_widget.set_text(clarification)
                except RuntimeError:
                    target_widget = None
            if not target_widget:
                self._append_message(sender_name, clarification, is_user=False)
            self._sync_turn(role="assistant", content=clarification, replace_last=True)
            return

        # 2. Ephemeral Document / Sandbox Script
        if task and task.metadata and task.metadata.get("is_ephemeral_doc"):
            self._handle_engineer_task_finished(task, result)
            return

        # 3. Перевод текста
        if task and task.metadata and task.metadata.get("is_translation"):
            target_widget = task.metadata.get("target_widget")
            translated_text = str(result or "").strip()
            if target_widget and isinstance(target_widget, ChatMessageWidget):
                if translated_text:
                    target_widget.set_translation(translated_text)
                    self._update_message_translation_in_session(target_widget, translated_text)
                else:
                    if getattr(target_widget, "btn_translate", None):
                        target_widget.btn_translate.setText("🌐 Перевести")
            return

        # 4. Фон сцены (Scene Background)
        if (task and task.metadata and task.metadata.get("is_scene_background")) or (isinstance(result, dict) and result.get("is_scene_background")):
            img_p = result.get("image_path") if isinstance(result, dict) else result
            if img_p and Path(img_p).exists():
                if self.current_session:
                    self.current_session["background_image"] = str(img_p)
                    self._save_current_session()
                if hasattr(self, "_apply_chat_background"):
                    self._apply_chat_background()
                sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
                self._append_message(sender_name, "🖼️ Фон сцены обновлен.", is_user=False, image_path=Path(img_p))
            return

        # 5. Генерация изображений (Scene Art vs Standalone Art)
        if isinstance(result, dict) and "image_path" in result:
            captured_mode = self.get_effective_mode() if hasattr(self, "get_effective_mode") else getattr(self, "current_mode", "assistant")
            if captured_mode == "assistant" and hasattr(self, "_handle_assistant_art_completed"):
                if self._handle_assistant_art_completed(task, result):
                    return

            img_path = Path(result["image_path"]) if result.get("image_path") else None
            plan: Optional[GenerationPlan] = result.get("plan")
            gen_params = self._extract_generation_params(result)
            sender_name = getattr(self.active_character, "name", "Элария") if hasattr(self, "active_character") and self.active_character else "Элария"

            if gen_params:
                last_seed = gen_params.get("seed", -1)
                last_prompt = getattr(self, "_last_art_prompt", "") or (task.metadata.get("prompt") if task and task.metadata else "")
                last_meta = {
                    "seed": last_seed,
                    "prompt": last_prompt,
                    "positive_prompt": gen_params.get("positive_prompt", ""),
                    "checkpoint": gen_params.get("checkpoint", "")
                }
                self._last_art_meta = last_meta
                if self.current_session and isinstance(self.current_session, dict):
                    self.current_session["last_generation"] = last_meta

            target_widget = task.metadata.get("target_widget") if (task and task.metadata) else None

            # А. РЕЖИМ INLINE (Иллюстрация сцены): встраиваем в существующий бабл ответа
            if target_widget or (task and task.metadata and task.metadata.get("is_scene_art")):
                widget_to_update = None
                if target_widget:
                    try:
                        _ = target_widget.objectName()
                        widget_to_update = target_widget
                    except (RuntimeError, AttributeError):
                        widget_to_update = None

                if widget_to_update is None and hasattr(self, "chat_layout"):
                    for i in reversed(range(self.chat_layout.count())):
                        item = self.chat_layout.itemAt(i)
                        w = item.widget() if item else None
                        if w and isinstance(w, ChatMessageWidget) and not getattr(w, "is_user", True):
                            widget_to_update = w
                            break

                if widget_to_update and img_path and img_path.exists():
                    try:
                        # 1. Текст реплики сохраняется: заглушка ставится ТОЛЬКО если бабл изначально был пуст
                        existing_text = getattr(widget_to_update, "raw_text", "") or ""
                        # Без текстовых заглушек при пустом бабле

                        # 2. Изображение монтируется в контейнер бабла без перезаписи текста
                        if hasattr(widget_to_update, "set_image"):
                            widget_to_update.set_image(str(img_path))
                        else:
                            widget_to_update.attach_file(img_path, generation_params=gen_params)

                        if widget_to_update and hasattr(widget_to_update, "set_scene_art_busy"):
                            widget_to_update.set_scene_art_busy(False)

                        if plan and getattr(plan, "negative_prompt", None) and hasattr(widget_to_update, "set_negative_prompt"):
                            widget_to_update.set_negative_prompt(plan.negative_prompt)
                        if gen_params and hasattr(widget_to_update, "set_generation_params"):
                            widget_to_update.set_generation_params(gen_params)
                        if hasattr(self, "_update_message_attachment"):
                            self._update_message_attachment(widget_to_update, img_path, generation_params=gen_params)
                    except RuntimeError:
                        widget_to_update = None

                if widget_to_update and hasattr(widget_to_update, "set_scene_art_busy"):
                    widget_to_update.set_scene_art_busy(False)

                if not widget_to_update and img_path and img_path.exists():
                    self._append_message(
                        sender_name,
                        "🎨 Иллюстрация сцены готова:",
                        is_user=False,
                        image_path=img_path,
                        negative_prompt=plan.negative_prompt if plan else None,
                        generation_params=gen_params
                    )

                # 3. Синхронизация сессии: сохраняем литературный текст реплики в JSON и добавляем image_path
                if self.current_session and "messages" in self.current_session and img_path:
                    for m in reversed(self.current_session["messages"]):
                        if not m.get("is_user") and m.get("role") in ("assistant", None):
                            m["image_path"] = str(img_path)
                            # Заглушка выставляется ТОЛЬКО если в сообщении изначально не было текста
                            if not (m.get("text") or "").strip():
                                m["text"] = "🎨 Иллюстрация сцены готова:"
                            break
                    self._save_current_session()

            # Б. РЕЖИМ STANDALONE (Прямой вызов !art / Командный рендер): создаём новый бабл
            elif img_path and img_path.exists():
                model_title = plan.model_title if plan else "SD Forge"
                style_name = plan.style_badge if (plan and plan.style_badge) else "Арт"
                caption = f"🎨 Арт-директор ({model_title} • {style_name}):"
                self._append_message(
                    sender_name,
                    caption,
                    is_user=False,
                    image_path=img_path,
                    negative_prompt=plan.negative_prompt if plan else None,
                    generation_params=gen_params
                )

            self.statusBar().showMessage("Изображение готово.", 5000)
            if hasattr(self, "_check_mode_lock"):
                self._check_mode_lock()
            return

        # 6. Текстовые ответы LLM (если не прошли стримингом)
        if isinstance(result, str):
            self._current_streaming_widget = None
            self._on_llm_response_received(result)
            self._last_llm_msg_text = result

        # 7. Аудио TTS
        elif isinstance(result, Path) and result.suffix.lower() == ".wav":
            sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
            self._append_message(sender_name, "Аудиозапись готова:", is_user=False, audio_path=result)

    def _on_tool_execution_finished(self, data: Any):
        """
        Универсальный обработчик завершения выполнения инструментов.
        Безопасно извлекает данные как из словаря dict, так и из объекта ToolResult.
        Гарантирует разблокировку интерфейса в блоке finally.
        """
        if not data:
            logger.warning("[ChatFlowController] Получен пустой ответ от ToolExecutionWorker")
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

            # Отрисовка артефактов / файлов в чате, если они присутствуют
            if artifacts:
                for art in artifacts:
                    art_path = Path(art) if not isinstance(art, Path) else art
                    if art_path.exists() and hasattr(self, "_turn_bubble") and self._turn_bubble is not None:
                        if hasattr(self._turn_bubble, "add_workspace_file_card"):
                            self._turn_bubble.add_workspace_file_card(
                                art_path,
                                on_run=getattr(self, "_execute_workspace_script", None)
                            )
                        if hasattr(self, "_update_message_attachment"):
                            self._update_message_attachment(self._turn_bubble, art_path)

            single_artifact = getattr(data, "artifact_path", None) if not isinstance(data, dict) else data.get("artifact_path")
            if single_artifact:
                single_path = Path(single_artifact) if not isinstance(single_artifact, Path) else single_artifact
                if single_path.exists() and hasattr(self, "_turn_bubble") and self._turn_bubble is not None:
                    if hasattr(self._turn_bubble, "add_workspace_file_card"):
                        self._turn_bubble.add_workspace_file_card(
                            single_path,
                            on_run=getattr(self, "_execute_workspace_script", None)
                        )
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

            obs_payload = (
                "[Tool Observation:\n"
                + "\n\n".join(obs_parts)
                + "\n]\n\n[Подтверди пользователю результат выполненных действий.]"
            )

            if hasattr(self, "chat_history") and isinstance(self.chat_history, list):
                self.chat_history.append({"role": "user", "content": obs_payload})

            self._is_post_tool_synthesis = True
            self._synthesis_first_chunk = True

            if hasattr(self, "_enqueue_llm_task"):
                self._enqueue_llm_task(is_roleplay=False)

        except Exception as e:
            logger.error(f"[ChatFlowController] Ошибка при обработке результатов инструмента: {e}", exc_info=True)
        finally:
            if hasattr(self, "_force_reset_busy_state"):
                self._force_reset_busy_state()

    @pyqtSlot(str)
    def _on_llm_response_received(self, text: str):
        """
        Единый диспетчер завершения ответа LLM.
        Детерминированно направляет поток в целевой контроллер режима без hasattr и теневого кода.
        """
        if getattr(self, "_is_switching_session", False):
            return

        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
        self._stream_token_buffer = ""
        self._current_streaming_widget = None

        if getattr(self, "_last_llm_msg_text", None) == text:
            return
        self._last_llm_msg_text = text

        mode_key = self.get_effective_mode()

        # ── ДОМЕН 1: РОЛЕВОЙ РЕЖИМ ──
        if mode_key == "roleplay":
            return self._handle_roleplay_llm_response(text)

        # ── ДОМЕН 2: ИНЖЕНЕРНЫЙ РЕЖИМ (ПЕСОЧНИЦА И ИНСТРУМЕНТЫ) ──
        if mode_key == "engineer":
            return self._handle_engineering_llm_flow(text)

        # ── ДОМЕН 3: РЕЖИМ АССИСТЕНТА (ЭЛАРИЯ) ──
        return self._handle_assistant_llm_response(text)

    def _handle_engineering_llm_flow(self, text: str):
        """Изолированная обработка ответа LLM для инженерного режима."""
        from services.tool_engine import ToolEngine
        from services.sandbox_manager import SandboxManager

        # 1. Завершение итогового синтеза после выполнения инструментов
        if getattr(self, "_is_post_tool_synthesis", False):
            clean_ans = ToolEngine.strip_tool_tags(text).strip() if ToolEngine else text.strip()
            if not clean_ans:
                clean_ans = "✅ Операция успешно выполнена в рабочей песочнице."
            if self._turn_bubble is not None:
                try:
                    self._turn_bubble.set_text(clean_ans)
                except RuntimeError:
                    pass
            self.chat_history.append({"role": "assistant", "content": clean_ans})
            self._is_post_tool_synthesis = False
            self._synthesis_first_chunk = False
            self._tool_loop_count = 0
            self._script_executed_this_turn = False
            return

        # 2. Перехват вызовов инструментов
        if ToolEngine and ToolEngine.has_tool_call(text):
            if hasattr(self, "_handle_tool_call_flow"):
                self._handle_tool_call_flow(text)
            return

        # 3. Fallback: перехват чистого блока кода
        if ToolEngine and not ToolEngine.has_tool_call(text) and not getattr(self, "_tool_executed_this_turn", False):
            extracted_code = ""
            code_block_match = re.search(r'```(?:python|py)?\s*[\r\n]+(.*?)(?:```|$)', text, flags=re.DOTALL | re.IGNORECASE)
            if code_block_match:
                extracted_code = code_block_match.group(1).strip()
            elif any(k in text for k in ("import ", "from ", "def ", "print(")):
                raw_lines = text.splitlines()
                code_lines = [l for l in raw_lines if not any(l.strip().startswith(p) for p in ("Этот скрипт", "Вывод программы", "Запуск"))]
                cand = "\n".join(code_lines).strip()
                if "print(" in cand or "import " in cand or "def " in cand:
                    extracted_code = cand

            if extracted_code:
                last_u = ""
                for m in reversed(self.chat_history):
                    if m.get("role") == "user" and not str(m.get("content", "")).startswith("[Системный результат"):
                        last_u = str(m.get("content", "")).lower()
                        break

                directives = ["сохрани", "напиши программу", "напиши скрипт", "создай файл", "создай скрипт", "создай", "запиши", "запусти", "выполни"]
                if any(d in last_u for d in directives):
                    fn_match = re.search(r'\b([a-zA-Z0-9_\-]+\.(?:py|json|txt|md|csv|ini))\b', last_u, re.IGNORECASE)
                    clean_fn = SandboxManager.clean_workspace_filename(fn_match.group(1) if fn_match else "test_app.py") if SandboxManager else (fn_match.group(1) if fn_match else "test_app.py")
                    ToolEngine.write_file(clean_fn, extracted_code)
                    self._tool_executed_this_turn = True

                    if self._turn_bubble is None:
                        sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
                        self._turn_bubble = self._append_message(sender_name, "", is_user=False)
                    f_path = SandboxManager.get_workspace_dir() / clean_fn if SandboxManager else Path(clean_fn)
                    if hasattr(self._turn_bubble, "add_workspace_file_card"):
                        self._turn_bubble.add_workspace_file_card(f_path, on_run=getattr(self, "_execute_workspace_script", None))
                    if hasattr(self, "_update_message_attachment"):
                        self._update_message_attachment(self._turn_bubble, f_path)

                    should_run = any(w in last_u for w in ("запусти", "выполни", "run", "execute", "проверь"))
                    if should_run and clean_fn.lower().endswith(".py"):
                        run_res = ToolEngine.run_script(clean_fn)
                        rc = getattr(run_res, "exit_code", 0) if not isinstance(run_res, str) else 0
                        out_text = getattr(run_res, "output", str(run_res))
                        is_err = not getattr(run_res, "success", rc == 0)
                        if hasattr(self._turn_bubble, "add_console_spoiler"):
                            self._turn_bubble.add_console_spoiler(
                                title=f"Консоль: {clean_fn}",
                                output=out_text,
                                is_error=is_err,
                                return_code=rc
                            )
                        fin_text = f"✅ Скрипт `{clean_fn}` создан в песочнице и успешно выполнен."
                    else:
                        fin_text = f"📄 Файл `{clean_fn}` сохранен в рабочей песочнице (workspace)."

                    if hasattr(self._turn_bubble, "set_text"):
                        self._turn_bubble.set_text(fin_text)
                    self.chat_history.append({"role": "assistant", "content": fin_text})
                    return

        # 4. Если инструментов нет — стандартный вывод отчета инженера
        from services.regex_filter import TextRegexFilter
        clean_text = TextRegexFilter.process_output(text).strip()
        if self._turn_bubble is not None:
            try:
                self._turn_bubble.set_text(clean_text)
            except RuntimeError:
                pass
        self._sync_turn(role="assistant", content=clean_text, replace_last=True)
ChatFlowController = ChatFlowControllerMixin

