import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from services.engineer_tdd import CodeFirstTDDPipeline
from services.sandbox_manager import SandboxManager


class FakeTDDClient:
    """Имитирует 7B-модель в цикле NEW_MODULE: ошибается в логике и повторяет ошибку."""
    def __init__(self, broken_impl: str, test_code: str):
        self.broken_impl = broken_impl
        self.test_code = test_code
        self.asked_to_alter_test = 0
        self.fix_impl_prompts = []

    def chat_completion(self, messages, **kwargs):
        sys_msg = (messages[0].get("content") or "") if messages else ""
        user_msg = (messages[-1].get("content") or "") if messages else ""

        if "Скорректируй сам файл тестов" in user_msg or "инженер по тестированию pytest" in sys_msg.lower() and "Скорректируй" in user_msg:
            self.asked_to_alter_test += 1
            return f"<<TOOL:write_file filename=\"test_calc.py\">\n{self.test_code}\n<</TOOL:write_file>>"

        if "ПРОМПТ-ГАРД ТЕСТЕРА" in user_msg or "Создай тестовый файл" in user_msg or "Напиши" in user_msg and "test_calc.py" in user_msg:
            return f"<<TOOL:write_file filename=\"test_calc.py\">\n{self.test_code}\n<</TOOL:write_file>>"

        if "Исправь код реализации" in user_msg or "Исправь реализацию" in user_msg:
            self.fix_impl_prompts.append(user_msg)
            return f"<<TOOL:write_file filename=\"calc.py\">\n{self.broken_impl}\n<</TOOL:write_file>>"

        # Первичная реализация модуля
        return f"<<TOOL:write_file filename=\"calc.py\">\n{self.broken_impl}\n<</TOOL:write_file>>"


def main():
    print("=" * 80)
    print("WAVE 2 BASELINE STAND: CodeFirstTDDPipeline Calibration")
    print("=" * 80)
    bugs = 0

    # --- BUG-W2-1: Русская фраза без кода затирает .py файл ---
    chatter = "Конечно, сейчас я исправлю функцию расчета и перепроверю тесты."
    blocks = CodeFirstTDDPipeline.extract_code_blocks(chatter, default_filename="calc.py")
    print(f"\n[W2-1] extract_code_blocks на русской фразе -> keys={list(blocks.keys())}")
    if blocks:
        print(">>> [REPRODUCED BUG-W2-1] Болтовня без кода попала в extract_code_blocks (перезатрёт .py файл на диске)!")
        bugs += 1
    else:
        print(">>> [PASS W2-1] Текст без кода отброшен, файл защищён.")

    orig_get_ws = SandboxManager.get_workspace_dir
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        SandboxManager.get_workspace_dir = classmethod(lambda cls, _p=tmp_ws: _p)
        try:
            plan = SimpleNamespace(
                task_type="NEW_MODULE",
                target_file="calc.py",
                target_artifact="calc.py",
                plan_desc="Калькулятор сложения",
                interface_spec={
                    "classes": [],
                    "functions": [{"name": "add", "args": ["a", "b"], "return_type": "int", "description": "Возвращает сумму a + b"}],
                },
            )

            # --- BUG-W2-2 & BUG-W2-3: AssertionError в логике -> попытка сломать тест + 15 холостых попыток ---
            broken_logic = "def add(a: int, b: int) -> int:\n    return a - b\n"
            good_test = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
            client1 = FakeTDDClient(broken_logic, good_test)

            res1 = CodeFirstTDDPipeline.run(
                plan=plan,
                client=client1,
                target_model="fake-coder",
                ws_dir=tmp_ws,
                task_prompt="Напиши модуль calc.py с функцией add(a, b)",
                worker_temp=0.70,
            )
            turns_spent = len(client1.fix_impl_prompts) + client1.asked_to_alter_test
            print(f"\n[W2-2 / W2-3] AssertionError логики: asked_to_alter_test={client1.asked_to_alter_test}, total_fix_calls={turns_spent}, success={res1.get('success')}")
            if client1.asked_to_alter_test > 0:
                print(">>> [REPRODUCED BUG-W2-2] При баге в логике calc.py пайплайн приказал переписать честный тест test_calc.py!")
                bugs += 1
            else:
                print(">>> [PASS W2-2] При баге логики честный тест test_calc.py не отдан под перезапись.")

            if turns_spent > 5:
                print(f">>> [REPRODUCED BUG-W2-3] Одинаковая ошибка крутилась {turns_spent} попыток вместо раннего останова по детектору зацикливания!")
                bugs += 1
            else:
                print(f">>> [PASS W2-3] Детектор зацикливания остановил цикл за {turns_spent} попыток.")

            # --- BUG-W2-4: Отсутствие diagnose_runtime_error в промпте починки ---
            broken_sig = "def add(a: int) -> int:\n    return a\n"
            client2 = FakeTDDClient(broken_sig, good_test)
            CodeFirstTDDPipeline.run(
                plan=plan,
                client=client2,
                target_model="fake-coder",
                ws_dir=tmp_ws,
                task_prompt="Напиши модуль calc.py с функцией add(a, b)",
                worker_temp=0.70,
            )
            first_fix_prompt = client2.fix_impl_prompts[0] if client2.fix_impl_prompts else ""
            has_diag = "РЕКОМЕНДАЦИИ ПО ИСПРАВЛЕНИЮ" in first_fix_prompt
            print(f"\n[W2-4] Промпт починки при TypeError содержит diagnose_runtime_error? -> {has_diag}")
            if not has_diag:
                print(">>> [REPRODUCED BUG-W2-4] Промпт починки TDD получает голый вывод pytest без diagnose_runtime_error!")
                bugs += 1
            else:
                print(">>> [PASS W2-4] В промпт починки TDD подключён diagnose_runtime_error.")
        finally:
            SandboxManager.get_workspace_dir = orig_get_ws

    print("\n" + "=" * 80)
    print(f"ИТОГ СТЕНДА ВОЛНЫ 2: воспроизведено багов {bugs}/4")
    print("=" * 80)


if __name__ == "__main__":
    main()
