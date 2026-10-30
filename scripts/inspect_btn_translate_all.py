from pathlib import Path

def find_in_files(dir_path, search_str):
    print("=" * 70)
    print(f"  ПОИСК '{search_str}' в {dir_path}")
    print("=" * 70)
    for p in Path(dir_path).rglob("*.py"):
        try:
            content = p.read_text(encoding="utf-8")
            if search_str in content:
                print(f"\n[+] Файл: {p}")
                for idx, line in enumerate(content.splitlines()):
                    if search_str in line or "btn_translate_all" in line:
                        print(f"  {idx+1:4d}: {line}")
        except Exception:
            pass

find_in_files("ui/dialogs/gallery", "btn_translate_all")
