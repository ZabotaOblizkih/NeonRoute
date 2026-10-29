"""
Card Translator Service for NeonRoute.
Handles literary translation of character card fields with tag masking,
paragraph chunking, token budget adaptation, and CJK Quality Gate validation.
"""

import re
import logging
from typing import Optional, Callable, Any

from services.task_queue import InferenceTask
from services.session_manager import SessionManager

logger = logging.getLogger("NeonRoute.CardTranslator")

RE_TAG_PATTERN = re.compile(r'(\{\{[^}]+\}\}|<[^>]+>|\[IMAGE[\s_-]PROMPT:[^\]]+\])', re.IGNORECASE)
RE_CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
RE_MIXED_SCRIPT_PATTERN = re.compile(r'\b(?=[a-zA-Z]*[а-яА-ЯёЁ])(?=[а-яА-ЯёЁ]*[a-zA-Z])[a-zA-Zа-яА-ЯёЁ]{2,}\b')

PROMPT_LITERARY_PROSE = """Ты — профессиональный писатель и литературный переводчик художественной ролевой прозы на русский язык.
ПРАВИЛА:
1. Переводи выразительным русским литературным языком, адаптируя обороты и избегая дословной механической кальки.
2. Все титулы, ранги, статусы и игровые классы персонажей (включая написанные с заглавной буквы: Sage, Great Sage, Mage, Knight) переводи смысловыми русскими словами (Мудрец, Великий Мудрец, Маг, Рыцарь).
3. Названия характеристик, параметров и списков (строки вида Параметр: Значение) переводи на русский язык.
4. Текст должен быть полностью на русском языке (кириллица). Каждое слово пишется строго на одном алфавите без смешивания латиницы и кириллицы.
5. Сохраняй неизменными системные идентификаторы вида __NR_TAG_X__ точно на своих местах.
6. Выводи только готовый перевод без предисловий и комментариев."""

PROMPT_MES_EXAMPLE = """Ты — профессиональный писатель и литературный переводчик ролевой прозы и диалогов на русский язык.
ПРАВИЛА:
1. Переведи все реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.
2. Все титулы, ранги и роли персонажей переводи смысловыми русскими словами.
3. Сохраняй неизменными идентификаторы вида __NR_TAG_X__.
4. Выводи только готовый перевод без комментариев."""

PROMPT_FIRST_MES = """Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.
ПРАВИЛА:
1. Переведи вступительную сцену полностью от первого до последнего предложения выразительным русским языком.
2. Сохраняй структуру абзацев, прямую речь и *действия* персонажей.
3. Все титулы, ранги, статусы и игровые классы персонажей (включая написанные с заглавной буквы: Sage, Great Sage, Mage, Knight) переводи смысловыми русскими словами (Мудрец, Великий Мудрец, Маг, Рыцарь).
4. Названия характеристик и параметров (строки вида Параметр: Значение) переводи на русский язык.
5. Текст должен быть строго на русском языке без смешивания алфавитов внутри слов.
6. Сохраняй идентификаторы вида __NR_TAG_X__ неизменными.
7. Выводи только готовый перевод без комментариев."""

def mask_card_tags(text: str) -> tuple[str, dict[str, str]]:
    """Маскирует макросы {{char}}, {{user}} и HTML/XML теги перед отправкой в LLM."""
    tag_map: dict[str, str] = {}
    counter = 0

    def _replace(match):
        nonlocal counter
        placeholder = f"__NR_TAG_{counter}__"
        tag_map[placeholder] = match.group(0)
        counter += 1
        return placeholder

    masked_text = RE_TAG_PATTERN.sub(_replace, text)
    return masked_text, tag_map


def unmask_card_tags(text: str, tag_map: dict[str, str]) -> str:
    """Восстанавливает исходные макросы и теги после перевода."""
    res = text
    for placeholder, original in sorted(tag_map.items(), key=lambda x: len(x[0]), reverse=True):
        m = re.search(r'\d+', placeholder)
        if m:
            num = m.group(0)
            pattern = re.compile(rf'_{{1,2}}\s*NR[\s_]+TAG[\s_]+{num}\s*_{{1,2}}', re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(placeholder).replace('_', r'[\s_]*'), re.IGNORECASE)
        res = pattern.sub(lambda _: original, res)
    return res


# Обратная совместимость алиасов функций на уровне модуля
mask_tags = mask_card_tags
unmask_tags = unmask_card_tags



def sanitize_repetition(text: str) -> tuple[str, bool]:
    """Детектирует и срезает зацикливания модели (например: ОКОКОК... или фразовые петли)."""
    pattern = re.compile(r'((?:.{2,40}?)){5,}', re.DOTALL)
    match = pattern.search(text)
    if match:
        clean = text[:match.start()].rstrip()
        return clean, True
    return text, False


def split_text_hierarchical(text: str, max_chars: int = 1400) -> list[str]:
    """
    Иерархический чанкинг для предотвращения перегрузки контекста модели:
    1. Сначала делит по двойным переводам строк (\n\n).
    2. Длинные блоки без \n\n делит по одиночным переводам строк (\n).
    3. Формирует плотные чанки оптимального размера (< max_chars).
    """
    raw_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    atomic_units = []

    for para in raw_paras:
        if len(para) > max_chars and "\n" in para:
            lines = [l.strip() for l in para.split("\n") if l.strip()]
            atomic_units.extend(lines)
        else:
            atomic_units.append(para)

    chunks = []
    current_chunk = []
    current_len = 0

    for unit in atomic_units:
        unit_len = len(unit)
        if current_len + unit_len > max_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [unit]
            current_len = unit_len
        else:
            current_chunk.append(unit)
            current_len += unit_len

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def translate_text_slot(
    text: str,
    field_key: str,
    lm_client: Any,
    abort_event: Optional[Any] = None,
    on_slot_chunk_cb: Optional[Callable[[str, int, int], None]] = None
) -> str:
    """
    Атомарный перевод отдельного текстового слота карточки:
    - Полноразмерный контекст (max_chars=4500) для предотвращения прорыва CJK
    - Температура 0.25 для исключения локальных BPE-склеек
    - Позитивный ретрай при детекции CJK без эха артефактов
    - Сохранение разметки и маскирование системных тегов
    """
    if not text or not text.strip():
        return ""

    if abort_event and abort_event.is_set():
        return ""

    sys_prompt = CardTranslator.build_field_prompt(field_key)
    if field_key.lower() == "personality":
        sys_prompt += (
            "\n\nКРИТИЧЕСКИ ВАЖНО:\n"
            "Текст ниже содержит шаблоны характеристик и правила статов.\n"
            "НЕ симулируй игру, НЕ вычисляй характеристики и НЕ заполняй статы.\n"
            "Переведи исключительно формулировки и правила на русский язык."
        )

    masked_text, tag_map = mask_card_tags(text)
    chunks = split_text_hierarchical(masked_text, max_chars=4500)

    translated_paras = []
    for idx, chunk in enumerate(chunks, 1):
        if abort_event and abort_event.is_set():
            break

        if on_slot_chunk_cb:
            on_slot_chunk_cb(field_key, idx, len(chunks))

        user_prompt = (
            "Переведи следующий текст на русский язык, "
            "строго сохраняя системные идентификаторы вида __NR_TAG_X__:\n\n"
            f"{chunk}"
        )

        resp = lm_client.chat_completion(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.25,
            max_tokens=4096,
            emit_signal=False,
            is_roleplay=False
        )

        raw_reply = str(resp or "").strip()
        clean_reply, had_loop = sanitize_repetition(raw_reply)

        if had_loop or len(clean_reply) < 10:
            logger.warning(
                f"[CardTranslator] Зацикливание токенов в слоте '{field_key}' (чанк {idx}/{len(chunks)}). "
                f"Повтор с temperature=0.15..."
            )
            retry_resp = lm_client.chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.15,
                max_tokens=2048,
                emit_signal=False,
                is_roleplay=False
            )
            retry_clean, _ = sanitize_repetition(str(retry_resp or "").strip())
            if retry_clean:
                clean_reply = retry_clean

        if RE_CJK_PATTERN.search(clean_reply):
            logger.warning(f"[CardTranslator] Иероглифы в слоте '{field_key}' (чанк {idx}). Позитивная повторная попытка...")
            cjk_user_prompt = (
                "Ты — профессиональный писатель и литературный переводчик ролевой прозы и диалогов на русский язык.\n"
                "ПРАВИЛА:\n"
                "1. Переведи все реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.\n"
                "2. Все титулы, ранги и роли персонажей переводи смысловыми русскими словами.\n\n"
                f"{chunk}"
            )
            cjk_resp = lm_client.chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": cjk_user_prompt}
                ],
                temperature=0.25,
                max_tokens=4096,
                emit_signal=False,
                is_roleplay=False
            )
            cjk_clean, _ = sanitize_repetition(str(cjk_resp or "").strip())
            if cjk_clean and not RE_CJK_PATTERN.search(cjk_clean):
                clean_reply = cjk_clean

        translated_paras.append(clean_reply)

    merged_text = "\n\n".join(translated_paras)
    return unmask_card_tags(merged_text, tag_map)
def translate_card_slots_sequential(
    card: Any,
    lm_client: Any,
    abort_event: Optional[Any] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None
) -> dict[str, str]:
    """
    Последовательный пошаговый перевод (Slot-by-Slot) карточки персонажа:
    - Изолированная обработка каждого непустого поля
    - Полная защита от разрушения контекста монолитными склейками
    - Возвращает канонический словарь res_dict с ключами DESCRIPTION, PERSONALITY, FIRST_MES, etc.
    """
    slots_to_translate: list[tuple[str, str, str]] = []

    if getattr(card, "description", None) and card.description.strip():
        slots_to_translate.append(("DESCRIPTION", "description", card.description.strip()))
    if getattr(card, "personality", None) and card.personality.strip():
        slots_to_translate.append(("PERSONALITY", "personality", card.personality.strip()))
    if getattr(card, "scenario", None) and card.scenario.strip():
        slots_to_translate.append(("SCENARIO", "scenario", card.scenario.strip()))
    if getattr(card, "mes_example", None) and card.mes_example.strip():
        slots_to_translate.append(("MES_EXAMPLE", "mes_example", card.mes_example.strip()))
    if getattr(card, "first_mes", None) and card.first_mes.strip():
        slots_to_translate.append(("FIRST_MES", "first_mes", card.first_mes.strip()))

    alts = getattr(card, "alternate_greetings", []) or []
    for idx, alt_greet in enumerate(alts):
        if alt_greet and alt_greet.strip():
            slots_to_translate.append((f"ALT_GREETING_{idx+1}", "alternate_greetings", alt_greet.strip()))

    total_slots = len(slots_to_translate)
    res_dict: dict[str, str] = {}

    for slot_idx, (dict_key, field_type, src_text) in enumerate(slots_to_translate, 1):
        if abort_event and abort_event.is_set():
            logger.info("[CardTranslator] Процесс перевода прерван по сигналу abort.")
            break

        if on_progress:
            try:
                on_progress(slot_idx, total_slots, dict_key)
            except Exception:
                pass

        logger.info(f"[CardTranslator] [{slot_idx}/{total_slots}] Перевод слота '{dict_key}' ({len(src_text)} симв.)...")
        translated_val = translate_text_slot(
            text=src_text,
            field_key=field_type,
            lm_client=lm_client,
            abort_event=abort_event
        )
        if translated_val:
            res_dict[dict_key] = translated_val

    return res_dict


class CardTranslator:
    # Модульные пошаговые методы локализации (v2)
    split_text_hierarchical = staticmethod(split_text_hierarchical)
    sanitize_repetition = staticmethod(sanitize_repetition)
    translate_text_slot = staticmethod(translate_text_slot)
    translate_card_slots_sequential = staticmethod(translate_card_slots_sequential)


    """
    Статический сервис для подготовки и выполнения переводов полей карточек персонажей.
    """

    mask_tags = staticmethod(mask_card_tags)
    unmask_tags = staticmethod(unmask_card_tags)
    mask_card_tags = staticmethod(mask_card_tags)
    unmask_card_tags = staticmethod(unmask_card_tags)

    @staticmethod
    def build_field_prompt(field_key: str) -> str:
        """
        Генерирует специализированный системный промпт литературного переводчика для конкретного поля:
        'description', 'personality', 'scenario', 'mes_example', 'first_mes'.
        """
        key = (field_key or "").lower().strip()
        if key == "mes_example":
            return PROMPT_MES_EXAMPLE
        elif key in ("first_mes", "greeting", "alternate_greetings"):
            return PROMPT_FIRST_MES
        elif key in ("description", "personality", "scenario"):
            return PROMPT_LITERARY_PROSE
        else:
            return PROMPT_LITERARY_PROSE

    @staticmethod
    def create_translation_task(
        source_text: str,
        system_prompt: str,
        lm_client: Any,
        on_done: Optional[Callable[[str, Any], None]] = None,
        on_fail: Optional[Callable[[str, str], None]] = None,
        max_chunk_tokens: int = 600,
        task_name: str = "Перевод"
    ) -> InferenceTask:
        """
        Создает и конфигурирует InferenceTask для перевода текста:
        - Маскирование системных тегов и макросов
        - Поабзацный чанкинг по \\n\\n через SessionManager.estimate_tokens
        - Адаптивный расчет токенов через SessionManager.calculate_translation_budget
        - Воркер с Quality Gate (повторная попытка при CJK иероглифах)
        - Сборка итогового текста и размаскирование тегов
        """
        def _worker_translation(t: Optional[str] = None) -> dict:
            text_to_process = source_text if t is None else t

            # 1. Маскирование системных тегов
            masked_text, tag_map = mask_card_tags(text_to_process)

            # 2. Поабзацный чанкинг для предотвращения сбоев внимания на длинных текстах
            total_est = SessionManager.estimate_tokens(masked_text)

            chunks: list[str] = []
            if total_est > max_chunk_tokens and "\n\n" in masked_text:
                raw_paragraphs = masked_text.split("\n\n")
                current_chunk = []
                current_tok = 0
                for p in raw_paragraphs:
                    p_tok = SessionManager.estimate_tokens(p)
                    if current_tok + p_tok > max_chunk_tokens and current_chunk:
                        chunks.append("\n\n".join(current_chunk))
                        current_chunk = [p]
                        current_tok = p_tok
                    else:
                        current_chunk.append(p)
                        current_tok += p_tok
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
            else:
                chunks = [masked_text]

            # 3. Воркер последовательного перевода чанков с Quality Gate
            translated_chunks = []
            had_cjk_issue = False

            for idx, chunk in enumerate(chunks):
                if not chunk.strip():
                    translated_chunks.append(chunk)
                    continue

                user_prompt = f"Переведи следующий текст на чистый литературный русский язык:\n\n{chunk}"
                budget = SessionManager.calculate_translation_budget(chunk)

                resp = lm_client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.35,
                    max_tokens=budget,
                    emit_signal=False,
                    is_roleplay=False
                )
                chunk_reply = str(resp or "").strip()

                # CJK Quality Gate: ровно 1 повторная попытка при наличии иероглифов
                if RE_CJK_PATTERN.search(chunk_reply):
                    logger.warning(
                        f"[CardTranslator] Обнаружены иероглифы в чанке #{idx+1}. "
                        f"Запуск корректирующей попытки (max_retries=1)..."
                    )
                    retry_user_prompt = (
                        f"В твоем предыдущем переводе остались китайские/японские иероглифы. "
                        f"Переведи этот фрагмент ПОЛНОСТЬЮ на русский язык. "
                        f"Категорически запрещено выводить иероглифы:\n\n{chunk}"
                    )
                    retry_resp = lm_client.chat_completion(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": retry_user_prompt}
                        ],
                        temperature=0.1,
                        max_tokens=budget,
                        emit_signal=False,
                        is_roleplay=False
                    )
                    retry_reply = str(retry_resp or "").strip()
                    if retry_reply:
                        chunk_reply = retry_reply
                    if RE_CJK_PATTERN.search(chunk_reply):
                        had_cjk_issue = True

                translated_chunks.append(chunk_reply)

            assembled_masked = "\n\n".join(translated_chunks)
            final_text = unmask_card_tags(assembled_masked, tag_map)
            return {"final_text": final_text, "had_cjk_issue": had_cjk_issue}

        abort_fn = getattr(lm_client, "abort", None)
        task = InferenceTask(
            name=task_name,
            task_type="translation",
            func=_worker_translation,
            kwargs={"t": source_text},
            abort_fn=abort_fn,
            metadata={
                "source_text": source_text,
                "system_prompt": system_prompt,
                "on_done": on_done,
                "on_fail": on_fail
            }
        )
        task.on_done = on_done
        task.on_fail = on_fail
        return task
