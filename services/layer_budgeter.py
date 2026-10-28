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
        Опрашивает pynvml. Замеряет базовую занятость VRAM чипа.
        Если занято < 250 МБ и nvmlDeviceGetDisplayActive == False:
            режим PURE COMPUTE (дисплей на iGPU Intel), возвращает (True, OS_RESERVE_PURE_COMPUTE_MB).
        Иначе:
            режим PRIMARY DISPLAY (дисплей или DWM на dGPU), возвращает (False, OS_RESERVE_PRIMARY_DISPLAY_MB).
        При ошибке NVML — безопасный фоллбэк на (False, 700).
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
            used_mb = mem_info.used / (1024.0 * 1024.0)

            display_active = False
            if hasattr(pynvml, "nvmlDeviceGetDisplayActive"):
                try:
                    display_active = bool(pynvml.nvmlDeviceGetDisplayActive(handle))
                except Exception:
                    display_active = False

            if used_mb < 250.0 and not display_active:
                logger.info(
                    f"[LayerBudgeter] Топология: PURE COMPUTE (VRAM used: {used_mb:.1f} MB, display_active={display_active}) -> резерв {OS_RESERVE_PURE_COMPUTE_MB} МБ"
                )
                return (True, int(OS_RESERVE_PURE_COMPUTE_MB))
            else:
                logger.info(
                    f"[LayerBudgeter] Топология: PRIMARY DISPLAY (VRAM used: {used_mb:.1f} MB, display_active={display_active}) -> резерв {OS_RESERVE_PRIMARY_DISPLAY_MB} МБ"
                )
                return (False, int(OS_RESERVE_PRIMARY_DISPLAY_MB))

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
                for _ in range(item_count):
                    v, off = read_val(off, item_type)
                    items.append(v)
                return items, off
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
            if isinstance(cached_data, dict):
                return dict(cached_data)

        # 1. Попытка парсинга заголовка GGUF (до 8 КБ)
        parsed_layers: Optional[int] = None
        architecture: str = "unknown"
        is_moe: bool = False
        context_length: Optional[int] = None

        try:
            with open(p, "rb") as f:
                head_buffer = f.read(8192)

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
        }

        # 3. Сохранение в кэш
        cache[cache_key] = result
        cls._write_models_cache(cache)

        return result

    # -------------------------------------------------------------------------
    # 3. Оценка размера KV-кэша
    # -------------------------------------------------------------------------
    @classmethod
    def estimate_kv_cache_mb(cls, context_length: int, cache_type: str = "q8_0") -> int:
        """
        Расчет объема буфера KV-кэша под Llama/Qwen (для q8_0 ~64 КБ на токен + 15% накладных расходов CUDA).
        """
        ctx = max(0, int(context_length or 0))
        kb_per_token = 64
        c_lower = str(cache_type or "").lower()
        if "q4" in c_lower:
            kb_per_token = 36
        elif "f16" in c_lower or "fp16" in c_lower:
            kb_per_token = 128

        raw_mb = (ctx * kb_per_token) / 1024.0
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
    ) -> Tuple[int, bool, str, Dict[str, Any]]:
        """
        1) Опрашивает detect_gpu_topology() и берет Total_VRAM из system_monitor (или NVML).
        2) Usable_VRAM = Total_VRAM - OS_Reserve - FORGE_VRAM_RESERVE_MB (450) - SAFETY_MARGIN_VRAM_MB (150) - KV_Cache_MB.
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
           и буфер контекста вмещает target_context -> оставить current_running_ngl.
        7) Pre-flight RAM Guard:
           Если ngl < Total_Layers:
               cpu_weights_mb = Model_Size_MB - (Base_Overhead + ngl * Layer_Weight).
               usable_ram = psutil.virtual_memory().available / (1024 * 1024) - 3000 (ОС) - 3500 (Forge RAM).
               Если cpu_weights_mb > usable_ram:
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

        # 2. Расчет Usable VRAM
        effective_cache_type = cache_type or "q8_0"
        kv_cache_mb = cls.estimate_kv_cache_mb(target_context, cache_type=effective_cache_type)
        forge_res = int(FORGE_VRAM_RESERVE_MB)
        safety_res = int(SAFETY_MARGIN_VRAM_MB)
        usable_vram_mb = total_vram - os_reserve_mb - forge_res - safety_res - kv_cache_mb

        # 3. Анатомия весов модели
        meta = cls.parse_gguf_metadata(p)
        model_size_mb = float(meta.get("model_size_mb", 0.0))
        total_layers = int(meta.get("total_layers", 28))
        if total_layers <= 0:
            total_layers = 28

        base_overhead_mb = model_size_mb * 0.20
        blocks_weight_mb = model_size_mb * 0.80
        layer_weight_mb = blocks_weight_mb / float(total_layers)

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
            # Если разница не превышает гистерезис и VRAM не находится в критическом дефиците
            if delta <= int(HYSTERESIS_LAYER_DELTA) and usable_vram_mb >= 0:
                calculated_ngl = current_running_ngl
                hysteresis_applied = True

        # 7. Pre-flight RAM Guard
        can_run = True
        reason = "OK: расчет распределения слоев завершен успешно"

        if available_ram_mb is None or available_ram_mb <= 0:
            available_ram_mb = psutil.virtual_memory().available / (1024.0 * 1024.0)

        usable_ram_mb = available_ram_mb - 3000.0 - 3500.0

        cpu_weights_mb = 0.0
        if calculated_ngl < total_layers:
            if calculated_ngl > 0:
                gpu_weights = base_overhead_mb + calculated_ngl * layer_weight_mb
                cpu_weights_mb = max(0.0, model_size_mb - gpu_weights)
            else:
                cpu_weights_mb = model_size_mb

            if cpu_weights_mb > usable_ram_mb:
                can_run = False
                reason = "OOM Guard: недостаточно VRAM + RAM для безопасного запуска"
                audit_data = {
                    "model_path": str(p),
                    "target_context": target_context,
                    "total_vram_mb": total_vram,
                    "os_reserve_mb": os_reserve_mb,
                    "is_pure_compute": is_pure_compute,
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
                return (0, False, reason, audit_data)

        # 8. Итоговый аудит-паспорт
        audit_data = {
            "model_path": str(p),
            "target_context": target_context,
            "total_vram_mb": total_vram,
            "os_reserve_mb": os_reserve_mb,
            "is_pure_compute": is_pure_compute,
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
        return (calculated_ngl, True, reason, audit_data)

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
        report = (
            f"\n[LayerBudgeter Diagnostic Passport]\n"
            f"  ├── 1. Топология GPU: {topo_str} | Резерв ОС: {audit_data.get('os_reserve_mb')} MB\n"
            f"  ├── 2. Баланс VRAM: Total={audit_data.get('total_vram_mb')} MB "
            f"- OS({audit_data.get('os_reserve_mb')}) "
            f"- Forge({audit_data.get('forge_reserve_mb')}) "
            f"- Safety({audit_data.get('safety_margin_mb')}) "
            f"- KV({audit_data.get('kv_cache_mb')}) "
            f"=> Usable VRAM = {audit_data.get('usable_vram_mb')} MB\n"
            f"  ├── 3. Анатомия модели: Размер={audit_data.get('model_size_mb')} MB | Всего слоев={audit_data.get('total_layers')} | "
            f"Base Overhead={audit_data.get('base_overhead_mb')} MB | Вес слоя={audit_data.get('layer_weight_mb')} MB\n"
            f"  ├── 4. Доступно под блоки: {audit_data.get('available_for_blocks_mb')} MB\n"
            f"  └── 5. Вердикт оффлоада: NGL = {audit_data.get('final_ngl')} | "
            f"Can Run = {audit_data.get('can_run')} | "
            f"Гистерезис = {audit_data.get('hysteresis_applied')} | "
            f"Статус: {audit_data.get('reason')}"
        )
        logger.info(report)
