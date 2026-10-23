"""
Card Translator Service for NeonRoute.
Handles literary translation of character card fields with tag masking,
paragraph chunking, token budget adaptation, and CJK Quality Gate validation.
"""

import re
import logging
from typing import Optional, Callable, Any

from services.task_queue import InferenceTask
from services.session_manager import SessionManager

logger = logging.getLogger("NeonRoute.CardTranslator")

RE_TAG_PATTERN = re.compile(r'(\{\{[^}]+\}\}|<[^>]+>|\[IMAGE[\s_-]PROMPT:[^\]]+\])', re.IGNORECASE)
RE_CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')

PROMPT_LITERARY_PROSE = (
    "Ты — профессиональный литературный переводчик художественной ролевой прозы на русский язык.\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. Переводи выразительным литературным языком. Избегай дословной механической кальки "
    "(не «дает кивок», а «кивает»; не «делает шаг», а «шагает»). Адаптируй оборот под нормы русского языка.\n"
    "2. Переводи текст с ЛЮБОГО языка оригинала (английский, китайский, японский или смешанный) ПОЛНОСТЬЮ.\n"
    "3. СТРОГИЙ ЗАПРЕТ: в ответе не должно быть китайских иероглифов, японских символов или остаточных фраз на английском.\n"
    "4. Сохраняй неизменными системные идентификаторы вида __NR_TAG_X__ точно на своих местах.\n"
    "5. Выводи ТОЛЬКО перевод без предисловий, пояснений и внешних кавычек."
)

PROMPT_MES_EXAMPLE = (
    "Ты — профессиональный литературный переводчик ролевой прозы и диалогов на русский язык.\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. Переведи ВСЕ реплики и действия персонажей живым языком. Сохраняй разметку диалога и звездочки *действий*.\n"
    "2. Переводи с любого языка оригинала полностью без сокращений.\n"
    "3. СТРОГИЙ ЗАПРЕТ: никаких иероглифов и непереведенного текста.\n"
    "4. Сохраняй неизменными идентификаторы вида __NR_TAG_X__.\n"
    "5. Выводи ТОЛЬКО готовый перевод."
)

PROMPT_FIRST_MES = (
    "Ты — профессиональный писатель и литературный переводчик художественной прозы на русский язык.\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. Переведи вступительную сцену ПОЛНОСТЬЮ от первого до последнего предложения живым, богатым языком.\n"
    "2. Избегай дословной кальки (не «дает непроницаемый кивок», а «сдержанно кивает»; не «маленький салют», а «коротко отдает честь»).\n"
    "3. Сохраняй структуру абзацев, прямую речь и *действия* персонажей.\n"
    "4. СТРОГИЙ ЗАПРЕТ: никаких китайских/японских иероглифов или остаточных английских фраз.\n"
    "5. Сохраняй системные идентификаторы вида __NR_TAG_X__ неизменными.\n"
    "6. Выводи ТОЛЬКО перевод без лишних вводных слов."
)


def mask_card_tags(text: str) -> tuple[str, dict[str, str]]:
    """Маскирует макросы {{char}}, {{user}} и HTML/XML теги перед отправкой в LLM."""
    tag_map: dict[str, str] = {}
    counter = 0

    def _replace(match):
        nonlocal counter
        placeholder = f"__NR_TAG_{counter}__"
        tag_map[placeholder] = match.group(0)
        counter += 1
        return placeholder

    masked_text = RE_TAG_PATTERN.sub(_replace, text)
    return masked_text, tag_map


def unmask_card_tags(text: str, tag_map: dict[str, str]) -> str:
    """Восстанавливает исходные макросы и теги после перевода."""
    res = text
    for placeholder, original in sorted(tag_map.items(), key=lambda x: len(x[0]), reverse=True):
        m = re.search(r'\d+', placeholder)
        if m:
            num = m.group(0)
            pattern = re.compile(rf'_{{1,2}}\s*NR[\s_]+TAG[\s_]+{num}\s*_{{1,2}}', re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(placeholder).replace('_', r'[\s_]*'), re.IGNORECASE)
        res = pattern.sub(lambda _: original, res)
    return res


# Обратная совместимость алиасов функций на уровне модуля
mask_tags = mask_card_tags
unmask_tags = unmask_card_tags


class CardTranslator:
    """
    Статический сервис для подготовки и выполнения переводов полей карточек персонажей.
    """

    mask_tags = staticmethod(mask_card_tags)
    unmask_tags = staticmethod(unmask_card_tags)
    mask_card_tags = staticmethod(mask_card_tags)
    unmask_card_tags = staticmethod(unmask_card_tags)

    @staticmethod
    def build_field_prompt(field_key: str) -> str:
        """
        Генерирует специализированный системный промпт литературного переводчика для конкретного поля:
        'description', 'personality', 'scenario', 'mes_example', 'first_mes'.
        """
        key = (field_key or "").lower().strip()
        if key == "mes_example":
            return PROMPT_MES_EXAMPLE
        elif key in ("first_mes", "greeting", "alternate_greetings"):
            return PROMPT_FIRST_MES
        elif key in ("description", "personality", "scenario"):
            return PROMPT_LITERARY_PROSE
        else:
            return PROMPT_LITERARY_PROSE

    @staticmethod
    def create_translation_task(
        source_text: str,
        system_prompt: str,
        lm_client: Any,
        on_done: Optional[Callable[[str, Any], None]] = None,
        on_fail: Optional[Callable[[str, str], None]] = None,
        max_chunk_tokens: int = 600,
        task_name: str = "Перевод"
    ) -> InferenceTask:
        """
        Создает и конфигурирует InferenceTask для перевода текста:
        - Маскирование системных тегов и макросов
        - Поабзацный чанкинг по \\n\\n через SessionManager.estimate_tokens
        - Адаптивный расчет токенов через SessionManager.calculate_translation_budget
        - Воркер с Quality Gate (повторная попытка при CJK иероглифах)
        - Сборка итогового текста и размаскирование тегов
        """
        def _worker_translation(t: Optional[str] = None) -> dict:
            text_to_process = source_text if t is None else t

            # 1. Маскирование системных тегов
            masked_text, tag_map = mask_card_tags(text_to_process)

            # 2. Поабзацный чанкинг для предотвращения сбоев внимания на длинных текстах
            total_est = SessionManager.estimate_tokens(masked_text)

            chunks: list[str] = []
            if total_est > max_chunk_tokens and "\n\n" in masked_text:
                raw_paragraphs = masked_text.split("\n\n")
                current_chunk = []
                current_tok = 0
                for p in raw_paragraphs:
                    p_tok = SessionManager.estimate_tokens(p)
                    if current_tok + p_tok > max_chunk_tokens and current_chunk:
                        chunks.append("\n\n".join(current_chunk))
                        current_chunk = [p]
                        current_tok = p_tok
                    else:
                        current_chunk.append(p)
                        current_tok += p_tok
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
            else:
                chunks = [masked_text]

            # 3. Воркер последовательного перевода чанков с Quality Gate
            translated_chunks = []
            had_cjk_issue = False

            for idx, chunk in enumerate(chunks):
                if not chunk.strip():
                    translated_chunks.append(chunk)
                    continue

                user_prompt = f"Переведи следующий текст на чистый литературный русский язык:\n\n{chunk}"
                budget = SessionManager.calculate_translation_budget(chunk)

                resp = lm_client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.35,
                    max_tokens=budget,
                    emit_signal=False,
                    is_roleplay=False
                )
                chunk_reply = str(resp or "").strip()

                # CJK Quality Gate: ровно 1 повторная попытка при наличии иероглифов
                if RE_CJK_PATTERN.search(chunk_reply):
                    logger.warning(
                        f"[CardTranslator] Обнаружены иероглифы в чанке #{idx+1}. "
                        f"Запуск корректирующей попытки (max_retries=1)..."
                    )
                    retry_user_prompt = (
                        f"В твоем предыдущем переводе остались китайские/японские иероглифы. "
                        f"Переведи этот фрагмент ПОЛНОСТЬЮ на русский язык. "
                        f"Категорически запрещено выводить иероглифы:\n\n{chunk}"
                    )
                    retry_resp = lm_client.chat_completion(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": retry_user_prompt}
                        ],
                        temperature=0.25,
                        max_tokens=budget,
                        emit_signal=False,
                        is_roleplay=False
                    )
                    retry_reply = str(retry_resp or "").strip()
                    if retry_reply:
                        chunk_reply = retry_reply
                    if RE_CJK_PATTERN.search(chunk_reply):
                        had_cjk_issue = True

                translated_chunks.append(chunk_reply)

            assembled_masked = "\n\n".join(translated_chunks)
            final_text = unmask_card_tags(assembled_masked, tag_map)
            return {"final_text": final_text, "had_cjk_issue": had_cjk_issue}

        abort_fn = getattr(lm_client, "abort", None)
        task = InferenceTask(
            name=task_name,
            task_type="translation",
            func=_worker_translation,
            kwargs={"t": source_text},
            abort_fn=abort_fn,
            metadata={
                "source_text": source_text,
                "system_prompt": system_prompt,
                "on_done": on_done,
                "on_fail": on_fail
            }
        )
        task.on_done = on_done
        task.on_fail = on_fail
        return task
