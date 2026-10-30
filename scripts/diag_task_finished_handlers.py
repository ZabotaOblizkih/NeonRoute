from pathlib import Path

# 1. Инспектируем _on_done_wrapper в roleplay_controller.py
p_rpc = Path("ui/controllers/roleplay_controller.py")
if p_rpc.exists():
    lines_rpc = p_rpc.read_text(encoding="utf-8").splitlines()
    print("=== 1. UI/CONTROLLERS/ROLEPLAY_CONTROLLER.PY (СТРОКИ 410..470) ===")
    for i in range(max(0, 410), min(len(lines_rpc), 470)):
        print(f"{i+1:4d}: {lines_rpc[i]}")

# 2. Инспектируем _on_task_finished в main_window.py
p_mw = Path("ui/main_window.py")
if p_mw.exists():
    lines_mw = p_mw.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. UI/MAIN_WINDOW.PY: _on_task_finished ===")
    for i, l in enumerate(lines_mw):
        if "def _on_task_finished(" in l:
            for j in range(i, min(len(lines_mw), i + 35)):
                print(f"{j+1:4d}: {lines_mw[j]}")
            break

# 3. Как lm_studio.py возвращает результат стрима в chat_completion
p_lm = Path("services/lm_studio.py")
if p_lm.exists():
    lines_lm = p_lm.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. LM_STUDIO.PY: ЗАВЕРШЕНИЕ СТРИМА И RETURN В CHAT_COMPLETION ===")
    for i in range(680, min(len(lines_lm), 730)):
        print(f"{i+1:4d}: {lines_lm[i]}")
