import py_compile
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

# Заменяем базовую температуру 0.25 на 0.05 в основном запросе
content = content.replace("temperature=0.25,", "temperature=0.05,")
# Заменяем температуру в ретраях CJK и повторов на 0.05 / 0.0
content = content.replace("temperature=0.2,", "temperature=0.05,")
content = content.replace("temperature=0.1,", "temperature=0.0,")

p.write_text(content, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("[+] services/card_translator.py: Температура перевода установлена на 0.05!")
