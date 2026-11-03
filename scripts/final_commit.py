import sys
import subprocess
from pathlib import Path

# Фиксируем все изменения
subprocess.run(["git", "add", "."], check=True)
subprocess.run(["git", "commit", "-m", "refactor(core): complete major architectural decoupling, eliminate shims, introduce UIContext (-4234 LOC)"], check=True)
print("[OK] Финальный коммит успешно зафиксирован!")
