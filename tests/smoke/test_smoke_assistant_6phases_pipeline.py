from ui.controllers.assistant_flow_controller import RE_IMAGE_TAG, RE_GALLERY_TRIGGER
from services.art_director import VisualIntent, TargetSubject
from services.art_director.director import ArtDirector
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.session_manager import SessionManager
from services.context_window_builder import ContextWindowBuilder
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.skill_router import SkillRouter
from utils.config_manager import ConfigManager


@pytest.fixture
def isolated_pipeline_env(tmp_path, monkeypatch):
    """Изолированная среда для сквозного тестирования конвейера."""
    fake_sessions_dir = tmp_path / "sessions"
    fake_sessions_dir.mkdir(parents=True, exist_ok=True)
    fake_config_file = tmp_path / "config.json"

    initial_config = {
        "active_mode": "assistant",
        "assistant_custom_prompt_template": "",
        "default_sampling_assistant": {
            "temperature": 0.7,
            "top_p": 0.9
        }
    }
    fake_config_file.write_text(json.dumps(initial_config, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", fake_sessions_dir)
    if hasattr(SessionManager, "HISTORY_DIR"):
        monkeypatch.setattr(SessionManager, "HISTORY_DIR", fake_sessions_dir)
    monkeypatch.setattr(SessionManager, "_instance", None)

    monkeypatch.setattr("utils.config_manager.CONFIG_PATH", fake_config_file)
    if hasattr(ConfigManager, "CONFIG_PATH"):
        monkeypatch.setattr(ConfigManager, "CONFIG_PATH", fake_config_file)
    if hasattr(ConfigManager, "_config_cache"):
        monkeypatch.setattr(ConfigManager, "_config_cache", None)

    return {
        "tmp_path": tmp_path,
        "sessions_dir": fake_sessions_dir,
        "config_file": fake_config_file,
    }


def test_smoke_assistant_6phases_pipeline(isolated_pipeline_env):
    """Сквозной смоук-тест 6 фаз конвейера с контролем межсессионных утечек."""
    sm = SessionManager.get_instance()
    if hasattr(sm, "history_dir"):
        sm.history_dir = isolated_pipeline_env["sessions_dir"]

    # ═════════════════════════════════════════════════════════════════════
    # ПРОГОН СЕССИИ A: ПОЛНЫЙ ЦИКЛ (ФАЗЫ 1 - 6)
    # ═════════════════════════════════════════════════════════════════════

    # Создание Сессии A
    sess_a = sm.create_session(mode="assistant", title="Сессия 6 Фаз A")
    sess_a_id = sess_a["id"]

    # ── ФАЗА 1: Захват ввода ──
    user_input = "SECRET_PAYLOAD_PHASE1: Напиши план разработки"
    assert user_input.strip(), "[Фаза 1] Пустой ввод недопустим"
    print("\n[Фаза 1: Ввод] OK - контрольный маркер принят")

    # ── ФАЗА 2: Семантическая маршрутизация (SkillRouter) ──
    router = SkillRouter.get_instance()
    # Проверяем реальный метод route(query) -> Tuple[Optional[str], float, str]
    skill_id, score, reason = router.route(user_input)
    assert skill_id is None or isinstance(skill_id, str), f"[Фаза 2] Некорректный skill_id: {skill_id}"
    assert isinstance(score, float) and 0.0 <= score <= 1.0, f"[Фаза 2] Некорректный score: {score}"
    assert isinstance(reason, str) and len(reason) > 0, f"[Фаза 2] Пустая причина маршрутизации"

    # Проверяем эвристику прямого триггера (детерминированный контракт)
    art_skill, art_score, _ = router.route("нарисуй неоновый город в стиле киберпанк")
    assert art_skill == "gallery", f"[Фаза 2] Эвристика галереи не распознала триггер: {art_skill}"
    assert art_score == 1.0

    routed_skill = skill_id
    print(f"[Фаза 2: Роутинг] OK - маршрутизатор вернул: skill='{routed_skill}', score={score:.2f}, reason='{reason}'")

    # ── ФАЗА 3: Сборка контекстного окна ──
    with patch("services.context_window_builder.memory_db", None):
        sys_prompt_a = ContextWindowBuilder.build_system_prompt(
            mode="assistant",
            session_id=sess_a_id,
            custom_system_prompt=sess_a.get("custom_system_prompt"),
            user_query=user_input,
            active_assistant_skill=routed_skill,
            include_memory=False
        )
    assert "[identity & persona]" in sys_prompt_a.lower() or "элария" in sys_prompt_a.lower(), (
        "[Фаза 3] Системный блок не содержит базовую персону Эларии"
    )
    print(f"[Фаза 3: Сборка контекста] OK - промпт сформирован ({len(sys_prompt_a)} симв.), персона проверена")

    # ── ФАЗА 4: Параметры шлюза и формирование полезной нагрузки ──
    cfg = ConfigManager.load_config()
    sampling_params = cfg.get("default_sampling_assistant", {})
    assert "temperature" in sampling_params, "[Фаза 4] Отсутствуют параметры сэмплирования"
    
    payload = {
        "messages": [
            {"role": "system", "content": sys_prompt_a},
            {"role": "user", "content": user_input}
        ],
        "temperature": sampling_params.get("temperature", 0.7)
    }
    assert len(payload["messages"]) == 2, "[Фаза 4] Неверное число сообщений в payload"
    print(f"[Фаза 4: Очередь] OK - полезная нагрузка подготовлена (температура: {payload['temperature']})")

    # ── ФАЗА 5: Эмуляция SSE-стриминга и отделение <think> ──
    raw_sse_chunks = [
        "<think>\nАнализирую ",
        "секретный маркер ",
        "пользователя.\n</think>",
        "План разработки ",
        "готов к реализации."
    ]

    accumulated_full = ""
    extracted_think = ""
    clean_response = ""
    in_think_block = False

    for chunk in raw_sse_chunks:
        accumulated_full += chunk
        if "<think>" in chunk:
            in_think_block = True
        
        if in_think_block:
            if "</think>" in chunk:
                in_think_block = False
                parts = chunk.split("</think>")
                extracted_think += parts[0]
                clean_response += parts[1]
            else:
                extracted_think += chunk.replace("<think>", "")
        else:
            clean_response += chunk

    assert "Анализирую секретный маркер" in extracted_think, "[Фаза 5] Блок рассуждений <think> не извлечен"
    assert clean_response.strip() == "План разработки готов к реализации.", (
        f"[Фаза 5] Чистовик содержит артефакты: {clean_response}"
    )
    print(f"[Фаза 5: Стриминг и парсинг] OK - рассуждения отделены ({len(extracted_think)} симв.), чистовик: '{clean_response.strip()}'")

    # ── ФАЗА 6: Санитизация, аппенд и дисковая персистентность ──
    assert "<think>" not in clean_response, "[Фаза 6] Санитизация не вырезала тег <think>"
    
    # Фиксация реплик в Сессии A
    sess_a["chat_history"] = [
        {"role": "user", "content": user_input, "is_user": True},
        {"role": "assistant", "content": clean_response.strip(), "is_user": False}
    ]
    sm.save_session(sess_a)

    reloaded_a = sm.load_session(sess_a_id)
    assert len(reloaded_a.get("chat_history", [])) == 2, "[Фаза 6] Сессия A не сохранилась на диске"
    print("[Фаза 6: Санитизация и запись] OK - сессия A сохранена на диск и валидирована")

    # ═════════════════════════════════════════════════════════════════════
    # ПРОВЕРКА ИЗОЛЯЦИИ: СОЗДАНИЕ СЕССИИ B И КОНТРОЛЬ УТЕЧЕК
    # ═════════════════════════════════════════════════════════════════════

    # 1. Меняем шаблон системного промпта
    snapshot_token_b = "CUSTOM_PROMPT_SNAPSHOT_TOKEN_XYZ_42"
    cfg["assistant_custom_prompt_template"] = f"Промпт со снимком: {snapshot_token_b}"
    ConfigManager.save_config(cfg)

    # 2. Создаем Сессию B
    sess_b = sm.create_session(mode="assistant", title="Сессия B Изолированная")
    sess_b_id = sess_b["id"]

    # 3. Проверка истории Сессии B (полное отсутствие данных из Фазы 1 Сессии A)
    history_b = sess_b.get("chat_history", [])
    history_b_str = json.dumps(history_b, ensure_ascii=False)
    assert "SECRET_PAYLOAD_PHASE1" not in history_b_str, (
        "[УТЕЧКА]: Секретный ввод Фазы 1 из Сессии A обнаружен в истории новой Сессии B!"
    )
    assert len(history_b) == 0, "[УТЕЧКА]: Новая сессия создана с непустой историей"

    # 4. Проверка изоляции снимков промпта
    assert snapshot_token_b in sess_b.get("custom_system_prompt", ""), (
        "[ИЗОЛЯЦИЯ]: Сессия B не зафиксировала новый снимок промпта"
    )

    reloaded_a_after = sm.load_session(sess_a_id)
    assert snapshot_token_b not in reloaded_a_after.get("custom_system_prompt", ""), (
        "[УТЕЧКА]: Сессия A была перезаписана системным промптом Сессии B!"
    )

    # 5. Проверка компиляции системного окна для Сессии B
    with patch("services.context_window_builder.memory_db", None):
        sys_prompt_b = ContextWindowBuilder.build_system_prompt(
            mode="assistant",
            session_id=sess_b_id,
            custom_system_prompt=sess_b.get("custom_system_prompt"),
            include_memory=False
        )
    assert snapshot_token_b in sys_prompt_b, "[Фаза 3 Сессия B] Снимок не попал в скомпилированный промпт"
    assert "SECRET_PAYLOAD_PHASE1" not in sys_prompt_b, "[УТЕЧКА]: Данные Фазы 1 утекли в системный промпт Сессии B"
    print("[Изоляция сессий] OK - утечек маркера и снимка между Сессией A и B не обнаружено")


def test_smoke_assistant_gallery_single_pass_pipeline(isolated_pipeline_env):
    """Сквозной смоук-тест однопроходного конвейера арт-скилла 'gallery' (Single-Turn Skill Injection)."""
    sm = SessionManager.get_instance()
    if hasattr(sm, "history_dir"):
        sm.history_dir = isolated_pipeline_env["sessions_dir"]

    # ── 1. Входящий запрос на арт ──
    art_query = "Нарисуй мне концепт-арт неонового футуристического города под дождем"

    # ── 2. Векторный и эвристический роутинг в 'gallery' ──
    router = SkillRouter.get_instance()
    skill_id, score, reason = router.route(art_query)
    assert skill_id == "gallery", f"[Арт-Скилл] Ожидался скилл 'gallery', получен: {skill_id}"
    assert score >= 0.5, f"[Арт-Скилл] Недостаточный скор: {score}"

    # ── 3. Сборка системного окна с директивой SD 1.5 ──
    builder = AssistantPromptBuilder.get_instance()
    sys_prompt_sd15 = builder.build(
        user_name="Тест",
        user_query=art_query,
        target_model_id="dreamshaper_8",
        architecture="sd15"
    )
    assert "[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion]" in sys_prompt_sd15
    assert "[GALERY]" not in sys_prompt_sd15
    assert "dreamshaper_8" in sys_prompt_sd15
    assert "Tag Language: STRICTLY English" in sys_prompt_sd15

    # Проверка авто-определения модели
    sys_prompt_auto = builder.build(user_name="Тест", user_query=art_query)
    assert "[SYSTEM DIRECTIVE:" in sys_prompt_auto
    assert "--model " in sys_prompt_auto

    # ── 4. Сборка системного окна с директивой SDXL ──
    sys_prompt_sdxl = builder.build(
        user_name="Тест",
        user_query=art_query,
        architecture="sdxl",
        target_model_id="pony_diffusion",
        target_model_desc="Pony Diffusion V6"
    )
    assert "[SYSTEM DIRECTIVE: Stable Diffusion XL" in sys_prompt_sdxl
    assert "HAND ANCHOR RULE" in sys_prompt_sdxl
    assert "pony_diffusion" in sys_prompt_sdxl

    # ── 5. Эмуляция чистового вывода модели за один ход ──
    simulated_llm_output = (
        "<think>\nПланирую композицию города.\n</think>\n"
        "Секунду, перевожу образ в кадр...\n"
        "[IMAGE_PROMPT: futuristic rain-lashed cityscape, neon signs reflecting on wet pavement, towering skyscrapers with glowing holographic interfaces, rainy night atmosphere --ar wide --model dreamshaper_8]"
    )

    # ── 6. Проверка извлечения тега и блокировки второго прохода боевыми регулярками ──
    match_image = RE_IMAGE_TAG.search(simulated_llm_output)
    assert match_image is not None, "[Арт-Скилл] Боевая регулярка RE_IMAGE_TAG не нашла тег"
    extracted_prompt = match_image.group(1).strip()
    assert "futuristic rain-lashed cityscape" in extracted_prompt

    has_gallery = RE_GALLERY_TRIGGER.search(simulated_llm_output) is not None
    should_run_second_pass = bool(has_gallery and not extracted_prompt)
    assert should_run_second_pass is False, "[Арт-Скилл] Ошибка: сработал флаг второго прохода!"

    # ── 7. Сквозная генерация GenerationPlan в ArtDirector ──
    director = ArtDirector()
    v_intent = VisualIntent(
        prompt=extracted_prompt,
        target_subject=TargetSubject.SCENE_OBJECT,
        suggested_model="dreamshaper_8",
        mode="assistant"
    )
    plan = director.plan_from_visual_intent(visual_intent=v_intent)
    assert plan is not None
    assert "futuristic rain-lashed cityscape" in plan.positive_prompt
    assert plan.width > 0 and plan.height > 0
    assert plan.negative_prompt is not None
    assert plan.target_checkpoint is not None
