from pathlib import Path

p_lms = Path("services/lm_studio.py")
lines = p_lms.read_text(encoding="utf-8").splitlines()

print("=== ПАРАМЕТРЫ СЭМПЛИНГА В services/lm_studio.py ===")
for i, l in enumerate(lines):
    if "dry_multiplier" in l or "xtc_probability" in l or "[LMS-PARAMS]" in l:
        for j in range(max(0, i - 5), min(len(lines), i + 35)):
            print(f"{j+1:4d}: {lines[j]}")
        break
