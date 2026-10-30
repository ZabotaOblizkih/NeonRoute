from pathlib import Path

lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()

print("=== 1. Метод управления сплиттером и режимами (строки 1375-1410) ===")
for i in range(1374, min(1410, len(lines))):
    print(f"{i+1:4d} | {lines[i]}")

print("\n=== 2. Создание нового чата [NEW-CHAT] ===")
for idx, l in enumerate(lines):
    if "[NEW-CHAT]" in l or "create_new_session" in l:
        for j in range(max(0, idx - 5), min(len(lines), idx + 25)):
            print(f"{j+1:4d} | {lines[j]}")
        break

print("\n=== 3. services/mode_coordinator.py: transition_to ===")
mc_lines = Path("services/mode_coordinator.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(mc_lines):
    if "def transition_to" in l:
        for j in range(idx, min(len(mc_lines), idx + 40)):
            print(f"{j+1:4d} | {mc_lines[j]}")
        break
