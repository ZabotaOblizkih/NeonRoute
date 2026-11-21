# История изменений и релизов NeonRoute

## v0.9.8.4-beta (2026-11-21)

## v0.9.8.4-beta (2026-11-21)

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
