# История изменений и релизов NeonRoute

## v0.9.8.3-beta (2026-11-21)

Внедрение 3-слойного раздельного промптинга ADetailer через Structured JSON Output (ADR-49)
Что изменилось:
    Раздельный 3-слойный промптинг ADetailer (Scene, Face, Hands): устранено взаимное загрязнение кропов лица и кистей рук общим промптом сцены. Слоты детекции и инпейнта Forge теперь получают строго изолированные наборы тегов с физическим разделением визуальной ДНК персонажа.
    Схемы Structured JSON Output для Арт-директора: директивы gallery и cinematic переведены на генерацию строго валидного JSON через response_format, возвращающего изолированные поля scene_prompt, face_prompt, hands_prompt, negative_prompt, aspect_ratio и reply_ru.
    Поддержка пропуска слотов ([SKIP]): пустые строки в face_prompt и hands_prompt распознаются как явная директива [SKIP] и транслируются в ad_model="None", предотвращая артефактный инпейнт пейзажей или персонажей со спины.
    Потоковое инкрементальное извлечение ответа (StreamArtTagFilter): фраза ассистента reply_ru декодируется и печатается в бабле чата в реальном времени во время генерации JSON с поддержкой escape-последовательностей и полной изоляцией служебных ключей до завершения потока.
    Анатомическая защита кистей и изоляция ДНК: слот лица изолирует ELARIA_VISUAL_DNA и companion DNA, а слот рук исключает описание лица/фона и автоматически дополняет негативный промпт анатомическим стеком (bad hands, mutated hands, extra fingers...).
    Диагностическое логирование и паспорт слотов: добавлены теги источников [src=json|override|fallback] для слотов Face и Hands, статус SKIPPED при [SKIP] и маркировка SlotN(SKIPPED) в slots_summary клиента Forge.
    Актуализация архитектурной спецификации: в ARCHITECTURE.md задокументировано решение ADR-49, добавлен раздел 6.7 и обновлены метрики верификации (821 тест, 100% Pass Rate).
Технический список изменений (Changelog):
    Контракт VisualIntent (services/art_director/contracts.py): добавлены опциональные поля face_prompt и hands_prompt с поддержкой [SKIP] для пустых строк и строковыми методами совместимости (__str__, lower, startswith, __contains__).
    Раздельные слоты в GenerationPlan (services/art_director/plan.py): разделены face_slot и hand_slot в to_forge_kwargs(); трансляция [SKIP] в ad_model="None"; добавлены поля ad_source и adetailer_hands_source.
    Изоляция и разрешение слотов в ArtDirector (services/art_director/director.py): независимая сборка resolved_face_prompt (с ELARIA_VISUAL_DNA / companion DNA, guidance, LoRA #1) и resolved_hands_prompt (override модели > hands_prompt > fallback final_positive); автоматическое дополнение resolved_hands_neg анатомическими токенами; метод _format_adetailer_log_blocks с тегами источников и статусом SKIPPED.
    Схемы и директивы (services/assistant_prompt_builder.py): объявлены ART_DIRECTOR_ASSISTANT_JSON_SCHEMA и ART_DIRECTOR_RP_SCENE_JSON_SCHEMA; экранированы фигурные скобки и обновлены шаблоны GALLERY_SKILL_DIRECTIVE_* и CINEMATIC_SKILL_DIRECTIVE_*.
    Потоковый фильтр и парсинг JSON (services/assistant_flow.py): в StreamArtTagFilter добавлены _json_mode, _json_reply_emitted, _json_reply_closed и инкрементальный парсер reply_ru с декодированием escape-символов; функция parse_structured_art_response с 4-фазным восстановлением JSON и диагностическим логированием ([StructuredArt] JSON parsed successfully / warning fallback).
    Контроллеры интерфейса (ui/controllers/assistant_flow_controller.py, services/rp_cast_extractor.py, services/roleplay_flow_controller.py, ui/controllers/hardware_engine.py): передача response_format в lm_client; безопасное извлечение session_id=curr_sess_id через getattr(self, "current_session", None); поддержка Structured JSON в генерации арт-сцен ролевого режима.
    Диагностика слотов Forge (services/forge_client.py): форматирование SlotN(SKIPPED) в slots_summary при ad_model == "None".
    Мобильный шлюз (mobile_server/services/chat_service.py): передача response_format в stream_chat_completion при активных арт-скиллах.
    Тестовая верификация (tests/unit/test_adetailer_dialog.py): добавлены тесты 3-слойного разделения, потокового извлечения reply_ru (test_stream_filter_incremental_json_reply) и диагностического логирования (test_adetailer_diagnostic_logging); 821 тест успешно пройден.
    Архитектурная документация (ARCHITECTURE.md): добавлен блок ADR-49, создан раздел 6.7 «Архитектура 3-слойного раздельного промптинга ADetailer через Structured JSON Output», обновлена сводка тестового сьюта.

## v0.9.8.2B-beta (2026-11-19)

- Устранена ошибка развертки фона при нажатии на сгенерированную картинку

## v0.9.8.2А-beta (2026-11-19)

1. Скорректировал поведения памяти при использовании моделей. 2. Доработал логику переключения SDforge

## v0.9.8.2 - beta (2026-11-18)

1. Доработал РП чат (ход мыслей) 2. Внес корректировку в формулу подсчета свободной ВРАМ. 3. Добавил иконку при запуске приложения.

## v0.9.8.1A (2026-11-17)

- Динамический выбор параметров mmap (--load-mode none vs --no-mmap) для llama-server
- Отказоустойчивая двухфазная загрузка и валидация библиотек CUDA runtime (cudart64 / cublas64)
- Автономный пакет хотфикса hotfix_v098/ для пользователей релиза v0.9.8-beta
- Интерактивное управление версиями и автоматическое ведение hotfix.md при сборке релиза
- Исправление потокового парсинга блоков кода Инженера и синхронизация thinking_mode
