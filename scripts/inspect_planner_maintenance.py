from pathlib import Path

ep_lines = Path("services/engineer_planner.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/engineer_planner.py: сборка плана и чтение файлов ===")
for idx, l in enumerate(ep_lines):
    if any(k in l for k in ["ТЕКУЩАЯ ВЕРСИЯ ФАЙЛА", "is_maintenance", "write_file", "patch_file"]):
        for j in range(max(0, idx - 4), min(len(ep_lines), idx + 20)):
            print(f"{j+1:4d} | {ep_lines[j]}")
        print("-" * 50)
