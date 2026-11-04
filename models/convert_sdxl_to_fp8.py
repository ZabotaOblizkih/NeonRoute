import os
import sys
import time
import json
import struct
import math
from pathlib import Path
import numpy as np


def f32_to_fp8_e4m3fn_numpy(f32_arr: np.ndarray) -> np.ndarray:
    """
    Точная векторизованная конвертация IEEE-754 float32 -> FP8 E4M3FN (OFP8 / c10::Float8_e4m3fn),
    100% побитово идентичная `torch.Tensor.to(torch.float8_e4m3fn)` без загрузки тяжелого PyTorch.
    """
    u32 = f32_arr.astype(np.float32, copy=False).view(np.uint32)
    sign = ((u32 >> 31) & np.uint32(1)).astype(np.uint8) << 7
    abs_u32 = u32 & np.uint32(0x7FFFFFFF)

    out = np.zeros(u32.shape, dtype=np.uint8)

    # 1. Значения > 464.0f (0x43E80000) или Inf/NaN переходят в 0x7F (NaN в стандарте E4M3FN)
    mask_nan = abs_u32 > np.uint32(0x43E80000)
    out[mask_nan] = np.uint8(0x7F)

    # 2. Денормализованные числа: < 2^-6 (0x3C800000 в float32) с RNE-округлением
    mask_denorm = abs_u32 < np.uint32(0x3C800000)
    if np.any(mask_denorm):
        denorm_f32 = abs_u32[mask_denorm].view(np.float32) + np.float32(16384.0)
        out[mask_denorm] = (denorm_f32.view(np.uint32) - np.uint32(0x46800000)).astype(np.uint8)

    # 3. Нормализованные числа: [2^-6 (0x3C800000), 464.0f (0x43E80000)] с RNE-округлением
    mask_norm = (abs_u32 >= np.uint32(0x3C800000)) & (~mask_nan)
    if np.any(mask_norm):
        norm_u32 = abs_u32[mask_norm]
        mant_odd = (norm_u32 >> 20) & np.uint32(1)
        round_add = np.uint32(0x0007FFFF) + mant_odd
        rounded = norm_u32 + round_add
        out[mask_norm] = ((rounded - np.uint32(0x3C000000)) >> 20).astype(np.uint8)

    return out | sign


def build_fp8_luts():
    """Строит 64 КБ таблицы мгновенного перевода для всех 65 536 значений F16 и BF16."""
    u16 = np.arange(65536, dtype=np.uint16)
    f16_as_f32 = u16.view(np.float16).astype(np.float32)
    lut_f16 = f32_to_fp8_e4m3fn_numpy(f16_as_f32)

    bf16_as_u32 = u16.astype(np.uint32) << 16
    lut_bf16 = f32_to_fp8_e4m3fn_numpy(bf16_as_u32.view(np.float32))
    return lut_f16, lut_bf16


def main():
    if len(sys.argv) > 1:
        src_path = sys.argv[1]
        if len(sys.argv) > 2:
            dst_path = sys.argv[2]
        else:
            src_p = Path(src_path)
            dst_path = str(src_p.with_name(f"{src_p.stem}_fp8e4{src_p.suffix}"))
    else:
        src_path = r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion\mopMixtureOfPerverts_instaV10DMD.safetensors"
        dst_path = r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion\mopMixtureOfPerverts_instaV10DMD_fp8e4.safetensors"

    print(f"[*] Открытие исходного чекпоинта: {src_path}", flush=True)
    t0 = time.time()

    dtype_bytes_map = {
        "F8_E4M3": 1,
        "F16": 2,
        "BF16": 2,
        "F32": 4,
        "I64": 8,
        "I32": 4,
        "I16": 2,
        "I8": 1,
        "U8": 1,
        "BOOL": 1,
    }
    chunk_bytes = 8 * 1024 * 1024  # 8 МБ потоковый буфер (Zero-Mmap, < 45 МБ ОЗУ)

    try:
        lut_f16, lut_bf16 = build_fp8_luts()

        with open(src_path, "rb") as f_in:
            raw_len = f_in.read(8)
            if len(raw_len) < 8:
                raise ValueError("Исходный файл поврежден (размер менее 8 байт).")
            in_header_size = struct.unpack("<Q", raw_len)[0]
            in_header = json.loads(f_in.read(in_header_size).decode("utf-8"))
            in_data_base = 8 + in_header_size

            raw_meta = in_header.get("__metadata__", {}) or {}
            clean_meta = {str(k): str(v) for k, v in raw_meta.items()}
            clean_meta["forge_unet_storage_dtype"] = "fp8e4"

            keys = [k for k in in_header.keys() if k != "__metadata__"]
            total = len(keys)
            print(f"[*] Загружено ключей: {total}. Расчет смещений заголовка (Zero-RAM)...", flush=True)

            out_header = {"__metadata__": clean_meta}
            plan = []
            out_offset = 0

            for k in keys:
                info = in_header[k]
                orig_shape = info["shape"]
                orig_dtype = info["dtype"]
                in_start, in_end = info["data_offsets"]
                numel = math.prod(orig_shape) if orig_shape else 1

                if "model.diffusion_model." in k and orig_dtype in ("F16", "F32", "BF16"):
                    target_dtype = "F8_E4M3"
                elif orig_dtype == "F32":
                    target_dtype = "F16"
                else:
                    target_dtype = orig_dtype

                elem_size = dtype_bytes_map.get(target_dtype, 2)
                out_len = numel * elem_size

                out_header[k] = {
                    "dtype": target_dtype,
                    "shape": orig_shape,
                    "data_offsets": [out_offset, out_offset + out_len],
                }
                plan.append((k, orig_dtype, target_dtype, in_data_base + in_start, in_end - in_start))
                out_offset += out_len

            out_header_json = json.dumps(out_header, separators=(",", ":")).encode("utf-8")
            pad_len = (8 - (len(out_header_json) % 8)) % 8
            out_header_json += b" " * pad_len
            out_header_size = len(out_header_json)

            print(f"[*] Сохранение оптимизированного файла (потоковый стриминг): {dst_path}", flush=True)
            with open(dst_path, "wb", buffering=chunk_bytes) as f_out:
                f_out.write(struct.pack("<Q", out_header_size))
                f_out.write(out_header_json)

                for idx, (k, orig_dt, target_dt, file_pos, byte_len) in enumerate(plan):
                    f_in.seek(file_pos)
                    rem = byte_len

                    if orig_dt == target_dt:
                        while rem > 0:
                            chunk = f_in.read(min(rem, chunk_bytes))
                            if not chunk:
                                break
                            f_out.write(chunk)
                            rem -= len(chunk)
                    elif target_dt == "F8_E4M3":
                        if orig_dt == "F16":
                            while rem > 0:
                                chunk = f_in.read(min(rem, chunk_bytes))
                                if not chunk:
                                    break
                                u16_arr = np.frombuffer(chunk, dtype=np.uint16)
                                f_out.write(lut_f16[u16_arr].tobytes())
                                rem -= len(chunk)
                        elif orig_dt == "BF16":
                            while rem > 0:
                                chunk = f_in.read(min(rem, chunk_bytes))
                                if not chunk:
                                    break
                                u16_arr = np.frombuffer(chunk, dtype=np.uint16)
                                f_out.write(lut_bf16[u16_arr].tobytes())
                                rem -= len(chunk)
                        elif orig_dt == "F32":
                            while rem > 0:
                                chunk = f_in.read(min(rem, chunk_bytes))
                                if not chunk:
                                    break
                                f32_arr = np.frombuffer(chunk, dtype=np.float32)
                                f_out.write(f32_to_fp8_e4m3fn_numpy(f32_arr).tobytes())
                                rem -= len(chunk)
                    elif target_dt == "F16" and orig_dt == "F32":
                        while rem > 0:
                            chunk = f_in.read(min(rem, chunk_bytes))
                            if not chunk:
                                break
                            f32_arr = np.frombuffer(chunk, dtype=np.float32)
                            f_out.write(f32_arr.astype(np.float16).tobytes())
                            rem -= len(chunk)

                    if idx % 300 == 0 or idx == total - 1:
                        print(f"    Прогресс: {idx + 1}/{total} слоев обработано...", flush=True)

    except Exception:
        import traceback
        print("[!] КРИТИЧЕСКАЯ ОШИБКА ПРИ ПОТОКОВОМ СОХРАНЕНИИ ФАЙЛА:", flush=True)
        print(traceback.format_exc(), flush=True)
        if os.path.exists(dst_path):
            try:
                os.remove(dst_path)
            except Exception:
                pass
        sys.exit(1)

    orig_size = os.path.getsize(src_path) / (1024 ** 3)
    new_size = os.path.getsize(dst_path) / (1024 ** 3)
    print(f"[+] Готово за {time.time() - t0:.1f}с! Размер: {orig_size:.2f} GB -> {new_size:.2f} GB", flush=True)


if __name__ == "__main__":
    main()
