from pathlib import Path

path = Path("ui/controllers/chat_flow_controller.py")
lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=" * 70)
print("ФУНКЦИЯ ЗАВЕРШЕНИЯ В chat_flow_controller.py (строки 1400-1435)")
print("=" * 70)
for i in range(max(0, 1400 - 1), min(len(lines), 1435)):
    print(f"[{i+1:4d}] {lines[i]}")
