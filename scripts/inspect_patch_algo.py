from pathlib import Path

pe_lines = Path("services/patch_engine.py").read_text(encoding="utf-8", errors="ignore").splitlines()
print("=== services/patch_engine.py: логика сопоставления патча ===")
for idx, l in enumerate(pe_lines):
    if any(k in l for k in ["def _apply_patch", "def _apply_search_replace", "def patch_file_transactional"]):
        for j in range(idx, min(len(pe_lines), idx + 35)):
            print(f"{j+1:4d} | {pe_lines[j]}")
        print("-" * 50)
