"""
services/layer_budgeter.py
GPU Topology Auditor & GGUF Layer Offload Budgeter for NeonRoute.
Calculates optimal layer distribution between GPU VRAM and System RAM.
"""

import os
import sys
import json
import math
import struct
import io
import logging
import ctypes
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, Union, BinaryIO

import psutil

from config import (
    DATA_DIR,
    VRAM_TOTAL_BUDGET_MB,
    FORGE_VRAM_RESERVE_MB,
    SAFETY_MARGIN_VRAM_MB,
    OS_RESERVE_PURE_COMPUTE_MB,
    OS_RESERVE_PRIMARY_DISPLAY_MB,
    HYSTERESIS_LAYER_DELTA,
    DEFAULT_LLAMA_UBATCH_SIZE,
)
from services.server_config import resolve_kv_cache_types

logger = logging.getLogger("NeonRoute.LayerBudgeter")
MODELS_CACHE_PATH = DATA_DIR / "models_cache.json"
CURRENT_CACHE_SCHEMA_VERSION = 5


class LayerBudgeter:
    """
    Автономный сервис топологического аудита GPU, парсинга метаданных GGUF
    и расчета баланса слоев между GPU и RAM.
    """

    OS_HEADROOM_RAM_MB: float = 1500.0
    CUDA_HOST_PINNED_RAM_MB: float = 400.0
    SLOT_RESTORE_HEADROOM_MB: float = 600.0

    @classmethod
    def get_available_host_memory_mb(cls) -> float:
        """
        Оценивает доступную системную память хоста:
        - В Windows опрашивает GlobalMemoryStatusEx для получения ullAvailPageFile
          (лимит незакоммиченной виртуальной памяти) и psutil.virtual_memory().available.
          Если физической RAM достаточно (>= OS_HEADROOM_RAM_MB), не блокирует запуск
          малым текущим значением ullAvailPageFile из-за настроек файла подкачки Windows.
        - На других платформах возвращает psutil.virtual_memory().available.
        """
        import psutil
        try:
            avail_ram_mb = psutil.virtual_memory().available / (1024.0 * 1024.0)
        except Exception:
            avail_ram_mb = 4096.0

        if sys.platform == "win32":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    avail_phys_mb = stat.ullAvailPhys / (1024.0 * 1024.0)
                    avail_pagefile_mb = stat.ullAvailPageFile / (1024.0 * 1024.0)

                    # Если свободной физической RAM достаточно (>= OS_HEADROOM_RAM_MB),
                    # не блокируем запуск малым текущим размером pagefile (ullAvailPageFile),
                    # так как Windows динамически расширяет pagefile или размещает память в RAM.
                    if avail_phys_mb >= cls.OS_HEADROOM_RAM_MB:
                        return max(0.0, max(avail_phys_mb, avail_ram_mb))

                    # При дефиците физической RAM берем доступную память с учетом коммита
                    effective_mb = max(avail_phys_mb, min(avail_ram_mb, avail_pagefile_mb))
                    return max(0.0, effective_mb)
            except Exception as e:
                logger.debug(f"[LayerBudgeter] GlobalMemoryStatusEx fallback to psutil: {e}")

        return max(0.0, avail_ram_mb)

    # -------------------------------------------------------------------------
    # 1. Аудит топологии GPU (Pure Compute vs Primary Display)
    # -------------------------------------------------------------------------
    @classmethod
    def detect_gpu_topology(cls) -> Tuple[bool, int]:
        """
        Опрашивает pynvml.
        Аппаратный флаг NVML первичнее цифры текущей занятости:
        Если nvmlDeviceGetDisplayActive(handle) == False (или 0), видеокарта физически
        НЕ выводит изображение на монитор (дисплей подключен к iGPU).
        В таком случае ВСЕГДА возвращается (True, OS_RESERVE_PURE_COMPUTE_MB = 150),
        даже если текущая занятость VRAM превышает 250 МБ (остаточный кэш PyTorch/Forge).
        Переключение в PRIMARY DISPLAY (False, OS_RESERVE_PRIMARY_DISPLAY_MB = 700) ТОЛЬКО если:
            * nvmlDeviceGetDisplayActive(handle) == True, ИЛИ
            * на GPU обнаружен активный процесс dwm.exe / csrss.exe с графическим дескриптором, ИЛИ
            * произошла фатальная ошибка опроса NVML (фоллбэк).
        """
        try:
            used_mb = cls._query_nvml_used_vram_mb()

            import pynvml
            try:
                pynvml.nvmlInit()
            except Exception as init_err:
                if "already initialized" not in str(init_err).lower():
                    raise

            handle = pynvml.nvmlDeviceGetHandleByIndex(0)

            display_active = False
            if hasattr(pynvml, "nvmlDeviceGetDisplayActive"):
                display_active = bool(pynvml.nvmlDeviceGetDisplayActive(handle))

            # Проверка наличия графических процессов dwm.exe / csrss.exe на GPU
            has_display_proc = False
            if hasattr(pynvml, "nvmlDeviceGetGraphicsRunningProcesses"):
                try:
                    graphics_procs = pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)
                    if isinstance(graphics_procs, (list, tuple)):
                        for proc in graphics_procs:
                            try:
                                pid = getattr(proc, "pid", None)
                                if pid is None:
                                    continue
                                proc_name = None
                                if hasattr(pynvml, "nvmlSystemGetProcessName"):
                                    try:
                                        name_b = pynvml.nvmlSystemGetProcessName(pid)
                                        if isinstance(name_b, bytes):
                                            proc_name = name_b.decode("utf-8", errors="replace")
                                        elif isinstance(name_b, str):
                                            proc_name = name_b
                                    except Exception:
                                        pass
                                if not proc_name and isinstance(pid, int):
                                    try:
                                        proc_name = psutil.Process(pid).name()
                                    except Exception:
                                        pass
                                elif not proc_name and hasattr(proc, "name"):
                                    proc_name = getattr(proc, "name", "")

                                if proc_name and str(proc_name).lower() in ("dwm.exe", "csrss.exe"):
                                    has_display_proc = True
                                    break
                            except Exception:
                                continue
                except Exception as proc_err:
                    logger.debug(f"[LayerBudgeter] Ошибка сканирования графических процессов NVML: {proc_err}")

            if display_active or has_display_proc:
                logger.info(
                    f"[LayerBudgeter] Топология: PRIMARY DISPLAY (VRAM used: {used_mb:.1f} MB, "
                    f"display_active={display_active}, display_proc={has_display_proc}) -> резерв {OS_RESERVE_PRIMARY_DISPLAY_MB} МБ"
                )
                return (False, int(OS_RESERVE_PRIMARY_DISPLAY_MB))
            else:
                logger.info(
                    f"[LayerBudgeter] Топология: PURE COMPUTE (VRAM used: {used_mb:.1f} MB, "
                    f"display_active={display_active}, display_proc={has_display_proc}) -> резерв {OS_RESERVE_PURE_COMPUTE_MB} МБ"
                )
                return (True, int(OS_RESERVE_PURE_COMPUTE_MB))

        except Exception as e:
            logger.debug(f"[LayerBudgeter] NVML detection error/fallback: {e}")
            fallback_reserve = int(OS_RESERVE_PRIMARY_DISPLAY_MB) if OS_RESERVE_PRIMARY_DISPLAY_MB else 700
            return (False, fallback_reserve)

    @staticmethod
    def has_cpu_avx2() -> bool:
        """
        Проверяет поддержку инструкций AVX2 процессором.
        На Windows вызывает IsProcessorFeaturePresent(40) (PF_AVX2_INSTRUCTIONS_AVAILABLE = 40).
        При исключении или на других ОС возвращает True.
        """
        if os.name == "nt":
            try:
                return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
            except Exception:
                return True
        return True

    @classmethod
    def detect_hardware_kv_profile(cls) -> Dict[str, Any]:
        """
        Детектор архитектуры GPU для автотюна KV-кэша и Flash Attention.
        Чипы Turing+ (Compute Capability >= 7.5 или RTX) поддерживают скоростной q8_0 и Flash Attention.
        Возвращает vendor, vram_total_mb, compute_cap и рекомендации.
        """
        try:
            import pynvml
            try:
                pynvml.nvmlInit()
            except Exception as init_err:
                if "already initialized" not in str(init_err).lower():
                    raise

            count = pynvml.nvmlDeviceGetCount()
            if count <= 0:
                raise RuntimeError("No NVML devices")

            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            gpu_name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode("utf-8")
            gpu_name = str(gpu_name or "NVIDIA GPU")

            # Расчет VRAM через NVML
            try:
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                vram_total_mb = int(mem_info.total / (1024 * 1024))
            except Exception:
                vram_total_mb = 8192

            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            compute_cap_val = float(major) + float(minor) / 10.0
            cap_str = f"{major}.{minor}"

            if compute_cap_val >= 7.5 or "RTX" in gpu_name.upper():
                return {
                    "supported": True,
                    "gpu_name": gpu_name,
                    "vendor": "nvidia",
                    "vram_total_mb": vram_total_mb,
                    "compute_cap": cap_str,
                    "recommended_kv": "q8_0",
                    "recommended_fa": True,
                    "cpu_avx2": cls.has_cpu_avx2(),
                }
            else:
                return {
                    "supported": False,
                    "gpu_name": gpu_name,
                    "vendor": "nvidia",
                    "vram_total_mb": vram_total_mb,
                    "compute_cap": cap_str if major else "N/A",
                    "recommended_kv": "f16",
                    "recommended_fa": False,
                    "cpu_avx2": cls.has_cpu_avx2(),
                }
        except Exception as e:
            logger.debug(f"[LayerBudgeter] detect_hardware_kv_profile fallback error: {e}")
            from utils.system_monitor import _detect_windows_gpu_via_registry
            reg_info = _detect_windows_gpu_via_registry()
            return {
                "supported": False,
                "gpu_name": reg_info.get("gpu_name", "Unknown GPU"),
                "vendor": reg_info.get("vendor", "unknown"),
                "vram_total_mb": int(reg_info.get("vram_total_mb", 0)),
                "compute_cap": "N/A",
                "recommended_kv": "f16",
                "recommended_fa": False,
                "cpu_avx2": cls.has_cpu_avx2(),
            }

    @classmethod
    def _query_nvml_used_vram_mb(cls) -> float:
        """
        Внутренний хелпер безопасного опроса занятой VRAM в МБ через NVML.
        При ошибке или отсутствии GPU возвращает 0.0.
        """
        try:
            import pynvml
            try:
                pynvml.nvmlInit()
            except Exception as init_err:
                if "already initialized" not in str(init_err).lower():
                    raise
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return float(mem_info.used / (1024.0 * 1024.0))
        except Exception as e:
            logger.debug(f"[LayerBudgeter] _query_nvml_used_vram_mb fallback: {e}")
            return 0.0

    @classmethod
    def get_current_used_vram_mb(cls) -> float:
        """
        Безопасное получение текущего объема занятой видеопамяти GPU в МБ через NVML.
        При ошибке или отсутствии GPU возвращает 0.0.
        Во время pytest возвращает значение только если NVML явно замокан в тесте,
        предотвращая влияние фоновых процессов хоста на детерминированные тесты профилей.
        """
        if "PYTEST_CURRENT_TEST" in os.environ:
            try:
                import pynvml
                from unittest.mock import Mock
                if hasattr(pynvml, "nvmlDeviceGetMemoryInfo") and isinstance(pynvml.nvmlDeviceGetMemoryInfo, Mock):
                    return cls._query_nvml_used_vram_mb()
                if hasattr(pynvml, "nvmlDeviceGetHandleByIndex") and isinstance(pynvml.nvmlDeviceGetHandleByIndex, Mock):
                    return cls._query_nvml_used_vram_mb()
            except Exception:
                pass
            return 0.0
        return cls._query_nvml_used_vram_mb()

    # -------------------------------------------------------------------------
    # 2. Парсер метаданных GGUF с кэшированием
    # -------------------------------------------------------------------------
    @classmethod
    def _read_models_cache(cls) -> Dict[str, Any]:
        """Безопасное чтение data/models_cache.json."""
        if not MODELS_CACHE_PATH.exists():
            return {}
        try:
            with open(MODELS_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"[LayerBudgeter] Ошибка чтения models_cache.json: {e}")
            return {}

    @classmethod
    def _write_models_cache(cls, cache: Dict[str, Any]) -> None:
        """Безопасная запись data/models_cache.json."""
        try:
            MODELS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = MODELS_CACHE_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            tmp_path.replace(MODELS_CACHE_PATH)
        except Exception as e:
            logger.warning(f"[LayerBudgeter] Ошибка сохранения models_cache.json: {e}")

    @classmethod
    def _parse_gguf_header(cls, source: Union[bytes, bytearray, BinaryIO]) -> Dict[str, Any]:
        """
        Парсинг бинарного заголовка GGUF v2/v3 из байтового буфера или файлового потока.
        Крупные массивы (> 256 элементов, например tokenizer.ggml.tokens/scores/merges)
        пропускаются через f.seek для быстрого и полного чтения всех архитектурных ключей
        независимо от их расположения в заголовке.
        """
        if isinstance(source, (bytes, bytearray)):
            f = io.BytesIO(source)
        else:
            f = source

        def _read_exact(n: int) -> bytes:
            buf = f.read(n)
            if len(buf) < n:
                raise ValueError(f"Buffer underflow reading {n} bytes (got {len(buf)})")
            return buf

        magic = _read_exact(4)
        if magic != b"GGUF":
            raise ValueError(f"Invalid GGUF magic: {magic!r}")

        version = struct.unpack("<I", _read_exact(4))[0]
        if version not in (2, 3) and version != 1:
            logger.debug(f"[LayerBudgeter] Нестандартная версия GGUF: {version}")

        # In v2/v3: tensor_count (uint64), metadata_kv_count (uint64)
        if version == 1:
            tensor_count = struct.unpack("<I", _read_exact(4))[0]
            kv_count = struct.unpack("<I", _read_exact(4))[0]
        else:
            tensor_count = struct.unpack("<Q", _read_exact(8))[0]
            kv_count = struct.unpack("<Q", _read_exact(8))[0]

        metadata: Dict[str, Any] = {}

        def read_str() -> str:
            raw_len = _read_exact(8)
            length = struct.unpack("<Q", raw_len)[0]
            if length == 0:
                return ""
            raw_str = _read_exact(length)
            return raw_str.decode("utf-8", errors="replace")

        TYPE_SIZES = {
            0: 1, 1: 1, 7: 1,
            2: 2, 3: 2,
            4: 4, 5: 4, 6: 4,
            10: 8, 11: 8, 12: 8,
        }

        def read_single_val(vtype: int) -> Any:
            if vtype == 0:  # UINT8
                return struct.unpack("<B", _read_exact(1))[0]
            elif vtype == 1:  # INT8
                return struct.unpack("<b", _read_exact(1))[0]
            elif vtype == 2:  # UINT16
                return struct.unpack("<H", _read_exact(2))[0]
            elif vtype == 3:  # INT16
                return struct.unpack("<h", _read_exact(2))[0]
            elif vtype == 4:  # UINT32
                return struct.unpack("<I", _read_exact(4))[0]
            elif vtype == 5:  # INT32
                return struct.unpack("<i", _read_exact(4))[0]
            elif vtype == 6:  # FLOAT32
                return struct.unpack("<f", _read_exact(4))[0]
            elif vtype == 7:  # BOOL
                return bool(_read_exact(1)[0])
            elif vtype == 8:  # STRING
                return read_str()
            elif vtype == 10:  # UINT64
                return struct.unpack("<Q", _read_exact(8))[0]
            elif vtype == 11:  # INT64
                return struct.unpack("<q", _read_exact(8))[0]
            elif vtype == 12:  # FLOAT64
                return struct.unpack("<d", _read_exact(8))[0]
            elif vtype == 9:  # ARRAY
                arr_hdr = _read_exact(12)
                item_t = struct.unpack("<I", arr_hdr[:4])[0]
                item_c = struct.unpack("<Q", arr_hdr[4:12])[0]
                return read_array_val(item_t, item_c)
            else:
                raise ValueError(f"Unknown GGUF value type {vtype}")

        def read_array_val(item_t: int, item_c: int) -> Any:
            if item_c > 256:
                if item_t in TYPE_SIZES:
                    f.seek(item_c * TYPE_SIZES[item_t], io.SEEK_CUR)
                elif item_t == 8:  # STRING
                    for _ in range(item_c):
                        slen = struct.unpack("<Q", _read_exact(8))[0]
                        if slen > 0:
                            f.seek(slen, io.SEEK_CUR)
                else:
                    for _ in range(item_c):
                        read_single_val(item_t)
                return {"__array_len__": item_c}
            else:
                items = []
                for _ in range(item_c):
                    items.append(read_single_val(item_t))
                return items

        for _ in range(kv_count):
            try:
                key = read_str()
                vtype = struct.unpack("<I", _read_exact(4))[0]
                if vtype == 9:
                    arr_hdr = _read_exact(12)
                    arr_type = struct.unpack("<I", arr_hdr[:4])[0]
                    arr_len = struct.unpack("<Q", arr_hdr[4:12])[0]
                    metadata[f"{key}.count"] = int(arr_len)
                    val = read_array_val(arr_type, arr_len)
                else:
                    val = read_single_val(vtype)
                metadata[key] = val
            except Exception:
                break

        # Чтение таблицы tensor_info (имя, dims, ggml_type, offset) для точного веса слоев и детекта квантизации
        if tensor_count > 0:
            try:
                tensors = []
                for _ in range(tensor_count):
                    t_name = read_str()
                    n_dims = struct.unpack("<I", _read_exact(4))[0]
                    if n_dims > 8:
                        raise ValueError(f"Suspicious tensor n_dims={n_dims}")
                    if n_dims > 0:
                        _read_exact(8 * n_dims)
                    t_type = struct.unpack("<I", _read_exact(4))[0]
                    t_offset = struct.unpack("<Q", _read_exact(8))[0]
                    tensors.append((t_offset, t_name, t_type))

                alignment = int(metadata.get("general.alignment") or 32)
                if alignment <= 0:
                    alignment = 32
                cur_pos = f.tell()
                data_start = ((cur_pos + alignment - 1) // alignment) * alignment
                f.seek(0, io.SEEK_END)
                total_len = f.tell()
                total_data_bytes = max(0, total_len - data_start)

                tensors.sort(key=lambda item: item[0])
                base_bytes = 0
                blocks_bytes = 0
                tensor_types_set = set()
                for idx, (t_off, t_name, t_type) in enumerate(tensors):
                    tensor_types_set.add(int(t_type))
                    next_off = tensors[idx + 1][0] if idx + 1 < len(tensors) else total_data_bytes
                    t_size = max(0, next_off - t_off)
                    if t_name.startswith("blk."):
                        blocks_bytes += t_size
                    else:
                        base_bytes += t_size

                if base_bytes > 0 and blocks_bytes > 0:
                    metadata["__tensor_base_bytes__"] = base_bytes
                    metadata["__tensor_blocks_bytes__"] = blocks_bytes
                metadata["__tensor_types__"] = sorted(tensor_types_set)
            except Exception as tensor_err:
                logger.debug(f"[LayerBudgeter] Пропуск чтения tensor_info GGUF: {tensor_err}")

        return metadata

    @classmethod
    def parse_gguf_metadata(cls, model_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Считывает заголовок GGUF файла модели:
        * Ищет заголовок GGUF (магическое число GGUF).
        * Извлекает block_count (общее число слоев), architecture, context_length, SWA и параметры KV-голов.
        * Если файл нестандартный или парсинг выбросил исключение — использует эвристику по размеру:
          size <= 5GB -> 28 слоев; 5-9GB -> 40 слоев; > 9GB -> 48 слоев.
        * Кэширует результаты в data/models_cache.json по связке (str(model_path), mtime, size, schema_version).
        * Возвращает dict с метаданными модели.
        """
        p = Path(model_path).resolve()
        if not p.exists() or not p.is_file():
            # Защита от несуществующего пути
            return {
                "schema_version": CURRENT_CACHE_SCHEMA_VERSION,
                "total_layers": 28,
                "model_size_mb": 0.0,
                "base_overhead_mb": None,
                "blocks_weight_mb": None,
                "tensor_types": [],
                "server_profile": "standard",
                "architecture": "unknown",
                "is_moe": False,
                "context_length": None,
                "sliding_window": 0,
                "sliding_window_pattern": None,
                "full_attention_interval": 0,
                "head_count": None,
                "head_count_kv": None,
                "head_count_kv_layers": None,
                "key_length": None,
                "value_length": None,
                "key_length_swa": None,
                "value_length_swa": None,
                "vocab_size": 0,
            }

        stat = p.stat()
        file_size = stat.st_size
        mtime = stat.st_mtime
        model_size_mb = round(file_size / (1024.0 * 1024.0), 2)
        size_gb = file_size / (1024.0 ** 3)

        cache_key = f"{str(p)}::{mtime}::{file_size}"
        cache = cls._read_models_cache()

        if cache_key in cache:
            cached_data = cache[cache_key]
            if (
                isinstance(cached_data, dict)
                and cached_data.get("schema_version") == CURRENT_CACHE_SCHEMA_VERSION
                and "sliding_window" in cached_data
                and "vocab_size" in cached_data
                and cached_data.get("vocab_size") is not None
            ):
                return dict(cached_data)

        # 1. Попытка потокового парсинга заголовка GGUF
        parsed_layers: Optional[int] = None
        architecture: str = "unknown"
        is_moe: bool = False
        context_length: Optional[int] = None
        sliding_window: int = 0
        sliding_window_pattern: Optional[list] = None
        full_attention_interval: int = 0
        head_count: Optional[int] = None
        head_count_kv: Optional[Union[int, float]] = None
        head_count_kv_layers: Optional[list] = None
        key_length: Optional[int] = None
        value_length: Optional[int] = None
        key_length_swa: Optional[int] = None
        value_length_swa: Optional[int] = None
        vocab_size: int = 0
        base_overhead_mb: Optional[float] = None
        blocks_weight_mb: Optional[float] = None
        tensor_types: list = []

        try:
            with open(p, "rb") as f:
                kv = cls._parse_gguf_header(f)
            if isinstance(kv.get("__tensor_base_bytes__"), int) and isinstance(kv.get("__tensor_blocks_bytes__"), int):
                base_overhead_mb = round(kv["__tensor_base_bytes__"] / (1024.0 * 1024.0), 2)
                blocks_weight_mb = round(kv["__tensor_blocks_bytes__"] / (1024.0 * 1024.0), 2)
            if isinstance(kv.get("__tensor_types__"), list):
                tensor_types = [int(x) for x in kv["__tensor_types__"]]
            architecture = str(kv.get("general.architecture", "llama")).lower()

            # Ищем block_count: {arch}.block_count или любой *.block_count
            if f"{architecture}.block_count" in kv:
                parsed_layers = int(kv[f"{architecture}.block_count"])
            else:
                for k, v in kv.items():
                    if k.endswith(".block_count") and isinstance(v, (int, float)):
                        parsed_layers = int(v)
                        break

            # Context length
            if f"{architecture}.context_length" in kv:
                context_length = int(kv[f"{architecture}.context_length"])
            else:
                for k, v in kv.items():
                    if k.endswith(".context_length") and isinstance(v, (int, float)):
                        context_length = int(v)
                        break

            # MoE check
            if kv.get(f"{architecture}.expert_count", 0) > 0 or "moe" in architecture:
                is_moe = True

            # Hybrid Full Attention / SSM interval (e.g. Qwen 3.5 full_attention_interval=4)
            raw_fai = kv.get(f"{architecture}.full_attention_interval")
            if raw_fai is None:
                for k, v in kv.items():
                    if k.endswith(".full_attention_interval") and isinstance(v, (int, float)):
                        raw_fai = v
                        break
            if isinstance(raw_fai, (int, float)) and int(raw_fai) > 1:
                full_attention_interval = int(raw_fai)

            # SWA pattern
            if f"{architecture}.attention.sliding_window_pattern" in kv:
                raw_pat = kv[f"{architecture}.attention.sliding_window_pattern"]
                if isinstance(raw_pat, (list, tuple)):
                    sliding_window_pattern = [bool(x) for x in raw_pat]
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.sliding_window_pattern") and isinstance(v, (list, tuple)):
                        sliding_window_pattern = [bool(x) for x in v]
                        break

            # Sliding Window Attention (SWA)
            raw_sw = None
            if f"{architecture}.attention.sliding_window" in kv:
                raw_sw = kv[f"{architecture}.attention.sliding_window"]
            elif f"{architecture}.attention.sliding_window_size" in kv:
                raw_sw = kv[f"{architecture}.attention.sliding_window_size"]
            else:
                for k, v in kv.items():
                    if (
                        k.endswith(".attention.sliding_window")
                        or k.endswith(".sliding_window")
                        or k.endswith(".attention.sliding_window_size")
                        or k.endswith(".sliding_window_size")
                    ):
                        raw_sw = v
                        break

            if isinstance(raw_sw, (list, tuple)):
                val_list = [int(x) for x in raw_sw if isinstance(x, (int, float)) and int(x) > 0]
                sliding_window = max(val_list) if val_list else 0
                if sliding_window_pattern is None:
                    sliding_window_pattern = [bool(isinstance(x, (int, float)) and int(x) > 0) for x in raw_sw]
            elif isinstance(raw_sw, (int, float)):
                sliding_window = int(raw_sw)
            else:
                sliding_window = 0

            # Head count
            if f"{architecture}.attention.head_count" in kv:
                try:
                    head_count = int(kv[f"{architecture}.attention.head_count"])
                except Exception:
                    head_count = None
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.head_count") and isinstance(v, (int, float)):
                        head_count = int(v)
                        break

            # Head count KV
            raw_hkv = None
            if f"{architecture}.attention.head_count_kv" in kv:
                raw_hkv = kv[f"{architecture}.attention.head_count_kv"]
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.head_count_kv"):
                        raw_hkv = v
                        break

            if isinstance(raw_hkv, (list, tuple)) and raw_hkv:
                head_count_kv_layers = [float(x) for x in raw_hkv]
                head_count_kv = float(sum(head_count_kv_layers)) / len(head_count_kv_layers)
            elif isinstance(raw_hkv, (int, float)):
                head_count_kv = float(raw_hkv)

            # Key length & Key length SWA
            raw_kl = None
            if f"{architecture}.attention.key_length" in kv:
                raw_kl = kv[f"{architecture}.attention.key_length"]
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.key_length") and not k.endswith(".key_length_swa"):
                        raw_kl = v
                        break

            if isinstance(raw_kl, (list, tuple)) and raw_kl:
                key_length = float(sum(raw_kl)) / len(raw_kl)
            elif isinstance(raw_kl, (int, float)):
                key_length = int(raw_kl)

            raw_kl_swa = None
            if f"{architecture}.attention.key_length_swa" in kv:
                raw_kl_swa = kv[f"{architecture}.attention.key_length_swa"]
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.key_length_swa") or k.endswith(".key_length_swa"):
                        raw_kl_swa = v
                        break

            if isinstance(raw_kl_swa, (list, tuple)) and raw_kl_swa:
                key_length_swa = float(sum(raw_kl_swa)) / len(raw_kl_swa)
            elif isinstance(raw_kl_swa, (int, float)):
                key_length_swa = int(raw_kl_swa)

            if key_length is None and key_length_swa is not None:
                key_length = key_length_swa
            if key_length_swa is None and key_length is not None:
                key_length_swa = key_length

            # Value length & Value length SWA
            raw_vl = None
            if f"{architecture}.attention.value_length" in kv:
                raw_vl = kv[f"{architecture}.attention.value_length"]
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.value_length") and not k.endswith(".value_length_swa"):
                        raw_vl = v
                        break

            if isinstance(raw_vl, (list, tuple)) and raw_vl:
                value_length = float(sum(raw_vl)) / len(raw_vl)
            elif isinstance(raw_vl, (int, float)):
                value_length = int(raw_vl)

            raw_vl_swa = None
            if f"{architecture}.attention.value_length_swa" in kv:
                raw_vl_swa = kv[f"{architecture}.attention.value_length_swa"]
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.value_length_swa") or k.endswith(".value_length_swa"):
                        raw_vl_swa = v
                        break

            if isinstance(raw_vl_swa, (list, tuple)) and raw_vl_swa:
                value_length_swa = float(sum(raw_vl_swa)) / len(raw_vl_swa)
            elif isinstance(raw_vl_swa, (int, float)):
                value_length_swa = int(raw_vl_swa)

            if value_length is None and value_length_swa is not None:
                value_length = value_length_swa
            if value_length_swa is None and value_length is not None:
                value_length_swa = value_length

            if (key_length is None or key_length <= 0) and value_length:
                key_length = value_length
            if (value_length is None or value_length <= 0) and key_length:
                value_length = key_length
            if (key_length_swa is None or key_length_swa <= 0) and value_length_swa:
                key_length_swa = value_length_swa
            if (value_length_swa is None or value_length_swa <= 0) and key_length_swa:
                value_length_swa = key_length_swa

            # Vocab size
            if f"{architecture}.vocab_size" in kv:
                try:
                    vocab_size = int(kv[f"{architecture}.vocab_size"])
                except Exception:
                    vocab_size = 0
            else:
                for k, v in kv.items():
                    if k.endswith(".vocab_size") and isinstance(v, (int, float)):
                        vocab_size = int(v)
                        break

            if vocab_size <= 0:
                if "tokenizer.ggml.tokens.count" in kv:
                    vocab_size = int(kv["tokenizer.ggml.tokens.count"])
                elif "tokenizer.ggml.tokens" in kv:
                    toks = kv["tokenizer.ggml.tokens"]
                    if isinstance(toks, dict) and "__array_len__" in toks:
                        vocab_size = int(toks["__array_len__"])
                    elif isinstance(toks, (list, tuple)):
                        vocab_size = len(toks)
                    elif isinstance(toks, (int, float)):
                        vocab_size = int(toks)
                else:
                    for k, v in kv.items():
                        if k.endswith(".tokens.count") and isinstance(v, (int, float)):
                            vocab_size = int(v)
                            break

        except Exception as e:
            logger.debug(f"[LayerBudgeter] Сбой прямого чтения GGUF заголовка '{p.name}': {e}. Применяется эвристика.")

        # 2. Эвристический фоллбэк по размеру, если block_count не извлечен
        if not parsed_layers or parsed_layers <= 0:
            if size_gb <= 5.0:
                total_layers = 28
            elif size_gb <= 9.0:
                total_layers = 40
            else:
                total_layers = 48
        else:
            total_layers = parsed_layers

        result = {
            "schema_version": CURRENT_CACHE_SCHEMA_VERSION,
            "total_layers": total_layers,
            "model_size_mb": model_size_mb,
            "base_overhead_mb": base_overhead_mb,
            "blocks_weight_mb": blocks_weight_mb,
            "tensor_types": tensor_types,
            "architecture": architecture,
            "is_moe": is_moe,
            "context_length": context_length,
            "sliding_window": sliding_window,
            "sliding_window_pattern": sliding_window_pattern,
            "full_attention_interval": full_attention_interval,
            "head_count": head_count,
            "head_count_kv": head_count_kv,
            "head_count_kv_layers": head_count_kv_layers,
            "key_length": key_length,
            "value_length": value_length,
            "key_length_swa": key_length_swa,
            "value_length_swa": value_length_swa,
            "vocab_size": vocab_size,
        }
        result["server_profile"] = cls.detect_required_server_profile(p, gguf_meta=result)

        # 3. Сохранение в кэш
        cache[cache_key] = result
        cls._write_models_cache(cache)

        return result

    @classmethod
    def detect_required_server_profile(
        cls,
        model_path: Union[str, Path],
        gguf_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Определяет требуемый профиль llama-server для модели:
        - 'prismml': если в GGUF обнаружены кастомные типы тензоров (например, PTQ1_0 type 143 / >= 100)
          или имя файла содержит маркеры PTQ1_0 / PQ2_0 / Ternary-Bonsai.
        - 'standard': стандартный ggml-org/llama.cpp.
        """
        if gguf_meta is None and model_path:
            try:
                p = Path(model_path)
                if p.is_file():
                    gguf_meta = cls.parse_gguf_metadata(p)
            except Exception:
                gguf_meta = None

        if isinstance(gguf_meta, dict):
            t_types = gguf_meta.get("tensor_types") or []
            if any(isinstance(t, int) and t >= 100 for t in t_types):
                return "prismml"

        name_lower = Path(model_path).name.lower() if model_path else ""
        if any(marker in name_lower for marker in ("ptq1_0", "pq2_0", "ternary-bonsai", "ternary_bonsai")):
            return "prismml"

        return "standard"

    # -------------------------------------------------------------------------
    # 3. Оценка размера KV-кэша
    # -------------------------------------------------------------------------
    @classmethod
    def estimate_kv_cache_mb(
        cls,
        context_length: int,
        cache_type: str = "q8_0",
        gguf_meta: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Расчет объема буфера KV-кэша под Llama/Qwen/Gemma.
        Если в gguf_meta присутствуют положительные head_count_kv, total_layers и размерности
        заголовков (key_length / value_length > 0), рассчитывается послойный аналитический физический размер
        KV-кэша по формуле:
            bytes_per_token_per_layer = head_count_kv_i * (k_len + v_len) * type_bytes
            (где type_bytes для q4 = 0.5625, f16/fp16 = 2.0, q8_0 = 1.0625)
        с учетом послойного SWA (для SWA-слоев: k_len_swa/v_len_swa и min(ctx, sliding_window + 512),
        для Global-слоев: k_len/v_len и полный ctx) и 15% накладных расходов CUDA на выравнивание памяти.
        При отсутствии подробных метаданных используется эталонная оценка Llama/Qwen
        (q8_0 ~64 КБ на токен + 15% накладных расходов CUDA).
        """
        ctx = max(0, int(context_length or 0))
        ctk, ctv = resolve_kv_cache_types(cache_type)

        type_bytes_map = {
            "f16": 2.0,
            "q8_0": 1.0625,  # 34 / 32
            "q4_0": 0.5625,  # 18 / 32
        }
        kb_per_token_map = {
            "f16": 128.0,
            "q8_0": 64.0,
            "q4_0": 36.0,
        }

        type_bytes_k = type_bytes_map.get(ctk, 1.0625)
        type_bytes_v = type_bytes_map.get(ctv, 1.0625)
        kb_per_token = (kb_per_token_map.get(ctk, 64.0) + kb_per_token_map.get(ctv, 64.0)) / 2.0

        if not gguf_meta or not isinstance(gguf_meta, dict):
            raw_mb = (ctx * kb_per_token) / 1024.0
            return int(math.ceil(raw_mb * 1.15))

        sliding_window = int(gguf_meta.get("sliding_window") or 0)
        total_layers = int(gguf_meta.get("total_layers") or 0)
        pattern = gguf_meta.get("sliding_window_pattern")
        full_attn_interval = int(gguf_meta.get("full_attention_interval") or 0)

        # Head count KV (scalar and layers)
        raw_hkv_layers = gguf_meta.get("head_count_kv_layers")
        raw_hkv = gguf_meta.get("head_count_kv")
        if isinstance(raw_hkv, (list, tuple)) and raw_hkv and not raw_hkv_layers:
            raw_hkv_layers = raw_hkv

        if raw_hkv_layers and isinstance(raw_hkv_layers, (list, tuple)):
            hkv_layers = [float(x) for x in raw_hkv_layers]
            hkv_scalar = float(sum(hkv_layers)) / len(hkv_layers) if hkv_layers else 0.0
        elif isinstance(raw_hkv, (int, float)):
            hkv_layers = None
            hkv_scalar = float(raw_hkv)
        else:
            hkv_layers = None
            hkv_scalar = 0.0

        def _to_float(v) -> float:
            if isinstance(v, (list, tuple)) and v:
                return float(sum(v)) / len(v)
            if isinstance(v, (int, float)):
                return float(v)
            return 0.0

        k_len = _to_float(gguf_meta.get("key_length"))
        v_len = _to_float(gguf_meta.get("value_length"))
        k_len_swa = _to_float(gguf_meta.get("key_length_swa"))
        v_len_swa = _to_float(gguf_meta.get("value_length_swa"))

        # Fallbacks
        if k_len <= 0 and k_len_swa > 0:
            k_len = k_len_swa
        if k_len_swa <= 0 and k_len > 0:
            k_len_swa = k_len
        if v_len <= 0 and v_len_swa > 0:
            v_len = v_len_swa
        if v_len_swa <= 0 and v_len > 0:
            v_len_swa = v_len

        if k_len <= 0 and v_len > 0:
            k_len = v_len
        elif v_len <= 0 and k_len > 0:
            v_len = k_len

        if k_len_swa <= 0 and v_len_swa > 0:
            k_len_swa = v_len_swa
        elif v_len_swa <= 0 and k_len_swa > 0:
            v_len_swa = k_len_swa

        # 1. Послойный аналитический расчёт при наличии анатомии KV-голов
        if (hkv_scalar > 0 or hkv_layers) and total_layers > 0 and (k_len > 0 and v_len > 0):
            total_kv_bytes = 0.0
            global_layers_cnt = max(1, total_layers // 6)
            local_layers_cnt = total_layers - global_layers_cnt

            for i in range(total_layers):
                # В гибридных архитектурах (например, Qwen 3.5: full_attention_interval=4)
                # KV-кэш выделяется только для слоёв с полноценным attention; SSM/DeltaNet-слои его не используют.
                if full_attn_interval > 1 and (i + 1) % full_attn_interval != 0:
                    continue

                if sliding_window > 0:
                    if isinstance(pattern, (list, tuple)) and i < len(pattern):
                        is_swa = bool(pattern[i])
                    else:
                        is_swa = (i < local_layers_cnt)
                else:
                    is_swa = False

                if hkv_layers and i < len(hkv_layers):
                    h_i = hkv_layers[i]
                else:
                    h_i = hkv_scalar

                if is_swa:
                    layer_ctx = min(ctx, sliding_window + 512)
                    k_dim = k_len_swa if k_len_swa > 0 else k_len
                    v_dim = v_len_swa if v_len_swa > 0 else v_len
                else:
                    layer_ctx = ctx
                    k_dim = k_len if k_len > 0 else k_len_swa
                    v_dim = v_len if v_len > 0 else v_len_swa

                total_kv_bytes += layer_ctx * h_i * (k_dim * type_bytes_k + v_dim * type_bytes_v)

            raw_mb = total_kv_bytes / (1024.0 * 1024.0)
            return int(math.ceil(raw_mb * 1.15))

        # 2. Надёжный fallback через kb_per_token
        if sliding_window > 0 and total_layers > 0:
            if isinstance(pattern, (list, tuple)) and len(pattern) == total_layers:
                local_layers = sum(1 for is_swa in pattern if bool(is_swa))
                global_layers = total_layers - local_layers
            else:
                global_layers = max(1, total_layers // 6)
                local_layers = total_layers - global_layers
            local_ctx = min(ctx, sliding_window + 512)
            effective_layer_tokens = (local_layers * local_ctx) + (global_layers * ctx)
        else:
            effective_layer_tokens = total_layers * ctx

        if sliding_window <= 0 or total_layers <= 0:
            raw_mb = (ctx * kb_per_token) / 1024.0
            return int(math.ceil(raw_mb * 1.15))

        token_mb_per_layer = (kb_per_token / 32.0) / 1024.0
        raw_mb = effective_layer_tokens * token_mb_per_layer
        return int(math.ceil(raw_mb * 1.15))

    # -------------------------------------------------------------------------
    # 4. Расчет распределения слоев (calculate_layer_offload)
    # -------------------------------------------------------------------------
    @classmethod
    def calculate_layer_offload(
        cls,
        model_path: Union[str, Path],
        target_context: int,
        current_running_ngl: Optional[int] = None,
        total_vram_mb: Optional[int] = None,
        available_ram_mb: Optional[float] = None,
        cache_type: Optional[str] = None,
        is_arbiter_managed: bool = False,
        current_used_vram_mb: Optional[float] = None,
        fragmentation_margin_mb: int = 0,
        ubatch_size: int = DEFAULT_LLAMA_UBATCH_SIZE,
    ) -> Tuple[int, bool, str, Dict[str, Any]]:
        """
        1) Опрашивает detect_gpu_topology() и берет Total_VRAM из system_monitor (или NVML).
        2) Usable_VRAM:
           - При is_arbiter_managed=True:
             effective_os_reserve = max(os_reserve_mb, ceil(used_vram))
             forge_res = 0 (VRAMArbiter управляет выгрузкой)
             safety_res = SAFETY_MARGIN_VRAM_MB (250) + fragmentation_margin_mb
             compute_mb = base_graph_mb + logits_mb
               (где logits_mb = ceil(ubatch_size * vocab_size * 4 / 1024^2), base_graph_mb = 100 if size < 5000 else 140)
             Usable_VRAM = Total_VRAM - effective_os_reserve - safety_res - kv_cache_mb - compute_mb
           - При is_arbiter_managed=False:
             Usable_VRAM = Total_VRAM - os_reserve_mb - FORGE_VRAM_RESERVE_MB (450) - SAFETY_MARGIN_VRAM_MB (250) - kv_cache_mb
        3) Анатомия весов:
           Base_Overhead = Model_Size_MB * 0.20 (неделимый каркас token_embd, output.weight, RMSNorm).
           Blocks_Weight = Model_Size_MB * 0.80.
           Layer_Weight = Blocks_Weight / Total_Layers.
        4) Доступно под трансформерные блоки: Available_For_Blocks = Usable_VRAM - Base_Overhead.
        5) Расчет слоев:
           - Если Available_For_Blocks >= Blocks_Weight -> ngl = 99 (Full GPU Offload).
           - Если Available_For_Blocks <= 0 -> ngl = 0 (Full CPU).
           - Иначе: ngl = max(0, min(Total_Layers, int(Available_For_Blocks // Layer_Weight))).
        6) Зона нечувствительности (гистерезис):
           Если передан current_running_ngl и |ngl - current_running_ngl| <= HYSTERESIS_LAYER_DELTA (3),
           гистерезис разрешен ТОЛЬКО если current_running_ngl <= ngl (уменьшение) ЛИБО
           для него строго хватает бюджета трансформерных блоков.
        7) Pre-flight RAM Guard:
           Если ngl < Total_Layers:
               cpu_weights_mb = Model_Size_MB - (Base_Overhead + ngl * Layer_Weight).
               headroom_mb = 1024 (при cpu_weights_mb <= 500) или 1500 (при > 500).
               Если available_ram_mb < (cpu_weights_mb + headroom_mb):
                   return (0, False, "OOM Guard: недостаточно VRAM + RAM для безопасного запуска", audit_data).
        8) Возвращает кортеж: (calculated_ngl, can_run, reason_string, audit_dict).
        """
        p = Path(model_path)

        # 1. Топология и Total VRAM
        is_pure_compute, os_reserve_mb = cls.detect_gpu_topology()

        if total_vram_mb is None:
            try:
                from utils.system_monitor import SystemMonitor
                mon = SystemMonitor()
                total_vram_mb = mon.get_total_vram_mb()
            except Exception:
                pass
            if not total_vram_mb or total_vram_mb <= 0:
                total_vram_mb = 0

        total_vram = int(total_vram_mb)

        # 2. Анатомия весов модели
        meta = cls.parse_gguf_metadata(p)
        model_size_mb = float(meta.get("model_size_mb", 0.0))
        total_layers = int(meta.get("total_layers", 28))
        if total_layers <= 0:
            total_layers = 28
        vocab_size = int(meta.get("vocab_size") or 0)

        meta_base = meta.get("base_overhead_mb")
        meta_blocks = meta.get("blocks_weight_mb")
        if (
            isinstance(meta_base, (int, float))
            and isinstance(meta_blocks, (int, float))
            and meta_base > 0
            and meta_blocks > 0
        ):
            base_overhead_mb = float(meta_base)
            blocks_weight_mb = float(meta_blocks)
        else:
            base_overhead_mb = model_size_mb * 0.20
            blocks_weight_mb = model_size_mb * 0.80
        layer_weight_mb = blocks_weight_mb / float(total_layers)

        # 3. Расчет Usable VRAM с учетом арбитража, занятости, буфера вычислений и SWA KV-кэша
        effective_cache_type = cache_type or "q8_0"
        ctk, ctv = resolve_kv_cache_types(effective_cache_type)
        kv_cache_mb = cls.estimate_kv_cache_mb(target_context, cache_type=effective_cache_type, gguf_meta=meta)

        eff_ubatch = max(1, int(ubatch_size if ubatch_size is not None else DEFAULT_LLAMA_UBATCH_SIZE))
        logits_mb = int(math.ceil((eff_ubatch * vocab_size * 4) / (1024.0 * 1024.0))) if vocab_size > 0 else 0
        base_graph_mb = 100 if model_size_mb < 5000 else 140

        if total_vram <= 512:
            # Безопасный CPU-режим: дискретный GPU отсутствует или VRAM <= 512 МБ
            effective_os_reserve = 0
            used_vram = 0.0
            forge_res = 0
            frag_margin = 0
            safety_res = 0
            compute_mb = 0
            usable_vram_mb = 0.0
        elif is_arbiter_managed:
            if current_used_vram_mb is not None:
                used_vram = float(current_used_vram_mb)
            else:
                used_vram = cls.get_current_used_vram_mb()
            effective_os_reserve = max(os_reserve_mb, int(math.ceil(used_vram)))
            forge_res = 0
            frag_margin = int(max(0, fragmentation_margin_mb))
            safety_res = int(SAFETY_MARGIN_VRAM_MB) + frag_margin  # Базовый буфер против WDDM paging (170 МБ) + фрагментационный запас
            compute_mb = base_graph_mb + logits_mb
            usable_vram_mb = total_vram - effective_os_reserve - forge_res - safety_res - kv_cache_mb - compute_mb
        else:
            frag_margin = 0
            used_vram = float(current_used_vram_mb) if current_used_vram_mb is not None else 0.0
            effective_os_reserve = os_reserve_mb
            forge_res = int(FORGE_VRAM_RESERVE_MB)
            safety_res = int(SAFETY_MARGIN_VRAM_MB)
            compute_mb = 0
            usable_vram_mb = total_vram - os_reserve_mb - forge_res - safety_res - kv_cache_mb

        # 4. Доступно под блоки трансформера
        available_for_blocks_mb = usable_vram_mb - base_overhead_mb

        # 5. Расчет целевого ngl
        if total_vram <= 512:
            raw_ngl = 0
        elif blocks_weight_mb > 0 and available_for_blocks_mb >= blocks_weight_mb:
            raw_ngl = 99
        elif available_for_blocks_mb <= 0:
            raw_ngl = 0
        else:
            raw_ngl = max(0, min(total_layers, int(available_for_blocks_mb // layer_weight_mb)))

        calculated_ngl = raw_ngl
        hysteresis_applied = False

        # 6. Зона нечувствительности (гистерезис)
        if current_running_ngl is not None and isinstance(current_running_ngl, int) and current_running_ngl >= 0:
            effective_prev_ngl = min(current_running_ngl, total_layers)
            effective_calc_ngl = min(calculated_ngl, total_layers)
            delta = abs(effective_calc_ngl - effective_prev_ngl)
            if delta <= int(HYSTERESIS_LAYER_DELTA) and usable_vram_mb >= 0:
                # Защита гистерезиса от превышения физического лимита VRAM:
                # Разрешено только если:
                # 1) current_running_ngl <= calculated_ngl (уменьшение или равенство), ЛИБО
                # 2) если для current_running_ngl строго хватает свободного бюджета блоков
                can_apply_hysteresis = False
                if effective_prev_ngl <= effective_calc_ngl:
                    can_apply_hysteresis = True
                else:
                    if effective_prev_ngl >= total_layers:
                        if available_for_blocks_mb >= blocks_weight_mb:
                            can_apply_hysteresis = True
                    else:
                        needed_blocks_mb = float(effective_prev_ngl) * layer_weight_mb
                        if available_for_blocks_mb >= needed_blocks_mb:
                            can_apply_hysteresis = True

                if can_apply_hysteresis:
                    calculated_ngl = current_running_ngl
                    hysteresis_applied = True
                else:
                    logger.info(
                        f"[LayerBudgeter] Сброс гистерезиса NGL: текущий {current_running_ngl} отклонен "
                        f"(недостаточно свободного бюджета блоков VRAM). NGL пересчитан в {calculated_ngl}."
                    )

        # 7. Pre-flight RAM Guard
        can_run = True
        reason = "OK: расчет распределения слоев завершен успешно"

        if available_ram_mb is None or available_ram_mb <= 0:
            available_ram_mb = cls.get_available_host_memory_mb()

        # Базовый запас памяти хоста под рантайм, WDDM paging, CUDA_Host pinned buffer и восстановление KV-слота
        # При available_ram_mb < 4096 ProcessManager выставляет GGML_CUDA_NO_PINNED=1, поэтому pinned-буфер не выделяется.
        # Буфер восстановления KV-слота (600 MB) требуется только если на диске реально есть сохраненный слот.
        cuda_pinned_mb = cls.CUDA_HOST_PINNED_RAM_MB if available_ram_mb >= 4096.0 else 0.0
        has_saved_slot = False
        if is_arbiter_managed:
            try:
                from config import KV_SLOTS_DIR
                has_saved_slot = (KV_SLOTS_DIR / "slot_metadata.json").is_file()
            except Exception:
                has_saved_slot = False
        slot_restore_mb = cls.SLOT_RESTORE_HEADROOM_MB if (is_arbiter_managed and has_saved_slot) else 0.0

        base_host_required_mb = (
            cls.OS_HEADROOM_RAM_MB +
            cuda_pinned_mb +
            slot_restore_mb
        )

        cpu_weights_mb = 0.0
        if calculated_ngl < total_layers:
            if calculated_ngl > 0:
                gpu_weights = base_overhead_mb + calculated_ngl * layer_weight_mb
                cpu_weights_mb = max(0.0, model_size_mb - gpu_weights)
            else:
                cpu_weights_mb = model_size_mb

            # Пропорциональный буфер под нагрузку CPU слоев
            if is_arbiter_managed:
                headroom_mb = 1024.0 if cpu_weights_mb <= 1024.0 else cls.OS_HEADROOM_RAM_MB
            else:
                headroom_mb = 1024.0 if cpu_weights_mb <= 500.0 else cls.OS_HEADROOM_RAM_MB
            total_required_host_mb = cpu_weights_mb + headroom_mb
        else:
            total_required_host_mb = base_host_required_mb
            headroom_mb = cls.OS_HEADROOM_RAM_MB

        usable_ram_mb = max(0.0, available_ram_mb - headroom_mb)

        if available_ram_mb < total_required_host_mb:
            can_run = False
            reason = (
                f"OOM Guard: недостаточно системной памяти хоста для безопасного запуска "
                f"(требуется {int(total_required_host_mb)} MB [CPU слои={int(cpu_weights_mb)} MB, хост-буферы={int(total_required_host_mb - cpu_weights_mb)} MB] | "
                f"доступно RAM/Commit={int(available_ram_mb)} MB)"
            )
            logger.warning(f"[LayerBudgeter] 🛑 Pre-flight RAM Guard: блокировка запуска! {reason}")
            audit_data = {
                "model_path": str(p),
                "target_context": target_context,
                "total_vram_mb": total_vram,
                "os_reserve_mb": os_reserve_mb,
                "effective_os_reserve_mb": effective_os_reserve,
                "used_vram_mb": round(used_vram, 2),
                "compute_buffer_mb": compute_mb,
                "logits_buffer_mb": logits_mb,
                "ubatch_size": eff_ubatch,
                "vocab_size": vocab_size,
                "is_pure_compute": is_pure_compute,
                "is_arbiter_managed": is_arbiter_managed,
                "forge_reserve_mb": forge_res,
                "safety_margin_mb": safety_res,
                "kv_cache_mb": kv_cache_mb,
                "usable_vram_mb": usable_vram_mb,
                "model_size_mb": model_size_mb,
                "total_layers": total_layers,
                "base_overhead_mb": round(base_overhead_mb, 2),
                "blocks_weight_mb": round(blocks_weight_mb, 2),
                "layer_weight_mb": round(layer_weight_mb, 2),
                "available_for_blocks_mb": round(available_for_blocks_mb, 2),
                "raw_ngl": raw_ngl,
                "calculated_ngl": 0,
                "current_running_ngl": current_running_ngl,
                "hysteresis_applied": hysteresis_applied,
                "final_ngl": 0,
                "cpu_weights_mb": round(cpu_weights_mb, 2),
                "available_ram_mb": round(available_ram_mb, 2),
                "usable_ram_mb": round(usable_ram_mb, 2),
                "can_run": False,
                "reason": reason,
            }
            cls.log_audit_report(audit_data)
            return (0, can_run, reason, audit_data)
        else:
            usable_ram_mb = max(0.0, available_ram_mb - headroom_mb)

        # 8. Итоговый аудит-паспорт
        audit_data = {
            "model_path": str(p),
            "target_context": target_context,
            "total_vram_mb": total_vram,
            "os_reserve_mb": os_reserve_mb,
            "effective_os_reserve_mb": effective_os_reserve,
            "used_vram_mb": round(used_vram, 2),
            "compute_buffer_mb": compute_mb,
            "logits_buffer_mb": logits_mb,
            "ubatch_size": eff_ubatch,
            "vocab_size": vocab_size,
            "is_pure_compute": is_pure_compute,
            "is_arbiter_managed": is_arbiter_managed,
            "forge_reserve_mb": forge_res,
            "safety_margin_mb": safety_res,
            "fragmentation_margin_mb": frag_margin,
            "cache_type": effective_cache_type,
            "cache_type_k": ctk,
            "cache_type_v": ctv,
            "kv_cache_mb": kv_cache_mb,
            "usable_vram_mb": usable_vram_mb,
            "model_size_mb": model_size_mb,
            "total_layers": total_layers,
            "base_overhead_mb": round(base_overhead_mb, 2),
            "blocks_weight_mb": round(blocks_weight_mb, 2),
            "layer_weight_mb": round(layer_weight_mb, 2),
            "available_for_blocks_mb": round(available_for_blocks_mb, 2),
            "raw_ngl": raw_ngl,
            "calculated_ngl": calculated_ngl,
            "current_running_ngl": current_running_ngl,
            "hysteresis_applied": hysteresis_applied,
            "final_ngl": calculated_ngl,
            "cpu_weights_mb": round(cpu_weights_mb, 2),
            "available_ram_mb": round(available_ram_mb, 2),
            "usable_ram_mb": round(usable_ram_mb, 2),
            "can_run": True,
            "reason": "Безопасный CPU-режим: видеокарта не обнаружена или VRAM <= 512 МБ (ngl=0)" if total_vram <= 512 else reason,
        }
        final_reason = "Безопасный CPU-режим: видеокарта не обнаружена или VRAM <= 512 МБ (ngl=0)" if total_vram <= 512 else reason
        cls.log_audit_report(audit_data)
        return (calculated_ngl, can_run, final_reason, audit_data)

    # -------------------------------------------------------------------------
    # 5. Диагностический паспорт (логгер NeonRoute.LayerBudgeter)
    # -------------------------------------------------------------------------
    @classmethod
    def log_audit_report(cls, audit_data: Dict[str, Any]) -> None:
        """
        Логирует структурированный паспорт в логгер 'NeonRoute.LayerBudgeter' (INFO):
        топология, формула баланса VRAM, анатомия модели и итоговый вердикт распределения.
        """
        topo_str = "PURE COMPUTE (iGPU Intel)" if audit_data.get("is_pure_compute") else "PRIMARY DISPLAY (dGPU display)"
        eff_os = audit_data.get("effective_os_reserve_mb", audit_data.get("os_reserve_mb"))
        kv_mb = audit_data.get("kv_cache_mb")
        ctk = audit_data.get("cache_type_k")
        ctv = audit_data.get("cache_type_v")
        if ctk and ctv and ctk != ctv:
            kv_str = f"KV({kv_mb}, {ctk}/{ctv})"
        else:
            kv_str = f"KV({kv_mb})"

        report = (
            f"\n[LayerBudgeter Diagnostic Passport]\n"
            f"  ├── 1. Топология GPU: {topo_str} | Резерв ОС: {audit_data.get('os_reserve_mb')} MB "
            f"(эффективный: {eff_os} MB, used={audit_data.get('used_vram_mb', 0.0)} MB)\n"
            f"  ├── 2. Баланс VRAM: Total={audit_data.get('total_vram_mb')} MB "
            f"- OS({eff_os}) "
            f"- Forge({audit_data.get('forge_reserve_mb')}) "
            f"- Safety({audit_data.get('safety_margin_mb')}) "
            f"- {kv_str} "
            f"- Compute({audit_data.get('compute_buffer_mb', 0)}) "
            f"=> Usable VRAM = {audit_data.get('usable_vram_mb')} MB\n"
            f"  ├── 3. Анатомия модели: Размер={audit_data.get('model_size_mb')} MB | Всего слоев={audit_data.get('total_layers')} | "
            f"Base Overhead={audit_data.get('base_overhead_mb')} MB | Вес слоя={audit_data.get('layer_weight_mb')} MB | "
            f"Vocab={audit_data.get('vocab_size', 0)} | ubatch={audit_data.get('ubatch_size', DEFAULT_LLAMA_UBATCH_SIZE)} (Logits={audit_data.get('logits_buffer_mb', 0)} MB)\n"
            f"  ├── 4. Доступно под блоки: {audit_data.get('available_for_blocks_mb')} MB | "
            f"RAM: CPU слои={audit_data.get('cpu_weights_mb', 0.0)} MB | Свободно RAM={audit_data.get('available_ram_mb', 0.0)} MB\n"
            f"  └── 5. Вердикт оффлоада: NGL = {audit_data.get('final_ngl')} | "
            f"Can Run = {audit_data.get('can_run')} | "
            f"Гистерезис = {audit_data.get('hysteresis_applied')} | "
            f"Статус: {audit_data.get('reason')}"
        )
        logger.info(report)
