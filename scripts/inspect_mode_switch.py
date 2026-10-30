from pathlib import Path

mw_lines = Path("ui/main_window.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== ui/main_window.py (строки 1395-1425) ===")
for i in range(1394, min(1425, len(mw_lines))):
    print(f"{i+1:4d} | {mw_lines[i]}")

mc_path = Path("services/mode_coordinator.py")
if mc_path.is_file():
    mc_lines = mc_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    print("\n=== services/mode_coordinator.py (from_string) ===")
    for idx, l in enumerate(mc_lines):
        if "def from_string" in l:
            for j in range(idx, min(idx + 25, len(mc_lines))):
                print(f"{j+1:4d} | {mc_lines[j]}")
            break
