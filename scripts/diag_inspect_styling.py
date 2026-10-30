from pathlib import Path
import re

print("=== 1. ПОИСК ОБРАБОТКИ СТАТУСОВ И БЛОКОВ КОДА В UI ===")
for p in Path("ui").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt.lower() for k in ("<stats>", "stats", "codeblock", "markdown", "highlight", "fluent")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(term in l.lower() for term in ("<stats>", "stats-block", "qtextbrowser", "code-block", "border-radius", "setmarkdown", "tohtml")):
                print(f"{p.name}:{i:4d}: {l.strip()[:110]}")

print("\n=== 2. ПОИСК ФИЛЬТРОВ И ФОРМАТТЕРОВ ТЕКСТА (TextRegexFilter / Markdown) ===")
for p in Path("services").rglob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt for k in ("TextRegexFilter", "format_message", "render_markdown", "highlight_syntax")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(term in l for k in ("TextRegexFilter", "def format", "def process_output", "def render")):
                print(f"{p.name}:{i:4d}: {l.strip()[:110]}")

print("\n=== 3. СВЕЖИЙ GIT DIFF ПО services/roleplay_state_manager.py ===")
p_sm = Path("services/roleplay_state_manager.py")
if p_sm.exists():
    print(p_sm.read_text(encoding="utf-8"))
