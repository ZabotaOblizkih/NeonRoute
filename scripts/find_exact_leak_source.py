import os
import json
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
TARGET_WORDS = ["эмансипация", "dark souls", "полинорианских", "культуры-источники"]

print("=" * 80)
print("ГЛОБАЛЬНЫЙ ПОИСК ИСТОЧНИКА ТЕКСТА ПО ВСЕМ ДАННЫМ И СЕССИЯМ")
print("=" * 80)

matches = []

# Сканируем json, txt, md в characters, history, data, knowledge_base
scan_dirs = [ROOT / "characters", ROOT / "history", ROOT / "data", ROOT / "knowledge_base"]

for s_dir in scan_dirs:
    if not s_dir.exists():
        continue
    for f in s_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in [".json", ".txt", ".md"]:
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
                txt_lower = txt.lower()
                for tw in TARGET_WORDS:
                    if tw in txt_lower:
                        matches.append((f, tw, txt))
                        break
            except Exception:
                pass

if not matches:
    print("❌ Текст не найден в файлах директорий characters/history/data. Ищем по всему корню...")
    for f in ROOT.glob("*.json"):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for tw in TARGET_WORDS:
                if tw in txt.lower():
                    matches.append((f, tw, txt))
                    break
        except Exception:
            pass

print(f"Найдено совпадений: {len(matches)}")
for f_path, word, full_txt in matches:
    print(f"\n ФАЙЛ: {f_path.relative_to(ROOT)} (маркер: '{word}')")
    # Если JSON, узнаем точный ключ
    if f_path.suffix.lower() == ".json":
        try:
            data = json.loads(full_txt)
            def find_keys(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        find_keys(v, f"{path}.{k}" if path else k)
                elif isinstance(obj, list):
                    for i, elem in enumerate(obj):
                        find_keys(elem, f"{path}[{i}]")
                elif isinstance(obj, str):
                    if any(w in obj.lower() for w in TARGET_WORDS):
                        print(f"  • КЛЮЧ: {path}")
                        print(f"  • Длина текста: {len(obj)} симв.")
                        print(f"  • Превью:\n    {obj[:200]}...")
            find_keys(data)
        except Exception as e:
            print(f"  (Ошибка парсинга json: {e})")
    else:
        print(f"  • Текстовый файл. Длина: {len(full_txt)} симв.")
        idx = full_txt.lower().find(word)
        print(f"  • Фрагмент:\n    {full_txt[max(0, idx-100):min(len(full_txt), idx+200)]}")

print("\n" + "=" * 80)
