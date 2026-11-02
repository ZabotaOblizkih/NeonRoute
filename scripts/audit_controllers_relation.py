import sys
import inspect
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

print("=" * 80)
print("1. АРХИТЕКТУРА И СВЯЗЬ CHAT_FLOW VS ROLEPLAY_FLOW")
print("=" * 80)

# Проверяем файлы контроллеров
flow_files = list((ROOT / "services").glob("*flow*.py")) + list((ROOT / "ui" / "controllers").glob("*flow*.py"))
for f in flow_files:
    print(f"• Найден контроллер: {f.relative_to(ROOT)}")

# Изучаем ChatFlowController
cfc_path = None
for p in [ROOT / "services" / "chat_flow_controller.py", ROOT / "ui" / "controllers" / "chat_flow_controller.py"]:
    if p.exists():
        cfc_path = p
        break

if cfc_path:
    txt = cfc_path.read_text(encoding="utf-8")
    print(f"\n[ChatFlowController в {cfc_path.relative_to(ROOT)}]")
    for line in txt.splitlines():
        if "class " in line or "def send_message" in line or "def _enqueue" in line or "def enqueue" in line:
            print(f"  {line.strip()}")

# Изучаем RoleplayFlowController
rfc_path = None
for p in [ROOT / "services" / "roleplay_flow_controller.py", ROOT / "ui" / "controllers" / "roleplay_flow_controller.py"]:
    if p.exists():
        rfc_path = p
        break

if rfc_path:
    txt = rfc_path.read_text(encoding="utf-8")
    print(f"\n[RoleplayFlowController в {rfc_path.relative_to(ROOT)}]")
    for line in txt.splitlines():
        if "class RoleplayFlowController" in line:
            print(f"  {line.strip()} (Наследование)")

print("\n" + "=" * 80)
print("2. УСЛОВИЕ СРАБАТЫВАНИЯ _should_trigger_roleplay_art")
print("=" * 80)
if rfc_path:
    lines = rfc_path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if "def _should_trigger_roleplay_art" in line:
            for j in range(idx, min(len(lines), idx + 25)):
                print(f"  L{j+1}: {lines[j]}")
            break

print("\n" + "=" * 80)
print("3. ПРОВЕРКА МУТАЦИИ self.chat_history (ПРОТЕКАЕТ ЛИ СКЛЕЙКА В ИСТОРИЮ)")
print("=" * 80)
if rfc_path:
    lines = rfc_path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if "_sync_turn" in line or "save_session" in line or "self.chat_history.append" in line:
            print(f"  L{idx+1}: {line.strip()[:100]}")

print("\n" + "=" * 80)
