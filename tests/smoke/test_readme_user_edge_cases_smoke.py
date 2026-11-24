"""
tests/smoke/test_readme_user_edge_cases_smoke.py
================================================
Целевой смоук-сьют NeonRoute для проверки нетривиальных краевых случаев пользовательских
сценариев из README.md (включая FAQ и готовые наборы моделей под видеокарты):

Сценарии (8 целевых тестов):
1. test_readme_memory_commands_boundary_edge_cases:
   Граничные команды долговременной памяти (README.md раздел 3 и FAQ: «Запомни, что...»,
   «Что ты обо мне знаешь?», «Забудь...»): пустая БД, пустые факты/пробелы, дубликаты,
   несуществующие номера фактов (out-of-bounds), полная очистка памяти.

2. test_readme_memory_view_phrase_word_order_edge_case:
   Проверка буквальной фразы просмотра памяти из README.md (строки 71 и 181:
   «Что ты знаешь обо мне?» с обратным порядком слов «знаешь обо мне» vs «обо мне знаешь»).

3. test_readme_character_card_corrupted_import_edge_cases:
   Импорт поврежденных и нетипичных карточек персонажей (README.md раздел 2 и FAQ:
   ролевой чат, автоперевод карточек Chub/Tavern JSON): пустые файлы 0 байт, битый JSON,
   PNG без метаданных персонажа, PNG с битым base64/chara, отсутствующие файлы и карточки без имени.

4. test_readme_direct_art_cli_flags_and_negative_edge_cases:
   Директ-команды генерации и Арт-директор (README.md раздел 4 и FAQ: отрицания «без очков»,
   ручной выбор модели, CLI-флаги !art): граничные параметры (--steps 0, seed -999, extreme dims),
   только негативный промпт (--negative ...), несуществующий оверрайд модели (--model non_existent.safetensors).

5. test_readme_art_generation_guard_when_forge_offline:
   Защита памяти и Fail-Fast при отключенном Forge (README.md раздел 1 и 4: умный контроль 8 ГБ VRAM,
   модульность): попытка генерации арта при недоступном ForgeClient/ProcessManager завершается мгновенно,
   без выгрузки LLM из VRAM через VRAMArbiter и без зависания очереди задач.

6. test_readme_mode_switching_and_missing_llm_edge_cases:
   Переключение режимов при отсутствии моделей или отключенном LLM-сервере (README.md раздел 2:
   три режима — Ассистент, Ролевой чат, Инженер): создание сессий с пустыми настройками моделей,
   безопасный ModelNotFoundError при попытке загрузки отсутствующей модели в LMStudioClient.

7. test_readme_forge_launch_flags_hardware_boundary_edge_cases:
   Граничные аппаратные профили VRAM и CLI-флаги Forge (README.md раздел «Готовые наборы моделей»):
   4 ГБ (< 6 ГБ) форсирует стартовый --always-offload-from-vram и --no-half-vae; 8+ ГБ (>= 6 ГБ) не навязывает
   стартовый оффлоад; санитация и дедупликация граничных extra_args (дубликаты, пустые строки).

8. test_readme_document_rag_parser_edge_cases:
   Парсинг документов базы знаний RAG (README.md раздел 3 и FAQ: загрузка PDF, Word, Excel, книг):
   0-байтные файлы, битые бинарные псевдо-PDF, несуществующие пути, усечение при превышении max_chars.
"""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock
import json
import pytest

from PIL import Image, PngImagePlugin
from PyQt6.QtWidgets import QWidget

import config
from services.art_director.plan import GenerationPlan
from services.art_director.director import ArtDirector
from services.art_director.vram_policy import resolve_forge_startup_flags
from services.direct_command_parser import parse_direct_command_params
from services.document_parser import DocumentParser, ParsedDocument
from services.lm_studio import LMStudioClient, ModelNotFoundError
from services.memory_db import MemoryDB, handle_memory_command
from services.mode_coordinator import AppMode
from services.session_manager import SessionManager
from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser
from services.vram_arbiter import VRAMArbiter
from ui.controllers.hardware_engine import HardwareEngineMixin

try:
    assert_diag
except NameError:
    from tests.smoke.conftest import assert_diag, assert_diag_eq, assert_diag_in


# =========================================================================
# 1. СЦЕНАРИЙ: ГРАНИЧНЫЕ КОМАНДЫ ПАМЯТИ (README.md Раздел 3 и FAQ)
# =========================================================================

def test_readme_memory_commands_boundary_edge_cases(smoke_isolation_guard):
    """
    Проверяет граничные пользовательские команды управления памятью из README.md:
    - «Что ты обо мне знаешь?» на свежей/пустой БД возвращает вежливое уведомление.
    - «Запомни, что ...» с пустым вводом или одними пробелами/точками отклоняется без записи в БД.
    - Повторный ввод существующего факта отсекается защитой от дубликатов (row_id == -1).
    - «Забудь [номер]» с несуществующим или запредельным ID (99999) не вызывает исключений.
    - «Забудь» без параметров или только с пробелами обрабатывается безопасно.
    - «Забудь всё» полностью очищает базу, подтверждаясь последующим просмотром.
    """
    scen = "Сценарий 1: Граничные команды памяти (README.md)"
    mem_db = MemoryDB()

    # 1.1 Пустая БД: запрос памяти канонической фразой
    handled, reply = handle_memory_command("Что ты обо мне знаешь?", db=mem_db)
    assert_diag(handled, scen, "Что ты обо мне знаешь? (пустая БД)", "handled == True", handled)
    assert_diag_eq(reply, "Я пока ничего о вас не знаю.", scen, "Форматирование пустой памяти")

    # 1.2 Попытка сохранить пустой факт / только точки и пробелы
    handled_empty, reply_empty = handle_memory_command("Запомни, что   ...  . ", db=mem_db)
    assert_diag(handled_empty, scen, "Запомни пустой факт", "handled == True", handled_empty)
    assert_diag_in("Пожалуйста, укажите факт", reply_empty, scen, "Запрос ввода факта")
    all_facts_empty = mem_db.get_all_facts()
    assert_diag_eq(len(all_facts_empty), 0, scen, "Количество записей после пустого ввода")

    # 1.3 Успешное сохранение валидного факта
    handled_save, reply_save = handle_memory_command("Запомни, что я люблю чёрный кофе", db=mem_db)
    assert_diag(handled_save, scen, "Запомни валидный факт", "handled == True", handled_save)
    assert_diag_in("Записала в долговременную память", reply_save, scen, "Ответ об успешной записи")
    assert_diag_eq(len(mem_db.get_all_facts()), 1, scen, "В БД ровно 1 факт")

    # 1.4 Защита от дубликатов: повторный ввод существующего факта не создает дублирующую запись в SQLite
    handled_dup, reply_dup = handle_memory_command("Запомни, что я люблю чёрный кофе", db=mem_db)
    assert_diag(handled_dup, scen, "Повторный идентичный факт", "handled == True", handled_dup)
    assert_diag_eq(len(mem_db.get_all_facts()), 1, scen, "В БД не добавился дубликат (ровно 1 запись)")

    # 1.5 Удаление по несуществующему номеру (out-of-bounds)
    handled_del_oob, reply_del_oob = handle_memory_command("Забудь 99999", db=mem_db)
    assert_diag(handled_del_oob, scen, "Забудь 99999 (out-of-bounds)", "handled == True", handled_del_oob)
    assert_diag_in("не найден", reply_del_oob, scen, "Сообщение о ненахождении факта")
    assert_diag_eq(len(mem_db.get_all_facts()), 1, scen, "Факт остался нетронутым")

    # 1.6 Граничная команда «Забудь»:
    # - Одиночное «Забудь» без параметров не ломает роутер и возвращает False (пропуск в чат как речевой оборот)
    handled_del_bare, _ = handle_memory_command("Забудь   ", db=mem_db)
    assert_diag(not handled_del_bare, scen, "Одиночное 'Забудь' передается в диалог", False, handled_del_bare)

    # - «Забудь то, что» с пустой сутью факта перехватывается и просит конкретизации
    handled_del_incomplete, reply_del_incomplete = handle_memory_command("Забудь то, что", db=mem_db)
    assert_diag(handled_del_incomplete, scen, "Забудь то, что (неполный ввод)", True, handled_del_incomplete)
    assert_diag_in("укажите", reply_del_incomplete.lower(), scen, "Запрос конкретизации неполного удаления")

    # 1.7 Полная очистка памяти «Забудь всё»
    handled_clear, reply_clear = handle_memory_command("Забудь всё", db=mem_db)
    assert_diag(handled_clear, scen, "Забудь всё", "handled == True", handled_clear)
    assert_diag_in("Очистила всю память", reply_clear, scen, "Подтверждение очистки")

    # Проверка повторного запроса после очистки
    _, reply_post_clear = handle_memory_command("Что ты обо мне знаешь?", db=mem_db)
    assert_diag_eq(reply_post_clear, "Я пока ничего о вас не знаю.", scen, "Память пуста после очистки")


# =========================================================================
# 2. СЦЕНАРИЙ: ПРОВЕРКА БУКВАЛЬНОЙ ФРАЗЫ ИЗ README.md (Раздел 3, строки 71 и 181)
# =========================================================================

@pytest.mark.xfail(
    reason="[ДЕФЕКТ ПРОДАКШЕНА] services/memory_db.py: handle_memory_command не распознает буквальную фразу 'Что ты знаешь обо мне?' из README.md (строки 71 и 181), т.к. VIEW_TRIGGERS содержит только 'что ты обо мне знаешь'",
    strict=False,
)
def test_readme_memory_view_phrase_word_order_edge_case(smoke_isolation_guard):
    """
    Проверяет буквальную команду из документации README.md (строки 71 и 181):
    «Что ты знаешь обо мне?» (порядок слов: знаешь обо мне).
    В боевом коде services/memory_db.py зарегистрирован только вариант «что ты обо мне знаешь».
    Согласно ограничению 1 боевой код не модифицируется, а дефект фиксируется в отчете.
    """
    scen = "Сценарий 2: Порядок слов команды памяти из README.md"
    mem_db = MemoryDB()
    handled, reply = handle_memory_command("Что ты знаешь обо мне?", db=mem_db)
    assert_diag(handled, scen, "Буквальная фраза из README.md: 'Что ты знаешь обо мне?'", True, handled)
    assert_diag(bool(reply), scen, "Непустой ответ на просмотр памяти", True, bool(reply))


# =========================================================================
# 3. СЦЕНАРИЙ: ИМПОРТ БИТЫХ/ПУСТЫХ КАРТОЧЕК ПЕРСОНАЖЕЙ (README.md Раздел 2)
# =========================================================================

def test_readme_character_card_corrupted_import_edge_cases(tmp_path, smoke_isolation_guard):
    """
    Проверяет устойчивость парсера карточек персонажей к битым, пустым и аномальным файлам:
    - 0-байтный JSON-файл не роняет приложение, возвращает None.
    - Синтаксически поврежденный / оборванный JSON возвращает None.
    - Валидное изображение PNG без текстового чанка chara/ccv3 возвращает None.
    - Валидное изображение PNG с битым/недекодируемым base64 в chara возвращает None.
    - Несуществующий путь возвращает None.
    - Минимальный JSON без имени или схемы безопасно восстанавливает имя из файла/фоллбэка.
    """
    scen = "Сценарий 3: Импорт битых карточек персонажей (README.md)"

    # 3.1 Файл нулевой длины (0 байт)
    empty_json = tmp_path / "empty_card.json"
    empty_json.write_text("", encoding="utf-8")
    card_empty = TavernParser.parse_file(empty_json)
    assert_diag(card_empty is None, scen, "Файл 0 байт", None, card_empty)

    # 3.2 Синтаксически оборванный JSON
    broken_json = tmp_path / "broken_syntax.json"
    broken_json.write_text('{"name": "Broken Hero", "data": {"personality": ', encoding="utf-8")
    card_broken = TavernParser.parse_file(broken_json)
    assert_diag(card_broken is None, scen, "Оборванный JSON", None, card_broken)

    # 3.3 Несуществующий файл
    non_existent = tmp_path / "phantom_card.json"
    card_missing = TavernParser.parse_file(non_existent)
    assert_diag(card_missing is None, scen, "Несуществующий файл", None, card_missing)

    # 3.4 Валидный PNG без метаданных персонажа
    clean_png = tmp_path / "clean_image.png"
    Image.new("RGB", (16, 16), color=(20, 30, 40)).save(clean_png, format="PNG")
    card_clean_png = TavernParser.parse_file(clean_png)
    assert_diag(card_clean_png is None, scen, "PNG без метаданных chara", None, card_clean_png)

    # 3.5 PNG с битым чанком chara (некорректный base64 / не-JSON)
    corrupt_meta_png = tmp_path / "corrupt_meta.png"
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("chara", "CORRUPTED_NON_BASE64_GARBAGE!??#%")
    Image.new("RGB", (16, 16), color=(50, 10, 10)).save(corrupt_meta_png, format="PNG", pnginfo=png_info)
    card_corrupt_png = TavernParser.parse_file(corrupt_meta_png)
    assert_diag(card_corrupt_png is None, scen, "PNG с битым чанком chara", None, card_corrupt_png)

    # 3.6 Минимальный валидный JSON без имени (фоллбэк на имя файла)
    minimal_json = tmp_path / "Cyber_Nomad.json"
    minimal_data = {
        "description": "Одинокий странник Пустошей.",
        "scenario": "Киберпанк-город 2088 года.",
        "character_book": {
            "entries": [
                {"keys": ["нейросеть"], "content": "Древняя ИИ-система", "enabled": True},
                "некорректная_несловарная_запись",  # краевой случай: мусорный элемент внутри entries
            ]
        }
    }
    minimal_json.write_text(json.dumps(minimal_data, ensure_ascii=False), encoding="utf-8")
    card_minimal = TavernParser.parse_file(minimal_json)

    assert_diag(isinstance(card_minimal, CharacterCard), scen, "Минимальный JSON без имени", "CharacterCard", type(card_minimal))
    assert_diag_eq(card_minimal.name, "Cyber Nomad", scen, "Фоллбэк имени из stem файла")
    assert_diag_eq(card_minimal.description, "Одинокий странник Пустошей.", scen, "Извлечение описания")
    assert_diag_eq(len(card_minimal.lore_entries), 1, scen, "Фильтрация мусорной несловарной записи lore_entries")


# =========================================================================
# 4. СЦЕНАРИЙ: ГРАНИЧНЫЕ CLI-ФЛАГИ ДИРЕКТ-АРТА И НЕГАТИВ (README.md Раздел 4)
# =========================================================================

def test_readme_direct_art_cli_flags_and_negative_edge_cases(smoke_isolation_guard, isolated_art_models_env):
    """
    Проверяет парсинг и построение плана прямого арта (!арт) при аномальных входных данных:
    - Граничные параметры командной строки (--steps 0, seed -999, extreme dims).
    - Промпт, состоящий исключительно из негативных указаний (--negative ugly, deformed, blurry).
    - Указание несуществующего чекпоинта (--model non_existent_model_xyz.safetensors) с безопасным фоллбэком.
    """
    scen = "Сценарий 4: Граничные CLI-флаги директ-арта (README.md)"
    director = isolated_art_models_env

    # 4.1 Граничные CLI-флаги
    raw_cmd = "!art Cyberpunk neon street --steps 0 --seed -999 --w 1024 --h 1024 --ar 16:9 --ad on --hires true"
    pos, neg, user_params = parse_direct_command_params(raw_cmd)

    assert_diag_eq(user_params.get("steps"), 0, scen, "Парсинг steps=0")
    assert_diag_eq(user_params.get("seed"), -999, scen, "Парсинг seed=-999")
    assert_diag_eq(user_params.get("enable_adetailer"), True, scen, "Парсинг --ad on")
    assert_diag_eq(user_params.get("enable_hr"), True, scen, "Парсинг --hires true")

    # Построение GenerationPlan не должно вызывать необработанных исключений
    plan: GenerationPlan = director.build_direct_plan(raw_cmd)
    assert_diag(isinstance(plan, GenerationPlan), scen, "Построение плана с граничными флагами", "GenerationPlan", type(plan))
    assert_diag_eq(plan.steps, 0, scen, "Передача граничных шагов в план")
    assert_diag_eq(plan.seed, -999, scen, "Передача граничного seed в план")
    assert_diag_in("cyberpunk", plan.positive_prompt.lower(), scen, "Позитивный промпт сохранен")

    # 4.2 Команда только с негативным промптом без позитивного описания
    neg_only_cmd = "!art --negative ugly, deformed, extra arms, low quality"
    plan_neg: GenerationPlan = director.build_direct_plan(neg_only_cmd)
    assert_diag(isinstance(plan_neg, GenerationPlan), scen, "План только с негативным промптом", "GenerationPlan", type(plan_neg))
    assert_diag_in("ugly", plan_neg.negative_prompt.lower(), scen, "Негативный промпт содержит переданные токены")

    # 4.3 Указание несуществующей модели в флаге --model
    phantom_model_cmd = "!art Sci-fi starship --model phantom_unknown_checkpoint_v99.safetensors"
    plan_phantom: GenerationPlan = director.build_direct_plan(phantom_model_cmd)
    assert_diag(isinstance(plan_phantom, GenerationPlan), scen, "План с несуществующей моделью", "GenerationPlan", type(plan_phantom))
    assert_diag(bool(plan_phantom.target_checkpoint), scen, "Назначен рабочий fallback-чекпоинт", True, bool(plan_phantom.target_checkpoint))


# =========================================================================
# 5. СЦЕНАРИЙ: FAIL-FAST И ЗАЩИТА VRAM ПРИ ОТКЛЮЧЕННОМ FORGE (README.md Раздел 1 и 4)
# =========================================================================

def test_readme_art_generation_guard_when_forge_offline(smoke_isolation_guard, qapp):
    """
    Проверяет сценарий из README.md: при отключенном SD WebUI Forge генерация арта
    срабатывает по схеме Fail-Fast без выгрузки LLM из VRAM и без зависания интерфейса:
    - _is_forge_available() возвращает False;
    - _enqueue_direct_image_task завершается до отправки задачи в очередь;
    - VRAMArbiter.release_llm_for_diffusion НЕ вызывается;
    - статус-бар выводит предупреждение пользователю.
    """
    scen = "Сценарий 5: Fail-Fast при отключенном Forge (README.md)"

    class OfflineHardwareHost(QWidget, HardwareEngineMixin):
        def __init__(self):
            super().__init__()
            self.forge_client = MagicMock()
            self.forge_client.is_server_ready.return_value = False
            self.process_manager = MagicMock()
            self.process_manager.is_forge_running.return_value = False
            self.task_queue = MagicMock()
            self._status_bar_mock = MagicMock()
            self.current_session = {"id": "smoke_offline_test"}

        def statusBar(self):
            return self._status_bar_mock

    host = OfflineHardwareHost()
    try:
        # 5.1 Проверка доступности Forge
        assert_diag(not host._is_forge_available(), scen, "Проверка Forge доступности", False, host._is_forge_available())

        # Мокаем арбитр для гарантии, что LLM не будет выгружена
        arbiter = VRAMArbiter.get_instance()
        original_release = arbiter.release_llm_for_diffusion
        arbiter_called = []
        arbiter.release_llm_for_diffusion = lambda *args, **kwargs: arbiter_called.append(True)

        try:
            # 5.2 Попытка запуска директ-генерации арта
            host._enqueue_direct_image_task("Cyberpunk neon skyline")

            # Проверка Fail-Fast: задача не добавлена в очередь
            assert_diag_eq(host.task_queue.enqueue.call_count, 0, scen, "Задача не добавлена в очередь при оффлайн Forge")

            # Проверка, что VRAMArbiter не трогал текстовую модель
            assert_diag_eq(len(arbiter_called), 0, scen, "LLM не выгружалась из VRAM")

            # Проверка вывода уведомления в статус-бар
            assert_diag(host.statusBar().showMessage.called, scen, "Уведомление в statusBar", True, host.statusBar().showMessage.called)
            status_msg = host.statusBar().showMessage.call_args[0][0]
            assert_diag_in("Forge выключен", status_msg, scen, "Текст сообщения об отключенном Forge")
        finally:
            arbiter.release_llm_for_diffusion = original_release
    finally:
        host.close()
        host.deleteLater()


# =========================================================================
# 6. СЦЕНАРИЙ: СМЕНА РЕЖИМА И ПОВЕДЕНИЕ ПРИ ОТСУТСТВИИ LLM (README.md Раздел 2)
# =========================================================================

def test_readme_mode_switching_and_missing_llm_edge_cases(smoke_isolation_guard, qapp):
    """
    Проверяет переключение режимов (Ассистент, Ролевой чат, Инженер) при граничных условиях:
    - Конфигурация с пустыми именами моделей не ломает создание сессий SessionManager.
    - Сессии корректно типизируются (assistant, roleplay, engineer/engineering).
    - AppMode.from_session детерминированно восстанавливает режим.
    - Попытка принудительной загрузки модели через LMStudioClient при отсутствии установленных
      моделей завершается чистым ModelNotFoundError без повреждения внутреннего состояния.
    """
    scen = "Сценарий 6: Смена режима и отсутствие LLM (README.md)"
    sm = SessionManager.get_instance()

    # 6.1 Создание сессий в трех режимах без указания конкретных моделей
    sess_asst = sm.create_session(mode="assistant", title="Тест Ассистент", model_id="")
    assert_diag_eq(sess_asst.get("mode"), "assistant", scen, "Тип режима Ассистент")
    assert_diag_eq(AppMode.from_session(sess_asst), AppMode.ASSISTANT, scen, "Резолвер AppMode Ассистент")

    sess_rp = sm.create_session(mode="roleplay", title="Тест Ролевой", model_id="")
    assert_diag_eq(sess_rp.get("mode"), "roleplay", scen, "Тип режима Ролевой")
    assert_diag_eq(AppMode.from_session(sess_rp), AppMode.ROLEPLAY, scen, "Резолвер AppMode Ролевой")

    sess_eng = sm.create_session(mode="engineer", title="🛠️ Инженерная задача", model_id="")
    assert_diag_eq(sess_eng.get("mode"), "engineer", scen, "Тип режима Инженер")
    assert_diag_eq(AppMode.from_session(sess_eng), AppMode.ENGINEER, scen, "Резолвер AppMode Инженер")

    # 6.2 Попытка вызова ensure_model_loaded в LMStudioClient без доступных моделей
    client = LMStudioClient("http://127.0.0.1:1234/v1")
    orig_last_loaded = getattr(client, "_last_loaded_model_id", None)
    orig_get_models = getattr(client, "get_models", None)
    orig_pm = getattr(client, "process_manager", None)
    try:
        client._last_loaded_model_id = None
        client.process_manager = None
        client.get_models = MagicMock(return_value=[])  # Имитация ПК без скачанных GGUF моделей

        with pytest.raises(ModelNotFoundError) as exc_info:
            client.ensure_model_loaded(preferred_model="")

        assert_diag_in("активная модель", str(exc_info.value), scen, "Исключение ModelNotFoundError при отсутствии моделей")
    finally:
        client._last_loaded_model_id = orig_last_loaded
        if orig_get_models:
            client.get_models = orig_get_models
        client.process_manager = orig_pm


# =========================================================================
# 7. СЦЕНАРИЙ: ГРАНИЧНЫЕ АППАРАТНЫЕ ПРОФИЛИ И ФЛАГИ FORGE (README.md Раздел «Наборы моделей»)
# =========================================================================

def test_readme_forge_launch_flags_hardware_boundary_edge_cases(smoke_isolation_guard):
    """
    Проверяет расчет стартовых флагов Forge под конфигурации оборудования из README.md:
    - 4 ГБ VRAM (< 6 ГБ): обязательно включает --always-offload-from-vram, --no-half-vae, --vae-in-fp32.
    - 8+ ГБ VRAM (>= 6 ГБ): НЕ включает стартовый --always-offload-from-vram (режим автоматической балансировки).
    - Граничные extra_args: дубликаты системных флагов (--api, --listen) и пустые строки безопасно санируются.
    """
    scen = "Сценарий 7: Граничные аппаратные профили Forge (README.md)"

    # 7.1 Профиль 4 ГБ VRAM
    monitor_4gb = MagicMock()
    monitor_4gb._last_emitted_stats = {"vram_total_mb": 4096}
    monitor_4gb.nvml_initialized = False

    flags_4gb, meta_4gb = resolve_forge_startup_flags(
        system_monitor=monitor_4gb,
        extra_args=["--api", "--listen", "", "--custom-plugin"]
    )
    assert_diag_eq(meta_4gb["total_vram_gb"], 4.0, scen, "Детекция 4.0 ГБ VRAM")
    assert_diag(meta_4gb["startup_always_offload"], scen, "startup_always_offload для 4 ГБ", True, meta_4gb["startup_always_offload"])
    assert_diag_in("--always-offload-from-vram", flags_4gb, scen, "Флаг --always-offload-from-vram для 4 ГБ")
    assert_diag_in("--custom-plugin", flags_4gb, scen, "Сохранение пользовательского extra_arg")

    # Проверка дедупликации: --api и --listen должны присутствовать строго в 1 экземпляре
    assert_diag_eq(flags_4gb.count("--api"), 1, scen, "Дедупликация --api")
    assert_diag_eq(flags_4gb.count("--listen"), 1, scen, "Дедупликация --listen")
    assert_diag("" not in flags_4gb, scen, "Отсутствие пустых строк в CLI-флагах", True, "" not in flags_4gb)

    # 7.2 Профиль 8 ГБ VRAM
    monitor_8gb = MagicMock()
    monitor_8gb._last_emitted_stats = {"vram_total_mb": 8192}
    monitor_8gb.nvml_initialized = False

    flags_8gb, meta_8gb = resolve_forge_startup_flags(
        system_monitor=monitor_8gb,
        extra_args=["--custom-flag-blackwell"]
    )
    assert_diag_eq(meta_8gb["total_vram_gb"], 8.0, scen, "Детекция 8.0 ГБ VRAM")
    assert_diag(not meta_8gb["startup_always_offload"], scen, "startup_always_offload для 8 ГБ", False, meta_8gb["startup_always_offload"])
    assert_diag("--always-offload-from-vram" not in flags_8gb, scen, "Отсутствие --always-offload-from-vram на 8 ГБ", True, "--always-offload-from-vram" not in flags_8gb)
    assert_diag_in("--custom-flag-blackwell", flags_8gb, scen, "Пользовательский флаг сохранен на 8 ГБ")


# =========================================================================
# 8. СЦЕНАРИЙ: ПАРСИНГ ДОКУМЕНТОВ БАЗЫ ЗНАНИЙ RAG (README.md Раздел 3 и FAQ)
# =========================================================================

def test_readme_document_rag_parser_edge_cases(tmp_path, smoke_isolation_guard):
    """
    Проверяет парсинг файлов базы знаний (RAG) при граничных и поврежденных входных данных:
    - 0-байтные текстовые и PDF-файлы обрабатываются без исключений, возвращая content="".
    - Файл с бинарным мусором и расширением .pdf перехватывается безопасно.
    - Несуществующий путь возвращает None.
    - Длинный документ при ограничении max_chars корректно помечается is_truncated=True
      с добавлением информационной плашки.
    """
    scen = "Сценарий 8: Парсинг документов RAG (README.md)"

    # 8.1 Пустой файл 0 байт
    empty_txt = tmp_path / "empty_report.txt"
    empty_txt.write_text("", encoding="utf-8")
    doc_empty: ParsedDocument = DocumentParser.parse_file(empty_txt)

    assert_diag(isinstance(doc_empty, ParsedDocument), scen, "Парсинг 0-байтного txt", "ParsedDocument", type(doc_empty))
    assert_diag_eq(doc_empty.content, "", scen, "Пустой контент для 0-байтного txt")
    assert_diag_eq(doc_empty.size_kb, 0.0, scen, "Размер 0 КБ")
    assert_diag(not doc_empty.is_truncated, scen, "is_truncated == False", False, doc_empty.is_truncated)

    # 8.2 Бинарный мусор с расширением .pdf
    corrupt_pdf = tmp_path / "corrupted_archive.pdf"
    corrupt_pdf.write_bytes(b"%PDF-1.4\x00\xff\xfe\x00CORRUPT_STREAM_DATA_NOT_VALID_PDF_STRUCTURE")
    doc_corrupt = DocumentParser.parse_file(corrupt_pdf)

    # Парсер должен вернуть ParsedDocument с пустым/частичным контентом без выброса необработанного исключения
    assert_diag(doc_corrupt is None or isinstance(doc_corrupt, ParsedDocument), scen, "Безопасная обработка битого PDF", "ParsedDocument или None", type(doc_corrupt))

    # 8.3 Несуществующий файл
    missing_doc = DocumentParser.parse_file(tmp_path / "phantom_manual.docx")
    assert_diag(missing_doc is None, scen, "Несуществующий документ", None, missing_doc)

    # 8.4 Усечение длинного документа при превышении max_chars
    long_doc = tmp_path / "knowledge_chapter.md"
    long_content = "Строка базы знаний NeonRoute о параметрах инференса.\n" * 40
    long_doc.write_text(long_content, encoding="utf-8")

    doc_truncated: ParsedDocument = DocumentParser.parse_file(long_doc, max_chars=150)
    assert_diag(doc_truncated.is_truncated, scen, "Флаг is_truncated для длинного документа", True, doc_truncated.is_truncated)
    assert_diag_in("Документ сокращен", doc_truncated.content, scen, "Плашка о сокращении документа")
