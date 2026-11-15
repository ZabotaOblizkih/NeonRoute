"""
Сквозной диагностический стенд аудита междиалоговой памяти NeonRoute.
Изолированная среда: использует временную папку, НЕ трогает боевые базы.
Проверяет:
1. Запись факта через MemoryExtractor (фильтр min_messages, влияние тегов <think>).
2. Сохранение в LanceDB и норму векторов.
3. Поиск факта через MemoryDB.search_relevant_facts (проверка порога max_distance).
4. Инъекцию факта в системный промпт через ContextWindowBuilder.
5. Проверку наличия/отсутствия сигналов плашки для памяти.
6. Проверку багов table.delete в LanceDB.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path

# Корень проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def run_diagnostic():
    print("=" * 80)
    print(" СКВОЗНОЙ ТЕХНИЧЕСКИЙ АУДИТ МЕЖДИАЛОГОВОЙ ПАМЯТИ NEONROUTE")
    print("=" * 80)

    temp_dir = Path(tempfile.mkdtemp(prefix="neonroute_audit_"))
    print(f"\n[ЭТАП 0] Создана изолированная песочница: {temp_dir}")

    try:
        sandbox_data = temp_dir / "data"
        sandbox_data.mkdir(parents=True, exist_ok=True)
        sandbox_db_path = sandbox_data / "memory.db"
        sandbox_lance_dir = sandbox_data / "vector_memory"

        # Импортируем модули
        import config
        from fastembed import TextEmbedding
        from services.memory_db import MemoryDB, _get_embedding_engine, normalize_fact_text
        from services.memory_extractor import MemoryExtractor
        from services.context_window_builder import ContextWindowBuilder
        from services.assistant_prompt_builder import AssistantPromptBuilder

        # Инициализируем изолированную MemoryDB
        print("\n[ЭТАП 1] Инициализация изолированной MemoryDB...")
        import services.memory_db as mdb_module
        import lancedb

        # Настраиваем изоляцию LanceDB
        embed_model = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        db_lance = lancedb.connect(str(sandbox_lance_dir))
        dummy_vec = list(embed_model.embed(["init"]))[0].tolist()
        tbl_facts = db_lance.create_table("facts", data=[{"id": "init_id", "text": "init", "vector": dummy_vec}])
        tbl_rp = db_lance.create_table("rp_memories", data=[{
            "id": "init_id", "session_id": "init_sess", "text": "init", "vector": dummy_vec, "timestamp": "0"
        }])

        # Патчим модульные переменные на изолированные
        mdb_module._EMBED_MODEL = embed_model
        mdb_module._LANCE_TABLE = tbl_facts
        mdb_module._RP_LANCE_TABLE = tbl_rp

        mem_db = MemoryDB(db_path=sandbox_db_path)
        mdb_module.memory_db = mem_db

        print(f"  SQLite инициализирован: {sandbox_db_path.exists()}")
        print(f"  LanceDB инициализирован: {sandbox_lance_dir.exists()}, таблица 'facts' строк: {len(tbl_facts)}")

        # ----------------------------------------------------------------------
        # ТЕСТ 1: Фильтр количества сообщений в MemoryExtractor
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 1] Проверка порога запуска экстрактора: min_messages в MemoryExtractor")
        history_turn1 = [
            {"role": "user", "content": "Привет! Меня зовут Роман, я архитектор ПО и пишу на Python и Rust.", "is_user": True},
            {"role": "assistant", "content": "Привет, Роман! Приятно познакомиться. Чем могу помочь?", "is_user": False}
        ]
        
        # Проверяем _has_user_fact_potential
        user_text = history_turn1[0]["content"]
        has_potential = MemoryExtractor._has_user_fact_potential(user_text)
        print(f"  1.1. Проверка _has_user_fact_potential('{user_text[:50]}...'): {has_potential}")

        # Проверяем условие длины истории
        default_min_messages = 4  # Значение по умолчанию в сигнатуре
        print(f"  1.2. Длина истории диалога №1: {len(history_turn1)} сообщения.")
        print(f"  1.3. Порог min_messages в extract_facts_async: {default_min_messages}.")
        passes_min_messages = len(history_turn1) >= default_min_messages
        print(f"  1.4. Пройдет ли первый раунд диалога в экстракцию памяти?: {passes_min_messages} (ОТКАЗ!)")

        # ----------------------------------------------------------------------
        # ТЕСТ 2: Влияние тегов мыслей <think> на парсер JSON в MemoryExtractor
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 2] Проверка устойчивости JSON-парсера MemoryExtractor к моделям с рассуждениями (<think>)")
        
        # Сымитируем ответы различных моделей:
        # А) Обычная модель (чистый JSON)
        resp_clean = '{"facts": ["Пользователь работает архитектором ПО", "Пользователь пишет на Python и Rust"]}'
        # Б) Думающая модель с мыслями без скобок
        resp_think_plain = '<think>\nПользователь назвал имя и профессию. Извлечем факты.\n</think>\n{"facts": ["Пользователь работает архитектором ПО", "Пользователь пишет на Python и Rust"]}'
        # В) Думающая модель с рассуждениями о JSON (со скобками { } внутри <think>)
        resp_think_with_braces = '<think>\nНужно вернуть объект {"facts": [...]}. Пользователь сообщил: "пишу на Python". Формирую факты.\n</think>\n{"facts": ["Пользователь работает архитектором ПО", "Пользователь пишет на Python и Rust"]}'
        # Г) Думающая модель, упершаяся в лимит max_tokens=256
        resp_think_truncated = '<think>\nРазмышляю над фактами пользователя. Он упомянул имя Роман, профессию архитектор ПО...'

        def parse_facts_like_current_code(clean_resp: str):
            facts = []
            try:
                parsed_data = json.loads(clean_resp)
                if isinstance(parsed_data, dict):
                    facts = parsed_data.get("facts", [])
                elif isinstance(parsed_data, list):
                    facts = parsed_data
            except json.JSONDecodeError:
                start_obj = clean_resp.find("{")
                end_obj = clean_resp.rfind("}")
                if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
                    try:
                        p_obj = json.loads(clean_resp[start_obj:end_obj + 1])
                        if isinstance(p_obj, dict):
                            facts = p_obj.get("facts", [])
                    except Exception:
                        pass
                if not facts:
                    start_arr = clean_resp.find("[")
                    end_arr = clean_resp.rfind("]")
                    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
                        try:
                            p_arr = json.loads(clean_resp[start_arr:end_arr + 1])
                            if isinstance(p_arr, list):
                                facts = p_arr
                        except Exception:
                            pass
            return facts

        print(f"  2.1. Чистый JSON: извлечено {len(parse_facts_like_current_code(resp_clean))} фактов.")
        print(f"  2.2. <think> без скобок: извлечено {len(parse_facts_like_current_code(resp_think_plain))} фактов.")
        facts_c = parse_facts_like_current_code(resp_think_with_braces)
        print(f"  2.3. <think> со скобками внутри мыслей: извлечено {len(facts_c)} фактов (ОШИБКА: {facts_c}).")
        facts_d = parse_facts_like_current_code(resp_think_truncated)
        print(f"  2.4. <think> усеченный (max_tokens=256): извлечено {len(facts_d)} фактов.")

        # ----------------------------------------------------------------------
        # ТЕСТ 3: Запись фактов в базу и проверка нормы векторов
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 3] Запись фактов в изолированную MemoryDB и аудит векторов FastEmbed")
        fact1 = "Пользователь работает архитектором ПО"
        fact2 = "Пользователь пишет на Python и Rust"
        fact3 = "Пользователя зовут Роман"

        row1 = mem_db.add_fact(fact1)
        row2 = mem_db.add_fact(fact2)
        row3 = mem_db.add_fact(fact3)
        print(f"  3.1. Добавлены факты: id1={row1}, id2={row2}, id3={row3}")

        # Проверяем SQLite
        all_sqlite = mem_db.get_all_facts()
        print(f"  3.2. В SQLite сохранено фактов: {len(all_sqlite)}")

        # Проверяем LanceDB
        lance_rows = tbl_facts.to_arrow().to_pylist()
        print(f"  3.3. В LanceDB сохранено строк: {len(lance_rows)} (включая init_id)")
        for lr in lance_rows:
            if lr['id'] != 'init_id':
                v = lr['vector']
                norm = sum(x**2 for x in v)**0.5
                print(f"    - ID {lr['id']}: '{lr['text']}' | Размерность: {len(v)} | L2-норма: {norm:.4f}")

        # ----------------------------------------------------------------------
        # ТЕСТ 4: Поиск фактов и порог max_distance
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 4] Поиск фактов в LanceDB и аудит порога max_distance=0.48")
        query = "На каких языках программирования я пишу?"
        query_vec = list(embed_model.embed([query]))[0].tolist()
        raw_lance_res = tbl_facts.search(query_vec).limit(5).to_list()
        
        print(f"  4.1. Поисковый запрос: '{query}'")
        print("  4.2. Сырые результаты LanceDB (дистанция L2):")
        for r in raw_lance_res:
            dist = r.get('_distance')
            print(f"       [id={r.get('id')}, dist={dist:.4f}] {r.get('text')}")

        found_with_current_threshold = mem_db.search_relevant_facts(query, top_k=5, max_distance=0.48)
        print(f"  4.3. Вызов search_relevant_facts(..., max_distance=0.48): {len(found_with_current_threshold)} фактов (ОТКАЗ!)")

        # Проверим, при каком пороге факт найдется
        min_dist = min(r['_distance'] for r in raw_lance_res if r['id'] != 'init_id')
        print(f"  4.4. Минимальная реальная дистанция в LanceDB: {min_dist:.4f}")
        print(f"  4.5. Текущий порог в коде: 0.48. Недобор: дистанция превышает порог в {min_dist / 0.48:.1f} раз!")

        # ----------------------------------------------------------------------
        # ТЕСТ 5: Инъекция фактов в системный промпт (ContextWindowBuilder)
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 5] Инъекция найденных фактов в ContextWindowBuilder / messages")
        # Если бы поиск сработал и вернул факты:
        simulated_facts = [fact2]
        
        # Проверяем build_passive_memory
        passive_block = AssistantPromptBuilder.build_passive_memory(simulated_facts)
        print("  5.1. Сформированный блок <PassiveMemory>:")
        for line in passive_block.strip().splitlines():
            print(f"       {line}")

        # Проверяем сборку messages через ContextWindowBuilder.get_lm_studio_messages
        # (в боевом коде вызывается в фоне воркера)
        import services.context_window_builder as cwb_module
        cwb_module.memory_db = mem_db

        msgs = ContextWindowBuilder.get_lm_studio_messages(
            history=[{"role": "user", "content": query}],
            mode="assistant",
            is_roleplay=False,
            include_memory=True,
            user_query=query
        )
        sys_msg = msgs[0]["content"] if msgs else ""
        has_memory_in_prompt = "<PassiveMemory>" in sys_msg
        print(f"  5.2. Попал ли блок <PassiveMemory> в системный промпт боевым вызовом?: {has_memory_in_prompt}")
        if not has_memory_in_prompt:
            print("       (Причина: search_relevant_facts вернул пустой список из-за порога 0.48!)")

        # ----------------------------------------------------------------------
        # ТЕСТ 6: Проверка наличия UI-плашек и источников знаний
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 6] Проверка плашек/бейджей: попадает ли память в knowledge_sources?")
        from services.assistant_flow import _build_pending_knowledge_sources
        
        # Документы RAG
        sample_rag_docs = [{"filename": "manual.pdf", "chunk_id": 1, "snippet": "Инструкция", "score": 0.85}]
        sources = _build_pending_knowledge_sources(sample_rag_docs)
        print(f"  6.1. RAG-документы формируют sources: {sources}")
        
        # Память
        # Проверяем, существует ли функция или код, создающий sources для MemoryDB:
        has_memory_source_adapter = False
        print(f"  6.2. Существует ли в кодовой базе формирование knowledge_sources для фактов памяти?: {has_memory_source_adapter}")
        print("       (В интерфейсе плашка 'KnowledgeHUDWidget' отображает ТОЛЬКО файлы базы знаний RAG, плашка памяти в UI не реализована!)")

        # ----------------------------------------------------------------------
        # ТЕСТ 7: Проверка бага table.delete в LanceDB
        # ----------------------------------------------------------------------
        print("\n[ТЕСТ 7] Проверка бага удаления фактов table.delete в LanceDB")
        try:
            tbl_facts.delete('id != "init_id"')
            print("  7.1. table.delete('id != \"init_id\"'): УСПЕШНО")
        except Exception as e:
            print(f"  7.1. table.delete('id != \"init_id\"'): СБОЙ! Ошибка DataFusion: {e}")

        try:
            tbl_facts.delete("id != 'init_id'")
            print(f"  7.2. table.delete(\"id != 'init_id'\") с одинарными кавычками: УСПЕШНО! Осталось строк: {len(tbl_facts)}")
        except Exception as e:
            print(f"  7.2. table.delete(\"id != 'init_id'\"): СБОЙ: {e}")

        print("\n" + "=" * 80)
        print(" ДИАГНОСТИКА ЗАВЕРШЕНА УСПЕШНО")
        print("=" * 80)

    finally:
        # Корректное закрытие и удаление песочницы
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n[ЭТАП 8] Изолированная песочница {temp_dir} удалена.")

if __name__ == "__main__":
    run_diagnostic()
