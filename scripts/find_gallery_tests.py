from pathlib import Path

print("[*] Поиск тестов галереи персонажей:")
for p in Path("tests").glob("**/*.py"):
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "CharactersGalleryDialog" in txt or "CharacterCardWidget" in txt or "render_seamless_avatar" in txt:
            print(f"    {p}")
    except Exception:
        pass
