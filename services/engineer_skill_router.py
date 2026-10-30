"""
services/engineer_skill_router.py - Легковесный векторный роутер для режима «Инженер» на CPU.
Обеспечивает маршрутизацию задач разработчика по 5 фундаментальным инженерным архетипам
на базе модели sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (FastEmbed CPU ONNX, 0 MB VRAM).
Полностью изолирован от services/skill_router.py режима ассистента.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("NeonRoute.EngineerSkillRouter")

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CONFIDENCE_THRESHOLD = 0.25
FALLBACK_SKILL = "pure_logic_tdd"

# Глобальный синглтон модели эмбеддингов
_EMBEDDER_INSTANCE = None
_EMBEDDER_LOCK = threading.Lock()


def get_shared_embedder():
    """
    Потокобезопасная ленивая инициализация или переиспользование синглтона FastEmbed TextEmbedding.
    Сначала пытается переиспользовать уже прогретый экземпляр из services.memory_db,
    при необходимости создает новый легковесный экземпляр на CPU.
    """
    global _EMBEDDER_INSTANCE
    if _EMBEDDER_INSTANCE is not None:
        return _EMBEDDER_INSTANCE

    with _EMBEDDER_LOCK:
        if _EMBEDDER_INSTANCE is not None:
            return _EMBEDDER_INSTANCE

        # 1. Попытка переиспользовать синглтон из memory_db
        try:
            from services.memory_db import _get_embedding_engine
            engine, _, _ = _get_embedding_engine()
            if engine is not None:
                _EMBEDDER_INSTANCE = engine
                logger.info("[EngineerSkillRouter] Успешно переиспользован FastEmbed из memory_db.")
                return _EMBEDDER_INSTANCE
        except Exception as e:
            logger.debug(f"[EngineerSkillRouter] Не удалось переиспользовать embedder из memory_db: {e}")

        # 2. Прямая инициализация FastEmbed CPU ONNX
        try:
            from fastembed import TextEmbedding
            _EMBEDDER_INSTANCE = TextEmbedding(model_name=MODEL_NAME)
            logger.info(f"[EngineerSkillRouter] Создан новый FastEmbed CPU ONNX: {MODEL_NAME}")
            return _EMBEDDER_INSTANCE
        except Exception as e:
            logger.error(f"[EngineerSkillRouter] Критическая ошибка инициализации FastEmbed: {e}")
            return None


ENGINEER_SKILLS: Dict[str, Dict[str, Any]] = {
    "interactive_loop": {
        "title": "Игры и GUI приложения (Pygame / Tkinter / PyQt)",
        "triggers": "pygame, tkinter, pyqt, игра, gui, графический интерфейс, canvas, анимация, окно",
        "default_artifact": "game.py",
        "architect_rules": (
            "- Разделяй чистое логическое ядро (<name>_core.py) и визуализатор/раннер (game.py или app.py).\n"
            "- Проектируй структуру игрового/событийного цикла (loop), обработку событий ввода, FPS и состояние приложения.\n"
            "- Запрещено проектировать интерактивный CLI-ввод input(); взаимодействие только через оконные события (Pygame events, Tkinter binds)."
        ),
        "worker_rules": (
            "- Целевой файл по умолчанию game.py (или app.py).\n"
            "- Для Pygame: pygame.init(), окно pygame.display.set_mode(), цикл clock.tick(60), опрос pygame.event.get(), flip().\n"
            "- Для Tkinter/PyQt: root.mainloop(), привязка клавиш root.bind, работа с Canvas через coords/move по ID.\n"
            "- Коллизии через rect.colliderect(), запрещено строгое равенство координат (==).\n"
            "- Категорически запрещен input() (песочница неинтерактивна, входной поток закрыт)."
        ),
        "anchors": [
            "Создай 2D игру на Pygame с анимацией, спрайтами и управлением с клавиатуры",
            "Напиши аркаду тетрис, змейку, арканоид или космический шутер с игровым циклом game loop",
            "Напиши игру змейку или тетрис со счетчиком очков, механикой движения и управлением",
            "Разработай десктопное GUI приложение на Tkinter или PyQt с кнопками, формами и холстом Canvas",
            "Интерактивная игра с отрисовкой кадров, расчетом коллизий colliderect и игровым счетом",
            "Графический интерфейс с окном, виджетами, обработкой кликов мыши и таймером обновления",
            "Create an interactive 2D game or desktop GUI with Pygame, Tkinter, PyQt, game loop and event handling"
        ]
    },
    "pure_logic_tdd": {
        "title": "Алгоритмы, математика и структуры данных (TDD)",
        "triggers": "алгоритм, математика, логика, модуль, класс, структура данных, расчет, tdd, pytest",
        "default_artifact": "module.py",
        "architect_rules": (
            "- Проектируй чистые алгоритмические контракты (interface_spec), изолированные классы и чистые функции.\n"
            "- Никаких внешних GUI/CLI зависимостей, модули должны быть детерминированными и легко тестируемыми.\n"
            "- Четко специфицируй типы входных и выходных данных, обработку граничных условий и исключений."
        ),
        "worker_rules": (
            "- Целевой файл по умолчанию module.py.\n"
            "- Строгая типизация параметров и возвращаемых значений (typing), подробные docstrings.\n"
            "- Чистая архитектура: структуры данных, изолированные алгоритмы без побочных эффектов.\n"
            "- В блоке if __name__ == '__main__': лаконичный пример использования класса или функции.\n"
            "- Код должен полностью покрываться модульными тестами pytest."
        ),
        "anchors": [
            "Реализуй алгоритм сортировки, граф, поиск пути A-star или структуру данных",
            "Напиши чистый математический модуль, класс вычислений или библиотеку бизнес-логики",
            "Создай изолированный класс без интерфейса, чистые функции и модульные тесты pytest",
            "Вычислительный алгоритм, матричные операции, парсер грамматик и математических выражений",
            "Разработай ядро логики: структуры данных, типизированные контракты и алгоритмические методы",
            "Implement pure logic algorithms, data structures, math functions and unit testable core components"
        ]
    },
    "batch_artifact": {
        "title": "Пакетная генерация документов (Excel / Word)",
        "triggers": "excel, xlsx, word, docx, openpyxl, python-docx, отчет, смета, таблица, документ",
        "default_artifact": "make_doc.py",
        "architect_rules": (
            "- Проектируй пакетный скрипт make_doc.py, генерирующий итоговый бинарный документ (.xlsx, .docx).\n"
            "- Для Excel: спецификация схемы колонок, стилей ячеек, автоподбора ширины и формул.\n"
            "- Для Word: спецификация структуры разделов, абзацев, стилей run и форматирования таблиц."
        ),
        "worker_rules": (
            "- Целевой файл по умолчанию make_doc.py. Скрипт генерирует файл на диске и выводит OUTPUT_FILE: <path>.\n"
            "- Excel (openpyxl): формулы пиши строго в .value заглавными буквами на английском (=SUM, =AVERAGE), свойство .formula запрещено.\n"
            "- Excel стили: объекты Font, PatternFill, Alignment, Border неизменяемы, задавай новыми объектами. Автоподбор ширины колонок ws.column_dimensions.\n"
            "- Word (docx): форматирование текста строго через runs (run.font.bold = True, Pt). Не вызывай cell.font.\n"
            "- Запрещено передавать .xlsx/.docx в filename у write_file/run_script (строго make_doc.py)."
        ),
        "anchors": [
            "Сформируй отчет или таблицу в Excel (.xlsx) с формулами SUM, стилями ячеек и автоподбором колонок",
            "Создай красивый документ Word (.docx) с форматированием текста, параграфами и таблицами",
            "Сгенерируй офисную таблицу openpyxl со сметами, финансовыми расчетами и границами ячеек",
            "Генерация офисных отчетов и таблиц, экспорт расчетных данных в Excel (.xlsx) или Word (.docx)",
            "Создай отчет с расчетом итогов, формулами Excel и профессиональным оформлением документа",
            "Generate Excel spreadsheet openpyxl or Word document docx with styles, formulas and formatted tables"
        ]
    },
    "service_backend": {
        "title": "Веб-сервисы, API и микросервисы (FastAPI / Боты)",
        "triggers": "fastapi, flask, rest api, микросервис, бэкенд, telegram bot, aiogram, эндпоинты, сервер",
        "default_artifact": "main.py",
        "architect_rules": (
            "- Проектируй REST API эндпоинты, Pydantic модели запросов/ответов или хэндлеры команд бота.\n"
            "- Предусматривай асинхронную обработку (async/await), обработку HTTP ошибок и валидацию данных.\n"
            "- Разделяй бизнес-логику и транспортный уровень (контроллеры маршрутов / хэндлеры)."
        ),
        "worker_rules": (
            "- Целевой файл по умолчанию main.py.\n"
            "- Для FastAPI: инициализация FastAPI(), типизированные Pydantic схемы BaseModel, async def эндпоинты, запуск uvicorn.run() в if __name__ == '__main__':.\n"
            "- Для Telegram-ботов: инициализация Bot и Dispatcher (aiogram), регистрация хэндлеров сообщений/команд, async main().\n"
            "- Корректная обработка исключений (HTTPException) и валидация входящих данных.\n"
            "- Никаких блокирующих вызовов в асинхронных хэндлерах."
        ),
        "anchors": [
            "Разработай REST API веб-сервис или микросервис на FastAPI с эндпоинтами и Pydantic схемами",
            "Создай асинхронного телеграм-бота на aiogram с кнопками, клавиатурами и хэндлерами команд",
            "Серверный бэкенд: маршруты API, CRUD операции, валидация данных и фоновые задачи",
            "HTTP сервер с JSON маршрутизацией, асинхронной обработкой запросов и API endpoints",
            "Веб-сервис на FastAPI или Flask с маршрутизацией запросов и обработкой ошибок",
            "Build a FastAPI REST backend service, web API endpoints, or asynchronous Telegram bot with aiogram"
        ]
    },
    "cli_automation": {
        "title": "CLI утилиты и системная автоматизация",
        "triggers": "cli, консоль, argparse, утилита, автоматизация, мониторинг, диск, файлы, скрипт, sys, os",
        "default_artifact": "main.py",
        "architect_rules": (
            "- Проектируй интерфейс командной строки на argparse: позиционные аргументы, флаги (--flag), опции и справку help.\n"
            "- Определяй структуру автоматизации: сканирование файловой системы, сбор метрик ОС, парсинг логов.\n"
            "- Проектируй автономное выполнение без интерактивного ожидания ввода от пользователя."
        ),
        "worker_rules": (
            "- Целевой файл по умолчанию main.py.\n"
            "- Использование argparse.ArgumentParser с подробными описаниями аргументов и флагов.\n"
            "- Информативный структурированный вывод в консоль через print() или logging.\n"
            "- Автономность: если скрипт читает входной файл, проверить его наличие и при отсутствии создать демо-файл. Запрещен input().\n"
            "- Точка входа: обязателен блок if __name__ == '__main__':."
        ),
        "anchors": [
            "Напиши консольную утилиту командной строки на argparse с флагами, аргументами и справкой",
            "Создай системный скрипт автоматизации для мониторинга дисков, памяти, процессов и файлов",
            "Скрипт командной строки для работы с файлами, пакетного переименования, поиска и перемещения в папках",
            "Консольный скрипт системного администрирования, парсер логов и инспекции ОС",
            "Автоматизация рутинных задач ОС через командную строку с понятным выводом в stdout",
            "Command-line CLI automation tool with argparse flags, disk and system monitoring script"
        ]
    }
}


class EngineerSkillRouter:
    """
    Легковесный векторный роутер инженерных скиллов на CPU (FastEmbed).
    Вычисляет косинусное сходство между запросом и откалиброванными якорями архетипов
    с помощью быстрого np.dot. При уверенности ниже 0.25 возвращает pure_logic_tdd.
    """

    _instance: Optional["EngineerSkillRouter"] = None
    _singleton_lock: threading.Lock = threading.Lock()
    skills: Dict[str, Dict[str, Any]] = ENGINEER_SKILLS

    @classmethod
    def get_instance(cls, embedder: Optional[Any] = None, auto_init: bool = True) -> "EngineerSkillRouter":
        """Возвращает глобальный синглтон EngineerSkillRouter."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls(embedder=embedder, auto_init=auto_init)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Сброс синглтона (используется для изоляции в тестах)."""
        with cls._singleton_lock:
            cls._instance = None

    def __init__(self, embedder: Optional[Any] = None, auto_init: bool = True):
        self.embedder = embedder
        self.anchor_matrices: Dict[str, np.ndarray] = {}

        if self.embedder is None and auto_init:
            self.embedder = get_shared_embedder()

        if self.embedder is not None:
            self._build_anchor_matrices()

    def set_embedder(self, embedder: Any) -> None:
        """Устанавливает или переопределяет модель эмбеддингов и перестраивает матрицы якорей."""
        self.embedder = embedder
        if self.embedder is not None:
            self._build_anchor_matrices()
        else:
            self.anchor_matrices.clear()

    def _get_vector(self, text: str) -> np.ndarray:
        """Генерирует вектор эмбеддинга для переданного текста."""
        if self.embedder is None:
            return np.zeros(384, dtype=np.float32)

        try:
            if hasattr(self.embedder, "embed"):
                vec = list(self.embedder.embed([text]))[0]
                return np.array(vec, dtype=np.float32)
            elif hasattr(self.embedder, "encode"):
                vec = self.embedder.encode(text)
                return np.array(vec, dtype=np.float32)
        except Exception as e:
            logger.warning(f"[EngineerSkillRouter] Ошибка вычисления вектора: {e}")

        return np.zeros(384, dtype=np.float32)

    def _build_anchor_matrices(self) -> None:
        """Предварительно вычисляет и нормализует матрицы векторов для всех архетипов."""
        self.anchor_matrices.clear()
        for skill_id, skill_data in ENGINEER_SKILLS.items():
            anchors = skill_data.get("anchors", [])
            normed_vectors = []
            for anchor in anchors:
                vec = self._get_vector(anchor)
                norm = np.linalg.norm(vec)
                normed = vec / (norm if norm > 0 else 1.0)
                normed_vectors.append(normed)

            if normed_vectors:
                self.anchor_matrices[skill_id] = np.array(normed_vectors, dtype=np.float32)

        logger.info(
            f"[EngineerSkillRouter] Построены матрицы якорей для {len(self.anchor_matrices)} инженерных архетипов"
        )

    def get_skill_metadata(self, skill_id: str) -> Dict[str, Any]:
        """Возвращает полные метаданные архетипа по его идентификатору."""
        skill = ENGINEER_SKILLS.get(skill_id)
        if not skill:
            skill = ENGINEER_SKILLS[FALLBACK_SKILL]
            skill_id = FALLBACK_SKILL

        return {
            "skill_id": skill_id,
            "title": skill["title"],
            "triggers": skill["triggers"],
            "architect_rules": skill["architect_rules"],
            "worker_rules": skill["worker_rules"],
            "default_artifact": skill["default_artifact"],
        }

    def get_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """Возвращает реестр всех поддерживаемых инженерных скиллов."""
        return {k: self.get_skill_metadata(k) for k in ENGINEER_SKILLS}

    def route(self, query: str) -> Tuple[str, float, Dict[str, Any]]:
        """
        Векторная маршрутизация пользовательского запроса по инженерным архетипам.

        Args:
            query: Текст задачи разработчика.

        Returns:
            Tuple[str, float, Dict[str, Any]]:
                (skill_id, confidence_score, skill_metadata)
                При уверенности ниже 0.25 возвращает фоллбэк pure_logic_tdd.
        """
        fallback_meta = self.get_skill_metadata(FALLBACK_SKILL)

        if not query or not query.strip():
            return FALLBACK_SKILL, 0.0, fallback_meta

        # Ленивая сборка матриц, если эмбеддер был предоставлен позже
        if not self.anchor_matrices:
            if self.embedder is None:
                self.embedder = get_shared_embedder()
            if self.embedder is not None:
                self._build_anchor_matrices()

        if not self.anchor_matrices or self.embedder is None:
            return FALLBACK_SKILL, 0.0, fallback_meta

        q_vec = self._get_vector(query.strip())
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return FALLBACK_SKILL, 0.0, fallback_meta

        q_normed = q_vec / q_norm

        best_skill = FALLBACK_SKILL
        best_score = -1.0

        # Быстрый расчет косинусного сходства через np.dot
        for skill_id, matrix in self.anchor_matrices.items():
            sims = np.dot(matrix, q_normed)
            score = float(np.max(sims))
            if score > best_score:
                best_score = score
                best_skill = skill_id

        # Проверка порога уверенности
        if best_score < CONFIDENCE_THRESHOLD:
            return FALLBACK_SKILL, max(0.0, best_score), fallback_meta

        return best_skill, best_score, self.get_skill_metadata(best_skill)
