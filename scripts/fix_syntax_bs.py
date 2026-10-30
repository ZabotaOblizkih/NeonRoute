from pathlib import Path

p = Path("services/lm_studio.py")
content = p.read_text(encoding="utf-8")

# Исправляем сломанную строку с заменой разделителя путей
content = content.replace('name = s.replace("\\", "/").rsplit("/", 1)[-1]', 'name = s.replace(chr(92), "/").rsplit("/", 1)[-1]')
content = content.replace('s.replace("\\", "/")', 's.replace(chr(92), "/")')

p.write_text(content, encoding="utf-8")
print("[+] services/lm_studio.py: Синтаксическая ошибка успешно устранена!")
