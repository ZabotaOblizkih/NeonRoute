import py_compile
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

old_cjk_block = """            cjk_user_prompt = (
                f"В предыдущем выводе обнаружены иероглифы. Переведи этот фрагмент ИСКЛЮЧИТЕЛЬНО на русский язык. "
                f"Категорически запрещено использовать китайские и японские символы:\\n\\n{chunk}"
            )"""

new_cjk_block = """            cjk_user_prompt = (
                "Ты — профессиональный писатель и литературный переводчик ролевой прозы и диалогов на русский язык.\\n"
                "ПРАВИЛА:\\n"
                "1. Переведи все реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.\\n"
                "2. Все титулы, ранги и роли персонажей переводи смысловыми русскими словами.\\n\\n"
                f"{chunk}"
            )"""

if old_cjk_block in content:
    content = content.replace(old_cjk_block, new_cjk_block, 1)
    p.write_text(content, encoding="utf-8")
    py_compile.compile(str(p), doraise=True)
    print("[+] services/card_translator.py: Промпт повторной попытки успешно обновлен!")
else:
    print("[-] Целевой блок cjk_user_prompt не найден для замены.")
