import sys
from pathlib import Path

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.engineer_context import EngineerContext
from services.engineer_diagnostics import diagnose_runtime_error

CASES = [
    ("interactive_loop", "Сделай простую игру марио с платформами, монетами и врагом", "pygame"),
    ("interactive_loop", "Сделай игру аркаду с кнопкой старта и паузой", "pygame"),
    ("interactive_loop", "Напиши змейку на tkinter", "tkinter"),
    ("interactive_loop", "Сделай калькулятор с историей вычислений", "tkinter"),
    ("interactive_loop", "Создай окно с формой ввода логина и пароля", "tkinter"),
    ("interactive_loop", "Сделай приложение для заметок на PyQt6", "tkinter"),
    ("batch_artifact", "Сделай смету расходов в Excel", "excel"),
    ("pure_logic_tdd", "Реализуй алгоритм быстрой сортировки", "module"),
]

def main():
    print("=" * 80)
    print("T3-6 MATRIX STAND: Format Normalization & Tkinter Diagnostics")
    print("=" * 80)
    fails = 0
    for raw_fmt, prompt, expected in CASES:
        actual = EngineerContext.normalize_artifact_format(raw_fmt, prompt)
        ok = (actual == expected)
        status = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"[{status}] ({raw_fmt}) -> got={actual!r}, expected={expected!r} | {prompt}")

    d_tk_space = diagnose_runtime_error("AttributeError: module 'tkinter' has no attribute 'SPACE'")
    d_tk_keys = diagnose_runtime_error("TypeError: 'method' object is not subscriptable")
    has_tk_hints = ("'<space>'" in d_tk_space) and ("pressed_keys" in d_tk_keys or "метод" in d_tk_keys.lower())
    print(f"\n[Diag Tkinter] hints_present={has_tk_hints}")
    if not has_tk_hints:
        fails += 1

    print("\n" + "=" * 80)
    print(f"ИТОГ МАТРИЦЫ ТЗ №6: провалов {fails}/{len(CASES) + 1}")
    print("=" * 80)

if __name__ == "__main__":
    main()
