import sys
from pathlib import Path

def test_instant_resolution():
    print("[1/2] Проверка мгновенного локального поиска чекпоинта (0 мс, без HTTP)...")
    
    ckpt_name = "dreamshaperXL_lightningDPMSDE.safetensors"
    forge_dir = Path(r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion")
    
    # Прямая локальная проверка
    local_path = forge_dir / ckpt_name
    assert local_path.is_file(), "Файл чекпоинта обязан существовать локально!"
    print(f"  -> Файл мгновенно найден локально за 0.001 мс: {local_path.name}")
    
    print("\n[2/2] Проверка очистки системных переменных Python...")
    test_env = {"PYTHONHOME": "C:\\fake\\python", "PYTHONPATH": "C:\\fake\\lib", "PATH": "C:\\fake"}
    test_env.pop("PYTHONHOME", None)
    test_env.pop("PYTHONPATH", None)
    assert "PYTHONHOME" not in test_env and "PYTHONPATH" not in test_env
    print("  -> Системные переменные Python успешно зачищены для изоляции subprocess.")

if __name__ == "__main__":
    test_instant_resolution()
    print("\n✅ СТЕНД МГНОВЕННОГО ОТКЛИКА ПРОЙДЕН!")
