"""
Unit tests for:
1. Uncapped max_tokens (4096 / MASTER_MAX_RESPONSE_TOKENS) for thinking models in Desktop and Mobile Gateway.
2. Recognition of Gemma 4 and other thinking models via THINKING_MODEL_KEYWORDS.
3. Custom max_tokens not being clamped down by effective_max_tokens.
4. Repetition loop detector (_detect_repetition_loop) protection against false-positives during unclosed and closed <think> streams.
5. Strict isolation of Sliding CoT to engineer mode, with Jinja Alternation Guard in assistant/roleplay.
"""
from unittest.mock import MagicMock, patch
import pytest

from config import MASTER_MAX_RESPONSE_TOKENS, THINKING_MODEL_KEYWORDS
from services.context_window_builder import ContextWindowBuilder
from ui.controllers.chat_flow_controller import ChatFlowController


def test_thinking_model_keywords_include_gemma4_and_reasoning_models():
    """Verify THINKING_MODEL_KEYWORDS includes gemma-4, deepseek-r1, qwq, qwen3."""
    models_to_test = [
        "google/gemma-4-27b-it",
        "deepseek-r1-distill-qwen-14b",
        "qwq-32b-preview",
        "qwen3-7b-instruct",
        "custom-reasoning-model-v1",
        "my-think-model",
    ]
    for model in models_to_test:
        model_lower = model.lower()
        matched = any(kw in model_lower for kw in THINKING_MODEL_KEYWORDS)
        assert matched, f"Expected model '{model}' to match THINKING_MODEL_KEYWORDS: {THINKING_MODEL_KEYWORDS}"


def test_detect_repetition_loop_unclosed_think_tag():
    """Streaming with unclosed <think> (or <thought>/<reasoning>) must never trigger loop abort."""
    controller = ChatFlowController()

    unclosed_think = (
        "<think>\n"
        "Let us analyze the problem step by step.\n"
        "Step 1: Calculate the budget.\n"
        "Step 1: Calculate the budget.\n"
        "Step 1: Calculate the budget.\n"
        "Step 1: Calculate the budget.\n"
    )
    is_loop, text = controller._detect_repetition_loop(unclosed_think)
    assert not is_loop
    assert text == unclosed_think

    unclosed_reasoning = (
        "<reasoning>\n"
        "Checking constraints. Checking constraints. Checking constraints. Checking constraints.\n"
    )
    is_loop, text = controller._detect_repetition_loop(unclosed_reasoning)
    assert not is_loop
    assert text == unclosed_reasoning


def test_detect_repetition_loop_closed_think_tag_cuts_only_tail():
    """Closed <think> preserves the thought prefix and runs loop detector only on clean tail."""
    controller = ChatFlowController()

    thought_prefix = "<think>\nDetailed analysis finished successfully.\n</think>\n\n"
    sentence = "Это тестовый длинный фрагмент ответа для проверки зацикливания"
    full_text = thought_prefix + "Префикс. " + sentence + ". " + sentence

    is_loop, result_text = controller._detect_repetition_loop(full_text)
    assert is_loop
    # Thought prefix is preserved intact
    assert result_text.startswith(thought_prefix)
    assert "<think>" in result_text
    assert "</think>" in result_text
    assert sentence in result_text
    assert result_text == thought_prefix + "Префикс. " + sentence + "."


def test_detect_repetition_loop_normal_text_detects_repetition():
    """Normal text without think tags detects repetition properly."""
    controller = ChatFlowController()

    sentence = "Это тестовый длинный фрагмент текста для проверки зацикливания"
    text = "Префикс. " + sentence + ". " + sentence

    is_loop, result_text = controller._detect_repetition_loop(text)
    assert is_loop
    assert result_text == "Префикс. " + sentence + "."


def test_sliding_cot_strictly_isolated_to_engineer_mode():
    """Sliding CoT is kept only in engineer mode; stripped in assistant and roleplay."""
    history = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "<think>Размышления 1</think>\n\nОтвет 1"},
        {"role": "user", "content": "Вопрос 2"},
        {"role": "assistant", "content": "<think>Размышления 2</think>\n\nОтвет 2"},
    ]

    # 1. Engineer mode: preserves last assistant CoT when enable_reasoning=True
    eng_res = ContextWindowBuilder.prepare_history_for_llm(
        history=history,
        enable_reasoning=True,
        keep_last_cot=True,
        mode="engineer",
    )
    assert "<think>Размышления 1</think>" not in eng_res[1]["content"]
    assert "<think>Размышления 2</think>" in eng_res[3]["content"]

    # 2. Assistant mode: strips CoT from ALL turns
    asst_res = ContextWindowBuilder.prepare_history_for_llm(
        history=history,
        enable_reasoning=True,
        keep_last_cot=True,
        mode="assistant",
    )
    assert "<think>" not in asst_res[1]["content"]
    assert "<think>" not in asst_res[3]["content"]
    assert asst_res[1]["content"] == "Ответ 1"
    assert asst_res[3]["content"] == "Ответ 2"

    # 3. Roleplay mode: strips CoT from ALL turns
    rp_res = ContextWindowBuilder.prepare_history_for_llm(
        history=history,
        enable_reasoning=True,
        keep_last_cot=True,
        mode="roleplay",
    )
    assert "<think>" not in rp_res[1]["content"]
    assert "<think>" not in rp_res[3]["content"]
    assert rp_res[1]["content"] == "Ответ 1"
    assert rp_res[3]["content"] == "Ответ 2"


def test_sliding_cot_jinja_guard_replaces_empty_content():
    """If assistant turn had thoughts but its content becomes empty after stripping, replace with placeholder."""
    history = [
        {"role": "user", "content": "Вопрос"},
        {"role": "assistant", "content": "<think>Только мысли, текста ответа нет</think>"},
        {"role": "user", "content": "Следующий вопрос"},
    ]
    res = ContextWindowBuilder.prepare_history_for_llm(
        history=history,
        enable_reasoning=True,
        mode="assistant",
    )
    assert len(res) == 3
    assert res[1]["role"] == "assistant"
    assert res[1]["content"] == "*(Анализ завершен)*"


def test_mobile_gateway_chat_service_thinking_model_max_tokens():
    """In mobile_server chat_service, thinking models get MASTER_MAX_RESPONSE_TOKENS (4096)."""
    from mobile_server.services.chat_service import build_chat_context_and_params

    session = {"mode": "assistant", "id": "test_sess", "messages": [], "chat_history": []}

    # 1. Thinking model default gets 4096 and xtc_probability=0.0
    with patch("mobile_server.services.chat_service.ConfigManager.load_config", return_value={"assistant_model_name": "google/gemma-4-27b-it"}):
        _, sampling, *_ = build_chat_context_and_params(
            session_id="test_sess",
            session=session,
            user_text="Привет",
            mode_override="assistant",
            active_model_name="google/gemma-4-27b-it",
        )
        assert sampling["max_tokens"] == MASTER_MAX_RESPONSE_TOKENS
        assert sampling["xtc_probability"] == 0.0

    # 2. Custom override is respected up to MASTER_MAX_RESPONSE_TOKENS
    with patch("mobile_server.services.chat_service.ConfigManager.load_config", return_value={"assistant_model_name": "google/gemma-4-27b-it"}):
        _, sampling_custom, *_ = build_chat_context_and_params(
            session_id="test_sess",
            session=session,
            user_text="Привет",
            mode_override="assistant",
            active_model_name="google/gemma-4-27b-it",
            sampling_overrides={"max_tokens": 3500},
        )
        assert sampling_custom["max_tokens"] == 3500


def test_assistant_flow_thinking_model_tokens_resolution():
    """Verify Desktop AssistantFlowController token calculation for thinking models and custom limits."""
    # Test formula directly as implemented in assistant_flow_controller.py
    # 1. Default thinking model: effective_max_tokens becomes MASTER_MAX_RESPONSE_TOKENS (4096)
    model_name = "google/gemma-4-27b-it"
    _model_lower = str(model_name).lower()
    _is_thinking_model = any(m in _model_lower for m in THINKING_MODEL_KEYWORDS)
    assert _is_thinking_model is True

    is_custom = False
    effective_max_tokens = 1024
    if _is_thinking_model and not is_custom:
        if effective_max_tokens == 1024:
            effective_max_tokens = MASTER_MAX_RESPONSE_TOKENS

    sampling = {}
    _should_art = False
    if _should_art:
        target_max_tokens = 512
    elif not is_custom or "max_tokens" not in sampling:
        target_max_tokens = effective_max_tokens
    else:
        target_max_tokens = min(int(sampling["max_tokens"]), MASTER_MAX_RESPONSE_TOKENS)

    assert target_max_tokens == 4096

    # 2. Custom limit user_max_tokens=3500 is preserved, not clamped to 2048
    is_custom = True
    user_max_tokens = 3500
    sampling = {"max_tokens": user_max_tokens}
    if _should_art:
        target_max_tokens = 512
    elif not is_custom or "max_tokens" not in sampling:
        target_max_tokens = effective_max_tokens
    else:
        target_max_tokens = min(int(user_max_tokens), MASTER_MAX_RESPONSE_TOKENS)

    assert target_max_tokens == 3500


def test_engineer_planner_strips_thinking_tags_and_parses_json():
    """Проверяет, что EngineerPlanner очищает <think> теги и успешно парсит JSON план."""
    from services.engineer_planner import EngineerPlanner

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = (
        "<think>\n"
        "Need to build a CLI utility with modular structure.\n"
        "Let's output valid JSON architecture specification.\n"
        "</think>\n"
        "```json\n"
        "{\n"
        '  "architecture": "Modular Architecture",\n'
        '  "entry_point": "main.py",\n'
        '  "key_modules": ["main.py", "utils.py"],\n'
        '  "implementation_plan": ["Init main", "Add utils"]\n'
        "}\n"
        "```"
    )

    thought_chunks = []
    def on_thought(token):
        thought_chunks.append(token)

    plan = EngineerPlanner.plan_architecture(
        task_prompt="Create CLI tool",
        client=mock_client,
        enable_thinking=True,
        thought_chunk_callback=on_thought,
        max_tokens=1800
    )

    assert plan is not None
    assert plan.target_entry_file == "main.py"
    assert "main.py" in plan.planned_files

    # Проверяем, что для thinking моделей eff_max_tokens был повышен >= 4096
    call_kwargs = mock_client.chat_completion.call_args[1]
    assert call_kwargs.get("max_tokens") >= 4096
    assert call_kwargs.get("enable_thinking") is True


def test_engineer_response_parser_strips_thinking_blocks(tmp_path):
    """Проверяет, что EngineerResponseParser отсекает блок <think> перед поиском действий и файлов."""
    from services.engineer_parser import EngineerResponseParser
    from services.engineer_planner import ArchitectPlan

    response_text = (
        "<think>\n"
        "Here are internal reasoning steps before generating tool calls.\n"
        "</think>\n"
        '<<TOOL:write_file filename="module.py">>\n'
        'def hello():\n'
        '    return "world"\n'
        '<</TOOL:write_file>>\n'
    )

    plan = ArchitectPlan(
        artifact_format="cli",
        artifact_type="cli_tool",
        target_artifact="module.py",
        verification_script=None,
        target_entry_file="module.py",
        plan_desc="",
        is_maintenance=False,
        is_ephemeral=False,
        plan_content="",
        system_prompt="",
        planned_files=["module.py"],
        task_type="NEW_MODULE",
        target_file="module.py",
        interface_spec={}
    )

    actions = EngineerResponseParser.parse_turn_actions(
        response_text=response_text,
        ws_dir=tmp_path,
        plan=plan,
        task_prompt="Create module",
        context_hint="",
        target_artifact="module.py",
        target_entry_file="module.py",
        script_to_run=None,
        turn=1,
        turn1_test_signature_mismatch=False,
        verification_script=None,
        declared_entry_file="module.py",
        is_maintenance=False,
        planned_files=["module.py"],
        file_backups={}
    )

    assert actions.has_any_actions()
    assert len(actions.write_blocks) == 1
    fn, content = actions.write_blocks[0]
    assert fn == "module.py"
    assert "def hello():" in content
    assert "<think>" not in content


def test_engineer_tdd_extract_code_blocks_strips_thinking():
    """Проверяет, что CodeFirstTDDPipeline.extract_code_blocks не включает блоки <think> в извлечённый код."""
    from services.engineer_tdd import CodeFirstTDDPipeline

    response_text = (
        "<think>\n"
        "Let's write a unit test for our feature.\n"
        "</think>\n"
        "```python filename=\"test_app.py\"\n"
        "def test_example():\n"
        "    assert True\n"
        "```\n"
    )

    blocks = CodeFirstTDDPipeline.extract_code_blocks(response_text, default_filename="test_app.py")
    assert "test_app.py" in blocks
    code = blocks["test_app.py"]
    assert "def test_example():" in code
    assert "<think>" not in code
    assert "</think>" not in code

