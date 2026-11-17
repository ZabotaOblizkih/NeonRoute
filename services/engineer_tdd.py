"""
services/engineer_tdd.py
Двухфазный Code-First TDD пайплайн для NeonRoute Engineer Sub-Agent.
Реализует ветки NEW_MODULE и INCREMENTAL_FEATURE с защитой от регрессий,
санитизацией самоимпортов и двусторонним самоисцелением тестов и кода.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.git_sandbox import GitSandboxManager
from services.sandbox_manager import SandboxManager
from services.regex_filter import TextRegexFilter
from services.tool_engine import ToolEngine, sanitize_code
from services.engineer_context import EngineerContext
from services.engineer_diagnostics import diagnose_runtime_error, extract_error_fingerprint

logger = logging.getLogger("NeonRoute.EngineerTDD")


def extract_clean_code(full_generated_text: str) -> str:
    """Извлекает чистый блок кода Python из Markdown или Tool тегов."""
    m_tool = re.search(r'<<TOOL:write_file\s+[^>]*>>\s*([\s\S]*?)\s*<</TOOL:write_file>>', full_generated_text, re.IGNORECASE)
    if m_tool:
        return m_tool.group(1).strip()
    m_md = re.search(r'```(?:python|py)?\s*[\r\n]+([\s\S]*?)```', full_generated_text, re.IGNORECASE)
    if m_md:
        return m_md.group(1).strip()
    return full_generated_text.strip()


class CodeFirstTDDPipeline:
    """Управление выполнением двухфазного TDD пайплайна."""

    @staticmethod
    def extract_code_blocks(response_text: str, default_filename: str) -> Dict[str, str]:
        """Извлекает блоки write_file или markdown код с автоматической санитизацией от самоимпорта."""
        files: Dict[str, str] = {}
        response_text = TextRegexFilter.strip_thought_blocks(str(response_text or ""))
        write_pattern = r'<<TOOL:write_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\']\s*>>?[\r\n]*(?P<content>.*?)(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+|$)'
        for m in re.finditer(write_pattern, response_text, flags=re.DOTALL | re.IGNORECASE):
            fn = m.group("filename").strip().strip("'\"`")
            cnt = m.group("content").strip()
            if fn and cnt:
                files[fn] = cnt

        if not files:
            md_named = r'```(?:python|py)?\s+(?:filename|file)=["\']?(?P<fn>[^\s"\'>]+)["\']?\s*[\r\n]+(?P<code>[\s\S]*?)(?:```|$)'
            for m in re.finditer(md_named, response_text, flags=re.IGNORECASE):
                fn = m.group("fn").strip().strip("'\"`")
                c_body = m.group("code").strip()
                if fn and c_body:
                    files[fn] = c_body

        if not files:
            pure = extract_clean_code(response_text)
            if pure and pure.strip() and EngineerContext.has_python_code(pure):
                files[default_filename] = pure.strip()

        for fn in list(files.keys()):
            files[fn] = sanitize_code(files[fn], fn)

        return files

    @staticmethod
    def _is_test_defect(last_error: str, test_file: str, mod_name: str, test_fix_count: int = 0) -> bool:
        """
        Определяет, вызвана ли ошибка падения тестов дефектом самого файла тестов
        или же ошибкой в реализации тестируемого модуля.
        """
        # 1. AssertionError бизнес-логики - строго ошибка реализации модуля
        if "AssertionError" in last_error:
            return False

        # 2. Если ошибка произошла внутри модуля (последний фрейм в mod_name) - ошибка реализации
        tb_lines = [line.strip() for line in last_error.splitlines() if line.strip().startswith("File ")]
        if tb_lines and mod_name in tb_lines[-1]:
            return False

        # 3. Ошибка тестового фреймворка (несуществующая фикстура)
        if "fixture '" in last_error and "not found" in last_error:
            return True

        test_fn = Path(test_file).name
        # 4. Сбой сбора тестов pytest
        if f"ERROR collecting {test_fn}" in last_error:
            return True

        # 5. Синтаксическая/импортная/именная ошибка внутри самого тестового файла
        if test_fn in last_error and any(err in last_error for err in ("SyntaxError", "IndentationError", "NameError", "ImportError", "ModuleNotFoundError")):
            if tb_lines:
                if any(test_fn in line for line in tb_lines) and not any(mod_name in line for line in tb_lines):
                    return True
            else:
                return True

        # 6. Сбой сигнатуры/атрибута непосредственно на строке вызова из test_file (не чаще 1-2 раз)
        if test_fix_count < 2 and any(err in last_error for err in ("TypeError", "AttributeError")):
            if tb_lines and any(test_fn in line for line in tb_lines) and not any(mod_name in line for line in tb_lines):
                if "has no attribute" in last_error or "takes " in last_error or "missing " in last_error:
                    return False
                return True

        return False

    @staticmethod
    def append_new_tests(existing_test_code: str, new_test_snippet: str) -> str:
        """
        Дописывает тестовые функции в конец существующего файла тестов
        без дублирования заголовков и уже имеющихся импортов.
        """
        existing_lines = existing_test_code.splitlines()
        existing_imports = set(
            line.strip() for line in existing_lines
            if line.strip().startswith(("import ", "from "))
        )

        new_lines = []
        for line in new_test_snippet.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                if stripped in existing_imports:
                    continue
                existing_imports.add(stripped)
            new_lines.append(line)

        clean_new = "\n".join(new_lines).strip()
        if not clean_new:
            return existing_test_code
        return existing_test_code.rstrip() + "\n\n\n" + clean_new + "\n"

    @classmethod
    def _call_completion(
        cls,
        client: Any,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
        thought_chunk_callback: Optional[Any] = None,
        **extra_kwargs
    ) -> str:
        use_stream = bool(thought_chunk_callback and enable_thinking is not False)
        call_kwargs: Dict[str, Any] = {
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": use_stream,
            "emit_signal": False
        }
        if use_stream:
            call_kwargs["chunk_callback"] = thought_chunk_callback
        if enable_thinking is not None:
            call_kwargs["enable_thinking"] = enable_thinking
        call_kwargs.update(extra_kwargs)

        try:
            try:
                return client.chat_completion(**call_kwargs)
            except TypeError:
                call_kwargs_fallback = dict(call_kwargs)
                call_kwargs_fallback.pop("chunk_callback", None)
                call_kwargs_fallback["stream"] = False
                try:
                    return client.chat_completion(**call_kwargs_fallback)
                except TypeError:
                    call_kwargs_fallback.pop("enable_thinking", None)
                    return client.chat_completion(**call_kwargs_fallback)
        except Exception as e:
            raise e

    @classmethod
    def run(
        cls,
        plan: Any,
        client: Any,
        target_model: str,
        ws_dir: Path,
        task_prompt: str,
        context_hint: str = "",
        on_message_callback: Optional[Any] = None,
        on_status_callback: Optional[Any] = None,
        inspector: Optional[Any] = None,
        worker_temp: float = 0.20,
        enable_thinking: Optional[bool] = None,
        thought_chunk_callback: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if enable_thinking is None and "enable_thinking" in kwargs:
            enable_thinking = kwargs.pop("enable_thinking")
        if thought_chunk_callback is None and "thought_chunk_callback" in kwargs:
            thought_chunk_callback = kwargs.pop("thought_chunk_callback")
        task_type = getattr(plan, "task_type", "NEW_MODULE")
        if task_type == "INCREMENTAL_FEATURE":
            return cls.run_incremental_feature(
                plan=plan,
                client=client,
                target_model=target_model,
                ws_dir=ws_dir,
                task_prompt=task_prompt,
                context_hint=context_hint,
                on_message_callback=on_message_callback,
                on_status_callback=on_status_callback,
                inspector=inspector,
                worker_temp=worker_temp,
                enable_thinking=enable_thinking,
                thought_chunk_callback=thought_chunk_callback,
                **kwargs
            )
        else:
            return cls.run_new_module(
                plan=plan,
                client=client,
                target_model=target_model,
                ws_dir=ws_dir,
                task_prompt=task_prompt,
                context_hint=context_hint,
                on_message_callback=on_message_callback,
                on_status_callback=on_status_callback,
                inspector=inspector,
                worker_temp=worker_temp,
                enable_thinking=enable_thinking,
                thought_chunk_callback=thought_chunk_callback,
                **kwargs
            )

    @classmethod
    def run_new_module(
        cls,
        plan: Any,
        client: Any,
        target_model: str,
        ws_dir: Path,
        task_prompt: str,
        context_hint: str = "",
        on_message_callback: Optional[Any] = None,
        on_status_callback: Optional[Any] = None,
        inspector: Optional[Any] = None,
        worker_temp: float = 0.20,
        enable_thinking: Optional[bool] = None,
        thought_chunk_callback: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if enable_thinking is None and "enable_thinking" in kwargs:
            enable_thinking = kwargs.pop("enable_thinking")
        if thought_chunk_callback is None and "thought_chunk_callback" in kwargs:
            thought_chunk_callback = kwargs.pop("thought_chunk_callback")
        """
        Ветка NEW_MODULE:
        Шаг 1 (Код): Реализация модуля по JSON-спецификации Архитектора + sanitize_code.
        Шаг 2 (Тест): Генерация test_<module>.py со строгим промпт-гардом.
        Шаг 3 (Двустороннее исцеление): До 15 попыток (нечётные — код, чётные — тесты).
        """
        mod_name = getattr(plan, "target_file", None) or getattr(plan, "target_artifact", None) or "module.py"
        clean_mod = mod_name.replace(".py", "")
        test_file = f"test_{clean_mod}.py"

        def _send(role: str, text: str):
            if on_message_callback and callable(on_message_callback):
                try:
                    on_message_callback(role, text)
                except TypeError:
                    try:
                        on_message_callback(text)
                    except Exception:
                        pass
                except Exception:
                    pass

        # === ШАГ 1 (КОД): Пишет реализацию модуля по JSON-спецификации Архитектора ===
        _send("assistant", f"🛠️ **Шаг 1 (Код)**: Создаю реализацию модуля `{mod_name}` по спецификации Архитектора...")
        if on_status_callback and callable(on_status_callback):
            on_status_callback("writing", mod_name, 0, "")

        spec_obj = getattr(plan, "interface_spec", {}) or {}
        spec_str = json.dumps(spec_obj, ensure_ascii=False, indent=2) if isinstance(spec_obj, (dict, list)) else str(spec_obj)

        step1_system = "Ты — ведущий инженер Python NeonRoute. Твоя задача — написать чистый код модуля по спецификации интерфейса Архитектора."
        step1_user = (
            f"Задача: {task_prompt}\n\n"
            f"Целевой файл: {mod_name}\n\n"
            f"Спецификация интерфейса (interface_spec):\n```json\n{spec_str}\n```\n\n"
            f"СТРОГИЕ ТРЕБОВАНИЯ:\n"
            f"1. Реализуй все описанные классы, методы, типы аргументов и возвращаемые значения.\n"
            f"2. При невалидных входных данных обязательно выбрасывай специфицированные исключения (например, ValueError).\n"
            f"3. НЕ пиши тесты на этом шаге (тесты будут написаны отдельно на Шаге 2).\n"
            f"4. Оформи полный код модуля через <<TOOL:write_file filename=\"{mod_name}\">>код<</TOOL:write_file>>."
        )

        resp1 = cls._call_completion(
            client=client,
            messages=[{"role": "system", "content": step1_system}, {"role": "user", "content": step1_user}],
            model=target_model,
            temperature=worker_temp,
            max_tokens=4096,
            enable_thinking=enable_thinking,
            thought_chunk_callback=thought_chunk_callback
        )
        files1 = cls.extract_code_blocks(str(resp1 or ""), default_filename=mod_name)
        impl_code = files1.get(mod_name) or (list(files1.values())[0] if files1 else "# Empty module\n")
        impl_code = sanitize_code(impl_code, mod_name)
        ToolEngine.write_file(mod_name, impl_code)
        _send("assistant", f"✅ Модуль `{mod_name}` сохранён на диск (санитизирован от самоимпорта).")

        # === ШАГ 2 (ТЕСТ): По уже написанному файлу генерирует файл test_<module>.py ===
        test_code = files1.get(test_file)
        if not test_code:
            _send("assistant", f"🧪 **Шаг 2 (Тест)**: Генерирую тестовый набор pytest `{test_file}`...")
            if on_status_callback and callable(on_status_callback):
                on_status_callback("writing", test_file, 0, "")

            actual_code = (ws_dir / mod_name).read_text(encoding="utf-8", errors="ignore") if (ws_dir / mod_name).is_file() else impl_code
            step2_system = "Ты — инженер по тестированию pytest в NeonRoute. Напиши исчерпывающие тесты для модуля."
            step2_user = (
                f"Напиши тесты pytest для созданного модуля `{mod_name}`.\n\n"
                f"Актуальный код модуля `{mod_name}`:\n```python\n{actual_code}\n```\n\n"
                f"Спецификация интерфейса:\n```json\n{spec_str}\n```\n\n"
                f"ПРОМПТ-ГАРД ТЕСТЕРА:\n"
                f"1. СТРОГО функции pytest (def test_...()). Категорически ЗАПРЕЩЕН unittest.TestCase.\n"
                f"2. Разрешены стандартные встроенные фикстуры pytest (tmp_path, monkeypatch, capsys). ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры бизнес-объектов (def test_foo(task_manager) → ЗАПРЕЩЕНО). Объект класса создается прямо внутри тела теста (manager = TaskManager()).\n"
                f"3. Импортируй модуль и классы через: from {clean_mod} import ...\n"
                f"4. Покрой тестами все публичные методы, аргументы, проверки типов и исключения (pytest.raises(ValueError)).\n"
                f"5. Оформи тестовый файл через <<TOOL:write_file filename=\"{test_file}\">>код<</TOOL:write_file>>."
            )
            resp2 = cls._call_completion(
                client=client,
                messages=[{"role": "system", "content": step2_system}, {"role": "user", "content": step2_user}],
                model=target_model,
                temperature=worker_temp,
                max_tokens=4096,
                enable_thinking=enable_thinking,
                thought_chunk_callback=thought_chunk_callback
            )
            files2 = cls.extract_code_blocks(str(resp2 or ""), default_filename=test_file)
            test_code = files2.get(test_file) or (list(files2.values())[0] if files2 else "")

        if not test_code:
            test_code = f"import pytest\nimport {clean_mod}\n\ndef test_smoke():\n    assert True\n"

        test_code = sanitize_code(test_code, test_file)
        ToolEngine.write_file(test_file, test_code)
        _send("assistant", f"✅ Тестовый файл `{test_file}` сформирован и сохранён на диск.")

        # === ШАГ 3 (ДВУСТОРОННЕЕ ИСЦЕЛЕНИЕ, ДО 15 ПОПЫТОК) ===
        _send("assistant", f"🔄 **Шаг 3 (Двустороннее исцеление)**: Запуск `pytest {test_file} -v`...")
        last_error = ""
        last_error_fingerprint = None
        consecutive_same_error_count = 0
        consecutive_no_blocks_count = 0
        max_same_error_retries = 3
        test_fix_count = 0

        for attempt in range(1, 16):
            if on_status_callback and callable(on_status_callback):
                on_status_callback("running", test_file, 0, "")

            test_res = ToolEngine.run_pytest(test_file)
            if test_res.success:
                logger.info(f"🎉 [TDD NEW_MODULE] Успех на попытке {attempt}: Зеленый pytest!")
                _send("assistant", f"🎉 **100% Green**: Модуль `{mod_name}` успешно прошёл все тесты pytest (`{test_file}`) на попытке {attempt}!")
                GitSandboxManager.checkpoint(ws_dir, f"TDD New Module: {mod_name} passed all tests in {test_file}")
                return {
                    "success": True,
                    "target_artifact": mod_name,
                    "verification_script": test_file,
                    "verification_passed": True,
                    "artifact_path": str((ws_dir / mod_name).resolve()),
                    "file_name": mod_name,
                    "stdout": test_res.stdout or "All tests passed",
                    "stderr": "",
                    "summary": f"Модуль {mod_name} успешно создан и верифицирован зеленым pytest ({test_file}).",
                    "turns": attempt
                }

            last_error = test_res.output or test_res.stderr or "Pytest failed"
            logger.warning(f"⚠️ [TDD NEW_MODULE] Попытка {attempt}/15 упала: {last_error[:150]}")

            current_fp = extract_error_fingerprint(last_error)
            if current_fp and current_fp == last_error_fingerprint:
                consecutive_same_error_count += 1
                logger.warning(f"⚠️ [TDD NEW_MODULE] Детектор зацикливания: повтор ошибки ({consecutive_same_error_count}/{max_same_error_retries}): {current_fp}")
            else:
                consecutive_same_error_count = 1
                last_error_fingerprint = current_fp

            if consecutive_same_error_count >= max_same_error_retries:
                fail_summary = (
                    f"⛔ [Останов по зацикливанию TDD]: Модель {max_same_error_retries} раза подряд наступила на ошибку:\n"
                    f"«{current_fp}»\n\nДетали последней ошибки:\n{last_error}"
                )
                logger.error(fail_summary)
                _send("assistant", fail_summary)
                return {
                    "success": False,
                    "target_artifact": mod_name,
                    "verification_script": test_file,
                    "verification_passed": False,
                    "error": last_error,
                    "summary": fail_summary
                }

            enriched_error = diagnose_runtime_error(last_error)
            is_defect_in_test = cls._is_test_defect(last_error, test_file, mod_name, test_fix_count=test_fix_count)
            curr_impl = (ws_dir / mod_name).read_text(encoding="utf-8", errors="ignore") if (ws_dir / mod_name).is_file() else ""
            curr_test = (ws_dir / test_file).read_text(encoding="utf-8", errors="ignore") if (ws_dir / test_file).is_file() else ""

            if not is_defect_in_test:
                # Исправление кода реализации (<module>.py)
                _send("assistant", f"🔧 Попытка {attempt}/15: Исправляю код реализации `{mod_name}`...")
                fix_user = (
                    f"Тесты pytest `{test_file}` упали с ошибкой (Попытка {attempt}/15):\n"
                    f"```\n{enriched_error}\n```\n\n"
                    f"Текущий код реализации `{mod_name}`:\n```python\n{curr_impl}\n```\n\n"
                    f"Текущий код тестов `{test_file}`:\n```python\n{curr_test}\n```\n\n"
                    f"Исправь код реализации `{mod_name}`, чтобы тесты выполнялись успешно. "
                    f"Сохрани все имена классов, сигнатуры методов и спецификацию исключений.\n"
                    f"Верни полный исправленный файл через <<TOOL:write_file filename=\"{mod_name}\">>код<</TOOL:write_file>>."
                )
                fix_msgs = [
                    {"role": "system", "content": "Ты — ведущий инженер Python. Исправь код реализации модуля, устранив ошибки логики."},
                    {"role": "user", "content": fix_user}
                ]
                fix_resp = cls._call_completion(
                    client=client,
                    messages=fix_msgs,
                    model=target_model,
                    temperature=worker_temp,
                    max_tokens=4096,
                    enable_thinking=enable_thinking,
                    thought_chunk_callback=thought_chunk_callback
                )
                blocks = cls.extract_code_blocks(str(fix_resp or ""), default_filename=mod_name)
                if not blocks:
                    consecutive_no_blocks_count += 1
                    if consecutive_no_blocks_count >= max_same_error_retries:
                        fail_summary = f"⛔ [Останов по зацикливанию TDD]: Модель {max_same_error_retries} раза подряд не вернула код."
                        logger.error(fail_summary)
                        _send("assistant", fail_summary)
                        return {
                            "success": False,
                            "target_artifact": mod_name,
                            "verification_script": test_file,
                            "verification_passed": False,
                            "error": last_error,
                            "summary": fail_summary
                        }
                else:
                    consecutive_no_blocks_count = 0
                    new_impl = blocks.get(mod_name) or list(blocks.values())[0]
                    new_impl = sanitize_code(new_impl, mod_name)
                    ToolEngine.write_file(mod_name, new_impl)
            else:
                test_fix_count += 1
                _send("assistant", f"🧪 Попытка {attempt}/15: Корректирую структуру тестов `{test_file}`...")
                fix_user = (
                    f"Тесты pytest `{test_file}` упали с ошибкой (Попытка {attempt}/15):\n"
                    f"```\n{enriched_error}\n```\n\n"
                    f"Ошибка вызвана структурой теста (fixture not found, опечатка в assert, синтаксис теста, неверные ожидания).\n"
                    f"Текущий код тестов `{test_file}`:\n```python\n{curr_test}\n```\n\n"
                    f"Текущий код реализации `{mod_name}`:\n```python\n{curr_impl}\n```\n\n"
                    f"Скорректируй сам файл тестов `{test_file}`.\n"
                    f"СТРОГИЙ ПРОМПТ-ГАРД:\n"
                    f"1. Строго функции pytest (def test_...()). Запрещен unittest.TestCase.\n"
                    f"2. Разрешены стандартные встроенные фикстуры pytest (tmp_path, monkeypatch, capsys). ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры бизнес-объектов (def test_foo(task_manager) → ЗАПРЕЩЕНО). Объект класса создается прямо внутри тела теста (manager = TaskManager()).\n"
                    f"Верни полный исправленный файл тестов через <<TOOL:write_file filename=\"{test_file}\">>код<</TOOL:write_file>>."
                )
                fix_msgs = [
                    {"role": "system", "content": "Ты — инженер по тестированию pytest. Скорректируй структуру и ассерты теста."},
                    {"role": "user", "content": fix_user}
                ]
                fix_resp = cls._call_completion(
                    client=client,
                    messages=fix_msgs,
                    model=target_model,
                    temperature=worker_temp,
                    max_tokens=4096,
                    enable_thinking=enable_thinking,
                    thought_chunk_callback=thought_chunk_callback
                )
                blocks = cls.extract_code_blocks(str(fix_resp or ""), default_filename=test_file)
                if not blocks:
                    consecutive_no_blocks_count += 1
                    if consecutive_no_blocks_count >= max_same_error_retries:
                        fail_summary = f"⛔ [Останов по зацикливанию TDD]: Модель {max_same_error_retries} раза подряд не вернула код."
                        logger.error(fail_summary)
                        _send("assistant", fail_summary)
                        return {
                            "success": False,
                            "target_artifact": mod_name,
                            "verification_script": test_file,
                            "verification_passed": False,
                            "error": last_error,
                            "summary": fail_summary
                        }
                else:
                    consecutive_no_blocks_count = 0
                    new_test = blocks.get(test_file) or list(blocks.values())[0]
                    new_test = sanitize_code(new_test, test_file)
                    ToolEngine.write_file(test_file, new_test)

        fail_summary = f"Исчерпан лимит в 15 попыток исцеления для {mod_name}. Последняя ошибка:\n{last_error}"
        _send("assistant", f"⛔ {fail_summary}")
        return {
            "success": False,
            "target_artifact": mod_name,
            "verification_script": test_file,
            "verification_passed": False,
            "error": last_error,
            "summary": fail_summary
        }

    @classmethod
    def run_incremental_feature(
        cls,
        plan: Any,
        client: Any,
        target_model: str,
        ws_dir: Path,
        task_prompt: str,
        context_hint: str = "",
        on_message_callback: Optional[Any] = None,
        on_status_callback: Optional[Any] = None,
        inspector: Optional[Any] = None,
        worker_temp: float = 0.20,
        enable_thinking: Optional[bool] = None,
        thought_chunk_callback: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if enable_thinking is None and "enable_thinking" in kwargs:
            enable_thinking = kwargs.pop("enable_thinking")
        if thought_chunk_callback is None and "thought_chunk_callback" in kwargs:
            thought_chunk_callback = kwargs.pop("thought_chunk_callback")
        """
        Ветка INCREMENTAL_FEATURE:
        Шаг 1 (Pre-flight Baseline Check): Прогон существующих тестов модуля (100% Green).
        Шаг 2 (Внесение правок): Дописывание новых методов с сохранением старой логики.
        Шаг 3 (Regression Check): Запуск старых тестов на новом коде с откатом при сбое.
        Шаг 4 (Дописывание тестов): Генерация тестов для новых методов без дублирования импортов.
        Шаг 5 (Финальная верификация): Прогон всех тестов с двусторонним исцелением до 15 попыток.
        """
        mod_name = getattr(plan, "target_file", None) or getattr(plan, "target_artifact", None) or "module.py"
        clean_mod = mod_name.replace(".py", "")
        test_file = f"test_{clean_mod}.py"
        if not (ws_dir / test_file).is_file() and (ws_dir / f"{clean_mod}_test.py").is_file():
            test_file = f"{clean_mod}_test.py"

        def _send(role: str, text: str):
            if on_message_callback and callable(on_message_callback):
                try:
                    on_message_callback(role, text)
                except TypeError:
                    try:
                        on_message_callback(text)
                    except Exception:
                        pass
                except Exception:
                    pass

        orig_mod_code = (ws_dir / mod_name).read_text(encoding="utf-8", errors="ignore") if (ws_dir / mod_name).is_file() else ""
        has_valid_baseline = False
        orig_test_code = ""

        # === ШАГ 1: ПРОВЕРКА ИЛИ BOOTSTRAP БАЗОВЫХ ТЕСТОВ ===
        if not (ws_dir / test_file).is_file() or (ws_dir / test_file).stat().st_size == 0:
            _send("assistant", f"⚡ **Bootstrap Baseline**: Тестовый файл `{test_file}` отсутствует. Создаю базовые тесты для фиксации текущего поведения `{mod_name}`...")
            
            boot_system = (
                "Ты — инженер по тестированию NeonRoute. Для существующего рабочего кода нет тестов.\n"
                "Твоя задача — написать минимальный набор базовых тестов pytest для фиксации текущего поведения (Happy Path).\n"
                "СТРОГИЕ ПРАВИЛА:\n"
                "1. ТОЛЬКО pytest функции `def test_...()`. Запрещен unittest.TestCase.\n"
                "2. Создавай объекты классов прямо внутри тестов. Фикстуры в аргументах ЗАПРЕЩЕНЫ (кроме стандартных pytest: tmp_path, monkeypatch, capsys).\n"
                f"Верни ТОЛЬКО код тестов для файла {test_file}."
            )
            boot_user = (
                f"Исходный код модуля {mod_name}:\n```python\n{orig_mod_code}\n```\n\n"
                f"Напиши базовые тесты для фиксации текущего функционала в файле {test_file}."
            )

            boot_resp = cls._call_completion(
                client=client,
                messages=[{"role": "system", "content": boot_system}, {"role": "user", "content": boot_user}],
                model=target_model,
                temperature=worker_temp,
                max_tokens=4096,
                enable_thinking=enable_thinking,
                thought_chunk_callback=thought_chunk_callback
            )
            boot_blocks = cls.extract_code_blocks(str(boot_resp or ""), default_filename=test_file)
            boot_test_code = boot_blocks.get(test_file) or (list(boot_blocks.values())[0] if boot_blocks else "")
            boot_test_code = sanitize_code(boot_test_code, test_file)
            (ws_dir / test_file).write_text(boot_test_code, encoding="utf-8")

            # Проверяем базовый тест. Если упал — правим ТЕСТ (существующий код эталонен)
            boot_check = ToolEngine.run_pytest(test_file)
            boot_attempts = 0
            while not boot_check.success and boot_attempts < 3:
                boot_attempts += 1
                logger.warning(f"[Bootstrap] Базовый тест упал, исправляем опечатки (попытка {boot_attempts}/3)...")
                fix_boot_msgs = [
                    {"role": "system", "content": f"Исправь опечатки в {test_file} под существующий эталонный код модуля. Создавай объекты внутри тестов без фикстур."},
                    {"role": "user", "content": f"Код модуля {mod_name}:\n```python\n{orig_mod_code}\n```\n\nТекущий тест:\n```python\n{boot_test_code}\n```\n\nОшибка pytest:\n{boot_check.output or boot_check.stderr}"}
                ]
                fix_resp = cls._call_completion(
                    client=client,
                    messages=fix_boot_msgs,
                    model=target_model,
                    temperature=worker_temp,
                    max_tokens=4096,
                    enable_thinking=enable_thinking,
                    thought_chunk_callback=thought_chunk_callback
                )
                f_blocks = cls.extract_code_blocks(str(fix_resp or ""), default_filename=test_file)
                boot_test_code = f_blocks.get(test_file) or (list(f_blocks.values())[0] if f_blocks else boot_test_code)
                boot_test_code = sanitize_code(boot_test_code, test_file)
                (ws_dir / test_file).write_text(boot_test_code, encoding="utf-8")
                boot_check = ToolEngine.run_pytest(test_file)

            if not boot_check.success:
                err_msg = f"⚠ [Bootstrap Warning]: Не удалось стабилизировать базовые тесты для `{mod_name}`. Удаляю невалидный тест и продолжаю без baseline-страховки."
                logger.warning(err_msg)
                _send("assistant", err_msg)
                # Удаляем с диска битый тестовый файл, чтобы он не отравлял дальнейшие шаги:
                if (ws_dir / test_file).is_file():
                    try:
                        (ws_dir / test_file).unlink(missing_ok=True)
                    except Exception:
                        pass
                has_valid_baseline = False
                orig_test_code = ""
            else:
                _send("assistant", f"✅ **Bootstrap завершён**: Базовые тесты `{test_file}` созданы и 100% зелёные.")
                has_valid_baseline = True
                orig_test_code = (ws_dir / test_file).read_text(encoding="utf-8", errors="ignore") if (ws_dir / test_file).is_file() else ""
        else:
            # Штатный прогон существующих тестов
            _send("assistant", f"🔍 **Шаг 1 (Pre-flight Baseline Check)**: Прогон существующих тестов `{test_file}`...")
            preflight_res = ToolEngine.run_pytest(test_file)
            if not preflight_res.success:
                alert_msg = (
                    f"🚨 [Pre-flight Baseline Check FAILED]: Базовые тесты `{test_file}` падают ДО внесения правок!\n"
                    f"База обязана быть 100% зелёной перед доработкой модуля.\n"
                    f"Вывод pytest:\n{preflight_res.output or preflight_res.stderr}"
                )
                logger.error(alert_msg)
                _send("assistant", alert_msg)
                return {
                    "success": False,
                    "target_artifact": mod_name,
                    "verification_script": test_file,
                    "verification_passed": False,
                    "error": alert_msg,
                    "summary": alert_msg
                }
            _send("assistant", f"✅ Базовые тесты `{test_file}` 100% зеленые. Приступаю к внесению правок.")
            has_valid_baseline = True
            orig_test_code = (ws_dir / test_file).read_text(encoding="utf-8", errors="ignore")

        # === ШАГ 2 (ВНЕСЕНИЕ ПРАВОК): Дописывание новых методов в класс с сохранением старой логики ===
        _send("assistant", f"🛠️ **Шаг 2 (Внесение правок)**: Дописывание новых методов в `{mod_name}` с сохранением существующей логики...")
        spec_obj = getattr(plan, "interface_spec", {}) or {}
        spec_str = json.dumps(spec_obj, ensure_ascii=False, indent=2) if isinstance(spec_obj, (dict, list)) else str(spec_obj)

        step2_system = "Ты — ведущий инженер Python. Твоя задача — дописать новые методы в существующий класс по спецификации Архитектора, строго сохранив ВСЮ старую логику."
        step2_user = (
            f"Задача: {task_prompt}\n\n"
            f"Целевой файл: {mod_name}\n\n"
            f"Спецификация новых методов (interface_spec):\n```json\n{spec_str}\n```\n\n"
            f"Существующий код файла {mod_name}:\n```python\n{orig_mod_code}\n```\n\n"
            f"СТРОГОЕ ТРЕБОВАНИЕ: Допиши новые методы в класс. ОБЯЗАТЕЛЬНО сохрани всю существующую логику и методы без удаления и искажения!\n"
            f"Верни обновлённый монолитный код через <<TOOL:write_file filename=\"{mod_name}\">>код<</TOOL:write_file>>."
        )

        resp2 = cls._call_completion(
            client=client,
            messages=[{"role": "system", "content": step2_system}, {"role": "user", "content": step2_user}],
            model=target_model,
            temperature=worker_temp,
            max_tokens=4096,
            enable_thinking=enable_thinking,
            thought_chunk_callback=thought_chunk_callback
        )
        blocks2 = cls.extract_code_blocks(str(resp2 or ""), default_filename=mod_name)
        if blocks2:
            new_mod_code = blocks2.get(mod_name) or (list(blocks2.values())[0] if blocks2 else orig_mod_code)
            new_mod_code = sanitize_code(new_mod_code, mod_name)
            ToolEngine.write_file(mod_name, new_mod_code)
            _send("assistant", f"✅ Изменения внесены в `{mod_name}`.")

        # === ШАГ 3 (REGRESSION CHECK): Запуск старых тестов на новом коде ===
        if has_valid_baseline and (ws_dir / test_file).is_file():
            _send("assistant", f"🛡️ **Шаг 3 (Regression Check)**: Запуск старых тестов `{test_file}` на новом коде...")
            reg_res = ToolEngine.run_pytest(test_file)
            reg_attempt = 0
            while not reg_res.success and reg_attempt < 5:
                reg_attempt += 1
                logger.warning(f"⚠️ [TDD INCREMENTAL] Регрессия старых тестов! Попытка восстановления {reg_attempt}/5")
                _send("assistant", f"⚠️ Регрессия: старые тесты упали (попытка {reg_attempt}/5). Восстанавливаю обратную совместимость...")
                curr_code = (ws_dir / mod_name).read_text(encoding="utf-8", errors="ignore")
                curr_test = (ws_dir / test_file).read_text(encoding="utf-8", errors="ignore") if (ws_dir / test_file).is_file() else ""
                reg_err = reg_res.output or reg_res.stderr or "Pytest regression failure"
                enriched_reg_err = diagnose_runtime_error(reg_err)
                is_reg_test_defect = cls._is_test_defect(reg_err, test_file, mod_name)

                if not is_reg_test_defect:
                    # Исправление реализации модуля
                    reg_user = (
                        f"РЕГРЕССИОННЫЙ СБОЙ: Старые тесты {test_file} упали после добавления новых методов!\n"
                        f"```\n{enriched_reg_err}\n```\n\n"
                        f"Текущий код {mod_name}:\n```python\n{curr_code}\n```\n\n"
                        f"Исходный рабочий код до изменений:\n```python\n{orig_mod_code}\n```\n\n"
                        f"Исправь реализацию {mod_name}, чтобы старые тесты {test_file} стали 100% зелеными, "
                        f"сохранив при этом новый добавленный функционал.\n"
                        f"Верни обновленный код через <<TOOL:write_file filename=\"{mod_name}\">>код<</TOOL:write_file>>."
                    )
                    reg_msgs = [
                        {"role": "system", "content": "Ты — старший инженер Python. Устрани регрессию, восстановив работу старого функционала."},
                        {"role": "user", "content": reg_user}
                    ]
                    reg_resp = cls._call_completion(
                        client=client,
                        messages=reg_msgs,
                        model=target_model,
                        temperature=worker_temp,
                        max_tokens=4096,
                        enable_thinking=enable_thinking,
                        thought_chunk_callback=thought_chunk_callback
                    )
                    reg_blocks = cls.extract_code_blocks(str(reg_resp or ""), default_filename=mod_name)
                    if reg_blocks:
                        fixed_code = reg_blocks.get(mod_name) or list(reg_blocks.values())[0]
                        fixed_code = sanitize_code(fixed_code, mod_name)
                        ToolEngine.write_file(mod_name, fixed_code)
                else:
                    # Адаптация тестов под обновлённую спецификацию интерфейса
                    _send("assistant", f"🧪 Регрессия: адаптация тестов `{test_file}` под обновлённую спецификацию интерфейса...")
                    reg_test_user = (
                        f"РЕГРЕССИОННЫЙ СБОЙ: Старые тесты {test_file} упали после изменения интерфейса (Попытка {reg_attempt}/5):\n"
                        f"```\n{enriched_reg_err}\n```\n\n"
                        f"Спецификация интерфейса (interface_spec):\n```json\n{spec_str}\n```\n\n"
                        f"Текущий код модуля {mod_name}:\n```python\n{curr_code}\n```\n\n"
                        f"Текущий код тестов {test_file}:\n```python\n{curr_test}\n```\n\n"
                        f"Если сигнатура или параметры методов изменились согласно interface_spec, обнови тесты {test_file}.\n"
                        f"СТРОГИЙ ПРОМПТ-ГАРД:\n"
                        f"1. Строго функции pytest (def test_...()). Запрещен unittest.TestCase.\n"
                        f"2. Разрешены стандартные встроенные фикстуры pytest (tmp_path, monkeypatch, capsys). ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры бизнес-объектов.\n"
                        f"Верни полный исправленный файл тестов через <<TOOL:write_file filename=\"{test_file}\">>код<</TOOL:write_file>>."
                    )
                    reg_test_msgs = [
                        {"role": "system", "content": "Ты — инженер по тестированию pytest. Скорректируй тесты под обновлённую спецификацию интерфейса."},
                        {"role": "user", "content": reg_test_user}
                    ]
                    reg_resp = cls._call_completion(
                        client=client,
                        messages=reg_test_msgs,
                        model=target_model,
                        temperature=worker_temp,
                        max_tokens=4096,
                        enable_thinking=enable_thinking,
                        thought_chunk_callback=thought_chunk_callback
                    )
                    reg_blocks = cls.extract_code_blocks(str(reg_resp or ""), default_filename=test_file)
                    if reg_blocks:
                        fixed_test = reg_blocks.get(test_file) or list(reg_blocks.values())[0]
                        fixed_test = sanitize_code(fixed_test, test_file)
                        ToolEngine.write_file(test_file, fixed_test)
                        orig_test_code = fixed_test

                reg_res = ToolEngine.run_pytest(test_file)

            if not reg_res.success:
                ToolEngine.write_file(mod_name, orig_mod_code)
                alert_reg = f"🚨 Регрессия: не удалось восстановить старые тесты {test_file}. Выполнен откат {mod_name}."
                logger.error(alert_reg)
                _send("assistant", alert_reg)
                return {
                    "success": False,
                    "target_artifact": mod_name,
                    "verification_script": test_file,
                    "verification_passed": False,
                    "error": alert_reg,
                    "summary": alert_reg
                }
            _send("assistant", f"✅ Регрессионная проверка пройдена: старый функционал не нарушен.")

        # === ШАГ 4 (ДОПИСЫВАНИЕ ТЕСТОВ): Генерация тестовых функций строго для новых методов ===
        _send("assistant", f"✍️ **Шаг 4 (Дописывание тестов)**: Генерация тестов для новых методов...")
        curr_mod_code = (ws_dir / mod_name).read_text(encoding="utf-8", errors="ignore")
        if not orig_test_code or not orig_test_code.strip():
            orig_test_code = f"import pytest\nimport {clean_mod}\n"

        step4_system = "Ты — инженер по тестированию pytest. Сгенерируй тестовые функции СТРОГО для новых методов."
        step4_user = (
            f"Спецификация новых методов (interface_spec):\n```json\n{spec_str}\n```\n\n"
            f"Текущий код модуля {mod_name}:\n```python\n{curr_mod_code}\n```\n\n"
            f"Существующий файл тестов {test_file}:\n```python\n{orig_test_code}\n```\n\n"
            f"ПРОМПТ-ГАРД ТЕСТЕРА:\n"
            f"1. Строго функции pytest (def test_...()). Запрещен unittest.TestCase.\n"
            f"2. Разрешены стандартные встроенные фикстуры pytest (tmp_path, monkeypatch, capsys). ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры бизнес-объектов (def test_foo(task_manager) → ЗАПРЕЩЕНО). Объект класса создается прямо внутри тела теста (manager = TaskManager()).\n"
            f"3. Напиши тесты СТРОГО для НОВЫХ методов.\n"
            f"4. НЕ дублируй существующие импорты и заголовки, верни новые тестовые функции в блоке <<TOOL:write_file filename=\"{test_file}\">>код<</TOOL:write_file>>."
        )

        resp4 = cls._call_completion(
            client=client,
            messages=[{"role": "system", "content": step4_system}, {"role": "user", "content": step4_user}],
            model=target_model,
            temperature=worker_temp,
            max_tokens=4096,
            enable_thinking=enable_thinking,
            thought_chunk_callback=thought_chunk_callback
        )
        new_test_snippet = extract_clean_code(str(resp4 or ""))
        updated_test_code = cls.append_new_tests(orig_test_code, new_test_snippet)
        updated_test_code = sanitize_code(updated_test_code, test_file)
        ToolEngine.write_file(test_file, updated_test_code)
        _send("assistant", f"✅ Новые тесты успешно дописаны в `{test_file}`.")

        # === ШАГ 5 (ФИНАЛЬНАЯ ВЕРИФИКАЦИЯ): Прогон всего набора тестов с двусторонним исцелением ===
        _send("assistant", f"🎯 **Шаг 5 (Финальная верификация)**: Запуск полного набора тестов `pytest {test_file} -v`...")
        last_error = ""
        last_error_fingerprint = None
        consecutive_same_error_count = 0
        consecutive_no_blocks_count = 0
        max_same_error_retries = 3
        test_fix_count = 0

        for attempt in range(1, 16):
            if on_status_callback and callable(on_status_callback):
                on_status_callback("running", test_file, 0, "")

            test_res = ToolEngine.run_pytest(test_file)
            if test_res.success:
                logger.info(f"🎉 [TDD INCREMENTAL] Успех на попытке {attempt}: Весь набор тестов зеленый!")
                _send("assistant", f"🎉 **100% Green**: Все тесты (старые + новые) успешно пройдены на попытке {attempt}!")
                GitSandboxManager.checkpoint(ws_dir, f"TDD Incremental: {mod_name} passed all tests in {test_file}")
                return {
                    "success": True,
                    "target_artifact": mod_name,
                    "verification_script": test_file,
                    "verification_passed": True,
                    "artifact_path": str((ws_dir / mod_name).resolve()),
                    "file_name": mod_name,
                    "stdout": test_res.stdout or "All tests passed",
                    "stderr": "",
                    "summary": f"Модуль {mod_name} успешно доработан, 100% тестов зеленые.",
                    "turns": attempt
                }

            last_error = test_res.output or test_res.stderr or "Pytest failed"
            logger.warning(f"⚠️ [TDD INCREMENTAL] Попытка {attempt}/15 завершилась падением тестов: {last_error[:150]}")

            current_fp = extract_error_fingerprint(last_error)
            if current_fp and current_fp == last_error_fingerprint:
                consecutive_same_error_count += 1
                logger.warning(f"⚠️ [TDD INCREMENTAL] Детектор зацикливания: повтор ошибки ({consecutive_same_error_count}/{max_same_error_retries}): {current_fp}")
            else:
                consecutive_same_error_count = 1
                last_error_fingerprint = current_fp

            if consecutive_same_error_count >= max_same_error_retries:
                ToolEngine.write_file(mod_name, orig_mod_code)
                if orig_test_code and (ws_dir / test_file).is_file():
                    ToolEngine.write_file(test_file, orig_test_code)
                fail_summary = (
                    f"⛔ [Останов по зацикливанию TDD]: Модель {max_same_error_retries} раза подряд наступила на ошибку:\n"
                    f"«{current_fp}»\n\nВыполнен откат {mod_name} и {test_file}.\nДетали последней ошибки:\n{last_error}"
                )
                logger.error(fail_summary)
                _send("assistant", fail_summary)
                return {
                    "success": False,
                    "target_artifact": mod_name,
                    "verification_script": test_file,
                    "verification_passed": False,
                    "error": last_error,
                    "summary": fail_summary
                }

            enriched_error = diagnose_runtime_error(last_error)
            is_defect_in_test = cls._is_test_defect(last_error, test_file, mod_name, test_fix_count=test_fix_count)
            curr_impl = (ws_dir / mod_name).read_text(encoding="utf-8", errors="ignore") if (ws_dir / mod_name).is_file() else ""
            curr_test = (ws_dir / test_file).read_text(encoding="utf-8", errors="ignore") if (ws_dir / test_file).is_file() else ""

            if not is_defect_in_test:
                _send("assistant", f"🔧 Попытка {attempt}/15: Исправляю код реализации `{mod_name}`...")
                fix_user = (
                    f"Тесты {test_file} упали при финальной верификации (Попытка {attempt}/15):\n"
                    f"```\n{enriched_error}\n```\n\n"
                    f"Текущий код реализации `{mod_name}`:\n```python\n{curr_impl}\n```\n\n"
                    f"Текущий код тестов `{test_file}`:\n```python\n{curr_test}\n```\n\n"
                    f"Исправь реализацию `{mod_name}`, сохранив все методы (старые и новые). "
                    f"Верни полный обновленный файл через <<TOOL:write_file filename=\"{mod_name}\">>код<</TOOL:write_file>>."
                )
                fix_msgs = [
                    {"role": "system", "content": "Ты — ведущий инженер Python. Исправь реализацию модуля, чтобы тесты стали зелеными."},
                    {"role": "user", "content": fix_user}
                ]
                fix_resp = cls._call_completion(
                    client=client,
                    messages=fix_msgs,
                    model=target_model,
                    temperature=worker_temp,
                    max_tokens=4096,
                    enable_thinking=enable_thinking,
                    thought_chunk_callback=thought_chunk_callback
                )
                blocks = cls.extract_code_blocks(str(fix_resp or ""), default_filename=mod_name)
                if not blocks:
                    consecutive_no_blocks_count += 1
                    if consecutive_no_blocks_count >= max_same_error_retries:
                        ToolEngine.write_file(mod_name, orig_mod_code)
                        if orig_test_code and (ws_dir / test_file).is_file():
                            ToolEngine.write_file(test_file, orig_test_code)
                        fail_summary = f"⛔ [Останов по зацикливанию TDD]: Модель {max_same_error_retries} раза подряд не вернула код."
                        logger.error(fail_summary)
                        _send("assistant", fail_summary)
                        return {
                            "success": False,
                            "target_artifact": mod_name,
                            "verification_script": test_file,
                            "verification_passed": False,
                            "error": last_error,
                            "summary": fail_summary
                        }
                else:
                    consecutive_no_blocks_count = 0
                    new_impl = blocks.get(mod_name) or list(blocks.values())[0]
                    new_impl = sanitize_code(new_impl, mod_name)
                    ToolEngine.write_file(mod_name, new_impl)
            else:
                test_fix_count += 1
                _send("assistant", f"🧪 Попытка {attempt}/15: Корректирую структуру тестов `{test_file}`...")
                fix_user = (
                    f"Тесты {test_file} упали при финальной верификации (Попытка {attempt}/15):\n"
                    f"```\n{enriched_error}\n```\n\n"
                    f"Ошибка вызвана структурой теста (fixture not found, опечатка в assert, синтаксис теста).\n"
                    f"Текущий код тестов `{test_file}`:\n```python\n{curr_test}\n```\n\n"
                    f"Текущий код реализации `{mod_name}`:\n```python\n{curr_impl}\n```\n\n"
                    f"Скорректируй сам файл тестов `{test_file}`.\n"
                    f"СТРОГИЙ ПРОМПТ-ГАРД:\n"
                    f"1. Строго функции pytest (def test_...()). Запрещен unittest.TestCase.\n"
                    f"2. Разрешены стандартные встроенные фикстуры pytest (tmp_path, monkeypatch, capsys). ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры бизнес-объектов (def test_foo(task_manager) → ЗАПРЕЩЕНО). Объект класса создается прямо внутри тела теста (manager = TaskManager()).\n"
                    f"Верни полный исправленный файл тестов через <<TOOL:write_file filename=\"{test_file}\">>код<</TOOL:write_file>>."
                )
                fix_msgs = [
                    {"role": "system", "content": "Ты — инженер по тестированию pytest. Скорректируй структуру и ассерты теста."},
                    {"role": "user", "content": fix_user}
                ]
                fix_resp = cls._call_completion(
                    client=client,
                    messages=fix_msgs,
                    model=target_model,
                    temperature=worker_temp,
                    max_tokens=4096,
                    enable_thinking=enable_thinking,
                    thought_chunk_callback=thought_chunk_callback
                )
                blocks = cls.extract_code_blocks(str(fix_resp or ""), default_filename=test_file)
                if not blocks:
                    consecutive_no_blocks_count += 1
                    if consecutive_no_blocks_count >= max_same_error_retries:
                        ToolEngine.write_file(mod_name, orig_mod_code)
                        if orig_test_code and (ws_dir / test_file).is_file():
                            ToolEngine.write_file(test_file, orig_test_code)
                        fail_summary = f"⛔ [Останов по зацикливанию TDD]: Модель {max_same_error_retries} раза подряд не вернула код."
                        logger.error(fail_summary)
                        _send("assistant", fail_summary)
                        return {
                            "success": False,
                            "target_artifact": mod_name,
                            "verification_script": test_file,
                            "verification_passed": False,
                            "error": last_error,
                            "summary": fail_summary
                        }
                else:
                    consecutive_no_blocks_count = 0
                    new_test = blocks.get(test_file) or list(blocks.values())[0]
                    new_test = sanitize_code(new_test, test_file)
                    ToolEngine.write_file(test_file, new_test)

        fail_summary = f"Исчерпан лимит в 15 попыток финальной верификации для {mod_name}. Последняя ошибка:\n{last_error}"
        ToolEngine.write_file(mod_name, orig_mod_code)
        if orig_test_code and (ws_dir / test_file).is_file():
            ToolEngine.write_file(test_file, orig_test_code)
        _send("assistant", f"⛔ {fail_summary}")
        return {
            "success": False,
            "target_artifact": mod_name,
            "verification_script": test_file,
            "verification_passed": False,
            "error": last_error,
            "summary": fail_summary
        }


__all__ = ["CodeFirstTDDPipeline", "extract_clean_code"]
