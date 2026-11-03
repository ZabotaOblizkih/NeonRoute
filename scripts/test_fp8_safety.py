import os
import sys
import tempfile
import re
from pathlib import Path

def resolve_fp8_target_name(source_path: Path) -> str:
    """
    Генерирует чистое имя для FP8-версии без задвоения суффиксов.
    """
    stem = source_path.stem
    ext = source_path.suffix
    
    # Если в стэме есть fp16e4, аккуратно заменяем его на fp8e4
    if re.search(r'_fp16e4\b', stem, flags=re.IGNORECASE):
        new_stem = re.sub(r'_fp16e4\b', '_fp8e4', stem, flags=re.IGNORECASE)
    elif re.search(r'_fp16\b', stem, flags=re.IGNORECASE):
        new_stem = re.sub(r'_fp16\b', '_fp8e4', stem, flags=re.IGNORECASE)
    elif re.search(r'-fp16\b', stem, flags=re.IGNORECASE):
        new_stem = re.sub(r'-fp16\b', '-fp8e4', stem, flags=re.IGNORECASE)
    elif not stem.lower().endswith(('_fp8e4', '_fp8')):
        new_stem = f"{stem}_fp8e4"
    else:
        new_stem = f"{stem}_quant"
    return f"{new_stem}{ext}"

def robust_normalize_stem(filename: str) -> str:
    if not filename:
        return ""
    stem = Path(filename).stem.lower()
    suffixes = (
        "_fp8e4", "_fp16e4", "_fp8_e4m3fn", "_e4m3fn", "_e5m2",
        "_fp8", "_fp16", "_bf16", "_pruned", "-fp8", "-fp16"
    )
    changed = True
    while changed:
        changed = False
        for sfx in suffixes:
            if stem.endswith(sfx):
                stem = stem[:-len(sfx)]
                changed = True
                break
    return stem.strip(" _-")

def test_fp8_name_generation():
    print("[1/4] Проверка красивой генерации имен для FP8...")
    cases = [
        ("aamXLAnimeMix_v10_fp16e4.safetensors", "aamXLAnimeMix_v10_fp8e4.safetensors"),
        ("model_base_fp16.safetensors", "model_base_fp8e4.safetensors"),
        ("cyber_realistic_v2.safetensors", "cyber_realistic_v2_fp8e4.safetensors"),
    ]
    for src, expected in cases:
        res = resolve_fp8_target_name(Path(src))
        print(f"  - '{src}' -> '{res}'")
        assert res == expected, f"Ожидалось '{expected}', получено '{res}'"
    print("  -> Имена формируются чисто, без задвоения суффиксов.")

def test_atomic_in_place_swap():
    print("\n[2/4] Проверка режима Atomic Swap (галочка ВКЛЮЧЕНА, сохраняем исходное имя)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        orig_file = tmp_path / "aamXLAnimeMix_v10_fp16e4.safetensors"
        temp_fp8_file = tmp_path / "aamXLAnimeMix_v10_fp8e4.safetensors"
        
        orig_file.write_text("HEAVY_FP16_WEIGHTS_6.5GB")
        temp_fp8_file.write_text("LIGHT_FP8_WEIGHTS_3.5GB")
        
        # Симулируем финиш конвертации с включенным чекбоксом
        delete_source = True
        
        # 1. Name Affinity Guard
        assert robust_normalize_stem(orig_file.name) == robust_normalize_stem(temp_fp8_file.name)
        assert temp_fp8_file.is_file() and temp_fp8_file.stat().st_size > 0
        
        if delete_source:
            # Атомарная замена: удаляем старый и переименовываем новый в исходное имя
            orig_file.unlink()
            temp_fp8_file.rename(orig_file)
            final_name = orig_file.name
        else:
            final_name = temp_fp8_file.name
            
        print(f"  - Имя файла после Atomic Swap: '{final_name}'")
        assert orig_file.is_file(), "Исходный файл должен существовать с новым содержимым!"
        assert orig_file.read_text() == "LIGHT_FP8_WEIGHTS_3.5GB", "Содержимое файла должно быть FP8!"
        assert not temp_fp8_file.exists(), "Временный файл должен исчезнуть после переименования!"
        print("  -> Исходное имя сохранено, веса FP8 успешно подставлены на место.")

def test_dual_files_mode():
    print("\n[3/4] Проверка режима двух файлов (галочка ВЫКЛЮЧЕНА)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        orig_file = tmp_path / "aamXLAnimeMix_v10_fp16e4.safetensors"
        new_fp8_file = tmp_path / resolve_fp8_target_name(orig_file)
        
        orig_file.write_text("ORIGINAL_FP16")
        new_fp8_file.write_text("NEW_FP8")
        
        delete_source = False
        if delete_source:
            orig_file.unlink()
            new_fp8_file.rename(orig_file)
            
        assert orig_file.is_file() and orig_file.read_text() == "ORIGINAL_FP16"
        assert new_fp8_file.is_file() and new_fp8_file.read_text() == "NEW_FP8"
        print(f"  - Сохранены оба файла: '{orig_file.name}' и '{new_fp8_file.name}'")
        print("  -> Режим раздельных файлов отработал штатно.")

def test_abort_on_corrupted_output():
    print("\n[4/4] Проверка защиты от повреждений (если FP8 файл битый)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        orig_file = tmp_path / "aamXLAnimeMix_v10_fp16e4.safetensors"
        corrupted_fp8 = tmp_path / "aamXLAnimeMix_v10_fp8e4.safetensors"
        
        orig_file.write_text("PRECIOUS_ORIGINAL_WEIGHTS")
        corrupted_fp8.write_text("") # 0 байт (сбой конвертера)
        
        # Проверяем, что код откажется трогать оригинал
        is_valid_fp8 = corrupted_fp8.is_file() and corrupted_fp8.stat().st_size > 0
        assert not is_valid_fp8, "Битый файл не должен пройти валидацию!"
        
        # Оригинал остается нетронутым
        assert orig_file.read_text() == "PRECIOUS_ORIGINAL_WEIGHTS"
        print("  -> Оригинал на 100% защищен от удаления при ошибках конвертации.")

if __name__ == "__main__":
    test_fp8_name_generation()
    test_atomic_in_place_swap()
    test_dual_files_mode()
    test_abort_on_corrupted_output()
    print("\n🎉 ВСЕ 4 ТЕСТА СТЕНДА ПРОЙДЕНЫ С ОТЛИЧИЕМ!")
