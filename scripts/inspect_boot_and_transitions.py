from pathlib import Path

print("=== 1. services/mode_coordinator.py: _boot_target_mode и _mount_session ===")
mc_lines = Path("services/mode_coordinator.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(mc_lines):
    if "def _boot_target_mode" in l or "def _mount_session" in l:
        for k in range(idx, min(idx + 25, len(mc_lines))):
            print(f"{k+1:4d} | {mc_lines[k]}")

print("\n=== 2. ui/main_window.py: все вызовы _update_mode_ui ===")
mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, l in enumerate(mw_lines):
    if "_update_mode_ui" in l:
        print(f"Строка {idx+1:4d} | {l.strip()}")

print("\n=== 3. ui/main_window.py: вызовы transition_to ===")
for idx, l in enumerate(mw_lines):
    if "transition_to" in l:
        print(f"Строка {idx+1:4d} | {l.strip()}")
