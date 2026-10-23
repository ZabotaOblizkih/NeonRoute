"""
Unit tests for SubagentEngineer autonomous cycle refactoring.
Tests error fingerprinting, test syntax validation exemption,
loop detection, and context compaction.
"""

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from services.subagent_engineer import SubagentEngineer


class TestSubagentEngineerAutonomousCycle(unittest.TestCase):

    def test_extract_error_fingerprint_empty(self):
        self.assertEqual(SubagentEngineer._extract_error_fingerprint(""), "")
        self.assertEqual(SubagentEngineer._extract_error_fingerprint(None), "")
        self.assertEqual(SubagentEngineer._extract_error_fingerprint("   \n  \n"), "")

    def test_extract_error_fingerprint_test_failures(self):
        output = (
            "test_addition (test_calc.TestCalc) ... FAIL\n"
            "FAIL: test_addition (test_calc.TestCalc)\n"
            "AssertionError: 2 != 3\n"
            "FAILED (failures=1)"
        )
        fp = SubagentEngineer._extract_error_fingerprint(output)
        self.assertTrue(fp.startswith("TEST_FAIL::test_addition::"))
        self.assertIn("FAILED (failures=1)", fp)

    def test_extract_error_fingerprint_traceback(self):
        output = (
            "Traceback (most recent call last):\n"
            '  File "C:\\NeonRoute\\main.py", line 42, in <module>\n'
            "    run_app()\n"
            '  File "C:\\NeonRoute\\engine.py", line 105, in run_app\n'
            "    raise ValueError('Invalid calculation parameter')\n"
            "ValueError: Invalid calculation parameter"
        )
        fp = SubagentEngineer._extract_error_fingerprint(output)
        self.assertEqual(fp, "engine.py:105::ValueError: Invalid calculation parameter")

    def test_extract_error_fingerprint_plain_error(self):
        output = "SyntaxError на строке 15: invalid syntax\nСтрока с ошибкой: def broken("
        fp = SubagentEngineer._extract_error_fingerprint(output)
        self.assertEqual(fp, "Строка с ошибкой: def broken(")

    def test_syntax_validator_exempts_tests(self):
        test_code = (
            "import unittest\n\n"
            "class MyTest(unittest.TestCase):\n"
            "    def test_something(self):\n"
            "        self.assertTrue(True)\n"
        )
        # Entry check is bypassed for test files
        valid, err = SubagentEngineer._validate_python_syntax("test_my_logic.py", test_code, is_entry_file=True)
        self.assertTrue(valid, f"Expected test_my_logic.py to pass validation: {err}")

        valid2, err2 = SubagentEngineer._validate_python_syntax("logic_test.py", test_code, is_entry_file=True)
        self.assertTrue(valid2, f"Expected logic_test.py to pass validation: {err2}")

        # Normal script without main block should fail entry check
        normal_code = (
            "class MyService:\n"
            "    def do_work(self):\n"
            "        pass\n"
        )
        valid3, err3 = SubagentEngineer._validate_python_syntax("service.py", normal_code, is_entry_file=True)
        self.assertFalse(valid3)
        self.assertIn("MissingEntryPoint", err3 or "")

    def test_loop_detection_terminates_at_max_same_error_retries(self):
        # Mock client that repeats the same broken code
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = (
            '<<TOOL:write_file filename="app.py">>\n'
            'class BrokenApp:\n'
            '    def run(self):\n'
            '        pass\n'
            '<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="app.py">>'
        )

        with patch("utils.config_manager.ConfigManager.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "engineer_settings": {
                    "max_turns": 10,
                    "max_same_error_retries": 3
                }
            }
            # We don't want architect to run LLM
            res = SubagentEngineer.run_worker(
                task_prompt="Сделай приложение app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            self.assertFalse(res["success"])
            self.assertIn("Останов по зацикливанию", res["summary"])
            self.assertIn("3 раза подряд наступил на идентичную ошибку", res["summary"])

    def test_max_turns_exceeded(self):
        # Architect response followed by different syntax errors on each turn
        responses = [
            '{"intent": "new", "target_file": "app.py", "plan": "plan"}',
            '<<TOOL:write_file filename="app.py">>\ndef f1():\n    undefined_var_1\n<</TOOL:write_file>>\n<<TOOL:run_script filename="app.py">>',
            '<<TOOL:write_file filename="app.py">>\ndef f2():\n    undefined_var_2\n<</TOOL:write_file>>\n<<TOOL:run_script filename="app.py">>',
        ]
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = responses

        with patch("utils.config_manager.ConfigManager.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "engineer_settings": {
                    "max_turns": 2,
                    "max_same_error_retries": 5
                }
            }
            res = SubagentEngineer.run_worker(
                task_prompt="Сделай приложение app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            self.assertFalse(res["success"])
            self.assertIn("Достигнут максимальный лимит шагов задачи (2)", res["summary"])

    def test_context_compaction_structure(self):
        # Verify that messages list passed to chat_completion on turn 2 has compacted structure
        recorded_calls = []

        def fake_chat(messages, **kwargs):
            recorded_calls.append(list(messages))
            call_idx = len(recorded_calls)
            return (
                f'<<TOOL:write_file filename="app.py">>\ndef f_{call_idx}():\n    undefined_{call_idx}\n<</TOOL:write_file>>\n'
                f'<<TOOL:run_script filename="app.py">>'
            )

        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = fake_chat

        with patch("utils.config_manager.ConfigManager.load_config") as mock_cfg:
            mock_cfg.return_value = {
                "engineer_settings": {
                    "max_turns": 2,
                    "max_same_error_retries": 5
                }
            }
            SubagentEngineer.run_worker(
                task_prompt="Создай модуль app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

        # We had 2 turns of engineer
        # First call was architect, then engineer turn 1, then engineer turn 2
        # Let's inspect the engineer calls:
        eng_calls = [c for c in recorded_calls if any("Элария" in str(m.get("content")) for m in c)]
        self.assertGreaterEqual(len(eng_calls), 2)
        second_turn_msgs = eng_calls[1]

        # Context compaction check:
        # messages[0]: system prompt
        # messages[1]: initial task prompt + architect plan
        # messages[-2]: assistant single-sentence acknowledgement (echo cleaned)
        # messages[-1]: latest error and fix instruction
        self.assertEqual(len(second_turn_msgs), 4)
        self.assertEqual(second_turn_msgs[0]["role"], "system")
        self.assertEqual(second_turn_msgs[1]["role"], "user")
        self.assertIn("Создай модуль app.py", second_turn_msgs[1]["content"])
        self.assertEqual(second_turn_msgs[-2]["role"], "assistant")
        self.assertIn("устраняю", second_turn_msgs[-2]["content"])
        self.assertEqual(second_turn_msgs[-1]["role"], "user")
        self.assertIn("СИНТАКСИЧЕСКАЯ ОШИБКА В КОДЕ", second_turn_msgs[-1]["content"])


if __name__ == "__main__":
    unittest.main()

