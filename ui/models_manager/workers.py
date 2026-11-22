"""
ui/models_manager/workers.py — Многопоточные воркеры сканирования, Civitai и FP8 конвертации.
"""
"""
Models Manager Dialog for NeonRoute.
Configures available Stable Diffusion Forge checkpoints, pipeline generation parameters,
and FastEmbed vector routing rules in modern Windows 11 / Glassmorphism aesthetic.
"""

import os
import subprocess
import logging

logger = logging.getLogger("NeonRoute.ModelsManager.Workers")
import uuid
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from utils.file_utils import get_python_cli_executable

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QHeaderView, QMessageBox, QFrame, QWidget, QApplication,
    QSplitter, QFormLayout, QScrollArea
)
from qfluentwidgets import (
    LineEdit,
    SearchLineEdit,
    TextEdit,
    ComboBox,
    EditableComboBox,
    CheckBox,
    Slider,
    SpinBox,
    DoubleSpinBox,
    PushButton,
    PrimaryPushButton,
    TransparentToolButton,
    SwitchButton,
    ProgressBar,
    InfoBar,
    InfoBarPosition,
    TableWidget,
    FluentIcon as FIF,
    setTheme,
    Theme
)

from services.model_router import ModelRouter
from services.art_director_config import (
    load_art_director_models,
    save_art_director_models
)
from services.art_director.geometry import is_xl_model
from services.forge_client import ForgeClient
from services.forge_models import sync_registry_with_user_config

class RoutingTestWorker(QThread):
    result_ready = pyqtSignal(str, float, str, list)  # (best_id, score, reason, snapshot_profiles)
    error_occurred = pyqtSignal(str)

    def __init__(self, text: str, profiles: List[Dict[str, Any]], router: ModelRouter, parent=None):
        super().__init__(parent)
        self.text = text
        self.profiles = profiles
        self.router = router
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()

    def run(self):
        if self._is_aborted or self.isInterruptionRequested():
            return
        try:
            winner_id, score, reason = self.router.match_prompt_detailed(self.text, profiles=self.profiles)
            if self._is_aborted or self.isInterruptionRequested():
                return
            if not winner_id:
                if not (self._is_aborted or self.isInterruptionRequested()):
                    self.error_occurred.emit("Не удалось определить подходящую модель среди активных.")
                return
            if not (self._is_aborted or self.isInterruptionRequested()):
                self.result_ready.emit(winner_id, score, reason, self.profiles)
        except Exception as e:
            if not (self._is_aborted or self.isInterruptionRequested()):
                logger.error(f"[RoutingTestWorker] Ошибка тестирования маршрутизации: {e}", exc_info=True)
                self.error_occurred.emit(str(e))


class ForgeScanWorker(QThread):
    scan_completed = pyqtSignal(list, list, list)  # (raw_models, installed_checkpoints, upscalers)
    scan_failed = pyqtSignal(str)

    def __init__(self, forge_client: ForgeClient, parent=None):
        super().__init__(parent)
        self.forge_client = forge_client
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()

    def run(self):
        if self._is_aborted or self.isInterruptionRequested():
            return
        try:
            raw_models = self.forge_client.get_models() or []
        except Exception as e:
            logger.warning(f"[ForgeModelsWorker] Сбой получения моделей Forge: {e}")
            raw_models = []

        if self._is_aborted or self.isInterruptionRequested():
            return

        try:
            installed = self.forge_client.get_installed_checkpoints() or []
        except Exception as e:
            logger.warning(f"[ForgeModelsWorker] Сбой получения чекпоинтов Forge: {e}")
            installed = []

        if self._is_aborted or self.isInterruptionRequested():
            return

        try:
            upscalers = self.forge_client.get_upscalers() or []
        except Exception as e:
            logger.warning(f"[ForgeModelsWorker] Сбой получения апскейлеров Forge: {e}")
            upscalers = []

        if not (self._is_aborted or self.isInterruptionRequested()):
            self.scan_completed.emit(raw_models, installed, upscalers)


class CivitaiParamsWorker(QThread):
    params_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, checkpoint: str, parent=None):
        super().__init__(parent)
        self.checkpoint = checkpoint
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()

    def run(self):
        if self._is_aborted or self.isInterruptionRequested():
            return
        try:
            from services.civitai_service import fetch_civitai_parameters
            params = fetch_civitai_parameters(self.checkpoint)
            if not (self._is_aborted or self.isInterruptionRequested()):
                self.params_ready.emit(params)
        except Exception as e:
            if not (self._is_aborted or self.isInterruptionRequested()):
                logger.error(f"[CivitaiParamsWorker] Ошибка запроса Civitai: {e}", exc_info=True)
                self.error_occurred.emit(str(e))


class FP8ConvertWorker(QThread):
    progress_updated = pyqtSignal(str)
    conversion_finished = pyqtSignal(str)
    conversion_failed = pyqtSignal(str)

    def __init__(self, python_exe: str, script_path: Path, input_checkpoint_path: Path, forge_client: Optional[ForgeClient] = None, parent=None):
        super().__init__(parent)
        self.python_exe = str(python_exe)
        self.script_path = Path(script_path)
        self.input_checkpoint_path = Path(input_checkpoint_path)
        self.forge_client = forge_client
        stem = self.input_checkpoint_path.stem
        ext = self.input_checkpoint_path.suffix
        stem_lower = stem.lower()
        if stem_lower.endswith("_fp16e4"):
            new_stem = stem[:-7] + "_fp8e4"
        elif stem_lower.endswith("_fp16"):
            new_stem = stem[:-5] + "_fp8e4"
        elif stem_lower.endswith("-fp16"):
            new_stem = stem[:-5] + "-fp8e4"
        elif not (stem_lower.endswith("_fp8e4") or stem_lower.endswith("_fp8")):
            new_stem = f"{stem}_fp8e4"
        else:
            new_stem = f"{stem}_quant_tmp"
        self.dst_path = self.input_checkpoint_path.with_name(f"{new_stem}{ext}")
        if self.dst_path.resolve() == self.input_checkpoint_path.resolve():
            self.dst_path = self.input_checkpoint_path.with_name(f"{stem}_quant_tmp{ext}")
        self.output_path = self.dst_path
        self._is_aborted = False
        self.process: Optional[subprocess.Popen] = None

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

    def run(self):
        if self._is_aborted or self.isInterruptionRequested():
            return

        startupinfo = None
        creationflags = 0
        import sys
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW

        import os as _os
        import collections as _collections

        py_dir = Path(self.python_exe).parent
        torch_lib = py_dir / "lib" / "site-packages" / "torch" / "lib"
        scripts_dir = py_dir / "Scripts"

        env = dict(_os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)

        # Изоляция PATH: ставим родные DLL Forge на первое место и исключаем библиотеки llama-server
        clean_path_parts = [str(torch_lib), str(py_dir), str(scripts_dir)]
        for p in _os.environ.get("PATH", "").split(_os.pathsep):
            p_low = p.lower()
            if "neonroute" not in p_low and ".lmstudio" not in p_low:
                clean_path_parts.append(p)
        env["PATH"] = _os.pathsep.join(clean_path_parts)

        stdout_tail = _collections.deque(maxlen=30)

        cmd = [
            self.python_exe,
            str(self.script_path),
            str(self.input_checkpoint_path),
            str(self.dst_path)
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                env=env,
                creationflags=creationflags
            )

            if self.process.stdout:
                for line in self.process.stdout:
                    if self._is_aborted or self.isInterruptionRequested():
                        break
                    stripped = line.strip()
                    if stripped:
                        stdout_tail.append(stripped)
                        logger.info(f"[FP8Convert] {stripped}")
                        self.progress_updated.emit(stripped)

            if self._is_aborted or self.isInterruptionRequested():
                if self.process.poll() is None:
                    try:
                        self.process.terminate()
                    except Exception:
                        pass
                return

            ret = self.process.wait()
            if ret == 0 and self.dst_path.exists() and self.dst_path.stat().st_size > 0:
                if self.forge_client is not None:
                    try:
                        self.forge_client.refresh_checkpoints()
                    except Exception as e_rf:
                        logger.debug(f"[FP8ConvertWorker] refresh_checkpoints background error (ignored): {e_rf}")
                self.conversion_finished.emit(str(self.dst_path))
            else:
                tail_msg = chr(10).join(stdout_tail)
                err_text = tail_msg if tail_msg else f"Код завершения: {ret}"
                logger.error(f"[FP8ConvertWorker] Сбой конвертации (код {ret}):\n{err_text}")
                self.conversion_failed.emit(err_text)
        except Exception as e:
            if not (self._is_aborted or self.isInterruptionRequested()):
                logger.error(f"[FP8ConvertWorker] Ошибка выполнения: {e}", exc_info=True)
                self.conversion_failed.emit(str(e))
        finally:
            self.process = None


_MODELS_MANAGER_ORPHANED_WORKERS: List[QThread] = []




def register_orphaned_worker(worker: Optional[QThread]) -> None:
    """
    Безопасно регистрирует работающий поток в глобальном реестре
    для предотвращения разрушения C++ объекта и падения Qt при закрытии диалога.
    """
    if worker is None:
        return
    try:
        if worker.isRunning():
            worker.setParent(None)
            if worker not in _MODELS_MANAGER_ORPHANED_WORKERS:
                _MODELS_MANAGER_ORPHANED_WORKERS.append(worker)
                worker.finished.connect(
                    lambda w=worker: _MODELS_MANAGER_ORPHANED_WORKERS.remove(w)
                    if w in _MODELS_MANAGER_ORPHANED_WORKERS else None
                )
                try:
                    worker.finished.connect(worker.deleteLater)
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"[Workers] register_orphaned_worker error: {e}")


def resolve_converter_script() -> Optional[Path]:
    """
    Ищет скрипт конвертации SDXL в FP8 по кандидатам относительно корня проекта.
    """
    from config import BASE_DIR
    candidates = [
        BASE_DIR / "models" / "convert_sdxl_to_fp8.py",
        BASE_DIR / "scripts" / "convert_sdxl_to_fp8.py"
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def verify_python(py_path: str) -> bool:
    """Проверяет работоспособность интерпретатора Python и наличие numpy."""
    if not py_path or not Path(py_path).is_file():
        return False
    try:
        res = subprocess.run(
            [str(py_path), "-c", "import numpy; print('OK')"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return res.returncode == 0 and "OK" in res.stdout
    except Exception:
        return False


_GLOBAL_TORCH_PYTHON: Optional[str] = None


def resolve_torch_python(parent_widget=None) -> Optional[str]:
    """
    Динамически определяет путь к интерпретатору с PyTorch и safetensors:
    - Проверяет кэш.
    - Проверяет sys.executable.
    - Проверяет forge_launcher.bat / launcher из настроек Forge.
    - Если не найдено — запрашивает через QFileDialog (если передан parent_widget).
    """
    global _GLOBAL_TORCH_PYTHON
    if _GLOBAL_TORCH_PYTHON and Path(_GLOBAL_TORCH_PYTHON).is_file():
        return _GLOBAL_TORCH_PYTHON

    import sys
    cli_python = get_python_cli_executable()
    if verify_python(cli_python):
        _GLOBAL_TORCH_PYTHON = str(Path(cli_python).resolve())
        return _GLOBAL_TORCH_PYTHON

    from config import FORGE_LAUNCHER_PATH, FORGE_ROOT_DIR

    candidates: List[Path] = []
    if FORGE_LAUNCHER_PATH.exists():
        launcher_dir = FORGE_LAUNCHER_PATH.parent
        candidates.extend([
            launcher_dir / "webui_forge_cu121_torch231" / "system" / "python" / "python.exe",
            launcher_dir / "system" / "python" / "python.exe",
            launcher_dir / "python" / "python.exe",
            launcher_dir / "venv" / "Scripts" / "python.exe",
        ])
        try:
            content = FORGE_LAUNCHER_PATH.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                if "set PYTHON=" in line:
                    val = line.split("set PYTHON=", 1)[1].strip().strip('"')
                    val_resolved = val.replace("%SYSTEM%", str(launcher_dir / "webui_forge_cu121_torch231" / "system"))
                    val_resolved = val_resolved.replace("%FORGE%", str(launcher_dir / "webui_forge_cu121_torch231"))
                    val_resolved = val_resolved.replace("%ROOT%", str(launcher_dir))
                    val_resolved = val_resolved.replace("%~dp0", str(launcher_dir) + "\\")
                    candidates.insert(0, Path(val_resolved))
        except Exception as e:
            logger.debug(f"Не удалось распарсить {FORGE_LAUNCHER_PATH}: {e}")

    candidates.extend([
        FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "system" / "python" / "python.exe",
        FORGE_ROOT_DIR / "system" / "python" / "python.exe",
        FORGE_ROOT_DIR / "python" / "python.exe",
        FORGE_ROOT_DIR / "venv" / "Scripts" / "python.exe",
    ])

    for c in candidates:
        if c.is_file() and verify_python(str(c)):
            _GLOBAL_TORCH_PYTHON = str(c.resolve())
            return _GLOBAL_TORCH_PYTHON

    if verify_python(cli_python):
        return cli_python

    if parent_widget is not None:
        from PyQt6.QtWidgets import QFileDialog
        chosen, _ = QFileDialog.getOpenFileName(
            parent_widget,
            "Выберите интерпретатор Python с PyTorch и safetensors",
            str(FORGE_ROOT_DIR if FORGE_ROOT_DIR.exists() else Path.home()),
            "Python Executable (python.exe);;All Files (*.*)"
        )
        if chosen and verify_python(chosen):
            _GLOBAL_TORCH_PYTHON = str(Path(chosen).resolve())
            return _GLOBAL_TORCH_PYTHON

    return None


def resolve_checkpoint_path(ckpt_name: str, forge_client: Optional[ForgeClient] = None) -> Optional[Path]:
    """
    Находит полный путь к чекпоинту: сначала мгновенно на локальном диске (0 мс),
    затем через кеш installed_checkpoints без блокировки GUI.
    """
    if not ckpt_name:
        return None

    p_raw = Path(ckpt_name)
    if p_raw.is_absolute() and p_raw.is_file():
        return p_raw.resolve()

    clean_name = p_raw.name.lower()

    # 0. Мгновенная локальная проверка на диске (0 мс)
    from config import FORGE_ROOT_DIR, BASE_DIR
    local_dirs = [
        FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion",
        FORGE_ROOT_DIR / "webui" / "models" / "Stable-diffusion",
        FORGE_ROOT_DIR / "models" / "Stable-diffusion",
    ]
    for ld in local_dirs:
        if ld and ld.is_dir():
            direct_file = ld / p_raw.name
            if direct_file.is_file():
                return direct_file.resolve()

    # 1. Запрос списка установленных чекпоинтов через ForgeClient
    if forge_client is not None:
        try:
            installed = forge_client.get_installed_checkpoints() or []
        except Exception:
            installed = []

        for item in installed:
            p = Path(item)
            if p.name.lower() == clean_name:
                if p.is_absolute() and p.is_file():
                    return p
                elif p.is_file():
                    return p.resolve()

        try:
            raw_models = forge_client.get_models() or []
        except Exception:
            raw_models = []

        for item in raw_models:
            if isinstance(item, dict):
                fn = item.get("filename")
                if fn:
                    p = Path(fn)
                    if p.name.lower() == clean_name and p.is_file():
                        return p

    # 2. Поиск по локальным каталогам
    all_local_dirs = [
        FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion",
        FORGE_ROOT_DIR / "webui" / "models" / "Stable-diffusion",
        FORGE_ROOT_DIR / "models" / "Stable-diffusion",
        BASE_DIR / "models"
    ]
    for d in all_local_dirs:
        candidate = d / Path(ckpt_name).name
        if candidate.is_file():
            return candidate.resolve()

    if p_raw.is_absolute() and p_raw.is_file():
        return p_raw.resolve()

    return None
