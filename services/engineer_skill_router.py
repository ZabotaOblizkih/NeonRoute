"""
services/engineer_skill_router.py - Легковесный векторный роутер для режима «Инженер» на CPU.
Обеспечивает маршрутизацию задач разработчика по фундаментальным инженерным архетипам
на базе модели sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (FastEmbed CPU ONNX, 0 MB VRAM).
Поддерживает кастомные пользовательские скиллы из data/custom_engineer_skills.json
и иконки из data/skill_icons/.
"""

import copy
import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger("NeonRoute.EngineerSkillRouter")

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CONFIDENCE_THRESHOLD = 0.25
FALLBACK_SKILL = "pure_logic_tdd"

# Директории хранения кастомных скиллов и иконок
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CUSTOM_SKILLS_FILE = DATA_DIR / "custom_engineer_skills.json"
SKILL_ICONS_DIR = DATA_DIR / "skill_icons"

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


def _ensure_default_icons() -> None:
    """Создает базовые иконки 48x48 в data/skill_icons, если они отсутствуют."""
    try:
        SKILL_ICONS_DIR.mkdir(parents=True, exist_ok=True)
        icon_colors = {
            "interactive_loop": (139, 92, 246),  # Фиолетовый
            "pure_logic_tdd": (6, 182, 212),     # Бирюзовый
            "batch_artifact": (16, 185, 129),    # Изумрудный
            "service_backend": (59, 130, 246),   # Синий
            "cli_automation": (245, 158, 11),    # Янтарный
        }
        from PIL import Image, ImageDraw
        for sid, color in icon_colors.items():
            icon_file = SKILL_ICONS_DIR / f"{sid}.png"
            if not icon_file.exists():
                img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.rounded_rectangle((2, 2, 45, 45), radius=10, fill=(*color, 45), outline=(*color, 200), width=2)
                d.rounded_rectangle((10, 10, 37, 37), radius=6, fill=(*color, 160))
                img.save(icon_file, "PNG")
    except Exception as e:
        logger.debug(f"[EngineerSkillRouter] Ошибка создания дефолтных иконок: {e}")


ENGINEER_SKILLS: Dict[str, Dict[str, Any]] = {
    "interactive_loop": {
        "title": "🎮 Игры и GUI (Pygame / Tkinter / PyQt)",
        "human_description": "Разработка 2D игр, графических интерфейсов и интерактивных приложений на Pygame, Tkinter или PyQt с игровым циклом и событиями.",
        "icon_path": "data/skill_icons/interactive_loop.png",
        "is_builtin": True,
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
        "title": "⚙️ Чистая логика (Алгоритмы и TDD)",
        "human_description": "Создание чистой бизнес-логики, алгоритмов, структур данных и математических расчетов без GUI с покрытием тестами pytest.",
        "icon_path": "data/skill_icons/pure_logic_tdd.png",
        "is_builtin": True,
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
        "title": "📊 Документы и отчеты (Excel / Word)",
        "human_description": "Пакетная генерация и стилизация офисных таблиц Excel (.xlsx) и документов Word (.docx) через openpyxl и python-docx.",
        "icon_path": "data/skill_icons/batch_artifact.png",
        "is_builtin": True,
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
        "title": "🌐 Сервисы и API (FastAPI / Боты)",
        "human_description": "Разработка REST API микросервисов на FastAPI, эндпоинтов, Pydantic моделей и асинхронных Telegram-ботов на aiogram.",
        "icon_path": "data/skill_icons/service_backend.png",
        "is_builtin": True,
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
        "title": "💻 Консольные утилиты (CLI и автоматизация)",
        "human_description": "Разработка консольных CLI утилит с флагами argparse, системных скриптов автоматизации, мониторинга ОС и файлов.",
        "icon_path": "data/skill_icons/cli_automation.png",
        "is_builtin": True,
        "triggers": "cli, консоль, argparse, утилита, автоматизация, мониторинг, диск, файлы, скрипт, sys, os",
        "default_artifact": "main.py",
        "architect_rules": (
            "- Проектируй интерфейс командной строки на argparse: опциональные флаги (--flag) или позиционные аргументы со значениями по умолчанию (nargs='?', default=...).\n"
            "- Определяй структуру автоматизации: сканирование файловой системы, сбор метрик ОС, парсинг логов.\n"
            "- Проектируй автономное выполнение: скрипт обязан успешно выполняться без аргументов в демо-режиме (код возврата 0)."
        ),
        "worker_rules": (
            "- Целевой файл по умолчанию main.py.\n"
            "- Использование argparse.ArgumentParser с подробными описаниями аргументов и флагов.\n"
            "- Информативный структурированный вывод в консоль через print() или logging.\n"
            "- Автономность: если скрипт принимает путь к файлу через argparse, ОБЯЗАТЕЛЬНО укажи default='demo.txt' и nargs='?' (или проверь if len(sys.argv) > 1). При отсутствии файла скрипт должен сам создать демо-файл и отработать на нем без сбоя. Запрещен input().\n"
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
    Поддерживает динамическую загрузку кастомных скиллов из JSON.
    """

    _instance: Optional["EngineerSkillRouter"] = None
    _singleton_lock: threading.Lock = threading.Lock()
    skills: Dict[str, Dict[str, Any]] = copy.deepcopy(ENGINEER_SKILLS)

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
        self.skills: Dict[str, Dict[str, Any]] = {}

        _ensure_default_icons()
        self._load_skills()
        EngineerSkillRouter.skills = self.skills

        if self.embedder is None and auto_init:
            self.embedder = get_shared_embedder()

        if self.embedder is not None:
            self._build_anchor_matrices()

    def _load_skills(self) -> None:
        """Загружает базовые скиллы и объединяет их с кастомными из JSON."""
        self.skills = copy.deepcopy(ENGINEER_SKILLS)
        for sid, sdata in self.skills.items():
            sdata["is_builtin"] = True
            sdata["skill_id"] = sid
            if not sdata.get("icon_path"):
                sdata["icon_path"] = f"data/skill_icons/{sid}.png"

        if CUSTOM_SKILLS_FILE.exists():
            try:
                with open(CUSTOM_SKILLS_FILE, "r", encoding="utf-8") as f:
                    custom_skills = json.load(f)
                    if isinstance(custom_skills, dict):
                        for sid, sdata in custom_skills.items():
                            if isinstance(sdata, dict):
                                sdata["is_builtin"] = False
                                sdata["skill_id"] = sid
                                self.skills[sid] = sdata
            except Exception as e:
                logger.error(f"[EngineerSkillRouter] Ошибка загрузки кастомных скиллов: {e}")

    def save_custom_skill(self, skill_dict: Dict[str, Any]) -> None:
        """
        Валидация, сохранение кастомного скилла в JSON и вызов reload_skills().
        """
        skill_id = str(skill_dict.get("skill_id", "")).strip()
        if not skill_id:
            raise ValueError("Идентификатор скилла (skill_id) не может быть пустым.")

        if skill_id in ENGINEER_SKILLS:
            raise ValueError(f"Нельзя перезаписать базовый архетип '{skill_id}'.")

        title = str(skill_dict.get("title", "")).strip()
        if not title:
            raise ValueError("Название скилла (title) не может быть пустым.")

        anchors_raw = skill_dict.get("anchors", [])
        if isinstance(anchors_raw, str):
            anchors = [line.strip() for line in anchors_raw.splitlines() if line.strip()]
        elif isinstance(anchors_raw, list):
            anchors = [str(a).strip() for a in anchors_raw if str(a).strip()]
        else:
            anchors = []

        triggers = str(skill_dict.get("triggers", "")).strip()
        if not triggers and anchors:
            triggers = ", ".join(anchors[:5])

        normalized_skill = {
            "skill_id": skill_id,
            "title": title,
            "human_description": str(skill_dict.get("human_description", "")).strip(),
            "triggers": triggers,
            "anchors": anchors,
            "architect_rules": str(skill_dict.get("architect_rules", "")).strip(),
            "worker_rules": str(skill_dict.get("worker_rules", "")).strip(),
            "default_artifact": str(skill_dict.get("default_artifact", "main.py")).strip() or "main.py",
            "icon_path": str(skill_dict.get("icon_path", "")).strip(),
            "is_builtin": False
        }

        custom_skills: Dict[str, Any] = {}
        if CUSTOM_SKILLS_FILE.exists():
            try:
                with open(CUSTOM_SKILLS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        custom_skills = loaded
            except Exception as e:
                logger.warning(f"[EngineerSkillRouter] Не удалось прочитать кастомные скиллы: {e}")

        custom_skills[skill_id] = normalized_skill

        CUSTOM_SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = CUSTOM_SKILLS_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(custom_skills, f, ensure_ascii=False, indent=2)
        shutil.move(temp_file, CUSTOM_SKILLS_FILE)

        self.reload_skills()
        logger.info(f"[EngineerSkillRouter] Сохранен кастомный скилл '{skill_id}'")

    def delete_custom_skill(self, skill_id: str) -> bool:
        """
        Удаление из JSON (запрещено удалять is_builtin=True) и вызов reload_skills().
        """
        if skill_id in ENGINEER_SKILLS or self.skills.get(skill_id, {}).get("is_builtin", False):
            logger.warning(f"[EngineerSkillRouter] Запрещено удалять базовый архетип: {skill_id}")
            return False

        if not CUSTOM_SKILLS_FILE.exists():
            return False

        try:
            with open(CUSTOM_SKILLS_FILE, "r", encoding="utf-8") as f:
                custom_skills = json.load(f)
                if not isinstance(custom_skills, dict):
                    return False

            if skill_id not in custom_skills:
                return False

            del custom_skills[skill_id]

            temp_file = CUSTOM_SKILLS_FILE.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(custom_skills, f, ensure_ascii=False, indent=2)
            shutil.move(temp_file, CUSTOM_SKILLS_FILE)

            self.reload_skills()
            logger.info(f"[EngineerSkillRouter] Удален кастомный скилл '{skill_id}'")
            return True
        except Exception as e:
            logger.error(f"[EngineerSkillRouter] Ошибка при удалении скилла '{skill_id}': {e}")
            return False

    def reload_skills(self) -> None:
        """Перезагружает скиллы и пересчитывает матрицы anchor_matrices через FastEmbed."""
        self._load_skills()
        EngineerSkillRouter.skills = self.skills
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
        """Предварительно вычисляет и нормализует матрицы векторов для всех активных скиллов."""
        self.anchor_matrices.clear()
        for skill_id, skill_data in self.skills.items():
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
            f"[EngineerSkillRouter] Построены матрицы якорей для {len(self.anchor_matrices)} инженерных скиллов"
        )

    def get_skill_metadata(self, skill_id: str) -> Dict[str, Any]:
        """Возвращает полные метаданные архетипа по его идентификатору."""
        skill = self.skills.get(skill_id)
        if not skill:
            skill = self.skills.get(FALLBACK_SKILL, ENGINEER_SKILLS[FALLBACK_SKILL])
            skill_id = FALLBACK_SKILL

        return {
            "skill_id": skill_id,
            "title": skill.get("title", ""),
            "triggers": skill.get("triggers", ""),
            "human_description": skill.get("human_description", ""),
            "icon_path": skill.get("icon_path", ""),
            "is_builtin": skill.get("is_builtin", False),
            "architect_rules": skill.get("architect_rules", ""),
            "worker_rules": skill.get("worker_rules", ""),
            "default_artifact": skill.get("default_artifact", "main.py"),
            "anchors": skill.get("anchors", []),
        }

    def get_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """Возвращает реестр всех поддерживаемых инженерных скиллов (базовые + кастомные)."""
        return {k: self.get_skill_metadata(k) for k in self.skills}

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
            if len(matrix) == 0:
                continue
            sims = np.dot(matrix, q_normed)
            score = float(np.max(sims))
            if score > best_score:
                best_score = score
                best_skill = skill_id

        # Проверка порога уверенности
        if best_score < CONFIDENCE_THRESHOLD:
            return FALLBACK_SKILL, max(0.0, best_score), fallback_meta

        return best_skill, best_score, self.get_skill_metadata(best_skill)

    def test_route_ranking(self, query: str) -> List[Dict[str, Any]]:
        """
        Вычисляет сходство для каждого скилла и возвращает список словарей
        с полями skill_id, title, score, is_winner, отсортированный по убыванию score.
        """
        if not self.anchor_matrices:
            if self.embedder is None:
                self.embedder = get_shared_embedder()
            if self.embedder is not None:
                self._build_anchor_matrices()

        ranking: List[Dict[str, Any]] = []

        if not query or not query.strip() or not self.anchor_matrices or self.embedder is None:
            for sid, sdata in self.skills.items():
                ranking.append({
                    "skill_id": sid,
                    "title": sdata.get("title", sid),
                    "human_description": sdata.get("human_description", ""),
                    "icon_path": sdata.get("icon_path", ""),
                    "is_builtin": sdata.get("is_builtin", False),
                    "score": 0.0,
                    "is_winner": (sid == FALLBACK_SKILL)
                })
            ranking.sort(key=lambda x: (not x["is_winner"], x["title"]))
            return ranking

        q_vec = self._get_vector(query.strip())
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            for sid, sdata in self.skills.items():
                ranking.append({
                    "skill_id": sid,
                    "title": sdata.get("title", sid),
                    "human_description": sdata.get("human_description", ""),
                    "icon_path": sdata.get("icon_path", ""),
                    "is_builtin": sdata.get("is_builtin", False),
                    "score": 0.0,
                    "is_winner": (sid == FALLBACK_SKILL)
                })
            ranking.sort(key=lambda x: (not x["is_winner"], x["title"]))
            return ranking

        q_normed = q_vec / q_norm

        best_skill = FALLBACK_SKILL
        best_score = -1.0
        scores_by_id: Dict[str, float] = {}

        for sid, sdata in self.skills.items():
            matrix = self.anchor_matrices.get(sid)
            if matrix is not None and len(matrix) > 0:
                sims = np.dot(matrix, q_normed)
                score_val = float(np.max(sims))
            else:
                score_val = 0.0
            scores_by_id[sid] = score_val
            if score_val > best_score:
                best_score = score_val
                best_skill = sid

        winning_skill = best_skill if best_score >= CONFIDENCE_THRESHOLD else FALLBACK_SKILL

        for sid, sdata in self.skills.items():
            s_val = max(0.0, scores_by_id.get(sid, 0.0))
            ranking.append({
                "skill_id": sid,
                "title": sdata.get("title", sid),
                "human_description": sdata.get("human_description", ""),
                "icon_path": sdata.get("icon_path", ""),
                "is_builtin": sdata.get("is_builtin", False),
                "score": round(s_val, 4),
                "is_winner": (sid == winning_skill)
            })

        ranking.sort(key=lambda x: x["score"], reverse=True)
        return ranking
