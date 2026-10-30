from pathlib import Path

lines = Path("services/card_translator.py").read_text(encoding="utf-8").splitlines()
constants = ["PROMPT_LITERARY_PROSE", "PROMPT_FIRST_MES", "PROMPT_MES_EXAMPLE"]

for const in constants:
    print(f"\n{'='*70}\n{const}\n{'='*70}")
    capturing = False
    for idx, line in enumerate(lines):
        if line.startswith(f"{const} ="):
            capturing = True
        if capturing:
            print(f"{idx+1:4d}: {line}")
            if line.rstrip().endswith('"""') and not line.startswith(f"{const} ="):
                break
