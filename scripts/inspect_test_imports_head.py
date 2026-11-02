from pathlib import Path

test_file = Path("tests/test_rp_chat_markup.py")
for idx, line in enumerate(test_file.read_text(encoding="utf-8").splitlines()[:25]):
    print(f"{idx+1:4d}: {line.strip()}")
