"""
Глубокие контрактные смок-тесты для 3 режимов (Assistant, Roleplay, Engineer) и ArtDirector.
Защищают от регрессий при добавлении новых фич, отрабатывая менее чем за 1 секунду.
"""
from pathlib import Path
import pytest

import config
from services.code_engine import AutoImporter, ASTSkeletonizer
from services.patch_engine import PatchApplier
from services.tool_parser import ToolParser
from services.markdown_chunker import MarkdownChunker
from services.document_parser import DocumentParser
from services.macro_engine import MacroEngine
from services.regex_filter import TextRegexFilter
from services.rp_scene_updater import apply_scene_updates_to_cast
from services.roleplay_state_manager import RoleplayStateManager
from services.art_director.director import _symmetrize_break_chunks
from services.art_director.prompting import extract_negations_from_prompt
from services.minor_safety import MinorSafetyDetector
from ui.components.rpg_elements import RPGContentParser


# =========================================================================
# РЕЖИМ 1: ИНЖЕНЕР (PatchApplier, AutoImporter, ASTSkeletonizer, ToolParser)
# =========================================================================

def test_engineer_patch_applier_surgical_search_replace():
    """Проверка хирургического патчера SEARCH/REPLACE с сохранением отступов."""
    original = (
        "class Engine:\n"
        "    def start(self):\n"
        "        return 'cold'\n"
    )
    patch_text = (
        "<<<<<<< SEARCH\n"
        "    def start(self):\n"
        "        return 'cold'\n"
        "=======\n"
        "    def start(self):\n"
        "        return 'warm_ready'\n"
        ">>>>>>> REPLACE"
    )
    res = PatchApplier.apply_diff_text(original, patch_text)
    assert res.success is True
    assert "warm_ready" in res.new_content


def test_engineer_auto_importer_zero_token_healing():
    """Проверка Zero-Token Healing: авто-инъекция забытого стандартного импорта по NameError."""
    target_py = config.DATA_DIR / "heal_target.py"
    target_py.write_text("def area(r: float) -> float:\n    return math.pi * (r ** 2)\n", encoding="utf-8")
    injected = AutoImporter.try_inject_import(target_py, "math")
    assert injected is True
    assert "import math" in target_py.read_text(encoding="utf-8")


def test_engineer_ast_skeletonizer_preserves_signatures():
    """Проверка AST-скелетирования крупных файлов (>600 строк) для экономии контекста Архитектора."""
    source = (
        "class DataProcessor:\n"
        "    def transform(self, items: list[int]) -> list[int]:\n"
        "        res = []\n"
        "        for x in items:\n"
        "            res.append(x * 2)\n"
        "        return res\n"
    )
    skeleton = ASTSkeletonizer.skeletonize(source)
    assert "class DataProcessor" in skeleton
    assert "def transform" in skeleton


def test_engineer_tool_parser_write_and_patch_tags():
    """Проверка парсера инструментов <<TOOL:...>> для write_file и replace_symbol."""
    llm_out = (
        '<<TOOL:write_file filename="app.py">>\n'
        'print("ok")\n'
        '<</TOOL>>\n'
        '<<TOOL:replace_symbol filename="app.py" name="foo">>\n'
        'def foo(): return 1\n'
        '<</TOOL>>'
    )
    calls = ToolParser.parse_tool_calls(llm_out)
    assert len(calls) == 2
    names = [c.name for c in calls]
    assert "write_file" in names and "replace_symbol" in names


# =========================================================================
# РЕЖИМ 2: АССИСТЕНТ (MarkdownChunker, DocumentParser, TextRegexFilter)
# =========================================================================

def test_assistant_markdown_chunker_code_block_integrity():
    """Проверка структурного нарезчика RAG: кодовые блоки ``` не должны разрываться пополам."""
    md_text = (
        "# Раздел 1\n" + ("Описание архитектуры. " * 25) + "\n"
        "```python\n"
        "def critical_function():\n"
        "    return 'must_stay_together'\n"
        "```\n"
        "# Раздел 2\n" + ("Дополнительные сведения. " * 25)
    )
    chunks = MarkdownChunker.chunk_document(md_text, filename="arch.md", min_chunk=200, max_chunk=600, overlap=50)
    assert len(chunks) >= 1
    assert any("critical_function" in c.get("text", "") for c in chunks)


def test_assistant_document_parser_and_regex_cleaning():
    """Проверка чтения документов .md/.txt и очистки спецтокенов в ответах модели."""
    doc_file = config.DATA_DIR / "spec_test.md"
    doc_file.write_text("Модуль VRAMArbiter управляет памятью 8 ГБ.", encoding="utf-8")
    extracted = DocumentParser.extract_text(doc_file)
    assert "VRAMArbiter" in extracted

    dirty = "<think>Внутренний анализ</think>Чистый ответ пользователю. [IMAGE_PROMPT: 1girl, neon]<|im_end|>"
    img_prompt, rest = TextRegexFilter.extract_image_prompt(dirty)
    assert img_prompt and "1girl" in img_prompt
    cleaned = TextRegexFilter.process_output(rest)
    assert "<think>" not in cleaned and "<|im_end|>" not in cleaned
    assert "Чистый ответ пользователю." in cleaned


# =========================================================================
# РЕЖИМ 3: РОЛЕВОЙ ЧАТ (RPGContentParser, MacroEngine, RPSceneUpdater)
# =========================================================================

def test_roleplay_rpg_content_parser_widgets_extraction():
    """Проверка парсера RPG-разметки (<stats>, <meters>, <choices>, <check>)."""
    rp_message = (
        "Стражник преградил путь.\n"
        "<check>Убеждение | Сложность 15 | Успех</check>\n"
        "<stats>\nЛокация: Ворота замка\nИнвентарь: Меч, Перстень\n</stats>\n"
        "<meters>\nДоверие: 65%\n</meters>\n"
        "<choices>\n1. Показать пропуск\n2. Обойти стену\n</choices>"
    )
    bundle = RPGContentParser.parse(rp_message)
    assert bundle is not None
    assert bundle.choices and len(bundle.choices) == 2
    assert bundle.stats is not None or bundle.meters is not None


def test_roleplay_scene_updater_relic_protection_and_npc_updates():
    """Проверка транзакционного обновления сцены (apply_scene_updates_to_cast): инвентарь, локация и NPC."""
    sess_id = "smoke_rp_cast_01"
    initial_cast = RoleplayStateManager.load_cast(sess_id, user_name="Игрок", companion_name="Лира")
    initial_cast["inventory"] = ["Старый плащ", "Квестовый Амулет Вечности"]
    RoleplayStateManager.save_cast(sess_id, initial_cast)

    scene_delta = {
        "new_location": "Темный лес",
        "items_added": ["Факел"],
        "npcs_present": [{"name": "Лесник Бран", "role": "npc", "visual_dna": "tall bearded ranger"}],
    }
    apply_scene_updates_to_cast(sess_id, scene_delta, user_name="Игрок", companion_name="Лира")
    updated = RoleplayStateManager.load_cast(sess_id, user_name="Игрок", companion_name="Лира")
    assert updated["current_location"] == "Темный лес"
    assert "Квестовый Амулет Вечности" in updated["inventory"]


def test_roleplay_macro_engine_dynamic_substitutions():
    """Проверка динамических макросов {{user}}, {{char}} и бросков кубика {{roll:XdY+Z}}."""
    raw = "{{char}} смотрит на {{user}}. Результат: {{roll:1d1+10}}"
    out = MacroEngine.process(raw, user_name="Рейн", char_name="Сильвия")
    assert "Сильвия смотрит на Рейн" in out
    assert "11" in out


# =========================================================================
# КОНТУР 4: АРТ-ДИРЕКТОР И БЕЗОПАСНОСТЬ (BREAK, Negations, MinorSafety)
# =========================================================================

def test_art_director_break_symmetrization_and_negations():
    """Проверка симметризации BREAK для мульти-персонажных сцен и выноса отрицаний в негатив."""
    pos_clean, neg_extra = extract_negations_from_prompt("1girl, cyberpunk street, no helmet, without glasses")
    assert "helmet" in neg_extra and "glasses" in neg_extra
    assert "no helmet" not in pos_clean

    sym_prompt = _symmetrize_break_chunks(
        "1girl, silver hair BREAK 1boy, dark armor",
        "ancient castle hall",
        "torchlight"
    )
    assert "BREAK" in sym_prompt
    assert sym_prompt.count("ancient castle hall") == 2


def test_minor_safety_detector_blocks_underage_tags():
    """Проверка фильтра возрастной безопасности MinorSafetyDetector."""
    assert MinorSafetyDetector.is_minor("24 years old veteran knight") is False
    assert MinorSafetyDetector.is_minor("14yo schoolgirl character") is True
    safe_prompt = MinorSafetyDetector.sanitize_positive_prompt("1girl, loli, nude, nsfw", context_hint="14yo")
    assert "nude" not in safe_prompt and "nsfw" not in safe_prompt
