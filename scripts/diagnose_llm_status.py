import json
import subprocess
from pathlib import Path

print("=== 1. ПОИСК ПРОЦЕССОВ LLAMA-SERVER ===")
try:
    ps_out = subprocess.check_output(
        ["powershell", "-Command", "Get-Process llama* -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path | Format-Table -AutoSize"],
        text=True, encoding="utf-8"
    )
    print(ps_out.strip() if ps_out.strip() else "  [-] Процессы llama* не найдены в памяти.")
except Exception as e:
    print(f"  Ошибка проверки процессов: {e}")

print("\n=== 2. СЛУШАЮЩИЕ ПОРТЫ LOCALHOST (8000-8090, 5000, 11434, 1234) ===")
try:
    net_out = subprocess.check_output(
        ["powershell", "-Command", "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8080,8081,8000,5000,11434,1234 } | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize"],
        text=True, encoding="utf-8"
    )
    print(net_out.strip() if net_out.strip() else "  [-] На стандартных портах LLM ничего не слушает.")
except Exception as e:
    print(f"  Ошибка проверки портов: {e}")

print("\n=== 3. КОНФИГУРАЦИЯ NEONROUTE (HOST / PORT / LM_CLIENT) ===")
for cfg_path in [Path("config.json"), Path("settings.json"), Path("config/config.json")]:
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            print(f"  [+] Найдено в {cfg_path}:")
            for k, v in cfg.items():
                if any(x in k.lower() for x in ("server", "port", "host", "api", "url", "model", "llm")):
                    print(f"      {k}: {v}")
        except Exception as e:
            print(f"  Ошибка чтения {cfg_path}: {e}")

# Ищем в коде lm_client дефолтный URL
for p in Path("services").glob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8")
        for line in txt.splitlines():
            if "http://" in line and any(x in line for x in ("8080", "127.0.0.1", "localhost", "base_url", "port")):
                print(f"  [+] В {p.name}: {line.strip()}")
    except Exception:
        pass
