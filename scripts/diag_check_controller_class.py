from pathlib import Path

p_fc = Path("services/roleplay_flow_controller.py")
if p_fc.exists():
    lines = p_fc.read_text(encoding="utf-8").splitlines()
    print("=== КЛАССЫ И МЕТОДЫ В services/roleplay_flow_controller.py ===")
    for i, l in enumerate(lines[:60], 1):
        if any(l.strip().startswith(k) for k in ("class ", "def _generate_ephemeral_rp_art_prompt")):
            print(f"{i:3d}: {l.strip()}")

p_trf = Path("tests/test_roleplay_flow.py")
if p_trf.exists():
    print("\n=== КАК ИМПОРТИРУЕТСЯ В tests/test_roleplay_flow.py ===")
    for l in p_trf.read_text(encoding="utf-8").splitlines()[:25]:
        if "roleplay_flow" in l or "import " in l:
            print(f"  {l.strip()}")
