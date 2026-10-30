import sys
import inspect
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=== 1. ГДЕ SessionManager ХРАНИТ СЕССИИ ===")
from services.session_manager import SessionManager
p_sm = Path("services/session_manager.py")
lines_sm = p_sm.read_text(encoding="utf-8").splitlines()
for i, l in enumerate(lines_sm, 1):
    if any(k in l for k in ("SESSIONS_DIR", "sessions_dir", "save_session", "def load_session")):
        print(f"  {i:4d}: {l.strip()[:100]}")

print("\n=== 2. ЛОГИКА RPCastManager: ОТКУДА БЕРЕТСЯ 'Спутница' (строки 40-155) ===")
p_cast = Path("services/rp_cast_manager.py")
lines_cast = p_cast.read_text(encoding="utf-8").splitlines()
for i in range(40, min(len(lines_cast), 155)):
    print(f"{i+1:4d}: {lines_cast[i]}")

print("\n=== 3. КАК СТРОИТСЯ БЛОК get_cast_prompt_block (строки 300-360) ===")
for i in range(299, min(len(lines_cast), 360)):
    print(f"{i+1:4d}: {lines_cast[i]}")
