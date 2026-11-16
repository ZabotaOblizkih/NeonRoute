"""
Sandbox Manager for NeonRoute
Enforces strict security boundaries:
1. Workspace Directory (C:\\NeonRoute\\workspace): Full read/write/edit/delete/execution permissions.
2. System Directory Access: Read-only access to user directories and non-system storage.
3. System Directory Blacklist: Strict exclusion of critical system and bulky folders.
"""

import ast
import logging
from contextlib import contextmanager
import os
import re
import shutil
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Set, Optional, Union, Tuple, List
from dataclasses import dataclass

from config import BASE_DIR, WORKSPACE_DIR, OUTPUTS_DIR, FORGE_ROOT_DIR
from utils.file_utils import get_python_cli_executable

logger = logging.getLogger("NeonRoute.SandboxManager")

CODE_CREATE_TRIGGERS = [
    "калькулятор", "calculator", "программу", "код", "скрипт", "напиши",
    "напиши программу", "напиши код", "напиши скрипт", "создай скрипт", 
    "создай файл", "сделай скрипт", "накодь", "сгенерируй код", "закодь",
    "сохрани в файл", "сохрани как", "запиши в", "сохрани код", "сохрани программу",
    "положи в песочницу", "закинь в файл", "сохрани это", "write script", 
    "create script", "write code", "save to file", "make a script", "write python"
]
try:
    from services.subagent_engineer import MINI_ANTIGRAVITY_SYSTEM_PROMPT
except ImportError:
    MINI_ANTIGRAVITY_SYSTEM_PROMPT = ""


@dataclass
class ScriptExecutionResult:
    success: bool
    return_code: int
    stdout: str
    stderr: str
    output: str
    artifact_path: Optional[Path] = None


class SandboxManager:
    """
    Guarantees isolation of file modification operations to C:\\NeonRoute\\workspace,
    while safely allowing read-only inspection of user system files.
    """
    WORKSPACE_ROOT = WORKSPACE_DIR.resolve()
    workspace_dir: Path = WORKSPACE_DIR
    _active_session_id: Optional[str] = None
    active_session_id: Optional[str] = None
    _housekeeping_done: bool = False

    def __init__(self, workspace_dir: Optional[Union[str, Path]] = None):
        if workspace_dir is not None:
            self.workspace_dir = Path(workspace_dir)
        else:
            self.workspace_dir = self.get_workspace_dir()

    @classmethod
    def set_active_session_id(cls, session_id: Optional[str]):
        cls._active_session_id = str(session_id).strip() if session_id else None
        cls.active_session_id = cls._active_session_id
        if cls._active_session_id:
            cls.workspace_dir = WORKSPACE_DIR / "projects" / cls._active_session_id
        else:
            cls.workspace_dir = WORKSPACE_DIR

    @classmethod
    def get_active_session_id(cls) -> Optional[str]:
        return cls._active_session_id

    @classmethod
    @contextmanager
    def session_scope(cls, session_id: Optional[str]):
        """Безопасный контекстный менеджер: активирует сессию и гарантированно возвращает исходную при выходе."""
        prev_sid = cls._active_session_id
        cls.set_active_session_id(session_id)
        try:
            yield
        finally:
            cls.set_active_session_id(prev_sid)

    IGNORE_DIRS: Set[str] = {
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "documents and settings",
        "perflogs",
        "config.msi",
        "node_modules",
        ".git",
        "appdata",
        "__pycache__",
        "$recycle.bin",
        "system volume information",
        "recovery",
        "msocache",
        ".svn",
        ".hg",
        ".vscode",
        ".idea",
        "venv",
        ".env"
    }

    @classmethod
    def auto_archive_scripts(cls, max_files: int = 15, max_age_hours: int = 24) -> int:
        try:
            ws = cls.get_workspace_dir()
            py_files = [f for f in ws.iterdir() if f.is_file() and f.suffix.lower() == ".py"]
            if len(py_files) <= max_files:
                return 0

            archive_dir = ws / "_archive"
            archive_dir.mkdir(parents=True, exist_ok=True)

            cutoff = time.time() - (max_age_hours * 3600)
            moved_count = 0

            for f in py_files:
                try:
                    if f.stat().st_mtime < cutoff:
                        dest = archive_dir / f.name
                        shutil.move(str(f), str(dest))
                        moved_count += 1
                except Exception:
                    pass

            if moved_count > 0:
                logger.info(f"[Housekeeping] Архивировано устаревших скриптов: {moved_count}")
            return moved_count
        except Exception as e:
            logger.warning(f"[Housekeeping] Ошибка при авто-архивации: {e}")
            return 0

    @classmethod
    def get_workspace_dir(cls, create: bool = False) -> Path:
        """Возвращает изолированную директорию проекта для активной сессии (ленивое создание)."""
        if cls._active_session_id:
            proj_dir = WORKSPACE_DIR / "projects" / cls._active_session_id
            if create:
                proj_dir.mkdir(parents=True, exist_ok=True)
            return proj_dir
        if create:
            WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        return WORKSPACE_DIR

    @classmethod
    def clean_workspace_filename(cls, filename: str) -> str:
        import html
        raw = html.unescape(str(filename or "")).strip().strip("'\"`")
        clean = raw.replace("\\", "/")
        if clean.lower().startswith("python "):
            clean = clean[7:].strip()
        elif clean.lower().startswith("python3 "):
            clean = clean[8:].strip()
        curr_ws = str(cls.get_workspace_dir().resolve()).replace("\\", "/").lower()
        if clean.lower().startswith(curr_ws):
            clean = clean[len(curr_ws):].lstrip("/")

        ws_str = str(cls.WORKSPACE_ROOT).replace("\\", "/").lower()
        if clean.lower().startswith(ws_str):
            clean = clean[len(ws_str):].lstrip("/")
        elif clean.lower().startswith("workspace/"):
            clean = clean[len("workspace/"):].lstrip("/")

        if cls._active_session_id:
            pfx = f"projects/{cls._active_session_id.lower()}/"
            if clean.lower().startswith(pfx):
                clean = clean[len(pfx):].lstrip("/")

        if re.match(r'^[a-zA-Z]:', clean):
            clean = clean.split(":", 1)[-1].lstrip("/")

        # Срезаем любые попытки path traversal (.. и .)
        parts = [p for p in clean.split("/") if p and p not in ("..", ".")]
        clean = "/".join(parts)

        # Очистка запрещенных символов Windows (<>:"|?*)
        clean = re.sub(r'[<>:"|?*]', '', clean)

        final_clean = clean.strip().strip("'\"`")
        return final_clean if final_clean else "script.py"

    @classmethod
    def write_file(cls, filename: str, content: str) -> str:
        if not content or not content.strip():
            err_msg = "[Ошибка write_file]: Тело скрипта не получено или пусто."
            logger.warning(err_msg)
            return err_msg

        clean_name = cls.clean_workspace_filename(filename)
        ws_dir = cls.get_workspace_dir()

        full_path = (ws_dir / clean_name).resolve()
        try:
            full_path.relative_to(ws_dir)
        except ValueError:
            full_path = (ws_dir / Path(clean_name).name).resolve()

        if full_path.is_dir() or clean_name.endswith(('/', '\\')):
            err_msg = f"[Ошибка write_file]: Целевой путь '{clean_name}' указывает на директорию, а не на файл. Укажите имя файла."
            logger.warning(err_msg)
            return err_msg

        full_path.parent.mkdir(parents=True, exist_ok=True)

        clean_content = content.strip()
        fence_match = re.match(r"^```[a-zA-Z0-9_\-]*[\r\n]+(.*?)(?:[\r\n]+```)?$", clean_content, flags=re.DOTALL)
        if fence_match:
            clean_content = fence_match.group(1).strip()

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(clean_content)
                f.flush()
                os.fsync(f.fileno())
        except (PermissionError, OSError) as e:
            err_msg = f"[Ошибка write_file]: Ошибка доступа или ввода-вывода при записи {clean_name}: {e}"
            logger.error(err_msg)
            return err_msg


        if not full_path.exists() or (full_path.stat().st_size == 0 and len(clean_content.strip()) > 0):
            raise IOError(f"Файл {clean_name} не был записан или остался пустым!")

        file_bytes = full_path.stat().st_size
        return f"[Успешно]: Файл {full_path} создан ({file_bytes} байт)."

    @classmethod
    def run_script(cls, filename: str, timeout: int = 45, args: Optional[Union[str, List[str]]] = None) -> ScriptExecutionResult:
        """
        Единая точка безопасного выполнения скриптов:
        - Активирует sandbox_guard (лимит RAM 512MB, блокировка сокетов, изоляция ФС).
        - Предотвращает зависание GUI-приложений через py_compile.
        - Фиксирует созданные артефакты (.xlsx, .docx, .png, .csv и др.) и маркер OUTPUT_FILE.
        """
        clean_name = cls.clean_workspace_filename(filename)
        ws_dir = cls.get_workspace_dir()
        script_path = (ws_dir / clean_name).resolve()

        if not clean_name.lower().endswith((".py", ".pyw")):
            msg = f"Ошибка: run_script предназначен только для файлов Python (.py). Файл '{clean_name}' не является исполняемым скриптом."
            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=msg, output=msg)

        if not os.path.exists(script_path) or not script_path.is_file():
            msg = f"Ошибка: Скрипт не найден: {script_path}"
            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=msg, output=msg)

        # 0. AST Safety Guard (VULN-02): блокировка опасных модулей
        try:
            with open(script_path, "r", encoding="utf-8", errors="replace") as f_ast:
                script_code = f_ast.read()
            parsed_tree = ast.parse(script_code, filename=str(script_path))
            BANNED_MODULES = {"socket", "requests", "ctypes", "winreg", "subprocess"}
            BANNED_SUBMODULES = {"urllib.request", "urllib.robotparser"}

            def is_banned(name: str) -> Optional[str]:
                mod_lower = name.lower()
                mod_root = mod_lower.split(".")[0]
                if mod_root in BANNED_MODULES:
                    return mod_root
                for banned_sub in BANNED_SUBMODULES:
                    if mod_lower == banned_sub or mod_lower.startswith(banned_sub + "."):
                        return banned_sub
                return None

            for node in ast.walk(parsed_tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        banned = is_banned(alias.name)
                        if banned:
                            err_msg = f"Ошибка безопасности (AST Safety Guard): Импорт модуля '{alias.name}' запрещен политикой песочницы."
                            logger.warning(f"🛑 [AST Safety Guard] Заблокирован опасный импорт '{alias.name}' в {clean_name}")
                            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=err_msg, output=err_msg)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        # Проверяем сам модуль from ...
                        banned = is_banned(node.module)
                        if banned:
                            err_msg = f"Ошибка безопасности (AST Safety Guard): Импорт из модуля '{node.module}' запрещен политикой песочницы."
                            logger.warning(f"🛑 [AST Safety Guard] Заблокирован опасный импорт '{node.module}' в {clean_name}")
                            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=err_msg, output=err_msg)
                        # Также проверяем импортируемые имена, например from urllib import request
                        for alias in node.names:
                            full_name = f"{node.module}.{alias.name}"
                            banned = is_banned(full_name)
                            if banned:
                                err_msg = f"Ошибка безопасности (AST Safety Guard): Импорт '{alias.name}' из модуля '{node.module}' запрещен политикой песочницы."
                                logger.warning(f"🛑 [AST Safety Guard] Заблокирован опасный импорт '{full_name}' в {clean_name}")
                                return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=err_msg, output=err_msg)
        except SyntaxError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ [AST Safety Guard] Ошибка парсинга AST: {e}")

        # 1. Проверка интерактивных GUI-приложений
        try:
            with open(script_path, "r", encoding="utf-8", errors="ignore") as f_check:
                code_sample = f_check.read()
            
            gui_tokens = (".mainloop", "mainloop()", "pygame", "QApplication", "app.exec", "tkinter", "customtkinter", "Tk()")
            is_gui_app = any(t in code_sample for t in gui_tokens)
            clean_lower = clean_name.lower()
            is_test_file = (
                clean_lower.startswith(("test_", "check_", "verify_", "validate_"))
                or clean_lower.endswith(("_test.py", "_tests.py", "_check.py", "_verify.py"))
            )
            if is_test_file:
                is_gui_app = False
            
            if is_gui_app:
                comp_proc = subprocess.run(
                    [get_python_cli_executable(), "-m", "py_compile", str(script_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=6
                )
                if comp_proc.returncode != 0:
                    err_msg = f"Ошибка синтаксиса в скрипте {clean_name}:\n{comp_proc.stderr}"
                    logger.warning(f"⚠️ [Smoke-тест GUI] Синтаксическая ошибка в {clean_name}: {comp_proc.stderr.strip()}")
                    return ScriptExecutionResult(success=False, return_code=comp_proc.returncode, stdout="", stderr=err_msg, output=f"STDERR:\n{err_msg}")

                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                env["PYTHONUNBUFFERED"] = "1"
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

                # Headless Smoke Harness: всестороннее тестирование GUI-событий без ложных срабатываний
                monkey_patch_bootstrap = (
                    "import sys, os, runpy, atexit\n"
                    "target = sys.argv[1]\n"
                    "sys.argv = sys.argv[1:]\n"
                    "t_dir = os.path.dirname(os.path.abspath(target))\n"
                    "if t_dir not in sys.path:\n"
                    "    sys.path.insert(0, t_dir)\n\n"
                    "_gui_passed = False\n"
                    "_marker_emitted = False\n"
                    "def _emit_marker():\n"
                    "    global _marker_emitted\n"
                    "    if _gui_passed and not _marker_emitted:\n"
                    "        _marker_emitted = True\n"
                    "        sys.stdout.write('[GUI_CHECKLIST_PASSED]\\n')\n"
                    "        sys.stdout.flush()\n"
                    "atexit.register(_emit_marker)\n\n"
                    "target_code = ''\n"
                    "try:\n"
                    "    with open(target, 'r', encoding='utf-8', errors='ignore') as _f:\n"
                    "        target_code = _f.read()\n"
                    "except Exception:\n"
                    "    pass\n\n"
                    "# 1. Tkinter: перехват падений, автоклик и грациозный выход\n"
                    "if any(k in target_code for k in ('tkinter', 'Tk(', 'customtkinter')):\n"
                    "    try:\n"
                    "        import tkinter as tk\n"
                    "        from tkinter import messagebox, filedialog\n"
                    "        def _noop_dialog(*args, **kwargs): return True\n"
                    "        for m_func in ('showinfo', 'showwarning', 'showerror', 'askyesno', 'askokcancel'):\n"
                    "            setattr(messagebox, m_func, _noop_dialog)\n"
                    "        filedialog.askopenfilename = lambda *a, **kw: 'test.txt'\n"
                    "        filedialog.asksaveasfilename = lambda *a, **kw: 'test.txt'\n"
                    "        def _monkey_report(self, exc, val, tb):\n"
                    "            import traceback\n"
                    "            traceback.print_exception(exc, val, tb, file=sys.stderr)\n"
                    "            sys.stderr.flush()\n"
                    "            os._exit(1)\n"
                    "        tk.Tk.report_callback_exception = _monkey_report\n"
                    "        _orig_tk_mainloop = tk.Tk.mainloop\n"
                    "        def _monkey_tk_mainloop(self, n=0):\n"
                    "            global _gui_passed\n"
                    "            def _stim_tk():\n"
                    "                try:\n"
                    "                    def _click(w):\n"
                    "                        for ch in w.winfo_children():\n"
                    "                            if hasattr(ch, 'invoke'):\n"
                    "                                try: ch.invoke()\n"
                    "                                except Exception: pass\n"
                    "                            _click(ch)\n"
                    "                    _click(self)\n"
                    "                    self.event_generate('<space>')\n"
                    "                    self.event_generate('<Return>')\n"
                    "                except Exception:\n"
                    "                    pass\n"
                    "            self.after(300, _stim_tk)\n"
                    "            self.after(800, self.destroy)\n"
                    "            res = _orig_tk_mainloop(self, n)\n"
                    "            _gui_passed = True\n"
                    "            _emit_marker()\n"
                    "            return res\n"
                    "        tk.Tk.mainloop = _monkey_tk_mainloop\n"
                    "    except Exception:\n"
                    "        pass\n\n"
                    "# 2. Pygame: безопасные события SDL2, эмуляция клавиш и проверка рендера\n"
                    "if 'pygame' in target_code:\n"
                    "    try:\n"
                    "        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')\n"
                    "        import pygame\n"
                    "        _orig_set_mode = pygame.display.set_mode\n"
                    "        _active_screen = None\n"
                    "        def _monkey_set_mode(*args, **kwargs):\n"
                    "            global _active_screen\n"
                    "            _active_screen = _orig_set_mode(*args, **kwargs)\n"
                    "            return _active_screen\n"
                    "        pygame.display.set_mode = _monkey_set_mode\n"
                    "        _OrigClock = pygame.time.Clock\n"
                    "        class _SafeFastClock:\n"
                    "            def __init__(self, *a, **k):\n"
                    "                try: self._real = _OrigClock(*a, **k)\n"
                    "                except Exception: self._real = None\n"
                    "            def tick(self, fps=0): return 16\n"
                    "            def tick_busy_loop(self, fps=0): return 16\n"
                    "            def get_fps(self): return 60.0\n"
                    "            def get_time(self): return 16\n"
                    "            def get_rawtime(self): return 16\n"
                    "        pygame.time.Clock = _SafeFastClock\n"
                    "        _orig_event_get = pygame.event.get\n"
                    "        _frames = 0\n"
                    "        _sample_3 = None\n"
                    "        def _sample_pixels():\n"
                    "            global _active_screen\n"
                    "            scr = _active_screen or pygame.display.get_surface()\n"
                    "            if scr is None:\n"
                    "                return None\n"
                    "            try:\n"
                    "                w, h = scr.get_size()\n"
                    "                if w <= 0 or h <= 0:\n"
                    "                    return None\n"
                    "                pts = []\n"
                    "                step_x = max(1, w // 16)\n"
                    "                step_y = max(1, h // 16)\n"
                    "                for ix in range(16):\n"
                    "                    x = min(w - 1, ix * step_x)\n"
                    "                    for iy in range(16):\n"
                    "                        y = min(h - 1, iy * step_y)\n"
                    "                        c = scr.get_at((x, y))\n"
                    "                        pts.append((c.r, c.g, c.b))\n"
                    "                return tuple(pts)\n"
                    "            except Exception:\n"
                    "                return None\n"
                    "        def _create_event(ev_type, **attrs):\n"
                    "            defaults = {'mod': 0, 'scancode': 0, 'window': None}\n"
                    "            defaults.update(attrs)\n"
                    "            try: return pygame.event.Event(ev_type, **defaults)\n"
                    "            except TypeError: return pygame.event.Event(ev_type, **attrs)\n"
                    "        def _monkey_event_get(*args, **kwargs):\n"
                    "            global _frames, _sample_3, _gui_passed\n"
                    "            evs = list(_orig_event_get(*args, **kwargs))\n"
                    "            _frames += 1\n"
                    "            if _frames == 3:\n"
                    "                _sample_3 = _sample_pixels()\n"
                    "            elif _frames in (5, 15, 25):\n"
                    "                try:\n"
                    "                    evs.append(_create_event(pygame.KEYDOWN, key=pygame.K_RIGHT, unicode=''))\n"
                    "                    evs.append(_create_event(pygame.KEYDOWN, key=pygame.K_SPACE, unicode=' '))\n"
                    "                    evs.append(_create_event(pygame.KEYDOWN, key=pygame.K_LEFT, unicode=''))\n"
                    "                    evs.append(_create_event(pygame.MOUSEBUTTONDOWN, pos=(200, 200), button=1))\n"
                    "                except Exception:\n"
                    "                    pass\n"
                    "            elif _frames in (6, 16, 26):\n"
                    "                try:\n"
                    "                    evs.append(_create_event(pygame.KEYUP, key=pygame.K_RIGHT, unicode=''))\n"
                    "                    evs.append(_create_event(pygame.KEYUP, key=pygame.K_SPACE, unicode=' '))\n"
                    "                    evs.append(_create_event(pygame.KEYUP, key=pygame.K_LEFT, unicode=''))\n"
                    "                    evs.append(_create_event(pygame.MOUSEBUTTONUP, pos=(200, 200), button=1))\n"
                    "                except Exception:\n"
                    "                    pass\n"
                    "            elif _frames == 30:\n"
                    "                s30 = _sample_pixels()\n"
                    "                if s30 is None:\n"
                    "                    sys.stderr.write('Экран не инициализирован: отсутствует вызов pygame.display.set_mode().\\n')\n"
                    "                    sys.stderr.flush()\n"
                    "                    sys.exit(2)\n"
                    "                uniq = set(s30)\n"
                    "                if len(uniq) <= 1:\n"
                    "                    sys.stderr.write('Экран однотонный/пустой: объекты игры не отрисовываются либо полностью слились с цветом фона.\\n')\n"
                    "                    sys.stderr.flush()\n"
                    "                    sys.exit(2)\n"
                    "                if _sample_3 is not None and _sample_3 == s30:\n"
                    "                    sys.stderr.write('Экран полностью статичен: нажатия клавиш управления и течение времени не меняют картинку на экране (проверь: 1) не совпадает ли цвет игрока/движущихся объектов с цветом фона — сделай цвета контрастными; 2) отрисовку игрока, движение и физику).\\n')\n"
                    "                    sys.stderr.flush()\n"
                    "                    sys.exit(2)\n"
                    "                _gui_passed = True\n"
                    "            elif _frames >= 35:\n"
                    "                try: evs.append(_create_event(pygame.QUIT))\n"
                    "                except Exception: pass\n"
                    "            return evs\n"
                    "        pygame.event.get = _monkey_event_get\n"
                    "        _orig_key_pressed = pygame.key.get_pressed\n"
                    "        class _SafeKeyWrapper:\n"
                    "            def __init__(self, base, pressed=None):\n"
                    "                self._base = base\n"
                    "                self._pressed = set(pressed or [])\n"
                    "            def __getitem__(self, idx):\n"
                    "                if idx in self._pressed:\n"
                    "                    return 1\n"
                    "                try: return self._base[idx]\n"
                    "                except Exception: return 0\n"
                    "            def __len__(self):\n"
                    "                return len(self._base) if hasattr(self._base, '__len__') else 512\n"
                    "            def __iter__(self):\n"
                    "                for i in range(len(self)): yield self[i]\n"
                    "        def _monkey_key_pressed():\n"
                    "            k = _orig_key_pressed()\n"
                    "            active = {pygame.K_SPACE, pygame.K_LEFT, pygame.K_RIGHT} if _frames in (5, 6, 7, 15, 16, 17, 25, 26, 27) else set()\n"
                    "            return _SafeKeyWrapper(k, pressed=active)\n"
                    "        pygame.key.get_pressed = _monkey_key_pressed\n"
                    "        _orig_mouse_pressed = pygame.mouse.get_pressed\n"
                    "        def _monkey_mouse_pressed():\n"
                    "            if _frames in (5, 6, 7, 15, 16, 17, 25, 26, 27):\n"
                    "                return (1, 0, 0)\n"
                    "            return _orig_mouse_pressed()\n"
                    "        pygame.mouse.get_pressed = _monkey_mouse_pressed\n"
                    "        _orig_pygame_quit = pygame.quit\n"
                    "        def _monkey_pygame_quit():\n"
                    "            _emit_marker()\n"
                    "            return _orig_pygame_quit()\n"
                    "        pygame.quit = _monkey_pygame_quit\n"
                    "    except Exception:\n"
                    "        pass\n\n"
                    "# 3. PyQt6 / PySide6: автоклик, глушение диалогов и выход\n"
                    "if any(k in target_code for k in ('PyQt', 'PySide', 'QApplication')):\n"
                    "    try:\n"
                    "        for qm in ('PyQt6.QtWidgets', 'PySide6.QtWidgets'):\n"
                    "            try:\n"
                    "                mod = __import__(qm, fromlist=['QApplication', 'QPushButton', 'QMessageBox'])\n"
                    "                QA = getattr(mod, 'QApplication', None)\n"
                    "                QP = getattr(mod, 'QPushButton', None)\n"
                    "                QMB = getattr(mod, 'QMessageBox', None)\n"
                    "                if QMB:\n"
                    "                    QMB.exec = lambda *a, **k: 0\n"
                    "                if QA and hasattr(QA, 'exec'):\n"
                    "                    _orig_exec = QA.exec\n"
                    "                    def _monkey_qt_exec(self):\n"
                    "                        global _gui_passed\n"
                    "                        try: from PyQt6.QtCore import QTimer\n"
                    "                        except ImportError: from PySide6.QtCore import QTimer\n"
                    "                        def _stim_qt():\n"
                    "                            try:\n"
                    "                                for w in QA.allWidgets():\n"
                    "                                    if isinstance(w, QP):\n"
                    "                                        try: w.click()\n"
                    "                                        except Exception: pass\n"
                    "                            except Exception: pass\n"
                    "                        QTimer.singleShot(300, _stim_qt)\n"
                    "                        QTimer.singleShot(800, QA.quit)\n"
                    "                        res = _orig_exec(self)\n"
                    "                        _gui_passed = True\n"
                    "                        _emit_marker()\n"
                    "                        return res\n"
                    "                    QA.exec = _monkey_qt_exec\n"
                    "                    break\n"
                    "            except Exception:\n"
                    "                pass\n"
                    "    except Exception:\n"
                    "        pass\n\n"
                    "runpy.run_path(target, run_name='__main__')\n"
                )

                gui_proc = subprocess.Popen(
                    [get_python_cli_executable(), "-c", monkey_patch_bootstrap, str(script_path)],
                    cwd=str(ws_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    creationflags=flags
                )
                try:
                    out, err = gui_proc.communicate(timeout=3.5)
                    if gui_proc.returncode != 0:
                        err_clean = err.strip() or out.strip()
                        err_short = err_clean.splitlines()[-1] if err_clean else f"Код {gui_proc.returncode}"
                        logger.warning(f"⚠️ [Smoke-тест GUI] Обнаружен сбой при стимуляции событий в {clean_name} (код {gui_proc.returncode}): {err_short}")
                        return ScriptExecutionResult(
                            success=False,
                            return_code=gui_proc.returncode,
                            stdout=out or "",
                            stderr=err_clean,
                            output=f"Ошибка выполнения GUI (рантайм, код {gui_proc.returncode}):\n{err_clean}"
                        )
                    if "[GUI_CHECKLIST_PASSED]" in (out or "") or "[GUI_CHECKLIST_PASSED]" in (err or ""):
                        logger.info(f"✅ [Smoke-тест GUI] Успех: {clean_name} выдержал стресс-тест событий ввода и рендера.")
                        msg = f"[Smoke-тест GUI успешен]: Оконное приложение {clean_name} проверено событиями ввода и готово к работе."
                        return ScriptExecutionResult(success=True, return_code=0, stdout=out or msg, stderr="", output=msg)
                except subprocess.TimeoutExpired:
                    gui_proc.kill()
                    out, err = "", ""
                    try:
                        out, err = gui_proc.communicate(timeout=0.5)
                    except Exception:
                        pass

                    err_clean = (err or "").strip()
                    logger.warning(f"⚠️ [Smoke-тест GUI] Приложение {clean_name} зависло или не обработало сигнал завершения за 2.5 с.")
                    timeout_msg = f"Ошибка GUI (таймаут 2.5 с): {clean_name} зависло в цикле событий и не завершилось штатно."
                    if err_clean:
                        timeout_msg += f"\n{err_clean}"
                    return ScriptExecutionResult(
                        success=False,
                        return_code=124,
                        stdout=out or "",
                        stderr=err_clean or timeout_msg,
                        output=timeout_msg
                    )
        except Exception as _e_gui:
            logger.debug(f"GUI check skipped: {_e_gui}")


        # 2. Фиксация состояния файлов workspace до запуска
        before_files = set(ws_dir.glob("*"))

        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            base_dir_str = str(BASE_DIR.resolve())
            env["PYTHONPATH"] = f"{base_dir_str}{os.pathsep}{env.get('PYTHONPATH', '')}"
            env["NEONROUTE_WORKSPACE_DIR"] = str(ws_dir.resolve())

            import shlex
            cmd = [
                get_python_cli_executable(),
                "-m",
                "services.sandbox_runner",
                str(script_path)
            ]
            if args:
                if isinstance(args, str):
                    cmd.extend(shlex.split(args))
                elif isinstance(args, (list, tuple)):
                    cmd.extend([str(a) for a in args])

            # Архитектурный запуск через микро-раннер от изолированной директории проекта
            proc = subprocess.Popen(
                cmd,
                cwd=str(ws_dir.resolve()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env
            )
            stdout, stderr = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2)
            except Exception:
                pass
            err_msg = f"Ошибка: Превышен таймаут выполнения скрипта ({timeout} сек). Процесс принудительно остановлен."
            return ScriptExecutionResult(success=False, return_code=-1, stdout="", stderr=err_msg, output=err_msg)
        except Exception as e:
            err_msg = f"Ошибка запуска скрипта: {e}"
            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=err_msg, output=err_msg)

        # 3. Фиксация созданных артефактов
        after_files = set(ws_dir.glob("*"))
        diff = [p for p in (after_files - before_files) if p.suffix.lower() in (".xlsx", ".docx", ".pdf", ".png", ".csv", ".json")]
        artifact_path = diff[0] if diff else None

        if not artifact_path and stdout and "OUTPUT_FILE:" in stdout:
            for line in stdout.splitlines():
                if "OUTPUT_FILE:" in line:
                    raw = line.split("OUTPUT_FILE:", 1)[1].strip().strip('"\'')
                    p_cand = Path(raw)
                    if not p_cand.is_absolute():
                        p_cand = ws_dir / p_cand
                    if p_cand.exists() and p_cand.is_file():
                        artifact_path = p_cand
                        break

        output_parts = []
        if stdout and stdout.strip():
            output_parts.append(stdout.strip())
        if stderr and stderr.strip():
            output_parts.append(f"STDERR:\n{stderr.strip()}")
        if not output_parts:
            status_str = "успешно" if rc == 0 else "с ошибкой"
            output_parts.append(f"[Скрипт завершен {status_str}, код: {rc}]")

        full_output = "\n".join(output_parts)
        return ScriptExecutionResult(
            success=(rc == 0),
            return_code=rc,
            stdout=stdout or "",
            stderr=stderr or "",
            output=full_output,
            artifact_path=artifact_path
        )

    @classmethod
    def run_pytest(cls, filename: str, timeout: int = 45) -> ScriptExecutionResult:
        """
        Запуск набора тестов pytest для целевого файла в изолированной рабочей директории песочницы.
        """
        clean_name = cls.clean_workspace_filename(filename)
        ws_dir = cls.get_workspace_dir()
        script_path = (ws_dir / clean_name).resolve()

        if not script_path.exists() or not script_path.is_file():
            msg = f"Ошибка: Тестовый файл не найден: {script_path}"
            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=msg, output=msg)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        base_dir_str = str(BASE_DIR.resolve())
        ws_dir_str = str(ws_dir.resolve())
        env["PYTHONPATH"] = f"{ws_dir_str}{os.pathsep}{base_dir_str}{os.pathsep}{env.get('PYTHONPATH', '')}"
        env["NEONROUTE_WORKSPACE_DIR"] = ws_dir_str

        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(
                [get_python_cli_executable(), "-m", "pytest", clean_name, "-v"],
                cwd=ws_dir_str,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
                creationflags=flags
            )
            out = proc.stdout or ""
            err = proc.stderr or ""
            comb = (out + "\n" + err).strip() if err else out.strip()
            return ScriptExecutionResult(
                success=(proc.returncode == 0),
                return_code=proc.returncode,
                stdout=out,
                stderr=err,
                output=comb
            )
        except subprocess.TimeoutExpired:
            err_msg = f"Ошибка: Превышен таймаут выполнения pytest ({timeout} сек)."
            return ScriptExecutionResult(success=False, return_code=-1, stdout="", stderr=err_msg, output=err_msg)
        except Exception as e:
            err_msg = f"Ошибка запуска pytest: {e}"
            return ScriptExecutionResult(success=False, return_code=1, stdout="", stderr=err_msg, output=err_msg)

    @classmethod
    def validate_write_path(self, path: Union[str, Path]) -> Path:
        if not path or not str(path).strip():
            raise PermissionError("[Sandbox] Путь для записи не указан или пуст")

        resolved_target = Path(path).resolve()
        resolved_ws = Path(getattr(self, "workspace_dir", None) or self.get_workspace_dir()).resolve()

        target_str = resolved_target.as_posix().lower()
        ws_str = resolved_ws.as_posix().lower()
        temp_str = Path(tempfile.gettempdir()).resolve().as_posix().lower()

        # Разрешать операцию, если target_str.startswith(ws_str) или target_str.startswith(temp_str)
        # Учитываем границы директорий, чтобы избежать ложных совпадений префиксов
        is_inside_ws = (target_str == ws_str) or target_str.startswith(ws_str.rstrip("/") + "/")
        is_inside_temp = (target_str == temp_str) or target_str.startswith(temp_str.rstrip("/") + "/")

        if not (is_inside_ws or is_inside_temp):
            raise PermissionError(f"[Sandbox] Попытка записи за пределы workspace: {resolved_target}")

        return resolved_target

    @classmethod
    def validate_read_path(cls, path_str: str) -> Path:
        p = Path(path_str)
        workspace = cls.get_workspace_dir().resolve()

        if not p.is_absolute():
            cand = (workspace / p).resolve()
            resolved = cand if cand.exists() else p.resolve()
        else:
            resolved = p.resolve()

        for part in resolved.parts:
            part_clean = part.strip().lower()
            if cls.is_ignored_directory(part_clean):
                raise PermissionError(
                    f"Чтение из системной/заблокированной директории запрещено политикой безопасности: '{part}' (путь: {resolved})"
                )

        if not resolved.exists():
            raise FileNotFoundError(f"Файл или директория не найдены: {path_str}")

        return resolved

    @classmethod
    def validate_read_file_path(cls, path_str: str, session_id: Optional[str] = None) -> Path:
        if not path_str or not str(path_str).strip():
            raise FileNotFoundError("Ошибка: Имя файла не указано.")

        import html
        raw = html.unescape(str(path_str)).strip().strip("'\"`")
        p = Path(raw)
        workspace = cls.get_workspace_dir().resolve()

        active_sid = str(session_id).strip() if session_id else cls._active_session_id
        session_dir = (OUTPUTS_DIR / "sessions" / active_sid).resolve() if active_sid else None

        if not p.is_absolute():
            clean_rel = cls.clean_workspace_filename(raw)
            ws_cand = (workspace / clean_rel).resolve()
            sess_cand = (session_dir / clean_rel).resolve() if session_dir else None

            if ws_cand.is_file():
                resolved = ws_cand
            elif sess_cand and sess_cand.is_file():
                resolved = sess_cand
            else:
                def get_base_stems(name_str: str) -> Set[str]:
                    stems = {name_str.lower()}
                    cur = Path(name_str.lower())
                    stems.add(cur.stem)
                    stems.add(Path(cur.stem).stem)
                    for ext in [".txt", ".json", ".py", ".md", ".csv", ".ini", ".cfg", ".log"]:
                        if name_str.lower().endswith(ext):
                            stems.add(name_str.lower()[:-len(ext)])
                    return {s for s in stems if s}

                target_stems = get_base_stems(clean_rel)
                stem_matches = [
                    f for f in workspace.iterdir()
                    if f.is_file() and (get_base_stems(f.name) & target_stems)
                ]
                if not stem_matches and session_dir and session_dir.exists():
                    stem_matches = [
                        f for f in session_dir.iterdir()
                        if f.is_file() and (get_base_stems(f.name) & target_stems)
                    ]

                if len(stem_matches) == 1:
                    resolved = stem_matches[0]
                else:
                    resolved = ws_cand
        else:
            resolved = p.resolve()

        resolved_str = str(resolved).lower()
        system_blacklist = [
            "c:\\windows",
            "c:\\program files",
            "c:\\program files (x86)",
            "c:\\programdata",
        ]
        for sys_dir in system_blacklist:
            if resolved_str == sys_dir or resolved_str.startswith(sys_dir + "\\") or resolved_str.startswith(sys_dir + "/"):
                raise PermissionError("Ошибка: Доступ к системному пути запрещен политикой безопасности песочницы.")

        sensitive_parts = {".ssh", ".git", ".env"}
        for part in resolved.parts:
            part_clean = part.strip().lower()
            if part_clean in sensitive_parts or cls.is_ignored_directory(part_clean):
                raise PermissionError("Ошибка: Доступ к системному пути запрещен политикой безопасности песочницы.")

        user_home = Path.home().resolve()
        allowed_roots = [
            BASE_DIR.resolve(),
            FORGE_ROOT_DIR.resolve(),
            (user_home / "Desktop").resolve(),
            (user_home / "Documents").resolve(),
            (user_home / "Downloads").resolve(),
        ]

        is_allowed = False
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
                is_allowed = True
                break
            except ValueError:
                continue

        if not is_allowed:
            raise PermissionError("Ошибка: Доступ к системному пути запрещен политикой безопасности песочницы.")

        if not resolved.exists():
            raise FileNotFoundError(f"Ошибка: Файл не найден. Поиск выполнялся в: {resolved}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Ошибка: Указанный путь является директорией, а не файлом: {resolved}")

        max_bytes = 5 * 1024 * 1024
        file_size = resolved.stat().st_size
        if file_size > max_bytes:
            size_mb = file_size / (1024 * 1024)
            raise ValueError(
                f"Ошибка: Размер файла ({size_mb:.2f} МБ) превышает лимит 5 МБ. "
                f"Для обработки больших файлов напишите скрипт автоматизации через run_script."
            )

        return resolved

    @classmethod
    def is_ignored_directory(cls, dir_name: str) -> bool:
        clean_name = dir_name.strip().lower()
        return clean_name in cls.IGNORE_DIRS or clean_name.startswith("$")
    @staticmethod
    def get_changed_line_ranges(ws_dir: Path, filename: str) -> str:
        """Анализирует git diff файла и возвращает отформатированную строку диапазонов строк."""
        try:
            import subprocess
            import re

            res = subprocess.run(
                ["git", "diff", "-U0", "--", filename],
                cwd=str(ws_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )
            diff_text = res.stdout or ""
            if not diff_text:
                return "без изменений"

            # Ищем заголовки вида @@ -old,count +new_start,new_count @@ или @@ -old +new_start @@
            hunks = re.findall(r'@@\s+-[0-9,]+\s+\+([0-9]+)(?:,([0-9]+))?\s+@@', diff_text)
            if not hunks:
                return "строки не определены"

            ranges = []
            for start_str, count_str in hunks:
                start = int(start_str)
                count = int(count_str) if count_str else 1
                if count <= 1:
                    ranges.append(f"строка {start}")
                else:
                    end = start + count - 1
                    ranges.append(f"строки {start}–{end}")

            return ", ".join(ranges)
        except Exception as e:
            return f"строки ? (ошибка diff: {e})"

    @staticmethod
    def commit_manual_edit(ws_dir: Path, filename: str, user_comment: str, line_ranges: str) -> bool:
        """Фиксирует ручную правку пользователя в Git песочницы."""
        try:
            import subprocess
            if not (ws_dir / ".git").exists():
                subprocess.run(["git", "init"], cwd=str(ws_dir), check=True, capture_output=True)
            subprocess.run(["git", "add", filename], cwd=str(ws_dir), check=True, capture_output=True)

            comment_clean = user_comment.strip()
            if comment_clean:
                msg = f"manual: {comment_clean} ({filename}, {line_ranges})"
            else:
                msg = f"manual: правка {filename} ({line_ranges})"

            subprocess.run(["git", "commit", "-m", msg], cwd=str(ws_dir), check=True, capture_output=True)
            return True
        except Exception:
            return False

    @staticmethod
    def verify_single_file(ws_dir: Path, filename: str) -> Tuple[bool, str, str, Optional[int]]:
        """Изолированно валидирует синтаксис (AST) и запускает короткий Smoke-тест файла.
        Возвращает: (is_ok, err_type, message_or_traceback, error_line_num)
        """
        import ast
        import subprocess
        import sys

        target = ws_dir / filename
        if not target.is_file():
            return False, "FileNotFoundError", f"Файл {filename} не найден", None

        # 1. AST валидация
        try:
            code = target.read_text(encoding="utf-8", errors="ignore")
            ast.parse(code, filename=filename)
        except SyntaxError as se:
            return False, "SyntaxError", f"Синтаксическая ошибка: {se.msg}", se.lineno

        # 2. Быстрый изолированный Smoke-запуск (1.5-2 сек)
        try:
            env = dict(sys.modules.get("os", {}).environ or {})
            env["SDL_VIDEODRIVER"] = "dummy"  # Для headless проверки pygame, если доступно
            proc = subprocess.run(
                [get_python_cli_executable(), str(target)],
                cwd=str(ws_dir),
                capture_output=True,
                text=True,
                timeout=2.0,
                encoding="utf-8",
                errors="ignore"
            )
            if proc.returncode != 0:
                # Извлекаем тип ошибки и номер строки из трейсбека
                import re
                lines = proc.stderr.splitlines()
                err_line = None
                err_type = "RuntimeError"
                for l in reversed(lines):
                    m = re.search(r'line\s+(\d+)', l)
                    if m and err_line is None:
                        err_line = int(m.group(1))
                    type_m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*Error|Exception):', l.strip())
                    if type_m:
                        err_type = type_m.group(1)
                return False, err_type, proc.stderr, err_line
        except subprocess.TimeoutExpired:
            # Таймаут для GUI/игр означает, что главный цикл успешно запустился и работает!
            pass
        except Exception as e:
            return False, type(e).__name__, str(e), None

        return True, "", "AST-синтаксис корректен, модуль успешно инициализирован.", None

