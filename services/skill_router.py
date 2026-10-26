"""
services/skill_router.py - Легковесный гибридный роутер микро-скиллов на CPU.
Уровень 1: Мгновенные эвристики интентов действия (gallery, analyst_logic) - 0 мс.
Уровень 2: Семантический векторный анализ (FastEmbed CPU, Max Similarity, строгий порог 0.52).
Поддерживаемые спец-скиллы: deep_engineer, gallery, code_architect, text_crafter, analyst_logic, wellness_bio.
"""
import logging
import numpy as np
from typing import Optional, Tuple, Dict, List

logger = logging.getLogger("NeonRoute.SkillRouter")


class SkillRouter:
    def __init__(self, embedder=None):
        self.embedder = embedder
        
        self.skill_anchors: Dict[str, List[str]] = {
            "deep_engineer": [
                "как работает вариатор акпп трансмиссия кинематика",
                "устройство механизмов, физика узлов, шкивы, конусы, ремень, гидравлика",
                "технический расчет, формулы, архитектура, механика, двигатель",
                "инженерный анализ, принцип действия агрегата, спецификация узла",
                "сопромат, расчет нагрузок, электротехника, принципиальная схема"
            ],
            "gallery": [
                "нарисуй арт изображение картинку визуализируй",
                "сгенерируй иллюстрацию покажи рисунок портрет кадр",
                "создай визуальный образ сцену концепт-арт"
            ],
            "code_architect": [
                "напиши скрипт код функция python powershell bash",
                "исправь ошибку баг исключение trace exception traceback",
                "архитектура программного обеспечения рефакторинг паттерн проектирования",
                "база данных sql запрос fastapi pydantic асинхронный код asyncio",
                "реализуй алгоритм типизация type hints модульные тесты pytest"
            ],
            "text_crafter": [
                "отредактируй текст перепиши без воды инфостиль",
                "составь деловое письмо коммерческое предложение пост",
                "сократи текст сделай выжимку саммари тезисы конспект",
                "исправь стилистику канцелярит улучши подачу текста",
                "структурируй статью заголовок лид призыв к действию"
            ],
            "analyst_logic": [
                "оценка рисков финансовая модель окупаемость юнит-экономика затраты",
                "анализ альтернатив взвешенная оценка критериев матрица решений",
                "логический разбор аргументов фактчекинг причинно-следственная связь",
                "стратегический анализ декомпозиция компромиссы риски внедрения"
            ],
            "wellness_bio": [
                "расчет калорий bmr дефицит профицит баланс бжу",
                "биомеханика упражнений техника приседа тяги тренировочный план",
                "физиология сна циркадные ритмы восстановление фазы сна",
                "эргономика рабочего места осанка нагрузка на позвоночник кресло монитор",
                "метаболизм энергообмен адаптация к нагрузкам перетренированность"
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
        logger.info(f"[SkillRouter] Инициализированы матрицы скиллов: {list(self.anchor_matrices.keys())}")

    def route(self, query: str) -> Tuple[Optional[str], float, str]:
        if not query or len(query.strip()) < 4:
            return None, 0.0, "Запрос слишком короткий"

        q_lower = query.lower().strip()

        # 1. Быстрые эвристики интента (0 мс)
        
        # 1.1. Арт и генерация изображений
        gallery_triggers = ("нарисуй", "сгенерируй арт", "сгенерируй картинку", "визуализируй образ", "нарисуй арт")
        if any(trig in q_lower for trig in gallery_triggers):
            return "gallery", 1.0, "Эвристический триггер запроса изображения"

        # 1.2. Сравнительный анализ альтернатив
        analyst_triggers = (
            "сравни ", "сравнить ", "сравнение ",
            "плюсы и минусы", "за и против",
            "что лучше выбрать", "что выбрать между",
            "матрица решений"
        )
        if any(trig in q_lower for trig in analyst_triggers):
            return "analyst_logic", 1.0, "Эвристический триггер сравнительного анализа"

        # 2. Семантический уровень (FastEmbed CPU через Max Similarity)
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

        # Строгий порог уверенности для чисто семантического поиска
        CONFIDENCE_THRESHOLD = 0.52
        if best_score >= CONFIDENCE_THRESHOLD:
            reason = f"Максимальная близость {best_score:.3f} >= {CONFIDENCE_THRESHOLD}"
            return best_skill, best_score, reason

        reason = f"Близость {best_score:.3f} ниже порога {CONFIDENCE_THRESHOLD} (дефолтный диалог Эларии)"
        return None, best_score, reason
