from pathlib import Path

print("=== 1. services/mode_coordinator.py: продолжение transition_to (строки 75-125) ===")
mc_lines = Path("services/mode_coordinator.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for i in range(74, min(125, len(mc_lines))):
    print(f"{i+1:4d} | {mc_lines[i]}")

print("\n=== 2. ui/main_window.py: запуск начальной сессии (строки 680-715) ===")
mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for j in range(679, min(715, len(mw_lines))):
    print(f"{j+1:4d} | {mw_lines[j]}")

print("\n=== 3. Где объявлен is_engineer_mode_active в ui/ ===")
for p in Path("ui").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "is_engineer_mode" in txt:
        for idx, l in enumerate(txt.splitlines()):
            if "is_engineer_mode" in l:
                print(f"{p.name}:{idx+1} | {l.strip()}")
