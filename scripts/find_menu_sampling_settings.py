import re
from pathlib import Path

print("=== 1. ПОИСК ДЕЙСТВИЯ В МЕНЮ (ui/builders/menu_builder.py / ui/main_window.py) ===")
menu_files = [
    Path("ui/builders/menu_builder.py"),
    Path("ui/main_window.py"),
    Path("ui/controllers/dialog_controller.py"),
]
for p in Path("ui").rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if any(w in txt.lower() for w in ("настройки текстовой модели", "диалоги", "default_sampling")):
            for i, line in enumerate(txt.splitlines()):
                if "настройки текстовой модели" in line.lower() or ("текстов" in line.lower() and "модел" in line.lower() and "action" in line.lower()):
                    print(f"[{p.as_posix()}:{i+1}] {line.strip()}")
    except Exception:
        pass

print("\n=== 2. ДИАЛОГИ НАСТРОЕК СЭМПЛИНГА И ИХ ОБРАБОТЧИКИ СОХРАНЕНИЯ ===")
dialog_paths = [
    Path("ui/dialogs/model_sampling_dialog.py"),
    Path("ui/dialogs/llm_engine_settings_dialog.py"),
    Path("ui/dialogs/user_settings_dialog.py"),
    Path("ui/controllers/dialog_controller.py")
]
for dp in dialog_paths:
    if dp.is_file():
        txt = dp.read_text(encoding="utf-8", errors="ignore")
        lines = txt.splitlines()
        print(f"\n--- Анализ {dp.as_posix()} ---")
        for i, l in enumerate(lines):
            if any(k in l for k in ("save_config", "ConfigManager", "default_sampling", "temperature", "apply_settings", "accept")):
                if any(m in l for m in ("def ", "ConfigManager.", "cfg[", "config[")):
                    s = max(0, i - 1)
                    e = min(len(lines), i + 8)
                    print(f"  Строка {i+1}:")
                    for idx in range(s, e):
                        print(f"    {idx+1:4d} | {lines[idx]}")

print("\n=== 3. ПОИСК МЕТОДОВ ОТКРЫТИЯ ДИАЛОГА В DIALOG_CONTROLLER ===")
dc_path = Path("ui/controllers/dialog_controller.py")
if dc_path.is_file():
    lines = dc_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, l in enumerate(lines):
        if any(w in l.lower() for w in ("sampling", "llm_engine", "text_model", "open_model_settings")):
            s = max(0, i - 2)
            e = min(len(lines), i + 12)
            print(f"\n--- ui/controllers/dialog_controller.py (строки {s+1}..{e}) ---")
            for idx in range(s, e):
                print(f"{idx+1:4d} | {lines[idx]}")
