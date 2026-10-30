from pathlib import Path

for p in Path("ui").rglob("*.py"):
    try:
        content = p.read_text(encoding="utf-8")
        if "CharactersGalleryDialog" in content:
            print(f"[+] В файле {p}:")
            for idx, line in enumerate(content.splitlines()):
                if "CharactersGalleryDialog" in line:
                    print(f"  {idx+1:4d}: {line.strip()}")
    except Exception:
        pass
