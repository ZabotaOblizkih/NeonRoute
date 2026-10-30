from pathlib import Path

print("=== ПОИСК ВЫЗОВА chat_completion В services/ ===")
for p in Path("services").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "chat_completion" in txt:
        print(f"\nФайл: {p.name}")
        for i, line in enumerate(txt.splitlines(), 1):
            if any(k in line for k in ("chat_completion(", "messages=", "RoleplayPromptBuilder")):
                print(f"  {i:4d}: {line.strip()[:100]}")
