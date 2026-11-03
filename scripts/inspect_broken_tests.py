import sys
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

broken = [
    "tests/smoke/scripts/test_tool_engine_baseline.py",
    "tests/smoke/test_smoke_mainwindow_lifecycle.py",
    "tests/smoke/test_smoke_subagent_engineer.py",
    "tests/smoke/test_smoke_tool_engine.py",
    "tests/test_history_sanitizer_e2e.py",
    "tests/test_rp_art_prompt_injection.py",
    "tests/test_rp_chat_markup.py",
    "tests/test_shutdown_lifecycle.py",
    "tests/test_artdirector_skoz.py",
    "tests/test_cinematic_pipeline.py",
]

print("=== ДИАГНОСТИКА ПРОБЛЕМНЫХ ФАЙЛОВ В tests/ ===")
for rel in broken:
    p = PROJECT_ROOT / rel
    if not p.exists():
        print(f"{rel}: НЕ СУЩЕСТВУЕТ")
        continue
    raw = p.read_bytes()
    has_nulls = b"\x00" in raw
    try:
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or has_nulls else "utf-8")
        tree = ast.parse(text.lstrip("\ufeff"))
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        print(f"{rel}: size={len(raw)}b, utf16/nulls={has_nulls}, AST OK после чистки BOM/UTF16, funcs={funcs[:5]}")
    except Exception as e:
        print(f"{rel}: size={len(raw)}b, utf16/nulls={has_nulls}, ERROR={e}")
