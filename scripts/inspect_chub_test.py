from pathlib import Path

lines = Path("tests/test_rp_card_integrity.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "def test_chub_client_lossless_download" in line:
        for j in range(idx, min(len(lines), idx + 60)):
            print(f"{j+1:4d}: {lines[j]}")
        break
