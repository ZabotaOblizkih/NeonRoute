from pathlib import Path

# 1. Параметры llm_kwargs и создание задачи в roleplay_flow_controller.py
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines_rp = p_rp.read_text(encoding="utf-8").splitlines()
    print("=== 1. ROLEPLAY_FLOW_CONTROLLER: СТРОКИ 275..325 ===")
    for i in range(274, min(len(lines_rp), 325)):
        print(f"{i+1:4d}: {lines_rp[i]}")

# 2. Что за метод на строках 1280..1335 в chat_flow_controller.py
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines_cf = p_cf.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. CHAT_FLOW_CONTROLLER: СТРОКИ 1280..1335 ===")
    for i in range(1279, min(len(lines_cf), 1335)):
        print(f"{i+1:4d}: {lines_cf[i]}")

# 3. Как TaskQueue оповещает о завершении задачи (task_finished)
p_tq = Path("services/task_queue.py")
if p_tq.exists():
    lines_tq = p_tq.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. TASK_QUEUE: СИГНАЛЫ И ЗАВЕРШЕНИЕ ЗАДАЧИ ===")
    for i, l in enumerate(lines_tq):
        if any(k in l for k in ("task_finished", "task_completed", "emit", "on_finished")):
            print(f"task_queue.py:{i+1:4d}: {l.strip()[:100]}")
