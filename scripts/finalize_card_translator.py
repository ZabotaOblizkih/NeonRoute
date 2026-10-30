import re
import py_compile
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

# 1. Проверяем и обновляем sanitize_repetition (добавляем защиту от залипания одного символа)
if "re.sub(r'(.)\\1{5,}'" not in content and "re.sub(r'([^\\w\\s]|\\w)\\1{5,}'" not in content:
    sanitize_target = "def sanitize_repetition(text: str) -> Tuple[str, bool]:"
    idx_san = content.find(sanitize_target)
    if idx_san != -1:
        # Вставляем срезку односимвольных петель перед многосимвольным regex
        anchor = "pattern = re.compile(r'((?:.{2,40}?)){5,}', re.DOTALL)"
        if anchor in content:
            replacement = (
                "# Срезка длинных залипаний одного символа (например, Аааааааа...)\n"
                "    text = re.sub(r'([а-яА-Яa-zA-Z])\\1{6,}', r'\\1\\1\\1', text)\n"
                "    " + anchor
            )
            content = content.replace(anchor, replacement, 1)

# 2. Финализируем константы промптов
start_marker = "PROMPT_LITERARY_PROSE = "
end_marker = "def mask_card_tags("
idx_start = content.find(start_marker)
idx_end = content.find(end_marker)

final_prompts = """PROMPT_LITERARY_PROSE = \"\"\"Ты — профессиональный писатель и литературный переводчик художественной ролевой прозы на русский язык.
ПРАВИЛА:
1. Переводи выразительным русским литературным языком, адаптируя обороты и избегая дословной механической кальки.
2. Все титулы, ранги, статусы и игровые классы персонажей (включая написанные с заглавной буквы: Sage, Great Sage, Mage, Knight) переводи смысловыми русскими словами (Мудрец, Великий Мудрец, Маг, Рыцарь).
3. Названия характеристик, параметров и списков (строки вида Параметр: Значение) переводи на русский язык.
4. Текст должен быть полностью на русском языке (кириллица). Каждое слово пишется строго на одном алфавите без смешивания латиницы и кириллицы.
5. Сохраняй неизменными системные идентификаторы вида __NR_TAG_X__ точно на своих местах.
6. Выводи только готовый перевод без предисловий и комментариев.\"\"\"

PROMPT_MES_EXAMPLE = \"\"\"Ты — профессиональный писатель и литературный переводчик ролевой прозы и диалогов на русский язык.
ПРАВИЛА:
1. Переведи все реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.
2. Все титулы, ранги и роли персонажей переводи смысловыми русскими словами.
3. Сохраняй неизменными идентификаторы вида __NR_TAG_X__.
4. Выводи только готовый перевод без комментариев.\"\"\"

PROMPT_FIRST_MES = \"\"\"Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.
ПРАВИЛА:
1. Переведи вступительную сцену полностью от первого до последнего предложения выразительным русским языком.
2. Сохраняй структуру абзацев, прямую речь и *действия* персонажей.
3. Все титулы, ранги, статусы и игровые классы персонажей (включая написанные с заглавной буквы: Sage, Great Sage, Mage, Knight) переводи смысловыми русскими словами (Мудрец, Великий Мудрец, Маг, Рыцарь).
4. Названия характеристик и параметров (строки вида Параметр: Значение) переводи на русский язык.
5. Текст должен быть строго на русском языке без смешивания алфавитов внутри слов.
6. Сохраняй идентификаторы вида __NR_TAG_X__ неизменными.
7. Выводи только готовый перевод без комментариев.\"\"\"

"""
content = content[:idx_start] + final_prompts + content[idx_end:]

# 3. Финализируем функцию translate_text_slot
start_slot_marker = "def translate_text_slot("
end_slot_marker = "\ndef translate_card_slots_sequential("

s_idx = content.find(start_slot_marker)
e_idx = content.find(end_slot_marker)

final_slot_func = '''def translate_text_slot(
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
            "\\n\\nКРИТИЧЕСКИ ВАЖНО:\\n"
            "Текст ниже содержит шаблоны характеристик и правила статов.\\n"
            "НЕ симулируй игру, НЕ вычисляй характеристики и НЕ заполняй статы.\\n"
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
            "строго сохраняя системные идентификаторы вида __NR_TAG_X__:\\n\\n"
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
                "Ты — профессиональный писатель и литературный переводчик ролевой прозы и диалогов на русский язык.\\n"
                "ПРАВИЛА:\\n"
                "1. Переведи все реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.\\n"
                "2. Все титулы, ранги и роли персонажей переводи смысловыми русскими словами.\\n\\n"
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

    merged_text = "\\n\\n".join(translated_paras)
    return unmask_card_tags(merged_text, tag_map)'''

content = content[:s_idx] + final_slot_func + content[e_idx:]

p.write_text(content, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("[+] services/card_translator.py: Все изменения успешно зафиксированы и скомпилированы!")
