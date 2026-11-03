import os
import sys
import subprocess
from pathlib import Path

def test_isolated_forge_run():
    print("[1/1] Проверка изолированного запуска Forge Python с очищенным PATH...")
    
    python_exe = r"C:\neyroGPD\webui_forge_cu121_torch231\system\python\python.exe"
    script_path = r"C:\NeonRoute\models\convert_sdxl_to_fp8.py"
    src = r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion\dreamshaperXL_lightningDPMSDE.safetensors"
    dst = r"C:\neyroGPD\webui_forge_cu121_torch231\webui\models\Stable-diffusion\dreamshaperXL_lightningDPMSDE_test_env.safetensors"
    
    # Формируем изолированный PATH для Forge
    py_dir = Path(python_exe).parent
    torch_lib = py_dir / "lib" / "site-packages" / "torch" / "lib"
    scripts_dir = py_dir / "Scripts"
    
    forge_env = dict(os.environ)
    forge_env["PYTHONIOENCODING"] = "utf-8"
    
    # Ставим родные DLL Forge на самый первый приоритет в PATH
    clean_path_parts = [str(torch_lib), str(py_dir), str(scripts_dir)]
    for p in os.environ.get("PATH", "").split(os.pathsep):
        # Исключаем пути NeonRoute и llama-server из поиска DLL
        if "neonroute" not in p.lower() and ".lmstudio" not in p.lower():
            clean_path_parts.append(p)
    forge_env["PATH"] = os.pathsep.join(clean_path_parts)
    
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = subprocess.CREATE_NO_WINDOW
        
    cmd = [python_exe, script_path, src, dst]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=forge_env,
        startupinfo=startupinfo,
        creationflags=creationflags
    )
    
    stdout, _ = proc.communicate()
    print(f"  -> Код завершения процесса: {proc.returncode}")
    assert proc.returncode == 0, f"Ошибка при изолированном запуске: {stdout}"
    
    if Path(dst).exists():
        Path(dst).unlink()
        print("  -> Сжатый файл успешно создан и удален после теста.")
    print("✓ Изоляция PATH гарантирует бессбойный запуск с кодом 0!")

if __name__ == "__main__":
    test_isolated_forge_run()
    print("\n✅ СТЕНД ИЗОЛЯЦИИ PATH ПРОЙДЕН УСПЕШНО!")
