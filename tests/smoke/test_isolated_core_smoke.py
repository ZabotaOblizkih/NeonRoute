import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import config
from utils.file_utils import atomic_write_json
from services.vram_arbiter import VRAMArbiter
from services.layer_budgeter import LayerBudgeter
from services.thought_stream_parser import ThoughtStreamParser
from services.code_validator import CodeValidator
from services.sandbox_manager import SandboxManager
from services.session_manager import SessionManager
from services.hybrid_search import HybridSearchEngine
from services.roleplay_state_manager import RoleplayStateManager
from services.tavern_parser import TavernParser
from services.token_budget_manager import TokenBudgetManager
from services.mode_coordinator import ModeCoordinator, AppMode
from PyQt6.QtWidgets import QApplication, QWidget
from ui.common.ui_context import UIContext


def test_01_acid_atomic_write_json():
    test_json = config.DATA_DIR / "acid_smoke.json"
    assert atomic_write_json(test_json, {"isolated": True}) is True
    assert test_json.exists()


def test_02_vram_arbiter_exclusivity():
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    # 1. Захват GPU под LLM: выгружает Forge и переключает владельца
    arbiter.prepare_for_task("llm")
    assert arbiter._current_owner == "llm"
    assert mock_forge.unload_checkpoint.called
    assert mock_forge.free_vram.called

    # 2. Переключение на диффузию: синхронно выгружает LLM и ждет освобождения VRAM
    arbiter.release_llm_for_diffusion()
    assert arbiter._current_owner == "diffusion"
    assert mock_lm.unload_models_cli.called
    assert mock_forge._wait_for_vram_clear.called

    # 3. Защита от преждевременной выгрузки LLM для задач генерации арт-промпта
    mock_lm.reset_mock()
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": True})
    assert not mock_lm.unload_models_cli.called


def test_03_layer_budgeter_kv_math(tmp_path):
    kv_mb_q8 = LayerBudgeter.estimate_kv_cache_mb(14336, "q8_0")
    kv_mb_fp16 = LayerBudgeter.estimate_kv_cache_mb(14336, "f16")
    assert kv_mb_fp16 > kv_mb_q8 > 0

    # Проверка can_run (OOM Guard) в LayerBudgeter
    dummy_gguf = tmp_path / "Qwen-2.5-VL-7B.gguf"
    dummy_gguf.write_bytes(b"0" * (50 * 1024 * 1024))  # 50 MB файл
    ngl_oom, can_run_oom, reason_oom, audit_oom = LayerBudgeter.calculate_layer_offload(
        model_path=str(dummy_gguf),
        target_context=4096,
        total_vram_mb=100.0,
        available_ram_mb=100.0
    )
    assert can_run_oom is False and audit_oom.get("can_run") is False
    assert ngl_oom == 0 and "OOM Guard" in reason_oom

    # Проверка norm_model (Case 2 slug match) в ServerConfigResolver
    from services.server_config import ServerConfigResolver
    other_gguf = tmp_path / "Llama-3-8B.gguf"
    other_gguf.write_bytes(b"0" * 1024)
    mmproj_cand = tmp_path / "mmproj-qwen25vl-f16.gguf"
    mmproj_cand.write_bytes(b"0" * 1024)
    resolved_mmproj = ServerConfigResolver.resolve_mmproj_for_model(dummy_gguf)
    assert resolved_mmproj is not None and Path(resolved_mmproj).name == "mmproj-qwen25vl-f16.gguf"


def test_04_thought_stream_parser_chunk_boundaries():
    tp = ThoughtStreamParser()
    tp.feed("<thi")
    tp.feed("nk>Скрытая мысль")
    tp.feed("</think>Чистый ответ")
    tp.finalize()
    td = tp.get_thought_data()
    assert td and "Скрытая мысль" in td.get("content", str(td))


def test_05_code_validator_ast_and_entry_guards():
    lib_code = "def calc(x: int) -> int:\n    return x * 2\n"
    assert CodeValidator.validate_python_syntax("module.py", lib_code, is_entry_file=False)[0] is True
    assert CodeValidator.validate_python_syntax("main.py", lib_code, is_entry_file=True)[0] is False
    entry_code = "def calc(x: int) -> int:\n    return x * 2\n\nif __name__ == '__main__':\n    print(calc(21))\n"
    assert CodeValidator.validate_python_syntax("main.py", entry_code, is_entry_file=True)[0] is True
    assert CodeValidator.validate_python_syntax("hack.py", "import subprocess\n", is_entry_file=False)[0] is False


def test_06_sandbox_manager_isolated_execution():
    entry_code = "def calc(x: int) -> int:\n    return x * 2\n\nif __name__ == '__main__':\n    print(calc(21))\n"
    with SandboxManager.session_scope("smoke_eng_01"):
        ws_dir = SandboxManager.get_workspace_dir()
        assert "pytest_airgap_sandbox" in str(ws_dir)
        report = SandboxManager.write_file("hello_smoke.py", entry_code)
        assert "[Успешно]" in report and (ws_dir / "hello_smoke.py").exists()
        exec_res = SandboxManager.run_script("hello_smoke.py", timeout=10)
        assert exec_res.success is True and exec_res.return_code == 0 and "42" in exec_res.stdout


def test_07_session_manager_acid_lifecycle():
    sess = SessionManager.create_session(mode="assistant", title="Smoke Test Chat")
    sess_id = sess["id"]
    saved_path = SessionManager.save_session(sess, trigger_memory=False)
    assert saved_path and "pytest_airgap_sandbox" in str(saved_path)
    loaded = SessionManager.load_session(sess_id)
    assert loaded and loaded["title"] == "Smoke Test Chat"


def test_08_ui_context_ssot_headless():
    app = QApplication.instance() or QApplication([])
    ui_ctx = UIContext()
    dummy_bubble = QWidget()
    ui_ctx.begin_turn(dummy_bubble)
    assert ui_ctx.is_busy is True and ui_ctx.turn_bubble is dummy_bubble
    ui_ctx.finish_turn()
    assert ui_ctx.is_busy is False and ui_ctx.turn_bubble is None


def test_09_hybrid_search_engine_in_memory():
    rag_engine = HybridSearchEngine(dense_vectorizer=False)
    chunks = rag_engine.chunk_text(
        "Артикул детали NR-9090 отвечает за охлаждение квантового ядра NeonRoute.",
        filename="manual_spec.md"
    )
    rag_engine.index_chunks(chunks)
    hits = rag_engine.search("Артикул NR-9090 охлаждение", top_k=1)
    assert len(hits) >= 1 and "NR-9090" in hits[0].get("text", str(hits[0]))


def test_10_roleplay_state_and_archetype_detection():
    rp_text = "Герой вошел в зал.\n<stats>\nЛокация: Цитадель Ночи\nКвест: Найти ключ\nИнвентарь: Факел, Клинок\n</stats>"
    parsed_status = RoleplayStateManager.parse_explicit_status(rp_text)
    assert parsed_status and ("Цитадель" in str(parsed_status) or len(parsed_status) > 0)
    arch_info = TavernParser.detect_card_archetype({
        "name": "System Guide",
        "description": "Система уровней, характеристик, квестов и инвентаря игрока [STATUS] Level Up LitRPG."
    })
    assert isinstance(arch_info, dict) and arch_info.get("is_system") is True


def test_11_token_budget_emergency_vram_guard():
    tier_normal = TokenBudgetManager.calculate_context_budget(2000, mode="assistant", free_vram_mb=2000)
    tier_emergency = TokenBudgetManager.calculate_context_budget(2000, mode="assistant", free_vram_mb=100)
    assert tier_emergency.max_tokens <= tier_normal.max_tokens
    assert tier_emergency.max_tokens == 512


def test_12_mode_coordinator_fsm_transitions():
    mock_mw = MagicMock()
    mock_mw.chat_history = [{"role": "user", "content": "hi"}]
    coord = ModeCoordinator(mock_mw)
    try:
        assert coord.transition_to(AppMode.ENGINEER) is True
        assert coord.transition_to(AppMode.ROLEPLAY) is True
    finally:
        SandboxManager.set_active_session_id(None)
