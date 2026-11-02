from pathlib import Path

lines = Path("services/tool_engine.py").read_text(encoding="utf-8").splitlines()
# Печатаем метод check_artifact_substance (строки 505-560)
for idx in range(504, min(len(lines), 560)):
    l = lines[idx]
    if l.strip():
        print(f"{idx+1:4d}: {l}")
