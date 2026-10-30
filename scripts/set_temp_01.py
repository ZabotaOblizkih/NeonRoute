import py_compile
from pathlib import Path

p = Path("services/card_translator.py")
content = p.read_text(encoding="utf-8")

# Устанавливаем температуру 0.1 во всех точках генерации
content = content.replace("temperature=0.05,", "temperature=0.1,")
content = content.replace("temperature=0.0,", "temperature=0.1,")
content = content.replace("temperature=0.25,", "temperature=0.1,")
content = content.replace("temperature=0.2,", "temperature=0.1,")

p.write_text(content, encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("[+] services/card_translator.py: Температура перевода зафиксирована на 0.1!")
