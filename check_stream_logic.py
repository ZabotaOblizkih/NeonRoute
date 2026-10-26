from pathlib import Path

print("=" * 70)
print("1. ЛОГИКА ФИНАЛИЗАЦИИ В assistant_flow_controller.py (строки 895-945)")
print("=" * 70)
ctrl_path = Path("ui/controllers/assistant_flow_controller.py")
if ctrl_path.exists():
    lines = ctrl_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i in range(max(0, 895 - 1), min(len(lines), 945)):
        print(f"[{i+1:4d}] {lines[i]}")

print("\n" + "=" * 70)
print("2. ПЕРЕХВАТ ЧАНКОВ (on_chunk / on_llm_chunk)")
print("=" * 70)
if ctrl_path.exists():
    for idx, line in enumerate(lines, 1):
        if any(k in line for k in ["def on_llm_chunk", "def _on_chunk", "def on_chunk", "_thought_stream_parser"]):
            start = max(0, idx - 2)
            end = min(len(lines), idx + 20)
            print(f"--- Блок около строки {idx} ---")
            for j in range(start, end):
                print(f"[{j+1:4d}] {lines[j]}")
            print()

print("=" * 70)
print("3. КОД services/thought_stream_parser.py")
print("=" * 70)
parser_path = Path("services/thought_stream_parser.py")
if parser_path.exists():
    print(parser_path.read_text(encoding="utf-8", errors="ignore"))
else:
    print("services/thought_stream_parser.py не найден")
