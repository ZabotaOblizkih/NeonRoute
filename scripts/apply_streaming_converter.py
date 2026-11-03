from pathlib import Path
import re

# 1. Переписываем models/convert_sdxl_to_fp8.py на Zero-RAM Streaming Writer
m_path = Path("models/convert_sdxl_to_fp8.py")

streaming_converter_code = """import os
import sys
import time
import json
import struct
import math
from pathlib import Path
import torch
from safetensors import safe_open

if len(sys.argv) > 1:
    SRC_PATH = sys.argv[1]
    if len(sys.argv) > 2:
        DST_PATH = sys.argv[2]
    else:
        src_p = Path(SRC_PATH)
        DST_PATH = str(src_p.with_name(f"{src_p.stem}_fp8e4{src_p.suffix}"))
else:
    SRC_PATH = r"C:\\neyroGPD\\webui_forge_cu121_torch231\\webui\\models\\Stable-diffusion\\mopMixtureOfPerverts_instaV10DMD.safetensors"
    DST_PATH = r"C:\\neyroGPD\\webui_forge_cu121_torch231\\webui\\models\\Stable-diffusion\\mopMixtureOfPerverts_instaV10DMD_fp8e4.safetensors"

print(f"[*] Открытие исходного чекпоинта: {SRC_PATH}", flush=True)
t0 = time.time()

dtype_bytes_map = {
    "F8_E4M3": 1,
    "F16": 2,
    "BF16": 2,
    "F32": 4,
    "I64": 8,
    "I32": 4,
    "BOOL": 1
}

# Этап 1. Быстрый аудит и расчет заголовка safetensors через get_slice (0 MB RAM)
with safe_open(SRC_PATH, framework="pt", device="cpu") as f_in:
    raw_meta = f_in.metadata() or {}
    clean_meta = {str(k): str(v) for k, v in raw_meta.items()}
    clean_meta["forge_unet_storage_dtype"] = "fp8e4"

    keys = list(f_in.keys())
    total = len(keys)
    print(f"[*] Загружено ключей: {total}. Расчет смещений заголовка (Zero-RAM)...", flush=True)

    header = {"__metadata__": clean_meta}
    target_plan = {}
    offset = 0

    for k in keys:
        s = f_in.get_slice(k)
        orig_shape = s.get_shape()
        orig_dtype = s.get_dtype()
        numel = math.prod(orig_shape) if orig_shape else 1

        if "model.diffusion_model." in k and orig_dtype in ("F16", "F32", "BF16"):
            target_dtype = "F8_E4M3"
        elif orig_dtype == "F32":
            target_dtype = "F16"
        else:
            target_dtype = orig_dtype

        elem_size = dtype_bytes_map.get(target_dtype, 2)
        t_len = numel * elem_size

        header[k] = {
            "dtype": target_dtype,
            "shape": orig_shape,
            "data_offsets": [offset, offset + t_len]
        }
        target_plan[k] = target_dtype
        offset += t_len

header_json = json.dumps(header, separators=(',', ':')).encode("utf-8")
pad_len = (8 - (len(header_json) % 8)) % 8
header_json += b" " * pad_len
header_size = len(header_json)

# Этап 2. Потоковая запись: пишем заголовок, затем конвертируем и пишем по одному тензору
print(f"[*] Сохранение оптимизированного файла (потоковый стриминг): {DST_PATH}", flush=True)
try:
    with open(DST_PATH, "wb") as f_out:
        f_out.write(struct.pack("<Q", header_size))
        f_out.write(header_json)

        with safe_open(SRC_PATH, framework="pt", device="cpu") as f_in:
            for idx, k in enumerate(keys):
                t = f_in.get_tensor(k)
                plan_dt = target_plan[k]

                if plan_dt == "F8_E4M3":
                    t = t.to(torch.float8_e4m3fn)
                elif plan_dt == "F16" and t.dtype != torch.float16:
                    t = t.to(torch.float16)

                raw_b = t.flatten().view(torch.uint8).numpy().tobytes()
                f_out.write(raw_b)
                del t

                if idx % 300 == 0 or idx == total - 1:
                    print(f"    Прогресс: {idx + 1}/{total} слоев обработано...", flush=True)
except Exception as e:
    import traceback
    print("[!] КРИТИЧЕСКАЯ ОШИБКА ПРИ ПОТОКОВОМ СОХРАНЕНИИ ФАЙЛА:", flush=True)
    print(traceback.format_exc(), flush=True)
    if os.path.exists(DST_PATH):
        try:
            os.remove(DST_PATH)
        except Exception:
            pass
    sys.exit(1)

orig_size = os.path.getsize(SRC_PATH) / (1024**3)
new_size = os.path.getsize(DST_PATH) / (1024**3)
print(f"[+] Готово за {time.time() - t0:.1f}с! Размер: {orig_size:.2f} GB -> {new_size:.2f} GB", flush=True)
"""

m_path.write_text(streaming_converter_code, encoding="utf-8")
print("✓ models/convert_sdxl_to_fp8.py успешно обновлен на Zero-RAM Streaming Writer")

# 2. Добавляем logger в ui/models_manager_dialog.py
d_path = Path("ui/models_manager_dialog.py")
d_text = d_path.read_text(encoding="utf-8")

if "logger = logging.getLogger" not in d_text:
    old_imp = "import logging"
    new_imp = "import logging\n\nlogger = logging.getLogger(\"NeonRoute.ModelsManagerDialog\")"
    d_text = d_text.replace(old_imp, new_imp, 1)
    d_path.write_text(d_text, encoding="utf-8")
    print("✓ ui/models_manager_dialog.py: logger успешно объявлен на уровне модуля")
else:
    print("! logger в models_manager_dialog.py уже объявлен")
