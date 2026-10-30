from pathlib import Path

p = Path("ui/controllers/roleplay_controller.py")
content = p.read_text(encoding="utf-8")

# 1. Проверяем импорты
import_target = "from services.card_translator import ("
if "translate_card_slots_sequential" not in content:
    content = content.replace(
        import_target,
        "from ui.components.step_progress_button import CardProgressBridge\nfrom services.card_translator import (\n    translate_card_slots_sequential,",
        1
    )

# 2. Заменяем метод _translate_character_card_task
start_marker = "    def _translate_character_card_task(self, card: CharacterCard, on_complete=None):"
end_marker = "    def _restore_roleplay_character_from_session("

idx_start = content.find(start_marker)
idx_end = content.find(end_marker)

if idx_start != -1 and idx_end != -1:
    new_method = """    def _translate_character_card_task(self, card: CharacterCard, on_complete=None, on_progress=None):
        \"\"\"Отказоустойчивый пошаговый (Slot-by-Slot) конвейер локализации карточки персонажа.\"\"\"
        # Проверяем наличие текста для перевода
        has_text = any([
            getattr(card, "description", "").strip(),
            getattr(card, "personality", "").strip(),
            getattr(card, "scenario", "").strip(),
            getattr(card, "mes_example", "").strip(),
            getattr(card, "first_mes", "").strip(),
            any(g.strip() for g in getattr(card, "alternate_greetings", []) or [])
        ])

        if not has_text:
            if on_complete:
                try:
                    on_complete(False, {})
                except TypeError:
                    on_complete(False)
            return

        # Мост сигналов для потокобезопасного обновления UI
        bridge = CardProgressBridge()
        if on_progress:
            bridge.progress_changed.connect(on_progress)

        def _worker_fn(task_card: CharacterCard) -> dict:
            def _slot_cb(cur: int, tot: int, slot_key: str):
                bridge.progress_changed.emit(cur, tot, slot_key)

            return translate_card_slots_sequential(
                card=task_card,
                lm_client=self.lm_client,
                on_progress=_slot_cb
            )

        def _on_done(task_id, result):
            res_dict = result if isinstance(result, dict) else {}
            if not res_dict:
                self.statusBar().showMessage(f"Не удалось перевести карточку '{card.name}'.", 5000)
                if on_complete:
                    try:
                        on_complete(False, {})
                    except TypeError:
                        on_complete(False)
                return

            if res_dict.get("DESCRIPTION"): card.description_ru = res_dict["DESCRIPTION"]
            if res_dict.get("PERSONALITY"): card.personality_ru = res_dict["PERSONALITY"]
            if res_dict.get("SCENARIO"): card.scenario_ru = res_dict["SCENARIO"]
            if res_dict.get("MES_EXAMPLE"): card.mes_example_ru = res_dict["MES_EXAMPLE"]
            if res_dict.get("FIRST_MES"): card.first_mes_ru = res_dict["FIRST_MES"]

            if hasattr(card, "alternate_greetings") and card.alternate_greetings:
                alt_ru = list(getattr(card, "alternate_greetings_ru", []) or [])
                while len(alt_ru) < len(card.alternate_greetings):
                    alt_ru.append("")
                for idx in range(len(card.alternate_greetings)):
                    key = f"ALT_GREETING_{idx+1}"
                    if res_dict.get(key):
                        alt_ru[idx] = res_dict[key]
                card.alternate_greetings_ru = alt_ru

            # Сохраняем на диск и синхронизируем с активной сессией (Self-Healing)
            TavernParser.save_card_translations(card)
            if self.current_session:
                cd = self.current_session.get("character_data")
                if isinstance(cd, dict):
                    if cd.get("name") == card.name or cd.get("id") == getattr(card, "id", None):
                        cd["description_ru"] = card.description_ru
                        cd["personality_ru"] = card.personality_ru
                        cd["scenario_ru"] = card.scenario_ru
                        cd["mes_example_ru"] = card.mes_example_ru
                        cd["first_mes_ru"] = card.first_mes_ru
                        cd["alternate_greetings_ru"] = card.alternate_greetings_ru or []
                        if getattr(card, "visual_dna", ""):
                            cd["visual_dna"] = card.visual_dna
                        self._save_current_session()

            self.statusBar().showMessage(f"Карточка персонажа '{card.name}' успешно переведена на русский язык!", 5000)
            if on_complete:
                try:
                    on_complete(True, res_dict)
                except TypeError:
                    on_complete(True)

        task = InferenceTask(
            name=f"🌐 Пошаговая локализация карточки: {card.name}",
            task_type="translation",
            func=_worker_fn,
            kwargs={"task_card": card},
            abort_fn=self.lm_client.abort
        )
        if hasattr(self, "_active_tasks"):
            self._active_tasks[task.id] = task

        def _disconnect_handlers():
            try:
                self.task_queue.task_finished.disconnect(_on_done_wrapper)
            except (TypeError, RuntimeError):
                pass
            try:
                self.task_queue.task_failed.disconnect(_on_fail_wrapper)
            except (TypeError, RuntimeError):
                pass

        def _on_done_wrapper(tid: str, res: Any):
            if tid != task.id:
                return
            _disconnect_handlers()
            if hasattr(self, "_active_tasks"):
                self._active_tasks.pop(task.id, None)
            _on_done(tid, res)

        def _on_fail_wrapper(tid: str, err: str):
            if tid != task.id:
                return
            _disconnect_handlers()
            if hasattr(self, "_active_tasks"):
                self._active_tasks.pop(task.id, None)
            if on_complete:
                try:
                    on_complete(False, {})
                except TypeError:
                    on_complete(False)

        self.task_queue.task_finished.connect(_on_done_wrapper)
        self.task_queue.task_failed.connect(_on_fail_wrapper)
        self.task_queue.enqueue(task)

"""
    content = content[:idx_start] + new_method + content[idx_end:]
    p.write_text(content, encoding="utf-8")
    print("[+] roleplay_controller.py: Пошаговый пайплайн перевода успешно подключен!")
else:
    print(f"[!] Не удалось найти маркеры: start={idx_start}, end={idx_end}")
