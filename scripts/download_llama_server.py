"""
scripts/download_llama_server.py
================================
CLI утилита для автоматической или ручной загрузки llama-server.exe в папку bin/.
Поддерживает флаги:
  --if-missing    Ничего не делать, если bin/llama-server.exe уже установлен
  --vendor        Принудительный выбор типа сборки (nvidia / amd / intel / cpu)
  --target-dir    Пользовательский путь назначения (по умолчанию bin/)
  --skip-llama    Принудительный пропуск скачивания
"""

import argparse
import os
import sys
from pathlib import Path

# Корень проекта
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config
from services.llama_downloader import (
    is_llama_server_installed,
    download_and_extract_llama_server,
    resolve_required_build,
)


def main():
    parser = argparse.ArgumentParser(description="Автозагрузчик llama-server для NeonRoute")
    parser.add_argument("--if-missing", action="store_true", help="Пропустить, если llama-server уже установлен")
    parser.add_argument("--vendor", type=str, default=None, help="Принудительный вендор (nvidia, amd, intel, cpu)")
    parser.add_argument("--target-dir", type=str, default=None, help="Целевая директория (по умолчанию bin/)")
    parser.add_argument("--skip-llama", action="store_true", help="Пропустить скачивание")
    args = parser.parse_args()

    # Проверка переменной окружения или флага пропуска
    if args.skip_llama or os.environ.get("NEONROUTE_SKIP_LLAMA") == "1":
        print("[INFO] Скачивание llama-server пропущено по запросу пользователя (--skip-llama / NEONROUTE_SKIP_LLAMA=1).")
        sys.exit(0)

    target_path = Path(args.target_dir).resolve() if args.target_dir else config.LOCAL_BIN_DIR

    if args.if_missing and is_llama_server_installed(target_path):
        print(f"[OK] llama-server.exe уже установлен в {target_path}, скачивание не требуется.")
        sys.exit(0)

    build_type = resolve_required_build(args.vendor)
    print("=======================================================")
    print("        NeonRoute - Загрузчик движка llama-server")
    print("=======================================================")
    print(f"  Целевая папка : {target_path}")
    print(f"  Тип сборки    : {build_type.upper()}")
    print("  Поиск релизов на GitHub ggml-org/llama.cpp ...")

    def _progress(msg, cur, tot):
        if tot > 0:
            pct = (cur / tot) * 100
            print(f"\r  {msg} [{pct:.1f}%] ({cur // (1024*1024)}/{tot // (1024*1024)} МБ)", end="", flush=True)
        else:
            print(f"\r  {msg}", end="", flush=True)

    success, info = download_and_extract_llama_server(
        target_dir=target_path,
        vendor=args.vendor,
        progress_callback=_progress
    )
    print()

    if success:
        print(f"[SUCCESS] {info}")
        sys.exit(0)
    else:
        if args.if_missing:
            print(f"[WARNING] Не удалось автоматически загрузить llama-server: {info}")
            print("[INFO] Вы можете загрузить его позже в меню: Параметры и настройки -> Движок LLM.")
            sys.exit(0)
        else:
            print(f"[ERROR] Ошибка загрузки llama-server: {info}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
