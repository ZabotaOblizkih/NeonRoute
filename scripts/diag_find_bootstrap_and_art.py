from pathlib import Path

print("=== 1. ПОИСК ВЫЗОВОВ bootstrap_party_from_card ПО ВСЕМУ ПРОЕКТУ ===")
for p in Path(".").rglob("*.py"):
    if "venv" in str(p) or ".git" in str(p):
        continue
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "bootstrap_party_from_card" in txt:
        for i, l in enumerate(txt.splitlines(), 1):
            if "bootstrap_party_from_card" in l and not l.strip().startswith("def "):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")

print("\n=== 2. КАК roleplay_flow_controller ГОТОВИТ last_assistant_message ДЛЯ АРТА ===")
p_fc = Path("services/roleplay_flow_controller.py")
if p_fc.exists():
    lines = p_fc.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if "clean_visual_summary" in l or "payload_lines =" in l:
            for j in range(max(0, i-5), min(len(lines), i+30)):
                print(f"{j+1:4d}: {lines[j]}")
            break
