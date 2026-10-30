import sys
import json
import re
import difflib
import ast
import urllib.request
import time
from pathlib import Path

# 1. Загрузка целевого файла
target_path = Path(r"C:\NeonRoute\tests\test_assistant_flow.py")
if not target_path.is_file():
    candidates = list(Path(r"C:\NeonRoute").rglob("test_assistant_flow*.py"))
    target_path = max(candidates, key=lambda p: p.stat().st_size)

print(f"📄 Анализ файла: {target_path}")
orig_content = target_path.read_text(encoding="utf-8", errors="ignore")
orig_lines = orig_content.splitlines()
print(f"📊 Полный размер: {len(orig_lines)} строк ({len(orig_content)} байт)")

# 2. Локализация целевого класса через AST
tree = ast.parse(orig_content)
target_class_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        doc = ast.get_docstring(node) or ""
        if "Легковесный эмулятор графического бабла" in doc or node.name == "FakeBubble":
            target_class_node = node
            break

if not target_class_node:
    print("❌ Целевой класс FakeBubble не найден в AST дерева!")
    sys.exit(1)

start_l = target_class_node.lineno
end_l = target_class_node.end_lineno
print(f"🎯 Найден целевой узел AST: class {target_class_node.name} (строки {start_l}-{end_l})")

# Демонстрация проблемы: что видел валидатор
real_class_code = "\n".join(orig_lines[start_l - 1 : end_l])
print("\n" + "=" * 60)
print("🔍 РЕАЛЬНЫЙ КОД ЦЕЛЕВОГО КЛАССА НА ДИСКЕ:")
print("=" * 60)
print(real_class_code)
print("=" * 60)

# 3. Формирование контекстного среза (Slice Window)
# Показываем модели импорты, окружение и ПОЛНЫЙ код целевого класса без заглушек '...'
header_imports = "\n".join(orig_lines[:25])
task_description = 'убери проверку: """Легковесный эмулятор графического бабла сообщения без Qt.""" (класс FakeBubble)'

system_prompt = (
    "Ты — ведущий инженер NeonRoute. Твоя задача — точечный рефакторинг крупных файлов.\n"
    "ПРАВИЛА:\n"
    "1. ФАЗА 1 (ДИАГНОЗ): Напиши 1-2 строки: подтверди удаление класса FakeBubble и его диапазон строк.\n"
    "2. ФАЗА 2 (КОД): Верни блок точечного удаления через инструмент patch_file:\n"
    "   <<TOOL:patch_file filename=\"test_assistant_flow.py\">>\n"
    "   <<<<<<< SEARCH\n"
    "   точный_фрагмент_исходного_кода_для_удаления\n"
    "   =======\n"
    "   >>>>>>> REPLACE\n"
    "   <</TOOL:patch_file>>\n"
    "3. В SEARCH скопируй целевой класс целиком ровно так, как он приведен в блоке [ЦЕЛЕВОЙ КЛАСС]."
)

user_prompt = (
    f"Задача: «{task_description}»\n\n"
    f"[КОНТЕКСТ ОКРУЖЕНИЯ ФАЙЛА (всего {len(orig_lines)} строк)]:\n"
    f"```python\n{header_imports}\n...\n```\n\n"
    f"[ЦЕЛЕВОЙ КЛАСС ДЛЯ УДАЛЕНИЯ (строки {start_l}-{end_l})]:\n"
    f"```python\n{real_class_code}\n```\n\n"
    f"Примени точечное удаление через patch_file."
)

# 4. Запрос к модели
url = "http://127.0.0.1:1234/v1/chat/completions"
payload = {
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    "temperature": 0.1,
    "max_tokens": 1024,
    "stream": True
}

req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})

print("\n🚀 СТРИМИНГ ОТВЕТА МОДЕЛИ (ТОЧЕЧНЫЙ ПАТЧ):")
print("-" * 60)

full_resp = []
t0 = time.time()
with urllib.request.urlopen(req, timeout=60) as resp:
    for line in resp:
        line_str = line.decode("utf-8").strip()
        if not line_str.startswith("data: "):
            continue
        data_json = line_str[6:]
        if data_json == "[DONE]":
            break
        try:
            chunk = json.loads(data_json)
            delta = chunk["choices"][0].get("delta", {}).get("content", "")
            if delta:
                sys.stdout.write(delta)
                sys.stdout.flush()
                full_resp.append(delta)
        except Exception:
            pass

raw_answer = "".join(full_resp)
print("\n" + "-" * 60)
print(f"⏱️ Время операции: {time.time() - t0:.2f} сек\n")

# 5. Применение патча и валидация
m_patch = re.search(r'<{3,}SEARCH[\r\n]*(?P<search>.*?)[\r\n]*={3,}[\r\n]*(?P<replace>.*?)[\r\n]*>{3,}', raw_answer, flags=re.DOTALL)
if m_patch:
    search_txt = m_patch.group("search").strip()
    replace_txt = m_patch.group("replace").strip()
    
    print("🔍 [Парсер патча]:")
    print(f"   SEARCH блок: {len(search_txt)} симв.")
    print(f"   REPLACE блок: {len(replace_txt)} симв. (удаление)")
    
    if search_txt in orig_content:
        updated_content = orig_content.replace(search_txt, replace_txt, 1)
        try:
            ast.parse(updated_content)
            print("✅ AST ВАЛИДАЦИЯ: Изменения применились, синтаксис 724-строчного файла 100% валиден!")
            print(f"📏 Было {len(orig_lines)} строк -> Стало {len(updated_content.splitlines())} строк")
        except SyntaxError as err:
            print(f"❌ Ошибка AST после применения патча: {err}")
    else:
        print("⚠️ Не удалось найти точное совпадение SEARCH блока в оригинальном файле.")
        print(f"Первые 100 симв SEARCH:\n{search_txt[:100]}")
else:
    print("⚠️ Блок patch_file SEARCH/REPLACE не обнаружен в ответе.")
