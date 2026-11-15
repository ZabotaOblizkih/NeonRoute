"""
Интерактивный тестовый стенд режима «Ассистент» (Элария) с вызовом штатной LLM.
Работает напрямую на боевой цепочке:
  _enqueue_assistant_llm_task -> run_assistant_inference_deferred ->
  SessionManager.get_lm_studio_messages -> ContextWindowBuilder ->
  AssistantPromptBuilder + SkillRouter + ModelRouter ->
  LMStudioClient (+ ProcessManager) -> sanitize_assistant_art_and_markdown ->
  _dispatch_art_to_forge -> ArtDirector.

Запуск из корня проекта:
  python scripts/assistant_prompt_stand.py
  python scripts/assistant_prompt_stand.py -q "Нарисуй красивый пейзаж"
  python scripts/assistant_prompt_stand.py --dry-run -q "Привет"
"""

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Гарантируем корректный UTF-8 ввод/вывод в Windows-консоли
for stream_name in ("stdin", "stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from PyQt6.QtCore import QCoreApplication

from config import DEFAULT_LM_STUDIO_URL, MODE_PROFILES
from services.art_director import ArtDirector
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.lm_studio import LMStudioClient
from services.model_router import ModelRouter, normalize_model_choice
from services.process_manager import ProcessManager
from ui.controllers.assistant_flow_controller import (
    AssistantFlowControllerMixin,
    run_assistant_inference_deferred,
    sanitize_assistant_art_and_markdown,
)
from utils.config_manager import ConfigManager


def _list_header_models(mr: ModelRouter) -> list[str]:
    items = ["✨ Арт-директор (Авто)"]
    for prof in getattr(mr, "profiles", []):
        if isinstance(prof, dict) and prof.get("active", True) and prof.get("name"):
            name = str(prof["name"])
            if name not in items:
                items.append(name)
    return items


def _print_models(items: list[str], current_ckpt: str) -> None:
    print("\nДоступные модели шапки (Forge):")
    for idx, name in enumerate(items):
        mark = " <-- текущая" if name == current_ckpt else ""
        print(f"  [{idx}] {name}{mark}")


def _resolve_pinned_assistant_model(app_cfg: Dict[str, Any], override: str = "") -> str:
    if override.strip():
        return override.strip()
    return (
        app_cfg.get("assistant_model_name")
        or app_cfg.get("active_model")
        or ""
    ).strip()


def _sync_existing_server_state(
    lm_client: LMStudioClient,
    assistant_model: str,
    target_ctx: int,
) -> bool:
    """Если штатный сервер уже запущен и модель загружена, синхронизирует локальное состояние клиента."""
    try:
        ok, _ = lm_client.check_health()
        if ok and assistant_model and lm_client.is_model_loaded(assistant_model):
            lm_client._is_model_unloaded = False
            lm_client._last_loaded_model_id = assistant_model
            lm_client._current_loaded_ctx = max(
                int(getattr(lm_client, "_current_loaded_ctx", 0) or 0),
                int(target_ctx),
            )
            return True
    except Exception:
        pass
    return False


def run_probe(
    user_query: str,
    header_ckpt: str,
    builder: AssistantPromptBuilder,
    mr: ModelRouter,
    lm_client: LMStudioClient,
    app_cfg: Dict[str, Any],
    assistant_model: str,
    stand_history: List[Dict[str, Any]],
    dry_run: bool = False,
    keep_history: bool = True,
    simulated_llm_tag: str = "",
) -> None:
    ctrl = AssistantFlowControllerMixin.__new__(AssistantFlowControllerMixin)
    ctrl.selected_forge_checkpoint = header_ckpt
    ctrl.chat_history = list(stand_history)
    ctrl.current_session = {
        "id": "stand_session",
        "model_id": assistant_model,
        "metadata": {"active_assistant_skill": "auto"},
    }
    ctrl._active_tasks = {}
    ctrl.task_queue = MagicMock()
    ctrl.lm_client = lm_client
    ctrl.lm_combo = None
    ctrl.active_lms_model = assistant_model
    ctrl._current_user_request = user_query
    ctrl.app_config = app_cfg
    ctrl.system_monitor = None
    ctrl._last_art_meta = {}
    ctrl.pill_forge = SimpleNamespace(is_online=True)

    ctrl._enqueue_assistant_llm_task()
    task = ctrl.task_queue.enqueue.call_args[0][0]

    skill_name, score, reason = builder.router.route(user_query)

    print("\n" + "=" * 90)
    print(f"ЗАПРОС          : {user_query!r}")
    print(f"ШАПКА (Forge)   : {header_ckpt!r}")
    print(f"LLM Ассистента  : {assistant_model or '<не задана в config.json>'!r}")
    print(f"История в чате  : {len(stand_history)} сообщ. | Режим: {'DRY-RUN (без LLM)' if dry_run else 'LIVE LLM'}")

    print("\n" + "-" * 90)
    print("[1] ОТРАБОТАВШИЙ СКИЛЛ:")
    if skill_name:
        print(f"  Название : {skill_name!r} | score={score:.3f} | reason={reason}")
        full_skill = builder._resolve_skill_section(
            skill_name=skill_name,
            target_user=task.kwargs.get("effective_user_name", "Пользователь"),
            effective_query=user_query,
            selected_forge_checkpoint=header_ckpt,
        )
        print("\n--- ПОЛНЫЙ ТЕКСТ СКИЛЛА, ПОДСТАВЛЕННЫЙ В ПРОМПТ ---")
        print(full_skill.strip())
    else:
        print(f"  Скилл НЕ активирован (дефолтный диалог Эларии) | score={score:.3f} | reason={reason}")

    if skill_name == "gallery":
        tid, tarch, sdesc, manual = AssistantPromptBuilder.resolve_gallery_target_profile(
            effective_query=user_query,
            selected_forge_checkpoint=header_ckpt,
            check_config_fallback=False,
        )
        prof = mr.get_profile(tid) or {}
        print("\n" + "-" * 90)
        print("[2] ДАННЫЕ АРТ-ПАЙПЛАЙНА ДЛЯ МОДЕЛИ:")
        print(f"  Режим           : {'РУЧНОЙ' if manual else 'АВТО (Арт-директор)'}")
        print(f"  Профиль         : id={tid!r} | name={prof.get('name')!r} | arch={tarch!r}")
        print(f"  Описание стиля  : {sdesc!r}")
        print(f"  Обязат. теги    : {prof.get('llm_guidance', '')!r}")

    captured_call: Dict[str, Any] = {}
    orig_chat_completion = lm_client.chat_completion

    def _intercepting_chat_completion(*c_args, **c_kwargs):
        captured_call.update(c_kwargs)
        messages = c_kwargs.get("messages") or []
        sys_prompt = messages[0]["content"] if messages else ""

        print("\n" + "-" * 90)
        print("[3] ПОЛНЫЙ СИСТЕМНЫЙ ПРОМПТ (messages[0]):\n")
        print(sys_prompt)
        print("\n[4] ПАРАМЕТРЫ ВЫЗОВА ШТАТНОЙ LLM:")
        print(
            f"  model={c_kwargs.get('model')!r} | ctx={c_kwargs.get('context_length')} | "
            f"max_tokens={c_kwargs.get('max_tokens')} | temp={c_kwargs.get('temperature')} | "
            f"min_p={c_kwargs.get('min_p')} | top_p={c_kwargs.get('top_p')}"
        )
        print(f"  messages_count={len(messages)} | last_user={messages[-1] if messages else None}")

        if dry_run:
            return ""

        print("\n" + "-" * 90)
        print("[5] ЖИВОЙ ОТВЕТ ЗАКРЕПЛЁННОЙ МОДЕЛИ (stream):\n")

        def _on_chunk(piece: str) -> None:
            print(piece, end="", flush=True)

        call_args = dict(c_kwargs)
        call_args["emit_signal"] = False
        call_args["chunk_callback"] = _on_chunk
        reply = orig_chat_completion(*c_args, **call_args)
        print()  # перевод строки после стрима
        return reply

    lm_client.chat_completion = _intercepting_chat_completion
    raw_reply = ""
    llm_error: Optional[Exception] = None
    try:
        raw_reply = run_assistant_inference_deferred(**task.kwargs) or ""
    except Exception as exc:
        llm_error = exc
        print(f"\n[ОШИБКА ВЫЗОВА ШТАТНОЙ LLM]: {exc}")
    finally:
        lm_client.chat_completion = orig_chat_completion

    # Пост-обработка ответа модели (как в _handle_assistant_llm_response -> _dispatch_art_to_forge)
    tag_to_check = simulated_llm_tag.strip()
    clean_ui_text = ""
    has_gallery = False
    extracted_prompt: Optional[str] = None

    if raw_reply:
        clean_ui_text, has_gallery, extracted_prompt = sanitize_assistant_art_and_markdown(
            raw_reply,
            user_requested_art=(skill_name == "gallery"),
        )
        if not tag_to_check and extracted_prompt:
            tag_to_check = extracted_prompt

    if tag_to_check:
        captured_art: Dict[str, Any] = {}
        ctrl._on_scene_art_requested = lambda **kw: captured_art.update(kw)
        ctrl._dispatch_art_to_forge(tag_to_check, MagicMock(), user_query)
        v_intent = captured_art["visual_intent"]
        plan = ArtDirector().plan_from_visual_intent(
            visual_intent=v_intent,
            preferred_style=captured_art.get("preferred_style"),
            character_name=captured_art.get("character_name"),
            mode="assistant",
        )
        print("\n" + "-" * 90)
        print("[6] ИТОГ ПОСЛЕ _dispatch_art_to_forge -> ArtDirector (ЧТО ПОШЛО БЫ В FORGE):")
        print(f"  Извлечённый тег : {tag_to_check}")
        print(f"  TargetSubject   : {v_intent.target_subject.value} | char={captured_art.get('character_name')}")
        print(f"  Checkpoint      : {plan.target_checkpoint} ({plan.width}x{plan.height})")
        print(f"  Positive        : {plan.positive_prompt}")
        print(f"  Negative        : {plan.negative_prompt}")
    elif not dry_run and not llm_error and skill_name == "gallery":
        print("\n[!] Модель не вернула тег [IMAGE_PROMPT: ...] (has_gallery={})".format(has_gallery))

    if keep_history and not dry_run and not llm_error and raw_reply:
        stand_history.append({"role": "user", "content": user_query})
        mem_content = clean_ui_text or raw_reply
        if extracted_prompt and "[IMAGE_PROMPT:" not in mem_content:
            mem_content = f"{mem_content}\n\n[IMAGE_PROMPT: {extracted_prompt}]".strip()
        stand_history.append({"role": "assistant", "content": mem_content})

    print("=" * 90)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Интерактивный тестовый стенд Ассистента (Элария) с вызовом штатной LLM."
    )
    parser.add_argument("--query", "-q", type=str, default="", help="Разовый запрос без интерактивного цикла")
    parser.add_argument("--ckpt", "-c", type=str, default="✨ Арт-директор (Авто)", help="Модель в шапке Forge")
    parser.add_argument("--llm-model", "-m", type=str, default="", help="Переопределить модель LLM из config.json")
    parser.add_argument("--tag", "-t", type=str, default="", help="Тестовый [IMAGE_PROMPT] для ручной проверки ArtDirector")
    parser.add_argument("--dry-run", action="store_true", help="Только показать промпты без вызова LLM")
    parser.add_argument("--no-history", action="store_true", help="Не сохранять историю между запросами")
    args = parser.parse_args()

    _qt_app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    app_cfg = ConfigManager.load_config()
    assistant_model = _resolve_pinned_assistant_model(app_cfg, args.llm_model)
    target_ctx = int(
        app_cfg.get(
            "context_length_assistant",
            MODE_PROFILES.get("assistant", {}).get("context_length", 14336),
        )
    )

    print("Инициализация FastEmbed, SkillRouter, ModelRouter и штатного LLM-клиента...")
    mr = ModelRouter.get_instance()
    mr.update_embeddings()
    builder = AssistantPromptBuilder.get_instance()

    lm_client = LMStudioClient(DEFAULT_LM_STUDIO_URL)
    process_manager = ProcessManager()
    process_manager.lm_client = lm_client
    lm_client.set_process_manager(process_manager)

    already_online = _sync_existing_server_state(lm_client, assistant_model, target_ctx)

    header_items = _list_header_models(mr)
    current_ckpt = args.ckpt
    stand_history: List[Dict[str, Any]] = []
    dry_run = bool(args.dry_run)
    keep_history = not bool(args.no_history)

    try:
        if args.query:
            run_probe(
                user_query=args.query,
                header_ckpt=current_ckpt,
                builder=builder,
                mr=mr,
                lm_client=lm_client,
                app_cfg=app_cfg,
                assistant_model=assistant_model,
                stand_history=stand_history,
                dry_run=dry_run,
                keep_history=keep_history,
                simulated_llm_tag=args.tag,
            )
            return

        print("\nСтенд готов (режим: Чат-ассистент Элария).")
        print(f"Закреплённая LLM Ассистента: {assistant_model or '<не задана>'}")
        print(
            "Статус сервера LLM: "
            + ("Уже онлайн с нужной моделью" if already_online else "Будет запущен/загружен штатно при первом запросе")
        )
        _print_models(header_items, current_ckpt)
        print("\nКоманды:")
        print("  /model        — выбрать модель Forge в шапке (Авто или конкретную)")
        print("  /dry          — переключить режим (LIVE LLM <-> DRY-RUN без вызова LLM)")
        print("  /clear        — очистить историю диалога стенда")
        print("  /tag <теги>   — задать тестовый [IMAGE_PROMPT] (если не задан, берётся реальный ответ LLM)")
        print("  /exit         — выход (остановит llama-server, если его запустил стенд)\n")

        sim_tag = args.tag
        while True:
            try:
                mode_tag = "DRY" if dry_run else "LIVE"
                line = input(f"\n[{mode_tag} | {current_ckpt}] Запрос > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nВыход.")
                break

            if not line:
                continue
            if line.lower() in ("/exit", "/quit", "exit", "quit"):
                break
            if line.lower() == "/model":
                _print_models(header_items, current_ckpt)
                choice = input("Введите номер модели (Enter — отмена): ").strip()
                if choice.isdigit():
                    idx = int(choice)
                    if 0 <= idx < len(header_items):
                        current_ckpt = header_items[idx]
                        mode_lbl = "Авто" if normalize_model_choice(current_ckpt) is None else "Ручной"
                        print(f"Установлено: {current_ckpt} ({mode_lbl})")
                    else:
                        print("Некорректный номер.")
                continue
            if line.lower() == "/dry":
                dry_run = not dry_run
                print(f"Режим: {'DRY-RUN (без вызова LLM)' if dry_run else 'LIVE LLM'}")
                continue
            if line.lower() == "/clear":
                stand_history.clear()
                print("История диалога стенда очищена.")
                continue
            if line.lower().startswith("/tag"):
                sim_tag = line[4:].strip()
                print(f"Тестовый тег LLM {'установлен: ' + sim_tag if sim_tag else 'сброшен'}.")
                continue

            run_probe(
                user_query=line,
                header_ckpt=current_ckpt,
                builder=builder,
                mr=mr,
                lm_client=lm_client,
                app_cfg=app_cfg,
                assistant_model=assistant_model,
                stand_history=stand_history,
                dry_run=dry_run,
                keep_history=keep_history,
                simulated_llm_tag=sim_tag,
            )
    finally:
        # Гасим только тот llama-server, который запустил сам стенд
        if getattr(process_manager, "_llama_process", None) is not None:
            print("\nОстановка llama-server, запущенного стендом...")
            try:
                process_manager._kill_llama_server_instance(for_forge_eviction=False)
            except Exception as e_kill:
                print(f"Предупреждение при остановке llama-server: {e_kill}")


if __name__ == "__main__":
    main()
