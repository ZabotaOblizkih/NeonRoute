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


def _detect_windows_gpu_via_registry() -> Dict[str, Any]:
    """
    Определяет дискретную/основную видеокарту и объем VRAM через системный реестр Windows.
    Ветка: HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}
    Возвращает словарь:
    {
        "gpu_name": str,
        "vram_total_mb": int,
        "vendor": "nvidia" | "amd" | "intel" | "unknown"
    }
    """
    result: Dict[str, Any] = {
        "gpu_name": "Unknown GPU",
        "vram_total_mb": 0,
        "vendor": "unknown"
    }
    if os.name != "nt":
        return result

    try:
        import winreg
        key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        candidates = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(k, i)
                    i += 1
                    if not subkey_name.isdigit():
                        continue
                    with winreg.OpenKey(k, subkey_name) as sk:
                        try:
                            desc, _ = winreg.QueryValueEx(sk, "DriverDesc")
                            desc = str(desc).strip()
                        except Exception:
                            continue

                        # Игнорируем базовые/виртуальные видеоадаптеры
                        desc_lower = desc.lower()
                        ignored = [
                            "microsoft basic", "basic render", "remote desktop",
                            "citrix", "virtual", "vbox", "vmware", "parsec", "rdp"
                        ]
                        if any(ign in desc_lower for ign in ignored):
                            continue

                        # Считываем размер памяти (64-битный qwMemorySize или MemorySize)
                        mem_bytes = 0
                        for mem_key in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                val, _ = winreg.QueryValueEx(sk, mem_key)
                                if isinstance(val, int) and val > 0:
                                    mem_bytes = val
                                    break
                                elif isinstance(val, bytes) and len(val) >= 4:
                                    import struct
                                    if len(val) >= 8:
                                        mem_bytes = struct.unpack("<Q", val[:8])[0]
                                    else:
                                        mem_bytes = struct.unpack("<I", val[:4])[0]
                                    if mem_bytes > 0:
                                        break
                            except Exception:
                                pass

                        vram_mb = int(mem_bytes // (1024 * 1024))
                        vendor = "unknown"
                        if any(x in desc_lower for x in ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")):
                            vendor = "nvidia"
                        elif any(x in desc_lower for x in ("amd", "radeon")):
                            vendor = "amd"
                        elif any(x in desc_lower for x in ("intel", "arc")):
                            vendor = "intel"

                        candidates.append({
                            "gpu_name": desc,
                            "vram_total_mb": vram_mb,
                            "vendor": vendor,
                            "bytes": mem_bytes
                        })
                except OSError:
                    break

        if not candidates:
            return result

        # Сортировка: в приоритете распознанный дискретный вендор с максимальным VRAM
        candidates.sort(key=lambda c: (c["vendor"] != "unknown", c["vram_total_mb"]), reverse=True)
        best = candidates[0]

        # Если VRAM <= 512 МБ — считаем, что подходящего дискретного GPU нет
        if best["vram_total_mb"] <= 512:
            return {
                "gpu_name": best["gpu_name"],
                "vram_total_mb": 0,
                "vendor": best["vendor"]
            }

        return {
            "gpu_name": best["gpu_name"],
            "vram_total_mb": best["vram_total_mb"],
            "vendor": best["vendor"]
        }
    except Exception as e:
        logger.debug(f"Registry GPU detection error: {e}")
        return result


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
                    return
            except Exception as e:
                logger.warning(f"Could not initialize NVML: {e}")
                self.nvml_initialized = False

        # Fallback to Windows Registry (AMD / Intel / Discrete)
        reg_info = _detect_windows_gpu_via_registry()
        if reg_info.get("gpu_name") and reg_info.get("gpu_name") != "Unknown GPU":
            self.gpu_name = reg_info["gpu_name"]
            logger.info(
                f"Windows Registry GPU Detected: {self.gpu_name} "
                f"(VRAM: {reg_info.get('vram_total_mb')} MB, Vendor: {reg_info.get('vendor')})"
            )

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
            val = int(self._last_emitted_stats.get("vram_total_mb", 0))
            if val > 0:
                return val
        try:
            if HAS_PYNVML:
                pynvml.nvmlInit()
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                return int(mem_info.total / (1024 * 1024))
        except Exception:
            pass
        # Fallback к реестру Windows (AMD / Intel)
        reg_info = _detect_windows_gpu_via_registry()
        if reg_info.get("vram_total_mb", 0) > 0:
            return int(reg_info["vram_total_mb"])
        return 0

    def get_free_vram_mb(self) -> int:
        """Возвращает текущий объем свободной видеопамяти в МБ."""
        if self.nvml_initialized and self.gpu_handle:
            try:
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                return int(mem_info.free / (1024 * 1024))
            except Exception:
                pass
        if self._last_emitted_stats:
            total = self._last_emitted_stats.get("vram_total_mb", 0)
            used = self._last_emitted_stats.get("vram_used_mb", 0)
            return max(0, total - used)
        total_reg = self.get_total_vram_mb()
        return max(0, total_reg - 500) if total_reg > 500 else 0

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
            "vram_total_mb": 0,
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
        # Быстрый прямой вызов без shell=True и без cmd.exe для систем с nvidia-smi
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
            return
        except Exception:
            pass

        # Если nvidia-smi недоступен (AMD, Intel, iGPU, CPU), опрашиваем реестр
        reg_info = _detect_windows_gpu_via_registry()
        reg_gpu = reg_info.get("gpu_name")
        if reg_gpu and reg_gpu != "Unknown GPU":
            stats["gpu_name"] = reg_gpu
            self.gpu_name = reg_gpu
        stats["vram_total_mb"] = reg_info.get("vram_total_mb", 0)
        stats["vram_used_mb"] = 0
        stats["vram_percent"] = 0.0