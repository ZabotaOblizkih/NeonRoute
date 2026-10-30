import re
from pathlib import Path

target_file = Path("services/card_translator.py")
content = target_file.read_text(encoding="utf-8")

functions_code = '''
def sanitize_repetition(text: str) -> tuple[str, bool]:
    """Детектирует и срезает зацикливания модели (например: ОКОКОК... или фразовые петли)."""
    pattern = re.compile(r'((?:.{2,40}?))\1{5,}', re.DOTALL)
    match = pattern.search(text)
    if match:
        clean = text[:match.start()].rstrip()
        return clean, True
    return text, False


def split_text_hierarchical(text: str, max_chars: int = 1400) -> list[str]:
    """
    Иерархический чанкинг для предотвращения перегрузки контекста модели:
    1. Сначала делит по двойным переводам строк (\\n\\n).
    2. Длинные блоки без \\n\\n делит по одиночным переводам строк (\\n).
    3. Формирует плотные чанки оптимального размера (< max_chars).
    """
    raw_paras = [p.strip() for p in text.split("\\n\\n") if p.strip()]
    atomic_units = []

    for para in raw_paras:
        if len(para) > max_chars and "\\n" in para:
            lines = [l.strip() for l in para.split("\\n") if l.strip()]
            atomic_units.extend(lines)
        else:
            atomic_units.append(para)

    chunks = []
    current_chunk = []
    current_len = 0

    for unit in atomic_units:
        unit_len = len(unit)
        if current_len + unit_len > max_chars and current_chunk:
            chunks.append("\\n\\n".join(current_chunk))
            current_chunk = [unit]
            current_len = unit_len
        else:
            current_chunk.append(unit)
            current_len += unit_len

    if current_chunk:
        chunks.append("\\n\\n".join(current_chunk))

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
    - Специализированный системный промпт (с защитным барьером для personality)
    - Маскирование тегов {{user}}, {{char}} и системных идентификаторов
    - Иерархический чанкинг до 1400 символов
    - Repetition Guard с автоматическим повтором при зацикливании
    - CJK Quality Gate
    """
    if not text or not text.strip():
        return ""

    if abort_event and abort_event.is_set():
        return ""

    sys_prompt = CardTranslator.build_field_prompt(field_key)
    if field_key.lower() == "personality":
        sys_prompt += (
            "\\n\\nКРИТИЧЕСКИ ВАЖНО:\\n"
            "Текст ниже содержит шаблоны характеристик и правила статов.\\n"
            "НЕ симулируй игру, НЕ вычисляй характеристики и НЕ заполняй статы.\\n"
            "Переведи исключительно формулировки и правила на русский язык."
        )

    masked_text, tag_map = mask_card_tags(text)
    chunks = split_text_hierarchical(masked_text, max_chars=1400)

    translated_paras = []
    for idx, chunk in enumerate(chunks, 1):
        if abort_event and abort_event.is_set():
            break

        if on_slot_chunk_cb:
            on_slot_chunk_cb(field_key, idx, len(chunks))

        user_prompt = (
            "Переведи следующий текст на чистый литературный русский язык, "
            "строго сохраняя системные идентификаторы вида __NR_TAG_X__:\\n\\n"
            f"{chunk}"
        )

        resp = lm_client.chat_completion(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.25,
            max_tokens=2048,
            emit_signal=False,
            is_roleplay=False
        )

        raw_reply = str(resp or "").strip()
        clean_reply, had_loop = sanitize_repetition(raw_reply)

        if had_loop or len(clean_reply) < 10:
            logger.warning(
                f"[CardTranslator] Зацикливание токенов в слоте '{field_key}' (чанк {idx}/{len(chunks)}). "
                f"Повтор с temperature=0.1..."
            )
            retry_resp = lm_client.chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=1500,
                emit_signal=False,
                is_roleplay=False
            )
            retry_clean, _ = sanitize_repetition(str(retry_resp or "").strip())
            if retry_clean:
                clean_reply = retry_clean

        if RE_CJK_PATTERN.search(clean_reply):
            logger.warning(f"[CardTranslator] Иероглифы в слоте '{field_key}' (чанк {idx}). Повторная попытка...")
            cjk_user_prompt = (
                f"В переводе остались иероглифы. Переведи этот фрагмент ПОЛНОСТЬЮ на русский язык. "
                f"Категорически запрещено выводить китайские и японские символы:\\n\\n{chunk}"
            )
            cjk_resp = lm_client.chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": cjk_user_prompt}
                ],
                temperature=0.2,
                max_tokens=2048,
                emit_signal=False,
                is_roleplay=False
            )
            cjk_clean, _ = sanitize_repetition(str(cjk_resp or "").strip())
            if cjk_clean and not RE_CJK_PATTERN.search(cjk_clean):
                clean_reply = cjk_clean

        translated_paras.append(clean_reply)

    merged_text = "\\n\\n".join(translated_paras)
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

'''

class_marker = "class CardTranslator:"
class_aliases = '''    # Модульные пошаговые методы локализации (v2)
    split_text_hierarchical = staticmethod(split_text_hierarchical)
    sanitize_repetition = staticmethod(sanitize_repetition)
    translate_text_slot = staticmethod(translate_text_slot)
    translate_card_slots_sequential = staticmethod(translate_card_slots_sequential)

'''

# 1. Вставляем функции ПЕРЕД классом
content = content.replace(class_marker, functions_code + "\n" + class_marker, 1)

# 2. Добавляем алиасы внутрь класса (теперь имена уже определены!)
content = content.replace(class_marker, class_marker + "\n" + class_aliases, 1)

target_file.write_text(content, encoding="utf-8")
print("[OK] services/card_translator.py успешно обновлен с правильным порядком объявления!")
