import py_compile
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

start_marker = "def translate_text_slot("
end_marker = "\ndef translate_card_slots_sequential("

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print(f"[-] Маркеры не найдены: start={start_idx}, end={end_idx}")
    exit(1)

clean_slot_code = '''def translate_text_slot(
    text: str,
    field_key: str,
    lm_client: Any,
    abort_event: Optional[Any] = None,
    on_slot_chunk_cb: Optional[Callable[[str, int, int], None]] = None
) -> str:
    """
    Атомарный перевод отдельного текстового слота карточки:
    - Проверенные литературные промпты с правилом для титулов и классов
    - Маскирование тегов {{user}}, {{char}}
    - Иерархический чанкинг до 1400 символов
    - Контур защиты от CJK и зацикливаний
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
    return unmask_card_tags(merged_text, tag_map)'''

new_content = content[:start_idx] + clean_slot_code + content[end_idx:]
p.write_text(new_content, encoding="utf-8")

# Валидация синтаксиса
py_compile.compile(str(p), doraise=True)
print("[+] services/card_translator.py успешно восстановлен и скомпилирован без синтаксических ошибок!")
