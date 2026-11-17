import json
from pathlib import Path

ROOT = Path(".")
sid = "551c6702-3139-4953-a36e-18c9d3da230b"

print("=== .neonroute_last_failure.json ===")
lf = ROOT / "workspace" / "projects" / sid / ".neonroute_last_failure.json"
print(lf.read_text(encoding="utf-8", errors="replace") if lf.exists() else "нет")

print("\n=== history/chat_...json ===")
jf = ROOT / "history" / f"chat_{sid}.json"
data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
print("mode:", data.get("mode"), "| metadata:", data.get("metadata"))
for i, m in enumerate(data.get("messages", data.get("chat_history", [])), 1):
    print(f"\n--- Msg {i} [{m.get('role')}] ---")
    print(m.get("content") or m.get("text") or m)
