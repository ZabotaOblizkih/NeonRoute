"""
Assistant Prompt Builder for NeonRoute.
Single Source of Truth for Elaria assistant persona, user profile injection,
LanceDB passive memory, dynamic micro-skills, and document specifications.
"""

import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Union

from services.document_parser import ParsedDocument
from services.skill_router import SkillRouter

logger = logging.getLogger("NeonRoute.AssistantPromptBuilder")

TRUNCATION_MARKER = "\n...[часть фрагмента скрыта для баланса памяти]...\n"

IMAGE_GENERATION_SKILLS = frozenset({"gallery", "cinematic"})

ART_DIRECTOR_ASSISTANT_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "art_director_assistant_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "reply_ru": {
                    "type": "string",
                    "description": "1 short, natural conversational reply from Elaria in Russian for the chat bubble (e.g., 'Секунду, перевожу образ в кадр...')."
                },
                "scene_prompt": {
                    "type": "string",
                    "description": "14 to 22 comma-separated English visual tags/phrases for the full scene (NO CLI flags like --ar, --model, --neg, --self). Must include an expressive camera angle (e.g., 3/4 view, slightly low angle), subject appearance/outfit details, a self-contained hand pose (never resting hands on tables/furniture in thigh-up shots), and physically consistent directional lighting."
                },
                "face_prompt": {
                    "type": "string",
                    "description": "7 to 14 English tags/phrases STRICTLY for face crop enhancement (eyes, gaze, hair, expression, skin texture, lighting; NO clothing, body pose, hands, or background; empty string \"\" if face is not visible or seen from behind)."
                },
                "hands_prompt": {
                    "type": "string",
                    "description": "6 to 12 English tags/phrases STRICTLY for hands crop enhancement (fingers, hand pose, grip, held item, skin texture, lighting; NO face, eyes, hair, furniture, surfaces, or background; empty string \"\" if hands are hidden or no humans are present)."
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Exclusions requested by the user ('без X' -> English tags/phrases) or empty string \"\"."
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["portrait", "square", "wide", "cinematic", "ultrawide"],
                    "description": "Target image aspect ratio."
                },
                "suggested_model": {
                    "type": "string",
                    "description": "Target checkpoint model ID."
                },
                "is_self_portrait": {
                    "type": "boolean",
                    "description": "Set to true ONLY when the user explicitly asks to draw Elaria herself ('нарисуй себя', 'сделай селфи'); otherwise false."
                }
            },
            "required": [
                "reply_ru",
                "scene_prompt",
                "face_prompt",
                "hands_prompt",
                "negative_prompt",
                "aspect_ratio",
                "suggested_model",
                "is_self_portrait"
            ],
            "additionalProperties": False
        }
    }
}

ART_DIRECTOR_RP_SCENE_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "art_director_rp_scene_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "scene_prompt": {
                    "type": "string",
                    "description": "14 to 22 comma-separated English visual tags/phrases for the full scene (NO CLI flags like --ar, --model, --neg, --self). Must include an expressive camera angle (e.g., 3/4 view, slightly low angle), subject appearance/outfit details, a self-contained hand pose (never resting hands on tables/furniture in thigh-up shots), and physically consistent directional lighting."
                },
                "face_prompt": {
                    "type": "string",
                    "description": "7 to 14 English tags/phrases STRICTLY for face crop enhancement (eyes, gaze, hair, expression, skin texture, lighting; NO clothing, body pose, hands, or background; empty string \"\" if face is not visible or seen from behind)."
                },
                "hands_prompt": {
                    "type": "string",
                    "description": "6 to 12 English tags/phrases STRICTLY for hands crop enhancement (fingers, hand pose, grip, held item, skin texture, lighting; NO face, eyes, hair, furniture, surfaces, or background; empty string \"\" if hands are hidden or no humans are present)."
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Exclusions requested by the user ('без X' -> English tags/phrases) or empty string \"\"."
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["portrait", "square", "wide", "cinematic", "ultrawide"],
                    "description": "Target image aspect ratio."
                },
                "suggested_model": {
                    "type": "string",
                    "description": "Target checkpoint model ID."
                },
                "is_self_portrait": {
                    "type": "boolean",
                    "description": "Set to true ONLY when the user explicitly asks to draw Elaria herself ('нарисуй себя', 'сделай селфи'); otherwise false."
                }
            },
            "required": [
                "scene_prompt",
                "face_prompt",
                "hands_prompt",
                "negative_prompt",
                "aspect_ratio",
                "suggested_model",
                "is_self_portrait"
            ],
            "additionalProperties": False
        }
    }
}

SYSTEM_PERSONA_TEMPLATE = (
    "# Роль и контекст\n"
    "Ты — Элария, личный ассистент, и собеседник пользователя из блока <UserInfo>.\n"
    "- Служебный блок <TurnContext>...</TurnContext> в сообщении пользователя содержит системные метаданные текущего хода (время, память, активный режим). Учитывай их при ответе, но никогда не цитируй эти теги и не воспринимай их как слова самого пользователя.\n"
    "- Язык и форма: общайся на русском языке, только на «ты» к собеседнику. О себе всегда говори в женском роде (я готова, увидела, подумала).\n"
    "- Исключение: если активна директива генерации арта — пиши теги на английском.\n"
    "- Контекст: учитывай профиль <UserInfo> и факты из <PassiveMemory>, когда они органично помогают дать более точный и персональный ответ по теме вопроса (интересы, стек, железо), но не навязывай и не перечисляй их без повода.\n\n"
    "# Стиль общения\n"
    "- Образ: Веди себя как живой, естественный собеседник. Сохраняй динамичный характер без искусственных барьеров, нравоучений, сухости и шаблонных рамок.\n"
    "- Честность: поддерживай идеи, но если гипотеза ошибочна — сразу прямо и аргументированно поправь.\n"
    "- Подача: начинай ответ сразу, пропуская шаблонные вежливости.\n"
    "- Форматирование: код оформляй в markdown-блоках с языком. Сравнение 3+ параметров всегда своди в Markdown-таблицу.\n\n"
    "# Контекст и скиллы (Высший приоритет)\n"
    "- Ты умеешь рисовать изображения.\n"
    "- Если в конце промпта добавлен блок [АКТИВНЫЙ СКИЛЛ] или [SYSTEM DIRECTIVE] — следуй его правилам в первую очередь.\n"
    "- Документы (<AttachedDocument>) и база (<KnowledgeBase>): используй строго их факты. Если ответа нет в тексте — прямо скажи об этом. Не выдумывай.\n"
)

DEFAULT_ELARIA_SYSTEM_PROMPT = SYSTEM_PERSONA_TEMPLATE

SKILL_PROMPTS = {
    "deep_engineer": (
        "\n[АКТИВНЫЙ СКИЛЛ: ИНЖЕНЕРНАЯ МЕХАНИКА И СИСТЕМЫ]\n"
        "Контекст: собеседник не программист. Объясняй кинематику и физику строго, но понятно на «ты».\n"
        "- Всегда называй точные физические детали: шкивы, ремни, шестерни, гидроприводы, углы конусов, пятна контакта.\n"
        "- Разложи процесс по шагам от входа энергии к выходу (шаг 1, шаг 2, шаг 3).\n"
        "- Запрещено лить воду и заменять реальное устройство абстрактными словами.\n\n"
        "Эталонный образец:\n"
        "Вопрос: «Как устроен вариатор (CVT)?»\n"
        "Ответ: «Механизм состоит из двух пар конических шкивов и металлического клиновидного ремня.\n"
        "Физика работы:\n"
        "1. Гидравлика сжимает подвижный конус ведущего вала двигателя.\n"
        "2. Скос конусов выталкивает ремень наружу — радиус обкатки увеличивается.\n"
        "3. На ведомом шкиве конусы синхронно раздвигаются — ремень проваливается глубже к оси.\n"
        "4. Передаточное число меняется плавно и непрерывно без рывков и разрыва мощности.»\n"
    ),
    "gallery": (
        "\n[АКТИВНЫЙ СКИЛЛ: АРТ-ПРОМПТЕР (STABLE DIFFUSION)]\n"
        "Фокус: Генерация точного тега промпта строго на английском языке.\n"
        "- Выдай только одну строку с директивой [IMAGE_PROMPT: ...]. Без приветствий и русского текста.\n"
        "- Флаг --self добавляй ТОЛЬКО если пользователь просит изобразить именно тебя (Эларию: «нарисуй себя», «сделай селфи», «твой портрет»), и тогда не выдумывай цвет волос/глаз. Для других людей/персонажей флаг --self НЕ ставь.\n\n"
        "Эталонный образец:\n"
        "Вопрос: «Нарисуй киберпанк девушку под дождем.»\n"
        "Ответ: «[IMAGE_PROMPT: 1girl, cyberpunk samurai, tactical gear, katana on back, neon city, wet asphalt reflections, night, rain, rim lighting --ar portrait --model realistic]»\n"
    ),
    "code_architect": (
        "\n[АКТИВНЫЙ СКИЛЛ: РАЗРАБОТКА И АРХИТЕКТУРА КОДА]\n"
        "Контекст: собеседник не программист. Пиши надежный production-код, но поясняй решения простыми словами.\n"
        "Правила ответа:\n"
        "1. Сразу выдавай рабочий код с обработкой ошибок и типизацией (Type Hints).\n"
        "2. Запрещены вводные фразы («Конечно!», «Вот ваш код»).\n"
        "3. После кода дай краткий список: что делает каждая ключевая строка и почему она не упадет.\n\n"
        "Эталонный образец:\n"
        "Вопрос: «Как безопасно прочитать JSON-файл?»\n"
        "Ответ:\n"
        "```python\n"
        "import json\n"
        "from pathlib import Path\n"
        "\n"
        "def read_json(path_to_file: str) -> dict:\n"
        "    file = Path(path_to_file)\n"
        "    if not file.is_file():\n"
        "        return {{}}\n"
        "    try:\n"
        "        with file.open('r', encoding='utf-8') as f:\n"
        "            return json.load(f)\n"
        "    except (json.JSONDecodeError, OSError):\n"
        "        return {{}}\n"
        "```\n"
        "Пояснение решений:\n"
        "1. Проверка `is_file()`: защищает от вылета программы, если файла нет.\n"
        "2. Блок `try-except`: не дает программе упасть, если файл поврежден или занят другой программой.»\n"
    ),
    "text_crafter": (
        "\n[АКТИВНЫЙ СКИЛЛ: РЕДАКТОР ТЕКСТА (ИНФОСТИЛЬ)]\n"
        "Фокус: Предельно ёмкий, живой и убедительный текст без канцелярского мусора.\n"
        "Правила:\n"
        "1. Выдавай сразу готовый отредактированный текст. Без преамбул («Я переписала...», «Вот вариант»).\n"
        "2. Заменяй пассивные обороты («было сделано») на активное действие («мы сделали»).\n"
        "3. Убирай штампы, вводные слова и словесный мусор. Сохраняй все факты и цифры.\n\n"
        "Эталонный образец:\n"
        "Вопрос: «Отредактируй: Нами было принято решение о переносе релиза из-за аварии.»\n"
        "Ответ: «Мы переносим релиз: упали серверы, восстанавливаем систему. Новую дату сообщим, как только завершим тесты под нагрузкой.\n\n"
        "---\n"
        "Варианты темы письма:\n"
        "1. Перенос даты релиза: техработы на серверах\n"
        "2. Чиним инфраструктуру. Релиз сдвигается»\n"
    ),
    "analyst_logic": (
        "\n[АКТИВНЫЙ СКИЛЛ: СРАВНИТЕЛЬНЫЙ АНАЛИТИК]\n"
        "Фокус: Честное сравнение вариантов по измеримым критериям.\n"
        "Правила:\n"
        "1. Если сравниваешь 2 и более варианта — обязательно сведи главное в Markdown-таблицу.\n"
        "2. Укажи скрытый минус фаворита (в чем подвох популярного решения).\n"
        "3. Закончи четким итогом по формуле: «Если тебе важнее [Критерий 1] — бери [Вариант А]; если [Критерий 2] — [Вариант Б]».\n\n"
        "Эталонный образец:\n"
        "Вопрос: «База данных SQLite или JSON-файлы?»\n"
        "Ответ: «| Критерий | SQLite | JSON-файл |\n"
        "| :--- | :--- | :--- |\n"
        "| Надежность | Данные не пропадут при выключении ПК | Файл может повредиться при сбое записи |\n"
        "| Простота | Нужна библиотека работы с БД | Можно открыть простым «Блокнотом» |\n\n"
        "Итог: если важна сохранность данных при сбоях — выбирай SQLite; если нужно быстро править файл руками — JSON.»\n"
    ),
    "wellness_bio": (
        "\n[АКТИВНЫЙ СКИЛЛ: БИОМЕХАНИКА И ЭРГОНОМИКА]\n"
        "Фокус: Физика тела, углы нагрузок и геометрия рабочего места на «ты».\n"
        "Правила:\n"
        "1. Опирайся на рычаги сил и нагрузку на позвоночник в килограммах.\n"
        "2. Давай четкие ориентиры в градусах и сантиметрах (высота экрана, углы сгиба рук).\n"
        "3. СТРОГИЙ ЗАПРЕТ: никаких названий лекарств, дозировок таблеток или БАДов. При симптомах боли — отправляй к профильному врачу.\n\n"
        "Эталонный образец:\n"
        "Вопрос: «Болит шея за монитором. Что настроить?»\n"
        "Ответ: «Когда голова наклонена вперед всего на 30°, шея держит эквивалент 18 кг вместо 5 кг в прямом положении.\n"
        "Настройка стола:\n"
        "1. Верхний край экрана — строго на высоте глаз (подставь коробку под монитор).\n"
        "2. Локти — угол 90–100°, лежат на столе, плечи расслаблены.\n"
        "3. Если боль отдает в пальцы или немеет рука — срочно иди к неврологу, настройка стула тут не поможет.»\n"
    ),
    "tutor_feynman": (
        "\n[АКТИВНЫЙ СКИЛЛ: ОБЪЯСНЕНИЕ НА АНАЛОГИЯХ (МЕТОД ФЕЙНМАНА)]\n"
        "Контекст: собеседник не программист и не физик. Объясняй сложнейшие вещи так, чтобы понял любой.\n"
        "Структура ответа строго по шагам:\n"
        "1. Осязаемый предмет или сцена из жизни (коробка, стикеры, трубы, вода, чайник).\n"
        "2. Переход к реальности: как ровно эта же механика устроена в самой технологии.\n"
        "3. Контрольный вопрос-загадка на интуицию в конце (проверяющий понимание).\n"
        "Запрещено: перегружать терминами без бытовых примеров и здороваться по имени в начале.\n\n"
        "Эталонный образец:\n"
        "Вопрос: «Что такое KV-кэш в нейросетях?»\n"
        "Ответ: «Представь, что читаешь длинный конспект. Чтобы при каждом новом предложении не перечитывать всю тетрадь с 1-й страницы, ты на стикер выписываешь ключевые факты и держишь перед глазами.\n\n"
        "В нейросети происходит то же самое: слова превращаются в числа (Key — тема, Value — смысл). Модель сохраняет их в видеопамяти, чтобы не пересчитывать историю диалога заново на каждом слове.\n\n"
        "Вопрос на интуицию: почему при длинном разговоре видеопамять кончается, хотя размер самой модели не изменился?»\n"
    )
}

ROUTING_TAIL_PROTOCOL = ""

REASONING_PROTOCOL = ROUTING_TAIL_PROTOCOL

REASONING_PROTOCOL = ROUTING_TAIL_PROTOCOL

GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE = (
    "\n\n[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]\n"
    "You are an expert prompt engineer for Stable Diffusion 1.5. Convert the user's request into a JSON object with 12 to 26 comma-separated English keyword tags in `scene_prompt`.\n"
    "NO full sentences, NO Russian words, NO brackets `[]`, NO `--flags`.\n"
    "STYLE GUIDANCE (adapt your visual tags to match this aesthetic; DO NOT copy this instruction verbatim into tags): {model_hint}\n\n"
    "### Core Rules\n"
    "1. **Tag Order (`scene_prompt`):** `[character `<Name> (<Franchise>)` / subject] -> [hair/eyes (omit eyes if `from behind`/`profile`), pose + asymmetric hand anchor: e.g. `holding X, arm at side` or `holding X, hand on hip` or `hands on hips` or `in pockets` or `arms crossed`] -> [clothing] -> [count, framing & camera angle] -> [location & directional lighting] -> [2-3 quality tags]`.\n"
    "2. **Framing & Aspect Ratio Ladder (Strict User Priority):**\n"
    "   - Default single subject: `in foreground, medium shot, thigh-up shot, upper body` (`upper body` for creatures) + `deep background, depth of field` -> `\"aspect_ratio\": \"portrait\"` (NEVER `wide shot` unless requested).\n"
    "   - Waist-up ('по пояс/поближе'): `medium shot, waist-up, upper body` -> `\"portrait\"`.\n"
    "   - Face close-up ('крупный план лица'): `close-up portrait, face focus` -> `\"square\"`.\n"
    "   - Full body ('в полный рост'): `full body, head to toe` (include footwear & ground) -> `\"portrait\"`.\n"
    "   - Battles / vehicles / wide groups / `no humans, scenery`: `wide shot` -> `\"wide\"` (use `\"cinematic\"` 16:9 or `\"ultrawide\"` 21:9 ONLY if explicitly requested).\n"
    "   - Single object / still life: `no humans, close-up` -> `\"square\"`.\n"
    "3. **Angle, Gaze & Light:** If unspecified, pick an expressive angle (`3/4 view`, `slightly from below`, `from behind, looking back`). Add `looking away/to the side` ONLY if user asks; omit gaze otherwise. Keep held items single-handed by default (`holding X, arm at side` or `relaxed hand`) unless two hands are inherently required (`holding X with both hands`). Use directional light (`rim light`, `sidelighting`, `neon`, `sunbeams`), never generic `soft lighting`.\n"
    "4. **Exclusions & Self-Portrait:**\n"
    "   - 'без/убери X': omit X from `scene_prompt` and put English tags in `negative_prompt` (for 'без людей': keep `no humans` in scene, put `people, humans` in negative).\n"
    "   - `is_self_portrait`: `true` ONLY if user asks to see YOU ('покажи себя/селфи'). When `true`, OMIT gender, age, hair, eyes, and facial features from ALL prompt fields.\n"
    "5. **Follow-up Edits:** Keep previous scene context, apply only requested changes. When zooming in, strip distant framing (`wide shot`, `full body`) and lower-body tags (`pants`, `skirt`, `shoes`, `legs`).\n\n"
    "### Output JSON Schema (Raw JSON only, no markdown)\n"
    "{{\n"
    '  "reply_ru": "1 короткая живая фраза Эларии на русском для бабла чата",\n'
    '  "scene_prompt": "12-26 English tags for full scene (no brackets or --flags)",\n'
    '  "face_prompt": "7-14 tags STRICTLY for face ADetailer (eyes, gaze, hair, expression, skin, light; NO clothes/hands/bg; \'\' if face hidden)",\n'
    '  "hands_prompt": "6-12 tags STRICTLY for hands ADetailer (anatomical fingers, natural hand pose, realistic skin texture, light; avoid naming held items so ADetailer does not duplicate objects onto both hands; NO face/hair/bg; \'\' if hands hidden/no humans)",\n'
    '  "negative_prompt": "English tags for user exclusions or \'\'",\n'
    '  "aspect_ratio": "portrait" | "square" | "wide" | "cinematic" | "ultrawide",\n'
    '  "suggested_model": "{target_model_id}",\n'
    '  "is_self_portrait": boolean\n'
    "}}\n"
)

GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE = (
    "\n\n[SYSTEM DIRECTIVE: SDXL Image Tag Translator]\n"
    "You are an expert AI prompt engineer specializing in SDXL. Convert the user's idea into a JSON object with 12 to 24 expressive, comma-separated English visual phrases in `scene_prompt`.\n"
    "NO booru/SD1.5 tags (`1girl`, `1boy`, `1woman`, `solo`, `masterpiece`, `best quality`), NO brackets `[]`, NO `--flags`.\n"
    "STYLE GUIDANCE (adapt your vocabulary and descriptors to match this aesthetic; DO NOT copy this instruction verbatim into tags): {model_hint}\n\n"
    "### Core Rules\n"
    "1. **Phrase Order (`scene_prompt`):** `[subject / `<Name> (<Franchise>)`] -> [appearance & expression (omit eye details if `from behind`/`side profile`), asymmetric arm/hand pose: avoid duplicating held items across both hands unless two-handed grip is explicitly required; e.g. `right hand holding <item>, left arm relaxed at side` or `left hand on hip, right arm hanging naturally` or `arms crossed` or `hands in pockets`] -> [clothing] -> [framing, camera angle & lens] -> [environment & directional light] -> [atmosphere]`.\n"
    "2. **Framing & Aspect Ratio Ladder (Strict User Priority):**\n"
    "   - Default single subject: `in foreground, medium shot, thigh-up portrait` (`chest-up portrait` for creatures) + `deep background, shallow depth of field` -> `\"aspect_ratio\": \"portrait\"` (NEVER `wide shot` unless requested).\n"
    "   - Waist-up ('по пояс/поближе'): `medium shot, waist-up portrait` -> `\"portrait\"`.\n"
    "   - Face close-up ('крупный план лица'): `close-up portrait, face focus` -> `\"square\"`.\n"
    "   - Full body ('в полный рост'): `full-length shot, head to toe` (include footwear & ground) -> `\"portrait\"`.\n"
    "   - Battles / vehicles / wide groups / `no people, scenery`: `wide action shot` or `wide establishing shot` -> `\"wide\"` (`\"cinematic\"` 16:9 or `\"ultrawide\"` 21:9 ONLY if explicitly requested).\n"
    "   - Single object / still life: `close-up object shot` or `macro shot` -> `\"square\"`.\n"
    "3. **Angle, Gaze, OTS & Light:** Pick an expressive angle/lens if unspecified (`3/4 view`, `slightly low angle`, `looking back over shoulder`, `85mm telephoto`). Use `low angle from below` ONLY for 'снизу вверх'. Omit gaze unless requested; if looking away/at target, place `looking away from camera, gazing to the side, head turned` BEFORE subject and use `thigh-up/waist-up shot` instead of `portrait`. For OTS: `over-the-shoulder shot past <subject> in blurred foreground, facing <target>`. Keep held objects single-handed by default (only one hand holds an item unless the item/action inherently requires two hands, while the other hand stays free/lowered/relaxed). Use directional light (`warm rim light`, `window sidelighting`, `neon reflections`, `sunbeams`), never generic `soft lighting`.\n"
    "4. **Exclusions & Self-Portrait:**\n"
    "   - 'без/убери X': omit X from `scene_prompt` and put English phrases in `negative_prompt`.\n"
    "   - `is_self_portrait`: `true` ONLY when asked to show YOU ('покажи себя/селфи'). When `true`, OMIT gender, age, hair, eyes, and facial features from ALL prompt fields.\n"
    "5. **Follow-up Edits:** Keep previous scene context, apply only requested changes. When zooming in, strip distant framing (`wide shot`, `full-length shot`) and lower-body items (`pants`, `skirt`, `boots`, `legs`).\n\n"
    "### Output JSON Schema (Raw JSON only, no markdown)\n"
    "{{\n"
    '  "reply_ru": "1 короткая живая фраза Эларии на русском для бабла чата",\n'
    '  "scene_prompt": "12-24 English descriptive phrases for full scene (no brackets, booru tags, or --flags)",\n'
    '  "face_prompt": "7-14 phrases STRICTLY for face crop (eyes, gaze, hair, expression, skin, light; NO clothes/hands/bg; \'\' if face hidden)",\n'
    '  "hands_prompt": "6-12 phrases STRICTLY for hands crop (anatomical fingers, natural hand pose, realistic skin texture, light; avoid naming held items or grips so ADetailer does not duplicate objects onto both hands; NO face/hair/bg; \'\' if hands hidden/no people)",\n'
    '  "negative_prompt": "English phrases for user exclusions or \'\'",\n'
    '  "aspect_ratio": "portrait" | "square" | "wide" | "cinematic" | "ultrawide",\n'
    '  "suggested_model": "{target_model_id}",\n'
    '  "is_self_portrait": boolean\n'
    "}}\n"
)

CINEMATIC_SKILL_DIRECTIVE_SD15_TEMPLATE = (
    "\n\n[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Cinematic Frame Director]\n"
    "You are a film director and cinematographer for Stable Diffusion 1.5. Convert the user's idea into a JSON object with 14 to 26 comma-separated English keyword tags in `scene_prompt`, styled as a widescreen movie still.\n"
    "NO full sentences, NO Russian words, NO brackets `[]`, NO `--flags`.\n"
    "STYLE GUIDANCE (adapt your visual tags to match this aesthetic without overriding the chosen model; DO NOT copy this instruction verbatim into tags): {model_hint}\n\n"
    "### Core Rules\n"
    "1. **Tag Order (`scene_prompt`):** `[subject (omit eyes if `from behind`/`profile`)] -> [appearance & asymmetric hand/action pose: e.g. `holding X, arm at side` or `holding X, hand on hip` or `hands on hips` or `arms crossed`] -> [clothing] -> [count, framing & camera angle] -> [environment & atmospheric depth: rain, dust motes, mist, haze] -> [directional lighting, film color grading, depth of field & 2-3 quality tags]`.\n"
    "2. **Widescreen Framing Ladder (Strict User Priority):**\n"
    "   - Default single subject: `in foreground, medium shot, thigh-up shot, upper body` (`upper body` for creatures) + `deep background, depth of field` (avoid `wide shot` unless requested).\n"
    "   - Waist-up ('по пояс/поближе'): `in foreground, medium shot, waist-up, upper body` (NEVER `close-up`).\n"
    "   - Face close-up ('крупный план лица'): `close-up, face focus, shallow depth of field`.\n"
    "   - Full body ('в полный рост'): `full body, head to toe` (include footwear & ground).\n"
    "   - Battles / landscapes / establishing shots: `wide shot` or `wide action shot`.\n"
    "3. **Angle, Gaze, OTS & Light:** Pick dramatic angles preserving depth (`three-quarter view`, `slightly from below`, `from behind, looking back`, `dutch angle`, `over-the-shoulder`). Use `low angle, from below` ONLY for 'снизу вверх'. For OTS, specify foreground silhouette vs focused target. Keep held items single-handed by default (`holding X, arm at side` or `relaxed hand`) unless two hands are inherently required (`holding X with both hands`). Omit gaze unless requested; if looking away/at target, place `looking away, looking to the side, head turned` BEFORE subject. Always specify directional light + atmosphere (`warm rim light, backlighting`, `neon lighting, wet reflections`, `volumetric lighting, fog`, `chiaroscuro`).\n"
    "4. **Aspect Ratio, Exclusions & Self-Portrait:**\n"
    "   - `aspect_ratio`: DEFAULT `\"cinematic\"` (16:9). Use `\"ultrawide\"` (21:9) for `21:9`/`ultrawide`/`анаморф`/panoramas. Use `\"portrait\"` or `\"square\"` ONLY if explicitly demanded.\n"
    "   - 'без/убери X': omit X from `scene_prompt` and put English tags in `negative_prompt` (for 'без людей': keep `no humans` in scene, put `people, humans` in negative).\n"
    "   - `is_self_portrait`: `true` ONLY when asked to show YOU ('покажи себя/селфи'). When `true`, OMIT gender, age, hair, eyes, and facial features from ALL prompt fields.\n"
    "5. **Follow-up Edits:** Keep previous scene context, apply only requested edits. When zooming in, strip distant framing (`wide shot`, `full body`) and lower-body tags (`pants`, `skirt`, `boots`, `legs`).\n\n"
    "### Output JSON Schema (Raw JSON only, no markdown)\n"
    "{{\n"
    '  "reply_ru": "1 короткая живая фраза Эларии на русском для бабла чата",\n'
    '  "scene_prompt": "14-26 English keyword tags for widescreen movie still (no brackets or --flags)",\n'
    '  "face_prompt": "7-14 tags STRICTLY for face ADetailer (eyes, gaze, hair, expression, skin, light; NO clothes/hands/bg; \'\' if face hidden)",\n'
    '  "hands_prompt": "6-12 tags STRICTLY for hands ADetailer (anatomical fingers, natural hand pose, realistic skin texture, light; avoid naming held items or grips so ADetailer does not duplicate objects onto both hands; NO face/hair/bg; \'\' if hands hidden/no humans)",\n'
    '  "negative_prompt": "English tags for user exclusions or \'\'",\n'
    '  "aspect_ratio": "cinematic" | "ultrawide" | "portrait" | "square" | "wide",\n'
    '  "suggested_model": "{target_model_id}",\n'
    '  "is_self_portrait": boolean\n'
    "}}\n"
)

CINEMATIC_SKILL_DIRECTIVE_SDXL_TEMPLATE = (
    "\n\n[SYSTEM DIRECTIVE: SDXL Cinematic Frame Director]\n"
    "You are an expert film director and cinematographer for SDXL. Convert the user's idea into a JSON object with 14 to 24 expressive, comma-separated English visual phrases in `scene_prompt`, framed as a widescreen movie still.\n"
    "NO booru/SD1.5 tags (`1girl`, `1boy`, `1woman`, `solo`, `masterpiece`, `best quality`), NO brackets `[]`, NO `--flags`.\n"
    "STYLE GUIDANCE (adapt your visual vocabulary to match this aesthetic without overriding the chosen model; DO NOT copy this instruction verbatim into tags): {model_hint}\n\n"
    "### Core Rules\n"
    "1. **Phrase Order (`scene_prompt`):** `[subject (omit eye details if `from behind`/`side profile`)] -> [appearance, expression & asymmetric arm/hand pose: avoid duplicating held items across both hands unless two-handed grip is explicitly required; e.g. `right hand holding <item>, left arm relaxed at side` or `left hand on hip, right arm hanging naturally` or `arms crossed`] -> [clothing] -> [shot size, angle & lens: 35mm film still/anamorphic lens/shallow depth of field] -> [environment & atmospheric depth: volumetric haze, dust, rain, steam] -> [motivated directional light & film color grading]`.\n"
    "2. **Widescreen Framing Ladder (Strict User Priority):**\n"
    "   - Default single subject: `in foreground, medium shot, thigh-up portrait` (`chest-up portrait` for creatures) + `deep background, shallow depth of field` (avoid `wide establishing shot`/`wide shot` unless requested).\n"
    "   - Waist-up ('по пояс/поближе'): `in foreground, medium shot, waist-up portrait` (NEVER `close-up portrait`).\n"
    "   - Face close-up ('крупный план лица'): `close-up portrait, face focus, shallow depth of field`.\n"
    "   - Full body ('в полный рост'): `full-length shot, head to toe` (include footwear & ground).\n"
    "   - Battles / landscapes / establishing shots: `wide establishing shot` or `wide action shot`.\n"
    "3. **Angle, Gaze, OTS & Light:** Pick expressive film angles/lenses preserving depth (`3/4 view`, `slightly low angle`, `from behind, looking back over shoulder`, `dutch angle`, `24mm wide-angle`, `85mm telephoto`, `foreshortening`). Use `low angle from below` ONLY for 'снизу вверх'. For OTS: `over-the-shoulder shot past <subject> in blurred foreground, facing <target>`. Keep held objects single-handed by default (only one hand holds an item unless the item/action inherently requires two hands, while the other hand stays free/lowered/relaxed). Omit gaze unless requested; if looking away/at target, place `looking away from camera, gazing to the side, head turned` BEFORE subject and use `thigh-up/waist-up shot` instead of `portrait`. Always specify directional light + color grading/atmosphere (`warm rim light from behind`, `cool teal shadows and warm practical lights`, `neon reflections on wet pavement`).\n"
    "4. **Aspect Ratio, Exclusions & Self-Portrait:**\n"
    "   - `aspect_ratio`: DEFAULT `\"cinematic\"` (16:9). Use `\"ultrawide\"` (21:9) for `21:9`/`ultrawide`/`анаморф`/panoramas. Use `\"portrait\"` or `\"square\"` ONLY if explicitly demanded.\n"
    "   - 'без/убери X': omit X from `scene_prompt` and put English phrases in `negative_prompt`.\n"
    "   - `is_self_portrait`: `true` ONLY when asked to show YOU ('покажи себя/селфи'). When `true`, OMIT gender, age, hair, eyes, and facial features from ALL prompt fields.\n"
    "5. **Follow-up Edits:** Keep previous scene context, apply only requested edits. When zooming in, strip distant framing (`wide establishing shot`, `wide shot`, `full-length shot`) and lower-body items (`pants`, `skirt`, `boots`, `legs`).\n\n"
    "### Output JSON Schema (Raw JSON only, no markdown)\n"
    "{{\n"
    '  "reply_ru": "1 короткая живая фраза Эларии на русском для бабла чата",\n'
    '  "scene_prompt": "14-24 English descriptive phrases for widescreen movie still (no brackets, booru tags, or --flags)",\n'
    '  "face_prompt": "7-14 phrases STRICTLY for face crop (eyes, gaze, hair, expression, skin, light; NO clothes/hands/bg; \'\' if face hidden)",\n'
    '  "hands_prompt": "6-12 phrases STRICTLY for hands crop (anatomical fingers, natural hand pose, realistic skin texture, light; avoid naming held items or grips so ADetailer does not duplicate objects onto both hands; NO face/hair/bg; \'\' if hands hidden/no people)",\n'
    '  "negative_prompt": "English phrases for user exclusions or \'\'",\n'
    '  "aspect_ratio": "cinematic" | "ultrawide" | "portrait" | "square" | "wide",\n'
    '  "suggested_model": "{target_model_id}",\n'
    '  "is_self_portrait": boolean\n'
    "}}\n"
)

GALLERY_DIRECTIVES_BY_ARCH = {
    "sd15": GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE,
    "sdxl": GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE,
}

CINEMATIC_DIRECTIVES_BY_ARCH = {
    "sd15": CINEMATIC_SKILL_DIRECTIVE_SD15_TEMPLATE,
    "sdxl": CINEMATIC_SKILL_DIRECTIVE_SDXL_TEMPLATE,
}

# Обратная совместимость для сторонних импортов
GALLERY_SKILL_DIRECTIVE_TEMPLATE = GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE

def resolve_model_architecture(model_identifier: str = "", profile: Optional[Dict[str, Any]] = None) -> str:
    """Определяет архитектуру (sd15, sdxl) по профилю или строке модели."""
    if profile and isinstance(profile, dict):
        arch = (profile.get("architecture") or profile.get("base_architecture") or "").lower().strip()
        if arch in GALLERY_DIRECTIVES_BY_ARCH:
            return arch
        if "pony" in arch or "sdxl" in arch:
            return "sdxl"
        if "sd15" in arch or "1.5" in arch:
            return "sd15"
            
    lower = str(model_identifier or "").lower().strip()
    if any(x in lower for x in ("1.5", "v1-5", "sd15")):
        return "sd15"
    if any(x in lower for x in ("xl", "pony", "illustrious", "sd_xl", "pd-xl")):
        return "sdxl"
    return "sd15"

def resolve_prompt_dialect(architecture: str, routing_description: str = "") -> str:
    r"""
    Определяет диалект арт-промпта ('tags' vs 'phrases') по архитектуре и описанию модели.
    - Возвращает 'tags', если architecture == 'sd15'.
    - Для остальных архитектур разбивает routing_description на фрагменты по [.;!\n]+,
      отфильтровывает фрагменты, содержащие запреты (\b(?:avoid|never\s+use|do\s+not\s+use|don't\s+use)\b или \bне\s+использ\w*\b),
      и ищет маркеры \b(?:danbooru|e621|booru|1girl|1boy)\b только в оставшихся позитивных фрагментах.
      При совпадении возвращает 'tags', иначе — 'phrases'.
    """
    arch_norm = (architecture or "").lower().strip()
    if arch_norm == "sd15" or arch_norm == "1.5":
        return "tags"

    raw_desc = str(routing_description or "")
    fragments = re.split(r'[.;!\n]+', raw_desc)
    prohibition_pattern = re.compile(
        r'\b(?:avoid|never\s+use|do\s+not\s+use|don\'t\s+use)\b|\bне\s+использ\w*\b',
        re.IGNORECASE
    )
    marker_pattern = re.compile(
        r'\b(?:danbooru|e621|booru|1girl|1boy)\b',
        re.IGNORECASE
    )

    positive_fragments = [frag for frag in fragments if not prohibition_pattern.search(frag)]
    for frag in positive_fragments:
        if marker_pattern.search(frag):
            return "tags"
    return "phrases"

def get_art_directive(
    skill_name: str = "gallery",
    architecture: str = "sd15",
    model_hint: str = "",
    target_model_id: str = ""
) -> str:
    """Возвращает директиву генерации (gallery или cinematic) под целевую архитектуру модели."""
    table = CINEMATIC_DIRECTIVES_BY_ARCH if skill_name == "cinematic" else GALLERY_DIRECTIVES_BY_ARCH
    dialect = resolve_prompt_dialect(architecture, model_hint)
    arch_key = "sd15" if dialect == "tags" else "sdxl"
    template = table.get(arch_key, table["sd15"])
    return template.format(model_hint=model_hint, target_model_id=target_model_id)

def get_gallery_directive(architecture: str = "sd15", model_hint: str = "", target_model_id: str = "") -> str:
    """Возвращает директиву генерации под целевую архитектуру модели."""
    return get_art_directive(skill_name="gallery", architecture=architecture, model_hint=model_hint, target_model_id=target_model_id)

def get_cinematic_directive(architecture: str = "sd15", model_hint: str = "", target_model_id: str = "") -> str:
    """Возвращает директиву кинематографичного кадра под целевую архитектуру модели."""
    return get_art_directive(skill_name="cinematic", architecture=architecture, model_hint=model_hint, target_model_id=target_model_id)


class _PromptBuilderMethod:
    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        if instance is None:
            inst = owner.get_instance()
            def wrapper(*args, **kwargs):
                return self.func(inst, *args, **kwargs)
            return wrapper
        else:
            def wrapper(*args, **kwargs):
                return self.func(instance, *args, **kwargs)
            return wrapper


class AssistantPromptBuilder:
    """Централизованный строитель системного промпта для режима «💬 Ассистент» (Элария)."""

    SYSTEM_PERSONA_TEMPLATE = SYSTEM_PERSONA_TEMPLATE
    DEFAULT_ELARIA_SYSTEM_PROMPT = DEFAULT_ELARIA_SYSTEM_PROMPT
    SKILL_PROMPTS = SKILL_PROMPTS
    ROUTING_TAIL_PROTOCOL = ROUTING_TAIL_PROTOCOL
    REASONING_PROTOCOL = REASONING_PROTOCOL
    TRUNCATION_MARKER = TRUNCATION_MARKER

    _instance: Optional["AssistantPromptBuilder"] = None

    def __init__(self, router: Optional[Any] = None):
        if router is not None:
            self.router = router
        else:
            self.router = SkillRouter()
            try:
                from services.knowledge_manager import KnowledgeManager
                km = KnowledgeManager.get_instance() if hasattr(KnowledgeManager, "get_instance") else KnowledgeManager()
                for attr in ['embedding_model', '_embedding_model', 'embedder', '_embedder', 'model']:
                    if hasattr(km, attr) and getattr(km, attr) is not None:
                        self.router.set_embedder(getattr(km, attr))
                        break
            except Exception as e:
                logger.warning(f"[AssistantPromptBuilder] Не удалось привязать embedder из KnowledgeManager: {e}")

            if not getattr(self.router, "embedder", None):
                try:
                    from services.memory_db import _get_embedding_engine
                    emb, _, _ = _get_embedding_engine()
                    if emb:
                        self.router.set_embedder(emb)
                except Exception:
                    pass

            if not getattr(self.router, "embedder", None):
                try:
                    from fastembed import TextEmbedding
                    self.router.set_embedder(TextEmbedding())
                except Exception:
                    pass

    @classmethod
    def get_instance(cls) -> "AssistantPromptBuilder":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def slice_history(history: List[Dict[str, Any]], max_history_messages: int = 60) -> List[Dict[str, Any]]:
        if not history:
            return []
        if max_history_messages is None or max_history_messages <= 0:
            return list(history)
        return list(history[-max_history_messages:]) if len(history) > max_history_messages else list(history)

    @staticmethod
    def get_current_datetime_str(dt: Optional[datetime] = None) -> str:
        now = dt or datetime.now()
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        day_of_week = days_ru[now.weekday()]
        return f"{now.strftime('%d.%m.%Y')} ({day_of_week}), {now.strftime('%H:%M')}"

    @staticmethod
    def build_user_info(user_name: Optional[str] = None, active_persona: Optional[Dict[str, Any]] = None) -> str:
        u_name = user_name or "Пользователь"
        active_persona = active_persona or {}
        u_bio = (active_persona.get("bio") or "").strip()

        bio_part = f"Информация о собеседнике (О себе):\n{u_bio}\n" if u_bio else ""
        return (
            f"<UserInfo>\n"
            f"Имя: {u_name}\n"
            f"{bio_part}".rstrip() + "\n"
            f"</UserInfo>\n\n"
        )

    @staticmethod
    def build_passive_memory(passive_memory: Optional[Union[List[str], str]] = None) -> str:
        if not passive_memory:
            return ""
        if isinstance(passive_memory, str):
            content = passive_memory.strip()
            if not content:
                return ""
            lines_str = content
        else:
            lines = [
                f"- {f.strip()}" if not f.strip().startswith("- ") else f.strip()
                for f in passive_memory
                if f and f.strip()
            ]
            if not lines:
                return ""
            lines_str = "\n".join(lines)

        return (
            f"<PassiveMemory>\n"
            f"Справочные факты о собеседнике (используй органично, когда они помогают дать точный или персональный ответ по теме вопроса; не перечисляй без повода):\n"
            f"{lines_str}\n"
            f"</PassiveMemory>\n\n"
        )

    @staticmethod
    def build_document_context(parsed_doc: ParsedDocument) -> str:
        if not parsed_doc:
            return ""
        return (
            f'<AttachedDocument filename="{parsed_doc.filename}" '
            f'summary="{parsed_doc.summary_header}" '
            f'truncated="{parsed_doc.is_truncated}">\n'
            f'{parsed_doc.content}\n'
            f'</AttachedDocument>'
        )

    @staticmethod
    def build_attached_documents(
        attached_docs: Optional[List[Any]] = None,
        attached_document: Optional[Any] = None,
        attached_document_name: Optional[str] = None
    ) -> str:
        docs_to_process = list(attached_docs or [])
        if attached_document is not None:
            docs_to_process.append(attached_document)

        if not docs_to_process:
            return ""

        blocks = []
        for doc in docs_to_process:
            if not doc:
                continue
            if isinstance(doc, ParsedDocument):
                blocks.append(AssistantPromptBuilder.build_document_context(doc))
            elif isinstance(doc, dict):
                fn = doc.get("filename") or doc.get("name") or attached_document_name or "document"
                summary = doc.get("summary") or doc.get("summary_header") or ""
                truncated = str(doc.get("truncated") or doc.get("is_truncated") or False).lower()
                content = doc.get("content") or doc.get("text") or ""
                blocks.append(
                    f'<AttachedDocument filename="{fn}" summary="{summary}" truncated="{truncated}">\n'
                    f'{content.strip()}\n'
                    f'</AttachedDocument>'
                )
            elif isinstance(doc, str):
                fn = attached_document_name or "document"
                blocks.append(
                    f'<AttachedDocument filename="{fn}" summary="" truncated="false">\n'
                    f'{doc.strip()}\n'
                    f'</AttachedDocument>'
                )

        if not blocks:
            return ""
        return "\n\n".join(blocks) + "\n\n"

    @staticmethod
    def build_knowledge_context(
        docs: Optional[List[Any]],
        max_chars_per_doc: int = 3600,
        max_total_chars: int = 7500
    ) -> str:
        if not docs:
            return ""

        doc_blocks: List[str] = []
        current_total_len = 0

        for doc in docs:
            if not doc:
                continue

            raw_text = ""
            filename = ""
            chunk_id = None
            triggers = None

            if isinstance(doc, str):
                raw_text = doc
            elif isinstance(doc, dict):
                raw_text = (
                    doc.get("content_snippet")
                    or doc.get("content")
                    or doc.get("text")
                    or doc.get("page_content")
                    or ""
                )
                filename = doc.get("filename") or doc.get("name") or doc.get("source") or ""
                chunk_id = doc.get("chunk_id")
                triggers = doc.get("triggers")
            else:
                raw_text = (
                    getattr(doc, "page_content", None)
                    or getattr(doc, "content", None)
                    or getattr(doc, "text", None)
                    or ""
                )
                metadata = getattr(doc, "metadata", {}) or {}
                filename = (
                    metadata.get("filename")
                    or metadata.get("name")
                    or metadata.get("source")
                    or getattr(doc, "filename", "")
                    or ""
                )
                chunk_id = metadata.get("chunk_id") or getattr(doc, "chunk_id", None)
                triggers = metadata.get("triggers") or getattr(doc, "triggers", None)

            if not raw_text and filename:
                try:
                    from services.knowledge_manager import KnowledgeManager
                    raw_text = KnowledgeManager.get_document_knowledge_snippet(filename) or ""
                except Exception as e:
                    logger.debug(f"Не удалось получить сниппет документа {filename}: {e}")

            text = (raw_text or "").strip()
            if not text:
                continue

            if len(text) > max_chars_per_doc:
                text = text[:max_chars_per_doc] + TRUNCATION_MARKER

            triggers_str = ""
            if triggers:
                if isinstance(triggers, list):
                    triggers_str = ", ".join(str(t) for t in triggers)
                else:
                    triggers_str = str(triggers)

            if chunk_id and not triggers_str:
                if filename:
                    header = f'<Document chunk_id="{chunk_id}" name="{filename}">'
                else:
                    header = f'<Document chunk_id="{chunk_id}">'
            elif triggers_str:
                if filename:
                    header = f'<Document name="{filename}" triggers="{triggers_str}">'
                else:
                    header = f'<Document triggers="{triggers_str}">'
            elif filename:
                header = f'<Document name="{filename}">'
            else:
                header = '<Document>'

            footer = '</Document>'
            block = f"{header}\n{text}\n{footer}"
            block_len = len(block)

            if current_total_len + block_len > max_total_chars:
                if doc_blocks:
                    break
                overhead = len(header) + len(footer) + 2
                available = max_total_chars - overhead
                if available > len(TRUNCATION_MARKER) + 20:
                    text = text[:available - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
                    block = f"{header}\n{text}\n{footer}"
                    doc_blocks.append(block)
                break

            doc_blocks.append(block)
            current_total_len += block_len

        if not doc_blocks:
            return ""

        inner = "\n".join(doc_blocks)
        return f"<KnowledgeBase>\n{inner}\n</KnowledgeBase>\n\n"

    @_PromptBuilderMethod
    def build(
        self,
        user_name: str = "Пользователь",
        active_persona: Optional[Dict[str, Any]] = None,
        include_memory: bool = True,
        relevant_facts: Optional[List[str]] = None,
        is_excel: bool = False,
        is_word: bool = False,
        custom_prompt: Optional[str] = None,
        knowledge_block: Optional[str] = None,
        matched_knowledge_docs: Optional[List[Any]] = None,
        enable_reasoning: bool = False,
        attached_docs: Optional[List[Any]] = None,
        attached_document: Optional[Any] = None,
        attached_document_name: Optional[str] = None,
        user_query: Optional[str] = None,
        static_core_only: bool = True,
        **kwargs
    ) -> str:
        """
        Собирает исключительно статическое ядро системного промпта:
        - [identity & persona] (на базе SYSTEM_PERSONA_TEMPLATE без даты/времени,
          с защитной инструкцией ролей <TurnContext>)
        - <UserInfo> (имя и неизменяемое био пользователя)
        - Модификаторы формата (Excel/Word при наличии)
        - ROUTING_TAIL_PROTOCOL
        
        Токены этого промпта остаются побитово идентичными на протяжении всей сессии диалога,
        обеспечивая 100% стабильность префикса KV-кэша слота 0.
        При static_core_only=False возвращает объединенный с build_turn_context промпт (legacy fallback).
        """
        target_user = user_name.strip() if user_name and user_name.strip() else "Пользователь"

        # 1. <UserInfo>
        user_info_block = AssistantPromptBuilder.build_user_info(
            user_name=target_user,
            active_persona=active_persona
        )

        # 2. SYSTEM_PERSONA_TEMPLATE (статическое ядро)
        base_template = (custom_prompt and custom_prompt.strip()) or AssistantPromptBuilder.SYSTEM_PERSONA_TEMPLATE
        if "{current_dt_str}" in base_template:
            base_template = (
                base_template.replace("{current_dt_str}", "")
                .replace("- Дата и время: .\n", "")
                .replace("- Дата и время: \n", "")
            )
        persona_body = base_template
        persona_block = f"[identity & persona]\n{persona_body.strip()}\n\n"

        # 3. Модификаторы формата
        format_modifiers = ""
        if is_excel:
            format_modifiers = "User requested an Excel spreadsheet. Present data as a clean Markdown table.\n\n"
        elif is_word:
            format_modifiers = "User requested a Word document. Format response with clean headers and bullet lists.\n\n"

        # 4. ROUTING_TAIL_PROTOCOL
        tail_section = self.ROUTING_TAIL_PROTOCOL.format(user_name=target_user)

        prompt_parts = [
            persona_block,
            user_info_block,
            format_modifiers,
            tail_section
        ]

        static_core = "".join(part for part in prompt_parts if part).strip()

        if not static_core_only:
            # 2. <PassiveMemory>
            passive_memory_block = ""
            if include_memory:
                memory_data = relevant_facts if relevant_facts is not None else kwargs.get("passive_memory")
                passive_memory_block = AssistantPromptBuilder.build_passive_memory(memory_data)

            # 3. <KnowledgeBase>
            knowledge_section = ""
            if knowledge_block and knowledge_block.strip():
                kb_str = knowledge_block.strip()
                knowledge_section = f"{kb_str}\n\n"
            elif matched_knowledge_docs:
                knowledge_section = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
            elif kwargs.get("knowledge_context") and str(kwargs.get("knowledge_context")).strip():
                kb_str = str(kwargs.get("knowledge_context")).strip()
                knowledge_section = f"{kb_str}\n\n"

            # 4. <AttachedDocument>
            attached_docs_data = attached_docs or kwargs.get("attached_documents")
            attached_section = AssistantPromptBuilder.build_attached_documents(
                attached_docs=attached_docs_data,
                attached_document=attached_document,
                attached_document_name=attached_document_name
            )

            # 5. Семантическая маршрутизация скиллов
            effective_query = user_query or kwargs.get("task_text") or kwargs.get("user_message_text")
            skill_name = None
            score = 0.0
            reason = ""

            session_metadata = kwargs.get("session_metadata")
            if not session_metadata and kwargs.get("session_id"):
                try:
                    from services.session_manager import SessionManager
                    sess = SessionManager._sessions_cache.get(str(kwargs.get("session_id"))) or SessionManager.load_session(str(kwargs.get("session_id")))
                    if sess and isinstance(sess, dict):
                        session_metadata = sess.get("metadata", {})
                except Exception:
                    pass

            manual_skill = (
                kwargs.get("active_assistant_skill")
                or kwargs.get("manual_skill")
                or (session_metadata.get("active_assistant_skill") if isinstance(session_metadata, dict) else None)
            )

            all_skills = self.router.get_all_skills() if (hasattr(self, "router") and self.router and hasattr(self.router, "get_all_skills")) else {}

            if manual_skill and manual_skill != "auto":
                skill_meta = all_skills.get(manual_skill)
                if skill_meta:
                    skill_name = manual_skill
                else:
                    logger.warning(f"[AssistantPrompt] Зафиксированный скилл '{manual_skill}' не найден в реестре.")
            elif effective_query and hasattr(self, "router") and self.router:
                skill_name, score, reason = self.router.route(effective_query)

            skill_section = self._resolve_skill_section(
                skill_name=skill_name,
                target_user=target_user,
                effective_query=effective_query or "",
                all_skills=all_skills,
                **kwargs
            )

            full_parts = [
                persona_block,
                user_info_block,
                passive_memory_block,
                knowledge_section,
                attached_section,
                format_modifiers,
                skill_section,
                tail_section
            ]
            return "".join(part for part in full_parts if part).strip()

        return static_core

    @_PromptBuilderMethod
    def build_turn_context(
        self,
        user_name: str = "Пользователь",
        include_memory: bool = True,
        relevant_facts: Optional[List[str]] = None,
        knowledge_block: Optional[str] = None,
        matched_knowledge_docs: Optional[List[Any]] = None,
        attached_docs: Optional[List[Any]] = None,
        attached_document: Optional[Any] = None,
        attached_document_name: Optional[str] = None,
        user_query: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Формирует служебный контекст текущего хода <TurnContext>...</TurnContext>,
        объединяющий только волатильные динамические данные:
        - Текущая дата и время
        - Память LanceDB (<PassiveMemory>)
        - База знаний (<KnowledgeBase>)
        - Прикрепленные документы (<AttachedDocument>)
        - Активный скилл от SkillRouter (включая директивы gallery)
        """
        target_user = user_name.strip() if user_name and user_name.strip() else "Пользователь"

        # 1. Дата и время
        current_dt_str = AssistantPromptBuilder.get_current_datetime_str(kwargs.get("dt"))
        dt_line = f"- Текущая дата и время: {current_dt_str}.\n\n"

        # 2. <PassiveMemory>
        passive_memory_block = ""
        if include_memory:
            memory_data = relevant_facts if relevant_facts is not None else kwargs.get("passive_memory")
            passive_memory_block = AssistantPromptBuilder.build_passive_memory(memory_data)

        # 3. <KnowledgeBase>
        knowledge_section = ""
        if knowledge_block and knowledge_block.strip():
            kb_str = knowledge_block.strip()
            knowledge_section = f"{kb_str}\n\n"
        elif matched_knowledge_docs:
            knowledge_section = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
        elif kwargs.get("knowledge_context") and str(kwargs.get("knowledge_context")).strip():
            kb_str = str(kwargs.get("knowledge_context")).strip()
            knowledge_section = f"{kb_str}\n\n"

        # 4. <AttachedDocument>
        attached_docs_data = attached_docs or kwargs.get("attached_documents")
        attached_section = AssistantPromptBuilder.build_attached_documents(
            attached_docs=attached_docs_data,
            attached_document=attached_document,
            attached_document_name=attached_document_name
        )

        # 5. Семантическая маршрутизация скиллов
        effective_query = user_query or kwargs.get("task_text") or kwargs.get("user_message_text")
        skill_name = None
        score = 0.0
        reason = ""

        session_metadata = kwargs.get("session_metadata")
        if not session_metadata and kwargs.get("session_id"):
            try:
                from services.session_manager import SessionManager
                sess = SessionManager._sessions_cache.get(str(kwargs.get("session_id"))) or SessionManager.load_session(str(kwargs.get("session_id")))
                if sess and isinstance(sess, dict):
                    session_metadata = sess.get("metadata", {})
            except Exception:
                pass

        manual_skill = (
            kwargs.get("active_assistant_skill")
            or kwargs.get("manual_skill")
            or (session_metadata.get("active_assistant_skill") if isinstance(session_metadata, dict) else None)
        )

        all_skills = self.router.get_all_skills() if (hasattr(self, "router") and self.router and hasattr(self.router, "get_all_skills")) else {}

        if manual_skill and manual_skill != "auto":
            skill_meta = all_skills.get(manual_skill)
            if skill_meta:
                skill_name = manual_skill
                logger.info(f"[AssistantPrompt] Применен зафиксированный скилл: {manual_skill}")
            else:
                logger.warning(f"[AssistantPrompt] Зафиксированный скилл '{manual_skill}' не найден в реестре.")
        elif effective_query and hasattr(self, "router") and self.router:
            skill_name, score, reason = self.router.route(effective_query)
            if skill_name:
                logger.info(
                    f"[SkillRouter] АКТИВИРОВАН СКИЛЛ: '{skill_name}' | "
                    f"Оценка: {score:.3f} | Причина: {reason} | Запрос: {effective_query[:60]}..."
                )
            else:
                logger.info(
                    f"[SkillRouter] ДЕФОЛТНЫЙ ДИАЛОГ (Элария) | "
                    f"Оценка: {score:.3f} | Причина: {reason} | Запрос: {effective_query[:60]}..."
                )

        skill_section = self._resolve_skill_section(
            skill_name=skill_name,
            target_user=target_user,
            effective_query=effective_query or "",
            all_skills=all_skills,
            **kwargs
        )

        turn_parts = [
            dt_line,
            passive_memory_block,
            knowledge_section,
            attached_section,
            skill_section
        ]
        body = "".join(part for part in turn_parts if part).strip()
        if not body:
            return ""
        return f"<TurnContext>\n{body}\n</TurnContext>"

    @staticmethod
    def resolve_gallery_target_profile(
        effective_query: str = "",
        selected_forge_checkpoint: Optional[str] = None,
        check_config_fallback: bool = False,
    ) -> tuple[str, str, str, Optional[str]]:
        """
        Единый SSOT-резолвер целевой модели Forge, её архитектуры и описания стиля (routing_description):
        - При ручном выборе модели в шапке (selected_forge_checkpoint) — строго профиль выбранной модели.
        - В режиме 'Арт-директор (Авто)' — семантический подбор через ModelRouter.match_prompt.
        - В model_hint всегда попадает routing_description («Описание стиля»), без затирания его тегами llm_guidance.
        Возвращает: (target_model_id, target_arch, style_desc, manual_choice).
        """
        from services.model_router import ModelRouter, normalize_model_choice
        router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()

        manual_choice = normalize_model_choice(selected_forge_checkpoint)
        if not manual_choice and check_config_fallback:
            try:
                from utils.config_manager import ConfigManager
                cfg = ConfigManager.load_config()
                manual_choice = normalize_model_choice(cfg.get("selected_forge_checkpoint"))
            except Exception:
                manual_choice = None

        target_model_id: Optional[str] = None
        target_arch: Optional[str] = None
        style_desc: str = ""

        if manual_choice:
            prof = router.get_profile(manual_choice)
            if prof:
                target_model_id = prof.get("id") or prof.get("name") or manual_choice
                target_arch = (
                    prof.get("architecture")
                    or prof.get("base_architecture")
                    or (prof.get("pipeline") or {}).get("architecture")
                )
                style_desc = (
                    prof.get("routing_description")
                    or prof.get("description")
                    or prof.get("name")
                    or manual_choice
                ).strip()
            else:
                target_model_id = manual_choice
                target_arch = resolve_model_architecture(manual_choice)
                style_desc = manual_choice
        else:
            matched_id, _score = router.match_prompt(effective_query or "")
            if matched_id:
                prof = router.get_profile(matched_id)
                if prof:
                    target_model_id = matched_id
                    target_arch = (
                        prof.get("architecture")
                        or prof.get("base_architecture")
                        or (prof.get("pipeline") or {}).get("architecture")
                    )
                    style_desc = (
                        prof.get("routing_description")
                        or prof.get("description")
                        or prof.get("name")
                        or matched_id
                    ).strip()
            if not target_model_id:
                def_prof = router.get_default_profile() if hasattr(router, "get_default_profile") else None
                if def_prof:
                    target_model_id = def_prof.get("id", "realistic")
                    target_arch = (
                        def_prof.get("architecture")
                        or def_prof.get("base_architecture")
                        or (def_prof.get("pipeline") or {}).get("architecture")
                    )
                    style_desc = (
                        def_prof.get("routing_description")
                        or def_prof.get("description")
                        or def_prof.get("name")
                        or target_model_id
                    ).strip()
                else:
                    target_model_id = "realistic"
                    target_arch = "sd15"
                    style_desc = "Realistic"

        if not target_arch:
            target_arch = resolve_model_architecture(target_model_id)

        return target_model_id, target_arch, style_desc, manual_choice

    def _resolve_art_skill_section(
        self,
        skill_name: str = "gallery",
        effective_query: str = "",
        **kwargs
    ) -> tuple[str, str, Optional[str]]:
        """Разрешает активную модель/архитектуру Forge и формирует боевую директиву для gallery или cinematic."""
        from services.model_router import normalize_model_choice

        target_model_id = kwargs.get("target_model_id")
        target_arch = kwargs.get("architecture")
        best_desc = kwargs.get("target_model_desc") or kwargs.get("model_hint")
        has_explicit_ckpt = "selected_forge_checkpoint" in kwargs

        if not (target_model_id and target_arch):
            resolved_id, resolved_arch, resolved_desc, manual_choice = AssistantPromptBuilder.resolve_gallery_target_profile(
                effective_query=effective_query,
                selected_forge_checkpoint=kwargs.get("selected_forge_checkpoint"),
                check_config_fallback=not has_explicit_ckpt,
            )
            target_model_id = target_model_id or resolved_id
            target_arch = target_arch or resolved_arch
            if not best_desc:
                best_desc = resolved_desc
        else:
            manual_choice = normalize_model_choice(kwargs.get("selected_forge_checkpoint"))

        if not target_arch:
            target_arch = resolve_model_architecture(target_model_id)

        directive = get_art_directive(
            skill_name=skill_name,
            architecture=target_arch,
            model_hint=best_desc or target_model_id,
            target_model_id=target_model_id
        )
        return directive, target_arch, manual_choice

    def _resolve_gallery_skill_section(
        self,
        effective_query: str = "",
        **kwargs
    ) -> tuple[str, str, Optional[str]]:
        """Прокси для обратной совместимости."""
        return self._resolve_art_skill_section(skill_name="gallery", effective_query=effective_query, **kwargs)

    def _resolve_skill_section(
        self,
        skill_name: Optional[str],
        target_user: str,
        effective_query: str = "",
        all_skills: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """Возвращает реальный текстовый блок скилла, подставляемый в системный промпт LLM."""
        if not skill_name:
            return ""

        if skill_name in IMAGE_GENERATION_SKILLS:
            directive, _arch, _manual = self._resolve_art_skill_section(
                skill_name=skill_name,
                effective_query=effective_query,
                **kwargs
            )
            return f"{directive.strip()}\n\n"

        if skill_name in self.SKILL_PROMPTS:
            formatted_skill = self.SKILL_PROMPTS[skill_name].format(user_name=target_user)
            return f"{formatted_skill.strip()}\n\n"

        skills_map = all_skills if all_skills is not None else (
            self.router.get_all_skills() if (hasattr(self, "router") and self.router and hasattr(self.router, "get_all_skills")) else {}
        )
        if skill_name in skills_map:
            skill_meta = skills_map[skill_name]
            raw_prompt = skill_meta.get("system_prompt", "")
            if raw_prompt:
                if "{user_name}" in raw_prompt:
                    raw_prompt = raw_prompt.format(user_name=target_user)
                title = skill_meta.get("title", skill_name)
                return f"\n[АКТИВНЫЙ СКИЛЛ: {title}]\n{raw_prompt.strip()}\n\n"

        return ""

    @_PromptBuilderMethod
    def get_effective_skill_prompt(
        self,
        skill_id: str,
        user_name: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Возвращает реальный промпт скилла, который подставляется в LLM при его активации.
        Единый источник истины (SSOT) для предпросмотра в «Знаниях Эларии».
        """
        if not skill_id:
            return ""

        target_user = user_name.strip() if (user_name and user_name.strip()) else ""
        if not target_user:
            try:
                from utils.config_manager import ConfigManager
                cfg = ConfigManager.load_config()
                target_user = str(cfg.get("user_name") or "").strip() or "Пользователь"
            except Exception:
                target_user = "Пользователь"

        if skill_id in IMAGE_GENERATION_SKILLS:
            directive, target_arch, manual_choice = self._resolve_art_skill_section(skill_name=skill_id, **kwargs)
            parts = [directive.strip()]
            if not manual_choice:
                table = CINEMATIC_DIRECTIVES_BY_ARCH if skill_id == "cinematic" else GALLERY_DIRECTIVES_BY_ARCH
                alt_arch = "sdxl" if target_arch == "sd15" else "sd15"
                alt_template = table.get(alt_arch, "")
                if alt_template:
                    parts.append(
                        f"--- Альтернативный шаблон (если Арт-директор выберет {alt_arch.upper()}) ---\n"
                        f"{alt_template.strip()}"
                    )
            return "\n\n".join(parts)

        return self._resolve_skill_section(
            skill_name=skill_id,
            target_user=target_user,
            **kwargs
        ).strip()

    @staticmethod
    def build_gallery_art_prompt(
        user_query: str,
        target_model_id: str = "realistic",
        target_model_desc: str = "",
        architecture: Optional[str] = None,
        skill_name: str = "gallery"
    ) -> str:
        if not architecture:
            architecture = resolve_model_architecture(target_model_id)
        model_hint = f"{target_model_id} ({target_model_desc})" if target_model_desc else target_model_id
        directive = get_art_directive(
            skill_name=skill_name,
            architecture=architecture,
            model_hint=model_hint,
            target_model_id=target_model_id
        )
        return f"{user_query.strip()}{directive}"
