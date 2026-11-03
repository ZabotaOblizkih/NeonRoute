import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FORGE_ROOT_DIR, FORGE_LAUNCHER_PATH

def test_forge_model_resolution():
    print("[1/2] Проверка корректности импорта путей Forge из config.py...")
    print(f"  - FORGE_ROOT_DIR      : {FORGE_ROOT_DIR}")
    print(f"  - FORGE_LAUNCHER_PATH : {FORGE_LAUNCHER_PATH}")
    assert FORGE_ROOT_DIR is not None
    
    print("\n[2/2] Проверка локального поиска чекпоинта через FORGE_ROOT_DIR...")
    candidate_dirs = [
        FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion",
        FORGE_ROOT_DIR / "webui" / "models" / "Stable-diffusion",
        FORGE_ROOT_DIR / "models" / "Stable-diffusion",
        Path("C:/neyroGPD/webui_forge_cu121_torch231/webui/models/Stable-diffusion")
    ]
    
    target_name = "dreamshaperXL_lightningDPMSDE.safetensors"
    found_path = None
    for d in candidate_dirs:
        if d and d.is_dir():
            target = d / target_name
            if target.is_file():
                found_path = target
                break
                
    assert found_path is not None, f"Чекпоинт {target_name} обязан быть найден!"
    print(f"  -> Файл мгновенно найден без сетевых вызовов: {found_path}")

if __name__ == "__main__":
    test_forge_model_resolution()
    print("\n✅ СТЕНД ПРОВЕРКИ ПУТЕЙ FORGE ПРОЙДЕН УСПЕШНО!")
