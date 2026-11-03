import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

from services.lm_studio import LMStudioClient

methods = [m for m in dir(LMStudioClient) if not m.startswith("_") and callable(getattr(LMStudioClient, m))]
print(f"[*] Публичные методы LMStudioClient ({len(methods)}):")
for m in sorted(methods):
    print(f"    • {m}")
