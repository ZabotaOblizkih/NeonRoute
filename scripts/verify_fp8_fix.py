import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.models_manager_dialog import ModelsManagerDialog
from ui.models_manager.workers import FP8ConvertWorker

def test_live_classes():
    print("[Проверка 1/2] Проверка реального метода _normalize_checkpoint_stem в ModelsManagerDialog...")
    
    stem_fp16e4 = ModelsManagerDialog._normalize_checkpoint_stem("aamXLAnimeMix_v10_fp16e4.safetensors")
    stem_fp8e4 = ModelsManagerDialog._normalize_checkpoint_stem("aamXLAnimeMix_v10_fp8e4.safetensors")
    stem_rv = ModelsManagerDialog._normalize_checkpoint_stem("Realistic_Vision_V5.1_noVAE.safetensors")
    
    print(f"  - aamXL fp16e4: '{stem_fp16e4}'")
    print(f"  - aamXL fp8e4 : '{stem_fp8e4}'")
    print(f"  - Realistic   : '{stem_rv}'")
    
    assert stem_fp16e4 == "aamxlanimemix_v10", f"Ошибка: получен стэм '{stem_fp16e4}'"
    assert stem_fp16e4 == stem_fp8e4, "Стэмы fp16e4 и fp8e4 обязаны совпадать!"
    assert stem_fp16e4 != stem_rv, "Стэмы aamXL и Realistic Vision не должны совпадать!"
    print("  -> Нормализация в боевом классе работает идеально.")

    print("\n[Проверка 2/2] Проверка красивого имени в FP8ConvertWorker...")
    worker = FP8ConvertWorker(
        python_exe=sys.executable,
        script_path=Path("dummy_script.py"),
        input_checkpoint_path=Path("C:/models/aamXLAnimeMix_v10_fp16e4.safetensors")
    )
    print(f"  - Вход : '{worker.input_checkpoint_path.name}'")
    print(f"  - Выход: '{worker.dst_path.name}'")
    assert worker.dst_path.name == "aamXLAnimeMix_v10_fp8e4.safetensors", f"Ошибка в имени: '{worker.dst_path.name}'"
    print("  -> Задвоение суффиксов в воркере полностью ликвидировано.")

if __name__ == "__main__":
    test_live_classes()
    print("\n🎉 ВСЕ БОЕВЫЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
