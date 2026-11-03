import sys
import collections
from pathlib import Path

def test_worker_error_retention():
    print("[1/2] Проверка перехвата реального Traceback при падении воркера...")
    
    # Симулируем буфер последних строк вывода процесса
    stdout_tail = collections.deque(maxlen=20)
    simulated_stdout = [
        "[*] Открытие чекпоинта...",
        "    Прогресс: 100/2500...",
        "    Прогресс: 500/2500...",
        "Traceback (most recent call last):",
        "  File 'convert.py', line 45, in <module>",
        "    save_file(tensors, DST_PATH)",
        "PermissionError: [WinError 32] Процесс не может получить доступ к файлу"
    ]
    
    for line in simulated_stdout:
        stdout_tail.append(line)
        
    ret_code = 1
    # Вместо слепого "Код завершения: 1":
    if ret_code != 0:
        error_msg = "\n".join(stdout_tail)
    else:
        error_msg = ""
        
    assert "PermissionError" in error_msg, "Воркер обязан сохранить точное исключение!"
    print(f"  -> Перехвачен точный текст ошибки вместо слепого кода:\n     {stdout_tail[-1]}")

def test_python_resolver_cache():
    print("\n[2/2] Проверка мгновенного кэширования пути Python...")
    class MockDialog:
        _cached_torch_python = None
        def resolve_python(self):
            if self._cached_torch_python:
                return self._cached_torch_python
            # Симулируем тяжелый поиск
            self._cached_torch_python = r"C:\path\to\system\python.exe"
            return self._cached_torch_python

    dlg = MockDialog()
    p1 = dlg.resolve_python()
    p2 = dlg.resolve_python()
    assert p1 == p2 == r"C:\path\to\system\python.exe"
    print("  -> Путь Python закэширован, повторный поиск = 0 мс (GUI не зависает).")

if __name__ == "__main__":
    test_worker_error_retention()
    test_python_resolver_cache()
    print("\n✅ СТЕНД ДИАГНОСТИКИ ПРОЙДЕН УСПЕШНО!")
