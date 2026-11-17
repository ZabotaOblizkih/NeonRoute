from pathlib import Path

lines = Path("services/engineer_tdd.py").read_text(encoding="utf-8", errors="replace").splitlines()
print("=== services/engineer_tdd.py L50..100 ===")
for i in range(50, min(len(lines), 100) + 1):
    print(f"L{i:04d}: {lines[i-1]}")

print("\n=== Ссылки на engineer_tdd / CodeFirstTDDPipeline в tests/ ===")
for tf in sorted(Path("tests").rglob("*.py")):
    txt = tf.read_text(encoding="utf-8", errors="replace")
    if "engineer_tdd" in txt or "CodeFirstTDDPipeline" in txt:
        print(f" - {tf}")
