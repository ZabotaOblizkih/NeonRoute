from pathlib import Path

# 1. Проверяем InferenceTask.__init__ в task_queue.py
p_tq = Path("services/task_queue.py")
if p_tq.exists():
    lines = p_tq.read_text(encoding="utf-8").splitlines()
    print("=== 1. INFERENCETASK.__INIT__ В SERVICES/TASK_QUEUE.PY ===")
    for i, l in enumerate(lines):
        if "class InferenceTask" in l:
            for j in range(i, min(len(lines), i + 30)):
                print(f"{j+1:4d}: {lines[j]}")
            break

# 2. Ищем существующие лог-файлы в проекте
print("\n=== 2. СВЕЖИЕ ЛОГ-ФАЙЛЫ В ПРОЕКТЕ ===")
logs = list(Path(".").rglob("*.log"))
if logs:
    for lg in sorted(logs, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
        print(f"  {lg} ({lg.stat().st_size} байт, изменен: {lg.stat().st_mtime})")
        # Поищем внутри упоминания RPCastExtractor или ошибки TaskQueue
        try:
            content = lg.read_text(encoding="utf-8", errors="ignore")
            matches = [line for line in content.splitlines() if any(k in line for k in ("RPCast", "TaskQueue", "memory", "10061"))]
            if matches:
                print(f"    Найдено {len(matches)} релевантных записей. Последние 3:")
                for m in matches[-3:]:
                    print(f"      {m[:120]}")
        except Exception as e:
            print(f"    Ошибка чтения лога: {e}")
else:
    print("Лог-файлы *.log не найдены.")

# 3. Как NeonRoute запускает llama-server и на каком порту
print("\n=== 3. КОНФИГУРАЦИЯ LLAMA-SERVER И ЭНДПОИНТА ===")
for p in Path("services").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if "llama-server" in txt or "DEFAULT_LM_STUDIO_URL" in txt:
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l for k in ("1234", "8080", "base_url", "llama-server.exe", "DEFAULT_LM_STUDIO_URL")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")
