from pathlib import Path

# 1. Что ответила модель
blob_path = Path("debug_captured_blob.txt")
if blob_path.exists():
    print("=== ЧТО СГЕНЕРИРОВАЛА МОДЕЛЬ (debug_captured_blob.txt) ===")
    print(blob_path.read_text(encoding="utf-8", errors="ignore"))
    print("=" * 60)
else:
    print("Файл debug_captured_blob.txt не найден.")

# 2. Как SubagentEngineer собирает сообщения перед отправкой
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("\n=== services/subagent_engineer.py (строки 560-610: сборка prompt/messages) ===")
for j in range(559, min(len(lines), 610)):
    print(f"{j+1:4d} | {lines[j]}")
