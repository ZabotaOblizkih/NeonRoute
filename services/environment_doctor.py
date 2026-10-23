import sys
import time
import shutil
import logging
import subprocess
import importlib.util
from pathlib import Path
from typing import List, Dict, Any
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("NeonRoute.EnvironmentDoctor")

DEPENDENCY_CATALOG: Dict[str, Dict[str, Any]] = {
    "git": {
        "type": "binary",
        "name": "Git (Система версионирования)",
        "description": "Позволяет делать мгновенные бэкапы проекта, откатывать ошибки генерации за миллисекунды и сохранять историю правок.",
        "consequence": "Без Git система будет использовать стандартные ZIP-архивы.",
        "install_guide": "Установите Git с git-scm.com либо скопируйте папку MinGit в NeonRoute/tools/git",
        "is_critical": False,
    },
    "pygame": {
        "type": "python",
        "package": "pygame",
        "name": "Pygame (Игровой 2D-движок)",
        "description": "Необходим Инженеру для создания 2D-игр, физических симуляций и интерактивных анимаций.",
        "consequence": "Инженер не сможет запускать и тестировать созданные 2D-игры.",
        "is_critical": False,
    },
    "customtkinter": {
        "type": "python",
        "package": "customtkinter",
        "name": "CustomTkinter (Современный графический интерфейс)",
        "description": "Используется Инженером для создания современных оконных программ, панелей управления и интерфейсов для баз данных SQLite в стиле NeonRoute.",
        "consequence": "Оконные приложения будут создаваться на стандартном базовом интерфейсе Tkinter.",
        "is_critical": False,
    },
    "openpyxl": {
        "type": "python",
        "package": "openpyxl",
        "name": "OpenPyXL (Работа с таблицами Excel)",
        "description": "Требуется для создания и модификации таблиц .xlsx, автоматических смет и отчётов с формулами.",
        "consequence": "Генерация Excel-документов будет недоступна.",
        "is_critical": False,
    },
    "docx": {
        "type": "python",
        "package": "python-docx",
        "name": "Python-Docx (Работа с документами Word)",
        "description": "Используется для генерации текстовых документов .docx, приказов и договоров с форматированием.",
        "consequence": "Генерация Word-документов будет недоступна.",
        "is_critical": False,
    },
}

class PipInstallWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, packages: List[str]):
        super().__init__()
        self.packages = packages

    def run(self):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        for pkg in self.packages:
            logger.info(f"[EnvironmentDoctor] Запуск установки pip: {pkg}...")
            self.progress.emit(f"Установка {pkg}...")
            cmd = [sys.executable, "-m", "pip", "install", pkg]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=flags
                )
                out, _ = proc.communicate()
                if proc.returncode != 0:
                    err_msg = f"Ошибка установки {pkg}:\n{out}"
                    logger.error(f"[EnvironmentDoctor] {err_msg}")
                    self.finished.emit(False, err_msg)
                    return
                logger.info(f"[EnvironmentDoctor] ✅ Пакет {pkg} успешно установлен.")
            except Exception as e:
                err_msg = f"Сбой запуска процесса pip: {e}"
                logger.error(f"[EnvironmentDoctor] {err_msg}")
                self.finished.emit(False, err_msg)
                return
        self.finished.emit(True, "Все выбранные компоненты успешно установлены!")

class EnvironmentDoctor:
    @classmethod
    def check_missing(cls, verbose: bool = True) -> List[Dict[str, Any]]:
        t_start = time.perf_counter()
        missing = []

        for key, meta in DEPENDENCY_CATALOG.items():
            if meta["type"] == "binary":
                bundled_git = Path("tools/git/cmd/git.exe").resolve()
                bundled_git_bin = Path("tools/git/bin/git.exe").resolve()
                if not bundled_git.is_file() and not bundled_git_bin.is_file() and not shutil.which("git"):
                    missing.append({**meta, "key": key})
            elif meta["type"] == "python":
                spec = importlib.util.find_spec(key)
                if spec is None:
                    missing.append({**meta, "key": key})

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        if verbose:
            if not missing:
                logger.info(
                    f"[EnvironmentDoctor] ✅ Все компоненты окружения в наличии "
                    f"({len(DEPENDENCY_CATALOG)} проверено за {elapsed_ms:.2f} мс)."
                )
            else:
                names = [f"{m['name']} ({m['key']})" for m in missing]
                logger.warning(
                    f"[EnvironmentDoctor] ⚠️ Обнаружено отсутствие {len(missing)} компонентов "
                    f"(сканирование заняло {elapsed_ms:.2f} мс): {', '.join(names)}"
                )

        return missing
