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
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, Union

import psutil

from config import (
    DATA_DIR,
    VRAM_TOTAL_BUDGET_MB,
    FORGE_VRAM_RESERVE_MB,
    SAFETY_MARGIN_VRAM_MB,
    OS_RESERVE_PURE_COMPUTE_MB,
    OS_RESERVE_PRIMARY_DISPLAY_MB,
    HYSTERESIS_LAYER_DELTA,
)

logger = logging.getLogger("NeonRoute.LayerBudgeter")
MODELS_CACHE_PATH = DATA_DIR / "models_cache.json"


class LayerBudgeter:
    """
    Автономный сервис топологического аудита GPU, парсинга метаданных GGUF
    и расчета баланса слоев между GPU и RAM.
    """

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

    @classmethod
    def detect_hardware_kv_profile(cls) -> Dict[str, Any]:
        """
        Детектор архитектуры GPU для автотюна KV-кэша и Flash Attention.
        Чипы Turing+ (Compute Capability >= 7.5 или RTX) поддерживают скоростной q8_0 и Flash Attention.
        """
        try:
            import pynvml
            try:
                pynvml.nvmlInit()
            except Exception as init_err:
                if "already initialized" not in str(init_err).lower():
                    raise

            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            gpu_name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode("utf-8")
            gpu_name = str(gpu_name or "NVIDIA GPU")

            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
            compute_cap_val = float(major) + float(minor) / 10.0
            cap_str = f"{major}.{minor}"

            if compute_cap_val >= 7.5 or "RTX" in gpu_name.upper():
                return {
                    "supported": True,
                    "gpu_name": gpu_name,
                    "compute_cap": cap_str,
                    "recommended_kv": "q8_0",
                    "recommended_fa": True,
                }
            else:
                return {
                    "supported": False,
                    "gpu_name": gpu_name,
                    "compute_cap": cap_str if major else "N/A",
                    "recommended_kv": "f16",
                    "recommended_fa": False,
                }
        except Exception as e:
            logger.debug(f"[LayerBudgeter] detect_hardware_kv_profile fallback error: {e}")
            return {
                "supported": False,
                "gpu_name": "Unknown GPU",
                "compute_cap": "N/A",
                "recommended_kv": "f16",
                "recommended_fa": False,
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
        """
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
    def _parse_gguf_header(cls, buffer: bytes) -> Dict[str, Any]:
        """Парсинг бинарного заголовка GGUF v2/v3 из буфера (до 8 КБ)."""
        if len(buffer) < 24:
            raise ValueError("Buffer too small for GGUF header")
        if buffer[:4] != b"GGUF":
            raise ValueError(f"Invalid GGUF magic: {buffer[:4]!r}")

        version = struct.unpack("<I", buffer[4:8])[0]
        if version not in (2, 3):
            # Version 1 is deprecated, but check if supported
            if version != 1:
                logger.debug(f"[LayerBudgeter] Нестандартная версия GGUF: {version}")

        # In v2/v3: tensor_count (uint64), metadata_kv_count (uint64)
        if version == 1:
            tensor_count = struct.unpack("<I", buffer[8:12])[0]
            kv_count = struct.unpack("<I", buffer[12:16])[0]
            offset = 16
        else:
            tensor_count = struct.unpack("<Q", buffer[8:16])[0]
            kv_count = struct.unpack("<Q", buffer[16:24])[0]
            offset = 24

        metadata: Dict[str, Any] = {}

        def read_str(off: int) -> Tuple[str, int]:
            if off + 8 > len(buffer):
                raise ValueError("Buffer overflow reading str length")
            length = struct.unpack("<Q", buffer[off:off + 8])[0]
            off += 8
            if off + length > len(buffer):
                raise ValueError("Buffer overflow reading str bytes")
            s = buffer[off:off + length].decode("utf-8", errors="replace")
            return s, off + length

        def read_val(off: int, vtype: int) -> Tuple[Any, int]:
            if vtype == 0:  # UINT8
                if off + 1 > len(buffer): raise ValueError("Overflow UINT8")
                return struct.unpack("<B", buffer[off:off + 1])[0], off + 1
            elif vtype == 1:  # INT8
                if off + 1 > len(buffer): raise ValueError("Overflow INT8")
                return struct.unpack("<b", buffer[off:off + 1])[0], off + 1
            elif vtype == 2:  # UINT16
                if off + 2 > len(buffer): raise ValueError("Overflow UINT16")
                return struct.unpack("<H", buffer[off:off + 2])[0], off + 2
            elif vtype == 3:  # INT16
                if off + 2 > len(buffer): raise ValueError("Overflow INT16")
                return struct.unpack("<h", buffer[off:off + 2])[0], off + 2
            elif vtype == 4:  # UINT32
                if off + 4 > len(buffer): raise ValueError("Overflow UINT32")
                return struct.unpack("<I", buffer[off:off + 4])[0], off + 4
            elif vtype == 5:  # INT32
                if off + 4 > len(buffer): raise ValueError("Overflow INT32")
                return struct.unpack("<i", buffer[off:off + 4])[0], off + 4
            elif vtype == 6:  # FLOAT32
                if off + 4 > len(buffer): raise ValueError("Overflow FLOAT32")
                return struct.unpack("<f", buffer[off:off + 4])[0], off + 4
            elif vtype == 7:  # BOOL
                if off + 1 > len(buffer): raise ValueError("Overflow BOOL")
                return bool(buffer[off]), off + 1
            elif vtype == 8:  # STRING
                return read_str(off)
            elif vtype == 9:  # ARRAY
                if off + 12 > len(buffer): raise ValueError("Overflow ARRAY header")
                item_type = struct.unpack("<I", buffer[off:off + 4])[0]
                item_count = struct.unpack("<Q", buffer[off + 4:off + 12])[0]
                off += 12
                items = []
                try:
                    for _ in range(item_count):
                        v, off = read_val(off, item_type)
                        items.append(v)
                    return items, off
                except Exception:
                    # Массив выходит за пределы буфера (например 262k токенов)
                    return {"__array_len__": item_count, "__partial_items__": items}, len(buffer)
            elif vtype == 10:  # UINT64
                if off + 8 > len(buffer): raise ValueError("Overflow UINT64")
                return struct.unpack("<Q", buffer[off:off + 8])[0], off + 8
            elif vtype == 11:  # INT64
                if off + 8 > len(buffer): raise ValueError("Overflow INT64")
                return struct.unpack("<q", buffer[off:off + 8])[0], off + 8
            elif vtype == 12:  # FLOAT64
                if off + 8 > len(buffer): raise ValueError("Overflow FLOAT64")
                return struct.unpack("<d", buffer[off:off + 8])[0], off + 8
            else:
                raise ValueError(f"Unknown GGUF value type {vtype}")

        for _ in range(kv_count):
            if offset >= len(buffer):
                break
            try:
                key, offset = read_str(offset)
                if offset + 4 > len(buffer):
                    break
                vtype = struct.unpack("<I", buffer[offset:offset + 4])[0]
                offset += 4
                if vtype == 9 and offset + 12 <= len(buffer):
                    arr_len = struct.unpack("<Q", buffer[offset + 4:offset + 12])[0]
                    metadata[f"{key}.count"] = int(arr_len)
                val, offset = read_val(offset, vtype)
                metadata[key] = val
            except Exception:
                break

        return metadata

    @classmethod
    def parse_gguf_metadata(cls, model_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Считывает первые 8 КБ файла модели:
        * Ищет заголовок GGUF (магическое число GGUF).
        * Извлекает block_count (общее число слоев), architecture, context_length.
        * Если файл нестандартный или парсинг выбросил исключение — использует эвристику по размеру:
          size <= 5GB -> 28 слоев; 5-9GB -> 40 слоев; > 9GB -> 48 слоев.
        * Кэширует результаты в data/models_cache.json по связке (str(model_path), mtime, size).
        * Возвращает dict с полями: total_layers, model_size_mb, architecture, is_moe.
        """
        p = Path(model_path).resolve()
        if not p.exists() or not p.is_file():
            # Защита от несуществующего пути
            return {
                "total_layers": 28,
                "model_size_mb": 0.0,
                "architecture": "unknown",
                "is_moe": False,
                "context_length": None,
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
                and "sliding_window" in cached_data
                and "vocab_size" in cached_data
                and cached_data.get("vocab_size") is not None
            ):
                return dict(cached_data)

        # 1. Попытка парсинга заголовка GGUF (до 64 КБ)
        parsed_layers: Optional[int] = None
        architecture: str = "unknown"
        is_moe: bool = False
        context_length: Optional[int] = None
        sliding_window: int = 0
        sliding_window_pattern: Optional[list] = None
        head_count: Optional[int] = None
        head_count_kv: Optional[Union[int, list]] = None
        key_length: Optional[int] = None
        value_length: Optional[int] = None
        vocab_size: int = 0

        try:
            with open(p, "rb") as f:
                head_buffer = f.read(65536)

            kv = cls._parse_gguf_header(head_buffer)
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

            # Sliding Window Attention (SWA)
            if f"{architecture}.attention.sliding_window" in kv:
                try:
                    sliding_window = int(kv[f"{architecture}.attention.sliding_window"])
                except Exception:
                    sliding_window = 0
            else:
                for k, v in kv.items():
                    if (k.endswith(".attention.sliding_window") or k.endswith(".sliding_window")) and isinstance(v, (int, float)):
                        sliding_window = int(v)
                        break

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
            if f"{architecture}.attention.head_count_kv" in kv:
                raw_hkv = kv[f"{architecture}.attention.head_count_kv"]
                if isinstance(raw_hkv, (list, tuple)):
                    sliding_window_pattern_hkv = [int(x) for x in raw_hkv]
                    head_count_kv = sliding_window_pattern_hkv
                elif isinstance(raw_hkv, (int, float)):
                    head_count_kv = int(raw_hkv)
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.head_count_kv"):
                        if isinstance(v, (list, tuple)):
                            head_count_kv = [int(x) for x in v]
                            break
                        elif isinstance(v, (int, float)):
                            head_count_kv = int(v)
                            break

            # Key length / Value length
            if f"{architecture}.attention.key_length" in kv:
                try:
                    key_length = int(kv[f"{architecture}.attention.key_length"])
                except Exception:
                    key_length = None
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.key_length") and isinstance(v, (int, float)):
                        key_length = int(v)
                        break

            if f"{architecture}.attention.value_length" in kv:
                try:
                    value_length = int(kv[f"{architecture}.attention.value_length"])
                except Exception:
                    value_length = None
            else:
                for k, v in kv.items():
                    if k.endswith(".attention.value_length") and isinstance(v, (int, float)):
                        value_length = int(v)
                        break

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
            "total_layers": total_layers,
            "model_size_mb": model_size_mb,
            "architecture": architecture,
            "is_moe": is_moe,
            "context_length": context_length,
            "sliding_window": sliding_window,
            "sliding_window_pattern": sliding_window_pattern,
            "head_count": head_count,
            "head_count_kv": head_count_kv,
            "key_length": key_length,
            "value_length": value_length,
            "vocab_size": vocab_size,
        }

        # 3. Сохранение в кэш
        cache[cache_key] = result
        cls._write_models_cache(cache)

        return result

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
        При отсутствии gguf_meta используется эталонная оценка Llama/Qwen (q8_0 ~64 КБ на токен + 15% накладных расходов CUDA).
        При наличии gguf_meta с sliding_window > 0 учитывается архитектура Sliding Window Attention (SWA),
        где локальные слои ограничены окном min(ctx, sliding_window + 512), а глобальные слои хранят полный контекст.
        """
        ctx = max(0, int(context_length or 0))
        kb_per_token = 64
        c_lower = str(cache_type or "").lower()
        if "q4" in c_lower:
            kb_per_token = 36
        elif "f16" in c_lower or "fp16" in c_lower:
            kb_per_token = 128

        if not gguf_meta or not isinstance(gguf_meta, dict):
            raw_mb = (ctx * kb_per_token) / 1024.0
            return int(math.ceil(raw_mb * 1.15))

        sliding_window = int(gguf_meta.get("sliding_window") or 0)
        total_layers = int(gguf_meta.get("total_layers") or 0)

        if sliding_window <= 0 or total_layers <= 0:
            raw_mb = (ctx * kb_per_token) / 1024.0
            return int(math.ceil(raw_mb * 1.15))

        # Определение числа локальных (SWA) и глобальных слоев
        pattern = gguf_meta.get("sliding_window_pattern")
        if isinstance(pattern, (list, tuple)) and len(pattern) == total_layers:
            local_layers = sum(1 for is_swa in pattern if bool(is_swa))
            global_layers = total_layers - local_layers
        else:
            global_layers = max(1, total_layers // 6)
            local_layers = total_layers - global_layers

        # Окно локального слоя с учетом физического ubatch=512
        local_ctx = min(ctx, sliding_window + 512)

        # Базовый kb_per_token откалиброван под 32 слоя полного контекста ctx
        token_mb_per_layer = (kb_per_token / 32.0) / 1024.0
        effective_layer_tokens = (local_layers * local_ctx) + (global_layers * ctx)
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
    ) -> Tuple[int, bool, str, Dict[str, Any]]:
        """
        1) Опрашивает detect_gpu_topology() и берет Total_VRAM из system_monitor (или NVML).
        2) Usable_VRAM:
           - При is_arbiter_managed=True:
             effective_os_reserve = max(os_reserve_mb, ceil(used_vram))
             forge_res = 0 (VRAMArbiter управляет выгрузкой)
             safety_res = 280 (150 МБ TDR + 130 МБ буфер против WDDM Shared Memory paging)
             compute_mb = (150 if model_size_mb < 5000 else 220) + (60 if vocab_size >= 200000 else 0)
             Usable_VRAM = Total_VRAM - effective_os_reserve - safety_res - kv_cache_mb - compute_mb
           - При is_arbiter_managed=False:
             Usable_VRAM = Total_VRAM - os_reserve_mb - FORGE_VRAM_RESERVE_MB (450) - SAFETY_MARGIN_VRAM_MB (150) - kv_cache_mb
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

        if total_vram_mb is None or total_vram_mb <= 0:
            try:
                from utils.system_monitor import SystemMonitor
                mon = SystemMonitor()
                total_vram_mb = mon.get_total_vram_mb()
            except Exception:
                pass
            if not total_vram_mb or total_vram_mb <= 0:
                total_vram_mb = int(VRAM_TOTAL_BUDGET_MB)

        total_vram = int(total_vram_mb)

        # 2. Анатомия весов модели
        meta = cls.parse_gguf_metadata(p)
        model_size_mb = float(meta.get("model_size_mb", 0.0))
        total_layers = int(meta.get("total_layers", 28))
        if total_layers <= 0:
            total_layers = 28
        vocab_size = int(meta.get("vocab_size") or 0)

        base_overhead_mb = model_size_mb * 0.20
        blocks_weight_mb = model_size_mb * 0.80
        layer_weight_mb = blocks_weight_mb / float(total_layers)

        # 3. Расчет Usable VRAM с учетом арбитража, занятости, буфера вычислений и SWA KV-кэша
        effective_cache_type = cache_type or "q8_0"
        kv_cache_mb = cls.estimate_kv_cache_mb(target_context, cache_type=effective_cache_type, gguf_meta=meta)

        if is_arbiter_managed:
            if current_used_vram_mb is not None:
                used_vram = float(current_used_vram_mb)
            else:
                used_vram = cls.get_current_used_vram_mb()
            effective_os_reserve = max(os_reserve_mb, int(math.ceil(used_vram)))
            forge_res = 0
            frag_margin = int(max(0, fragmentation_margin_mb))
            safety_res = 280 + frag_margin  # 150 МБ TDR + 130 МБ буфер против WDDM Shared Memory paging + фрагментационный запас
            compute_mb = (150 if model_size_mb < 5000 else 220) + (60 if vocab_size >= 200000 else 0)
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
        if available_for_blocks_mb >= blocks_weight_mb:
            raw_ngl = 99
        elif available_for_blocks_mb <= 0:
            raw_ngl = 0
        else:
            raw_ngl = max(0, min(total_layers, int(available_for_blocks_mb // layer_weight_mb)))

        calculated_ngl = raw_ngl
        hysteresis_applied = False

        # 6. Зона нечувствительности (гистерезис)
        if current_running_ngl is not None and isinstance(current_running_ngl, int) and current_running_ngl >= 0:
            delta = abs(calculated_ngl - current_running_ngl)
            if delta <= int(HYSTERESIS_LAYER_DELTA) and usable_vram_mb >= 0:
                # Защита гистерезиса от превышения физического лимита VRAM:
                # Разрешено только если:
                # 1) current_running_ngl <= calculated_ngl (уменьшение или равенство), ЛИБО
                # 2) если для current_running_ngl строго хватает свободного бюджета блоков
                can_apply_hysteresis = False
                if current_running_ngl <= calculated_ngl:
                    can_apply_hysteresis = True
                else:
                    if current_running_ngl >= total_layers:
                        if available_for_blocks_mb >= blocks_weight_mb:
                            can_apply_hysteresis = True
                    else:
                        needed_blocks_mb = float(current_running_ngl) * layer_weight_mb
                        if available_for_blocks_mb >= needed_blocks_mb:
                            can_apply_hysteresis = True

                if can_apply_hysteresis:
                    calculated_ngl = current_running_ngl
                    hysteresis_applied = True

        # 7. Pre-flight RAM Guard
        can_run = True
        reason = "OK: расчет распределения слоев завершен успешно"

        if available_ram_mb is None or available_ram_mb <= 0:
            available_ram_mb = psutil.virtual_memory().available / (1024.0 * 1024.0)

        min_os_headroom_mb = 1500.0  # Минимальный буфер для дыхания Windows и Python

        cpu_weights_mb = 0.0
        if calculated_ngl < total_layers:
            if calculated_ngl > 0:
                gpu_weights = base_overhead_mb + calculated_ngl * layer_weight_mb
                cpu_weights_mb = max(0.0, model_size_mb - gpu_weights)
            else:
                cpu_weights_mb = model_size_mb

            # Пропорциональный буфер: 1024 МБ для 1-3 слоев (<= 500 МБ) или 1500 МБ для тяжелого оффлоада
            headroom_mb = 1024.0 if cpu_weights_mb <= 500.0 else min_os_headroom_mb
            usable_ram_mb = max(0.0, available_ram_mb - headroom_mb)

            if available_ram_mb < (cpu_weights_mb + headroom_mb):
                can_run = False
                reason = "OOM Guard: недостаточно VRAM + RAM для безопасного запуска"
                audit_data = {
                    "model_path": str(p),
                    "target_context": target_context,
                    "total_vram_mb": total_vram,
                    "os_reserve_mb": os_reserve_mb,
                    "effective_os_reserve_mb": effective_os_reserve,
                    "used_vram_mb": round(used_vram, 2),
                    "compute_buffer_mb": compute_mb,
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
                    "usable_ram_mb": round(usable_ram_mb, 2),
                    "can_run": False,
                    "reason": reason,
                }
                cls.log_audit_report(audit_data)
                return (0, can_run, reason, audit_data)
        else:
            usable_ram_mb = max(0.0, available_ram_mb - min_os_headroom_mb)

        # 8. Итоговый аудит-паспорт
        audit_data = {
            "model_path": str(p),
            "target_context": target_context,
            "total_vram_mb": total_vram,
            "os_reserve_mb": os_reserve_mb,
            "effective_os_reserve_mb": effective_os_reserve,
            "used_vram_mb": round(used_vram, 2),
            "compute_buffer_mb": compute_mb,
            "vocab_size": vocab_size,
            "is_pure_compute": is_pure_compute,
            "is_arbiter_managed": is_arbiter_managed,
            "forge_reserve_mb": forge_res,
            "safety_margin_mb": safety_res,
            "fragmentation_margin_mb": frag_margin,
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
            "usable_ram_mb": round(usable_ram_mb, 2),
            "can_run": True,
            "reason": reason,
        }
        cls.log_audit_report(audit_data)
        return (calculated_ngl, can_run, reason, audit_data)

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
        report = (
            f"\n[LayerBudgeter Diagnostic Passport]\n"
            f"  ├── 1. Топология GPU: {topo_str} | Резерв ОС: {audit_data.get('os_reserve_mb')} MB "
            f"(эффективный: {eff_os} MB, used={audit_data.get('used_vram_mb', 0.0)} MB)\n"
            f"  ├── 2. Баланс VRAM: Total={audit_data.get('total_vram_mb')} MB "
            f"- OS({eff_os}) "
            f"- Forge({audit_data.get('forge_reserve_mb')}) "
            f"- Safety({audit_data.get('safety_margin_mb')}) "
            f"- KV({audit_data.get('kv_cache_mb')}) "
            f"- Compute({audit_data.get('compute_buffer_mb', 0)}) "
            f"=> Usable VRAM = {audit_data.get('usable_vram_mb')} MB\n"
            f"  ├── 3. Анатомия модели: Размер={audit_data.get('model_size_mb')} MB | Всего слоев={audit_data.get('total_layers')} | "
            f"Base Overhead={audit_data.get('base_overhead_mb')} MB | Вес слоя={audit_data.get('layer_weight_mb')} MB | Vocab={audit_data.get('vocab_size', 0)}\n"
            f"  ├── 4. Доступно под блоки: {audit_data.get('available_for_blocks_mb')} MB\n"
            f"  └── 5. Вердикт оффлоада: NGL = {audit_data.get('final_ngl')} | "
            f"Can Run = {audit_data.get('can_run')} | "
            f"Гистерезис = {audit_data.get('hysteresis_applied')} | "
            f"Статус: {audit_data.get('reason')}"
        )
        logger.info(report)
