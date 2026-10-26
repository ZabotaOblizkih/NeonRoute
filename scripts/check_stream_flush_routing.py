from pathlib import Path

cf_path = Path("ui/controllers/chat_flow_controller.py")
cf_lines = cf_path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=" * 70)
print("АНАЛИЗ _flush_stream_buffer И СТРИМИНГА ТОКЕНОВ (строки 960-1065)")
print("=" * 70)

print("\n[1] Окончание _flush_stream_buffer (строки 960-1015):")
for idx in range(959, min(len(cf_lines), 1015)):
    print(f"{idx+1:04d}: {cf_lines[idx]}")

print("\n[2] Прием чанков потока (строки 1015-1070):")
for idx in range(1014, min(len(cf_lines), 1070)):
    print(f"{idx+1:04d}: {cf_lines[idx]}")

print("=" * 70)
