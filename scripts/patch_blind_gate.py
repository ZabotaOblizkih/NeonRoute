import re
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

# Заменяем блок Quality Gate на «слепой» ретрай без эха артефактов
old_gate = """        # Mixed Script Quality Gate: авто-повтор при обнаружении гибридных слов
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
                    clean_reply = m_clean"""

new_gate = """        # Mixed Script Quality Gate: слепой авто-повтор без обратного вброса артефактов в контекст
        mixed_found = [w for w in RE_MIXED_SCRIPT_PATTERN.findall(clean_reply) if not w.startswith("__NR_TAG_")]
        if mixed_found:
            logger.warning(f"[CardTranslator] Обнаружены гибридные слова {mixed_found} в слоте '{field_key}' (чанк {idx}). Чистый автоповтор...")
            blind_prompt = (
                "КРИТИЧЕСКИ ВАЖНО: Переведи следующий фрагмент ИСКЛЮЧИТЕЛЬНО на чистый русский язык.\\n"
                "Все титулы, обращения, роли и статусы (включая Sage, Great Sage, Master) переводи русскими словами (Мудрец, Великий Мудрец).\\n"
                "Категорически запрещено использовать латинские буквы и оставлять непереведенные английские слова.\\n\\n"
                f"{chunk}"
            )
            m_resp = lm_client.chat_completion(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": blind_prompt}
                ],
                temperature=0.15,
                max_tokens=2048,
                emit_signal=False,
                is_roleplay=False
            )
            m_clean, _ = sanitize_repetition(str(m_resp or "").strip())
            if m_clean:
                clean_reply = m_clean"""

if old_gate in content:
    content = content.replace(old_gate, new_gate, 1)
    p.write_text(content, encoding="utf-8")
    print("[+] Quality Gate обновлен: исключен эффект эха мусорных токенов!")
else:
    print("[!] Шаблон старого Quality Gate не найден")
