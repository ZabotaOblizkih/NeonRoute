"""
services/skill_router.py - Легковесный гибридный роутер микро-скиллов на CPU.
Уровень 1: Мгновенные эвристики интентов действия (gallery, analyst_logic, wellness_bio, tutor_feynman) - 0 мс.
Уровень 2: Семантический векторный анализ (FastEmbed CPU, Max Similarity по естественным фразам-архетипам).
"""
import json
import logging
import shutil
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any

import numpy as np

logger = logging.getLogger("NeonRoute.SkillRouter")

from config import DATA_DIR
CUSTOM_ASSISTANT_SKILLS_FILE = DATA_DIR / "custom_assistant_skills.json"

ASSISTANT_SKILLS: Dict[str, Dict[str, Any]] = {
    "analyst_logic": {
        "title": "📊 Системный аналитик",
        "human_description": "Глубокий сравнительный анализ, поиск компромиссов (trade-offs), оценка рисков и структурированные матрицы решений.",
        "triggers": "сравни, разница между, плюсы и минусы, за и против, анализ, сопоставь, матрица решений, trade-off",
        "is_builtin": True,
        "system_prompt": "Действуй как ведущий системный аналитик. Проводи беспристрастный сравнительный анализ, структурируй альтернативы, явно выделяй trade-offs и риски."
    },
    "code_architect": {
        "title": "💻 Архитектор кода",
        "human_description": "Проектирование чистой архитектуры, рефакторинг, отладка ошибок и эффективные алгоритмы со строгой типизацией.",
        "triggers": "код, скрипт, функция, баг, архитектура, рефакторинг, алгоритм, traceback, pytest",
        "is_builtin": True,
        "system_prompt": "Действуй как Software Architect. Пиши чистый, типизированный и надежный код, предлагай масштабируемые решения и детально разбирай первопричины ошибок."
    },
    "text_crafter": {
        "title": "✍️ Текстовый редактор",
        "human_description": "Редактура текста, устранение канцелярита и воды, адаптация в инфостиле, деловые письма и емкие выжимки.",
        "triggers": "отредактируй, перепиши, инфостиль, конспект, статья, выжимка, деловое письмо, пост",
        "is_builtin": True,
        "system_prompt": "Действуй как опытный редактор. Пиши емко, выразительно, в понятном инфостиле без канцелярита, структурируй текст и усиливай смысл."
    },
    "tutor_feynman": {
        "title": "🎓 Метод Фейнмана",
        "human_description": "Объяснение сложных концепций простым языком на пальцах через наглядные бытовые аналогии без заумных терминов.",
        "triggers": "на пальцах, простыми словами, метод фейнмана, простым языком, наглядно, как для пятиклассника",
        "is_builtin": True,
        "system_prompt": "Используй метод Фейнмана. Объясняй сложнейшие концепции предельно доступно, используя яркие бытовые аналогии и поэтапную логику от простого к глубокому."
    },
    "wellness_bio": {
        "title": "🌿 Биомеханика и эргономика",
        "human_description": "Эргономика рабочего места, поддержание осанки, фазы сна, циркадные ритмы, расчет калорий BMR и производственная разминка.",
        "triggers": "калории, бжу, bmr, осанка, эргономика, фазы сна, циркадные, разминка, суставы",
        "is_builtin": True,
        "system_prompt": "Выступай экспертом по биомеханике, эргономике и физиологии. Давай научно обоснованные рекомендации по осанке, сну, восстановлению и эргономике."
    },
    "gallery": {
        "title": "🎨 Промпт-инженер артов",
        "human_description": "Визуализация сцен, генерация художественных описаний и подготовка детальных промптов для диффузионных моделей.",
        "triggers": "нарисуй, сгенерируй арт, картинка, визуализируй, иллюстрация, портрет, кадр",
        "is_builtin": True,
        "system_prompt": "Действуй как арт-директор и эксперт по промпт-инжинирингу. Формируй выразительные художественные композиции, описывай детали освещения, стиля и ракурса."
    },
    "deep_engineer": {
        "title": "⚙️ Инженерная физика",
        "human_description": "Фундаментальная механика, кинематика, сопромат, расчет нагрузок и принципы действия промышленных узлов.",
        "triggers": "физика, кинематика, механика, сопромат, расчет нагрузок, гидравлика, принципиальная схема",
        "is_builtin": True,
        "system_prompt": "Действуй как инженер-механик и физик. Объясняй принципы работы узлов, кинематику, распределение нагрузок и точные физические законы."
    }
}


class SkillRouter:
    _instance: Optional["SkillRouter"] = None

    @classmethod
    def get_instance(cls, embedder=None) -> "SkillRouter":
        if cls._instance is None:
            cls._instance = cls(embedder=embedder)
        elif embedder is not None and cls._instance.embedder is None:
            cls._instance.set_embedder(embedder)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    def __init__(self, embedder=None):
        self.embedder = embedder
        self.custom_skills: Dict[str, Dict[str, Any]] = {}

        # Естественные фразы-архетипы интентов вместо изолированных ключевых слов
        self.skill_anchors: Dict[str, List[str]] = {
            "deep_engineer": [
                "Объясни физику, кинематику и механику работы этого узла или механизма",
                "Как устроена эта деталь, расчет нагрузок, сопромат и принципиальная схема",
                "Инженерный расчет прочности, гидравлическая система или электромеханика агрегата",
                "Принцип действия и спецификация промышленного механизма, трансмиссии или двигателя",
                "Фундаментальная физика, законы механики, термодинамика и технические расчеты"
            ],
            "gallery": [
                "Нарисуй концепт-арт или детальную цифровую иллюстрацию",
                "Визуализируй эту сцену в виде художественного изображения или портрета",
                "Сделай красивый визуальный кадр по текстовому описанию",
                "Сгенерируй изображение персонажа или локации в высоком качестве",
                "Покажи мне как выглядит этот персонаж, животное, предмет или сцена в виде арта"
            ],
            "code_architect": [
                "Напиши чистый рабочий код функции или скрипт с обработкой исключений",
                "Помоги найти причину бага, разобрать traceback ошибки и отрефакторить код",
                "Спроектируй архитектуру сервиса, схему базы данных или асинхронный пайплайн",
                "Реализуй эффективный алгоритм со строгой типизацией и тестами pytest",
                "Оптимизируй производительность скрипта, запросы к базе или сетевой код"
            ],
            "text_crafter": [
                "Отредактируй текст, убери воду и канцелярит, улучши подачу",
                "Составь убедительное деловое письмо, коммерческое предложение или статью",
                "Сделай краткую структурированную выжимку, тезисы и конспект материала",
                "Перепиши черновик в емком понятном инфостиле для публикации",
                "Помоги составить пост, цепляющий заголовок и структуру текста"
            ],
            "analyst_logic": [
                "Проведи глубокий сравнительный анализ и покажи ключевые различия",
                "В чем принципиальная разница между этими двумя решениями или технологиями",
                "Разбери компромиссы, скрытые риски, плюсы и минусы каждого варианта",
                "Сопоставь альтернативы и помоги выбрать оптимальный путь с учетом trade-offs",
                "Составь матрицу выбора, взвесив сильные и слабые стороны подходов",
                "Проанализируй данные, таблицу или отчет и сделай логические выводы"
            ],
            "wellness_bio": [
                "Сколько калорий мне нужно потреблять в день, расчет нормы базового обмена и дефицита",
                "Как эргономично настроить рабочее место, монитор и кресло для осанки",
                "Правильная биомеханика упражнений и предотвращение травм спины и суставов",
                "Оптимизация фаз сна, циркадные ритмы и способы восстановления сил",
                "Влияние сидячего образа жизни на организм, баланс БЖУ и разминка"
            ],
            "tutor_feynman": [
                "Объясни мне эту сложную концепцию простыми словами на пальцах",
                "Приведи наглядную бытовую аналогию, чтобы понять физическую суть явления",
                "В чем фундаментальный смысл этой абстрактной теории без заумных терминов",
                "Как устроены квантовая физика, расщепление атомов или теория относительности наглядно",
                "Интуитивное понимание сложных научных тем через жизненные примеры"
            ]
        }
        self.anchor_matrices: Dict[str, np.ndarray] = {}
        self._load_custom_skills()
        if self.embedder:
            self._build_anchor_matrices()

    def get_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """Возвращает объединение базовых и кастомных скиллов."""
        all_s: Dict[str, Dict[str, Any]] = {}
        for k, v in ASSISTANT_SKILLS.items():
            all_s[k] = dict(v)
            all_s[k]["skill_id"] = k
        for k, v in self.custom_skills.items():
            all_s[k] = dict(v)
            all_s[k]["skill_id"] = k
        return all_s

    def _load_custom_skills(self):
        """Безопасно читает data/custom_assistant_skills.json."""
        self.custom_skills = {}
        if CUSTOM_ASSISTANT_SKILLS_FILE.exists():
            try:
                with open(CUSTOM_ASSISTANT_SKILLS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        for sid, sdata in loaded.items():
                            if isinstance(sdata, dict):
                                sdata["is_builtin"] = False
                                sdata["skill_id"] = sid
                                self.custom_skills[sid] = sdata
                                anchors_raw = sdata.get("anchors") or sdata.get("triggers", "")
                                if isinstance(anchors_raw, list):
                                    anchors = [str(a).strip() for a in anchors_raw if str(a).strip()]
                                elif isinstance(anchors_raw, str):
                                    anchors = [a.strip() for a in anchors_raw.split(",") if a.strip()]
                                else:
                                    anchors = []
                                if anchors:
                                    self.skill_anchors[sid] = anchors
                                elif sdata.get("title"):
                                    self.skill_anchors[sid] = [sdata.get("title")]
            except Exception as e:
                logger.error(f"[SkillRouter] Ошибка загрузки кастомных скиллов: {e}")

    def save_custom_skill(self, skill_dict: Dict[str, Any]) -> bool:
        """Валидирует skill_id, сохраняет в JSON и обновляет skill_anchors в памяти."""
        try:
            skill_id = str(skill_dict.get("skill_id", "")).strip()
            if not skill_id:
                logger.error("[SkillRouter] skill_id не может быть пустым.")
                return False
            if skill_id in ASSISTANT_SKILLS:
                logger.error(f"[SkillRouter] Нельзя перезаписать базовый скилл: '{skill_id}'.")
                return False

            title = str(skill_dict.get("title", "")).strip()
            if not title:
                logger.error("[SkillRouter] title не может быть пустым.")
                return False

            triggers = str(skill_dict.get("triggers", "")).strip()
            anchors_raw = skill_dict.get("anchors", [])
            if isinstance(anchors_raw, str):
                anchors = [line.strip() for line in anchors_raw.splitlines() if line.strip()]
            elif isinstance(anchors_raw, list):
                anchors = [str(a).strip() for a in anchors_raw if str(a).strip()]
            else:
                anchors = []

            if not anchors and triggers:
                anchors = [t.strip() for t in triggers.split(",") if t.strip()]

            human_description = str(skill_dict.get("human_description", "")).strip()
            system_prompt = str(skill_dict.get("system_prompt", "")).strip()

            normalized_skill = {
                "skill_id": skill_id,
                "title": title,
                "human_description": human_description,
                "triggers": triggers or (", ".join(anchors[:5]) if anchors else ""),
                "anchors": anchors,
                "system_prompt": system_prompt,
                "is_builtin": False
            }

            custom_skills: Dict[str, Any] = {}
            if CUSTOM_ASSISTANT_SKILLS_FILE.exists():
                try:
                    with open(CUSTOM_ASSISTANT_SKILLS_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            custom_skills = loaded
                except Exception as e:
                    logger.warning(f"[SkillRouter] Не удалось прочитать кастомные скиллы: {e}")

            custom_skills[skill_id] = normalized_skill

            CUSTOM_ASSISTANT_SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp_file = CUSTOM_ASSISTANT_SKILLS_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(custom_skills, f, ensure_ascii=False, indent=2)
            shutil.move(temp_file, CUSTOM_ASSISTANT_SKILLS_FILE)

            self.custom_skills[skill_id] = normalized_skill
            if anchors:
                self.skill_anchors[skill_id] = anchors
            elif triggers:
                self.skill_anchors[skill_id] = [t.strip() for t in triggers.split(",") if t.strip()]
            else:
                self.skill_anchors[skill_id] = [title]

            if self.embedder:
                self._build_anchor_matrices()

            logger.info(f"[SkillRouter] Сохранен кастомный скилл: {skill_id}")
            return True
        except Exception as e:
            logger.error(f"[SkillRouter] Ошибка сохранения кастомного скилла: {e}", exc_info=True)
            return False

    def delete_custom_skill(self, skill_id: str) -> bool:
        """Удаляет скилл из JSON (запрещая удаление is_builtin == True) и убирает из памяти."""
        if skill_id in ASSISTANT_SKILLS or self.get_all_skills().get(skill_id, {}).get("is_builtin", False):
            logger.warning(f"[SkillRouter] Запрещено удалять базовый скилл: {skill_id}")
            return False

        if not CUSTOM_ASSISTANT_SKILLS_FILE.exists():
            return False

        try:
            with open(CUSTOM_ASSISTANT_SKILLS_FILE, "r", encoding="utf-8") as f:
                custom_skills = json.load(f)
            if not isinstance(custom_skills, dict):
                return False
            if skill_id not in custom_skills:
                return False

            del custom_skills[skill_id]

            temp_file = CUSTOM_ASSISTANT_SKILLS_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(custom_skills, f, ensure_ascii=False, indent=2)
            shutil.move(temp_file, CUSTOM_ASSISTANT_SKILLS_FILE)

            self.custom_skills.pop(skill_id, None)
            self.skill_anchors.pop(skill_id, None)
            self.anchor_matrices.pop(skill_id, None)

            logger.info(f"[SkillRouter] Удален кастомный скилл: {skill_id}")
            return True
        except Exception as e:
            logger.error(f"[SkillRouter] Ошибка при удалении скилла '{skill_id}': {e}", exc_info=True)
            return False

    def set_embedder(self, embedder):
        self.embedder = embedder
        self._build_anchor_matrices()

    def _get_vector(self, text: str) -> np.ndarray:
        if hasattr(self.embedder, "embed"):
            vec = list(self.embedder.embed([text]))[0]
            return np.array(vec, dtype=np.float32)
        elif hasattr(self.embedder, "encode"):
            vec = self.embedder.encode(text)
            return np.array(vec, dtype=np.float32)
        return np.zeros(384, dtype=np.float32)

    def _build_anchor_matrices(self):
        for name, anchors in self.skill_anchors.items():
            vecs = [self._get_vector(a) for a in anchors]
            normed = []
            for v in vecs:
                n = np.linalg.norm(v)
                normed.append(v / (n if n > 0 else 1.0))
            self.anchor_matrices[name] = np.array(normed, dtype=np.float32)
        logger.info(f"[SkillRouter] Инициализировано скиллов в RAM: {len(self.anchor_matrices)}")

    def route(self, query: str) -> Tuple[Optional[str], float, str]:
        if not query or len(query.strip()) < 4:
            return None, 0.0, "Запрос слишком короткий"

        q_lower = query.lower().strip()

        # ── 1. Быстрые эвристики интента (0 мс) ──
        
        # 1.1. Арт, генерация и показ изображений
        gallery_triggers = (
            "нарисуй", "сгенерируй арт", "сгенерируй картинку", "визуализируй", "сделай арт",
            "покажи как выглядит", "покажи как выглядят", "покажи картинку", "покажи арт",
            "покажи рисунок", "просьба показать"
        )
        if any(trig in q_lower for trig in gallery_triggers):
            return "gallery", 1.0, "Эвристический триггер запроса изображения"

        # 1.2. Сравнительный анализ и выбор
        analyst_triggers = (
            "сравни", "сравнить", "сравнение", "сравнительн",
            "разница между", "разницу между", "различия между", "в чем разница", "в чём разница",
            "плюсы и минусы", "за и против", "плюсов и минусов",
            "что лучше", "проведи анализ", "проанализируй", "сопоставь",
            "матрица решений", "trade-off", "компромиссы"
        )
        if any(trig in q_lower for trig in analyst_triggers):
            return "analyst_logic", 1.0, "Эвристический триггер сравнительного анализа"

        # 1.3. Здоровье, эргономика и калории
        wellness_triggers = (
            "калор", "каллор", "ккал", "bmr", "бжу",
            "осанк", "эргономик", "фазы сна", "циркадн", "разминк"
        )
        if any(trig in q_lower for trig in wellness_triggers):
            return "wellness_bio", 1.0, "Эвристический триггер здоровья и биомеханики"

        # 1.4. Метод Фейнмана и простые аналогии
        feynman_triggers = (
            "на пальцах", "простыми словами", "как для пятиклассника",
            "метод фейнмана", "объясни суть простым языком", "простым языком"
        )
        if any(trig in q_lower for trig in feynman_triggers):
            return "tutor_feynman", 1.0, "Эвристический триггер метода Фейнмана"

        # ── 2. Семантический уровень (FastEmbed CPU через Max Similarity) ──
        if not self.anchor_matrices or not self.embedder:
            return None, 0.0, "Эмбеддер не инициализирован (дефолтный диалог Эларии)"

        q_vec = self._get_vector(query)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return None, 0.0, "Нулевой вектор запроса"
        q_vec = q_vec / q_norm

        best_skill = None
        best_score = -1.0

        for name, matrix in self.anchor_matrices.items():
            sims = np.dot(matrix, q_vec)
            score = float(np.max(sims))
            if score > best_score:
                best_score = score
                best_skill = name

        # Скорректированный порог уверенности
        CONFIDENCE_THRESHOLD = 0.50
        if best_score >= CONFIDENCE_THRESHOLD:
            reason = f"Максимальная близость {best_score:.3f} >= {CONFIDENCE_THRESHOLD}"
            return best_skill, best_score, reason

        reason = f"Близость {best_score:.3f} ниже порога {CONFIDENCE_THRESHOLD} (дефолтный диалог Эларии)"
        return None, best_score, reason
