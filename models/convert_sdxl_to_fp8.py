import os
import sys
import time
from pathlib import Path
import torch
from safetensors import safe_open
from safetensors.torch import save_file

if len(sys.argv) > 1:
    SRC_PATH = sys.argv[1]
    if len(sys.argv) > 2:
        DST_PATH = sys.argv[2]
    else:
        src_p = Path(SRC_PATH)
        DST_PATH = str(src_p.with_name(f"{src_p.stem}_fp8e4{src_p.suffix}"))
else:
    SRC_PATH = r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion\mopMixtureOfPerverts_instaV10DMD.safetensors"
    DST_PATH = r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion\mopMixtureOfPerverts_instaV10DMD_fp8e4.safetensors"

print(f"[*] Открытие исходного чекпоинта: {SRC_PATH}", flush=True)
t0 = time.time()

tensors = {}
with safe_open(SRC_PATH, framework="pt", device="cpu") as f:
    metadata = f.metadata() or {}
    keys = list(f.keys())
    total = len(keys)
    print(f"[*] Загружено ключей: {total}. Начало конвертации UNet в FP8 (e4m3fn)...", flush=True)

    for idx, k in enumerate(keys):
        t = f.get_tensor(k)
        # Квантуем строго веса UNet, пропуская VAE и CLIP
        if "model.diffusion_model." in k and t.dtype in (torch.float16, torch.float32):
            t = t.to(torch.float8_e4m3fn)
        else:
            if t.dtype == torch.float32:
                t = t.to(torch.float16)
        tensors[k] = t

        if idx % 300 == 0 or idx == total - 1:
            print(f"    Прогресс: {idx + 1}/{total} слоев обработано...", flush=True)

print(f"[*] Сохранение оптимизированного файла: {DST_PATH}", flush=True)
metadata["forge_unet_storage_dtype"] = "fp8e4"
save_file(tensors, DST_PATH, metadata=metadata)

orig_size = os.path.getsize(SRC_PATH) / (1024**3)
new_size = os.path.getsize(DST_PATH) / (1024**3)
print(f"[+] Готово за {time.time() - t0:.1f}с! Размер: {orig_size:.2f} GB -> {new_size:.2f} GB", flush=True)
