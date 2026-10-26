"""
services/skill_router.py - Легковесный гибридный роутер микро-скиллов на CPU.
Уровень 1: Мгновенные эвристики интентов действия (gallery, analyst_logic, wellness_bio, tutor_feynman) - 0 мс.
Уровень 2: Семантический векторный анализ (FastEmbed CPU, Max Similarity по естественным фразам-архетипам).
"""
import logging
import numpy as np
from typing import Optional, Tuple, Dict, List

logger = logging.getLogger("NeonRoute.SkillRouter")


class SkillRouter:
    def __init__(self, embedder=None):
        self.embedder = embedder
        
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
        if self.embedder:
            self._build_anchor_matrices()

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
