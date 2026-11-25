# История изменений и релизов NeonRoute

## v0.9.8.8-beta (2026-11-25)

> **Ключевое обновление:** полноценный рендеринг Markdown в мобильном веб-клиенте, галерея персонажей и лор мира прямо в шапке РП-чата, сквозное сохранение негативного промпта (`--neg`) при доработке изображений, устранение ошибок Forge (`KeyError: 'forge_vae_tiling'`, `TypeError` в Hires.fix) и устранение дублирования оркестрации VRAM между десктопом и мобильным сервером.

### Что изменилось

#### Мобильный веб-интерфейс (PWA) и РП-режим

- **Отображение Markdown в ответах нейросети:** внедрён XSS-безопасный парсер Markdown с поддержкой заголовков, списков, цитат, таблиц, инлайн-кода, блоков кода с кнопкой копирования в один клик и корректной отрисовкой незакрытых блоков кода прямо во время потокового стриминга.
- **Галерея персонажей и «Правила мира и лор» в шапке РП-чата:** в режиме Roleplay кнопки верхней панели теперь открывают детальную карточку персонажей (описание, характер, сценарий, приветствие, теги, автор и выбор компаньона) и справочник правил мира/лора, возвращаясь к галерее артов в режимах Ассистента и Инженера.
- **Чистый интерфейс вне РП-чата:** кнопки действий под сообщениями (`🎨 Арт сцены`, `🔄 Повтор`, `📋 Копировать`) отображаются только в режиме Roleplay и больше не загромождают обычные диалоги.
- **Корректная прокрутка и вёрстка на мобильных браузерах (включая Firefox Mobile):** добавлена гарантированная гибкая распорка снизу области чата и защита карточек от сжатия при появлении экранной клавиатуры; кэш PWA принудительно обновлён (`v6`).

#### Генерация изображений, память промптов и SD WebUI Forge

- **Сквозное сохранение негативного промпта (`--neg`) между кадрами:** при уточнении или продолжении генерации следующего изображения модель теперь автоматически получает из истории диалога не только позитивные теги, но и предыдущий негативный промпт (`--neg`), не теряя пользовательские исключения.
- **Исправление ошибок SD WebUI Forge (`KeyError` и `TypeError` в Hires.fix):** управление тайлингом VAE переведено на штатный скрипт Forge (`Never OOM Integrated`) вместо несуществующих ключей настроек, а при включённом `Hires.fix` устранено падение второго прохода на `hr_additional_modules`.
- **Исправление сбоя при завершении генерации арта в чате:** устранена ошибка `UnboundLocalError` при обработке результата генерации за счёт каскадного извлечения параметров визуального плана.
- **Единая оркестрация LLM / Forge / VRAM и ускорение тестов:** устранено дублирование логики переключения видеопамяти между десктопным приложением и мобильным сервером, добавлена потокобезопасная блокировка переключений `VRAMArbiter` и настроен параллельный запуск тестов через `pytest-xdist`.

---

### Технический список изменений (Changelog)

- **Рендеринг Markdown и интерфейс мобильного клиента (`mobile_server/static/js/markdown.js`, `ui.js`, `app.js`, `index.html`, `components.css`, `layout.css`, `sw.js`):**
  - Добавлен XSS-безопасный рендерер `markdown.js` (заголовки, списки, цитаты, таблицы, инлайн-код, блоки кода с `.md-code-copy-btn`, ссылки, форматирование и обработка незакрытых code-блоков при стриминге), интегрированный в `updateTokenChunk` и `finalizeAssistantMessage`.
  - В `createAssistantMessagePlaceholder` сохранено поле `mode`; рендер панели `.message-action-footer` ограничен условием `placeholder.mode === 'roleplay'`.
  - Логика выбора персонажа вынесена в `selectCompanion(char)`; в режиме `roleplay` кнопка `#btnWorldStatus` открывает `openCharactersGalleryModal()`, а `#btnHeaderGallery` переключается на `openWorldStatusModal()`.
  - В `layout.css` добавлена flex-распорка `.chat-viewport::after` (`calc(110px + max(12px, env(safe-area-inset-bottom, 0px)) + var(--keyboard-offset, 0px))`), в `components.css` задано `flex-shrink: 0` для `.chat-row` и `.cyberpunk-viewfinder`; версия кэша Service Worker повышена до `neonroute-shell-v6` (`?v=6`).
- **Галерея персонажей и единая оркестрация VRAM (`mobile_server/routes/characters.py`, `art.py`, `mobile_server/services/art_service.py`, `mobile_server/dependencies.py`, `services/vram_arbiter.py`, `services/process_manager.py`, `services/assistant_flow.py`):**
  - В `CharacterSummary` добавлены поля `personality` и `scenario`, снята обрезка `description` до 35 слов и `tags` до 5 элементов.
  - В `VRAMArbiter` добавлен реентрантный мьютекс `threading.RLock()` (`self._lock`) для `prepare_for_task`, `release_llm_for_diffusion`, `release_diffusion_for_llm` и `release_all`; параметризован `wait_stabilization` в `_release_forge_vram`.
  - В `process_manager.py` и `mobile_server/dependencies.py` реализовано переиспользование единого инстанса `ProcessManager` (`get_process_manager()` / `get_or_create_process_manager()`) с привязкой к `_lm_studio_client`.
  - В `assistant_flow.py`, `art_service.py` и роутах `/generate`, `/direct-command`, `/scene-background` удалены дублирующие `prepare_session_forge_kwargs` и `enqueue_forge_plan_async` с переходом на единый `enqueue_forge_generation_async` поверх `run_forge_art_generation()`.
- **Интеграция с SD WebUI Forge и контроллер генераций (`services/forge_client.py`, `ui/controllers/assistant_flow_controller.py`):**
  - Удалена передача несуществующих ключей `forge_vae_tiling` / `sd_vae_tile`; управление тайлингом переведено на `alwayson_scripts["Never OOM Integrated"]["args"] = [False, want_vae_tiling]`.
  - При `enable_hr=True` в payload `/sdapi/v1/txt2img` добавлена явная передача `"hr_additional_modules": []` с очисткой при `enable_hr=False` и в ветках ретрая (устранение `TypeError: argument of type 'NoneType' is not iterable`).
  - В `_handle_assistant_art_completed` извлечение `v_int` перенесено в начало метода с многоуровневым каскадом фоллбэков (`widget_to_update`, `task.metadata`, `result`, `plan`) и безопасным чтением `generation_params` / `parameters`.
- **Сквозная передача негативного промпта `--neg` (`services/assistant_flow.py`, `services/session_manager.py`, `ui/controllers/assistant_flow_controller.py`, `ui/builders/chat_bubble_builder.py`):**
  - В `extract_last_image_prompt()` добавлена функция `_extract_msg_negative(m)` (извлечение `user_neg` из `visual_intent` или `generation_params`) с автоматическим добавлением `--neg <tokens>` в `[IMAGE_PROMPT: ...]` при последующих шагах генерации.
  - Обеспечена запись и реконструкция флага `--neg` в `memory_content` (`assistant_flow_controller.py`), `session_manager.py` и `chat_bubble_builder.py`.
- **Параллелизация и изоляция тестового контура (`tests/conftest.py`):**
  - Подключён `pytest-xdist` (`pytest -n auto`), изолирован `_AIRGAP_TEMP_ROOT` по `PYTEST_XDIST_WORKER`, добавлены заглушки `ProcessManager.is_port_open` и `VRAMArbiter._wait_for_vram_stabilization`, а также повторное применение `_apply_airgap_paths` после каждого теста.

## v0.9.8.7-beta (2026-11-24)

> **Ключевое обновление:** 100% сохранение KV-кэша в Roleplay-чате на каждом ходе, устранение сетевого сбоя запуска SD WebUI Forge (`clip==1.0` / `tqdm` на PyPI), защита ядра Forge после обновлений (`git pull`), мягкая деградация при отсутствии ADetailer и динамический аудит установленных чекпоинтов.

### Что изменилось

#### Максимальная скорость и стабильность KV-кэша в Roleplay-чате

- **100% попадание в KV-кэш на каждом ходе в РП:** системный промпт персонажа и стартовое приветствие (`first_mes`) теперь остаются побитово неизменными на протяжении всей сессии. Динамические статьи лорбука, контекст сцены и память больше не меняют начало диалога, а аккуратно добавляются только в конец текущего хода — нейросети не приходится пересчитывать весь контекст заново.
- **Прозрачная диагностика KV-кэша (`[KV-DIAG]`):** добавлено детальное логирование слотов генерации, целостности дампов памяти после переключения с SD Forge и контрольных сумм префикса диалога.

#### Надёжный запуск SD WebUI Forge и гибкая работа с моделями

- **Устранение сетевого сбоя при старте Forge (`clip==1.0` / `tqdm`):** встроенный лаунчер больше не падает по таймауту (`ReadTimeoutError` / `exit code: 1`) при попытке разрешить зависимости через `pypi.org` на медленном или изолированном соединении. Зависимости изолированы локально с приоритетом готовых бинарных пакетов.
- **Защита ядра Forge и автовосстановление после обновлений (`git pull`):** исправлена критическая ошибка `TypeError: forge_loader() got an unexpected keyword argument 'sd_vae'`. Перед применением патчей проверяются контрольные суммы и сигнатуры файлов ядра Forge — несовместимые старые бэкапы больше не применяются вслепую, а безопасно обновляются или откатываются к чистому состоянию.
- **Мягкая деградация без ADetailer (защита от `HTTP 422`):** перед отправкой запроса на генерацию проверяется наличие расширения ADetailer и нужных моделей. Если расширение не установлено или веса отсутствуют, генерация спокойно выполняется без него вместо падения с ошибкой `422 Script 'ADetailer' not found`.
- **Динамическая проверка любых установленных чекпоинтов SD:** убрана жёсткая привязка к четырём конкретным моделям (`Realistic Vision`, `CyberRealistic`, `MeinaHentai`, `DreamShaper 8`). Программа сканирует все непустые `.safetensors` и `.ckpt` в папке моделей (с поддержкой `--ckpt-dir`, `--models-dir` и NTFS Junctions), привязывает к ним профили генерации и не засоряет лог ложными предупреждениями.
- **Актуализация архитектуры и тестов:** спецификация `ARCHITECTURE.md` синхронизирована с кодовой базой; полный прогон тестов расширен до `949 passed` (`0` регрессий).

---

### Технический список изменений (Changelog)

- **Стабилизация KV-кэша и диагностика в Roleplay (`services/roleplay_prompt_builder.py`, `services/context_window_builder.py`, `services/lm_studio.py`, `services/process_manager.py`):**
  - В `RoleplayPromptBuilder.build` добавлен параметр `include_dynamic_lore: bool = False`, исключающий `recalled_lore_block` из `messages[0]` ради побитовой статичности системного промпта.
  - В `ContextWindowBuilder.get_lm_studio_messages` динамические блоки (`<RecalledLore>`, `<SceneContext>`, `<SceneMemory>`) вынесены из `messages[0]` и инжектируются исключительно в эфемерную копию `messages[-1]`, сохраняя неизменными `messages[0]` (Якорь 1), `first_mes` (Якорь 2) и историю в `SessionManager`.
  - Внедрён диагностический контур `[KV-DIAG]`: логирование `id_slot`, `caller` и `messages_count` в `services/lm_studio.py`, контроль `saved_ngl`, `expected_ngl` и `mtime_diff` при восстановлении слота после SD Forge в `services/process_manager.py`, а также логирование MD5-хэша префикса (`messages[0]` и `messages[1]`) в `services/context_window_builder.py`.
- **Локальная изоляция зависимостей Forge (`core/forge_locator.py`, `core/forge_patcher.py`):**
  - Внедрена генерация и применение `constraints.txt` с фиксацией версий (`clip==1.0`, `tqdm>=4.64.0`, `ftfy>=6.1.1`, `regex>=2023.12.25`) и передачей флагов `--no-build-isolation` / `--prefer-binary` для исключения таймаутов на `pypi.org`.
  - В `get_clean_forge_env()` сохранена очистка `VIRTUAL_ENV`, `PYTHONPATH`, `PYTHONHOME`, прокси-переменных и установка `NO_PROXY="localhost,127.0.0.1,0.0.0.0"`.
- **Защита ядра Forge, мягкая деградация ADetailer и синхронизация профилей (`core/forge_patcher.py`, `services/scene_art_director.py`):**
  - Добавлена валидация контрольных сумм и сигнатур файлов ядра Forge (`modules/sd_models.py`, `modules/api/api.py`, `modules/api/models.py`), исключающая `TypeError: forge_loader() got an unexpected keyword argument 'sd_vae'` после обновления Forge.
  - В `services/scene_art_director.py` и генераторе JSON-нагрузки добавлен pre-flight чек доступности ADetailer и весов с пропуском `alwayson_scripts.ADetailer` при их отсутствии (защита от `HTTP 422`).
  - Профили генерации переведены на динамическую привязку к фактически установленным чекпоинтам.
- **Динамический аудит чекпоинтов SD (`services/asset_validator.py`):**
  - Убрана жёсткая привязка pre-flight аудита к списку из 4 моделей `REQUIRED_CHECKPOINTS` (константа сохранена для обратной совместимости импортов).
  - Реализовано динамическое сканирование `models/Stable-diffusion` (с поддержкой `--ckpt-dir`, `--models-dir` и `.resolve()` для симлинков / NTFS Junctions) с проверкой ненулевого размера `.safetensors` и `.ckpt` (`checkpoints_count >= 1`).
  - Добавлены ключи `checkpoints_count` и `checkpoints_files` в отчёт; предупреждение `WARNING` выводится только при `checkpoints_count == 0` или отсутствии весов ADetailer.
- **Документация и тестирование (`ARCHITECTURE.md`, `tests/unit/test_asset_validator.py`):**
  - Актуализирован `ARCHITECTURE.md` (изоляция зависимостей Forge, динамический аудит ассетов, мягкая деградация ADetailer, защита от рассинхронизации версий ядра).
  - Добавлен тест `test_asset_validator_empty_and_zero_byte_checkpoints`; общий прогон `pytest -q`: **949 passed (0 регрессий)**.

## v0.9.8.6-beta (2026-11-23)

> **Ключевое обновление:** исправление полноэкранного превью картинок и залипания курсора, неблокирующее модальное окно мобильного сервера, нормализация геометрии спинбоксов, обратимая фиксация версий библиотек SD Forge через `PIP_CONSTRAINT`, диагностика запуска/handshake (`HTTP 404`), управление флагом `--ad-no-huggingface` и мгновенное автосохранение настроек.

### Что изменилось

#### Интерфейс, превью изображений и мобильный сервер

- **Исправлено открытие превью сгенерированных картинок:** устранена ошибка развёртки окна при клике на изображение в чате, а также падение обработчика нажатия мыши в интерфейсе.
- **Стабильная работа окна просмотра и гигиена курсора:** диалог полноэкранного просмотра теперь корректно привязывается к главному окну программы и гарантированно освобождает ресурсы после закрытия. Устранено залипание курсора («палец вверх») во всём окне после закрытия превью.
- **Стабильный запуск мобильного сервера без блокировки интерфейса:** диалог мобильного сервера переведён на полноценное модальное окно с собственным заголовком, что полностью исключило намертво блокирующий оверлей поверх главного окна. Сам запуск сервера больше не подвешивает интерфейс.
- **Нормальное отображение цифр в спинбоксах:** текстовые поля числовых счётчиков больше не сжимаются до 1 пикселя из-за стилей интерфейса.

#### Стабильность SD WebUI Forge, защита зависимостей и диагностика запуска

- **Обратимая фиксация версий библиотек SD Forge (`PIP_CONSTRAINT`):** при применении патча программа фиксирует эталонные версии ключевых пакетов (`fastapi==0.104.1`, `starlette==0.27.0`, `pydantic==2.8.2`, `pydantic-core==2.20.1`, `gradio==4.40.0`, `gradio-client==1.2.0`, `anyio==3.7.1`, `typing-extensions==4.16.0`) через `neonroute_constraints.txt` и `webui-user.bat`. Это защищает окружение от поломки при ручном запуске или установке сторонних расширений, при этом откат патча полностью возвращает исходное состояние.
- **Умная проверка эталонного SD Forge:** если установлена эталонная сборка с правильными версиями библиотек, программа больше не предлагает навязчиво применить патч при каждом открытии настроек. Добавлен чекбокс, позволяющий запомнить выбор для конкретной папки.
- **Чистое окружение запуска и понятная диагностика ошибок Forge:** при старте Forge гарантированно очищаются системные прокси-переменные, выставляется `NO_PROXY` для локального хоста и подхватывается `PIP_CONSTRAINT`. Если веб-сервер Gradio поднялся, но API `/sdapi/v1/*` не успел зарегистрироваться (`HTTP 404`) или порт недоступен, в лог пишется точная диагностическая причина, а процесс штатно завершается без висящих дескрипторов.
- **Гибкий поиск папок Forge и поддержка NTFS Junctions:** валидатор ассетов избавлен от жёсткой привязки к папке `webui_forge_cu121_torch231`, поддерживает разные структуры папок Forge, аргументы лаунчера (`--ckpt-dir`, `--models-dir`) и симлинки / NTFS Junctions при переносе моделей на другие диски.
- **Переключатель для флага `--ad-no-huggingface`:** в меню настроек SD Forge появился тумблер управления запретом автоскачивания моделей ADetailer с Hugging Face. По умолчанию он выключен — при отсутствии модели лица или рук Forge спокойно докачает её сам; при включении флаг запрещает обращения в сеть, и работа идёт строго по локальным файлам.
- **Мгновенное сохранение тумблеров без кнопки «Сохранить»:** переключение «Квантомании» (FP8) и запрета скачивания с Hugging Face сохраняется сразу в момент клика. Настройки больше не теряются, если закрыть окно крестиком, кнопкой «Отмена» или клавишей `Esc`.
- **Защита от ложных срабатываний и гонки потоков:** открытие окна настроек больше не дёргает диск вхолостую благодаря блокировке сигналов при инициализации, а чтение и запись конфига защищены реентрантным мьютексом от одновременных обращений фоновых процессов и интерфейса.
- **Прозрачное логирование и актуализация документации:** в логах запуска Forge теперь чётко отражается состояние `--ad-no-huggingface` и статус «Квантомании» (`ВКЛ fp8e4` / `ВЫКЛ`); документ `ARCHITECTURE.md` обновлён под все изменения и актуальный тестовый сьют (`926 тестов`, `100% Pass Rate`).

---

### Технический список изменений (Changelog)

- **Превью изображений и гигиена курсоров (`ui/components/chat_bubble.py`):**
  - В `_open_image_preview` добавлена привязка к родительскому окну `parent=self.window()`, опциональный параметр `generation_params` с фолбэком на `self.generation_params`, а вызов `dlg.exec()` обёрнут в `try...finally` со строгим `dlg.deleteLater()` и сбросом override-курсоров через `QApplication.restoreOverrideCursor()` и `parent_win.unsetCursor()`.
  - В `_create_image_thumbnail` прямая лямбда с вызовом `ImagePreviewDialog(...).exec()` заменена на централизованный вызов `_open_image_preview` через `QTimer.singleShot(0, ...)`.
  - В `_setup_ui` лямбда-обработчик `mousePressEvent` заменён на явную функцию `_on_preview_mouse_press` с возвратом `None` при клике ЛКМ (исключает `TypeError: invalid argument to sipBadCatcherResult()` в PyQt6).
- **Диалог и запуск мобильного сервера (`ui/dialogs/mobile_server_dialog.py`, `ui/dialogs/__init__.py`, `mobile_server/api.py`):**
  - `MobileServerDialog` переведён с дочерней маски `MessageBoxBase` (`MaskDialogBase`) на `FramelessDialog` с `FluentTitleBar`, селектором IP через `ComboBox`, динамическим QR-кодом и безопасным жизненным циклом; добавлена регистрация в `ui/dialogs/_LAZY_EXPORTS`.
  - В `start_mobile_server_thread` во время ожидания порта внедрена прокачка очереди событий через `qapp.processEvents()` вместо блокирующего сна `time.sleep(0.05)`.
- **Синхронизация геометрии спинбоксов (`ui/common/spinbox_utils.py`):**
  - Реализованы функция `sync_spinbox_lineedit_geometry`, миксин `SpinBoxGeometrySyncMixin` и фильтр событий `SpinBoxGeometryEventFilter`, гарантирующие `lineEdit().width() >= 24` под QSS `qfluentwidgets`.
- **Обратимая фиксация библиотек и инспекция Forge (`services/forge_patcher.py`, `ui/dialogs/forge_settings_dialog.py`):**
  - Введён словарь `PINNED_FORGE_LIBRARIES` (8 пакетов: `fastapi==0.104.1`, `starlette==0.27.0`, `pydantic==2.8.2`, `pydantic-core==2.20.1`, `gradio==4.40.0`, `gradio-client==1.2.0`, `anyio==3.7.1`, `typing-extensions==4.16.0`).
  - В `apply_patch()` добавлены сохранение оригинального `requirements_versions.txt` в `.neonroute_backup/original/` (`create_backup()`), генерация `neonroute_constraints.txt`, обновление `requirements_versions.txt` и инъекция `set "PIP_CONSTRAINT=%~dp0neonroute_constraints.txt"` в `webui-user.bat` (`pin_libraries()`).
  - В `restore_backup()` реализован полный обратимый откат `requirements_versions.txt`, `webui-user.bat` и удаление `neonroute_constraints.txt`.
  - В `check_status()` добавлена проверка `are_libraries_pinned()` и распознавание эталонных сборок по `.dist-info` в `site-packages`; добавлено единое логирование этапов с префиксом `[ForgePatcher]`.
  - В `ForgeSettingsDialog` добавлен чекбокс игнорирования повторных уведомлений о патче для конкретной папки, внедрён тумблер `switch_ad_no_hf` с мгновенным автосохранением (`switch_fp8` и `switch_ad_no_hf` пишут напрямую через `ConfigManager.update_setting` и `parent_win.app_config`) и изоляцией сигналов через `blockSignals(True/False)` в `_sync_with_current_config`.
- **Изоляция окружения и диагностика запуска Forge (`core/forge_locator.py`, `services/process_manager.py`):**
  - В `get_clean_forge_env()` подтверждена полная очистка прокси-переменных (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `FTP_PROXY`, `AG_LS_PROXY` в обоих регистрах), установка `NO_PROXY`/`no_proxy="localhost,127.0.0.1,0.0.0.0"`, `PYTHONUNBUFFERED="1"`, очистка путей Python NeonRoute из `PATH` и передача `PIP_CONSTRAINT` при наличии `neonroute_constraints.txt`.
  - В `verify_forge_http_handshake()` добавлено диагностическое логирование `[ForgeHandshake]` при `HTTP 404 Not Found` (веб-сервер активен, но API-роутер `/sdapi/v1/*` не зарегистрирован) и недоступности порта; при истечении `startup_timeout` выполняется зондирующий опрос с записью точной причины в `neonroute.log` (`WARNING`), передачей в сигналы `forge_status_changed` / `action_completed`, штатным вызовом `self._run_forge_stop()`, закрытием `_forge_log_file` и сбросом `_forge_process` в `None`.
- **Каскадный валидатор ассетов и разрешение путей (`services/asset_validator.py`):**
  - Удалена жёсткая привязка к `webui_forge_cu121_torch231`; внедрён каскадный поиск корня Forge (`resolve_forge_root()`), рабочей директории `webui` (5 уровней фоллбэка, включая `webui*/webui`), лаунчера (`resolve_forge_launcher()`) и парсинг CLI-аргументов `--ckpt-dir` / `--models-dir` (`parse_forge_launcher_args`).
  - Добавлена нормализация путей через `.resolve()` для корректной работы с симлинками и NTFS Junctions; добавлен метод `find_adetailer_model(model_name)` и ключ `resolved_paths` в `validate_all_assets()` при сохранении обратной совместимости публичного API и ветвлении отчёта по `forge_ad_no_huggingface`.
- **Потокобезопасность конфига, флаги и логирование (`utils/config_manager.py`, `config.py`, `ui/dialogs/dependency_dialog.py`, `services/art_director/vram_policy.py`, `director.py`, `services/forge_client.py`):**
  - Класс `ConfigManager` оснащён реентрантным мьютексом `threading.RLock()` для `load_config`, `save_config` и `update_setting`; добавлены `forge_ad_no_huggingface: False`, `forge_patch_ignored_paths` и `check_dependencies_on_startup`; в `dependency_dialog.py` добавлен динамический `get_config_path()` и сохранение `cb_dont_show`.
  - Из `resolve_forge_startup_flags` удалён статический хардкод `--ad-no-huggingface`, добавлен параметр `ad_no_huggingface` с фолбэком на `ConfigManager` и фиксацией в `startup_meta`.
  - В `process_manager.py`, `forge_client.py` и `art_director/director.py` добавлено логирование состояния `ad_no_huggingface` и статуса `[Квантомания: ВКЛ (fp8e4) / ВЫКЛ]`.
- **Тестирование и документация (`tests/`, `ARCHITECTURE.md`):**
  - Добавлены и расширены модульные и смоук-тесты (`tests/unit/test_asset_validator.py`, `tests/smoke/test_forge_settings_dialog_smoke.py`, `tests/unit/test_forge_vram_policy.py`).
  - Полный прогон `pytest -q`: **926 passed в 99 модулях (100% Pass Rate)**; актуализированы разделы `3.2.4`, `4.4.1`, системный инвариант 10 и метрики тестов в `ARCHITECTURE.md`.
---

### Технический список изменений (Changelog)

- **Превью изображений и гигиена курсоров (`ui/components/chat_bubble.py`):**
  - В `_open_image_preview` добавлена привязка к родительскому окну `parent=self.window()`, опциональный параметр `generation_params` с фолбэком на `self.generation_params`, а вызов `dlg.exec()` обёрнут в `try...finally` со строгим `dlg.deleteLater()` и сбросом override-курсоров через `QApplication.restoreOverrideCursor()` и `parent_win.unsetCursor()`.
  - В `_create_image_thumbnail` прямая лямбда с вызовом `ImagePreviewDialog(...).exec()` заменена на централизованный вызов `_open_image_preview` через `QTimer.singleShot(0, ...)`.
  - В `_setup_ui` лямбда-обработчик `mousePressEvent` заменён на явную функцию `_on_preview_mouse_press` с возвратом `None` при клике ЛКМ (исключает `TypeError: invalid argument to sipBadCatcherResult()` в PyQt6).
- **Диалог и запуск мобильного сервера (`ui/dialogs/mobile_server_dialog.py`, `ui/dialogs/__init__.py`, `mobile_server/api.py`):**
  - `MobileServerDialog` переведён с дочерней маски `MessageBoxBase` (`MaskDialogBase`) на `FramelessDialog` с `FluentTitleBar`, селектором IP через `ComboBox`, динамическим QR-кодом и безопасным жизненным циклом; добавлена регистрация в `ui/dialogs/_LAZY_EXPORTS`.
  - В `start_mobile_server_thread` во время ожидания порта внедрена прокачка очереди событий через `qapp.processEvents()` вместо блокирующего сна `time.sleep(0.05)`.
- **Синхронизация геометрии спинбоксов (`ui/common/spinbox_utils.py`):**
  - Реализованы функция `sync_spinbox_lineedit_geometry`, миксин `SpinBoxGeometrySyncMixin` и фильтр событий `SpinBoxGeometryEventFilter`, гарантирующие `lineEdit().width() >= 24` под QSS `qfluentwidgets`.
- **Обратимая фиксация библиотек и инспекция Forge (`services/forge_patcher.py`, `ui/dialogs/forge_settings_dialog.py`):**
  - Введён словарь `PINNED_FORGE_LIBRARIES` (8 пакетов: `fastapi==0.104.1`, `starlette==0.27.0`, `pydantic==2.8.2`, `pydantic-core==2.20.1`, `gradio==4.40.0`, `gradio-client==1.2.0`, `anyio==3.7.1`, `typing-extensions==4.16.0`).
  - В `apply_patch()` добавлены сохранение оригинального `requirements_versions.txt` в `.neonroute_backup/original/` (`create_backup()`), генерация `neonroute_constraints.txt`, обновление `requirements_versions.txt` и инъекция `set "PIP_CONSTRAINT=%~dp0neonroute_constraints.txt"` в `webui-user.bat` (`pin_libraries()`).
  - В `restore_backup()` реализован полный обратимый откат `requirements_versions.txt`, `webui-user.bat` и удаление `neonroute_constraints.txt`.
  - В `check_status()` добавлена проверка `are_libraries_pinned()` и распознавание эталонных сборок по `.dist-info` в `site-packages`; добавлено единое логирование этапов с префиксом `[ForgePatcher]`.
  - В `ForgeSettingsDialog` добавлен чекбокс игнорирования повторных уведомлений о патче для конкретной папки, внедрён тумблер `switch_ad_no_hf` с мгновенным автосохранением (`switch_fp8` и `switch_ad_no_hf` пишут напрямую через `ConfigManager.update_setting` и `parent_win.app_config`) и изоляцией сигналов через `blockSignals(True/False)` в `_sync_with_current_config`.
- **Изоляция окружения и диагностика запуска Forge (`core/forge_locator.py`, `services/process_manager.py`):**
  - В `get_clean_forge_env()` подтверждена полная очистка прокси-переменных (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `FTP_PROXY`, `AG_LS_PROXY` в обоих регистрах), установка `NO_PROXY`/`no_proxy="localhost,127.0.0.1,0.0.0.0"`, `PYTHONUNBUFFERED="1"`, очистка путей Python NeonRoute из `PATH` и передача `PIP_CONSTRAINT` при наличии `neonroute_constraints.txt`.
  - В `verify_forge_http_handshake()` добавлено диагностическое логирование `[ForgeHandshake]` при `HTTP 404 Not Found` (веб-сервер активен, но API-роутер `/sdapi/v1/*` не зарегистрирован) и недоступности порта; при истечении `startup_timeout` выполняется зондирующий опрос с записью точной причины в `neonroute.log` (`WARNING`), передачей в сигналы `forge_status_changed` / `action_completed`, штатным вызовом `self._run_forge_stop()`, закрытием `_forge_log_file` и сбросом `_forge_process` в `None`.
- **Каскадный валидатор ассетов и разрешение путей (`services/asset_validator.py`):**
  - Удалена жёсткая привязка к `webui_forge_cu121_torch231`; внедрён каскадный поиск корня Forge (`resolve_forge_root()`), рабочей директории `webui` (5 уровней фоллбэка, включая `webui*/webui`), лаунчера (`resolve_forge_launcher()`) и парсинг CLI-аргументов `--ckpt-dir` / `--models-dir` (`parse_forge_launcher_args`).
  - Добавлена нормализация путей через `.resolve()` для корректной работы с симлинками и NTFS Junctions; добавлен метод `find_adetailer_model(model_name)` и ключ `resolved_paths` в `validate_all_assets()` при сохранении обратной совместимости публичного API и ветвлении отчёта по `forge_ad_no_huggingface`.
- **Потокобезопасность конфига, флаги и логирование (`utils/config_manager.py`, `config.py`, `ui/dialogs/dependency_dialog.py`, `services/art_director/vram_policy.py`, `director.py`, `services/forge_client.py`):**
  - Класс `ConfigManager` оснащён реентрантным мьютексом `threading.RLock()` для `load_config`, `save_config` и `update_setting`; добавлены `forge_ad_no_huggingface: False`, `forge_patch_ignored_paths` и `check_dependencies_on_startup`; в `dependency_dialog.py` добавлен динамический `get_config_path()` и сохранение `cb_dont_show`.
  - Из `resolve_forge_startup_flags` удалён статический хардкод `--ad-no-huggingface`, добавлен параметр `ad_no_huggingface` с фолбэком на `ConfigManager` и фиксацией в `startup_meta`.
  - В `process_manager.py`, `forge_client.py` и `art_director/director.py` добавлено логирование состояния `ad_no_huggingface` и статуса `[Квантомания: ВКЛ (fp8e4) / ВЫКЛ]`.
- **Тестирование и документация (`tests/`, `ARCHITECTURE.md`):**
  - Добавлены и расширены модульные и смоук-тесты (`tests/unit/test_asset_validator.py`, `tests/smoke/test_forge_settings_dialog_smoke.py`, `tests/unit/test_forge_vram_policy.py`).
  - Полный прогон `pytest -q`: **926 passed в 99 модулях (100% Pass Rate)**; актуализированы разделы `3.2.4`, `4.4.1`, системный инвариант 10 и метрики тестов в `ARCHITECTURE.md`.

## v0.9.8.5-beta (2026-11-21)

> **Ключевое обновление:** устранение вылета при скачивании SD WebUI Forge с автозащитой от несуществующих дисков, безопасное логирование рантайма `pythonw.exe` (`crash.log`) и полная ликвидация зависаний интерфейса в «Модели генерации».

### Что изменилось

#### Стабильность загрузки SD WebUI Forge и защита от несуществующих дисков

- **Устранение вылета при нажатии «Скачать и установить SD Forge»:** локализован и исправлен критический сбой в окне `ForgeSettingsDialog`. Фоновый поток `ForgeDownloadWorker` переведён на безопасную изоляцию (`parent=None`) с корректной отпиской и переподключением сигналов при повторном открытии окна.
- **Автоматическая защита от несуществующих томов диска (`[WinError 3]`):** перед стартом установки проверяется физическое наличие выбранного тома (например, отсутствующего диска `D:\`). Если том недоступен, загрузчик автоматически переключается на безопасный путь на системном диске `C:\` вместо аварийного падения программы.

#### Гигиена рантайма `pythonw.exe` и прозрачное логирование сбоев

- **Защита от падений при запуске через `run.bat` (`pythonw.exe`):** устранены ошибки `FastEmbed` (`'NoneType' object has no attribute 'write'`) и тихие вылеты в оконном режиме без консоли за счёт перенаправления `sys.stdout` в `logs/stdout.log` и `sys.stderr` в `logs/stderr.log`.
- **Дампы сбоев всех потоков (`logs/crash.log`):** настроены перехватчики `sys.excepthook`, `threading.excepthook` и `faulthandler.enable()` — при любом непредвиденном сбое в файл `logs/crash.log` гарантированно записывается полный стектрейс всех активных потоков.

#### Отзывчивый интерфейс в «Модели генерации» и безопасный жизненный цикл потоков

- **Ликвидация мёртвых зависаний при сохранении моделей:** синхронный пересчёт эмбеддингов `FastEmbed` полностью вынесен из главного GUI-потока в неблокирующий фоновый режим (`update_embeddings_async`). При закрытии окна через «Отмена» или крестик сохраняется строгая синхронизация профилей в памяти.
- **Защита от гонок в `ModelRouter`:** внедрена коалесценция запросов пересчёта векторов, исключающая взаимные блокировки (`deadlock`) при фоновом прогреве роутера; подмена векторов выполняется атомарно.
- **Разгрузка интерфейса при FP8-квантовании и единый жизненный цикл воркеров:** сетевой запрос обновления чекпоинтов Forge перенесён из UI-слота в фоновый поток `FP8ConvertWorker.run()`, все рабочие потоки менеджера моделей приведены к созданию с `parent=None`, очистке по сигналу `finished` и гарантированному сбросу курсора ожидания при закрытии диалога.
- **Сквозная диагностика отправки сообщений (`[SendFlow]`):** добавлена единая телеметрия прохождения запросов пользователя до постановки в очередь задач и защитный барьер перехвата исключений при нажатии кнопки отправки.
- **Актуализация спецификации и верификация:** в `ARCHITECTURE.md` задокументированы правила гигиены `pythonw.exe`, валидации дисков Forge, телеметрии `[SendFlow]` и жизненного цикла воркеров; полный тестовый сьют расширен до `861 passed` в `93` модулях (`100% Pass Rate`).

---

### Технический список изменений (Changelog)

- **Загрузчик Forge и диалог настроек (`services/forge_downloader.py`, `ui/dialogs/forge_settings_dialog.py`):**
  - Реализованы функции `validate_or_fallback_forge_dir()` и `get_default_forge_install_dir()` с проверкой существования `anchor` тома диска и безопасным фоллбэком на `C:\` при отсутствии целевого тома.
  - Переведён `ForgeDownloadWorker` на потокобезопасную инициализацию (`parent=None`), метод `get_active_instance()` и троттлинг сигналов `0.12 с`.
  - В `ForgeSettingsDialog` добавлена безопасная отписка и переподключение сигналов в `closeEvent`, `reject`, `accept`, исключены прямые вызовы виджетов из фонового потока.
- **Гигиена рантайма и логирование (`main.py`):**
  - Реализована функция `setup_environment_and_logging()` с проверкой `sys.stdout` / `sys.stderr` на `None` и перенаправлением в `logs/stdout.log` и `logs/stderr.log`.
  - Включены `faulthandler.enable()`, `sys.excepthook` и `threading.excepthook` с записью дампов в `logs/crash.log`.
- **Арт-директор и роутер моделей (`services/art_director/director.py`, `services/model_router.py`):**
  - В `ArtDirector.reload_models()` синхронный вызов `update_embeddings()` заменён на `self.router.load_profiles()` + `self.router.update_embeddings_async(delay=0.0)`.
  - В `ModelRouter` добавлена защита от гонок через `_embed_calc_lock`, флаги `_update_in_progress` / `_rerun_requested`, кэш дескрипторов `_last_computed_descriptors` и атомарная подмена `cached_embeddings`.
- **Менеджер моделей (`ui/models_manager_dialog.py`, `ui/models_manager/mixins.py`, `ui/models_manager/workers.py`):**
  - Метод `ModelsManagerDialog._save_all()` переведён на `update_embeddings_async(delay=0.0)`.
  - В `FP8ConversionMixin._update_fp8_button_state()` проверка состояния кнопки переведена на $O(1)$ строковые операции без вызова `subprocess.run` в GUI; `self.forge_client.refresh_checkpoints()` перенесён в `FP8ConvertWorker.run()`.
  - Воркеры `ForgeScanWorker`, `CivitaiParamsWorker`, `FP8ConvertWorker`, `RoutingTestWorker` переведены на создание с `parent=None`, очистку ссылок по сигналу `finished` и `deleteLater`; в `register_orphaned_worker()` добавлено подключение `deleteLater`, а в `_stop_all_workers()` — гарантированный сброс `WaitCursor`.
- **Диспетчеризация чата (`ui/controllers/chat_flow_controller.py`, `task_lifecycle_controller.py`, `assistant_flow_controller.py`):**
  - Добавлена сквозная телеметрия `[SendFlow]` в `_on_send_clicked`, `_route_and_enqueue` и постановке задач в `AssistantFlowControllerMixin`, а также защитный барьер `try...except` в `_on_send_clicked`.
- **Тестирование и документация (`tests/`, `ARCHITECTURE.md`):**
  - Добавлены тесты `tests/smoke/test_dialog_sync_smoke.py` (5 смоук-тестов), `tests/unit/test_logging_hygiene.py` (6 юнит-тестов) и тесты валидации томов в `tests/unit/test_forge_downloader.py`.
  - Полный прогон `pytest -q`: **861 passed в 93 модулях (100% Pass Rate)**; обновлены разделы `2.1`, `3.2.1`, `5.2` и `5.4` в `ARCHITECTURE.md`.

## v0.9.8.4-beta (2026-11-21)

> **Ключевое обновление:** встроенный автозагрузчик и установщик SD WebUI Forge (`ADR-50`), 3-слойный раздельный промптинг ADetailer через Structured JSON Output (`ADR-49`) и поддержка старых процессоров без инструкций AVX2.

### Что изменилось

#### Автоустановка SD WebUI Forge и генерации картинок «из коробки» (`ADR-50`)

- **Встроенный загрузчик SD WebUI Forge:** больше не нужно вручную искать, скачивать и распаковывать сборки Forge на GitHub. Прямо в окне настроек генерации картинок (и при первом онбординге) появилась кнопка скачивания с проверкой свободного места на диске (`12 ГБ` / `14.5 ГБ` с базовой моделью), прогресс-баром, скоростью загрузки и поддержкой докачки при обрыве связи.
- **Каскадная распаковка и безопасный лаунчер (`forge_launcher.bat`):** распаковка 7z-архива работает через встроенный в Windows `tar.exe` (без всплывающих окон консоли), установленный `7z.exe` или встроенную библиотеку `py7zr`. После распаковки автоматически создаётся `forge_launcher.bat`, корректно пробрасывающий аргументы запуска и изолирующий окружение.
- **Автоподхват стартовой модели DreamShaper 8 и детекторов ADetailer:** при установке автоматически скачиваются и регистрируются стартовый чекпоинт `dreamshaper_8.safetensors` (с активацией по умолчанию) и детекторы `face_yolov8m.pt` + `hand_yolov9c.pt` — генерация картинок и коррекция лиц/рук работают сразу после установки.
- **Корректное завершение при выходе:** если пользователь закрывает NeonRoute во время скачивания Forge, фоновый загрузчик аккуратно останавливается до выгрузки процессов.

#### Арт-директор и 3-слойный ADetailer (`ADR-49`)

- **Раздельный 3-слойный промптинг ADetailer (`Scene`, `Face`, `Hands`):** устранено взаимное загрязнение кропов лица и кистей рук общим промптом сцены. Слоты детекции и инпейнта Forge теперь получают строго изолированные наборы тегов с физическим разделением визуальной ДНК персонажа.
- **Схемы Structured JSON Output для Арт-директора:** директивы `gallery` и `cinematic` переведены на генерацию строго валидного JSON через `response_format`, возвращающего изолированные поля `scene_prompt`, `face_prompt`, `hands_prompt`, `negative_prompt`, `aspect_ratio` и `reply_ru`.
- **Поддержка пропуска слотов (`[SKIP]`):** пустые строки в `face_prompt` и `hands_prompt` распознаются как явная директива `[SKIP]` и транслируются в `ad_model="None"`, предотвращая артефактный инпейнт пейзажей или персонажей со спины.
- **Потоковое извлечение ответа (`StreamArtTagFilter`):** фраза ассистента `reply_ru` декодируется и печатается в бабле чата в реальном времени во время генерации JSON с поддержкой escape-последовательностей и полной изоляцией служебных ключей до завершения потока.
- **Анатомическая защита кистей и изоляция ДНК:** слот лица изолирует `ELARIA_VISUAL_DNA` и companion DNA, а слот рук исключает описание лица/фона и автоматически дополняет негативный промпт анатомическим стеком (`bad hands`, `mutated hands`, `extra fingers` и др.).
- **Диагностическое логирование и паспорт слотов:** добавлены теги источников `[src=json|override|fallback]` для слотов Face и Hands, статус `SKIPPED` при `[SKIP]` и маркировка `SlotN(SKIPPED)` в `slots_summary` клиента Forge.

#### Совместимость с железом и движок `llama-server`

- **Поддержка процессоров без AVX2 (режим совместимости):** добавлена автоматическая проверка поддержки инструкций AVX2 на Windows. Если процессор их не поддерживает, загрузчик исключает несовместимые сборки (`avx2`, `avx512`) и выбирает безопасные CPU-бинарники с приоритетом `noavx` → `avx` → базовый `cpu`.
- **Универсальная верификация бинарника и понятная диагностика сбоев:** пробный запуск с флагом `--list-devices` теперь выполняется для всех типов сборок, а не только для CUDA. При падении процесса с кодом `0xC000001D` (`STATUS_ILLEGAL_INSTRUCTION`) вместо сухой ошибки возвращается ясное диагностическое сообщение о несовместимости с AVX2.
- **Автоматический откат с Vulkan на совместимую CPU-сборку:** если сборка `vulkan` завершается с ошибкой `0xC000001D`, загрузчик автоматически очищает старые `.dll` в целевой папке, скачивает и распаковывает совместимую CPU-сборку без AVX2 и повторно запускает проверку работоспособности.
- **Адаптивный Flash Attention и индикация в UI:** дефолтное состояние Flash Attention в настройках движка больше не захардкожено и подтягивается из аппаратного профиля оборудования, а в карточке железа для AMD, Intel и CPU-режима при отсутствии AVX2 выводится пометка `CPU без AVX2 (режим совместимости)`.
- **Актуализация спецификации и верификация:** в `ARCHITECTURE.md` задокументированы решения `ADR-49` и `ADR-50`, добавлены разделы `3.2.1` и `6.7`; полный тестовый сьют проекта: `838 passed` в `pytest -q` (100% Pass Rate).

---

### Технический список изменений (Changelog)

- **Встроенный загрузчик и конвейер установки Forge (`services/forge_downloader.py`, `requirements.txt`):**
  - Реализованы dataclass `ForgeInstallSpec` и функция `resolve_forge_hardware_spec()` (детекция NVIDIA GPU, `VRAM >= 3800 МБ`, флаг Blackwell `CC >= 10.0` / RTX 50xx, авторезолв релиза `cu121` через GitHub API с fallback).
  - Реализована каскадная распаковка `extract_7z_archive()` (`tar.exe` → `7z.exe` → `py7zr` с флагом `CREATE_NO_WINDOW`), верификация корня `resolve_extracted_forge_root()` и генератор лаунчера `create_forge_launcher_bat()` с пробросом `%*`.
  - Реализован конвейер `install_forge_pipeline()` (проверка свободного места на диске, 4 этапа установки, best-effort `ForgePatcher.apply_patch()`, скачивание детекторов `face_yolov8m.pt` / `hand_yolov9c.pt` и авто-активация `dreamshaper_8.safetensors` в `data/art_director_models.json` с `is_default: True`).
  - Реализован фоновый воркер `ForgeDownloadWorker(QThread)` и синглтон `get_active_forge_download_worker()` с троттлингом прогресса `0.12 с` и расчётом скорости по дельте байт; добавлена зависимость `py7zr>=0.20.0`.
- **UI настроек Forge, онбординг и корректное завершение (`ui/dialogs/forge_settings_dialog.py`, `ui/dialogs/forge_onboarding_dialog.py`, `ui/controllers/hardware_engine.py`, `ui/workers/shutdown_worker.py`):**
  - В `ForgeSettingsDialog` добавлен блок автоустановки (`lbl_gpu_spec`, `chk_download_dreamshaper`, `btn_download_forge`, `btn_cancel_forge_dl`, `forge_progress_bar`, `lbl_download_status`), поддержка `auto_start_download=True`, безопасная отписка от сигналов воркера в `closeEvent`, `reject`, `accept` и переподключение к активной загрузке при повторном открытии окна.
  - В онбординге замена внешнего перехода на GitHub на запуск встроенного диалога `ForgeSettingsDialog(parent=self, auto_start_download=True)`.
  - В `ShutdownWorker` добавлена отмена активного `ForgeDownloadWorker` перед выгрузкой VRAM и завершением процессов.
- **Детекция AVX2, верификация и фоллбэк `llama-server` (`services/layer_budgeter.py`, `services/llama_downloader.py`, `ui/dialogs/llm_engine_settings_dialog.py`):**
  - В `LayerBudgeter` добавлен статический метод `has_cpu_avx2() -> bool` (`IsProcessorFeaturePresent(40)` на Windows) и ключ `"cpu_avx2"` в `detect_hardware_kv_profile()`.
  - В `select_download_urls(assets, build_type, cpu_avx2=None)` при `cpu_avx2 is False` исключаются сборки `avx2` / `avx512` и применяется приоритет `noavx` → `avx` → `cpu`.
  - В `verify_llama_server_binary(target_dir)` пробный запуск `[str(exe_path), "--list-devices"]` распространён на все типы сборок с диагностикой `0xC000001D` (`STATUS_ILLEGAL_INSTRUCTION`) и автоматическим откатом `vulkan` → `cpu` без AVX2 в `download_and_extract_llama_server(...)`.
  - В `LLMEngineSettingsDialog` дефолтный Flash Attention считывается из `self.hw_profile.get("recommended_fa", False)`, добавлена индикация `• CPU без AVX2 (режим совместимости)`.
- **3-слойный ADetailer и Structured JSON Output (`services/art_director/contracts.py`, `plan.py`, `director.py`, `services/assistant_prompt_builder.py`, `services/assistant_flow.py`):**
  - В `VisualIntent` добавлены поля `face_prompt` и `hands_prompt` с поддержкой `[SKIP]` для пустых строк и строковыми методами совместимости; в `GenerationPlan` разделены `face_slot` и `hand_slot` с трансляцией `[SKIP]` в `ad_model="None"`.
  - В `ArtDirector` реализована независимая сборка `resolved_face_prompt` (с `ELARIA_VISUAL_DNA` / companion DNA и LoRA #1) и `resolved_hands_prompt` (с анатомическим стеком в негативе) и диагностика `[src=json|override|fallback]`.
  - Объявлены схемы `ART_DIRECTOR_ASSISTANT_JSON_SCHEMA` и `ART_DIRECTOR_RP_SCENE_JSON_SCHEMA`, реализован инкрементальный потоковый парсер `reply_ru` в `StreamArtTagFilter` и 4-фазное восстановление JSON в `parse_structured_art_response`.
- **Контроллеры, Forge и мобильный шлюз (`ui/controllers/assistant_flow_controller.py`, `services/rp_cast_extractor.py`, `services/roleplay_flow_controller.py`, `services/forge_client.py`, `mobile_server/services/chat_service.py`):**
  - Обеспечена сквозная передача `response_format` в `lm_client` и `stream_chat_completion`, безопасное извлечение `session_id` в `AssistantFlowController`, поддержка Structured JSON в арт-сценах ролевого режима и маркировка `SlotN(SKIPPED)` в `ForgeClient`.
- **Тестирование и документация (`tests/unit/`, `ARCHITECTURE.md`):**
  - Добавлены юнит-тесты `tests/unit/test_forge_downloader.py` (11 тестов), тесты AVX2-фоллбэка в `tests/unit/test_llama_downloader.py` и тесты 3-слойного ADetailer в `tests/unit/test_adetailer_dialog.py`.
  - Итоговый прогон `pytest -q`: **838 passed (100% Pass Rate)**; в `ARCHITECTURE.md` добавлены блоки `ADR-49`, `ADR-50` и разделы `3.2.1`, `6.7`.

## v0.9.8.3-beta (2026-11-21)

> **Ключевое обновление:** внедрение 3-слойного раздельного промптинга ADetailer через Structured JSON Output (`ADR-49`) и поддержка процессоров без инструкций AVX2.

### Что изменилось

#### Совместимость с железом и движок `llama-server`
* **Поддержка процессоров без AVX2 (режим совместимости):** добавлена автоматическая проверка поддержки инструкций AVX2 на Windows. Если процессор их не поддерживает, загрузчик исключает несовместимые сборки (`avx2`, `avx512`) и выбирает безопасные CPU-бинарники с приоритетом `noavx` → `avx` → базовый `cpu`.
* **Универсальная верификация бинарника и понятная диагностика сбоев:** пробный запуск с флагом `--list-devices` теперь выполняется для всех типов сборок, а не только для CUDA. При падении процесса с кодом `0xC000001D` (`STATUS_ILLEGAL_INSTRUCTION`) вместо сухой ошибки возвращается ясное диагностическое сообщение о несовместимости с AVX2.
* **Автоматический откат с Vulkan на совместимую CPU-сборку:** если сборка `vulkan` завершается с ошибкой `0xC000001D`, загрузчик автоматически очищает старые `.dll` в целевой папке, скачивает и распаковывает совместимую CPU-сборку без AVX2 и повторно запускает проверку работоспособности.
* **Адаптивный Flash Attention и индикация в UI:** дефолтное состояние Flash Attention в настройках движка больше не захардкожено и подтягивается из аппаратного профиля оборудования, а в карточке железа для AMD, Intel и CPU-режима при отсутствии AVX2 выводится пометка `CPU без AVX2 (режим совместимости)`.
* **Покрытие тестами и обратная совместимость:** изменения выполнены точечно с сохранением всех существующих сигнатур; логика подбора, отлова `0xC000001D` и фоллбэка покрыта юнит-тестами (полный прогон: `824 passed` в `pytest -q`, 100% Pass Rate).

#### Арт-директор и 3-слойный ADetailer (`ADR-49`)
* **Раздельный 3-слойный промптинг ADetailer (`Scene`, `Face`, `Hands`):** устранено взаимное загрязнение кропов лица и кистей рук общим промптом сцены. Слоты детекции и инпейнта Forge теперь получают строго изолированные наборы тегов с физическим разделением визуальной ДНК персонажа.
* **Схемы Structured JSON Output для Арт-директора:** директивы `gallery` и `cinematic` переведены на генерацию строго валидного JSON через `response_format`, возвращающего изолированные поля `scene_prompt`, `face_prompt`, `hands_prompt`, `negative_prompt`, `aspect_ratio` и `reply_ru`.
* **Поддержка пропуска слотов (`[SKIP]`):** пустые строки в `face_prompt` и `hands_prompt` распознаются как явная директива `[SKIP]` и транслируются в `ad_model="None"`, предотвращая артефактный инпейнт пейзажей или персонажей со спины.
* **Потоковое извлечение ответа (`StreamArtTagFilter`):** фраза ассистента `reply_ru` декодируется и печатается в бабле чата в реальном времени во время генерации JSON с поддержкой escape-последовательностей и полной изоляцией служебных ключей до завершения потока.
* **Анатомическая защита кистей и изоляция ДНК:** слот лица изолирует `ELARIA_VISUAL_DNA` и companion DNA, а слот рук исключает описание лица/фона и автоматически дополняет негативный промпт анатомическим стеком (`bad hands`, `mutated hands`, `extra fingers` и др.).
* **Диагностическое логирование и паспорт слотов:** добавлены теги источников `[src=json|override|fallback]` для слотов Face и Hands, статус `SKIPPED` при `[SKIP]` и маркировка `SlotN(SKIPPED)` в `slots_summary` клиента Forge.
* **Актуализация спецификации:** в `ARCHITECTURE.md` задокументировано решение `ADR-49`, добавлен раздел 6.7 и обновлены метрики верификации.

---

### Технический список изменений (Changelog)

* **Детекция AVX2 в аппаратных профилях (`services/layer_budgeter.py`):**
  * Добавлен статический метод `has_cpu_avx2() -> bool` (на Windows при `os.name == "nt"` вызывает `IsProcessorFeaturePresent(40)` для `PF_AVX2_INSTRUCTIONS_AVAILABLE` с безопасным перехватом исключений и возвратом `True`, на остальных ОС возвращает `True`).
  * Ключ `"cpu_avx2": cls.has_cpu_avx2()` добавлен в `detect_hardware_kv_profile()` для всех трёх профилей (Turing+/RTX, старые NVIDIA GPU и fallback-профиль AMD/Intel/CPU).
* **Подбор CPU-сборок без AVX2, верификация и фоллбэк (`services/llama_downloader.py`):**
  * В `select_download_urls(assets, build_type, cpu_avx2=None)` добавлен опциональный параметр `cpu_avx2` с резолвингом через `LayerBudgeter.has_cpu_avx2()`; при `cpu_avx2 is False` исключаются архивы с `avx2` и `avx512` в имени и применяется приоритет `noavx` → `avx` (без `avx2`/`avx512`) → нейтральный `cpu`.
  * В `verify_llama_server_binary(target_dir)` вызов `[str(exe_path), "--list-devices"]` распространён на все типы сборок; при `returncode in (3221225501, -1073741795)` (`0xC000001D` / `STATUS_ILLEGAL_INSTRUCTION`) формируется диагностическое сообщение о несовместимости с AVX2.
  * В `download_and_extract_llama_server(...)` реализован откат при падении `vulkan`-сборки с `0xC000001D`: очистка старых `.dll` в папке назначения, скачивание и распаковка `select_download_urls(assets, "cpu", cpu_avx2=False)` и повторная верификация.
* **Настройки и UI (`ui/dialogs/llm_engine_settings_dialog.py`):**
  * В `_load_current_settings()` значение Flash Attention по умолчанию считывается из `self.hw_profile.get("recommended_fa", False)` вместо жёсткого `True`.
  * В `_init_ui()` в карточке `hw_card` при `not self.hw_profile.get("cpu_avx2", True)` к `status_text` добавляется пометка `" • CPU без AVX2 (режим совместимости)"` для AMD, Intel и CPU-режима.
* **Контракт и план генерации (`services/art_director/contracts.py`, `services/art_director/plan.py`):**
  * В `VisualIntent` добавлены опциональные поля `face_prompt` и `hands_prompt` с поддержкой `[SKIP]` для пустых строк и строковыми методами совместимости (`__str__`, `lower`, `startswith`, `__contains__`).
  * В `GenerationPlan` разделены `face_slot` и `hand_slot` в `to_forge_kwargs()`, добавлена трансляция `[SKIP]` в `ad_model="None"`, а также поля `ad_source` и `adetailer_hands_source`.
* **Изоляция слотов в ArtDirector (`services/art_director/director.py`):**
  * Независимая сборка `resolved_face_prompt` (с `ELARIA_VISUAL_DNA` / companion DNA, guidance, LoRA #1) и `resolved_hands_prompt` (override модели > `hands_prompt` > fallback `final_positive`).
  * Автоматическое дополнение `resolved_hands_neg` анатомическими токенами; добавлен метод `_format_adetailer_log_blocks` с тегами источников и статусом `SKIPPED`.
* **Схемы и директивы (`services/assistant_prompt_builder.py`):**
  * Объявлены `ART_DIRECTOR_ASSISTANT_JSON_SCHEMA` и `ART_DIRECTOR_RP_SCENE_JSON_SCHEMA`; экранированы фигурные скобки и обновлены шаблоны `GALLERY_SKILL_DIRECTIVE_*` и `CINEMATIC_SKILL_DIRECTIVE_*`.
* **Потоковый фильтр и парсинг JSON (`services/assistant_flow.py`):**
  * В `StreamArtTagFilter` добавлены `_json_mode`, `_json_reply_emitted`, `_json_reply_closed` и инкрементальный парсер `reply_ru` с декодированием escape-символов.
  * Добавлена функция `parse_structured_art_response` с 4-фазным восстановлением JSON и диагностическим логированием (`[StructuredArt] JSON parsed successfully` / `warning fallback`).
* **Контроллеры, Forge и мобильный шлюз:**
  * `ui/controllers/assistant_flow_controller.py`, `services/rp_cast_extractor.py`, `services/roleplay_flow_controller.py`, `ui/controllers/hardware_engine.py`: передача `response_format` в `lm_client`, безопасное извлечение `session_id=curr_sess_id` через `getattr(self, "current_session", None)`, поддержка Structured JSON в генерации арт-сцен ролевого режима.
  * `services/forge_client.py`: форматирование `SlotN(SKIPPED)` в `slots_summary` при `ad_model == "None"`.
  * `mobile_server/services/chat_service.py`: передача `response_format` в `stream_chat_completion` при активных арт-скиллах.
* **Тестирование и документация (`tests/unit/`, `ARCHITECTURE.md`):**
  * `tests/unit/test_llama_downloader.py`: добавлены `test_select_download_urls_cpu_no_avx2_priority`, `test_verify_llama_server_binary_illegal_instruction` и `test_download_and_extract_vulkan_fallback_to_cpu_on_illegal_instruction`.
  * `tests/unit/test_adetailer_dialog.py`: добавлены тесты 3-слойного разделения, потокового извлечения `reply_ru` (`test_stream_filter_incremental_json_reply`) и диагностического логирования (`test_adetailer_diagnostic_logging`).
  * Итоговый прогон `pytest -q`: **824 passed (100% Pass Rate)**.
  * `ARCHITECTURE.md`: добавлен блок `ADR-49`, создан раздел 6.7 «Архитектура 3-слойного раздельного промптинга ADetailer через Structured JSON Output», обновлена сводка тестового сьюта.

---

## v0.9.8.2B-beta (2026-11-19)

* Устранена ошибка развёртки фона при нажатии на сгенерированную картинку.

## v0.9.8.2A-beta (2026-11-19)

* Скорректировано поведение памяти при использовании моделей.
* Доработана логика переключения SD Forge.

## v0.9.8.2-beta (2026-11-18)

* Доработан РП-чат (ход мыслей).
* Внесена корректировка в формулу подсчёта свободной VRAM.
* Добавлена иконка при запуске приложения.

## v0.9.8.1A (2026-11-17)

* Динамический выбор параметров `mmap` (`--load-mode none` vs `--no-mmap`) для `llama-server`.
* Отказоустойчивая двухфазная загрузка и валидация библиотек CUDA runtime (`cudart64` / `cublas64`).
* Автономный пакет хотфикса `hotfix_v098/` для пользователей релиза `v0.9.8-beta`.
* Интерактивное управление версиями и автоматическое ведение `hotfix.md` при сборке релиза.
* Исправление потокового парсинга блоков кода Инженера и синхронизация `thinking_mode`.
