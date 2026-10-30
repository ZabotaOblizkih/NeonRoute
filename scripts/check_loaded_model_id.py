import json
import urllib.request
from pathlib import Path

print("=== 1. МОДЕЛИ, ЗАГРУЖЕННЫЕ В РАБОТАЮЩИЙ LLAMA-SERVER (1234) ===")
try:
    req = urllib.request.Request("http://127.0.0.1:1234/v1/models")
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(json.dumps(data, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"[-] Ошибка опроса /v1/models: {e}")

print("\n=== 2. ПОИСК ФАЙЛОВ .GGUF В ХРАНИЛИЩЕ .LMSTUDIO ===")
lm_models = Path.home() / ".lmstudio" / "models"
if lm_models.exists():
    for f in lm_models.rglob("*.gguf"):
        if "mmproj" not in f.name.lower():
            print(f"  • {f.name}")
            print(f"    Путь: {f}")
else:
    print("[-] Папка .lmstudio/models не существует")
