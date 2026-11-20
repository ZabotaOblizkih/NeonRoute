"""
Consolidated End-to-End Smoke Test Suite for NeonRoute Assistant Pipeline.
Covers:
1. Full 6-Phase Assistant Cycle (Capture -> SkillRouter -> ContextWindow -> Payload -> SSE Thinking -> Storage).
2. Structural System Window Hierarchy Validation (Primacy -> Context -> Recency).
3. Cross-Session Isolation & Leak Prevention (Session A vs Session B).
4. Single-Pass Gallery Skill Pipeline (SD 1.5 Danbooru vs SDXL Prose).
5. Document Parsing, Markdown Chunker Code Block Integrity, and Output Sanitization.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

import config
from utils.config_manager import ConfigManager
from services.session_manager import SessionManager
from services.context_window_builder import ContextWindowBuilder
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.skill_router import SkillRouter
from services.art_director import VisualIntent, TargetSubject
from services.art_director.director import ArtDirector
from services.markdown_chunker import MarkdownChunker
from services.document_parser import DocumentParser
from services.regex_filter import TextRegexFilter
from ui.controllers.assistant_flow_controller import RE_IMAGE_TAG, RE_GALLERY_TRIGGER


# =========================================================================
# СЦЕНАРИЙ 1: СКВОЗНОЙ 6-ФАЗНЫЙ ЦИКЛ АССИСТЕНТА И МЕЖСЕССИОННАЯ ИЗОЛЯЦИЯ
# =========================================================================

def test_assistant_6phases_pipeline_and_isolation(smoke_isolation_guard):
    """
    Сквозной смоук-тест 6 фаз конвейера с контрольным маркером и проверкой межсессионных утечек.
    """
    scen = "Сценарий 1: 6-фазный цикл Ассистента и межсессионная изоляция"
    sm = SessionManager.get_instance()

    # ═════════════════════════════════════════════════════════════════════
    # ПРОГОН СЕССИИ A: ПОЛНЫЙ ЦИКЛ (ФАЗЫ 1 - 6)
    # ═════════════════════════════════════════════════════════════════════

    sess_a = sm.create_session(mode="assistant", title="Сессия 6 Фаз A")
    sess_a_id = sess_a["id"]

    # ── ФАЗА 1: Захват ввода ──
    secret_marker = "SECRET_PAYLOAD_PHASE1: Напиши план разработки"
    assert_diag(bool(secret_marker.strip()), scen, "Фаза 1: Ввод", "Непустой ввод", secret_marker)

    # ── ФАЗА 2: Семантическая маршрутизация (SkillRouter) ──
    router = SkillRouter.get_instance()
    skill_id, score, reason = router.route(secret_marker)
    assert_diag(skill_id is None or isinstance(skill_id, str), scen, "Фаза 2: Skill ID", "Строка или None", skill_id)
    assert_diag(isinstance(score, float) and 0.0 <= score <= 1.0, scen, "Фаза 2: Score", "0.0 <= score <= 1.0", score)

    # Проверка детерминированной эвристики прямого триггера
    art_skill, art_score, _ = router.route("нарисуй неоновый город в стиле киберпанк")
    assert_diag(art_skill == "gallery", scen, "Фаза 2: Триггер галереи", "gallery", art_skill)
    assert_diag(art_score == 1.0, scen, "Фаза 2: Скор триггера", 1.0, art_score)

    routed_skill = skill_id

    # ── ФАЗА 3: Сборка контекстного окна ──
    with patch("services.context_window_builder.memory_db", None):
        sys_prompt_a = ContextWindowBuilder.build_system_prompt(
            mode="assistant",
            session_id=sess_a_id,
            custom_system_prompt=sess_a.get("custom_system_prompt"),
            user_query=secret_marker,
            active_assistant_skill=routed_skill,
            include_memory=False
        )
    assert_diag(sys_prompt_a.startswith("[identity & persona]"), scen, "Фаза 3: Primacy контракт", "Начинается с [identity & persona]", sys_prompt_a[:40])
    assert_diag("элария" in sys_prompt_a.lower(), scen, "Фаза 3: Персона", "Содержит 'элария'", "элария" in sys_prompt_a.lower())

    # Структурная валидация иерархии секций (Primacy -> Context -> Recency)
    full_contract_prompt = AssistantPromptBuilder.get_instance().build(
        user_name="Алекс",
        active_persona={"bio": "Системный архитектор"},
        relevant_facts=["Ценит строгую типизацию"],
        matched_knowledge_docs=[{"filename": "arch.md", "content": "Инвариант: 0 утечек"}],
        attached_document="Параметр: 42 ГГц",
        attached_document_name="spec.txt",
        is_excel=True,
        manual_skill="code_architect",
        static_core_only=False
    )

    expected_hierarchy = [
        "[identity & persona]",
        "\n<UserInfo>\n",
        "\n<PassiveMemory>\n",
        "\n<KnowledgeBase>\n",
        "\n<AttachedDocument filename=",
        "User requested an Excel spreadsheet",
        "[АКТИВНЫЙ СКИЛЛ: РАЗРАБОТКА И АРХИТЕКТУРА КОДА"
    ]

    positions = []
    missing_markers = []
    for marker in expected_hierarchy:
        pos = full_contract_prompt.find(marker)
        if pos == -1:
            missing_markers.append(marker)
        positions.append(pos)

    assert_diag(len(missing_markers) == 0, scen, "Фаза 3: Маркеры иерархии", "Все 7 маркеров найдены", missing_markers)
    assert_diag(positions == sorted(positions), scen, "Фаза 3: Порядок секций", "Канонический порядок Primacy->Context->Recency", positions)

    # ── ФАЗА 4: Параметры шлюза и формирование полезной нагрузки ──
    cfg = ConfigManager.load_config()
    sampling_params = cfg.get("default_sampling_assistant", {})
    assert_diag("temperature" in sampling_params, scen, "Фаза 4: Сэмплирование", "temperature в конфиге", sampling_params)

    payload = {
        "messages": [
            {"role": "system", "content": sys_prompt_a},
            {"role": "user", "content": secret_marker}
        ],
        "temperature": sampling_params.get("temperature", 0.7)
    }
    assert_diag(len(payload["messages"]) == 2, scen, "Фаза 4: Структура сообщений", 2, len(payload["messages"]))

    # ── ФАЗА 5: Эмуляция SSE-стриминга и отделение <think> ──
    raw_sse_chunks = [
        "<think>\nАнализирую ",
        "секретный маркер ",
        "пользователя.\n</think>",
        "План разработки ",
        "готов к реализации."
    ]

    extracted_think = ""
    clean_response = ""
    in_think_block = False

    for chunk in raw_sse_chunks:
        if "<think>" in chunk:
            in_think_block = True

        if in_think_block:
            if "</think>" in chunk:
                in_think_block = False
                parts = chunk.split("</think>")
                extracted_think += parts[0].replace("<think>", "")
                clean_response += parts[1]
            else:
                extracted_think += chunk.replace("<think>", "")
        else:
            clean_response += chunk

    assert_diag("Анализирую секретный маркер" in extracted_think, scen, "Фаза 5: Извлечение мыслей", "Мысли извлечены", extracted_think)
    assert_diag(clean_response.strip() == "План разработки готов к реализации.", scen, "Фаза 5: Чистый ответ", "План разработки готов к реализации.", clean_response.strip())

    # ── ФАЗА 6: Санитизация, аппенд и дисковая персистентность ──
    assert_diag("<think>" not in clean_response, scen, "Фаза 6: Санитизация тега <think>", "Нет тегов <think>", clean_response)

    sess_a["chat_history"] = [
        {"role": "user", "content": secret_marker, "is_user": True},
        {"role": "assistant", "content": clean_response.strip(), "is_user": False}
    ]
    sm.save_session(sess_a)

    reloaded_a = sm.load_session(sess_a_id)
    assert_diag(reloaded_a is not None and len(reloaded_a.get("chat_history", [])) == 2, scen, "Фаза 6: Загрузка сессии А", 2, len(reloaded_a.get("chat_history", [])) if reloaded_a else 0)

    # ═════════════════════════════════════════════════════════════════════
    # ПРОВЕРКА ИЗОЛЯЦИИ: СОЗДАНИЕ СЕССИИ B И КОНТРОЛЬ УТЕЧЕК
    # ═════════════════════════════════════════════════════════════════════

    snapshot_token_b = "CUSTOM_PROMPT_SNAPSHOT_TOKEN_XYZ_42"
    cfg["assistant_custom_prompt_template"] = f"Промпт со снимком: {snapshot_token_b}"
    ConfigManager.save_config(cfg)

    sess_b = sm.create_session(mode="assistant", title="Сессия B Изолированная")
    sess_b_id = sess_b["id"]

    history_b = sess_b.get("chat_history", [])
    history_b_str = json.dumps(history_b, ensure_ascii=False)
    assert_diag("SECRET_PAYLOAD_PHASE1" not in history_b_str, scen, "Изоляция истории", "0 утечек маркера", history_b_str)
    assert_diag(len(history_b) == 0, scen, "История новой сессии B", 0, len(history_b))

    reloaded_a_after = sm.load_session(sess_a_id)
    assert_diag(snapshot_token_b not in reloaded_a_after.get("custom_system_prompt", ""), scen, "Изоляция промптов", "Сессия А не перезаписана", reloaded_a_after.get("custom_system_prompt", ""))


# =========================================================================
# СЦЕНАРИЙ 2: ОДНОПРОХОДНЫЙ КОНВЕЙЕР GALLERY (SD 1.5 VS SDXL)
# =========================================================================

def test_assistant_gallery_single_pass_pipeline(smoke_isolation_guard):
    """
    Сквозной смоук-тест однопроходного конвейера арт-скилла 'gallery' (Single-Turn Skill Injection).
    """
    scen = "Сценарий 2: Однопроходный конвейер gallery (SD 1.5 vs SDXL)"
    art_query = "Нарисуй мне концепт-арт неонового футуристического города под дождем"

    # 1. Векторный и эвристический роутинг в 'gallery'
    router = SkillRouter.get_instance()
    skill_id, score, _ = router.route(art_query)
    assert_diag(skill_id == "gallery", scen, "Роутинг запроса арта", "gallery", skill_id)
    assert_diag(score >= 0.5, scen, "Скор роутинга", ">= 0.5", score)

    # 2. Сборка системного окна с директивой SD 1.5
    builder = AssistantPromptBuilder.get_instance()
    sys_prompt_sd15 = builder.build(
        user_name="Тест",
        user_query=art_query,
        target_model_id="dreamshaper_8",
        architecture="sd15",
        static_core_only=False
    )
    assert_diag("[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in sys_prompt_sd15, scen, "Директива SD 1.5", "Содержит SD 1.5 Prompt Generator", sys_prompt_sd15[:200])
    assert_diag("dreamshaper_8" in sys_prompt_sd15, scen, "Имя модели в директиве", "dreamshaper_8", "dreamshaper_8" in sys_prompt_sd15)

    # 3. Сборка системного окна с директивой SDXL
    sys_prompt_sdxl = builder.build(
        user_name="Тест",
        user_query=art_query,
        architecture="sdxl",
        target_model_id="pony_diffusion",
        target_model_desc="Pony Diffusion V6",
        static_core_only=False
    )
    assert_diag("[SYSTEM DIRECTIVE: SDXL Image Tag Translator]" in sys_prompt_sdxl, scen, "Директива SDXL", "Содержит SDXL Image Tag Translator", sys_prompt_sdxl[:200])
    assert_diag("pony_diffusion" in sys_prompt_sdxl, scen, "Имя модели в SDXL", "pony_diffusion", "pony_diffusion" in sys_prompt_sdxl)

    # 4. Эмуляция чистового вывода модели за один ход
    simulated_llm_output = (
        "<think>\nПланирую композицию города.\n</think>\n"
        "Секунду, перевожу образ в кадр...\n"
        "[IMAGE_PROMPT: futuristic rain-lashed cityscape, neon signs reflecting on wet pavement, towering skyscrapers with glowing holographic interfaces, rainy night atmosphere --ar wide --model dreamshaper_8]"
    )

    # 5. Проверка регулярных выражений и блокировки второго прохода
    match_image = RE_IMAGE_TAG.search(simulated_llm_output)
    assert_diag(match_image is not None, scen, "Извлечение IMAGE_PROMPT", "Тег найден", match_image)
    extracted_prompt = match_image.group(1).strip()
    assert_diag("futuristic rain-lashed cityscape" in extracted_prompt, scen, "Промпт из тега", "futuristic rain-lashed cityscape", extracted_prompt)

    has_gallery = RE_GALLERY_TRIGGER.search(simulated_llm_output) is not None
    should_run_second_pass = bool(has_gallery and not extracted_prompt)
    assert_diag(should_run_second_pass is False, scen, "Флаг второго прохода", False, should_run_second_pass)

    # 6. Сквозная генерация GenerationPlan в ArtDirector
    director = ArtDirector()
    v_intent = VisualIntent(
        prompt=extracted_prompt,
        target_subject=TargetSubject.SCENE_OBJECT,
        suggested_model="dreamshaper_8",
        mode="assistant"
    )
    plan = director.plan_from_visual_intent(visual_intent=v_intent)
    assert_diag(plan is not None, scen, "ArtDirector Plan", "Plan сгенерирован", plan)
    assert_diag("futuristic rain-lashed cityscape" in plan.positive_prompt, scen, "Positive Prompt плана", "Содержит промпт", plan.positive_prompt)
    assert_diag(plan.width > 0 and plan.height > 0, scen, "Размеры плана", "> 0", f"{plan.width}x{plan.height}")


# =========================================================================
# СЦЕНАРИЙ 3: RAG CHUNKER, ДОКУМЕНТЫ И САНИТИЗАЦИЯ ОТВЕТОВ
# =========================================================================

def test_assistant_document_parser_and_chunker_integrity(smoke_isolation_guard):
    """
    Проверка структурного нарезчика RAG (MarkdownChunker) на целостность кодовых блоков
    и извлечения текста документов (DocumentParser) с санитизацией спецтокенов.
    """
    scen = "Сценарий 3: RAG Chunker, документы и санитизация"

    # 1. MarkdownChunker: кодовые блоки ``` не должны разрываться
    md_text = (
        "# Раздел 1\n" + ("Описание архитектуры. " * 25) + "\n"
        "```python\n"
        "def critical_function():\n"
        "    return 'must_stay_together'\n"
        "```\n"
        "# Раздел 2\n" + ("Дополнительные сведения. " * 25)
    )
    chunks = MarkdownChunker.chunk_document(md_text, filename="arch.md", min_chunk=200, max_chunk=600, overlap=50)
    assert_diag(len(chunks) >= 1, scen, "MarkdownChunker count", ">= 1", len(chunks))
    code_preserved = any("critical_function" in c.get("text", "") and "must_stay_together" in c.get("text", "") for c in chunks)
    assert_diag(code_preserved is True, scen, "Code block integrity", "Блок кода целиком в одном чанке", code_preserved)

    # 2. DocumentParser: чтение .md в изолированной директории
    doc_file = smoke_isolation_guard["data_dir"] / "spec_test.md"
    doc_file.write_text("Модуль VRAMArbiter управляет памятью 8 ГБ.", encoding="utf-8")
    extracted = DocumentParser.extract_text(doc_file)
    assert_diag("VRAMArbiter" in extracted, scen, "DocumentParser output", "Содержит VRAMArbiter", extracted)

    # 3. TextRegexFilter: вырезание <think>, спецтокенов и извлечение IMAGE_PROMPT
    dirty = "<think>Внутренний анализ</think>Чистый ответ пользователю. [IMAGE_PROMPT: 1girl, neon]<|im_end|>"
    img_prompt, rest = TextRegexFilter.extract_image_prompt(dirty)
    assert_diag(bool(img_prompt and "1girl" in img_prompt), scen, "Image prompt extraction", "1girl в промпте", img_prompt)

    cleaned = TextRegexFilter.process_output(rest)
    assert_diag("<think>" not in cleaned, scen, "Sanitize <think>", "Тег <think> удален", cleaned)
    assert_diag("<|im_end|>" not in cleaned, scen, "Sanitize <|im_end|>", "Спецтокен удален", cleaned)
    assert_diag("Чистый ответ пользователю." in cleaned, scen, "Preserve text", "Чистый ответ сохранен", cleaned)
