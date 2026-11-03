import re
from pathlib import Path

mixin_files = list(Path("ui/controllers").glob("*.py")) + list(Path("ui/builders").glob("*.py"))

common_fields = [
    "current_session",
    "_turn_bubble",
    "_current_assistant_bubble",
    "is_busy",
    "is_generating",
    "task_queue",
    "lm_client",
    "forge_client",
    "mode_coordinator",
    "chat_input",
    "chat_stream_widget",
    "workspace_inspector"
]

print(f"{'Миксин / Файл':<38} | " + " | ".join(f"{f[:8]:<8}" for f in common_fields))
print("-" * 155)

for mf in sorted(mixin_files):
    if mf.name == "__init__.py":
        continue
    txt = mf.read_text(encoding="utf-8", errors="ignore")
    row = [f"{mf.name:<38}"]
    for f in common_fields:
        count = len(re.findall(r'\bself\.' + re.escape(f) + r'\b', txt))
        val = str(count) if count > 0 else "."
        row.append(f"{val:<8}")
    print(" | ".join(row))
