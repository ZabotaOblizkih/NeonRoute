import re
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

# 1. Добавляем RE_MIXED_SCRIPT_PATTERN, если его еще нет
if "RE_MIXED_SCRIPT_PATTERN" not in content:
    cjk_def = "RE_CJK_PATTERN = "
    cjk_idx = content.find(cjk_def)
    if cjk_idx != -1:
        end_line = content.find("\n", cjk_idx)
        mixed_def = "\nRE_MIXED_SCRIPT_PATTERN = re.compile(r'\\b(?=[a-zA-Z]*[а-яА-ЯёЁ])(?=[а-яА-ЯёЁ]*[a-zA-Z])[a-zA-Zа-яА-ЯёЁ]{2,}\\b')"
        content = content[:end_line] + mixed_def + content[end_line:]

# 2. Обновляем промпты: позитивные инварианты без упоминания латинских калек
start_marker = "PROMPT_LITERARY_PROSE = "
end_marker = "def mask_card_tags("
idx_start = content.find(start_marker)
idx_end = content.find(end_marker)

clean_prompts = """PROMPT_LITERARY_PROSE = \"\"\"Ты — профессиональный литературный переводчик художественной ролевой прозы на русский язык.
ПРАВИЛА ПЕРЕВОДА:
1. Переводи выразительным, богатым литературным языком. Избегай дословной механической кальки, адаптируй конструкции под нормы русского языка.
2. Переводи текст оригинала полностью, без пропусков и сокращений.
3. ПРАВИЛО МОНОЛИТНОСТИ АЛФАВИТА:
   - Все повествование, диалоги, описания и названия параметров переводи на русский язык.
   - Каждое слово должно состоять строго из символов одной языковой системы (запрещено смешивать латиницу и кириллицу внутри одного слова).
   - На латинице допустимо оставлять только общепринятые игровые аббревиатуры (HP, MP, EXP, BP, RPG) и системные метки __NR_TAG_X__.
4. Сохраняй неизменными системные идентификаторы вида __NR_TAG_X__ точно на своих местах.
5. Выводи только готовый перевод без предисловий, пояснений и внешних кавычек.\"\"\"

PROMPT_MES_EXAMPLE = \"\"\"Ты — профессиональный литературный переводчик ролевой прозы и диалогов на русский язык.
ПРАВИЛА ПЕРЕВОДА:
1. Переведи все реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.
2. Переводи оригинал полностью без сокращений.
3. ПРАВИЛО МОНОЛИТНОСТИ АЛФАВИТА:
   - Каждое слово пишется строго на одном алфавите без смешивания букв.
   - Никаких иероглифов и непереведенного текста.
4. Сохраняй неизменными идентификаторы вида __NR_TAG_X__.
5. Выводи только готовый перевод.\"\"\"

PROMPT_FIRST_MES = \"\"\"Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.
ПРАВИЛА ПЕРЕВОДА:
1. Переведи вступительную сцену полностью от первого до последнего предложения живым, атмосферным литературным языком.
2. Избегай дословной кальки. Сохраняй структуру абзацев, прямую речь и *действия* персонажей.
3. ПРАВИЛО МОНОЛИТНОСТИ АЛФАВИТА И ХАРАКТЕРИСТИК:
   - Все повествование, прямая речь, а также названия характеристик и параметров (строки вида Параметр: Значение) переводятся на русский язык.
   - Категорически запрещено использовать буквы двух разных алфавитов внутри одного слова.
   - На латинице допустимы только стандартные аббревиатуры (HP, MP, BP, EXP) и системные теги __NR_TAG_X__.
4. Сохраняй системные идентификаторы вида __NR_TAG_X__ неизменными.
5. Выводи только перевод без лишних вводных слов.\"\"\"

"""
content = content[:idx_start] + clean_prompts + content[idx_end:]

# 3. Встраиваем Mixed Script Quality Gate в translate_text_slot
mixed_gate_code = """
        # Mixed Script Quality Gate: авто-повтор при обнаружении гибридных слов
        mixed_found = [w for w in RE_MIXED_SCRIPT_PATTERN.findall(clean_reply) if not w.startswith("__NR_TAG_")]
        if mixed_found:
            logger.warning(f"[CardTranslator] Обнаружены гибридные слова {mixed_found} в слоте '{field_key}' (чанк {idx}). Автоповтор...")
            mixed_prompt = (
                f"В предыдущем переводе обнаружены слова со смешанной латиницей и кириллицей: {', '.join(mixed_found[:4])}.\\n"
                f"Переведи этот фрагмент полностью на чистый русский язык без гибридных токенов. "
                f"Названия параметров также переводи на русский:\\n\\n{chunk}"
            )
            m_resp = lm_client.chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": mixed_prompt}
                ],
                temperature=0.2,
                max_tokens=2048,
                emit_signal=False,
                is_roleplay=False
            )
            m_clean, _ = sanitize_repetition(str(m_resp or "").strip())
            if m_clean:
                still_mixed = [w for w in RE_MIXED_SCRIPT_PATTERN.findall(m_clean) if not w.startswith("__NR_TAG_")]
                if not still_mixed:
                    clean_reply = m_clean
"""

target_cjk_anchor = "if cjk_clean and not RE_CJK_PATTERN.search(cjk_clean):\n                clean_reply = cjk_clean"
if target_cjk_anchor in content and "Mixed Script Quality Gate" not in content:
    content = content.replace(target_cjk_anchor, target_cjk_anchor + "\n" + mixed_gate_code, 1)

p.write_text(content, encoding="utf-8")
print("[+] services/card_translator.py: Системный инвариант и Quality Gate успешно внедрены!")
