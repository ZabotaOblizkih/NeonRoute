import json
import re
from pathlib import Path

print("=== 1. ПРОВЕРКА DEFAULT SAMPLING PARAMS В SESSION MANAGER ===")
sm_path = Path("services/session_manager.py")
if sm_path.is_file():
    content = sm_path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if "get_default_sampling_params" in line or "default_sampling" in line.lower():
            s = max(0, i - 2)
            e = min(len(lines), i + 25)
            print(f"\n--- services/session_manager.py (строки {s+1}..{e}) ---")
            for idx in range(s, e):
                print(f"{idx+1:4d} | {lines[idx]}")
            break

print("\n=== 2. ПРОВЕРКА CONFIG.JSON НА НАЛИЧИЕ НАСТРОЕК РЕЖИМА ИНЖЕНЕР ===")
cfg_path = Path("config.json")
if cfg_path.is_file():
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8", errors="ignore"))
        print("Ключи в config.json:")
        for k in cfg.keys():
            if any(w in k.lower() for w in ("sampling", "engineer", "model", "temp", "param")):
                print(f"  • {k}: {cfg[k]}")
    except Exception as e:
        print(f"Ошибка чтения config.json: {e}")

print("\n=== 3. ПОИСК ДИАЛОГА НАСТРОЙКИ СЭМПЛИНГА ЧАТА (КЛИК ПО АВАТАРКЕ) ===")
dialog_candidates = [
    Path("ui/dialogs/model_sampling_dialog.py"),
    Path("ui/dialogs/llm_engine_settings_dialog.py"),
    Path("ui/components/bubbles/avatar_renderer.py"),
    Path("ui/components/chat_message_widget.py"),
]
for p in Path("ui").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "sampling_params" in txt and ("contextMenu" in txt or "customContextMenuRequested" in txt or "dialog" in p.name.lower()):
            print(f"  Найден файл с привязкой sampling_params: {p.as_posix()}")
    except Exception:
        pass

print("\n=== 4. АРХИТЕКТОР: ПЕРЕДАЧА ТЕМПЕРАТУРЫ В ENGINEER PLANNER ===")
ep_path = Path("services/engineer_planner.py")
if ep_path.is_file():
    lines = ep_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, line in enumerate(lines):
        if "temperature" in line or "plan_architecture" in line:
            s = max(0, i - 2)
            e = min(len(lines), i + 10)
            print(f"\n--- services/engineer_planner.py (строки {s+1}..{e}) ---")
            for idx in range(s, e):
                print(f"{idx+1:4d} | {lines[idx]}")

print("\n=== 5. ИНЖЕНЕР: ЧТЕНИЕ И ЗАЖИМ ТЕМПЕРАТУРЫ В SUBAGENT ENGINEER ===")
se_path = Path("services/subagent_engineer.py")
if se_path.is_file():
    lines = se_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, line in enumerate(lines):
        if "worker_temp" in line or "sampling_params" in line:
            s = max(0, i - 3)
            e = min(len(lines), i + 15)
            print(f"\n--- services/subagent_engineer.py (строки {s+1}..{e}) ---")
            for idx in range(s, e):
                print(f"{idx+1:4d} | {lines[idx]}")
            break
