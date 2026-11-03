import os
import sys
import tempfile
import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock

# 1. Включаем Headless-режим для Qt ДО любых импортов UI
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 2. Перехватываем фоновые daemon-потоки прогрева эмбеддингов, чтобы смок-тесты
# работали мгновенно (0 мс на ONNX/LanceDB) и не трогали боевую векторную БД
_orig_thread_start = threading.Thread.start
def _safe_thread_start(self, *args, **kwargs):
    target_name = getattr(getattr(self, "_target", None), "__name__", "")
    if getattr(self, "name", "") == "FastEmbedWarmup" or target_name in ("_get_embedding_engine", "_run", "warmup_memory_engine"):
        return
    return _orig_thread_start(self, *args, **kwargs)
threading.Thread.start = _safe_thread_start

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def snapshot_prod_state(root: Path) -> dict:
    """Снимает слепок состояния критичных боевых файлов и папок."""
    state = {}
    for rel in ["config.json", "data/memory.db", "data/user_profiles.json", "knowledge_base/registry.json"]:
        p = root / rel
        state[rel] = p.stat().st_mtime_ns if p.exists() else None
    for folder in ["history", "workspace", "outputs"]:
        d = root / folder
        state[folder] = sorted([x.name for x in d.iterdir()]) if d.exists() else []
    return state

def redirect_config_to_temp(temp_root: Path):
    """Перенаправляет ВСЕ пути записи в config.py во временный полигон ДО импорта сервисов."""
    import config
    mapping = {
        "CONFIG_DIR": temp_root / "config",
        "CONFIG_PATH": temp_root / "config.json",
        "DATA_DIR": temp_root / "data",
        "HISTORY_DIR": temp_root / "history",
        "WORKSPACE_DIR": temp_root / "workspace",
        "OUTPUTS_DIR": temp_root / "outputs",
        "LOGS_DIR": temp_root / "logs",
        "EXPORTS_DIR": temp_root / "exports",
        "KNOWLEDGE_BASE_DIR": temp_root / "knowledge_base",
        "KNOWLEDGE_REGISTRY_PATH": temp_root / "knowledge_base" / "registry.json",
        "MEMORY_DB_PATH": temp_root / "data" / "memory.db",
        "ART_DIRECTOR_MODELS_PATH": temp_root / "data" / "art_director_models.json",
        "LORA_REGISTRY_PATH": temp_root / "data" / "lora_registry.json",
    }
    for attr, target_path in mapping.items():
        setattr(config, attr, target_path)
        if attr.endswith("_DIR"):
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)

def run_smoke_suite():
    temp_root = Path(tempfile.mkdtemp(prefix="neonroute_airgap_"))
    print(f"[*] Создан изолированный полигон: {temp_root}")
    prod_before = snapshot_prod_state(PROJECT_ROOT)

    try:
        redirect_config_to_temp(temp_root)

        # Отключаем тяжелый LanceDB/ONNX в memory_db, оставляя чистый SQLite в Temp
        import services.memory_db as mdb
        mdb._EMBED_MODEL = False
        mdb._LANCE_TABLE = False
        mdb._RP_LANCE_TABLE = False

        # --- СМОК 1: ACID-запись JSON (utils.file_utils) ---
        from utils.file_utils import atomic_write_json
        import config
        test_json = config.DATA_DIR / "acid_smoke.json"
        assert atomic_write_json(test_json, {"isolated": True}) is True
        assert test_json.exists()
        print("[OK] 1/12 ACID atomic_write_json (строго в Temp)")

        # --- СМОК 2: VRAMArbiter (взаимное исключение LLM и Forge) ---
        from services.vram_arbiter import VRAMArbiter
        arbiter = VRAMArbiter(lm_client=MagicMock(), forge_client=MagicMock())
        arbiter.prepare_for_task("llm_chat")
        arbiter.release_llm_for_diffusion()
        print("[OK] 2/12 VRAMArbiter (prepare_for_task / release_llm_for_diffusion)")

        # --- СМОК 3: LayerBudgeter (математика KV-кэша) ---
        from services.layer_budgeter import LayerBudgeter
        kv_mb_q8 = LayerBudgeter.estimate_kv_cache_mb(14336, "q8_0")
        kv_mb_fp16 = LayerBudgeter.estimate_kv_cache_mb(14336, "f16")
        assert kv_mb_fp16 > kv_mb_q8 > 0
        print(f"[OK] 3/12 LayerBudgeter (KV 14K: q8_0={kv_mb_q8} MB, f16={kv_mb_fp16} MB)")

        # --- СМОК 4: ThoughtStreamParser (потоковый парсер <think>) ---
        from services.thought_stream_parser import ThoughtStreamParser
        tp = ThoughtStreamParser()
        tp.feed("<thi")
        tp.feed("nk>Скрытая мысль")
        tp.feed("</think>Чистый ответ")
        tp.finalize()
        td = tp.get_thought_data()
        assert td and "Скрытая мысль" in td.get("content", str(td))
        print("[OK] 4/12 ThoughtStreamParser (границы чанков <thi + nk> обработаны)")

        # --- СМОК 5: CodeValidator (EntryPoint Guard + AST Safety) ---
        from services.code_validator import CodeValidator
        lib_code = "def calc(x: int) -> int:\n    return x * 2\n"
        assert CodeValidator.validate_python_syntax("module.py", lib_code, is_entry_file=False)[0] is True
        assert CodeValidator.validate_python_syntax("main.py", lib_code, is_entry_file=True)[0] is False
        entry_code = "def calc(x: int) -> int:\n    return x * 2\n\nif __name__ == '__main__':\n    print(calc(21))\n"
        assert CodeValidator.validate_python_syntax("main.py", entry_code, is_entry_file=True)[0] is True
        assert CodeValidator.validate_python_syntax("hack.py", "import subprocess\n", is_entry_file=False)[0] is False
        print("[OK] 5/12 CodeValidator (EntryPoint Guard, Lib Mode и AST Safety)")

        # --- СМОК 6: SandboxManager (изолированная запись и запуск в Temp) ---
        from services.sandbox_manager import SandboxManager
        SandboxManager.set_active_session_id("smoke_eng_01")
        ws_dir = SandboxManager.get_workspace_dir()
        assert str(temp_root) in str(ws_dir)
        report = SandboxManager.write_file("hello_smoke.py", entry_code)
        assert "[Успешно]" in report and (ws_dir / "hello_smoke.py").exists()
        exec_res = SandboxManager.run_script("hello_smoke.py", timeout=10)
        assert exec_res.success is True and exec_res.return_code == 0 and "42" in exec_res.stdout
        print(f"[OK] 6/12 SandboxManager (изолированный запуск -> stdout={exec_res.stdout.strip()})")

        # --- СМОК 7: SessionManager (создание, сохранение и чтение сессии в Temp) ---
        from services.session_manager import SessionManager
        sess = SessionManager.create_session(mode="assistant", title="Smoke Test Chat")
        sess_id = sess["id"]
        saved_path = SessionManager.save_session(sess, trigger_memory=False)
        assert saved_path and str(temp_root) in str(saved_path)
        loaded = SessionManager.load_session(sess_id)
        assert loaded and loaded["title"] == "Smoke Test Chat"
        print(f"[OK] 7/12 SessionManager (ACID-сохранение сессии {sess_id[:8]} в Temp)")

        # --- СМОК 8: UIContext SSOT + Headless PyQt6 ---
        from PyQt6.QtWidgets import QApplication, QWidget
        from ui.common.ui_context import UIContext
        app = QApplication.instance() or QApplication([])
        ui_ctx = UIContext()
        dummy_bubble = QWidget()
        ui_ctx.begin_turn(dummy_bubble)
        assert ui_ctx.is_busy is True and ui_ctx.turn_bubble is dummy_bubble
        ui_ctx.finish_turn()
        assert ui_ctx.is_busy is False and ui_ctx.turn_bubble is None
        print("[OK] 8/12 UIContext SSOT + Headless PyQt6 (begin_turn / finish_turn)")

        # --- СМОК 9: HybridSearchEngine (RAG в памяти без касания диска) ---
        from services.hybrid_search import HybridSearchEngine
        rag_engine = HybridSearchEngine(dense_vectorizer=False)
        chunks = rag_engine.chunk_text(
            "Артикул детали NR-9090 отвечает за охлаждение квантового ядра NeonRoute.",
            filename="manual_spec.md"
        )
        rag_engine.index_chunks(chunks)
        hits = rag_engine.search("Артикул NR-9090 охлаждение", top_k=1)
        assert len(hits) >= 1 and "NR-9090" in hits[0].get("text", str(hits[0]))
        print("[OK] 9/12 HybridSearchEngine (индексация и поиск по артикулу NR-9090 в RAM)")

        # --- СМОК 10: RoleplayStateManager & TavernParser (парсинг <stats> и архетип карточки) ---
        from services.roleplay_state_manager import RoleplayStateManager
        from services.tavern_parser import TavernParser
        rp_text = "Герой вошел в зал.\n<stats>\nЛокация: Цитадель Ночи\nКвест: Найти ключ\nИнвентарь: Факел, Клинок\n</stats>"
        parsed_status = RoleplayStateManager.parse_explicit_status(rp_text)
        assert parsed_status and ("Цитадель" in str(parsed_status) or len(parsed_status) > 0)
        arch_info = TavernParser.detect_card_archetype({
            "name": "System Guide",
            "description": "Система уровней, характеристик, квестов и инвентаря игрока [STATUS] Level Up LitRPG."
        })
        assert isinstance(arch_info, dict) and arch_info.get("is_system") is True
        print(f"[OK] 10/12 RoleplayStateManager + TavernParser (is_system={arch_info.get('is_system')})")

        # --- СМОК 11: TokenBudgetManager (двухконтурный бюджет и пожарный рубильник VRAM < 150 MB) ---
        from services.token_budget_manager import TokenBudgetManager
        tier_normal = TokenBudgetManager.calculate_context_budget(2000, mode="assistant", free_vram_mb=2000)
        tier_emergency = TokenBudgetManager.calculate_context_budget(2000, mode="assistant", free_vram_mb=100)
        assert tier_emergency.max_tokens <= tier_normal.max_tokens
        assert tier_emergency.max_tokens == 512, f"Ожидался аварийный лимит 512 токенов, получено: {tier_emergency}"
        print(f"[OK] 11/12 TokenBudgetManager (Normal={tier_normal.max_tokens} tok -> Emergency VRAM={tier_emergency.max_tokens} tok)")

        # --- СМОК 12: ModeCoordinator (FSM переключения режимов с изолированным mock-окном) ---
        from services.mode_coordinator import ModeCoordinator, AppMode
        mock_mw = MagicMock()
        mock_mw.chat_history = [{"role": "user", "content": "hi"}]
        coord = ModeCoordinator(mock_mw)
        assert coord.transition_to(AppMode.ENGINEER) is True
        assert coord.transition_to(AppMode.ROLEPLAY) is True
        print("[OK] 12/12 ModeCoordinator (FSM Teardown -> Switch -> Boot -> Mount)")

        # Закрываем SQLite дескриптор в Temp перед удалением временной папки
        if hasattr(mdb.memory_db, "close"):
            mdb.memory_db.close()

        # Финальная сверка неприкосновенности боевого приложения
        prod_after = snapshot_prod_state(PROJECT_ROOT)
        assert prod_before == prod_after, f"Нарушена изоляция! Diff: {prod_before} vs {prod_after}"
        print("\n[SUCCESS] 12/12 изолированных смок-тестов пройдены! Боевое приложение на 100% не затронуто.")

    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        print(f"[*] Временный полигон {temp_root} полностью очищен.")

if __name__ == "__main__":
    run_smoke_suite()
