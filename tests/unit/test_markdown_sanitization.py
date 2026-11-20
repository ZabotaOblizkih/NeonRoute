"""
Unit tests for Markdown-safe regex sanitization and streaming suppression.
Verifies that Markdown code examples (--ar, --model, <thought>, [IMAGE_PROMPT: ...], lists)
are preserved while real image generation triggers still extract and fire cleanly.
"""
from unittest.mock import MagicMock

from services.regex_filter import TextRegexFilter
from ui.controllers.assistant_flow_controller import (
    AssistantFlowControllerMixin,
    sanitize_assistant_art_and_markdown,
)
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin


def test_text_regex_filter_preserves_markdown_code_and_lists():
    sample = (
        "- Первый пункт\n"
        "- Второй пункт\n"
        "Пример флага `--model realistic` и тега `<thought>` внутри кода.\n"
        "```\n"
        "<think>пример внутри блока кода</think>\n"
        "- элемент внутри кода\n"
        "```"
    )
    cleaned_assistant = TextRegexFilter.process_output(sample, format_dialogue_dashes=False)
    assert cleaned_assistant == sample

    cleaned_rp = TextRegexFilter.process_output(sample, format_dialogue_dashes=True)
    assert cleaned_rp.startswith("— Первый пункт\n— Второй пункт\n")
    assert "`--model realistic`" in cleaned_rp
    assert "`<thought>`" in cleaned_rp
    assert "<think>пример внутри блока кода</think>" in cleaned_rp
    assert "- элемент внутри кода" in cleaned_rp


def test_text_regex_filter_still_cleans_real_artifacts():
    dirty = "<think>Скрытые мысли</think>Готовый ответ. --model realistic<|im_end|>"
    cleaned = TextRegexFilter.process_output(dirty, format_dialogue_dashes=False)
    assert cleaned == "Готовый ответ."


def test_assistant_sanitization_preserves_markdown_tag_examples():
    reply_with_flags = (
        "Да, Виктор! Я готова. Просто скажи мне, что именно ты хочешь, "
        "и я создам для тебя качественное изображение. Теги будут на английском "
        "(например, `1girl, solo, portrait --ar portrait --model realistic`)."
    )
    clean, has_gallery, prompt = sanitize_assistant_art_and_markdown(
        reply_with_flags, user_requested_art=False
    )
    assert clean == reply_with_flags
    assert has_gallery is False
    assert prompt is None

    reply_with_image_example = (
        "Для генерации используется тег "
        "`[IMAGE_PROMPT: 1girl, rain, cyberpunk --ar portrait --model realistic]`, "
        "а также `Positive Prompt:` и `![preview](img.png)`."
    )
    clean2, has_gallery2, prompt2 = sanitize_assistant_art_and_markdown(
        reply_with_image_example, user_requested_art=False
    )
    assert clean2 == reply_with_image_example
    assert has_gallery2 is False
    assert prompt2 is None


def test_assistant_sanitization_extracts_real_image_prompts():
    # 1. Обычный незакавыченный тег работает даже без явного триггера в тексте вопроса
    raw_normal = (
        "Секунду, перевожу образ в кадр...\n\n"
        "[IMAGE_PROMPT: rocky coastline, calm sea, lighthouse --ar wide --model dreamshaper_8]"
    )
    clean, has_gallery, prompt = sanitize_assistant_art_and_markdown(
        raw_normal, user_requested_art=False
    )
    assert clean == "Секунду, перевожу образ в кадр..."
    assert has_gallery is False
    assert prompt == "rocky coastline, calm sea, lighthouse --ar wide --model dreamshaper_8"

    # 2. Если пользователь просил нарисовать, а модель обернула тег в backticks — генерация всё равно срабатывает
    raw_backticked = "`[IMAGE_PROMPT: 1girl, cozy cafe, morning light --ar portrait --model realistic]`"
    clean_bt, has_gallery_bt, prompt_bt = sanitize_assistant_art_and_markdown(
        raw_backticked, user_requested_art=True
    )
    assert clean_bt == ""
    assert has_gallery_bt is False
    assert prompt_bt == "1girl, cozy cafe, morning light --ar portrait --model realistic"

    # 3. В арт-ответе хвостовые CLI-флаги вне кода по-прежнему вырезаются
    raw_leaked = "[GALLERY]\nРисую пейзаж! --ar wide --model dreamshaper_8"
    clean_gal, has_gallery_gal, prompt_gal = sanitize_assistant_art_and_markdown(
        raw_leaked, user_requested_art=True
    )
    assert clean_gal == "Рисую пейзаж!"
    assert has_gallery_gal is True
    assert prompt_gal is None


def test_handle_assistant_llm_response_preserves_thought_tag_inside_code():
    class DummyFlow(AssistantFlowControllerMixin):
        def __init__(self):
            self._thought_stream_parser = None
            self._turn_bubble = None
            self._current_streaming_widget = None
            self._current_user_request = "Какие теги ты используешь?"
            self._is_running_gallery_art_pass = False
            self.is_busy = True
            self.synced = []
            self.appended = []

        def _get_current_avatar_path(self):
            return None

        def _append_message(self, sender, text, is_user=False, avatar_path=None):
            w = MagicMock()
            w.raw_text = text
            self.appended.append((sender, text))
            return w

        def _sync_turn(self, **kwargs):
            self.synced.append(kwargs)

        def _trigger_assistant_memory_extraction(self):
            pass

    flow = DummyFlow()
    sample = (
        "<think>Внутреннее рассуждение</think>"
        "Теги мыслей оформляются как `<thought>`, а затем идёт текст ответа."
    )
    flow._handle_assistant_llm_response(sample)

    assert len(flow.synced) == 1
    saved = flow.synced[0]["content"]
    assert "`<thought>`" in saved
    assert "а затем идёт текст ответа." in saved
    assert flow.synced[0]["thought"] is not None
    assert "Внутреннее рассуждение" in flow.synced[0]["thought"]["content"]


class _DummyStreamController(ChatFlowControllerMixin):
    def __init__(self):
        self._turn_bubble = MagicMock()
        self._turn_bubble.raw_text = ""
        self._stream_token_buffer = ""
        self._stream_flush_timer = MagicMock()
        self._stream_flush_timer.isActive.return_value = False

        def _append_chunk(c):
            self._turn_bubble.raw_text += c

        self._turn_bubble.append_stream_chunk.side_effect = _append_chunk

    def _scroll_to_bottom(self):
        pass

    def is_engineer_mode_active(self):
        return False


def test_stream_buffer_suppresses_real_image_prefix_without_leaking():
    ctrl = _DummyStreamController()
    for ch in ["Привет! ", "[", "I", "M", "A", "G", "E_PROMPT: 1girl, neon]"]:
        ctrl._stream_token_buffer += ch
        ctrl._flush_stream_buffer(force=False)
    ctrl._flush_stream_buffer(force=True)

    assert ctrl._turn_bubble.raw_text == "Привет! "
    assert ctrl._turn_bubble.show_art_loading_placeholder.called


def test_stream_buffer_preserves_image_prompt_inside_markdown_code():
    ctrl = _DummyStreamController()
    ctrl._stream_token_buffer = "Пример: `[IMAGE_PROMPT: 1girl, neon]` — вот так."
    ctrl._flush_stream_buffer(force=True)

    assert ctrl._turn_bubble.raw_text == "Пример: `[IMAGE_PROMPT: 1girl, neon]` — вот так."
    assert not ctrl._turn_bubble.show_art_loading_placeholder.called


def test_extract_image_prompt_ignores_thoughts_and_picks_last_match():
    raw = (
        "<think>Черновик: [IMAGE_PROMPT: cinematic view, epic landscape, river, mountains --ar wide]</think>\n"
        "Секунду, перевожу образ в кадр...\n\n"
        "[IMAGE_PROMPT: medium shot, serene river flowing through lush green mountains, panoramic view --ar wide]"
    )
    prompt, clean = TextRegexFilter.extract_image_prompt(raw)
    assert prompt == "medium shot, serene river flowing through lush green mountains, panoramic view --ar wide"
    assert clean == "Секунду, перевожу образ в кадр..."

    clean_ui, has_gallery, extracted = sanitize_assistant_art_and_markdown(raw, user_requested_art=True)
    assert clean_ui == "Секунду, перевожу образ в кадр..."
    assert has_gallery is False
    assert extracted == prompt


def test_extract_image_prompt_handles_thought_variants_and_draft_only():
    # 1. Черновик только внутри <reasoning> / неявного </think> / незакрытого <analyze> не запускает арт
    for sample in (
        "<reasoning>[IMAGE_PROMPT: draft_1]</reasoning>Финальный текст без тега.",
        "Скрытый черновик [IMAGE_PROMPT: draft_2]</think>Финальный текст без тега.",
        "Финальный текст.<analyze>незакрытый черновик [IMAGE_PROMPT: draft_3]",
    ):
        prompt, clean = TextRegexFilter.extract_image_prompt(sample)
        assert prompt is None
        assert clean == "Финальный текст." if "Финальный текст." in sample else "Финальный текст без тега."

    # 2. Несколько тегов вне мыслей -> берётся последний, а из текста вырезаются все
    multi = "Вот первый [IMAGE_PROMPT: first_draft] и исправленный: [IMAGE_PROMPT: final_tag]"
    prompt_m, clean_m = TextRegexFilter.extract_image_prompt(multi)
    assert prompt_m == "final_tag"
    assert clean_m == "Вот первый  и исправленный:" or clean_m == "Вот первый  и исправленный:".strip()
    assert "[IMAGE_PROMPT:" not in clean_m


def test_sanitize_assistant_ignores_gallery_and_backtick_draft_inside_think():
    raw = (
        "<think>Может быть [GALLERY] или `[IMAGE_PROMPT: draft_in_code]`?</think>\n"
        "Вот чистовой вариант:\n"
        "`[IMAGE_PROMPT: final_in_backticks --ar portrait]`"
    )
    clean, has_gallery, extracted = sanitize_assistant_art_and_markdown(raw, user_requested_art=True)
    assert has_gallery is False
    assert extracted == "final_in_backticks --ar portrait"
    assert clean == "Вот чистовой вариант:"


def test_gallery_art_pass_ignores_draft_inside_think():
    dispatched = []

    class DummyGalleryPassFlow(AssistantFlowControllerMixin):
        def __init__(self):
            self._is_running_gallery_art_pass = True
            self._pending_gallery_art_widget = MagicMock()
            self._pending_gallery_user_query = "река и горы"
            self.is_busy = True

        def _dispatch_art_to_forge(self, extracted_image_prompt, target_widget, raw_user_query):
            dispatched.append((extracted_image_prompt, raw_user_query))

    flow = DummyGalleryPassFlow()
    flow._handle_assistant_llm_response(
        "<think>Draft: [IMAGE_PROMPT: draft river --ar wide]</think>\n"
        "[IMAGE_PROMPT: final serene river, mountains --ar wide]"
    )
    assert dispatched == [("final serene river, mountains --ar wide", "река и горы")]


def test_text_regex_filter_typos_and_casing():
    """Проверяет извлечение и очистку тегов с опечатками и разным регистром (SSOT)."""
    samples = [
        ("[IMAGE_PROP: 1girl, cyberpunk samurai]", "1girl, cyberpunk samurai"),
        ("[image_prop: 1girl, neon city]", "1girl, neon city"),
        ("[Image-Prompt: 1girl, castle courtyard]", "1girl, castle courtyard"),
        ("[IMAGE_PROMT: 1girl, space station]", "1girl, space station"),
        ("[IMG_PROMPT: 1girl, snowy forest]", "1girl, snowy forest"),
        ("[img_prop: 1girl, sunny beach]", "1girl, sunny beach"),
    ]
    for tag_str, expected_prompt in samples:
        full_text = f"Вот описание сцены:\n{tag_str}\nПриятного просмотра!"
        prompt, clean = TextRegexFilter.extract_image_prompt(full_text)
        assert prompt == expected_prompt, f"Failed extracting prompt from: {tag_str}"
        assert tag_str not in clean
        assert "Вот описание сцены:" in clean
        assert "Приятного просмотра!" in clean

        # Проверка strip_image_tags
        stripped = TextRegexFilter.strip_image_tags(full_text)
        assert tag_str not in stripped
        assert "Вот описание сцены:" in stripped
        assert "Приятного просмотра!" in stripped


def test_stream_art_tag_filter_handles_typos_and_case():
    """Проверяет, что StreamArtTagFilter перехватывает опечатки [IMAGE_PROP: ...] в потоке."""
    from services.assistant_flow import StreamArtTagFilter

    for tag in ["[IMAGE_PROP: 1girl, samurai]", "[image_prop: 1girl, samurai]", "[IMAGE_PROMT: 1girl, samurai]"]:
        flt = StreamArtTagFilter()
        rendered, triggered = flt.process_buffer(f"Ответ {tag} конец", prior_text="", force=True)
        assert triggered is True
        assert tag not in rendered
        assert "Ответ" in rendered
        assert "конец" in rendered


def test_sanitize_assistant_art_with_typo_tags():
    """Проверяет sanitize_assistant_art_and_markdown с тегами [IMAGE_PROP: ...] и [image_prop: ...]."""
    raw_1 = "Конечно, вот арт:\n[IMAGE_PROP: 1girl, solo, portrait --ar portrait]\nНадеюсь, тебе понравится!"
    clean_1, has_gal_1, prompt_1 = sanitize_assistant_art_and_markdown(raw_1, user_requested_art=True)
    assert prompt_1 == "1girl, solo, portrait --ar portrait"
    assert "[IMAGE_PROP:" not in clean_1
    assert "Конечно, вот арт:" in clean_1

    raw_2 = "Держи изображение:\n[image_prop: 1girl, gothic dress --ar portrait]"
    clean_2, has_gal_2, prompt_2 = sanitize_assistant_art_and_markdown(raw_2, user_requested_art=True)
    assert prompt_2 == "1girl, gothic dress --ar portrait"
    assert "[image_prop:" not in clean_2


def test_roleplay_ephemeral_prompt_clean_typo_prefix(monkeypatch):
    """
    Проверяет, что в РП-пайплайне _generate_ephemeral_rp_art_prompt
    корректно очищает теги и префиксы IMAGE_PROP / image_prop без оставления префикса.
    """
    from services.roleplay_flow_controller import RoleplayFlowControllerMixin
    from services.tavern_models import CharacterCard
    from services.rp_cast_manager import RPCastManager

    controller = RoleplayFlowControllerMixin.__new__(RoleplayFlowControllerMixin)
    controller.lm_client = MagicMock()
    controller.active_character = CharacterCard(name="Лия", description="Маг", visual_dna="blue hair")

    structured_cast = {
        "scene_type": "dialogue",
        "environment": {"location_dna": "library"},
        "companion_state": {"visual_dna": "blue hair"},
        "inventory": []
    }
    monkeypatch.setattr(RPCastManager, "load_cast", MagicMock(return_value=structured_cast))

    # 1. Вариант в квадратных скобках с опечаткой [IMAGE_PROP: ...]
    controller.lm_client.chat_completion.return_value = "[IMAGE_PROP: 1girl, blue hair, reading book, library --model realistic]"
    prompt = controller._generate_ephemeral_rp_art_prompt("sess_1", "реплика")
    assert not prompt.lower().startswith("image")
    assert not prompt.lower().startswith("prop")
    assert "1girl, blue hair, reading book, library" in prompt

    # 2. Вариант без квадратных скобок (fallback по строкам): IMAGE_PROP: ...
    controller.lm_client.chat_completion.return_value = "IMAGE_PROP: 1girl, blue hair, casting spell, library --model realistic"
    prompt_fallback = controller._generate_ephemeral_rp_art_prompt("sess_1", "реплика")
    assert not prompt_fallback.lower().startswith("image")
    assert not prompt_fallback.lower().startswith("prop")
    assert "1girl, blue hair, casting spell, library" in prompt_fallback

    # 3. Вариант в нижнем регистре: [image_prop: ...]
    controller.lm_client.chat_completion.return_value = "[image_prop: 1girl, blue hair, resting, library]"
    prompt_lower = controller._generate_ephemeral_rp_art_prompt("sess_1", "реплика")
    assert not prompt_lower.lower().startswith("image")
    assert "1girl, blue hair, resting, library" in prompt_lower
