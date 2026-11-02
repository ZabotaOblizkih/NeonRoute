from pathlib import Path

files = [
    "services/tool_engine.py",
    "services/subagent_engineer.py",
    "services/engineer_stream.py",
    "services/engineer_review.py",
    "services/engineer_parser.py",
    "tests/smoke/test_smoke_tool_engine.py",
    "tests/smoke/test_smoke_subagent_engineer.py"
]

print(f"{'Файл':<48} | {'Всего':<6} | {'Код':<6} | {'Статус':<15}")
print("-" * 80)

for f in files:
    p = Path(f)
    if p.exists():
        lines = p.read_text(encoding="utf-8").splitlines()
        code_lines = len([l for l in lines if l.strip()])
        status = "Облегчен" if "subagent" in f or "tool_engine" in f else "Выделенный модуль"
        if "test" in f:
            status = "Смок-тест"
        print(f"{f:<48} | {len(lines):<6} | {code_lines:<6} | {status:<15}")
