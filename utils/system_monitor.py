"""
System Hardware Monitor for NeonRoute
Optimized to prevent any UI micro-stutters or subprocess spawn lag on Windows.
"""

import os
import logging
import subprocess
from typing import Dict, Any, Optional
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("NeonRoute.SystemMonitor")

try:
    import pynvml
    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False


class SystemMonitor(QThread):
    stats_updated = pyqtSignal(dict)

    def __init__(self, interval_ms: int = 4000):
        super().__init__()
        self.interval_ms = max(interval_ms, 3500)
        self._running = True
        self.nvml_initialized = False
        self.gpu_handle = None
        self.gpu_name = "NVIDIA GPU"
        self._last_emitted_stats: Optional[Dict[str, Any]] = None

    def run(self):
        self._init_nvml()

        # Первый замер
        try:
            stats = self._poll_hardware()
            self._emit_if_changed(stats)
        except Exception as e:
            logger.debug(f"Initial hardware poll error: {e}")

        while self._running:
            elapsed = 0
            while elapsed < self.interval_ms and self._running:
                self.msleep(100)
                elapsed += 100

            if not self._running:
                break

            try:
                stats = self._poll_hardware()
                self._emit_if_changed(stats)
            except Exception as e:
                logger.debug(f"Hardware poll iteration error: {e}")

        self._cleanup_nvml()

    def _init_nvml(self):
        if HAS_PYNVML:
            try:
                pynvml.nvmlInit()
                count = pynvml.nvmlDeviceGetCount()
                if count > 0:
                    self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    name = pynvml.nvmlDeviceGetName(self.gpu_handle)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", errors="replace")
                    self.gpu_name = name
                    self.nvml_initialized = True
                    logger.info(f"NVML Initialized. Detected: {self.gpu_name}")
            except Exception as e:
                logger.warning(f"Could not initialize NVML: {e}")
                self.nvml_initialized = False

    def _cleanup_nvml(self):
        if self.nvml_initialized:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self.nvml_initialized = False

    def stop(self):
        self._running = False
        self.wait(1200)

    def get_total_vram_mb(self) -> int:
        """Возвращает общий объем видеопамяти GPU в МБ."""
        if self.nvml_initialized and self.gpu_handle:
            try:
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                return int(mem_info.total / (1024 * 1024))
            except Exception:
                pass
        if self._last_emitted_stats:
            return int(self._last_emitted_stats.get("vram_total_mb", 8192))
        try:
            if HAS_PYNVML:
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                return int(mem_info.total / (1024 * 1024))
        except Exception:
            pass
        return 8192

    def get_free_vram_mb(self) -> int:
        """Возвращает текущий объем свободной видеопамяти в МБ."""
        if self.nvml_initialized and self.gpu_handle:
            try:
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                return int(mem_info.free / (1024 * 1024))
            except Exception:
                pass
        if self._last_emitted_stats:
            total = self._last_emitted_stats.get("vram_total_mb", 8192)
            used = self._last_emitted_stats.get("vram_used_mb", 0)
            return max(0, total - used)
        return 4000  # Безопасное значение по умолчанию

    def _emit_if_changed(self, stats: Dict[str, Any]):
        # Предотвращает дергание интерфейса стилями, если цифры не изменились
        if self._last_emitted_stats is None:
            self._last_emitted_stats = stats
            self.stats_updated.emit(stats)
            return

        diff_vram = abs(stats["vram_used_mb"] - self._last_emitted_stats.get("vram_used_mb", 0)) > 30
        diff_ram = abs(stats["ram_used_gb"] - self._last_emitted_stats.get("ram_used_gb", 0)) > 0.1
        diff_temp = stats.get("gpu_temp_c") != self._last_emitted_stats.get("gpu_temp_c")

        if diff_vram or diff_ram or diff_temp:
            self._last_emitted_stats = stats
            self.stats_updated.emit(stats)

    def get_hardware_snapshot(self) -> Dict[str, Any]:
        """Публичный синхронный снимок телеметрии GPU/VRAM/RAM."""
        return self._poll_hardware()

    def _poll_hardware(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "vram_used_mb": 0,
            "vram_total_mb": 8192,
            "vram_percent": 0.0,
            "gpu_name": self.gpu_name,
            "ram_used_gb": 0.0,
            "ram_total_gb": 16.0,
            "ram_used_mb": 0.0,
            "ram_total_mb": 16384.0,
            "ram_percent": 0.0,
            "gpu_temp_c": None
        }

        # 1. Замер VRAM
        if self.nvml_initialized and self.gpu_handle:
            try:
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                stats["vram_used_mb"] = int(mem_info.used / (1024 * 1024))
                stats["vram_total_mb"] = int(mem_info.total / (1024 * 1024))
                if stats["vram_total_mb"] > 0:
                    stats["vram_percent"] = round((stats["vram_used_mb"] / stats["vram_total_mb"]) * 100, 1)

                try:
                    stats["gpu_temp_c"] = pynvml.nvmlDeviceGetTemperature(
                        self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"NVML poll error: {e}")
                self._poll_vram_fallback(stats)
        else:
            self._poll_vram_fallback(stats)

        # 2. Замер RAM
        try:
            import psutil
            mem = psutil.virtual_memory()
            stats["ram_used_mb"] = round(mem.used / (1024 * 1024), 1)
            stats["ram_total_mb"] = round(mem.total / (1024 * 1024), 1)
            stats["ram_used_gb"] = round(mem.used / (1024 ** 3), 2)
            stats["ram_total_gb"] = round(mem.total / (1024 ** 3), 2)
            stats["ram_percent"] = mem.percent
        except Exception as e:
            logger.debug(f"psutil poll error: {e}")

        return stats

    def _poll_vram_fallback(self, stats: Dict[str, Any]):
        # Быстрый прямой вызов без shell=True и без cmd.exe
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            res = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu", "--format=csv,nounits,noheader"],
                shell=False,
                stderr=subprocess.DEVNULL,
                timeout=0.6,
                creationflags=flags
            )
            line = res.decode("utf-8", errors="replace").strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                stats["vram_used_mb"] = int(float(parts[0]))
                stats["vram_total_mb"] = int(float(parts[1]))
                if stats["vram_total_mb"] > 0:
                    stats["vram_percent"] = round((stats["vram_used_mb"] / stats["vram_total_mb"]) * 100, 1)
            if len(parts) >= 3:
                stats["gpu_temp_c"] = int(float(parts[2]))
        except Exception:
            pass