from pathlib import Path

# 1. Поиск CardProgressBridge и card_translator в roleplay_controller.py
rp_content = Path("ui/controllers/roleplay_controller.py").read_text(encoding="utf-8")
print("CardProgressBridge in controller:", "CardProgressBridge" in rp_content)
for idx, line in enumerate(rp_content.splitlines()):
    if "card_translator" in line or "CardProgressBridge" in line:
        print(f"  {idx+1:4d}: {line}")

# 2. Проверяем git diff по chub_client.py
import subprocess
diff = subprocess.run(["git", "diff", "services/chub_client.py"], capture_output=True, text=True).stdout
print("\n=== git diff services/chub_client.py ===")
print(diff if diff else "No changes in chub_client.py")
