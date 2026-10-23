"""
Sandbox Guard for NeonRoute
Security perimeter enforcing system call boundaries, isolating forbidden modules,
and preventing privilege escalation from within the sandbox execution runtime.
"""

import sys
import os
import builtins
import logging
import tempfile
from pathlib import Path
from config import WORKSPACE_DIR

logger = logging.getLogger("NeonRoute.SandboxGuard")

_GUARD_INSTALLED = False

BANNED_MODULES = {
    "socket", "requests", "ctypes", "winreg", "subprocess"
}

# Прогреваем tempfile.gettempdir() во избежание дедлока блокировки _once_flag при вызове open в аудит-хуке
try:
    tempfile.gettempdir()
except Exception:
    pass

SYSTEM_BLACKLIST = (
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata",
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
)


def _get_workspace_dir() -> Path:
    env_ws = os.environ.get("NEONROUTE_WORKSPACE_DIR")
    if env_ws:
        return Path(env_ws).resolve()
    return WORKSPACE_DIR.resolve()


def _is_safe_path(target_path: Path, resolved_ws: Path, allow_ws_root: bool = True) -> bool:
    try:
        t_str = target_path.resolve().as_posix().lower().rstrip('/')
        ws_str = resolved_ws.resolve().as_posix().lower().rstrip('/')
        temp_str = Path(tempfile.gettempdir()).resolve().as_posix().lower().rstrip('/')
        home_str = Path.home().resolve().as_posix().lower().rstrip('/')

        # 1. Запрет системных корней
        if any(t_str == b or t_str.startswith(b + '/') for b in SYSTEM_BLACKLIST):
            return False

        # 2. Проверка workspace
        if allow_ws_root and t_str == ws_str:
            return True
        if t_str.startswith(ws_str + '/'):
            return True

        # 3. Проверка директории tempfile
        if t_str == temp_str or t_str.startswith(temp_str + '/'):
            return True

        # 4. Проверка безопасных кэшей библиотек в профиле пользователя (matplotlib/fontconfig)
        matplotlib_cache = f"{home_str}/.matplotlib"
        if t_str == matplotlib_cache or t_str.startswith(matplotlib_cache + '/'):
            return True

        return False
    except Exception:
        return False


def install_guard() -> None:
    """
    Устанавливает ограничения рантайма для изоляции песочницы:
    - Блокирует прямой импорт запрещенных модулей (socket, subprocess, ctypes, winreg, requests)
    - Блокирует сетевые подмодули urllib (urllib.request, urllib.robotparser)
    - Блокирует системные вызовы через os.system, os.popen, os.spawn*
    - Устанавливает аудит-хук sys.addaudithook для пресечения сетевых сокетов, процессов и несанкционированной записи на диск
    """
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return
    _GUARD_INSTALLED = True

    # 1. Запрет вызова опасных функций os
    for fn in ("system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe"):
        if hasattr(os, fn):
            def _make_blocked(name: str):
                def _blocked(*args, **kwargs):
                    raise PermissionError(f"Ошибка безопасности: вызов os.{name} запрещен политикой песочницы.")
                return _blocked
            setattr(os, fn, _make_blocked(fn))

    # 2. Изоляция запрещенных модулей на уровне builtins.__import__ для скрипта песочницы
    orig_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if globals is None:
            try:
                globals = sys._getframe(1).f_globals
            except Exception:
                globals = None
        caller_file = str(globals.get("__file__", "")).replace("\\", "/").lower() if globals else ""
        caller_name = globals.get("__name__") if globals else None
        mod_root = name.split(".")[0].lower()
        is_workspace_file = "/workspace/" in caller_file or caller_file.endswith("/workspace")
        if (caller_name == "__main__" or is_workspace_file):
            if mod_root in BANNED_MODULES:
                raise PermissionError(f"Ошибка безопасности (Sandbox Guard): Импорт модуля '{name}' запрещен политикой песочницы.")
            if name.lower() in ("urllib.request", "urllib.robotparser") or name.lower().startswith("urllib.request."):
                raise PermissionError(f"Ошибка безопасности (Sandbox Guard): Сетевой импорт '{name}' запрещен политикой песочницы.")
            if mod_root == "urllib" and fromlist and any(isinstance(f, str) and f.lower() in ("request", "robotparser") for f in fromlist):
                raise PermissionError(f"Ошибка безопасности (Sandbox Guard): Сетевой импорт '{name}.{fromlist}' запрещен политикой песочницы.")
        return orig_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import

    # 3. Аудит-хук рантайма Python для предотвращения эскалации привилегий
    def _audit_hook(event: str, args: tuple) -> None:
        if event == "os.system" or event.startswith("os.spawn") or event.startswith("os.posix_spawn"):
            raise PermissionError(f"Ошибка безопасности: системный вызов {event} запрещен политикой песочницы.")

        elif event.startswith("subprocess."):
            try:
                frame = sys._getframe(1)
                while frame:
                    fn = frame.f_code.co_filename.replace("\\", "/").lower()
                    if "sandbox_manager.py" in fn or "_pytest" in fn or "pytest" in fn:
                        return
                    frame = frame.f_back
            except Exception:
                pass
            raise PermissionError("Ошибка безопасности: запуск дочерних процессов из песочницы запрещен.")

        elif event in ("socket.connect", "socket.bind", "socket.listen", "socket.socket"):
            raise PermissionError("Ошибка безопасности: сетевые соединения из песочницы запрещены.")

        elif event.startswith("winreg.") and any(w in event for w in ("Create", "Delete", "Set", "Save")):
            raise PermissionError("Ошибка безопасности: модификация системного реестра запрещена.")

        elif event == "os.chdir":
            if args:
                # Разрешаем pytest восстанавливать рабочую директорию в wrap_session
                try:
                    caller = sys._getframe(1)
                    if caller and caller.f_code.co_name == "wrap_session" and "_pytest" in caller.f_code.co_filename.replace("\\", "/").lower():
                        return
                except Exception:
                    pass

                raw_path = args[0]
                p_str = os.fspath(raw_path) if hasattr(os, "fspath") and isinstance(raw_path, (str, bytes, os.PathLike)) else str(raw_path)
                if isinstance(p_str, bytes):
                    p_str = p_str.decode("utf-8", errors="ignore")
                resolved_target = Path(p_str).resolve()
                resolved_ws = _get_workspace_dir()
                try:
                    resolved_target.relative_to(resolved_ws)
                except ValueError:
                    raise PermissionError("Смена директории за пределы workspace запрещена")

        elif event in ("os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace", "shutil.rmtree"):
            resolved_ws = _get_workspace_dir()
            targets = []
            if event in ("os.rename", "os.replace"):
                if len(args) >= 1:
                    targets.append(args[0])
                if len(args) >= 2:
                    targets.append(args[1])
            elif args:
                targets.append(args[0])

            for target in targets:
                if isinstance(target, (int, float)):
                    continue
                p_str = os.fspath(target) if hasattr(os, "fspath") and isinstance(target, (str, bytes, os.PathLike)) else str(target)
                if isinstance(p_str, bytes):
                    p_str = p_str.decode("utf-8", errors="ignore")
                p_clean = p_str.replace("\\", "/").lower()
                if ".pytest_cache" in p_clean or "pytest-" in p_clean:
                    continue
                resolved_target = Path(p_str).resolve()
                resolved_ws = _get_workspace_dir()

                # Удалять можно файлы внутри workspace и temp, но не сам корень workspace
                if not _is_safe_path(resolved_target, resolved_ws, allow_ws_root=False):
                    raise PermissionError(
                        f"Удаление или перемещение вне рабочей директории workspace запрещено политикой безопасности: {p_str}"
                    )

        elif event == "open":
            path = args[0] if len(args) >= 1 else None
            mode = args[1] if len(args) >= 2 else None
            flags = args[2] if len(args) >= 3 else None

            mode_str = str(mode or "").lower()
            is_write = any(m in mode_str for m in ("w", "a", "x", "+"))
            if not is_write and flags is not None and isinstance(flags, int):
                write_flags = os.O_WRONLY | os.O_RDWR | getattr(os, "O_APPEND", 0) | getattr(os, "O_CREAT", 0)
                if bool(flags & write_flags):
                    is_write = True

            if is_write and path is not None:
                if isinstance(path, (int, float)):
                    if path not in (0, 1, 2):
                        raise PermissionError(f"Запись через файловый дескриптор {path} запрещена.")
                elif isinstance(path, (str, bytes, os.PathLike)):
                    p_str = os.fspath(path) if hasattr(os, "fspath") else str(path)
                    if isinstance(p_str, bytes):
                        p_str = p_str.decode("utf-8", errors="ignore")

                    p_clean = p_str.replace("\\", "/").lower()
                    if ".pytest_cache" in p_clean or "pytest-" in p_clean:
                        return
                    if any(p_clean.startswith(b) for b in SYSTEM_BLACKLIST) or "/windows/" in p_clean:
                        raise PermissionError(f"Запись в системную директорию запрещена политикой безопасности: {p_str}")

                    resolved_target = Path(p_str).resolve()
                    resolved_ws = _get_workspace_dir()

                    if not _is_safe_path(resolved_target, resolved_ws, allow_ws_root=True):
                        raise PermissionError(f"Запись вне рабочей директории workspace запрещена политикой безопасности: {p_str}")

    try:
        sys.addaudithook(_audit_hook)
    except Exception as e:
        logger.debug(f"sys.addaudithook not supported or already installed: {e}")

    logger.info("[SandboxGuard] Периметр безопасности песочницы успешно активирован.")
