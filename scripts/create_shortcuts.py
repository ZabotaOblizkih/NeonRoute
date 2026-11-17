"""
create_shortcuts.py — Branded Windows shortcut generator for NeonRoute.
Creates:
  1) NeonRoute.lnk in BASE_DIR pointing to run.bat with APP_ICON_PATH.
  2) NeonRoute.lnk on Desktop (unless --no-desktop is passed or CI env is set).
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

# Add repo root to sys.path so config can be imported directly
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import BASE_DIR, APP_ICON_PATH, APP_NAME


def get_desktop_dir() -> Path:
    """
    Resolves Desktop directory using PowerShell [Environment]::GetFolderPath('Desktop')
    to properly handle OneDrive-redirected or localized Desktop paths.
    """
    try:
        ps_cmd = "[Environment]::GetFolderPath('Desktop')"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=True
        )
        desktop_str = result.stdout.strip()
        if desktop_str:
            p = Path(desktop_str)
            if p.exists():
                return p
    except Exception as e:
        print(f"[create_shortcuts] Warning resolving Desktop via PowerShell: {e}", file=sys.stderr)

    # Fallback to standard user desktop
    fallback = Path.home() / "Desktop"
    return fallback


def create_shortcut_powershell(
    target_path: Path,
    link_path: Path,
    working_dir: Path,
    icon_path: Path | None = None,
    description: str = ""
) -> bool:
    """
    Creates a Windows .lnk shortcut using WScript.Shell via PowerShell.
    """
    target_str = str(target_path.resolve())
    link_str = str(link_path.resolve())
    work_str = str(working_dir.resolve())
    icon_str = f"{icon_path.resolve()}, 0" if icon_path and icon_path.exists() else ""

    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{link_str}')
$s.TargetPath = '{target_str}'
$s.WorkingDirectory = '{work_str}'
$s.Description = '{description}'
"""
    if icon_str:
        ps_script += f"$s.IconLocation = '{icon_str}'\n"
    ps_script += "$s.Save()\n"

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=True
        )
        return link_path.exists()
    except subprocess.CalledProcessError as e:
        print(f"[create_shortcuts] Error creating {link_path}: {e.stderr}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Windows shortcuts for NeonRoute.")
    parser.add_argument(
        "--no-desktop",
        action="store_true",
        help="Skip creating the shortcut on the Desktop."
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("[create_shortcuts] Non-Windows platform detected; skipping shortcut creation.")
        return 0

    run_bat = BASE_DIR / "run.bat"
    if not run_bat.exists():
        print(f"[create_shortcuts] Error: run.bat not found at {run_bat}", file=sys.stderr)
        return 1

    icon = APP_ICON_PATH if APP_ICON_PATH.exists() else None
    desc = f"{APP_NAME} - Local AI Orchestrator"
    success = True

    # 1. Base directory shortcut
    root_lnk = BASE_DIR / "NeonRoute.lnk"
    print(f"[create_shortcuts] Creating root shortcut: {root_lnk}")
    if create_shortcut_powershell(run_bat, root_lnk, BASE_DIR, icon, desc):
        print(f"[create_shortcuts] Created: {root_lnk}")
    else:
        print(f"[create_shortcuts] Failed to create: {root_lnk}", file=sys.stderr)
        success = False

    # 2. Desktop shortcut (skip if --no-desktop or CI environment)
    is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
    if args.no_desktop or is_ci:
        print("[create_shortcuts] Skipping Desktop shortcut (--no-desktop or CI mode).")
    else:
        desktop_dir = get_desktop_dir()
        if desktop_dir.exists():
            desktop_lnk = desktop_dir / "NeonRoute.lnk"
            print(f"[create_shortcuts] Creating Desktop shortcut: {desktop_lnk}")
            if create_shortcut_powershell(run_bat, desktop_lnk, BASE_DIR, icon, desc):
                print(f"[create_shortcuts] Created: {desktop_lnk}")
            else:
                print(f"[create_shortcuts] Failed to create: {desktop_lnk}", file=sys.stderr)
                success = False
        else:
            print(f"[create_shortcuts] Desktop directory not found at {desktop_dir}; skipping.", file=sys.stderr)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
