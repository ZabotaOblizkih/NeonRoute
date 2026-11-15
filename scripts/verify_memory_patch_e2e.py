"""
Сквозной верификационный стенд патча междиалоговой памяти NeonRoute.
Запускается в изолированной песочнице (не трогает боевые базы).
"""

import sys
import os
import re
import json
import time
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def verify_all():
    print("=" * 80)
    print(" ВЕРИФИКАЦИОННЫЙ ПРОГОН ПАТЧА МЕЖДИАЛОГОВОЙ ПАМЯТИ NEONROUTE")
    print("=" * 80)

    temp_dir = Path(tempfile.mkdtemp(prefix="neonroute_patch_verify_"))
    try:
        sandbox_data = temp_dir / "data"
        sandbox_data.mkdir(parents=True, exist_ok=True)
        sandbox_db_path = sandbox_data / "memory.db"
        sandbox_lance_dir = sandbox_data / "vector_memory"

        import services.memory_db as mdb_module
        from services.memory_db import MemoryDB, _normalize_l2
        from services.memory_extractor import MemoryExtractor
        from services.assistant_flow import assemble_assistant_context
        from services.assistant_prompt_builder import AssistantPromptBuilder
        from fastembed import TextEmbedding
        import lancedb

        print("\n--- 1. Тест L2-нормализации и косинусной метрики в MemoryDB ---")
        embed_model = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        db_lance = lancedb.connect(str(sandbox_lance_dir))
        dummy_vec = _normalize_l2(list(embed_model.embed(["init"]))[0].tolist())
        tbl_facts = db_lance.create_table("facts", data=[{"id": "init_id", "text": "init", "vector": dummy_vec}])

        mdb_module._EMBED_MODEL = embed_model
        mdb_module._LANCE_TABLE = tbl_facts
        mem_db = MemoryDB(db_path=sandbox_db_path)
        mdb_module.memory_db = mem_db

        # 1.1 Добавление фактов
        fact1 = "Пользователь пишет на Python и Rust"
        fact2 = "Пользователь предпочитает черный кофе без сахара"
        fact3 = "Пользователя зовут Роман"

        r1 = mem_db.add_fact(fact1)
        r2 = mem_db.add_fact(fact2)
        r3 = mem_db.add_fact(fact3)
        assert r1 > 0 and r2 > 0 and r3 > 0, f"Ошибка добавления: {r1}, {r2}, {r3}"
        print(f"  [OK] Факты успешно добавлены в SQLite и LanceDB: IDs={r1}, {r2}, {r3}")

        # 1.2 Проверка нормы сохраненных векторов
        lance_rows = tbl_facts.to_arrow().to_pylist()
        for lr in lance_rows:
            if lr["id"] != "init_id":
                norm = sum(x**2 for x in lr["vector"])**0.5
                assert abs(norm - 1.0) < 0.01, f"Вектор не нормализован: {norm}"
        print("  [OK] Все векторы в LanceDB строго L2-нормализованы (норма = 1.0).")

        # 1.3 Проверка семантической дедупликации (порог 0.15)
        dup_fact = "Я пишу код на Python и Rust" # normalize_fact_text сделает Пользователь пишет...
        r_dup = mem_db.add_fact(dup_fact)
        assert r_dup == r1, f"Дедупликация не сработала: {r_dup} != {r1}"
        print(f"  [OK] Семантическая дедупликация успешно опознала существующий факт #{r_dup}.")

        # 1.4 Проверка поиска search_relevant_facts (порог 0.58)
        found_lang = mem_db.search_relevant_facts("На каких языках я программирую?")
        assert len(found_lang) > 0 and fact1 in found_lang, f"Факт о языках не найден: {found_lang}"
        print(f"  [OK] Поиск по языкам успешно нашёл факт: '{found_lang[0]}'")

        found_coffee = mem_db.search_relevant_facts("Какой кофе я обычно пью?")
        assert len(found_coffee) > 0 and fact2 in found_coffee, f"Факт о кофе не найден: {found_coffee}"
        print(f"  [OK] Поиск по кофе успешно нашёл факт: '{found_coffee[0]}'")

        found_name = mem_db.search_relevant_facts("Напомни, как меня зовут?")
        assert len(found_name) > 0 and fact3 in found_name, f"Факт об имени не найден: {found_name}"
        print(f"  [OK] Поиск по имени успешно нашёл факт: '{found_name[0]}'")

        # 1.5 Проверка отсечения шума
        found_noise = mem_db.search_relevant_facts("Какое расстояние от Земли до Луны?")
        assert len(found_noise) == 0, f"Шум не отсечён: {found_noise}"
        print("  [OK] Несвязанный вопрос корректно вернул 0 фактов памяти.")

        # 1.6 Проверка безопасного синтаксиса delete в LanceDB
        deleted = mem_db.delete_fact_by_id(r2)
        assert deleted, "Ошибка удаления по ID"
        lance_after_del = [r for r in tbl_facts.to_arrow().to_pylist() if r["id"] == str(r2)]
        assert len(lance_after_del) == 0, "Запись не удалилась из LanceDB"
        print(f"  [OK] delete_fact_by_id #{r2} чисто удалил запись из SQLite и LanceDB.")

        # 1.7 Проверка синхронизации sync_and_reindex_facts
        # Вручную испортим LanceDB: добавим мусорную запись
        tbl_facts.add([{"id": "9999", "text": "Мусор", "vector": [0.1]*384}])
        assert len(tbl_facts.to_arrow().to_pylist()) == 4
        reindexed_cnt = mem_db.sync_and_reindex_facts()
        assert reindexed_cnt == 2, f"Ожидалось 2 живых факта, синхронизировано: {reindexed_cnt}"
        assert len([r for r in tbl_facts.to_arrow().to_pylist() if r["id"] == "9999"]) == 0
        print("  [OK] sync_and_reindex_facts успешно очистил сироту и переиндексировал живую базу.")

        print("\n--- 2. Тест экстрактора MemoryExtractor (2 сообщения + теги <think>) ---")
        history_2_msgs = [
            {"role": "user", "content": "Привет! Меня зовут Роман, я архитектор ПО и люблю походы.", "is_user": True},
            {"role": "assistant", "content": "Привет, Роман! Рада познакомиться.", "is_user": False}
        ]

        # Мокаем LLM с тегами <think>
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = (
            "<think>\n"
            "Пользователь представился: Роман, архитектор ПО, любит походы.\n"
            "Сформирую JSON {facts: [...]}.\n"
            "</think>\n"
            '{"facts": ["Пользователь по имени Роман", "Пользователь работает архитектором ПО", "Пользователь любит походы"]}'
        )

        with patch("services.task_queue.get_task_queue", return_value=None), \
             patch("services.memory_extractor.memory_db", mem_db):
            MemoryExtractor.extract_facts_async(history_2_msgs, lm_client=mock_client, min_messages=2)
            # Ждем завершения фонового потока экстракции
            for _ in range(50):
                if not MemoryExtractor._is_running and mock_client.chat_completion.called:
                    time.sleep(0.05)
                    break
                time.sleep(0.05)

        # Проверяем аргументы chat_completion: enable_thinking=False, max_tokens=512
        assert mock_client.chat_completion.called, "chat_completion не был вызван!"
        call_kwargs = mock_client.chat_completion.call_args[1]
        assert call_kwargs.get("enable_thinking") is False, "enable_thinking не равен False"
        assert call_kwargs.get("max_tokens") == 512, f"max_tokens не равен 512 ({call_kwargs.get('max_tokens')})"
        print("  [OK] extract_facts_async сработал на 2 сообщениях и передал enable_thinking=False, max_tokens=512.")

        # Проверяем, что факты попали в базу
        facts_after_extract = mem_db.get_all_facts()
        fact_texts = [f["fact"] for f in facts_after_extract]
        assert any("Роман" in t for t in fact_texts), f"Факт с именем не сохранен: {fact_texts}"
        print(f"  [OK] Факты из <think>-ответа успешно распарсены и сохранены в БД (всего {len(facts_after_extract)} фактов).")

        print("\n--- 3. Тест сборки контекста и UI-источников памяти ---")
        dummy_controller = MagicMock()
        dummy_controller.current_session = {"messages": [], "chat_history": []}
        dummy_controller._pending_knowledge_sources = None

        with patch("services.memory_db.memory_db", mem_db):
            msgs, matched_docs, kb_block = assemble_assistant_context(
                actual_prompt="Я работаю архитектором?",
                session_id="test_sess",
                curr_sess=dummy_controller.current_session,
                controller=dummy_controller,
                include_memory=True
            )

        # Проверяем, что блок <PassiveMemory> попал в системный промпт
        sys_prompt = msgs[0]["content"]
        assert "<PassiveMemory>" in sys_prompt, "Блок <PassiveMemory> отсутствует в системном промпте!"
        assert "архитектор" in sys_prompt.lower(), "Факт о профессии не найден в системном промпте!"
        print("  [OK] Блок <PassiveMemory> с извлечёнными фактами успешно встроен в messages[0].")

        # Проверяем, что плашка памяти сформирована в _pending_knowledge_sources
        sources = dummy_controller._pending_knowledge_sources
        assert sources is not None and len(sources) > 0, "pending_knowledge_sources пуст!"
        mem_source = next((s for s in sources if s.get("is_memory")), None)
        assert mem_source is not None, f"Источник с is_memory: True не найден: {sources}"
        assert "Долговременная память" in mem_source["filename"]
        print(f"  [OK] Плашка памяти успешно сформирована для HUD: {mem_source['filename']} ({mem_source['chunk_id']})")

        print("\n--- 4. Тест устойчивости очереди: отправка сообщений не отменяет память ---")
        from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_LOW
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        tq = TaskQueueWorker()
        mem_task = InferenceTask(name="Память", task_type="memory", func=lambda: None, priority=PRIORITY_LOW)
        tq.enqueue(mem_task)
        # Симулируем новый клик отправки сообщения: abort_tasks_by_type("memory") больше НЕ вызывается в _on_send_clicked
        # Проверяем, что в chat_flow_controller нет вызова abort_tasks_by_type("memory")
        with open(PROJECT_ROOT / "ui" / "controllers" / "chat_flow_controller.py", "r", encoding="utf-8") as f:
            cfc_code = f.read()
        assert 'abort_tasks_by_type("memory")' not in cfc_code, "В chat_flow_controller.py остался вызов abort_tasks_by_type('memory')!"
        with open(PROJECT_ROOT / "ui" / "controllers" / "hardware_engine.py", "r", encoding="utf-8") as f:
            he_code = f.read()
        assert 'abort_tasks_by_type("memory")' not in he_code, "В hardware_engine.py остался вызов abort_tasks_by_type('memory')!"
        print("  [OK] Вытеснение очереди задач памяти полностью устранено из контроллеров.")

        print("\n--- 5. Тест mobile_server: наличие вызова экстрактора ---")
        with open(PROJECT_ROOT / "mobile_server" / "services" / "chat_service.py", "r", encoding="utf-8") as f:
            mobile_code = f.read()
        assert "MemoryExtractor.extract_facts_async" in mobile_code, "В mobile_server/services/chat_service.py нет вызова MemoryExtractor!"
        print("  [OK] В mobile_server/services/chat_service.py подключён вызов MemoryExtractor.extract_facts_async.")

        print("\n" + "=" * 80)
        print(" ВСЕ 5 ЭТАПОВ ВЕРИФИКАЦИИ УСПЕШНО ПРОЙДЕНЫ (100% PASS RATE)!")
        print("=" * 80)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    verify_all()
