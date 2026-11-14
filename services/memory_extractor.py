import re
"""
Asynchronous User Fact Extractor for NeonRoute.
Periodically scans assistant conversations to persist long-term user facts into MemoryDB.
Runs sequentially via TaskQueue (PRIORITY_LOW) to eliminate VRAM and socket collisions.
Includes signature deduplication and debounce to avoid queue spam.
"""

import time
import json
import logging
import threading
from typing import List, Dict, Any, Optional

from services.memory_db import memory_db

logger = logging.getLogger("NeonRoute.MemoryExtractor")

EXTRACTION_SYSTEM_PROMPT = """Ты — аналитический модуль извлечения долговременной памяти о пользователе.
Твоя задача — находить стабильные факты О ПОЛЬЗОВАТЕЛЕ (человеке) для сохранения в базу знаний.

КАТЕГОРИЧЕСКИЕ ПРАВИЛА:
1. ИЗВЛЕКАЙ ФАКТЫ ТОЛЬКО О ПОЛЬЗОВАТЕЛЕ. Источник факта — исключительно слова Пользователя.
2. СТРОЖАЙШИЙ ЗАПРЕТ: Никогда не приписывай навыки, инструменты или свойства Ассистента (Эларии) Пользователю! 
   Если Ассистент пишет: «Я умею писать код на Python, создавать Excel-таблицы, генерировать арты», это функции ИИ, а НЕ Пользователя! Запрещено создавать факты вроде «Пользователь умеет программировать на Python».
3. Игнорируй очевидные мета-факты («пользователь общается на русском», «пользователь пишет сообщение»).
4. Формат вывода: краткий JSON-список строк в третьем лице («Пользователь предпочитает...», «Пользователь живет в...»). Если новых фактов о человеке нет, верни пустой список [].
5. СТРОЖАЙШИЙ ЗАПРЕТ НА МЕТА-ФАКТЫ И ИНВЕРСИЮ: Вопросы пользователя к ИИ («что ты умеешь?», «почему так?») НЕ являются интересами пользователя. Категорически запрещено создавать факты о том, что пользователь «задает вопросы», «может общаться» или «интересуется возможностями ИИ». Если пользователь прямо не говорит о своих увлечениях или фактах жизни от первого лица — верни пустой список []."""

# Алиас для обратной совместимости
EXTRACTOR_SYSTEM_PROMPT = EXTRACTION_SYSTEM_PROMPT


class MemoryExtractor:
    _lock = threading.Lock()
    _is_running = False
    _processed_signatures = set()
    _last_enqueue_time: float = 0.0
    COOLDOWN_SECONDS: float = 8.0

    @staticmethod
    def _has_user_fact_potential(text: str) -> bool:
        raw = text.strip()
        if len(raw) < 8:
            return False

        lowered = raw.lower()

        # 1. Отсекаем чистый этикет и междометия
        etiquette = {
            "привет", "здравствуй", "добрый день", "добрый вечер", "доброе утро",
            "спасибо", "благодарю", "понятно", "ясно", "ок", "хорошо", "отлично",
            "пока", "до свидания", "да", "нет", "не знаю", "согласен"
        }
        if lowered in etiquette or any(lowered.startswith(w) and len(lowered) < len(w) + 4 for w in etiquette):
            return False

        # 2. Выявляем прямые вопросы о возможностях бота (без личного контекста)
        bot_query_prefixes = (
            "что ты ", "кто ты", "как ты", "умеешь ли", "расскажи о себе",
            "что можешь", "какие у тебя", "почему ты", "помоги мне с", "покажи "
        )
        is_pure_bot_query = any(lowered.startswith(p) for p in bot_query_prefixes)

        # 3. Маркеры самораскрытия (факты о пользователе)
        self_markers = (
            "я ", "я,", "я —", "я -", "меня ", "мне ", "мой ", "моя ", "мое ", "моё ", "мои ",
            "у меня ", "обо мне ", "со мной ", "живу ", "работаю ", "занимаюсь ", "учусь ",
            "люблю ", "предпочитаю ", "ненавижу ", "разрабатываю ", "создаю ", "пишу на "
        )
        has_self_marker = any(m in lowered for m in self_markers)

        # Если это вопрос о боте и при этом нет личных маркеров о себе — отсекаем
        if is_pure_bot_query and not has_self_marker:
            return False

        # Если нет ни одного маркера о пользователе — отсекаем
        return has_self_marker

    @staticmethod
    def extract_facts_async(
        messages_history: List[Dict[str, Any]], 
        lm_client: Optional[Any] = None,
        min_messages: int = 2,
        context: Optional[Any] = None
    ) -> None:
        """
        Отправляет задачу экстракции памяти в приоритетную очередь TaskQueue.
        Исполняется строго 1 раз на раунд диалога с низким приоритетом (PRIORITY_LOW).
        """
        if context is not None:
            # Защита от преждевременного извлечения памяти
            if getattr(context, "_is_initializing", False) or getattr(context, "_is_switching_session", False):
                logger.debug("[Memory] Экстракция памяти пропущена: идет инициализация или смена сессии.")
                return

            # Защита от извлечения в инженерном режиме
            if getattr(context, "_engineering_mode_active", False):
                return

            # Проверка наличия сообщений: извлекать факты можно только если есть реальный диалог пользователя
            if not getattr(context, "chat_history", None) or len(context.chat_history) < 2:
                return

        if not messages_history or len(messages_history) < min_messages:
            return

        # 1. Запуск строго после завершения ответа ассистента
        last_msg = messages_history[-1]
        if last_msg.get("is_user") or last_msg.get("role") == "user":
            return

        # 2. Поиск последней реплики пользователя для формирования сигнатуры раунда
        last_user_text = ""
        for m in reversed(messages_history):
            if m.get("is_user") or m.get("role") == "user":
                last_user_text = str(m.get("content") or m.get("text") or "").strip()
                break

        if not last_user_text:
            return

        # Игнорируем команды генерации арта — они не содержат фундаментальных фактов о пользователе
        art_cmd_prefixes = ("нарисуй", "покажи", "изобрази", "визуализируй", "сгенерируй", "сделай арт", "сделай картинку", "!art", "!арт")
        if any(last_user_text.lower().startswith(p) for p in art_cmd_prefixes):
            return

        # Игнорируем сервисные команды управления памятью (Забудь, Запомни, Очисти память и т.д.)
        from services.memory_db import handle_memory_command
        if handle_memory_command(last_user_text)[0]:
            return

        if not MemoryExtractor._has_user_fact_potential(last_user_text):
            return

        turn_sig = f"{len(messages_history)}:{last_user_text[:80]}"

        with MemoryExtractor._lock:
            now = time.time()
            # Отсекаем повторные вызовы save_session для того же самого шага диалога
            if turn_sig in MemoryExtractor._processed_signatures:
                return
            # Дебаунс по времени между анализами
            if now - MemoryExtractor._last_enqueue_time < MemoryExtractor.COOLDOWN_SECONDS:
                return

            MemoryExtractor._processed_signatures.add(turn_sig)
            MemoryExtractor._last_enqueue_time = now

            if len(MemoryExtractor._processed_signatures) > 100:
                MemoryExtractor._processed_signatures.clear()
                MemoryExtractor._processed_signatures.add(turn_sig)

        from services.task_queue import get_task_queue, InferenceTask, PRIORITY_LOW
        tq = get_task_queue()

        if tq:
            task = InferenceTask(
                name="🧠 Извлечение фактов памяти",
                task_type="memory",
                func=MemoryExtractor._run_extraction,
                args=(messages_history, lm_client),
                priority=PRIORITY_LOW,
                metadata={"silent": True}
            )
            tq.enqueue(task)
        else:
            threading.Thread(
                target=MemoryExtractor._run_extraction,
                args=(messages_history, lm_client),
                daemon=True,
                name="FactExtractorThread"
            ).start()

    @staticmethod
    def _run_extraction(messages_history: List[Dict[str, Any]], lm_client: Optional[Any] = None):
        with MemoryExtractor._lock:
            if MemoryExtractor._is_running:
                logger.debug("[MemoryExtractor] Экстракция уже запущена, пропуск дубликата.")
                return
            MemoryExtractor._is_running = True

        try:
            client = lm_client
            if client is None:
                try:
                    from services.lm_studio import LMStudioClient
                    from config import DEFAULT_LM_STUDIO_URL
                    client = LMStudioClient(DEFAULT_LM_STUDIO_URL)
                except Exception as _e_init:
                    logger.debug(f"[MemoryExtractor] Ошибка инициализации клиента: {_e_init}")
                    return

            # 1. Берем СТРОГО последние 4 сообщения хода диалога
            recent_messages = messages_history[-4:] if len(messages_history) > 4 else messages_history

            # 2. Очищаем реплики и обрезаем длинные дампы (код, OCR), чтобы не превысить 1000 токенов
            dialog_lines = []
            for m in recent_messages:
                role = m.get("role") or ("user" if m.get("is_user") else "assistant")
                sender = "Пользователь" if role == "user" else "Ассистент (Элария)"
                txt = str(m.get("text") or m.get("content") or "").strip()
                # Вырезаем системные блоки базы знаний и цитаты
                txt = re.sub(r"<KnowledgeBase>[\s\S]*?</KnowledgeBase>", "", txt, flags=re.IGNORECASE)
                txt = re.sub(r"<Document[\s\S]*?</Document>", "", txt, flags=re.IGNORECASE)
                txt = re.sub(r"<AttachedDocument[\s\S]*?</AttachedDocument>", "", txt, flags=re.IGNORECASE)
                txt = re.sub(r"\[Источник:\s*[^\]]+\]", "", txt, flags=re.IGNORECASE)
                txt = re.sub(r"\[Документ:\s*[^\]]+\]", "", txt, flags=re.IGNORECASE)
                txt = re.sub(r"\[Chunk\s*#?[0-9a-zA-Z_-]+\]", "", txt, flags=re.IGNORECASE)
                txt = txt.strip()
                # Пропускаем сервисные теги инструментов, системные логи и команды управления памятью
                if txt.startswith(("[IMAGE_PROMPT", "<<TOOL:", "[Системный", "Забыла:", "Удалила из памяти", "Записала в долговременную память", "Очистила всю память")):
                    continue
                from services.memory_db import handle_memory_command
                if handle_memory_command(txt)[0]:
                    continue
                if len(txt) > 800:
                    txt = txt[:800] + "..."
                dialog_lines.append(f"{sender}: {txt}")

            dialog_text = "\n".join(dialog_lines)
            if not dialog_text.strip():
                return

            req_messages = [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Фрагмент диалога для анализа:\n{dialog_text}"}
            ]

            raw_resp = ""
            # Спецификация Structured Outputs под требования LM Studio
            schema_payload = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extracted_user_facts",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "facts": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "required": ["facts"]
                    }
                }
            }
            try:
                raw_resp = client.chat_completion(
                    messages=req_messages,
                    temperature=0.1,
                    max_tokens=512,
                    stream=False,
                    emit_signal=False,
                    enable_thinking=False,
                    response_format=schema_payload
                )
            except Exception as _e_json:
                logger.debug(f"[MemoryExtractor] Structured outputs fallback: {_e_json}")
                try:
                    raw_resp = client.chat_completion(
                        messages=req_messages,
                        temperature=0.1,
                        max_tokens=512,
                        stream=False,
                        emit_signal=False,
                        enable_thinking=False
                    )
                except Exception as _e_raw:
                    logger.debug(f"[MemoryExtractor] Raw chat_completion failed: {_e_raw}")

            clean_resp = str(raw_resp or "").strip()
            # Санация тегов рассуждений <think> (парные, одиночные и оборванные хвосты)
            clean_resp = re.sub(r'<think>[\s\S]*?</think>', '', clean_resp, flags=re.IGNORECASE)
            clean_resp = re.sub(r'</think>', '', clean_resp, flags=re.IGNORECASE)
            clean_resp = re.sub(r'<think>[\s\S]*$', '', clean_resp, flags=re.IGNORECASE)
            clean_resp = clean_resp.strip()

            facts: List[Any] = []

            # 1. Прямой детерминированный разбор чистого JSON-объекта
            try:
                parsed_data = json.loads(clean_resp)
                if isinstance(parsed_data, dict):
                    facts = parsed_data.get("facts", [])
                elif isinstance(parsed_data, list):
                    facts = parsed_data
            except json.JSONDecodeError as _jde:
                logger.debug(f"[MemoryExtractor] Прямой json.loads не удался: {_jde}. Пробуем поиск среза JSON...")
                # 2. Фоллбэк-парсинг среза на случай сторонних моделей
                start_obj = clean_resp.find("{")
                end_obj = clean_resp.rfind("}")
                if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
                    try:
                        p_obj = json.loads(clean_resp[start_obj:end_obj + 1])
                        if isinstance(p_obj, dict):
                            facts = p_obj.get("facts", [])
                    except Exception as _e_fobj:
                        logger.debug(f"[MemoryExtractor] Фоллбэк среза объекта не удался: {_e_fobj}")
                if not facts:
                    start_arr = clean_resp.find("[")
                    end_arr = clean_resp.rfind("]")
                    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
                        try:
                            p_arr = json.loads(clean_resp[start_arr:end_arr + 1])
                            if isinstance(p_arr, list):
                                facts = p_arr
                        except Exception as _e_farr:
                            logger.debug(f"[MemoryExtractor] Фоллбэк среза массива не удался: {_e_farr}")

            if isinstance(facts, list) and facts:
                saved_count = 0
                for f in facts:
                    f_str = str(f).strip()
                    if f_str and len(f_str) > 5:
                        row_id = memory_db.add_fact(f_str)
                        if row_id != -1:
                            saved_count += 1
                if saved_count > 0:
                    logger.info(f"[MemoryExtractor] Автоматически извлечено и сохранено новых фактов: {saved_count}")
        except Exception as e:
            logger.warning(f"[MemoryExtractor] Ошибка при извлечении фактов: {e}")
        finally:
            with MemoryExtractor._lock:
                MemoryExtractor._is_running = False
