import sys
import inspect
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

print("=" * 80)
print(" ТРАССИРОВКА БОЕВОГО РАНТАЙМА NEONROUTE ДЛЯ РЕЖИМА ROLEPLAY")
print("=" * 80)

# 1. Проверяем ContextWindowBuilder
cwb_path = ROOT / "services" / "context_window_builder.py"
if cwb_path.exists():
    print("\n[1. СБОРЩИК КОНТЕКСТА: services/context_window_builder.py]")
    lines = cwb_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if any(k in line.lower() for k in ["build", "prepare", "messages", "anchor", "token", "system"]):
            if "def " in line or "class " in line or "messages.append" in line or "role" in line:
                print(f"  L{i+1}: {line.strip()[:100]}")
else:
    print("\n❌ context_window_builder.py не найден.")

# 2. Проверяем методы отправки в RoleplayFlowController
rfc_path = ROOT / "services" / "roleplay_flow_controller.py"
if rfc_path.exists():
    print("\n[2. ОРКЕСТРАТОР: services/roleplay_flow_controller.py]")
    lines = rfc_path.read_text(encoding="utf-8").splitlines()
    # Ищем основной метод обработки хода (send_message / execute_turn / generate)
    for i, line in enumerate(lines):
        if line.strip().startswith("def ") and any(k in line for k in ["send", "turn", "generate", "process", "chat"]):
            print(f"  L{i+1}: {line.strip()}")

# 3. Ищем, как данные реально передаются в воркер/клиент LM Studio
print("\n[3. ПОИСК ВЫЗОВА LLM КЛИЕНТА В FLOW CONTROLLER]")
if rfc_path.exists():
    lines = rfc_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if any(k in line for k in ["worker", "client", "post", "stream", "generate_stream", "chat_completion"]):
            if "=" in line or "(" in line:
                print(f"  L{i+1}: {line.strip()[:100]}")

print("\n" + "=" * 80)
