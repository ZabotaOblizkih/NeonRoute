import subprocess
from pathlib import Path

arch_path = Path("ARCHITECTURE.md")
content = arch_path.read_text(encoding="utf-8")

# 1. Обновляем статус верификации и строки
content = content.replace("487+ тестов пройдено (100% Pass Rate)", "918+ тестов пройдено (100% Pass Rate)")
content = content.replace("services/tool_engine.py                          # [3720 строк]", "services/tool_engine.py                          # [850 строк] Диспетчер инструментов песочницы (нормализован)")
content = content.replace("services/subagent_engineer.py                     # [2176 строк]", "services/subagent_engineer.py                    # [1776 строк] Субагент «Инженер» (оркестратор TDD, Healing и Loop)")
content = content.replace("ui/models_manager_dialog.py                   # [1882 строки]", "ui/models_manager_dialog.py                   # [2329 строк] Пульт управления моделями Forge и Civitai")
content = content.replace("ui/dialogs/characters_gallery_dialog.py         # [2054 строки]", "ui/dialogs/characters_gallery_dialog.py         # [1725 строк] Двухпанельная галерея персонажей")

# 2. Убираем удаленные шимы из дерева
content = content.replace("│   ├── config_manager.py                        # [6 строк] Шим-совместимость для utils.config_manager.ConfigManager\n", "")
content = content.replace("│   ├── engineer_controller.py                   # [7 строк] Шим-совместимость для ui.controllers.engineer_controller\n", "")
content = content.replace("│   ├── lora_manager.py                          # [11 строк] Шим-совместимость для services.art_director.lora_manager\n", "")
content = content.replace("│   ├── rpg_status_widget.py                 # [24 строки] Шим реэкспорта rpg_elements\n", "")
content = content.replace("│   ├── search_bar_widget.py                 # [8 строк] Шим реэкспорта search_bar.SearchBarWidget\n", "")
content = content.replace("│   ├── models_manager_dialog.py             # [22 строки] Прокси-модуль для ui.models_manager_dialog.ModelsManagerDialog\n", "")

# 3. Добавляем новые модули в дерево
if "engineer_stream.py" not in content:
    content = content.replace(
        "│   ├── engineer_skill_router.py                 # [605 строк]",
        "│   ├── engineer_stream.py                       # [79 строк] Потоковый парсинг токенов LLM (CodeStreamExtractor, extract_clean_code)\n"
        "│   ├── engineer_review.py                       # [169 строк] Ветка статического аудита, Git Diff и чекпоинтов (ReviewPipeline)\n"
        "│   ├── engineer_parser.py                       # [371 строка] Парсер ответов LLM, извлечение инструментов и fallback-эвристики\n"
        "│   ├── engineer_skill_router.py                 # [605 строк]"
    )

if "ui_context.py" not in content:
    content = content.replace(
        "│   ├── common/                                  # Общие интерфейсные утилиты\n│   │   ├── __init__.py                          # [1 строка] Инициализация пакета ui.common\n│   │   └── icon_utils.py                        # [24 строки]",
        "│   ├── common/                                  # Общие интерфейсные утилиты\n│   │   ├── __init__.py                          # [1 строка] Инициализация пакета ui.common\n│   │   ├── icon_utils.py                        # [24 строки] Прокси-метакласс SafeFIF\n│   │   └── ui_context.py                        # [85 строк] Единый источник правды (SSOT) состояния интерфейса, сессии и бабла генерации"
    )

if "character_card_widget.py" not in content:
    content = content.replace(
        "│   │   └── gallery/                             # Миксины диалога галереи персонажей\n│   │       ├── __init__.py                      # [11 строк] Инициализация пакета ui.dialogs.gallery",
        "│   │   └── gallery/                             # Компоненты и миксины галереи персонажей\n│   │       ├── __init__.py                      # [11 строк] Инициализация пакета ui.dialogs.gallery\n│   │       ├── character_card_widget.py         # [340 строк] CardItemDelegate, render_seamless_avatar, CharacterCardWidget"
    )

# 4. Добавляем раздел про UIContext в блок декомпозиции MainWindow
if "### 3.2. Архитектура единого источника правды (`UIContext` SSOT)" not in content:
    content = content.replace(
        "## 4. Жизненный цикл выключения (Graceful Shutdown Flow)",
        "### 3.2. Архитектура единого источника правды (`UIContext` SSOT)\n"
        "Для исключения дефекта «хрупкого базового класса» (Fragile Base Class), при котором 13 миксинов хаотично мутировали `self.current_session` и `self._turn_bubble`, в систему внедрен класс `ui.common.ui_context.UIContext`. "
        "Он выступает единым источником правды для жизненного цикла диалогового хода (`begin_turn`, `finish_turn`, `turn_bubble`) и активной сессии. "
        "В классе `MainWindow` обратная совместимость обеспечивается прозрачными свойствами `@property`, делегирующими доступ в `self.ui_context` без риска регрессии в существующих вызовах.\n\n"
        "## 4. Жизненный цикл выключения (Graceful Shutdown Flow)"
    )

arch_path.write_text(content, encoding="utf-8")
print("[OK] ARCHITECTURE.md актуализирован!")

subprocess.run(["git", "add", "ARCHITECTURE.md"], check=True)
subprocess.run(["git", "commit", "-m", "docs(architecture): update specification to reflect UIContext, modular extractions, and shim removals (918+ tests)"], check=True)
print("[OK] Коммит документации зафиксирован!")
