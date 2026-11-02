from pathlib import Path

content = Path("tests/test_subagent_engineer_loop.py").read_text(encoding="utf-8")
print("--- Фрагменты моков из tests/test_subagent_engineer_loop.py ---")
for idx, line in enumerate(content.splitlines()):
    if "plan_architecture" in line or "mock" in line.lower() or "chat_completion" in line:
        print(f"{idx+1:4d}: {line.strip()}")
