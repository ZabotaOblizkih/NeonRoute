"""
Mixin for Character Gallery: Scenery DNA, Visual DNA, and Party Extraction.
"""

import re
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QMessageBox, QPushButton, QFrame, QHBoxLayout,
    QLabel, QApplication, QTextEdit
)

from services.tavern_parser import CharacterCard, TavernParser
from services.art_director.contracts import VisualIntent
from services.token_budget_manager import TokenBudgetManager
from services.task_queue import InferenceTask
from ui.dialogs.local_lora_dialog import LocalLoraDialog
from ui.dialogs.card_visual_setup_dialog import CardVisualSetupDialog

logger = logging.getLogger("NeonRoute.CharactersGallery.DnaPartyMixin")


class GalleryDnaPartyMixin:
    def _extract_and_apply_scenery_dna(self, card: CharacterCard, json_path: Path):
        """Фоновое извлечение геометрии окружения карточки через локальную LLM."""
        if getattr(card, "is_lorebook", False):
            return

        # 1. Если есть regex-теги, сохраняем сразу
        scenery = getattr(card, "scenery_dna", "").strip()
        if not scenery and (card.scenario or card.description):
            scenery = TavernParser.extract_scenery_dna_regex(card.scenario or card.description)

        if scenery:
            card.scenery_dna = scenery
            TavernParser.save_card_scenery_dna(card, json_path)
            return

        # 2. Фоновый инференс через TaskQueue
        parent_win = self.parent()
        if not (parent_win and hasattr(parent_win, "task_queue") and hasattr(parent_win, "lm_client")):
            return

        desc_for_scenery = card.scenario or card.description or card.first_mes
        if not desc_for_scenery:
            return

        sys_prompt = (
            "You are an environment concept artist for Stable Diffusion. "
            "Extract 4-8 physical environment and architecture tags in English from the text.\n"
            "Examples of valid output:\n"
            "- 'ancient stone ruins, broken pillars, moss, overgrown, cloudy sky'\n"
            "- 'cyberpunk street, neon signs, wet asphalt, dark alley, holographic billboards'\n"
            "STRICT RULES:\n"
            "1. Do NOT include abstract category words (e.g. 'room type', 'furniture', 'materials', 'lighting', 'weather', 'outdoor scenery').\n"
            "2. Do NOT include character names, humans, living creatures, or actions.\n"
            "3. Output ONLY comma-separated tags."
        )
        user_prompt = f"Extract location environment tags from this scenario/setting:\n{desc_for_scenery}"

        def _worker_scenery(payload: str) -> dict:
            resp = parent_win.lm_client.chat_completion(
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2,
                emit_signal=False,
                is_roleplay=False
            )
            return {"raw_reply": resp}

        target_file = Path(json_path).resolve()
        card_ref = card
        target_key = str(target_file)

        task = InferenceTask(
            name=f"🏞️ Извлечение локации: {card.name}",
            task_type="memory",
            func=_worker_scenery,
            kwargs={"payload": desc_for_scenery},
            abort_fn=parent_win.lm_client.abort,
            metadata={"silent": True}
        )

        def _on_scenery_done(task_id, result):
            active_set = self._running_analysis_tasks.get(target_key, set())
            active_set.discard(task_id)
            if not active_set:
                self._running_analysis_tasks.pop(target_key, None)

            reply = result.get("raw_reply", "") if isinstance(result, dict) else str(result)
            if reply and reply.strip():
                raw_result = reply.strip().strip('"').strip("'")
                if raw_result.lower().startswith("tags:"):
                    raw_result = raw_result[5:].strip()

                # Санитизация тегов от персонажей и сюжетных глаголов
                WHITELIST_COMPOUND_LOCATIONS = {
                    "slave market", "slave cage", "slave cell", "slave quarters", 
                    "slave auction", "slave camp", "slave caravan", "prison cell", "iron cage"
                }
                forbidden_words = {"protects", "protect", "aggressive", "problem", "lot", "sisters", "sister", "girls", "girl", "slave", "slaves", "defiant", "terrified"}
                if card_ref.name:
                    forbidden_words.update(w.lower() for w in re.findall(r'\b[a-zA-Zа-яА-ЯёЁ0-9_-]+\b', card_ref.name))
                for comp in getattr(card_ref, "companions", []):
                    if isinstance(comp, dict) and comp.get("name"):
                        forbidden_words.update(w.lower() for w in re.findall(r'\b[a-zA-Zа-яА-ЯёЁ0-9_-]+\b', comp["name"]))

                INSTRUCTION_META_NOISE = {
                    "only architecture", "architecture", "room type", "furniture", 
                    "materials", "lighting", "weather", "outdoor scenery", 
                    "room", "environment", "tags", "indoor"
                }
                raw_tags = [t.strip() for t in raw_result.split(",") if t.strip()]
                clean_tags = []
                for t in raw_tags:
                    t_low = t.lower().strip()
                    if t_low in INSTRUCTION_META_NOISE:
                        continue
                    if t_low in WHITELIST_COMPOUND_LOCATIONS:
                        clean_tags.append(t)
                        continue
                    words = t_low.split()
                    if any(fw in words for fw in forbidden_words):
                        continue
                    clean_tags.append(t)
                final_scenery_dna = ", ".join(clean_tags)

                card_ref.scenery_dna = final_scenery_dna
                TavernParser.save_card_scenery_dna(card_ref, target_file)
                if self.active_entry and Path(self.active_entry["path"]).resolve() == target_file:
                    if hasattr(self.active_entry.get("card"), "scenery_dna"):
                        self.active_entry["card"].scenery_dna = final_scenery_dna

            self._update_card_analysis_ui()

        def _on_scenery_fail(task_id, err):
            active_set = self._running_analysis_tasks.get(target_key, set())
            active_set.discard(task_id)
            if not active_set:
                self._running_analysis_tasks.pop(target_key, None)
            logger.warning(f"Ошибка фонового извлечения scenery_dna: {err}")
            self._update_card_analysis_ui()

        self._ensure_task_queue_connected()
        self._running_analysis_tasks.setdefault(target_key, set()).add(task.id)
        parent_win._active_tasks[task.id] = task
        self._active_dialog_tasks[task.id] = {
            "on_done": _on_scenery_done,
            "on_fail": _on_scenery_fail,
            "abort_fn": parent_win.lm_client.abort
        }
        self._update_card_analysis_ui()
        parent_win.task_queue.enqueue(task)

    def _extract_and_apply_visual_dna(self, card, json_path: Path):
        dna = getattr(card, "visual_dna", "")
        if not dna and hasattr(card, "description") and card.description:
            try:
                dna = TavernParser.extract_physical_dna_regex(card.description)
            except Exception as e:
                logger.warning(f"Не удалось извлечь Visual DNA для '{getattr(card, 'name', 'Unknown')}': {e}")
                dna = ""

        if dna:
            card.visual_dna = dna
            try:
                TavernParser.save_card_visual_dna(card, json_path)
            except Exception as e:
                logger.warning(f"Не удалось сохранить visual_dna в {json_path}: {e}")

            if hasattr(self, "active_entry") and self.active_entry and Path(self.active_entry.get("path", "")).resolve() == Path(json_path).resolve():
                if hasattr(self, "input_visual_dna"):
                    self.input_visual_dna.setPlainText(dna)
                if hasattr(self, "_update_context_budget"):
                    self._update_context_budget()

        if getattr(self, "_awaiting_visual_modal_path", None) == str(Path(json_path).resolve()):
            self._awaiting_visual_modal_path = None
            QTimer.singleShot(200, lambda: self._show_visual_setup_modal(card, Path(json_path)))


    def _check_and_extract_party(self, card: CharacterCard, json_path: Path):
        """
        Логика фонового извлечения сопартийцев:
        Строго проверяет, является ли карточка групповой.
        Если да и отряд еще не сохранен — запускает фоновое распознавание состава отряда via LLM.
        Если карточка НЕ групповая — вызов _extract_party_via_llm категорически запрещен.
        Обычные карточки идут штатным путем _extract_and_apply_visual_dna.
        """
        tags_lower = [str(t).lower() for t in getattr(card, "tags", [])]
        is_group = getattr(card, "is_group", False) or "multiple characters" in tags_lower
        has_saved_party = bool(getattr(card, "companions", []))

        if is_group and not has_saved_party:
            self._extract_party_via_llm(card, Path(json_path))
        else:
            self._update_party_selector_ui()
            if not is_group:
                self._extract_and_apply_visual_dna(card, Path(json_path))

    def _extract_party_via_llm(self, card: CharacterCard, json_path: Path):
        """
        Фоновое распознавание состава отряда через локальную LLM в TaskQueue.
        Запускается ТОЛЬКО для карточек, идентифицированных как групповые.
        """
        parent_win = self.parent()
        if not (parent_win and hasattr(parent_win, "task_queue") and hasattr(parent_win, "lm_client")):
            return

        # Исключаем заметки автора (авторские комментарии), system_prompt и посторонние метаданные:
        desc_parts = []
        if card.description:
            desc_parts.append(f"Описание:\n{card.description.strip()}")
        if card.personality:
            desc_parts.append(f"Личность:\n{card.personality.strip()}")
        if card.scenario:
            desc_parts.append(f"Сценарий:\n{card.scenario.strip()}")
        if card.first_mes:
            desc_parts.append(f"Первая сцена:\n{card.first_mes.strip()[:600]}")

        party_source_text = "\n\n".join(desc_parts)
        if not party_source_text:
            return

        sys_prompt = (
            "You are an expert AI data extractor. Analyze this group character card and extract all distinct companions/members.\n"
            "Return STRICT JSON list of objects:\n"
            "[\n"
            "  {\n"
            "    \"name\": \"Character Name\",\n"
            "    \"role\": \"Brief role/relationship\",\n"
            "    \"visual_dna\": \"8-12 English comma-separated tags for SD: age, gender, race, hair, eyes, body, distinct features. NO clothing.\"\n"
            "  }\n"
            "]"
        )
        user_prompt = f"Analyze this group character card and extract all companions:\n{party_source_text}"

        def _worker_party(payload: str) -> dict:
            resp = parent_win.lm_client.chat_completion(
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2,
                emit_signal=False,
                is_roleplay=False
            )
            return {"raw_reply": resp}

        target_file = Path(json_path).resolve()
        card_ref = card
        target_key = str(target_file)

        def _on_party_done(task_id, result):
            active_set = self._running_analysis_tasks.get(target_key, set())
            active_set.discard(task_id)
            if not active_set:
                self._running_analysis_tasks.pop(target_key, None)

            reply = result.get("raw_reply", "") if isinstance(result, dict) else str(result)
            if reply and reply.strip():
                clean_reply = reply.strip()
                if "```json" in clean_reply:
                    clean_reply = clean_reply.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in clean_reply:
                    clean_reply = clean_reply.split("```", 1)[1].split("```", 1)[0].strip()

                parsed = None
                try:
                    parsed = json.loads(clean_reply)
                except Exception:
                    m = re.search(r'\[\s*\{.*\}\s*\]', clean_reply, re.DOTALL)
                    if m:
                        try:
                            parsed = json.loads(m.group(0))
                        except Exception:
                            parsed = None

                if isinstance(parsed, dict):
                    if "companions" in parsed and isinstance(parsed["companions"], list):
                        parsed = parsed["companions"]
                    elif "members" in parsed and isinstance(parsed["members"], list):
                        parsed = parsed["members"]

                if isinstance(parsed, list) and len(parsed) >= 2:
                    valid_comps = []
                    for item in parsed:
                        if isinstance(item, dict) and item.get("name"):
                            valid_comps.append({
                                "name": str(item.get("name", "")).strip(),
                                "role": str(item.get("role", "")).strip(),
                                "visual_dna": str(item.get("visual_dna", "")).strip(),
                                "aliases": [str(item.get("name", "")).strip()]
                            })

                    if len(valid_comps) >= 2:
                        card_ref.companions = valid_comps
                        card_ref.is_group = True
                        TavernParser.save_card_companions(card_ref, target_file)
                        if self.active_entry and Path(self.active_entry["path"]).resolve() == target_file:
                            if hasattr(self.active_entry.get("card"), "companions"):
                                self.active_entry["card"].companions = valid_comps
                                self.active_entry["card"].is_group = True
                            self._update_party_selector_ui()

            self._update_card_analysis_ui()
            if self._awaiting_visual_modal_path == target_key:
                self._awaiting_visual_modal_path = None
                QTimer.singleShot(200, lambda: self._show_visual_setup_modal(card_ref, target_file))

        def _on_party_fail(task_id, err):
            active_set = self._running_analysis_tasks.get(target_key, set())
            active_set.discard(task_id)
            if not active_set:
                self._running_analysis_tasks.pop(target_key, None)
            logger.warning(f"Ошибка фонового извлечения сопартийцев: {err}")
            self._update_card_analysis_ui()
            if self._awaiting_visual_modal_path == target_key:
                self._awaiting_visual_modal_path = None
                QTimer.singleShot(200, lambda: self._show_visual_setup_modal(card_ref, target_file))

        task = InferenceTask(
            name=f"👥 Распознавание отряда: {card.name}",
            task_type="memory",
            func=_worker_party,
            kwargs={"payload": party_source_text},
            abort_fn=parent_win.lm_client.abort,
            metadata={"silent": True}
        )
        self._ensure_task_queue_connected()
        self._running_analysis_tasks.setdefault(target_key, set()).add(task.id)
        parent_win._active_tasks[task.id] = task
        self._active_dialog_tasks[task.id] = {
            "on_done": _on_party_done,
            "on_fail": _on_party_fail,
            "abort_fn": parent_win.lm_client.abort
        }
        self._update_card_analysis_ui()
        parent_win.task_queue.enqueue(task)

    def _update_party_selector_ui(self):
        """
        Динамический инспектор сопартийцев:
        Панель отображается СТРОГО при обнаружении более 1 персонажа (N > 1).
        Для одиночных карточек (N <= 1) панель полностью скрыта (setVisible(False)).
        """
        while self.party_selector_layout.count():
            item = self.party_selector_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.party_buttons.clear()

        if not self.active_entry:
            self.party_selector_container.setVisible(False)
            self.btn_gallery_generate_art.setText("🎨 Сгенерировать арт")
            self.active_companion_idx = -1
            return

        card = self.active_entry.get("card")
        companions = getattr(card, "companions", []) if card else []

        # СТРОГОЕ ПРАВИЛО: если len <= 1 -> скрыть панель
        if len(companions) <= 1:
            self.party_selector_container.setVisible(False)
            self.btn_gallery_generate_art.setText("🎨 Сгенерировать арт")
            self.active_companion_idx = -1
            return

        # len > 1 -> отобразить панель сопартийцев
        self.party_selector_container.setVisible(True)

        for i, comp in enumerate(companions):
            c_name = comp.get("name", "").strip() or f"Спутник {i+1}"
            btn = QPushButton(f"👤 {c_name}")
            btn.setObjectName("btn_tr_field")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_companion_chip_clicked(idx))
            self.party_buttons.append(btn)
            self.party_selector_layout.addWidget(btn)

        btn_all = QPushButton("👥 Общий кадр")
        btn_all.setObjectName("btn_tr_field")
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.clicked.connect(lambda checked=False: self._on_companion_chip_clicked(-1))
        self.party_buttons.append(btn_all)
        self.party_selector_layout.addWidget(btn_all)
        self.party_selector_layout.addStretch()

        if self.active_companion_idx == -1 or self.active_companion_idx >= len(companions):
            self.active_companion_idx = 0

        self._refresh_party_button_styles()

        if 0 <= self.active_companion_idx < len(companions):
            comp = companions[self.active_companion_idx]
            self.input_visual_dna.setPlainText(comp.get("visual_dna", ""))
            self.btn_gallery_generate_art.setText(f"🎨 Сгенерировать арт: {comp.get('name', 'Спутник')}")
        else:
            combined = ", ".join(c.get("visual_dna", "") for c in companions if c.get("visual_dna")).replace(", ", " BREAK ")
            self.input_visual_dna.setPlainText(combined)
            self.btn_gallery_generate_art.setText("🎨 Сгенерировать арт: Отряд")

    def _refresh_party_button_styles(self):
        """Подсвечивает активную кнопку сопартийца неоновым акцентом #00F0FF."""
        card = self.active_entry.get("card") if self.active_entry else None
        companions = getattr(card, "companions", []) if card else []
        n_comps = len(companions)

        palette = getattr(self, "_current_palette", None) or {}
        accent = palette.get("accent", "#00F0FF")
        card_border = palette.get("border", "#1E293B")
        card_bg = palette.get("btn", "#0F172A")
        text_sec = palette.get("text_secondary", "#94A3B8")

        for i, btn in enumerate(self.party_buttons):
            is_active = False
            if i < n_comps:
                is_active = (i == self.active_companion_idx)
            else:
                is_active = (self.active_companion_idx == -1)

            if is_active:
                btn.setStyleSheet(
                    f"border: 1px solid {accent}; background-color: rgba(0, 240, 255, 0.18); "
                    f"color: {accent}; font-weight: bold; padding: 3px 8px; border-radius: 4px; font-size: 11px;"
                )
            else:
                btn.setStyleSheet(
                    f"border: 1px solid {card_border}; background-color: {card_bg}; "
                    f"color: {text_sec}; font-weight: normal; padding: 3px 8px; border-radius: 4px; font-size: 11px;"
                )

    def _on_companion_chip_clicked(self, idx: int):
        """
        Смена сопартийца:
        1. Сохраняет текущий набранный текст из input_visual_dna в card.companions[active_companion_idx].
        2. Переключает active_companion_idx = idx.
        3. Если выбран конкретный персонаж: подставляет visual_dna, меняет текст кнопки.
           Если выбран «Общий кадр»: объединяет теги через BREAK, меняет текст кнопки на «🎨 Сгенерировать арт: Отряд».
        4. Перерисовывает стили кнопок.
        """
        if not self.active_entry:
            return
        card = self.active_entry.get("card")
        companions = getattr(card, "companions", []) if card else []
        if not companions:
            return

        # 1. Сохраняем текущий набранный текст
        if 0 <= self.active_companion_idx < len(companions):
            companions[self.active_companion_idx]["visual_dna"] = self.input_visual_dna.toPlainText().strip()

        # 2. Переключаем индекс
        self.active_companion_idx = idx

        # 3. Обновляем поле ввода и кнопку генерации
        if 0 <= idx < len(companions):
            comp = companions[idx]
            self.input_visual_dna.setPlainText(comp.get("visual_dna", ""))
            self.btn_gallery_generate_art.setText(f"🎨 Сгенерировать арт: {comp.get('name', 'Спутник')}")
        else:
            # «Общий кадр» (-1)
            combined = ", ".join(c.get("visual_dna", "") for c in companions if c.get("visual_dna")).replace(", ", " BREAK ")
            self.input_visual_dna.setPlainText(combined)
            self.btn_gallery_generate_art.setText("🎨 Сгенерировать арт: Отряд")

        # 4. Перерисовываем стили кнопок
        self._refresh_party_button_styles()
        self._update_context_budget()

    def _on_gallery_generate_art_clicked(self):
        """
        Обработчик кнопки прямой генерации арта из галереи:
        Считывает текущий prompt_text из input_visual_dna,
        собирает VisualIntent и отправляет в TaskQueue / ArtDirector без открытия чата.
        """
        prompt_text = self.input_visual_dna.toPlainText().strip()
        if not prompt_text:
            self.btn_gallery_generate_art.setToolTip("⚠️ Промпт внешности (Visual DNA) пуст!")
            parent_win = self.parent()
            if parent_win and hasattr(parent_win, "statusBar"):
                parent_win.statusBar().showMessage("⚠️ Visual DNA пуст. Заполните теги внешности для генерации.", 4000)
            QMessageBox.warning(self, "Visual DNA пуст", "Поле внешности (Visual DNA) пусто.\nЗаполните теги внешности для генерации арта.")
            return

        if not self.active_entry:
            return

        card = self.active_entry["card"]
        parent_win = self.parent()
        if not parent_win:
            QMessageBox.warning(self, "Ошибка", "Родительское окно недоступно.")
            return

        has_art = hasattr(parent_win, "art_director") or hasattr(parent_win, "_on_scene_art_requested")
        if not has_art:
            QMessageBox.warning(self, "Сервис недоступен", "Сервис генерации артов недоступен.")
            return

        from services.art_director import VisualIntent, TargetSubject

        comp_name = card.name
        if 0 <= self.active_companion_idx < len(getattr(card, "companions", [])):
            comp_name = card.companions[self.active_companion_idx]["name"]
        elif self.active_companion_idx == -1 and getattr(card, "companions", []):
            comp_name = "Отряд"

        v_intent = VisualIntent(
            prompt=prompt_text,
            target_subject=TargetSubject.COMPANION,
            user_neg="",
            suggested_model=getattr(parent_win, "selected_forge_checkpoint", None),
            seed=-1,
            raw_user_query=f"Прямая генерация из галереи: {comp_name}",
            mode="roleplay"
        )

        orig_btn_text = self.btn_gallery_generate_art.text()
        self.btn_gallery_generate_art.setText("⏳ Отправлено в Forge...")
        self.btn_gallery_generate_art.setEnabled(False)

        def _restore_btn():
            self.btn_gallery_generate_art.setText(orig_btn_text)
            self.btn_gallery_generate_art.setEnabled(True)

        QTimer.singleShot(2000, _restore_btn)

        if hasattr(parent_win, "_on_scene_art_requested"):
            parent_win._on_scene_art_requested(
                visual_intent=v_intent,
                character_name=comp_name
            )
        elif hasattr(parent_win, "task_queue") and hasattr(parent_win, "art_director") and hasattr(parent_win, "forge_client"):
            def _gallery_art_worker(v_payload: VisualIntent, ckpt: Optional[str] = None):
                getattr(parent_win, "_cancel_lms_reload", lambda: None)()
                avail = parent_win.forge_client.get_models()
                plan = parent_win.art_director.plan_from_visual_intent(
                    visual_intent=v_payload,
                    preferred_style=ckpt,
                    available_models=avail,
                    character_name=comp_name,
                    character=card,
                    mode="roleplay"
                )
                from services.vram_arbiter import VRAMArbiter
                VRAMArbiter.get_instance().release_llm_for_diffusion()

                kwargs = plan.to_forge_kwargs()
                img = parent_win.forge_client.text_to_image(**kwargs)
                try:
                    parent_win.forge_client.unload_checkpoint()
                except Exception:
                    pass
                if hasattr(parent_win, "_reload_active_lms_model_async"):
                    parent_win._reload_active_lms_model_async(delay=1.2)
                return {"image_path": img, "plan": plan}

            task = InferenceTask(
                name=f"🎨 Арт: {comp_name}",
                task_type="image",
                func=_gallery_art_worker,
                kwargs={"v_payload": v_intent, "ckpt": getattr(parent_win, "selected_forge_checkpoint", None)},
                abort_fn=parent_win.forge_client.interrupt,
                metadata={"prompt": prompt_text, "silent": True, "needs_llm_prompt_gen": False}
            )
            if hasattr(parent_win, "_active_tasks") and isinstance(parent_win._active_tasks, dict):
                parent_win._active_tasks[task.id] = task
            parent_win.task_queue.enqueue(task)

        if hasattr(parent_win, "statusBar"):
            parent_win.statusBar().showMessage(f"🎨 Генерация арта для '{comp_name}' отправлена в Forge.", 4000)

    def _on_find_lora_clicked(self):
        if not self.active_entry:
            return
        card = self.active_entry["card"]
        
        # Определяем целевое имя: если выбран конкретный сопартиец — ищем его
        target_name = card.name
        if 0 <= self.active_companion_idx < len(getattr(card, "companions", [])):
            target_name = card.companions[self.active_companion_idx]["name"]
        
        # Очищаем имя от лишних приписок ("Кай и Эйла - Рабыни" -> "Кай", отсекаем скобки)
        clean_query = re.sub(r'[\(\[].*?[\)\]]', '', target_name)
        clean_query = clean_query.split('-')[0].split('—')[0].strip()

        from ui.dialogs.civitai_lora_dialog import CivitaiLoraDialog
        dlg = CivitaiLoraDialog(
            character_name=target_name,
            initial_query=clean_query,
            active_architecture="SD 1.5",
            parent=self
        )
        dlg.lora_installed.connect(self._on_lora_installed_for_card)
        dlg.exec()

    def _on_lora_installed_for_card(self, file_stem: str, character_name: str, trained_words: list):
        """Предлагает добавить тег LoRA и триггерные слова в поле Visual DNA карточки."""
        act_tags = ", ".join(trained_words[:5]) if trained_words else ""
        lora_token = f"<lora:{file_stem}:0.8>"
        suggested_injection = f"{lora_token}, {act_tags}".strip(" ,")

        reply = QMessageBox.question(
            self,
            "LoRA установлена",
            f"LoRA '{file_stem}' успешно сохранена в Forge и зарегистрирована!\n\n"
            f"Добавить тег активации в Visual DNA персонажа?\n\n{suggested_injection}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )

        if reply == QMessageBox.StandardButton.Yes:
            cur_dna = re.sub(r'<lora:[^>]+>\s*,?\s*', '', self.input_visual_dna.toPlainText(), flags=re.IGNORECASE).strip(' ,')
            new_dna = f"{suggested_injection}, {cur_dna}".strip(" ,") if cur_dna else suggested_injection
            self.input_visual_dna.setPlainText(new_dna)
            self._save_changes(silent=True)

    def _show_visual_setup_modal(self, card: CharacterCard, target_path: Path):
        if not self.isVisible():
            return
        dlg = CardVisualSetupDialog(card, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_dna = dlg.get_visual_dna()
            card.visual_dna = new_dna
            self._save_card_visual_dna(card, target_path)
            if self.active_entry and Path(self.active_entry["path"]).resolve() == target_path.resolve():
                self.input_visual_dna.setPlainText(new_dna)
                self._update_context_budget()
                self._save_changes(silent=True)

    def _on_choose_local_lora_clicked(self):
        if not self.active_entry:
            return
        card = self.active_entry["card"]
        target_name = card.name
        if 0 <= self.active_companion_idx < len(getattr(card, "companions", [])):
            target_name = card.companions[self.active_companion_idx]["name"]

        dlg = LocalLoraDialog(character_name=target_name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            lora_tag, triggers = dlg.get_result()
            if lora_tag:
                cur_dna = re.sub(r'<lora:[^>]+>\s*,?\s*', '', self.input_visual_dna.toPlainText(), flags=re.IGNORECASE).strip(' ,')
                add_s = lora_tag
                if triggers:
                    add_s += f", {', '.join(triggers[:4])}"
                new_dna = f"{add_s}, {cur_dna}".strip(" ,") if cur_dna else add_s
                self.input_visual_dna.setPlainText(new_dna)
                self._save_changes(silent=True)

    def _save_card_visual_dna(self, card: CharacterCard, json_path: Path):
        """Saves card.visual_dna into the character JSON file on disk."""
        try:
            p = Path(json_path)
            if not p.exists():
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            target = data.get("data", data) if ("spec" in data or "data" in data) else data
            target["visual_dna"] = getattr(card, "visual_dna", "")
            if "visual_dna" in data and data is not target:
                data["visual_dna"] = getattr(card, "visual_dna", "")
            from utils.file_utils import atomic_write_json
            atomic_write_json(p, data)
        except Exception as e:
            logger.warning(f"Could not persist visual_dna to {json_path}: {e}")

