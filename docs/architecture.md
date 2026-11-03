# ARCHITECTURE.md
# Техническая спецификация архитектуры NeonRoute

> **Версия системы**: NeonRoute v0.9.0-beta  
> **Целевая платформа**: Windows 10/11 x64, Python 3.10+, PyQt6, QFramelessWindow, QFluentWidgets  
> **Аппаратный профиль**: 8 ГБ VRAM (NVIDIA GeForce RTX 5050 Laptop GPU / 16 ГБ RAM)  
> **Метрики кодовой базы**: 183 файла, 71 953 строки кода  
> **Статус верификации**: 918+ тестов пройдено (100% Pass Rate)

---

## 1. Архитектурный манифест и принципы проектирования

Система **NeonRoute** спроектирована как десктопный ИИ-оркестратор для рабочих станций с жестким аппаратным лимитом видеопамяти (8 ГБ VRAM). В основу кодовой базы заложены следующие архитектурные принципы:

1. **Взаимное исключение VRAM (Mutual Exclusion Principle) и топологическое бюджетирование слоев (`LayerBudgeter`)**:
   При аппаратном бюджете в 8192 МБ одновременное размещение квантованной LLM (Qwen 7B / L3-Stheno-8B: ~4.2–5.5 ГБ VRAM) и диффузионного чекпоинта Stable Diffusion (SD 1.5 / SDXL: ~3.2–4.8 ГБ VRAM) во время вычислений вызывает вытеснение памяти в медленную Shared RAM или сбой `CUDA out of memory`. Модуль `services.vram_arbiter.VRAMArbiter` гарантирует монопольный доступ к GPU только одному вычислительному ядру, а `services.layer_budgeter.LayerBudgeter` проводит предстартовый аудит топологии GPU (`PURE COMPUTE` vs `PRIMARY DISPLAY`), парсит бинарный заголовок GGUF и рассчитывает безопасное число трансформерных слоев (`-ngl`) с защитой системной ОЗУ (`Pre-flight RAM Guard`).
2. **Строгая детерминированная очередность (`Single Worker Task Queue`)**:
   Все тяжелые операции инференса (LLM-диалог, диффузионная генерация арта, синтез речи F5-TTS, выполнение инструментов песочницы, фоновая экстракция памяти и каста) ставятся в приоритетную очередь `services.task_queue.TaskQueueWorker` со строгим лимитом `max_workers = 1` и тремя уровнями приоритета (`PRIORITY_HIGH = 0`, `PRIORITY_NORMAL = 10`, `PRIORITY_LOW = 20`) с поддержкой вытесняющей отмены (`abort_tasks_by_type`).
3. **Execution-First и двухфазный Code-First TDD для разработки ПО**:
   Субагент «Инженер» (`services.subagent_engineer.SubagentEngineer`) и модуль `services.engineer_tdd.CodeFirstTDDPipeline` не полагаются на теоретическую оценку сгенерированного кода. Единственным критерием корректности является запуск в песочнице с кодом возврата `exit_code == 0`, прохождение проверки содержательности артефакта (`SubstanceGuard`), успешное выполнение набора тестов `pytest` (или Headless GUI Smoke-теста) и многоуровневых AST-проверок (`CodeValidator`).
4. **Безопасность и изоляция рантайма**:
   Все операции создания и исполнения скриптов изолированы в каталоге `workspace/projects/<session_id>/`. Системные вызовы операционной системы фильтруются на уровне AST-префлайта и рантайм аудит-хуков `sys.addaudithook` в `services.sandbox_guard.py`.
5. **ACID-персистентность состояния**:
   Все операции записи сессий, каста персонажей, реестров знаний, пользовательских скиллов и настроек выполняются атомарно через функцию `utils.file_utils.atomic_write_json` со сбросом буфера операционной системы (`fsync`) во временный файл `.tmp` и экспоненциальным ретраем при блокировках `WinError 32`.

---

## 2. Полное дерево файлов кодовой базы (Actual File Tree: 183 файла, 71 953 строки)

Ниже приведена полная и точная топология файлов проекта на основе статического анализа дампа кодовой базы v0.9.0-beta:

```
NeonRoute/
├── collect_codeMINI.py                          # [114 строк] Утилита сборки дампа кодовой базы проекта в project_code_dump.txt
├── config.py                                    # [190 строк] Единый источник правды (SSOT): базовые пути, лимиты и резервы VRAM, MODE_PROFILES, SD-настройки
├── main.py                                      # [126 строк] Точка входа: QApplication, setup_environment_and_logging (7-дневная ротация), global_exception_handler
│
├── core/                                        # Ядро и интерфейсы совместимости
│   ├── __init__.py                              # [3 строки] Инициализация пакета core
│   └── session_manager.py                       # [8 строк] Реэкспорт SessionManager, промпты инженера
│
├── services/                                    # Сервисный слой и бизнес-логика (Business Logic Layer)
│   ├── __init__.py                              # [1 строка] Инициализация пакета services
│   ├── art_director_config.py                   # [256 строк] Атомарная загрузка и сохранение data/art_director_models.json, эвристический фоллбэк
│   ├── asset_validator.py                       # [103 строки] Pre-flight аудит наличия чекпоинтов SD и весов ADetailer YOLO
│   ├── assistant_prompt_builder.py              # [610 строк] Сборщик системного промпта Эларии (UserInfo, PassiveMemory, базовые/кастомные скиллы, GALLERY_SKILL_DIRECTIVE)
│   ├── card_translator.py                       # [444 строки] Пошаговый (Slot-by-Slot) и чанковый переводчик карточек с маскированием __NR_TAG_X__ и CJK Quality Gate
│   ├── chub_client.py                           # [416 строк] REST API клиент каталога Chub.ai (поиск, очистка промо-мусора sanitize_card_description, распаковка PNG/JSON)
│   ├── civitai_service.py                       # [181 строка] Запрос параметров модели по полному AutoV2 SHA256 с Civitai API
│   ├── code_engine.py                           # [327 строк] Модульный движок: ASTSkeletonizer, ExecutionSandbox, AutoImporter (Zero-Token Healing), реэкспорт валидатора
│   ├── code_validator.py                        # [614 строк] AST-валидатор синтаксиса, Scope Guard (NameError через symtable), Stub/Reachability Guard, AST Linter импортов
│   ├── context_window_builder.py                # [942 строки] Сборщик контекста LLM, архитектура «Трёх якорей», Sliding CoT Preservation, KV-Cache In-Chat Injection
│   ├── direct_command_parser.py                 # [108 строк] Парсер CLI-параметров и флагов прямого рендера (!арт / !art)
│   ├── document_parser.py                       # [598 строк] Универсальный CPU-парсер документов, кода и дампов чатов (.pdf, .docx, .doc, .xlsx, .csv, .pptx, .fb2, .json)
│   ├── engineer_context.py                      # [354 строки] Сбор файлового контекста, AST-скелетирование (>600 строк), извлечение кода и откат песочницы
│   ├── engineer_diagnostics.py                  # [342 строки] Очистка стектрейсов clean_traceback, экспертная диагностика ошибок рантайма, срез места сбоя extract_crash_slice
│   ├── engineer_planner.py                      # [508 строк] Фаза «Архитектор»: интеграция с EngineerSkillRouter, 3-уровневая AST-локализация узлов для файлов >250 строк, IntentOverrideGuard
│   ├── engineer_prompts.py                      # [128 строк] Мета-промпты Архитектора, Инженера и RCA с динамической инъекцией правил архетипа, реестр PyPI
│   ├── engineer_stream.py                       # [79 строк] Потоковый парсинг токенов LLM (CodeStreamExtractor, extract_clean_code)
│   ├── engineer_review.py                       # [169 строк] Ветка статического аудита, Git Diff и чекпоинтов (ReviewPipeline)
│   ├── engineer_parser.py                       # [371 строка] Парсер ответов LLM, извлечение инструментов и fallback-эвристики
│   ├── engineer_skill_router.py                 # [605 строк] Векторный роутер 5 инженерных архетипов на CPU (FastEmbed) с поддержкой кастомных скиллов и иконок
│   ├── engineer_tdd.py                          # [629 строк] Двухфазный Code-First TDD пайплайн (NEW_MODULE / INCREMENTAL_FEATURE, Bootstrap Baseline, Regression Check)
│   ├── entity_mapper.py                         # [160 строк] Детерминированный конвертер кириллических сущностей в теги SD 1.5, Ambiguity Gate
│   ├── environment_doctor.py                    # [124 строки] Аудит окружения и фоновая установка пакетов через PipInstallWorker (Git, pygame, customtkinter, openpyxl, docx)
│   ├── file_generator.py                        # [318 строк] Генератор стилизованных документов Excel (.xlsx) и Word (.docx) из ответов LLM
│   ├── forge_client.py                          # [956 строк] REST клиент SD WebUI Forge (/sdapi/v1/txt2img, Hires.fix, ADetailer Guard, Watchdog 90s, автоочистка VRAM)
│   ├── forge_models.py                          # [392 строки] Реестр паспортов моделей SD (MODEL_REGISTRY, ModelCapabilities, AuthorCanon)
│   ├── git_sandbox.py                           # [333 строки] GitSandboxManager: локальный Git-репозиторий песочницы, извлечение get_working_diff и fallback на ZIP-снапшоты
│   ├── hybrid_search.py                         # [609 строк] Трехконтурный гибридный поиск: BM25Index + BuiltinDenseVectorizer/FastEmbed + RRF и Gatekeeper
│   ├── knowledge_manager.py                     # [980 строк] Менеджер базы знаний Эларии: извлечение триггеров, registry.json, search_index.json, Cross-Reference Linker
│   ├── knowledge_watcher.py                     # [201 строка] QFileSystemWatcher сателлит с дебаунсом 2.0 с для горячей инвалидации базы знаний
│   ├── layer_budgeter.py                        # [628 строк] Топологический аудитор GPU (Pure Compute vs Primary Display), парсер заголовков GGUF и калькулятор слоев NGL
│   ├── lm_studio.py                             # [735 строк] Потокобезопасный синглтон-клиент LLM (Keep-Alive, SSE-стриминг с chunk_callback, DRY/XTC сэмплеры, Structured Outputs)
│   ├── macro_engine.py                          # [64 строки] Парсер динамических макросов {{user}}, {{char}}, {{random: ...}}, {{roll: XdY+Z}} и <START>
│   ├── markdown_chunker.py                      # [400 строк] Структурный нарезчик Markdown с защитой целостности кодовых блоков ``` и шапок таблиц
│   ├── memory_db.py                             # [812 строк] Двухконтурное хранилище памяти: SQLite (WAL) + LanceDB (facts и rp_memories) на CPU через FastEmbed ONNX
│   ├── memory_extractor.py                      # [308 строк] Асинхронный экстрактор фактов о пользователе (PRIORITY_LOW, фильтр самораскрытия, JSON Schema)
│   ├── minor_safety.py                          # [117 строк] Детектор несовершеннолетних персонажей и неразрушающий фильтр безопасности промптов (MinorSafetyDetector)
│   ├── mode_coordinator.py                      # [471 строка] Конечный автомат переключения режимов (Teardown -> Switch -> Boot -> Mount) с полной изоляцией контекста
│   ├── model_router.py                          # [504 строки] Гибридный роутер моделей для Арт-директора (триггеры стиля + FastEmbed косинусное сходство + дефолт)
│   ├── patch_engine.py                          # [499 строк] Хирургический PatchApplier (Exact, Indent-Shift, Normalized, SequenceMatcher Fuzzy) и SelfHealingEngine
│   ├── process_manager.py                       # [1366 строк] Оркестратор процессов: headless llama-server.exe (с интеграцией LayerBudgeter и Port Conflict Guard), LM Studio и SD Forge
│   ├── regex_filter.py                          # [71 строка] Очистка тегов <think>, спецтокенов <|im_end|>, извлечение [IMAGE_PROMPT] и нормализация тире диалогов
│   ├── roleplay_flow_controller.py              # [743 строки] Контроллер диалогового цикла RP: инъекция Depth-0 якоря, кастомный системный промпт, кино-директивы арта
│   ├── roleplay_prompt_builder.py               # [548 строк] Сборщик промптов RP: эталонные Depth-0 якоря (build_depth_anchor), 3 ветки идентичности, 11 архетипов и 4 кино-директивы
│   ├── roleplay_state_manager.py                # [137 строк] Детерминированный парсер блоков <stats>/[STATUS] (локация, инвентарь, квесты, статы ЛитРПГ) и очистка чата
│   ├── rp_cast_extractor.py                     # [553 строки] Асинхронный летописец сцены (JSON Schema): NPC, локации, мутации, сдвиги отношений, кино-архетип scene_type
│   ├── rp_cast_manager.py                       # [978 строк] Менеджер состояния кампании (history/cast_<id>.json): сопартийцы, format_cast_data_block, переключение фокуса, TTL NPC
│   ├── rp_constants.py                          # [26 строк] Словарь ключевых слов триггеров анализа сцены (SCAN_KEYWORDS)
│   ├── rp_scene_updater.py                      # [475 строк] Транзакционное применение изменений сцены к cast_data, таргетированные companion_updates, защита реликвий
│   ├── sandbox_guard.py                         # [236 строк] Периметр безопасности рантайма: sys.addaudithook, перехват builtins.__import__, защита ФС и блокировка сети
│   ├── sandbox_manager.py                       # [924 строки] Менеджер песочницы: изоляция проектов, run_pytest, verify_single_file, commit_manual_edit, Headless GUI Smoke Harness
│   ├── sandbox_runner.py                        # [63 строки] Изолированный раннер пользовательских скриптов через runpy.run_path с активацией sandbox_guard
│   ├── search_tokenizer.py                      # [173 строки] Токенизатор для индекса BM25 со стеммингом Портера RU/EN, разбором camelCase/snake_case и декомпозицией корней
│   ├── server_config.py                         # [496 строк] 3-уровневый каскад ServerConfigResolver для поиска llama-server.exe, CUDA 12 DLLs, GGUF и mmproj
│   ├── session_manager.py                       # [815 строк] Управление сессиями диалогов, кэш сессий _sessions_cache, ACID-запись chat_<id>.json и session.json
│   ├── skill_router.py                          # [391 строка] Гибридный роутер микро-скиллов Ассистента (7 базовых + пользовательские скиллы из custom_assistant_skills.json)
│   ├── stt_service.py                           # [68 строк] Сервис голосового ввода (Faster-Whisper stub)
│   ├── subagent_engineer.py                     # [2176 строк] Автономный субагент «Инженер»: CodeStreamExtractor, ветка review/Git, интеграция TDD, Zero-Token Healing, SubstanceGuard
│   ├── task_queue.py                            # [239 строк] Последовательная приоритетная очередь задач (TaskQueueWorker, вытесняющая отмена abort_tasks_by_type)
│   ├── tavern_models.py                         # [692 строки] Модели CharacterCard и LoreEntry: автоопределение 11 архетипов, пола, POV (1/3 лицо) и Self-Healing с диска
│   ├── tavern_parser.py                         # [864 строки] Парсер карточек Tavern V1/V2/CCv3 и Chub (JSON/PNG/WebP), классификатор detect_card_archetype, извлечение лорбуков
│   ├── thought_stream_parser.py                 # [218 строк] Потоковый конечный автомат <think>...</think> с буферизацией границ чанков и circuit breaker (1800 симв.)
│   ├── token_budget_manager.py                  # [345 строк] Оценка токенов BPE, светофор карточек и двухконтурная модель контекста (ContextTierDecision)
│   ├── tool_engine.py                           # [3720 строк] Диспетчер инструментов песочницы (write_file, patch_file, replace_symbol, run_script, run_pytest, check_artifact_substance, git_checkpoint)
│   ├── tool_parser.py                           # [308 строк] Парсер тегов <<TOOL:...>> с поддержкой write_file, patch_file (SEARCH/REPLACE) и replace_symbol
│   ├── tts_service.py                           # [146 строк] Клиент синтеза речи F5-TTS на CPU (http://127.0.0.1:5010) с генерацией резервного полифонического WAV-сигнала
│   ├── user_profile_manager.py                  # [179 строк] Менеджер профилей пользователя (data/user_profiles.json) и кэша русских приветствий карточек
│   ├── vision_utils.py                          # [102 строки] Препроцессинг и LANCZOS-сжатие изображений (max_dim=768, JPEG 85) в base64 для Vision-моделей
│   ├── vram_arbiter.py                          # [91 строка] Централизованный арбитр видеопамяти (GPUOwner: NONE, LLM, DIFFUSION)
│   ├── workspace_launcher.py                    # [75 строк] Интерактивный запуск скриптов в отдельном окне cmd.exe /k и открытие папок сессии
│   │
│   └── art_director/                            # Подсистема режиссуры изображений «Арт-директор»
│       ├── __init__.py                          # [68 строк] Фасад подсистемы art_director
│       ├── contracts.py                         # [113 строк] Контракты данных: TargetSubject (ELARIA, COMPANION, SCENE_OBJECT), VisualIntent, SceneAnalysisContract
│       ├── director.py                          # [1489 строк] ArtDirector: сборка RP-контекста, микширование llm_guidance, LoRA-резолвинг, симметризация BREAK и защита 8GB VRAM
│       ├── geometry.py                          # [100 строк] Разрешения SD 1.5 и SDXL по 6 архетипам кадра (fullbody, solo, closeup, pair, scenery, cinematic)
│       ├── legacy_helpers.py                    # [117 строк] Вспомогательные функции обратной совместимости
│       ├── lora_manager.py                      # [279 строк] Чтение бинарных заголовков .safetensors на CPU (0 MB VRAM), извлечение топ-3 триггеров и реестр lora_registry.json
│       ├── plan.py                              # [144 строки] Модель GenerationPlan и сериализатор to_forge_kwargs() с формированием слотов ADetailer (лицо + руки)
│       └── prompting.py                         # [141 строка] Константа ELARIA_VISUAL_DNA, перехват отрицаний extract_negations_from_prompt и сборка негатива
│
├── ui/                                          # Слой графического интерфейса (Presentation Layer)
│   ├── __init__.py                              # [1 строка] Инициализация пакета ui
│   ├── engineer_skills_dialog.py                # [998 строк] Центр управления знаниями инженера (Skills Hub): редактор архетипов и тестовый стенд роутинга FastEmbed
│   ├── log_console.py                           # [186 строк] Live-консоль логов текущей сессии (QtLogHandler, кольцевой буфер на 2000 записей)
│   ├── main_window.py                           # [2039 строк] Главное окно MainWindow (3-колоночный сплиттер с WorkspaceInspectorWidget, дебаунс смены моделей, автотюн KV)
│   ├── models_manager_dialog.py                 # [1882 строки] Пульт управления моделями Forge, параметрами пайплайна (Civitai AutoFill) и тестированием роутера FastEmbed
│   ├── styles.py                                # [311 строк] Глобальная таблица стилей NEON_THEME_QSS (Cyberpunk Glassmorphism)
│   │
│   ├── builders/                                # Билдеры интерфейса
│   │   ├── __init__.py                          # [1 строка] Инициализация пакета ui.builders
│   │   ├── chat_bubble_builder.py               # [563 строки] ChatBubbleBuilderMixin: рендеринг баблов (включая EngineerTaskCardWidget), ленивая подгрузка истории
│   │   └── menu_builder.py                      # [345 строк] MenuBuilder и ClickOnlyMenuBar: верхнее меню на базе RoundMenu (включая «Знания инженера» и «Настройки движка LLM»)
│   │
│   ├── common/                                  # Общие интерфейсные утилиты
│   │   ├── __init__.py                          # [1 строка] Инициализация пакета ui.common
│   │   ├── icon_utils.py                        # [24 строки] Прокси-метакласс SafeFIF
│   │   └── ui_context.py                        # [85 строк] Единый источник правды (SSOT) состояния интерфейса, сессии и бабла генерации Прокси-метакласс SafeFIF для безопасного разрешения иконок FluentIcon
│   │
│   ├── components/                              # Графические компоненты и виджеты
│   │   ├── __init__.py                          # [1 строка] Инициализация пакета ui.components
│   │   ├── assistant_chat_bubble.py             # [456 строк] AssistantChatMessageWidget и CyberpunkArtPlaceholderWidget (анимированный 28 FPS видоискатель синтеза кадра)
│   │   ├── audio_player.py                      # [126 строк] AudioPlayerWidget: встроенный плеер QMediaPlayer для воспроизведения озвучки F5-TTS
│   │   ├── chat_background.py                   # [123 строки] ChatBackgroundViewport: отрисовка обоев чата с кэшированием масштабированного QPixmap в resizeEvent
│   │   ├── chat_bubble.py                       # [1399 строк] Базовый ChatMessageWidget, SelectableTextBrowser, квантование ширины бабла (шаг 16px)
│   │   ├── chat_header_widget.py                # [254 строки] ChatHeaderWidget и HeaderAvatarWidget: шапка чата, меню фона сцены, селектор Forge, кнопка инспектора песочницы (Ctrl+B)
│   │   ├── chat_input_panel.py                  # [244 строки] ChatInputPanel: нижняя парящая капсула ввода, панель чипов вложений, кнопки Attach/Mic/Emoji
│   │   ├── chat_list_item.py                    # [200 строк] ChatSessionItemWidget: элемент списка сессий с бейджем режима (InfoBadge) и контекстным меню выбора персоны
│   │   ├── chat_message_widget.py               # [181 строка] KnowledgeHUDWidget и KnowledgeSourceChip: неоновые микро-чипы источников RAG с превью цитат в тултипе
│   │   ├── chat_stream_widget.py                # [201 строка] ChatStreamWidget: SmoothScrollArea, масштабирование шрифта Ctrl+Колесо (11–22 pt)
│   │   ├── emoji_picker.py                      # [97 строк] EmojiPickerMenu: категоризированная палитра эмодзи
│   │   ├── file_card.py                         # [153 строки] FileCardWidget: интерактивная карточка созданного документа (Excel/Word/Image/Audio)
│   │   ├── input_box.py                         # [349 строк] ChatInputBox: поле ввода с авторасширением (42–250px), вставкой скриншотов Ctrl+V и кнопкой-трансформером
│   │   ├── reminder_toast.py                    # [63 строки] ReminderToastWidget: всплывающее уведомление напоминания
│   │   ├── rpg_elements.py                      # [513 строк] Парсер RPGContentParser и виджеты: RPGStatusWidget, RPGThoughtWidget, RPGChoicesWidget, блоки <check>/<terminal>/<sms>
│   │   │   ├── scroll_button.py                     # [136 строк] FloatingScrollDownButton: круглая акриловая кнопка прокрутки ко дну чата
│   │   ├── search_bar.py                        # [165 строк] SearchBarWidget: панель поиска по тексту диалога с навигацией по совпадениям
│   │   │   ├── sidebar_widget.py                    # [263 строки] SidebarWidget: левая боковая панель с карточкой компаньона, кнопкой «Новый чат», списком диалогов и папками
│   │   ├── status_bar_widget.py                 # [313 строк] MainStatusBar, VramLabel, RamLabel, ServiceStatusBadge: телеметрия железа и управление серверами
│   │   ├── status_pill.py                       # [51 строка] StatusPill: компактный LED-индикатор статуса сервисов
│   │   ├── step_progress_button.py              # [94 строки] StepProgressButton и CardProgressBridge: кнопка с кольцевым и линейным прогрессом перевода слотов карточки
│   │   ├── thought_spoiler_widget.py            # [257 строк] ThoughtSpoilerWidget: сворачиваемый блок «Ход мыслей» с очисткой служебных тегов clean_thought_chunk
│   │   ├── typing_loader.py                     # [106 строк] TypingLoaderWidget: анимированный индикатор генерации ответа
│   │   ├── workspace_inspector.py               # [751 строка] WorkspaceInspectorWidget: правая панель песочницы (дерево файлов, редактор с подсветкой Python, стриминг кода, Git-коммит, Smoke-тест F5)
│   │   │
│   │   ├── bubbles/                             # Модульные карточки и модальные просмотрщики
│   │   │   ├── __init__.py                      # [29 строк] Фасад пакета bubbles
│   │   │   ├── avatar_renderer.py               # [183 строки] HiDPI 3x рендеринг круглых аватаров и LRU-кэш скругленных превью артов (_IMAGE_PREVIEW_CACHE)
│   │   │   ├── image_preview_dialog.py          # [126 строк] Полноразмерный модальный просмотрщик изображений (ImagePreviewDialog)
│   │   │   └── workspace_cards.py               # [457 строк] WorkspaceFileCardWidget, ConsoleOutputSpoilerWidget и GenerationInfoAccordionWidget
│   │   │
│   │   └── rp/                                  # Ролевые компоненты отображения
│   │       ├── __init__.py                      # [6 строк] Инициализация пакета rp
│   │       └── rp_chat_bubble.py                # [246 строк] RPChatMessageWidget: специализированный бабл с парсингом RPG-разметки
│   │
│   ├── controllers/                             # Контроллеры и миксины MainWindow
│   │   ├── __init__.py                          # [1 строка] Инициализация пакета ui.controllers
│   │   ├── assistant_flow_controller.py         # [1150 строк] AssistantFlowControllerMixin: двухконтурный бюджет 14K, RAG-инъекция, HistorySanitizer, двухпроходный арт-протокол [GALERY]
│   │   ├── avatar_controller.py                 # [787 строк] AvatarControllerMixin: синхронизация аватаров, меню выбора скиллов Инженера/Ассистента, вызов AnchorViewer и PromptViewer
│   │   ├── chat_flow_controller.py              # [1567 строк] ChatFlowControllerMixin: диспетчеризация ввода, парсинг напоминаний dateparser, батчинг стрима (28 FPS), Anti-Loop Guard
│   │   ├── dialog_controller.py                 # [418 строк] DialogControllerMixin: управление модальными окнами настроек, базы знаний, скиллов инженера и экспорта
│   │   ├── engineer_controller.py               # [703 строки] EngineerControllerMixin: маршрутизация задач в SubagentEngineer, синхронизация статусов с WorkspaceInspectorWidget
│   │   ├── hardware_engine.py                   # [865 строк] HardwareEngineMixin: управление процессами LLM/Forge, автотюн KV-кэша _perform_hardware_kv_autotune, пайплайны генерации арта
│   │   ├── header_controller.py                 # [129 строк] HeaderStateController: реактивный контроллер состояния шапки чата и прогресса этапов Forge
│   │   ├── reminder_controller.py               # [98 строк] ReminderController: 4-секундный пуллинг SQLite напоминаний, отправка в системный трей, Windows Toast и чат
│   │   ├── roleplay_controller.py               # [492 строки] RoleplayControllerMixin: управление фоном сцены, сводкой кампании и пошаговой локализацией карточек
│   │   ├── search_controller.py                 # [77 строк] ChatSearchMixin: поиск и навигация по сообщениям активного диалога
│   │   ├── session_controller.py                # [138 строк] SessionControllerMixin: создание, переименование и безопасное удаление сессий
│   │   ├── task_lifecycle_controller.py         # [467 строк] TaskLifecycleMixin: маршрутизация задач в очередь, перехват команд памяти и обработка ошибок (ModelNotFoundError)
│   │   └── window_state_controller.py           # [150 строк] WindowStateMixin: кэширование фона главного окна и полноэкранный режим (F11)
│   │
│   ├── dialogs/                                 # Модальные диалоговые окна приложения
│   │   ├── __init__.py                          # [25 строк] Фасад диалоговых окон
│   │   ├── about_dialog.py                      # [146 строк] О программе NeonRoute
│   │   ├── anchor_viewer_dialog.py              # [145 строк] AnchorViewerDialog: просмотр и кастомизация Depth-0 якоря сцены для текущей РП-сессии
│   │   ├── avatar_crop_dialog.py                # [287 строк] Интерактивное кадрирование аватара 512x512 с панорамированием и зумом (CropCanvas)
│   │   ├── avatar_lightbox_dialog.py            # [61 строка] Полноэкранный LightBox просмотра аватара
│   │   ├── background_dialog.py                 # [178 строк] Настройка обоев рабочего пространства и уровня затемнения (0–90%)
│   │   ├── campaign_info_dialog.py              # [1034 строки] Дашборд кампании (5 вкладок: Память/Статус, Инвентарь, Сценарий/Лор, Каст/NPC, Локация/Атмосфера)
│   │   ├── campaign_subdialogs.py               # [397 строк] Вспомогательные диалоги кампании: EntityConditionDialog, CastEditDialog, SelectLorebookDialog
│   │   ├── card_visual_setup_dialog.py          # [158 строк] Быстрая настройка Visual DNA и привязка LoRA после импорта карточки
│   │   ├── characters_gallery_dialog.py         # [2054 строки] Двухпанельная галерея персонажей с бесшовными аватарами, светофором токенов и редактором секций
│   │   ├── chub_catalog_dialog.py               # [575 строк] Сетка-каталог Chub.ai с многопоточной подгрузкой постеров и фильтрацией по русскому языку/токенам
│   │   ├── civitai_catalog_dialog.py            # [1066 строк] Браузер каталога Civitai с потоковой загрузкой .safetensors, защитой от SSRF и проверкой SHA-256
│   │   ├── civitai_lora_dialog.py               # [595 строк] Поиск и установка персонажных LoRA с проверкой совместимости архитектур (SD 1.5 vs SDXL/Pony)
│   │   ├── clean_cache_dialog.py                # [612 строк] Фоновый анализатор и очиститель кэша Forge, логов, превью и таблиц LanceDB
│   │   ├── dependency_dialog.py                 # [181 строка] Диалог проверки и установки опциональных зависимостей режима Инженер
│   │   ├── engineer_settings_dialog.py          # [352 строки] ModeModelSettingsDialog: назначение постоянных LLM-моделей для режимов Ассистент, РП Чат и Инженер
│   │   ├── help_dialog.py                       # [208 строк] Справочник возможностей и горячих клавиш NeonRoute
│   │   ├── knowledge_manager_dialog.py          # [997 строк] 3-вкладочный центр «Знания Эларии»: База знаний (документы), Скиллы Эларии и редактор Системного промпта
│   │   ├── llm_engine_settings_dialog.py        # [351 строка] LLMEngineSettingsDialog: настройка квантования KV-кэша (Q8_0/Q4_0/FP16) и Flash Attention с аудитом GPU
│   │   ├── local_lora_dialog.py                 # [274 строки] Выбор локальных весов LoRA из папок Forge с настройкой веса и триггеров
│   │   ├── media_gallery_dialog.py              # [207 строк] Сетка всех сгенерированных изображений чата или всех сессий
│   │   ├── model_sampling_dialog.py             # [803 строки] Тонкая настройка сэмплинга (Temperature, Min-P, Top-P, Repeat Penalty, DRY, XTC, Max Tokens, DM Mode)
│   │   │   ├── new_chat_dialog.py                   # [202 строки] Всплывающий селектор режима при создании нового диалога (РП Чат, Ассистент, Инженер)
│   │   ├── prompt_viewer_dialog.py              # [181 строка] PromptViewerDialog: инспекция и редактирование системного промпта карточки для текущей РП-сессии
│   │   ├── shutdown_dialog.py                   # [83 строки] Индикатор прогресса выгрузки VRAM и сохранения состояния при выходе
│   │   ├── user_settings_dialog.py              # [1005 строк] Настройки профиля пользователя для Ассистента и менеджер игровых персон для Ролевого чата
│   │   │
│   │   └── gallery/                             # Компоненты и миксины галереи персонажей
│   │       ├── __init__.py                      # [11 строк] Инициализация пакета ui.dialogs.gallery
│   │       ├── character_card_widget.py         # [340 строк] CardItemDelegate, render_seamless_avatar, CharacterCardWidget
│   │       ├── dna_party_mixin.py               # [663 строки] Извлечение Scenery DNA, Visual DNA, распознавание состава отряда и прямая генерация арта из галереи
│   │       └── translation_mixin.py             # [297 строк] Конвейер перевода отдельных полей и всей карточки персонажа с переключением RU/EN
│   │
│   ├── widgets/                                 # Специализированные виджеты задач и сообщений
│   │   ├── __init__.py                          # [7 строк] Инициализация пакета ui.widgets
│   │   ├── chat_message.py                      # [18 строк] Реэкспорт ChatMessageWidget, карточек песочницы и EngineerTaskCardWidget
│   │   └── engineer_task_card.py                # [1122 строки] EngineerTaskCardWidget: монолитная консоль задачи инженера (Activity Header, SmoothSpoiler, Execution Stepper)
│   │
│   └── workers/                                 # Фоновые потоки QThread
│       ├── __init__.py                          # [1 строка] Инициализация пакета ui.workers
│       ├── model_discovery_worker.py            # [52 строки] Неблокирующее обнаружение моделей LM Studio/GGUF и чекпоинтов Forge при старте
│       ├── shutdown_worker.py                   # [63 строки] Воркер штатного завершения работы с 8-секундным сторожевым таймером (Watchdog)
│       └── tool_execution_worker.py             # [184 строки] Асинхронный исполнитель цепочек вызовов инструментов (write_file -> run_script)
│
├── utils/                                       # Вспомогательные системные утилиты
│   ├── __init__.py                              # [1 строка] Инициализация пакета utils
│   ├── config_manager.py                        # [128 строк] Управление конфигурацией config.json с рекурсивным слиянием дефолтов
│   ├── file_utils.py                            # [39 строк] Атомарная запись JSON (atomic_write_json) с уникальным .tmp, fsync и защитой от WinError 32
│   ├── health_checker.py                        # [100 строк] Быстрый асинхронный сокет-монитор портов 1234, 7860 и 5010 с дедупликацией сигналов
│   └── system_monitor.py                        # [184 строки] Мониторинг VRAM/RAM/Temp через pynvml (с fallback на прямой вызов nvidia-smi) и psutil
│
└── workspace/                                   # Изолированная песочница разработки
    └── calculator.py                            # [5 строк] Базовый демонстрационный модуль песочницы
```

---

## 3. Декомпозиция MainWindow, 3-колоночный сплиттер и иерархия миксинов

Главное окно приложения `MainWindow` спроектировано на базе фреймворка `qframelesswindow.FramelessMainWindow` с композицией из 13 специализированных миксинов, использует 3-колоночный `QSplitter` (`SidebarWidget` | `MainContent` | `WorkspaceInspectorWidget`) и координируется конечным автоматом `ModeCoordinator` (`services/mode_coordinator.py`):

```mermaid
classDiagram
    class FramelessMainWindow
    class WindowStateMixin {
        +_update_background_cache()
        +paintEvent()
        +_toggle_fullscreen()
    }
    class DialogControllerMixin {
        +_open_user_settings_dialog()
        +_open_model_sampling_dialog()
        +_open_clean_cache_dialog()
        +_open_knowledge_manager_dialog()
        +_open_engineer_skills_dialog()
    }
    class TaskLifecycleMixin {
        +_enqueue_translation_task()
        +_route_and_enqueue()
        +_on_task_started()
        +_on_task_finished()
        +_on_task_failed()
    }
    class ChatSearchMixin {
        +_show_search_bar()
        +_on_search_query_changed()
        +_search_next()
    }
    class AvatarControllerMixin {
        +_get_current_avatar_path()
        +_update_expression_avatar()
        +_on_select_engineer_skill()
        +_on_select_assistant_skill()
        +_open_anchor_dialog()
        +_open_prompts_dialog()
    }
    class SessionControllerMixin {
        +_on_new_chat_clicked()
        +_save_current_session()
        +_on_rename_session_requested()
        +_on_delete_session_requested()
    }
    class ChatBubbleBuilderMixin {
        +_render_stored_message()
        +_append_message()
        +_load_previous_messages_chunk()
        +_clear_chat_widgets()
    }
    class ChatFlowControllerMixin {
        +_on_send_clicked()
        +_sync_turn()
        +_on_token_streamed()
        +_flush_stream_buffer()
        +_on_llm_response_received()
        +_detect_repetition_loop()
    }
    class AssistantFlowControllerMixin {
        +_enqueue_assistant_llm_task()
        +_handle_assistant_llm_response()
        +_enqueue_gallery_art_pass()
        +_handle_assistant_art_completed()
        +process_assistant_attachments()
    }
    class RoleplayControllerMixin {
        +_apply_chat_background()
        +_open_campaign_info_dialog()
        +_translate_character_card_task()
        +_restore_roleplay_character_from_session()
    }
    class RoleplayFlowControllerMixin {
        +_enqueue_roleplay_llm_task()
        +_handle_roleplay_llm_response()
        +_generate_ephemeral_rp_art_prompt()
        +_on_scene_visual_intent_ready()
    }
    class HardwareEngineMixin {
        +_on_health_updated()
        +_on_hardware_stats()
        +_perform_hardware_kv_autotune()
        +_on_scene_art_requested()
        +_reload_active_lms_model_async()
    }
    class EngineerControllerMixin {
        +is_engineer_mode_active()
        +_open_mode_model_settings_dialog()
        +_dispatch_ephemeral_doc_worker()
        +_handle_tool_call_flow()
        +_handle_engineer_task_finished()
    }
    class MainWindow {
        +lm_client: LMStudioClient
        +forge_client: ForgeClient
        +task_queue: TaskQueueWorker
        +mode_coordinator: ModeCoordinator
        +workspace_inspector: WorkspaceInspectorWidget
        +header_controller: HeaderStateController
        +reminder_controller: ReminderController
        +knowledge_watcher: KnowledgeWatcher
        +toggle_workspace_inspector()
        +closeEvent()
    }

    FramelessMainWindow <|-- MainWindow
    WindowStateMixin <|-- MainWindow
    DialogControllerMixin <|-- MainWindow
    TaskLifecycleMixin <|-- MainWindow
    ChatSearchMixin <|-- MainWindow
    AvatarControllerMixin <|-- MainWindow
    SessionControllerMixin <|-- MainWindow
    ChatBubbleBuilderMixin <|-- MainWindow
    ChatFlowControllerMixin <|-- MainWindow
    AssistantFlowControllerMixin <|-- MainWindow
    RoleplayControllerMixin <|-- MainWindow
    RoleplayFlowControllerMixin <|-- MainWindow
    HardwareEngineMixin <|-- MainWindow
    EngineerControllerMixin <|-- MainWindow
```

### 3.1. Архитектура исключения конфликтов метаклассов и изоляции режимов (`ModeCoordinator`)
- Все 13 миксинов контроллеров (`*Mixin`) являются «чистыми» классами Python без прямого наследования от `QObject` или `QWidget`. Единственной точкой входа в иерархию классов Qt выступает сам класс `MainWindow(FramelessMainWindow, ...)`, что предотвращает коллизии метаклассов `sip.wrappertype`.
- Переключение между тремя режимами (`AppMode.ASSISTANT`, `AppMode.ROLEPLAY`, `AppMode.ENGINEER`) управляется конечным автоматом `services.mode_coordinator.ModeCoordinator` по четырехфазному циклу **Teardown -> Switch -> Boot -> Mount**:
  1. **Teardown**: принудительная остановка активной генерации, сохранение уходящей сессии на диск, остановка фоновых воркеров (`_tool_worker`, `_ephemeral_doc_worker`, `_cast_extractor_worker`), отмена таймеров возврата LLM и сборка мусора `gc.collect()`.
  2. **Switch**: фиксация целевого `AppMode` и очистка оперативного списка `chat_history`.
  3. **Boot**: конфигурация UI-компонентов под целевой режим и динамическая перебалансировка 3-колоночного сплиттера (`_sync_splitter_layout`: `[260, 560, 380]` с открытым `WorkspaceInspectorWidget` для режима «Инженер» и `[280, 920, 0]` для «Ассистента» и «Ролевого чата»).
  4. **Mount**: привязка изолированной папки песочницы `SandboxManager.set_active_session_id(sess_id)`, восстановление карточки персонажа, планирование переключения выделенной модели режима с дебаунсом 400 мс (`_model_switch_timer`) и пакетная отрисовка сообщений с пагинацией по 35 реплик.

---

### 3.2. Архитектура единого источника правды (`UIContext` SSOT)
Для исключения дефекта «хрупкого базового класса» (Fragile Base Class), при котором 13 миксинов хаотично мутировали `self.current_session` и `self._turn_bubble`, в систему внедрен класс `ui.common.ui_context.UIContext`. Он выступает единым источником правды для жизненного цикла диалогового хода (`begin_turn`, `finish_turn`, `turn_bubble`) и активной сессии. В классе `MainWindow` обратная совместимость обеспечивается прозрачными свойствами `@property`, делегирующими доступ в `self.ui_context` без риска регрессии в существующих вызовах.

## 4. Жизненный цикл выключения (Graceful Shutdown Flow)

Процедура штатного закрытия приложения (`ui/main_window.py` -> `ui/workers/shutdown_worker.py`) гарантирует освобождение ресурсов видеокарты и предотвращает появление процессов-сирот `llama-server.exe` и `SD WebUI Forge` в операционной системе Windows:

```mermaid
sequenceDiagram
    autonumber
    actor User as Пользователь
    participant MW as MainWindow
    participant Dialog as ShutdownProgressDialog
    participant SW as ShutdownWorker (QThread)
    participant Watchdog as Аварийный Watchdog (Thread)
    participant Storage as Сессии и LanceDB
    participant PM as ProcessManager
    participant Forge as SD WebUI Forge (PID)
    participant LLM as llama-server.exe / LM Studio

    User->>MW: Закрытие окна (closeEvent)
    MW->>MW: event.ignore(), остановка ReminderController и KnowledgeWatcher, self.hide()
    MW->>Dialog: .show() (Glassmorphic индикатор)
    MW->>SW: .start()
    SW->>Watchdog: threading.Thread(target=_watchdog, daemon=True).start(8.0s)
    
    rect rgb(20, 30, 45)
    Note over SW,Storage: Шаг 1: Сохранение состояния и оптимизация LanceDB
    SW->>MW: _save_current_session()
    SW->>Storage: ConfigManager.save_config()
    SW->>Storage: memory_db.close() (compact_files, cleanup_old_versions, optimize)
    end

    rect rgb(30, 40, 25)
    Note over SW,MW: Шаг 2: Остановка фоновых потоков мониторинга
    SW->>MW: health_worker.stop() (wait 1000ms)
    SW->>MW: system_monitor.stop() (wait 1000ms)
    SW->>MW: task_queue.stop() (wait 1000ms)
    end

    rect rgb(45, 20, 30)
    Note over SW,PM: Шаг 3: Аппаратная зачистка VRAM и дерева процессов
    SW->>PM: shutdown_all()
    PM->>Forge: 1. taskkill /F /T /PID <forge_pid> (timeout=3s)
    PM->>Forge: 2. Зачистка остаточных процессов launch.py/webui через psutil
    PM->>LLM: 3. _kill_llama_server_instance() (taskkill /F /T для llama-server.exe)
    PM->>LLM: 4. POST /api/v0/models/unload + lms unload --all (timeout=5s)
    end

    SW-->>MW: Сигнал finished
    MW->>Dialog: .close(), .deleteLater()
    MW->>MW: _shutdown_completed = True, QApplication.quit()
    
    opt Срабатывание таймера при зависании
        Watchdog->>Watchdog: Истекло 8.0 секунд
        Watchdog-->>MW: os._exit(0) (Принудительное завершение)
    end
```

---

## 5. Модель передачи VRAM между LLM и SD Forge (`VRAMArbiter`) и бюджетирование слоев (`LayerBudgeter`)

Монопольный доступ к ресурсам GPU реализуется через синглтон `services.vram_arbiter.VRAMArbiter` в связке с `LayerBudgeter`, `ProcessManager` и `ForgeClient`:

```mermaid
stateDiagram-v2
    [*] --> GPU_IDLE: Инициализация системы + Аппаратный автотюн KV (Turing+ -> Q8_0 + FA)

    state GPU_IDLE {
        [*] --> NONE
        NONE: GPU свободен / Фоновый мониторинг
    }

    GPU_IDLE --> LLM_ACTIVE: Вход текстовой задачи (LLM / Translation / Engineer)
    
    state LLM_ACTIVE {
        [*] --> BUDGET_LAYERS
        BUDGET_LAYERS: LayerBudgeter.calculate_layer_offload (Топология GPU, парсинг GGUF, расчет NGL и RAM Guard)
        LOAD_LLM: Запуск llama-server.exe (-ngl N, -ctk q8_0, -fa on) или lms load
        HOLD_LLM: Инференс Qwen / L3-Stheno (4.2-5.5 ГБ VRAM)
        BUDGET_LAYERS --> LOAD_LLM
        LOAD_LLM --> HOLD_LLM
    }

    LLM_ACTIVE --> TRANSITION_TO_DIFFUSION: Запрос генерации арта (после генерации тегов LLM, если нужен перевод)

    state TRANSITION_TO_DIFFUSION {
        [*] --> UNLOAD_LLM
        UNLOAD_LLM: evict_llm_for_forge_sync() (taskkill llama-server / lms unload --all)
        WAIT_WDDM: Динамический опрос NVML (Ожидание Free VRAM >= 3800 МБ, до 4.0 с)
        UNLOAD_LLM --> WAIT_WDDM
    }

    TRANSITION_TO_DIFFUSION --> DIFFUSION_ACTIVE: Память очищена
    
    state DIFFUSION_ACTIVE {
        [*] --> LOAD_SD
        LOAD_SD: Загрузка чекпоинта Forge в VRAM (override_settings)
        RENDER_TXT2IMG: Базовый проход (steps=10-28, DPM++ 2M / SDE Karras)
        RENDER_HIRES: Hires.fix апскейл (VAE Tiling активен, отключен для SDXL >= 640K px)
        RENDER_ADETAILER: Детекция лиц и рук YOLO (если разрешено ADetailer Guard)
        LOAD_SD --> RENDER_TXT2IMG
        RENDER_TXT2IMG --> RENDER_HIRES
        RENDER_HIRES --> RENDER_ADETAILER
    }

    DIFFUSION_ACTIVE --> TRANSITION_TO_LLM: Рендер завершен (блок finally в ForgeClient.text_to_image)

    state TRANSITION_TO_LLM {
        [*] --> UNLOAD_SD
        UNLOAD_SD: POST /sdapi/v1/unload-checkpoint (Сброс чекпоинта в системную RAM)
        PURGE_CUDA: POST /sdapi/v1/free-vram (Очистка кэша PyTorch CUDA)
        TIMER_DELAY: Отложенный тихий возврат LLM (QTimer 1.2s при пустой очереди артов)
        UNLOAD_SD --> PURGE_CUDA
        PURGE_CUDA --> TIMER_DELAY
    }

    TRANSITION_TO_LLM --> LLM_ACTIVE: Автовозврат активной модели режима в VRAM
```

### 5.1. Формула баланса VRAM и топологический аудит (`services/layer_budgeter.py`)
Перед каждым запуском `llama-server.exe` модуль `LayerBudgeter.calculate_layer_offload` выполняет строгий математический расчет:
1. **Детекция топологии GPU (`detect_gpu_topology`)**:
   - Если `nvmlDeviceGetDisplayActive(handle) == False` и на видеокарте отсутствуют графические процессы `dwm.exe` / `csrss.exe` — видеокарта работает в режиме **`PURE COMPUTE`** (монитор подключен к встроенной графике iGPU), резерв ОС составляет **`OS_RESERVE_PURE_COMPUTE_MB = 150` МБ**.
   - Иначе фиксируется режим **`PRIMARY DISPLAY`** с резервом **`OS_RESERVE_PRIMARY_DISPLAY_MB = 700` МБ**.
2. **Формула доступной видеопамяти (`Usable_VRAM`)**:
   $$\text{Usable\_VRAM} = \text{Total\_VRAM} - \text{OS\_Reserve} - \text{FORGE\_VRAM\_RESERVE\_MB (450)} - \text{SAFETY\_MARGIN\_VRAM\_MB (150)} - \text{KV\_Cache\_MB}$$
   Где `KV_Cache_MB` рассчитывается с запасом 15% на накладные расходы CUDA: `~64 КБ/токен` для `q8_0`, `~36 КБ/токен` для `q4_0`, `~128 КБ/токен` для `f16`.
3. **Анатомия весов GGUF и гистерезис слоев**:
   - Неделимый каркас модели (`token_embd`, `output.weight`, `RMSNorm`) принимается за `Base_Overhead = Model_Size_MB * 0.20`, а вес трансформерных блоков — `Blocks_Weight = Model_Size_MB * 0.80`.
   - Если разница между новым `ngl` и текущим запущенным `current_running_ngl` не превышает **`HYSTERESIS_LAYER_DELTA = 3`**, сохраняется текущее значение во избежание лишних перезапусков.
   - **Pre-flight RAM Guard**: при частичном оффлоаде (`ngl < total_layers`) проверяет наличие свободной системной ОЗУ (`available_ram_mb >= cpu_weights_mb + headroom_mb`, где буфер ОС равен 1024–1500 МБ).

---

## 6. Субагент «Инженер», роутер архетипов и двухфазный Code-First TDD (`subagent_engineer.py`, `engineer_tdd.py`)

Субагент разработки `services.subagent_engineer.SubagentEngineer` работает в режиме нулевого загрязнения диалоговой памяти (Zero-State Persistence для LanceDB) и координирует два специализированных контура исполнения:

```mermaid
flowchart TD
    Start([Постановка задачи]) --> SkillRoute[EngineerSkillRouter.route: Векторный выбор из 5 архетипов FastEmbed CPU]
    SkillRoute --> ArchPhase[Фаза 1: Архитектор: EngineerPlanner.plan_architecture -> JSON interface_spec + intent]
    ArchPhase --> IntentCheck{Проверка intent и архетипа}

    IntentCheck -->|intent == 'review'| ReviewBranch[Ветка Review / Git: анализ git diff или фиксация коммита без перезаписи кода]
    IntentCheck -->|skill_id == 'pure_logic_tdd' & interface_spec| TDDBranch[CodeFirstTDDPipeline: NEW_MODULE или INCREMENTAL_FEATURE]
    IntentCheck -->|interactive_loop / batch_artifact / service_backend / cli_automation| LoopBranch[Автономный цикл Self-Healing до 15 ходов]

    subgraph TDD [Двухфазный Code-First TDD Пайплайн]
        TDDBranch --> TDDStep1[Шаг 1: Генерация кода модуля по interface_spec + sanitize_code от самоимпорта]
        TDDStep1 --> TDDStep2[Шаг 2: Генерация или инкрементальное дописывание pytest тестов со строгим промпт-гардом]
        TDDStep2 --> TDDHeal[Шаг 3: Двустороннее исцеление через run_pytest до 15 попыток: нечётные — код, чётные — тесты]
    end

    subgraph SelfHealing [Цикл Self-Healing с поддержкой крупных файлов > 250 строк]
        LoopBranch --> StreamUI[CodeStreamExtractor -> живая трансляция кода в WorkspaceInspectorWidget]
        StreamUI --> ToolParse[Парсинг write_file / patch_file / replace_symbol + AST Linter]
        ToolParse --> CodeVal[CodeValidator: Синтаксис, AST Safety, Scope Guard, Stub/Reachability, Integrity Guard]
        CodeVal -->|Успешно| RunExec[SandboxManager.run_script: Headless GUI Smoke Harness / sandbox_runner]
        RunExec -->|NameError стандартной библиотеки| AutoImp[AutoImporter: Zero-Token Healing тихая инъекция импорта и перезапуск]
        AutoImp --> RunExec
        RunExec -->|exit_code == 0| SubstGuard[ToolEngine.check_artifact_substance: SubstanceGuard >= 350 байт и наличие классов/функций]
        RunExec -->|exit_code != 0| GitRB[Git откат + clean_traceback + extract_crash_slice + RCA анализ]
        GitRB --> StreamUI
    end

    ReviewBranch --> Done([Вывод результата в EngineerTaskCardWidget])
    TDDHeal --> Done
    SubstGuard --> Done
```

### Ключевые архитектурные механизмы Инженера:
1. **Векторная маршрутизация инженерных архетипов (`services/engineer_skill_router.py`)**:
   Роутер на CPU через `FastEmbed` сопоставляет запрос разработчика с 5 встроенными архетипами (`interactive_loop`, `pure_logic_tdd`, `batch_artifact`, `service_backend`, `cli_automation`) и пользовательскими скиллами из `data/custom_engineer_skills.json`, инжектируя профильные `architect_rules` и `worker_rules`.
2. **Трехуровневая AST-локализация и `replace_symbol` для крупных файлов (`> 250` строк)**:
   При доработке существующего файла объемом свыше 250 строк `EngineerPlanner` запрещает полную перезапись файла и формирует 3-уровневый срез: Уровень 1 (Импорты модуля), Уровень 2 (Сигнатурный каркас родительского класса), Уровень 3 (Целевой узел с окружением `±10` строк). Модель вносит правку точечным инструментом `<<TOOL:replace_symbol filename="..." name="...">>`.
3. **Zero-Token Healing (`AutoImporter` в `services/code_engine.py`)**:
   Если скрипт падает с `NameError` из-за забытого импорта стандартной библиотеки или `typing` (`List`, `Dict`, `Optional`, `Path`, `math`, `json`, `re`, `datetime`, `dataclass` и др.), `AutoImporter.try_inject_import` мгновенно внедряет недостающую строку импорта в начало файла и перезапускает скрипт без обращения к LLM.
4. **Headless GUI Smoke Harness (`SandboxManager.run_script`)**:
   Оконные приложения (`tkinter`, `customtkinter`, `pygame`, `PyQt6`/`PySide6`) автоматически оборачиваются в тестовый раннер, который подавляет модальные окна, эмулирует клики по кнопкам, нажатия клавиш и события мыши в течение 35 кадров / 800 мс, перехватывая любые скрытые исключения рантайма.

---

## 7. Подсистема базы знаний и гибридного поиска RAG (Knowledge Base & Hybrid Search)

Подсистема базы знаний Эларии реализована строго на **CPU (0 МБ VRAM, latency < 50 мс)** и обеспечивает долговременное семантическое индексирование файлов документации, исходного кода, логов и таблиц:

```
[Пользовательский файл]
        │
        ▼
DocumentParser (.pdf, .docx, .doc, .xlsx, .csv, .pptx, .fb2, .txt, .md, .py, .json)
        │
        ▼
MarkdownChunker (500-1400 симв., overlap 150, сохранение блоков ``` и шапок таблиц)
        │
        ├─────────────────────────────────┬─────────────────────────────────┐
        ▼                                 ▼                                 ▼
   BM25Index                      BuiltinDenseVectorizer              FastEmbed ONNX
(Точные артикулы,                (Частотно-морфологические           (MiniLM-L12-v2,
 коды ошибок, коды деталей)       семантические корни, 256d)         семантический вектор, 384d)
        │                                 │                                 │
        └─────────────────────────────────┼─────────────────────────────────┘
                                          ▼
                             HybridSearchEngine (Контур 3)
                                          │
                         Reciprocal Rank Fusion (RRF, k=60) + Linear Fusion
                                          │
                         Gatekeeper (Отсев small talk + порог dense >= 0.75 при BM25 == 0)
                                          │
                         Cross-Reference Linker (Авто-добор связанных файлов)
                                          │
                                          ▼
                      KnowledgeHUDWidget (Неоновые микро-чипы источников в бабле чата)
```

### Архитектурные компоненты RAG:
1. **`services.knowledge_manager.KnowledgeManager`**:
   Синглтон управления документами в каталоге `knowledge_base/`. Извлекает 8–10 ключевых триггеров на CPU (`extract_triggers`), ведет атомарный реестр `registry.json` и сериализованный индекс `search_index.json`. Поддерживает **Query Fusion** (`_fuse_contextual_query`) и **Cross-Reference Linker** (`_find_cross_references`).
2. **`services.knowledge_watcher.KnowledgeWatcher`**:
   Фоновый сателлит на базе `QFileSystemWatcher` с дебаунс-таймером **2.0 секунды**, запускающий синхронизацию через `TaskQueue` (`PRIORITY_LOW = 20`).
3. **`ui.dialogs.knowledge_manager_dialog.KnowledgeManagerDialog` (3 вкладки)**:
   - Вкладка 1 (`📚 База знаний`): управление документами и просмотр чипов триггеров (`DocumentCardWidget`).
   - Вкладка 2 (`⚡ Скиллы Эларии`): просмотр базовых и создание/удаление пользовательских микро-скиллов Ассистента (`data/custom_assistant_skills.json`).
   - Вкладка 3 (`⚙️ Системный промпт`): редактирование базового системного промпта личности Эларии (`assistant_custom_prompt_template`) для новых сессий.

---

## 8. Подсистема потокового рассуждения CoT, HistorySanitizer и двухпроходной визуализации Эларии

### 8.1. Изоляция мыслей (`ThoughtStreamParser`, `ThoughtSpoilerWidget` и `HistorySanitizer`)
Для поддержки рассуждающих моделей и протокола `ROUTING_TAIL_PROTOCOL` (где первым токеном ответа всегда открывается `<think>`) реализован потоковый контур на CPU:

```
LLM Streaming Chunk (SSE) 
         │
         ▼
ThoughtStreamParser (State Machine: NORMAL_TEXT -> IN_THINK -> THINK_FINISHED)
         │
         ├─► [IN_THINK]:        Перехват <think>/<thought>, очистка тегов clean_thought_chunk
         │                      Отрисовка в ThoughtSpoilerWidget (28 FPS)
         │                      Circuit Breaker: автозакрытие при >= 1800 симв. (~450 токенов)
         │
         └─► [THINK_FINISHED]:  Чистовой текст ответа в AssistantChatMessageWidget
                                Трехфазный HistorySanitizer (Rescue -> Clean -> Empty Text Guard)
```

- **Трехфазный `HistorySanitizer` (`AssistantFlowControllerMixin._handle_assistant_llm_response`)**:
  - **Фаза 1 (Rescue to UI Spoiler)**: перехватывает нестандартные теги рассуждений (`<think>`, `<analyze>`, `<thought>`, `<reasoning>`) и спасает их содержимое в `thought_data`.
  - **Фаза 2 (Clean)**: вырезает блоки рассуждений из чистового текста ответа.
  - **Фаза 3 (Empty Text Guard)**: если ответ модели на 100% состоял из блока рассуждений без чистового текста после закрывающего тега, восстанавливает текст из `thought_data`, предотвращая появление пустого сообщения.
- **Sliding CoT Preservation (`ContextWindowBuilder.prepare_history_for_llm`)**: В истории для LLM сохраняется **ровно одно последнее рассуждение `<think>`** ассистента, а более ранние блоки `<think>.*?</think>` вырезаются ради экономии контекстного окна 14K.

### 8.2. Двухпроходный протокол визуализации (`[GALERY]` -> `Gallery Art Pass` -> `SD Forge`)
1. **Проход 1 (Диалоговый отклик)**: Элария отвечает живой репликой на русском языке и завершает сообщение маркером `[GALERY]`. Потоковый буфер перехватывает маркер на лету и разворачивает в бабле анимированный видоискатель `CyberpunkArtPlaceholderWidget`.
2. **Проход 2 (`_enqueue_gallery_art_pass`)**: В фоне запускается легковесный запрос `Gallery Art Pass` (контекст 4096, `max_tokens=512`), генерирующий строку `[IMAGE_PROMPT: <35-60 английских Danbooru-тегов> --ar <wide|portrait|square> --model <id>]`.
3. **Диспетчеризация в Forge (`_dispatch_art_to_forge`)**: Промпт обогащается `ELARIA_VISUAL_DNA` (для автопортрета), проверяется на фиксацию сида (`Seed Lock`) и передается в `ArtDirector.plan_from_visual_intent`.

---

## 9. Ролевой движок (Roleplay Engine), Архитектура «Трёх якорей» и Арт-директор

### 9.1. Архитектура «Трёх якорей» (`Three Anchors`), Depth-0 Директива и KV-Cache In-Chat Injection
В режиме `roleplay` (бюджет 24 576 токенов) модуль `ContextWindowBuilder.get_lm_studio_messages` и `RoleplayFlowControllerMixin` формируют контекстное окно без инвалидации KV-кэша `llama-server`:

```
┌───────────────────────────────────────────────────────────────────────────┐
│ ЯКОРЬ 1 (messages[0]): Замороженный System Prompt (или кастомный промпт)  │
│ • Идентичность (Среда / Группа спутников / Одиночный персонаж + POV 1/3)  │
│ • Авторский system_prompt + Архетип (1 из 11) + Описание/Личность/Сценарий│
│ • Постоянные статьи лорбука (constant=True) + <PlayerCharacter>           │
│ • Сводка активных спутников, статуса и инвентаря (get_cast_prompt_block)  │
│ * Может быть переопределен через диалог «📜 Промпты...» (PromptViewer)    │
├───────────────────────────────────────────────────────────────────────────┤
│ ЯКОРЬ 2 (messages[1]): Первое сообщение (first_mes / Root Anchor)         │
│ • Каноническая завязка сюжета и эталон стиля автора — НИКОГДА не удаляется│
├───────────────────────────────────────────────────────────────────────────┤
│ ЗОНА ВЫТЕСНЕНИЯ (middle_messages = history[1:-12])                        │
│ • При превышении бюджета 24K старейшие сообщения удаляются по одному      │
│ • При триггерах воспоминаний («помнишь», «в начале») работает Deep Recall │
├───────────────────────────────────────────────────────────────────────────┤
│ ЯКОРЬ 3 (recent_tail = history[-12:]): Оперативный хвост (12 сообщений)   │
│ • Всегда сохраняется целиком для неразрывной связности текущей сцены      │
│ • К ПОСЛЕДНЕЙ реплике игрока (messages[-1]) приклеивается In-Chat суффикс:│
│   <WorldState> + <RecalledLore> (LanceDB RP) + <SceneContext> +           │
│   <TriggeredCardLore> + [Ключевая заметка автора: depth_prompt] +         │
│   [Системная директива сцены: Depth-0 Якорь (AnchorViewerDialog)]         │
└───────────────────────────────────────────────────────────────────────────┘
```

### 9.2. Классификация 11 ролевых архетипов (`TavernParser.detect_card_archetype` & `RoleplayPromptBuilder`)
При загрузке карточки автоматически детектируются 11 жанровых архетипов с выделенными правилами, абстрактными Depth-0 якорями (`get_default_depth_anchor`) и UI-виджетами (`RPGStatusWidget`, `RPGChoicesWidget`, `RPGThoughtWidget`):
1. `System (ЛитРПГ/Исекай)` — интерфейс Системы, накопительный блок `<stats>...</stats>`.
2. `Dating-Sim (Симпатия)` — постепенная динамика сближения и шкалы `<meters>` (Симпатия, Доверие, Стадия).
3. `Horror (Рассудок)` — психологический хоррор, ненадежный рассказчик и шкала Рассудка/Стресса в `<meters>`.
4. `CYOA (Интерактивная новелла)` — генерация развилок сюжета в интерактивных кнопках `<choices>`.
5. `Reverse-RP (Зависимый персонаж)` — допрос, суд или наставничество (ведущая инициатива у игрока).
6. `Detective (Расследование)` — причинно-следственная логика улик и журнал дела в `<stats>`.
7. `DM (Гейммастер)` — объективный ведущий с проверками навыков `<check>` и развилками `<choices>`.
8. `Scenario (Выживание)` — учет ранений, усталости и расхода предметов инвентаря.
9. `Group (Группа спутников)` — полифонический отыгрыш отряда с переключением `focus_companion` и индивидуальными срезами внешности/отношений.
10. `Lorebook (Энциклопедия)` — справочный модуль мира, подключаемый к сессиям через дашборд кампании.
11. `Solo (Одиночный персонаж)` — классический ролевой отыгрыш с автоопределением лица повествования (`narrative_pov`: 1-е или 3-е лицо) и грамматического рода.

### 9.3. Детерминированный перехват статуса, защита от обрыва тегов и летописец сцены
- **Предохранитель парсера стрима (`RoleplayFlowControllerMixin._handle_roleplay_llm_response`)**: Автоматически дозакрывает оборванные моделью блоки кода ` ``` ` и теги `<stats>`, `<meters>`, `<choices>` перед передачей в `RPGContentParser`.
- **`RoleplayStateManager` (`services/roleplay_state_manager.py`)**: Мгновенно на CPU парсит блоки `<stats>...</stats>` или `[STATUS: ...]`, извлекая локацию, инвентарь, экипировку, квест и характеристики ЛитРПГ в `cast_<session_id>.json`.
- **`RPCastExtractor` & `RPSceneUpdater` (`services/rp_cast_extractor.py`, `services/rp_scene_updater.py`)**: В фоновом потоке (`PRIORITY_LOW`) через `Structured Outputs` (JSON Schema) анализирует последний ход, обновляя реестр NPC, локацию, инвентарь (с защитой реликвий), `companion_updates`, сдвиги отношений, сюжетные вехи в `LanceDB` (`rp_memories`), кино-архетип `scene_type` (`intimate`, `action`, `dialogue`, `exploration`) и `visual_intent`.
- **Подсистема «Арт-директор» (`services/art_director/`) и `MinorSafetyDetector`**: Формирует промпты по 4 мизансценам, выполняет симметризацию мульти-персонажных сцен через оператор `BREAK` и проверяет возрастную безопасность через `MinorSafetyDetector`.

---

## 10. Двухконтурная маршрутизация скиллов (`skill_router.py` и `engineer_skill_router.py`)

В системе действуют два независимых гибридных роутера скиллов на CPU (`FastEmbed ONNX`, 0 МБ VRAM):

### 10.1. Роутер микро-скиллов Ассистента (`services/skill_router.py`)
Поддерживает ручную фиксацию скилла в сессии (`active_assistant_skill` через меню аватара), пользовательские скиллы из `data/custom_assistant_skills.json` и автоматический выбор:
- **Уровень 1 (Мгновенные эвристики, 0 мс)**: `gallery`, `analyst_logic`, `wellness_bio`, `tutor_feynman`.
- **Уровень 2 (Семантический векторный анализ, порог `0.50`)**: `deep_engineer`, `code_architect`, `text_crafter` + кастомные скиллы пользователя.

### 10.2. Роутер архетипов Инженера (`services/engineer_skill_router.py`)
Поддерживает ручную фиксацию скилла (`active_engineer_skill`), пользовательские архетипы из `data/custom_engineer_skills.json`, иконки в `data/skill_icons/` и автоматическое векторное сопоставление (порог `0.25`, фоллбэк `pure_logic_tdd`):
1. `interactive_loop` (`🎮 Игры и GUI` -> `game.py`)
2. `pure_logic_tdd` (`⚙️ Чистая логика (Алгоритмы и TDD)` -> `module.py`)
3. `batch_artifact` (`📊 Документы и отчеты (Excel / Word)` -> `make_doc.py`)
4. `service_backend` (`🌐 Сервисы и API (FastAPI / Боты)` -> `main.py`)
5. `cli_automation` (`💻 Консольные утилиты (CLI и автоматизация)` -> `main.py`)

---

## 11. Автономная конфигурация и запуск headless сервера LLM (`server_config.py`, `process_manager.py`, `layer_budgeter.py`)

Для обеспечения автономной работы без сторонних окон предусмотрен запуск фонового сервера `llama-server.exe`:

```
                    ServerConfigResolver.resolve_server_exe()
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
Уровень 1: bin/                Уровень 2: server_config.ini   Уровень 3: Рекурсивный поиск
(LOCAL_BIN_DIR / llama-server) (параметр server_bin_path)    (~/.lmstudio/extensions/backends/*cuda*)
```

1. **`services.server_config.ServerConfigResolver`**:
   - **Разрешение бинарника (`resolve_server_exe`)**: 3-уровневый каскад с приоритетом CUDA 12.
   - **Поиск CUDA DLL (`resolve_cuda_dir`)**: Обнаружение `cudart64*.dll` и инъекция в `PATH`.
   - **Поиск моделей (`discover_gguf_models`)**: Сканирование папок `models/`, каталогов LM Studio и привязка мультимодальных адаптеров `*mmproj*.gguf`.
2. **`services.process_manager.ProcessManager`**:
   - **Port Conflict Guard (`inspect_port_occupant`)**: перед стартом проверяет порт 1234 через `psutil.net_connections`. Если порт занят нашим зависшим процессом — завершает его через `taskkill`; если сторонним приложением — выводит точное имя процесса и PID.
   - Интегрируется с `LayerBudgeter.calculate_layer_offload` для автоматического расчета флага `-ngl`, читает настройки `kv_cache_type` (`-ctk`/`-ctv`) и `flash_attention_enabled` (`-fa on/off`) из `config.json`, автоматически включает `--no-mmap` при гибридном оффлоаде слоев и опрашивает `GET /v1/models` до полной готовности.

---

## 12. Сборщик контекстных окон и профили режимов (`context_window_builder.py`, `config.py`)

### Параметры профилей режимов (`MODE_PROFILES` в `config.py`):

| Режим (`mode`) | Контекст (`context_length`) | Буфер истории (`history_tokens`) | Лимит сообщений (`history_limit`) | Temperature | Min-P | Top-P | Repetition Penalty | DRY Mult | XTC Prob | Max Tokens |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`assistant`** | 14 336 (14K) | 11 000 | 60 | 0.70 | 0.05 | 0.95 | 1.05 | 0.80 | 0.20 | 1024 |
| **`roleplay`** | 24 576 (24K) | 18 000 | 65 | 1.10 | 0.05 | 0.95 | 1.02 | 0.80 | 0.30 | 4096 |
| **`engineer`** | 28 672 (28K) | 22 000 | 50 | 0.70 | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 4096 |

### Аппаратные предохранители VRAM и двухконтурный бюджет:
- **`HARDWARE_MAX_CONTEXT_LENGTH = 28672`** (28K): Абсолютный аппаратный потолок размера контекста LLM.
- **`MASTER_MAX_RESPONSE_TOKENS = 4096`** (4K): Абсолютный потолок длины ответа за один проход инференса.
- **Контур 1 (Относительный бюджет токенов `TokenBudgetManager.calculate_context_budget`)**:
  - **Ступень 1 (`ratio < 0.60`)**: `history_limit = 60`, `max_tokens = 1024`.
  - **Ступень 2 (`0.60 <= ratio < 0.80`)**: `history_limit = 42`, `max_tokens = 768`.
  - **Ступень 3 (`ratio >= 0.80`)**: `history_limit = 24`, `max_tokens = 512`.
- **Контур 2 (`EMERGENCY_VRAM_THRESHOLD_MB = 150`)**: Пожарный рубильник — при падении свободной VRAM ниже 150 МБ параметры принудительно переключаются на Ступень 3 (24 сообщения / 512 токенов) независимо от заполнения контекста.

---

## 13. Спецификация форматов персистентного хранения данных

### 13.1. Сессия диалога: `history/chat_<session_id>.json` (и зеркало `outputs/sessions/<session_id>/session.json`)
- `id` (string UUID), `title` (string), `mode` (`"assistant"` | `"roleplay"` | `"engineer"`), `type` (`"engineering"`, optional).
- `metadata` (object, optional): хранит зафиксированные скиллы сессии `active_assistant_skill` и `active_engineer_skill`.
- `custom_system_prompt` (string, optional): переопределенный системный промпт сессии.
- `custom_depth_anchor` (string, optional): переопределенная Depth-0 директива сцены для РП-сессии.
- `character_data` (object, optional): снимок карточки персонажа с синхронизацией переводов `_ru` и `visual_dna`.
- `active_lorebook` (string, optional): имя подключенного внешнего файла лорбука из `lorebooks/`.
- `messages` (array): массив сообщений для UI (`id`, `sender`, `text`, `is_user`, `is_error`, `image_path`, `audio_path`, `file_paths`, `translated_text`, `generation_params`, `visual_intent`, `knowledge_sources`, `thought`, `timestamp`).
- `chat_history` (array): OpenAI-совместимый массив `[{"role": ..., "content": ...}]` для LLM.
- `sampling_params` (object): параметры инференса (`temperature`, `min_p`, `top_p`, `repeat_penalty`, `dry_multiplier`, `xtc_probability`, `xtc_threshold`, `max_tokens`, `is_dm_mode`).

### 13.2. Каст ролевого мира: `history/cast_<session_id>.json`
- `focus_companion` (string), `scene_type` (`"dialogue"` | `"action"` | `"intimate"` | `"exploration"`), `current_location` (string), `active_quest` (string).
- `relationship_context` (object): `dynamic_tone`, `foundation`, `current_bond`.
- `inventory` (array of strings): предметы снаряжения отряда (до 16 предметов с защитой сюжетных реликвий от вытеснения).
- `stats` (object): словарь характеристик и шкал ЛитРПГ/дейт-сима/хоррора.
- `world_state` (array of strings): активные опасности и правила мира.
- `player_state` / `companion_state` (object): `vital_status` (`"alive"` | `"critical"` | `"dead"`), `visual_dna`, `mutations`, `current_outfit`.
- `environment` (object): `world_baseline`, `scene_ambience`, `location_dna`, `is_enclosed` (boolean).
- `entities` (object): реестр сопартийцев (`role: "companion"`, `ttl: 999`) и встреченных NPC (`role: "npc"`, `presence: "physical" | "memory"`, `vital_status`, `ttl`, `visual_dna`, `facts`, `aliases`).

### 13.3. Долговременная память, кэш моделей и пользовательские скиллы (`data/`)
- `data/memory.db` (SQLite WAL): таблицы `user_memory`, `rp_memory` (до 100 вех на сессию), `reminders`, синхронизированные с векторными таблицами `facts` и `rp_memories` в `data/vector_memory/` (`LanceDB`).
- `data/models_cache.json`: кэш распарсенных заголовков GGUF (`total_layers`, `model_size_mb`, `architecture`, `is_moe`, `context_length`) по ключу `path::mtime::size`.
- `data/custom_engineer_skills.json` и `data/custom_assistant_skills.json`: пользовательские архетипы разработки и микро-скиллы Ассистента.
- `knowledge_base/registry.json` и `knowledge_base/search_index.json`: реестр документов, триггеров и сериализованный индекс гибридного поиска `HybridSearchEngine`.

---

## 14. Матрица верификации тестов (487+ тестов)

| Тестовый модуль | Количество тестов | Направление верификации |
| :--- | :---: | :--- |
| `tests/test_shutdown_lifecycle.py` | 18 | Тестирование Graceful Shutdown, Watchdog таймера (8.0s), приоритета `taskkill /F /T` для Forge/llama-server и зачистки процессов через `psutil`. |
| `tests/test_vram_arbiter.py` | 24 | Проверка монопольного владения GPU (`GPUOwner`), взаимного исключения LLM/Forge, флага `needs_llm_prompt_gen`, `LayerBudgeter` и динамических пауз WDDM. |
| `tests/test_mode_coordinator.py` | 32 | Жизненный цикл FSM переключения режимов (`Teardown -> Switch -> Boot -> Mount`), очистка очередей, изоляция контекста и геометрия сплиттера. |
| `tests/test_code_engine.py` | 46 | Работа `ASTSkeletonizer`, `AutoImporter` (Zero-Token Healing), нечеткий матчинг `PatchApplier`, `replace_symbol` и `CodeFirstTDDPipeline`. |
| `tests/test_code_validator.py` | 52 | Валидация AST, Scope Guard (`NameError` через `symtable`), детекция ленивых токенов (`# rest of code`), `SubstanceGuard`, контракты openpyxl/docx. |
| `tests/test_sandbox_guard.py` | 28 | Аудит-хуки безопасности `sys.addaudithook`, блокировка системных вызовов, опасных импортов и сетевых сокетов. |
| `tests/test_token_budget_manager.py` | 34 | Пессимистичный расчет токенов BPE, двухконтурная модель `ContextTierDecision` (`EMERGENCY_VRAM_THRESHOLD_MB = 150`), светофор лимитов карточек. |
| `tests/test_tavern_parser.py` | 48 | Парсинг спецификаций Tavern V1/V2, Chub.ai, автоизвлечение Visual DNA, Scenery DNA, `detect_card_archetype` (11 архетипов) и статей лорбуков. |
| `tests/test_art_director.py` | 64 | Формирование `GenerationPlan`, архетипы геометрии кадра, симметризация `BREAK`, интеграция LoRA, ADetailer и `MinorSafetyDetector`. |
| `tests/test_rp_cast_manager.py` | 42 | Управление отрядом, `format_cast_data_block`, ротация фокусного спутника, таргетированные `companion_updates`, перехват `<stats>` в `RoleplayStateManager`. |
| `tests/test_session_manager.py` | 55 | Атомарное сохранение сессий, ACID-запись через `.tmp`, архитектура «Трёх якорей», `HistorySanitizer` и Sliding CoT Preservation. |
| `tests/test_document_parser.py` | 44 | Безопасное извлечение текста из `.docx`, `.xlsx`, `.pptx`, `.pdf`, `.fb2` и гибридный поиск `HybridSearchEngine` (BM25 + Dense + RRF). |
| **ИТОГО** | **487+** | **100% Pass Rate (0 failed, 0 skipped, 0 warnings)** |

---

## 15. Архитектура интеграций и сетевая топология внешних сервисов (C4 Container Level)

```
                                  ┌────────────────────────┐
                                  │   Chub.ai Catalog API  │
                                  │ (https://api.chub.ai)  │
                                  └───────────▲────────────┘
                                              │ HTTPS REST
                                              │ (WAF Bypass Headers)
┌───────────────────────────┐  HTTP REST  ┌───┴────────────────────┐  HTTPS REST   ┌────────────────────────┐
│ Headless llama-server.exe │◄───────────►│       NeonRoute        │◄─────────────►│ Civitai Model Catalog  │
│ / LM Studio (:1234/v1)    │ SSE / JSON  │   Orchestrator Core    │ SHA256 Check  │ (https://civitai.com)  │
└───────────────────────────┘             │  (PyQt6 / Python 3.10) │               └────────────────────────┘
                                          └───┬───────┬────────────┘
                                              │       │
                                    HTTP REST │       │ HTTP REST
                             (/sdapi/v1/...)  │       │ (GET/POST /synthesize)
                                              ▼       ▼
                                   ┌────────────┐   ┌────────────┐
                                   │ SD WebUI   │   │ F5-TTS     │
                                   │ Forge      │   │ Voice Core │
                                   │ (:7860)    │   │ (:5010)    │
                                   └────────────┘   └────────────┘
```

#### Спецификация сетевых эндпоинтов и защиты:
- **Headless `llama-server.exe` / LM Studio (`127.0.0.1:1234`)**:
  - `GET /v1/models`: Запрос списка загруженных GGUF моделей и проверка готовности сервера.
  - `POST /v1/chat/completions`: Вызов генерации текста и Vision-анализа (`Qwen2.5-VL`). Поддерживает Server-Sent Events (`stream=True`) с `chunk_callback`, промпт-кэширование (`cache_prompt=True`), нативные сэмплеры `dry_multiplier`/`xtc_probability` и строгую валидацию JSON Schema (`response_format`).
  - Выгрузка из VRAM: останов процесса `llama-server.exe` через `evict_llm_for_forge_sync()` либо вызов `POST /api/v0/models/unload` / `lms unload --all`.
- **SD WebUI Forge (`127.0.0.1:7860`)**:
  - `POST /sdapi/v1/txt2img`: Пайплайн генерации с двухступенчатым отказоустойчивым фоллбэком (при сбое с ADetailer повторяет запрос без ADetailer; при нехватке памяти в Hires.fix повторяет запрос с `enable_hr=False`).
  - `POST /sdapi/v1/options`: Динамическая смена активного чекпоинта (`sd_model_checkpoint`).
  - `POST /sdapi/v1/unload-checkpoint` & `POST /sdapi/v1/free-vram`: Освобождение памяти GPU (~3.2 ГБ) и очистка CUDA-кэша.
  - `GET /sdapi/v1/progress`: Опрос прогресса генерации (350 мс) со сторожевым таймером зависания шагов (`stagnation_timeout = 90.0` с).
  - `POST /sdapi/v1/interrupt`: Мгновенная остановка рендера.
- **F5-TTS (`127.0.0.1:5010`)**:
  - `GET /health` & `POST /synthesize`: Передача текста для CPU-синтеза речи (`device="cpu"`). При недоступности сервера генерирует чистый пентатонический WAV-файл предпросмотра.
- **Chub.ai (`https://api.chub.ai`)**:
  - `GET /api/characters/search`: Поиск карточек персонажей с лингвистической проверкой кириллицы (`russian_only`) и фильтрацией по бюджету токенов.
  - `GET /api/characters/{full_path}?full=true`: Скачивание метаданных Tavern V2 с очисткой промо-ссылок `sanitize_card_description` и резервной распаковкой `chara_card_v2.png` из CDN.
- **Civitai (`https://civitai.com/api/v1`)**:
  - `GET /model-versions/by-hash/{hash}`: Автонастройка параметров рендера по SHA-256 хэшу файла модели (таймаут 4.0 с, безопасный fallback на `DEFAULT_PIPELINE_PARAMS`).
  - Потоковое скачивание (`CivitaiDownloadWorker`): чанки по 1 МБ во временный файл `.part`, строгая фильтрация расширения (только `.safetensors`), защита от SSRF по белому списку доменов `*.civitai.com` и финальная сверка контрольной суммы SHA-256.

---

## 16. Модель безопасности песочницы и аудит-хуки (`sandbox_guard.py`, `code_validator.py`)

Безопасность выполнения кода обеспечивается двойным контуром: статическим AST-анализом до записи на диск (`CodeValidator.validate_python_syntax`) и динамическим перехватом в рантайме (`sandbox_guard.install_guard`):

| Системный вызов / Модуль | Статус в песочнице | Механизм контроля |
| :--- | :---: | :--- |
| `os.system`, `os.popen`, `os.spawn*` | **ЗАБЛОКИРОВАНО** | Подмена функций в модуле `os` на заглушки, генерирующие `PermissionError`, плюс перехват в `sys.addaudithook`. |
| `subprocess.*` | **ЗАБЛОКИРОВАНО** | Блокировка импорта в AST и аудит-хук `subprocess.*` (разрешены только вызовы из самого `sandbox_manager.py` и `pytest`). |
| `socket.connect`, `bind`, `listen`, `socket` | **ЗАБЛОКИРОВАНО** | Блокировка импорта `socket`/`requests` в AST и аудит-хук `socket.*` для пресечения сетевой активности. |
| `ctypes`, `winreg`, `shutil` | **ЗАБЛОКИРОВАНО** | Заблокированы на уровне `AST Safety Guard` и `guarded_import` в `builtins.__import__`. |
| `urllib.request`, `urllib.robotparser` | **ЗАБЛОКИРОВАНО** | Заблокированы сетевые подмодули `urllib` как при прямом импорте, так и через `from urllib import request`. |
| `os.chdir` | **ОГРАНИЧЕНО** | Разрешен переход строго внутри каталога `workspace/projects/<session_id>/`. |
| `os.remove`, `unlink`, `rmdir`, `rename`, `replace` | **ОГРАНИЧЕНО** | Разрешено удаление/переименование только внутри `workspace/` и `tempfile.gettempdir()` (защита корня `workspace` от удаления). |
| `open` (запись `'w'`, `'a'`, `'x'`, `'+'`) | **ОГРАНИЧЕНО** | Разрешена запись СТРОГО в каталоги `workspace/`, `tempfile.gettempdir()` и кэш `~/.matplotlib`. Запись в системные каталоги (`C:\Windows`, `C:\Program Files`) вызывает `PermissionError`. |

---

## 17. Спецификация конфигурации приложения (`config.py`, `config.json`)

Файл `config.json` служит единым источником правды для пользовательских настроек, профилей сэмплинга, назначенных по режимам моделей и параметров движка `llama-server`. Загрузка и атомарная запись выполняются через `utils.config_manager.ConfigManager` с глубоким рекурсивным слиянием (`_deep_merge_dicts`):

```json
{
  "theme": "dark",
  "chat_font_size": 13,
  "auto_translate": false,
  "active_model": "qwen2.5-coder-7b-instruct",
  "engineer_model": "qwen2.5-coder-7b-instruct",
  "engineer_model_name": "qwen2.5-coder-7b-instruct",
  "rp_model_name": "",
  "assistant_model_name": "",
  "compute_device_mode": "Auto",
  "background_path": "",
  "background_dim_opacity": 140,
  "assistant_custom_prompt_template": "",
  "context_length": 24576,
  "context_length_roleplay": 24576,
  "context_length_assistant": 14336,
  "context_length_engineer": 28672,
  "default_sampling_roleplay": {
    "temperature": 1.10,
    "min_p": 0.05,
    "top_p": 0.95,
    "repeat_penalty": 1.02,
    "dry_multiplier": 0.80,
    "xtc_probability": 0.30,
    "xtc_threshold": 0.10,
    "max_tokens": 4096,
    "is_dm_mode": false
  },
  "default_sampling_assistant": {
    "temperature": 0.70,
    "min_p": 0.05,
    "top_p": 0.95,
    "repeat_penalty": 1.05,
    "dry_multiplier": 0.80,
    "xtc_probability": 0.20,
    "xtc_threshold": 0.10,
    "max_tokens": 1024
  },
  "default_sampling_engineer": {
    "temperature": 0.70,
    "min_p": 0.00,
    "top_p": 1.00,
    "repeat_penalty": 1.00,
    "dry_multiplier": 0.00,
    "xtc_probability": 0.00,
    "xtc_threshold": 0.10,
    "max_tokens": 4096
  },
  "auto_generate_art": false,
  "visualizer_settings": {
    "auto_generate_art": false,
    "steps": 28,
    "cfg_scale": 6.0,
    "sampler_name": "DPM++ 2M",
    "denoising_strength": 0.35,
    "default_style": "realistic"
  },
  "translation_settings": {
    "temperature": 0.20,
    "max_tokens_limit": 4096
  },
  "hardware_autotune_done": false,
  "kv_cache_type": "q8_0",
  "flash_attention_enabled": true
}
```

---

## 18. Детальный журнал изменений и сверки с кодовой базой (Changelog & Audit Diff)

Ниже зафиксированы результаты аудита и актуализации `ARCHITECTURE.md` в полном соответствии с дампом `project_code_dump.txt` (**183 файла, 71 953 строки кода**, прирост **+10 новых файлов** и **+11 535 строк кода**, обновлены метрики и описание **48 существующих модулей**):

1. **Добавлены 10 новых модулей в дерево архитектуры и документацию**:
   - `services/layer_budgeter.py` (`[628 строк]`) и `ui/dialogs/llm_engine_settings_dialog.py` (`[351 строка]`): топологический аудитор GPU (`PURE COMPUTE` vs `PRIMARY DISPLAY`), парсер бинарных заголовков GGUF v2/v3 с кэшем `data/models_cache.json`, расчет слоев `ngl` с гистерезисом (`HYSTERESIS_LAYER_DELTA = 3`), `Pre-flight RAM Guard` и аппаратный автотюн KV-кэша (`q8_0`) и Flash Attention (`-fa on`).
   - `services/engineer_skill_router.py` (`[605 строк]`) и `ui/engineer_skills_dialog.py` (`[998 строк]`): векторный роутер 5 фундаментальных инженерных архетипов (`interactive_loop`, `pure_logic_tdd`, `batch_artifact`, `service_backend`, `cli_automation`) на базе `FastEmbed CPU`, поддержка кастомных скиллов (`data/custom_engineer_skills.json`) и центр управления знаниями инженера (**Skills Hub**).
   - `services/engineer_tdd.py` (`[629 строк]`): двухфазный **Code-First TDD пайплайн** (`CodeFirstTDDPipeline`) с ветками `NEW_MODULE` и `INCREMENTAL_FEATURE`, автоматическим `Bootstrap Baseline`, регрессионным контролем и двусторонним исцелением кода/тестов до 15 попыток через `pytest`.
   - `ui/components/workspace_inspector.py` (`[751 строка]`): правая боковая панель инспектора песочницы в 3-колоночном сплиттере `MainWindow` (`Ctrl+B`) с деревом файлов, редактором кода, живым приемом стриминга кода (`CodeStreamExtractor`), ручным коммитом правок в Git и изолированным Smoke-тестом по `F5`.
   - `ui/widgets/engineer_task_card.py` (`[1122 строки]`): монолитная агентская консоль задачи `EngineerTaskCardWidget` (Activity Header с секундомером, бейдж скилла, `SmoothSpoiler`, вертикальный `Execution Stepper` с переходом в инспектор и защитой `sip.isdeleted`).
   - `ui/dialogs/anchor_viewer_dialog.py` (`[145 строк]`) и `ui/dialogs/prompt_viewer_dialog.py` (`[181 строка]`): диалоги просмотра и кастомизации Depth-0 якоря сцены (`custom_depth_anchor`) и системного промпта карточки (`custom_system_prompt`) для текущей РП-сессии.
   - `workspace/calculator.py` (`[5 строк]`): демонстрационный модуль песочницы.
2. **Актуализированы изменения в существующих модулях**:
   - **Конфигурация и пороги (`config.py`, `utils/config_manager.py`, `ui/dialogs/model_sampling_dialog.py`)**: порог `EMERGENCY_VRAM_THRESHOLD_MB` снижен с `600` до **`150 МБ`**; дефолтная температура режима `engineer` изменена с жестко заблокированных `0.15` на настраиваемые **`0.70`**; добавлены константы резервирования VRAM (`FORGE_VRAM_RESERVE_MB = 450`, `SAFETY_MARGIN_VRAM_MB = 150`, `OS_RESERVE_PURE_COMPUTE_MB = 150`, `OS_RESERVE_PRIMARY_DISPLAY_MB = 700`).
   - **Инструменты и диагностика Инженера (`services/tool_engine.py`: `785 -> 3720 строк`, `services/subagent_engineer.py`: `1730 -> 2176 строк`, `services/engineer_planner.py`: `283 -> 508 строк`)**: добавлены инструменты `replace_symbol` (точечная AST-замена узлов в файлах `> 250` строк), `run_pytest`, проверка содержательности артефакта `SubstanceGuard`, `AutoImporter` (Zero-Token Healing для 26 стандартных импортов), ветка `intent == "review"` и очистка стектрейсов `clean_traceback`.
   - **Центр знаний и скиллов Эларии (`services/skill_router.py`: `168 -> 391 строка`, `ui/dialogs/knowledge_manager_dialog.py`: `412 -> 997 строк`, `ui/controllers/avatar_controller.py`: `412 -> 787 строк`)**: добавлена поддержка пользовательских скиллов Ассистента (`data/custom_assistant_skills.json`), редактор базового системного промпта Эларии во вкладке «⚙️ Системный промпт» и быстрый выбор скилла из контекстного меню аватара в шапке.
   - **Ролевой режим и потоковая очистка (`services/roleplay_prompt_builder.py`: `395 -> 548 строк`, `ui/controllers/assistant_flow_controller.py`: `1117 -> 1150 строк`)**: добавлены эталонные абстрактные Depth-0 якоря `get_default_depth_anchor`, предохранитель незакрытых тегов `<stats>`/`<meters>`/`<choices>` в стриме и трехфазный `HistorySanitizer` рассуждений.
