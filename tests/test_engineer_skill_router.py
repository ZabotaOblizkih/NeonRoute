"""
tests/test_engineer_skill_router.py
Тесты для изолированного векторного роутера режима «Инженер» (EngineerSkillRouter).
"""

import numpy as np
import pytest

from services.engineer_skill_router import (
    CONFIDENCE_THRESHOLD,
    ENGINEER_SKILLS,
    FALLBACK_SKILL,
    MODEL_NAME,
    EngineerSkillRouter,
    get_shared_embedder,
)


@pytest.fixture(autouse=True)
def reset_router_singleton():
    """Сбрасывает синглтон перед и после каждого теста для изоляции."""
    EngineerSkillRouter.reset_instance()
    yield
    EngineerSkillRouter.reset_instance()


class DummyMockEmbedder:
    """Легковесный мок для модульных тестов без вызова тяжелых моделей."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, texts):
        for text in texts:
            rng = np.random.RandomState(abs(hash(text)) % (2**31 - 1))
            vec = rng.randn(self.dim).astype(np.float32)
            yield vec


def test_skills_archetypes_and_properties_structure():
    """Проверка наличия всех 5 фундаментальных архетипов и обязательных полей."""
    expected_archetypes = {
        "interactive_loop",
        "pure_logic_tdd",
        "batch_artifact",
        "service_backend",
        "cli_automation",
    }
    assert set(ENGINEER_SKILLS.keys()) == expected_archetypes, "Должно быть ровно 5 инженерных архетипов"

    expected_defaults = {
        "interactive_loop": "game.py",
        "pure_logic_tdd": "module.py",
        "batch_artifact": "make_doc.py",
        "service_backend": "main.py",
        "cli_automation": "main.py",
    }

    for skill_id, skill in ENGINEER_SKILLS.items():
        assert "title" in skill and isinstance(skill["title"], str) and skill["title"]
        assert "triggers" in skill and isinstance(skill["triggers"], str) and skill["triggers"]
        assert "architect_rules" in skill and isinstance(skill["architect_rules"], str) and skill["architect_rules"]
        assert "worker_rules" in skill and isinstance(skill["worker_rules"], str) and skill["worker_rules"]
        assert "default_artifact" in skill and skill["default_artifact"] == expected_defaults[skill_id]
        assert "anchors" in skill and isinstance(skill["anchors"], list) and len(skill["anchors"]) >= 4


def test_singleton_pattern():
    """Проверка создания и сброса синглтона EngineerSkillRouter."""
    router1 = EngineerSkillRouter.get_instance(auto_init=False)
    router2 = EngineerSkillRouter.get_instance(auto_init=False)
    assert router1 is router2

    EngineerSkillRouter.reset_instance()
    router3 = EngineerSkillRouter.get_instance(auto_init=False)
    assert router3 is not router1


def test_get_shared_embedder():
    """Проверка инициализации разделяемой модели эмбеддингов."""
    embedder = get_shared_embedder()
    assert embedder is not None
    assert MODEL_NAME == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_skill_metadata_and_get_all_skills():
    """Проверка методов получения метаданных скиллов."""
    router = EngineerSkillRouter(auto_init=False)

    meta = router.get_skill_metadata("interactive_loop")
    assert meta["skill_id"] == "interactive_loop"
    assert meta["default_artifact"] == "game.py"
    assert "Pygame" in meta["title"]

    # Проверка фоллбэка для несуществующего скилла
    fallback_meta = router.get_skill_metadata("unknown_skill_xyz")
    assert fallback_meta["skill_id"] == FALLBACK_SKILL
    assert fallback_meta["default_artifact"] == "module.py"

    all_skills = router.get_all_skills()
    assert len(all_skills) == 5
    assert "service_backend" in all_skills


def test_vector_normalization_and_dot_product_mock():
    """Проверка нормализации векторов и расчета через np.dot с моком."""
    mock_embedder = DummyMockEmbedder()
    router = EngineerSkillRouter(embedder=mock_embedder, auto_init=False)

    for skill_id, mat in router.anchor_matrices.items():
        assert isinstance(mat, np.ndarray)
        assert mat.dtype == np.float32
        # Каждый вектор в матрице должен быть единичной длины
        for row in mat:
            norm = np.linalg.norm(row)
            assert np.isclose(norm, 1.0, atol=1e-4)

    # Запрос через мок возвращает валидную структуру
    skill_id, score, meta = router.route("Тестовый запрос логики")
    assert isinstance(skill_id, str)
    assert isinstance(score, float)
    assert isinstance(meta, dict)
    assert "title" in meta


def test_empty_and_blank_queries_return_fallback():
    """Пустые строки и пробелы должны немедленно возвращать pure_logic_tdd с оценкой 0.0."""
    router = EngineerSkillRouter(auto_init=False)

    for empty_q in ["", "   ", "\n\t", None]:
        skill_id, score, meta = router.route(empty_q)
        assert skill_id == FALLBACK_SKILL
        assert score == 0.0
        assert meta["skill_id"] == FALLBACK_SKILL
        assert meta["default_artifact"] == "module.py"


def test_low_confidence_fallback():
    """Запросы с уверенностью ниже 0.25 должны возвращать pure_logic_tdd."""
    router = EngineerSkillRouter.get_instance()

    # Запросы вне сферы программирования / бытовые фразы
    low_confidence_queries = [
        "какая сейчас погода на улице",
        "расскажи смешной анекдот",
        "сколько будет два плюс два",
        "купи хлеб и молоко в магазине",
    ]
    for q in low_confidence_queries:
        skill_id, score, meta = router.route(q)
        assert score < CONFIDENCE_THRESHOLD, f"Скор для '{q}' должен быть < {CONFIDENCE_THRESHOLD}, получено {score:.3f}"
        assert skill_id == FALLBACK_SKILL
        assert meta["skill_id"] == FALLBACK_SKILL


def test_real_semantic_routing_archetypes():
    """
    Реальное тестирование векторной маршрутизации по 5 архетипам
    через предобученный FastEmbed (CPU ONNX).
    """
    router = EngineerSkillRouter.get_instance()

    test_cases = [
        # 1. interactive_loop (Игры / GUI)
        ("Сделай 2D аркаду тетрис на pygame с управлением и падающими блоками", "interactive_loop"),
        ("Напиши змейку на пайгейме со счетчиком очков", "interactive_loop"),
        ("Создай десктопный калькулятор с графическим интерфейсом на Tkinter или PyQt", "interactive_loop"),

        # 2. pure_logic_tdd (Алгоритмы / Математика / Чистые модули)
        ("Реализуй алгоритм быстрой сортировки quicksort и модульные тесты", "pure_logic_tdd"),
        ("Напиши структуру данных префиксное дерево Trie для автодополнения слов", "pure_logic_tdd"),
        ("Создай класс матрицы и математические операции умножения и детерминанта", "pure_logic_tdd"),

        # 3. batch_artifact (Excel / Word)
        ("Сформируй финансовый отчет по продажам в Excel .xlsx с формулами SUM и стилями ячеек", "batch_artifact"),
        ("Сделай скрипт генерации документа Word .docx со сводной таблицей и параграфами", "batch_artifact"),
        ("Создай таблицу openpyxl со сметой проекта и красивым оформлением границ", "batch_artifact"),

        # 4. service_backend (FastAPI / Telegram-боты)
        ("Разработай REST API бэкенд на FastAPI с эндпоинтами регистрации и Pydantic моделями", "service_backend"),
        ("Создай асинхронного телеграм-бота на aiogram с хэндлерами команд /start и /help", "service_backend"),
        ("HTTP веб-сервис на FastAPI с JSON маршрутизацией и асинхронной базой", "service_backend"),

        # 5. cli_automation (CLI / Системные утилиты)
        ("Напиши консольную CLI утилиту на argparse для проверки дискового пространства", "cli_automation"),
        ("Скрипт командной строки для пакетного переименования файлов в папке", "cli_automation"),
        ("Создай инструмент мониторинга загрузки CPU и памяти в консоли с аргументами sys", "cli_automation"),
    ]

    for query, expected_skill in test_cases:
        skill_id, score, meta = router.route(query)
        assert skill_id == expected_skill, (
            f"Запрос '{query}' был маршрутизирован в '{skill_id}' вместо '{expected_skill}'. "
            f"Score: {score:.3f}"
        )
        assert score >= CONFIDENCE_THRESHOLD, (
            f"Скор {score:.3f} для '{query}' должен быть >= {CONFIDENCE_THRESHOLD}"
        )
        assert meta["skill_id"] == expected_skill
        assert meta["title"] == ENGINEER_SKILLS[expected_skill]["title"]
        assert meta["default_artifact"] == ENGINEER_SKILLS[expected_skill]["default_artifact"]


def test_set_embedder_rebuilds_anchor_matrices():
    """Проверка смены модели эмбеддингов через set_embedder."""
    router = EngineerSkillRouter(auto_init=False)
    assert len(router.anchor_matrices) == 0

    mock = DummyMockEmbedder()
    router.set_embedder(mock)
    assert len(router.anchor_matrices) == 5

    router.set_embedder(None)
    assert len(router.anchor_matrices) == 0


def test_assistant_skill_router_isolation():
    """
    Проверка изоляции: services/skill_router.py не должен быть затронут
    и должен продолжать работать независимо для режима ассистента.
    """
    from services.skill_router import SkillRouter

    assistant_router = SkillRouter()
    # Проверка работы эвристик ассистента
    res, score, reason = assistant_router.route("Нарисуй арт киберпанк города")
    assert res == "gallery"

    res, score, reason = assistant_router.route("Сравни React и Vue, в чем плюсы и минусы")
    assert res == "analyst_logic"
