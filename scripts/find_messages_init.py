from pathlib import Path
lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8", errors="ignore").splitlines()

# Поиск точек сборки сообщений и промптов перед циклом
targets = []
for idx, line in enumerate(lines, 1):
    if idx < 580 and any(k in line for k in ("messages =", "system_prompt =", "worker_temp =", "engineer_system_prompt")):
        targets.append(idx)

print("Найдены строки конфигурации промптов:", targets)
if targets:
    start_l = max(0, min(targets) - 10)
    end_l = min(len(lines), max(targets) + 30)
    print(f"\n=== services/subagent_engineer.py (строки {start_l}-{end_l}) ===")
    for j in range(start_l - 1, end_l):
        print(f"{j+1:4d} | {lines[j]}")
