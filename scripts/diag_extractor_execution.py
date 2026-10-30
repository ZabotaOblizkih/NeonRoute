from pathlib import Path

# 1. Как устроен блок вызова LLM и завершения в RPCastExtractor._run_extraction
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines_ext = p_ext.read_text(encoding="utf-8").splitlines()
    print("=== 1. SERVICES/RP_CAST_EXTRACTOR.PY: СТРОКИ 400..530 ===")
    for i in range(399, min(len(lines_ext), 530)):
        print(f"{i+1:4d}: {lines_ext[i]}")

# 2. Как TaskQueueWorker выполняет задачи в _worker_loop
p_tq = Path("services/task_queue.py")
if p_tq.exists():
    lines_tq = p_tq.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. SERVICES/TASK_QUEUE.PY: _worker_loop (СТРОКИ 170..240) ===")
    for i in range(169, min(len(lines_tq), 240)):
        print(f"{i+1:4d}: {lines_tq[i]}")
