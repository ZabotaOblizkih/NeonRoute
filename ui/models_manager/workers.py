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
import uuid
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

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
        except Exception:
            raw_models = []

        if self._is_aborted or self.isInterruptionRequested():
            return

        try:
            installed = self.forge_client.get_installed_checkpoints() or []
        except Exception:
            installed = []

        if self._is_aborted or self.isInterruptionRequested():
            return

        try:
            upscalers = self.forge_client.get_upscalers() or []
        except Exception:
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

    def __init__(self, python_exe: str, script_path: Path, input_checkpoint_path: Path, parent=None):
        super().__init__(parent)
        self.python_exe = str(python_exe)
        self.script_path = Path(script_path)
        self.input_checkpoint_path = Path(input_checkpoint_path)
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
                creationflags=creationflags
            )

            if self.process.stdout:
                for line in self.process.stdout:
                    if self._is_aborted or self.isInterruptionRequested():
                        break
                    stripped = line.strip()
                    if stripped:
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
                self.conversion_finished.emit(str(self.dst_path))
            else:
                self.conversion_failed.emit(f"Код завершения: {ret}")
        except Exception as e:
            if not (self._is_aborted or self.isInterruptionRequested()):
                logger.error(f"[FP8ConvertWorker] Ошибка выполнения: {e}", exc_info=True)
                self.conversion_failed.emit(str(e))
        finally:
            self.process = None


_MODELS_MANAGER_ORPHANED_WORKERS: List[QThread] = []


