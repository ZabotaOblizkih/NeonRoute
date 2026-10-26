import json
import sys
from pathlib import Path

root_dir = Path(".").resolve()
sys.path.insert(0, str(root_dir))

print("=" * 70)
print("1. АНАЛИЗ ПОСЛЕДНЕЙ СЕССИИ В history/")
print("=" * 70)

history_dir = root_dir / "history"
session_files = sorted(history_dir.glob("chat_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if session_files:
    latest = session_files[0]
    print(f"Файл сессии: {latest.name} (изменен: {latest.stat().st_mtime})")
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        msgs = data.get("messages", [])
        print(f"Всего сообщений в сессии: {len(msgs)}")
        # Выводим последние 3 сообщения
        for i, m in enumerate(msgs[-3:], start=max(0, len(msgs)-3)):
            sender = m.get("sender") or m.get("role")
            th = m.get("thought")
            txt = m.get("text") or m.get("content") or ""
            print(f"\n--- Сообщение [{i}] {sender} ---")
            print(f"  text (len={len(txt)}): {repr(txt[:140])}")
            if th:
                if isinstance(th, dict):
                    th_c = th.get("content", "")
                    print(f"  thought: dict | duration={th.get('duration')}s | tokens={th.get('token_count')}")
                    print(f"  thought.content (len={len(th_c)}): {repr(th_c[:140])}")
                else:
                    print(f"  thought: {repr(str(th)[:140])}")
            else:
                print("  thought: None / False")
    except Exception as e:
        print(f"Ошибка чтения сессии: {e}")
else:
    print("Файлы сессий не найдены в history/")

print("\n" + "=" * 70)
print("2. ТОЧКА СОЗДАНИЯ _thought_stream_parser В assistant_flow_controller.py")
print("=" * 70)
af_path = root_dir / "ui" / "controllers" / "assistant_flow_controller.py"
if af_path.exists():
    lines = af_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx in range(535, min(len(lines), 575)):
        print(f"{idx+1:04d}: {lines[idx]}")

print("\n" + "=" * 70)
print("3. ПОСЛЕДНИЕ СОБЫТИЯ ИЗ logs/neonroute.log")
print("=" * 70)
log_path = root_dir / "logs" / "neonroute.log"
if log_path.exists():
    log_lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for l in log_lines[-25:]:
        print(l)
else:
    print("Файл logs/neonroute.log не найден.")

print("=" * 70)
