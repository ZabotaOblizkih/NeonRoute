from pathlib import Path

ec_path = Path("services/engineer_context.py")
if ec_path.is_file():
    lines = ec_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    print("=== inspect_workspace_file в services/engineer_context.py ===")
    capturing = False
    for idx, l in enumerate(lines):
        if "def inspect_workspace_file" in l:
            capturing = True
        if capturing:
            print(f"{idx+1:4d} | {l}")
            if idx > 0 and l.startswith("    def ") and "inspect_workspace_file" not in l:
                break
else:
    print("[-] services/engineer_context.py не найден")
