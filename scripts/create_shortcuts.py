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

try:
    from config import BASE_DIR, APP_ICON_PATH, APP_USER_MODEL_ID
    try:
        from config import APP_NAME
    except ImportError:
        APP_NAME = "NeonRoute"
except Exception:
    BASE_DIR = REPO_ROOT
    APP_ICON_PATH = REPO_ROOT / "assets" / "neonroute.ico"
    APP_NAME = "NeonRoute"
    APP_USER_MODEL_ID = "NeonRoute.Desktop.App.1"


def _set_shortcut_app_id(link_path: Path, app_id: str) -> None:
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon

        # 2 = GPS_READWRITE
        store = propsys.SHGetPropertyStoreFromParsingName(
            str(link_path.resolve()), None, 2, propsys.IID_IPropertyStore
        )
        prop_var = propsys.PROPVARIANTType(app_id, pythoncom.VT_LPWSTR)
        store.SetValue(pscon.PKEY_AppUserModel_ID, prop_var)
        store.Commit()
    except Exception as e:
        print(f"[WARNING] Could not set AppUserModelID on {link_path.name}: {e}")


def get_desktop_dir() -> Path:
    """
    Resolves Desktop directory using native Win32 API SHGetKnownFolderPath
    (FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}) via ctypes
    (c_wchar_p + CoTaskMemFree) to properly handle OneDrive-redirected or
    localized Desktop paths and Cyrillic user names in UTF-16 without console
    codepage dependency.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            import uuid

            # FOLDERID_Desktop = {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
            guid_bytes = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}").bytes_le
            guid_buf = ctypes.create_string_buffer(guid_bytes)
            ppath = ctypes.c_wchar_p()

            res = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid_buf), 0, None, ctypes.byref(ppath)
            )
            if res == 0 and ppath.value:
                desktop_path = Path(ppath.value)
                ctypes.windll.ole32.CoTaskMemFree(ppath)
                if desktop_path.exists():
                    return desktop_path
            elif ppath.value:
                ctypes.windll.ole32.CoTaskMemFree(ppath)
        except Exception as e:
            print(f"[create_shortcuts] Warning resolving Desktop via Win32 API: {e}", file=sys.stderr)

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
    Paths are passed via environment variables (NR_LNK_PATH, NR_TARGET_PATH, etc.)
    to safely handle apostrophes, spaces, and special characters.
    """
    env = os.environ.copy()
    env["NR_LNK_PATH"] = str(link_path.resolve())
    env["NR_TARGET_PATH"] = str(target_path.resolve())
    env["NR_WORKING_DIR"] = str(working_dir.resolve())
    env["NR_DESCRIPTION"] = str(description)
    env["NR_ICON_PATH"] = f"{icon_path.resolve()}, 0" if icon_path and icon_path.exists() else ""

    ps_script = """
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($env:NR_LNK_PATH)
$s.TargetPath = $env:NR_TARGET_PATH
$s.WorkingDirectory = $env:NR_WORKING_DIR
$s.Description = $env:NR_DESCRIPTION
if ($env:NR_ICON_PATH) {
    $s.IconLocation = $env:NR_ICON_PATH
}
$s.Save()
"""

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            env=env,
            check=True
        )
        if link_path.exists():
            _set_shortcut_app_id(link_path, APP_USER_MODEL_ID)
            return True
        return False
    except subprocess.CalledProcessError as e:
        print(f"[create_shortcuts] Error creating {link_path}: {e.stderr}", file=sys.stderr)
        return False


create_shortcut = create_shortcut_powershell


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
        if desktop_dir and desktop_dir.exists():
            desktop_lnk = desktop_dir / "NeonRoute.lnk"
            print(f"[create_shortcuts] Creating Desktop shortcut: {desktop_lnk}")
            if create_shortcut_powershell(run_bat, desktop_lnk, BASE_DIR, icon, desc):
                print(f"[create_shortcuts] Created: {desktop_lnk}")
            else:
                print(f"[create_shortcuts] Failed to create: {desktop_lnk}", file=sys.stderr)
                success = False
        else:
            print(f"[create_shortcuts] Error: Desktop directory not found at {desktop_dir}", file=sys.stderr)
            success = False

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
