from pathlib import Path

print("=== 1. ПОДГОТОВКА ЗАДАЧИ В services/roleplay_flow_controller.py (строки 275-330) ===")
p1 = Path("services/roleplay_flow_controller.py")
lines1 = p1.read_text(encoding="utf-8").splitlines()
for i in range(274, min(len(lines1), 330)):
    print(f"{i+1:4d}: {lines1[i]}")

print("\n=== 2. СБОРКА КОНТЕКСТА В services/context_window_builder.py (строки 150-200) ===")
p2 = Path("services/context_window_builder.py")
lines2 = p2.read_text(encoding="utf-8").splitlines()
for i in range(149, min(len(lines2), 200)):
    print(f"{i+1:4d}: {lines2[i]}")
