from pathlib import Path

# Ищем реализацию chat_completion в сервисах клиента
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "def chat_completion" in txt:
        print(f"Найден метод в: {p}")
        lines = txt.splitlines()
        for idx, line in enumerate(lines, 1):
            if "def chat_completion" in line:
                for j in range(idx - 1, min(len(lines), idx + 45)):
                    print(f"[{j+1:4d}] {lines[j]}")
                break
