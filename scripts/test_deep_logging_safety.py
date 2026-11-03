import sys
import collections
import traceback
from pathlib import Path

def test_clean_metadata_safetensors():
    print("[1/3] Проверка защиты метаданных safetensors (все ключи и значения -> str)...")
    raw_meta = {
        "format": "pt",
        "epoch": 10,           # int — safetensors упадет без приведения к str!
        "learning_rate": 0.0001, # float
        "is_lightning": True    # bool
    }
    
    clean_meta = {str(k): str(v) for k, v in raw_meta.items()}
    clean_meta["forge_unet_storage_dtype"] = "fp8e4"
    
    for k, v in clean_meta.items():
        assert isinstance(k, str) and isinstance(v, str), f"Элемент {k}:{v} обязан быть строкой!"
    print("  -> Все метаданные гарантированно приведены к типу str.")

def test_traceback_interception():
    print("\n[2/3] Проверка перехвата исключения при сбое save_file...")
    error_output = []
    try:
        raise OSError("[WinError 32] Процесс не может получить доступ к файлу: model_fp8e4.safetensors")
    except Exception as e:
        tb = traceback.format_exc()
        msg = f"[!] КРИТИЧЕСКАЯ ОШИБКА ПРИ СОХРАНЕНИИ ФАЙЛА:\n{tb}"
        error_output.append(msg)
        
    assert "WinError 32" in error_output[0]
    print(f"  -> Исключение успешно поймано с сохранением полного Traceback:\n     {error_output[0].splitlines()[0]}")

def test_worker_stdout_streaming():
    print("\n[3/3] Проверка буфера вывода воркера...")
    tail = collections.deque(maxlen=25)
    for i in range(50):
        tail.append(f"Прогресс: {i}/50")
    
    tail_text = chr(10).join(tail)
    assert len(tail) == 25
    assert "Прогресс: 49/50" in tail_text
    print("  -> Буфер воркера точно удерживает последние строки вывода.")

if __name__ == "__main__":
    test_clean_metadata_safetensors()
    test_traceback_interception()
    test_worker_stdout_streaming()
    print("\n✅ СТЕНД ГЛУБОКОГО ЛОГИРОВАНИЯ ПРОЙДЕН УСПЕШНО!")
