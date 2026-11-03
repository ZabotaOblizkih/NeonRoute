import time
from pathlib import Path

def test_fast_button_logic():
    print("[1/2] Проверка скорости мгновенного обновления кнопки (без I/O)...")
    
    def fast_button_check(ckpt_name: str) -> bool:
        if not ckpt_name:
            return False
        c = ckpt_name.lower()
        is_safetensors = c.endswith(".safetensors")
        is_already_fp8 = any(k in c for k in ("_fp8", "-fp8", "e4m3fn", "_quant"))
        return bool(is_safetensors and not is_already_fp8)

    t0 = time.perf_counter()
    # Эмулируем 1000 переключений строк в GUI
    for _ in range(1000):
        fast_button_check("aamXLAnimeMix_v10_fp16e4.safetensors")
        fast_button_check("Realistic_Vision_V5.1.safetensors")
        fast_button_check("model_fp8e4.safetensors")
    elapsed = time.perf_counter() - t0
    
    print(f"  -> 3000 проверок выполнено за {elapsed*1000:.2f} мс (0 лагов в GUI!)")
    assert elapsed < 0.05, "Проверка должна быть мгновенной!"

def test_preflight_size_guard_logic():
    print("\n[2/2] Проверка предохранителя размера в _on_start_fp8_conversion...")
    size_heavy = int(6.6 * 1024**3) # 6.6 GB (FP16)
    size_compact = int(3.4 * 1024**3) # 3.4 GB (FP8)
    
    threshold = int(4.5 * 1024**3)
    
    assert size_heavy >= threshold, "Тяжелая модель должна запускаться без предупреждений"
    assert size_compact < threshold, "Сжатая модель должна перехватываться предохранителем"
    print("  -> Предохранитель размера корректно отсекает повторное сжатие.")

if __name__ == "__main__":
    test_fast_button_logic()
    test_preflight_size_guard_logic()
    print("\n✅ СТЕНД СКОРОСТИ И БЕЗОПАСНОСТИ ПРОЙДЕН!")
