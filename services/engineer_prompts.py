"""
services/engineer_prompts.py
System prompts, knowledge bases, and package resolution metadata for SubagentEngineer.
"""

import importlib.util
from typing import Dict, List, Optional


# Сопоставление имени импорта с реальным именем пакета в PyPI / pip
PACKAGE_PIP_MAP: Dict[str, str] = {
    "docx": "python-docx",
    "python_docx": "python-docx",
    "cv2": "opencv-python",
    "cv": "opencv-python",
    "PIL": "pillow",
    "pil": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "fitz": "pymupdf",
    "sklearn": "scikit-learn",
}

# Реестр прикладных библиотек для быстрой проверки окружения
POPULAR_LIBS: List[str] = [
    "pygame", "openpyxl", "docx", "pandas", "numpy",
    "matplotlib", "requests", "PIL", "customtkinter", "xlsxwriter"
]

CODE_CREATE_TRIGGERS: List[str] = [
    "калькулятор", "calculator", "программу", "код", "скрипт", "напиши",
    "напиши программу", "напиши код", "напиши скрипт", "создай скрипт", 
    "создай файл", "сделай скрипт", "накодь", "сгенерируй код", "закодь",
    "сохрани в файл", "сохрани как", "запиши в", "сохрани код", "сохрани программу",
    "положи в песочницу", "закинь в файл", "сохрани это", "write script", 
    "create script", "write code", "save to file", "make a script", "write python"
]


def scan_installed_libraries() -> List[str]:
    """Быстрое сканирование доступных прикладных библиотек (0 VRAM, <5мс)."""
    installed = []
    for lib in POPULAR_LIBS:
        try:
            if importlib.util.find_spec(lib) is not None:
                installed.append(lib)
        except Exception:
            pass
    return installed


def resolve_pip_package(import_name: str) -> Optional[str]:
    """Возвращает имя пакета для pip install только если модуль есть в белом списке сторонних библиотек."""
    clean_name = import_name.strip().split('.')[0]
    if clean_name in PACKAGE_PIP_MAP:
        return PACKAGE_PIP_MAP[clean_name]
    if clean_name in POPULAR_LIBS:
        return clean_name
    return None


def get_engineer_system_prompt(
    artifact_format: str = "cli",
    is_analysis: bool = False,
    injected_worker_rules: str = "",
    **kwargs
) -> str:
    """Компактный мета-промпт Инженера (Работяги) с инъекцией стандартов реализации."""
    return (
        "Ты — ведущий инженер-разработчик NeonRoute.\n"
        "Твоя задача — реализовать код файлов строго по архитектурному плану и спецификации контрактов.\n\n"
        "СТАНДАРТЫ РЕАЛИЗАЦИИ ДЛЯ ДАННОГО АРХЕТИПА:\n"
        f"{injected_worker_rules}\n\n"
        "ОБЩИЕ ПРАВИЛА КОДОГЕНЕРАЦИИ:\n"
        "1. Запрещены заглушки, pass вместо реализации и комментарии '# TODO'. Пиши полный рабочий код.\n"
        "2. Создавай файлы через отдельные блоки <<TOOL:write_file filename=\"...\">>...<</TOOL:write_file>>.\n"
        "3. Точно сохраняй сигнатуры классов, методов и аргументов из interface_spec Архитектора.\n"
        "4. Запрещено импортировать несуществующие локальные модули (например, 'from models import ...'), если ты не создаёшь эти файлы в этом же ответе отдельными блоками <<TOOL:write_file>>. Если файл один — реализуй все классы и функции внутри него."
    )


MINI_ANTIGRAVITY_SYSTEM_PROMPT: str = get_engineer_system_prompt()


def get_architect_prompt(
    available_libs: Optional[List[str]] = None,
    ws_files: Optional[List[str]] = None,
    injected_architect_rules: str = "",
    default_artifact: str = "app.py"
) -> str:
    """Компактный мета-промпт Архитектора с динамической инъекцией правил скилла."""
    base = (
        "Ты — главный системный архитектор среды NeonRoute.\n"
        "ЗАПРЕТ НА ГЕНЕРАЦИЮ ТЕСТОВ: Архитектор категорически не пишет код на Python, ассерты и фикстуры! "
        "Только концептуальное проектирование и спецификация контрактов в формате JSON.\n\n"
        "ПРАВИЛА И СТАНДАРТЫ ДЛЯ ТЕКУЩЕГО ТИПА ЗАДАЧИ:\n"
        f"{injected_architect_rules}\n\n"
        "ПРАВИЛА INTENT:\n"
        "- \"new\": создание новой программы, модуля или игры с нуля (выбирай ВСЕГДА, если создается новая сущность);\n"
        "- \"maintenance\": доработка или исправление существующего файла, прямо названного пользователем;\n"
        "- \"review\": аудит кода, анализ без изменения файлов или команды Git.\n\n"
        "Проанализируй задачу и верни СТРОГО валидный JSON без markdown-тегов:\n"
        "{\n"
        '  "task_type": "NEW_MODULE" | "INCREMENTAL_FEATURE",\n'
        '  "target_file": "целевой_файл_логики_или_ядра.py",\n'
        f'  "target_artifact": "{default_artifact}",\n'
        '  "format": "interactive_loop" | "pure_logic_tdd" | "batch_artifact" | "service_backend" | "cli_automation",\n'
        '  "intent": "new" | "maintenance" | "review",\n'
        '  "interface_spec": {\n'
        '    "classes": [{"name": "ИмяКласса", "methods": [{"name": "метод", "args": ["self"], "return_type": "None", "exceptions": [], "description": "..."}]}],\n'
        '    "functions": []\n'
        '  },\n'
        '  "plan": "краткое описание архитектуры на русском языке (до 80 слов, включая основные игровые механики, физику и взаимодействие объектов)"\n'
        "}"
    )
    addons = []
    if available_libs:
        addons.append(f"[Доступные библиотеки: {', '.join(available_libs)}]")
    if ws_files:
        addons.append(f"[Существующие файлы в рабочей директории: {', '.join(sorted(ws_files))}]")
    return base + ("\n\n" + "\n".join(addons) if addons else "")


def get_rca_prompt() -> str:
    """Системный промпт экспресс-анализа ошибок (RCA)."""
    return (
        "Ты — системный архитектор. Произошла ошибка выполнения.\n"
        "Сформулируй краткий текстовый разбор (до 100 слов).\n"
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать код и блоки ```python (только пояснительный текст):\n"
        "1. Первопричина сбоя (несоответствие типов данных, пропущенный аргумент, логика).\n"
        "2. Какие файлы нужно изменить и что конкретно в них скорректировать словами."
    )
