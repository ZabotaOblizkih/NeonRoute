"""
scripts/patch_forge.py
======================
CLI-утилита проверки, резервного копирования и применения патчей совместимости NeonRoute
к локальной установке Stable Diffusion WebUI Forge.

Использование:
  python scripts/patch_forge.py --check
  python scripts/patch_forge.py --apply
  python scripts/patch_forge.py --restore
  python scripts/patch_forge.py --path C:\\neyroGPD --check
"""

import argparse
import sys
from pathlib import Path

# Добавляем корень репозитория в sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.forge_locator import find_forge_root
from services.forge_patcher import ForgePatcher, ForgePatchStatus


def main():
    parser = argparse.ArgumentParser(
        description="NeonRoute SD WebUI Forge Patch & Compatibility Utility"
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Путь к директории SD WebUI Forge (по умолчанию определяется автоматически через forge_locator)."
    )
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--check",
        action="store_true",
        help="Проверить текущий статус совместимости и наличие патчей (по умолчанию)."
    )
    action_group.add_argument(
        "--apply",
        action="store_true",
        help="Создать резервную копию и применить патчи совместимости."
    )
    action_group.add_argument(
        "--restore",
        action="store_true",
        help="Восстановить оригинальные файлы Forge из .neonroute_backup/original/."
    )

    args = parser.parse_args()

    # 1. Определение рабочей директории Forge
    forge_path = args.path
    if not forge_path:
        found = find_forge_root()
        if found:
            forge_path = str(found)

    if not forge_path:
        print("[ERROR] Не удалось автоматически обнаружить директорию SD WebUI Forge.")
        print("        Укажите путь явно: python scripts/patch_forge.py --path <ПУТЬ> ...")
        sys.exit(1)

    webui_dir = ForgePatcher.find_forge_webui_dir(forge_path)
    print("=" * 65)
    print("  NeonRoute — SD WebUI Forge Compatibility & Patch Manager")
    print("=" * 65)
    print(f"  Корень Forge:     {forge_path}")
    print(f"  Директория WebUI: {webui_dir or 'НЕ НАЙДЕНА'}")
    print(f"  Резервная копия:  {'ЕСТЬ (.neonroute_backup/original/)' if ForgePatcher.has_backup(forge_path) else 'НЕТ'}")
    print("-" * 65)

    status, msg = ForgePatcher.check_status(forge_path)
    print(f"  Статус:  [{status.value}]")
    print(f"  Инфо:    {msg}")
    print("=" * 65)

    if args.apply:
        print("\n[ACTION] Применение патчей совместимости...")
        ok, res_msg = ForgePatcher.apply_patch(forge_path)
        if ok:
            print(f"[SUCCESS] {res_msg}")
            sys.exit(0)
        else:
            print(f"[FAILURE] {res_msg}")
            sys.exit(2)

    elif args.restore:
        print("\n[ACTION] Восстановление оригинальных файлов из бэкапа...")
        ok, res_msg = ForgePatcher.restore_backup(forge_path)
        if ok:
            print(f"[SUCCESS] {res_msg}")
            sys.exit(0)
        else:
            print(f"[FAILURE] {res_msg}")
            sys.exit(2)

    else:
        # Default: check only
        if status == ForgePatchStatus.PATCHED:
            sys.exit(0)
        elif status == ForgePatchStatus.NEEDS_PATCH:
            print("\n[HINT] Для применения патча запустите:")
            print(f"       python scripts/patch_forge.py --path \"{forge_path}\" --apply")
            sys.exit(1)
        else:
            sys.exit(2)


if __name__ == "__main__":
    main()
