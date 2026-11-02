"""
Roleplay & Companion Controller (Mixin) for NeonRoute MainWindow.
Handles companions, scene backgrounds, characters gallery, campaign info dialogs,
character localization (_ru fields), and character state hydration.
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from PyQt6.QtGui import QPixmap

from services.tavern_parser import CharacterCard, TavernParser
from services.session_manager import SessionManager
from services.task_queue import InferenceTask
from ui.components.step_progress_button import CardProgressBridge
from services.card_translator import translate_card_slots_sequential

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("NeonRoute.RoleplayController")


class RoleplayControllerMixin:
    """
    Миксин управления ролевым режимом (RP), компаньонами и сценой для MainWindow.
    """

    def _apply_chat_background(self):
        """Применяет персональный фон сцены для текущего диалога с затемнением."""
        bg_path = None
        if self.current_session:
            bg_path = self.current_session.get("background_image")

        applied = False
        if bg_path:
            p = Path(bg_path).resolve()
            if p.exists() and p.is_file():
                pix = QPixmap(str(p))
                if not pix.isNull():
                    dim_alpha = int(255 * 0.70)
                    if hasattr(self, "chat_viewport") and self.chat_viewport:
                        self.chat_viewport.set_background(pix, alpha=dim_alpha)
                        applied = True

        if not applied:
            if hasattr(self, "chat_viewport") and self.chat_viewport:
                if hasattr(self, "background_pixmap") and self.background_pixmap and not self.background_pixmap.isNull():
                    self.chat_viewport.set_background(self.background_pixmap, alpha=self.background_dim_opacity)
                else:
                    self.chat_viewport.set_background(None)

        if hasattr(self, "chat_viewport") and self.chat_viewport:
            self.chat_viewport.update()
        if hasattr(self, "scroll_area") and self.scroll_area:
            self.scroll_area.update()

    def _on_choose_background_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите фоновое изображение для сцены",
            "",
            "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_path:
            return
        norm_path = str(Path(file_path).resolve())
        if self.current_session:
            self.current_session["background_image"] = norm_path
            self._save_current_session()
        self._apply_chat_background()
        self.statusBar().showMessage("Фон сцены обновлен.", 3000)

    def _on_reset_background_clicked(self):
        if self.current_session:
            self.current_session["background_image"] = None
            self._save_current_session()
        self._apply_chat_background()
        self.statusBar().showMessage("Фон сцены сброшен.", 3000)

    def _on_generate_background_clicked(self):
        """Generates scene environment background without people using SD WebUI Forge pipeline."""
        sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
        if not self.pill_forge.is_online:
            logger.warning("[SD Forge] Отклонен запрос генерации фона сцены: сервер Forge офлайн.")
            user_msg = (
                "⚠️ **SD WebUI Forge не запущен или еще загружается.**\n\n"
                "Запустите Forge через кнопку в нижней панели и дождитесь зеленого индикатора."
            )
            self._append_message(sender_name, user_msg, is_user=False, is_error=True)
            self.statusBar().showMessage("Forge офлайн. Запустите сервер в нижней панели.", 6000)
            return

        checkpoint = getattr(self, "selected_forge_checkpoint", "✨ Арт-директор (Авто)")
        sess_id = self.current_session.get("id") if self.current_session else None
        sess_images_dir = SessionManager.get_session_images_dir(sess_id) if sess_id else None

        loc_dna = ""
        if sess_id:
            from services.rp_cast_manager import RPCastManager
            cast_data = RPCastManager.load_cast(sess_id)
            loc_dna = (cast_data.get("environment") or {}).get("location_dna", "").strip()

        if loc_dna:
            prompt_seed = f"scenery, empty environment, {loc_dna}, masterpiece, detailed background, no humans"
        else:
            scene_desc = ""
            if self.active_character:
                scene_desc = self.active_character.scenario or self.active_character.description or self.active_character.name
            prompt_seed = f"scenery, empty environment, {scene_desc.strip()[:260]}, atmospheric lighting, no humans"

        def _bg_pipeline(raw_prompt: str, ckpt: Optional[str] = None) -> Dict[str, Any]:
            self._cancel_lms_reload()
            available_models = self.forge_client.get_models()
            plan = self.art_director.plan_generation(
                raw_prompt=raw_prompt,
                preferred_style=ckpt,
                lm_client=self.lm_client,
                character=None,
                available_models=available_models
            )
            plan.positive_prompt = f"scenery, empty landscape, atmospheric lighting, no humans, {plan.positive_prompt}"
            human_neg = "human, person, 1girl, 1boy, woman, man, character, people, crowd, face, hands, body, portrait, "
            plan.negative_prompt = human_neg + (plan.negative_prompt or "")
            plan.width = 768
            plan.height = 512
            plan.enable_adetailer = False

            from services.vram_arbiter import VRAMArbiter
            VRAMArbiter.get_instance().release_llm_for_diffusion()

            kwargs = plan.to_forge_kwargs(output_dir=sess_images_dir, session_id=sess_id)
            img_path = self.forge_client.text_to_image(**kwargs)

            # Быстрый тихий возврат активной модели режима в VRAM
            if hasattr(self, "_reload_active_lms_model_async"):
                self._reload_active_lms_model_async(delay=1.2)

            if img_path and Path(img_path).resolve().exists():
                if self.current_session:
                    self.current_session["background_image"] = str(Path(img_path).resolve())
                    self._save_current_session()
                QTimer.singleShot(0, self, self._apply_chat_background)
            return {"image_path": img_path, "plan": plan, "checkpoint": plan.target_checkpoint, "is_scene_background": True}

        needs_llm_gen = bool(not loc_dna and re.search(r"[а-яА-ЯёЁ]", prompt_seed))
        task = InferenceTask(
            name="🖼️ Фон сцены",
            task_type="scene_background",
            func=_bg_pipeline,
            kwargs={"raw_prompt": prompt_seed, "ckpt": checkpoint},
            abort_fn=self.forge_client.interrupt,
            metadata={"is_scene_background": True, "needs_llm_prompt_gen": needs_llm_gen}
        )
        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)
        self.statusBar().showMessage("Генерация фона сцены запущена в Forge...", 5000)

    def _open_campaign_info_dialog(self):
        """Открывает интерактивное окно сводки кампании, правил мира и памяти ИИ."""
        if not self.active_character:
            self.statusBar().showMessage("Сначала загрузите карточку персонажа или ДМ.", 3000)
            return
        try:
            from ui.dialogs.campaign_info_dialog import CampaignInfoDialog
            dlg = CampaignInfoDialog(
                character_card=self.active_character,
                current_session=self.current_session,
                parent=self
            )
            dlg.exec()
        except Exception as e:
            logger.error(f"Ошибка открытия сводки кампании: {e}", exc_info=True)

    def _open_characters_gallery_dialog(self):
        """Открытие галереи персонажей с защитой от повторного открытия экземпляров."""
        if hasattr(self, "_active_gallery_dialog") and self._active_gallery_dialog is not None:
            try:
                if self._active_gallery_dialog.isVisible():
                    self._active_gallery_dialog.activateWindow()
                    self._active_gallery_dialog.raise_()
                    return
            except (RuntimeError, ReferenceError):
                self._active_gallery_dialog = None

        from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog
        dialog = CharactersGalleryDialog(parent=self)
        self._active_gallery_dialog = dialog

        # Гарантируем пересчет геометрии сплиттера после закрытия диалога
        def _on_dialog_closed():
            self._active_gallery_dialog = None
            if hasattr(self, "_sync_splitter_layout"):
                QTimer.singleShot(0, lambda: self._sync_splitter_layout(self.is_engineer_mode_active()))

        dialog.finished.connect(_on_dialog_closed)
        dialog.card_saved.connect(self._on_character_card_updated)
        res = dialog.exec()
        if res:
            card = getattr(dialog, "selected_card", None)
            if card:
                self._create_new_session(mode="roleplay", character_card=card)

    def _on_character_card_updated(self, card: CharacterCard):
        """Синхронизирует данные идентичности персонажа, локализацию и visual_dna в сессии и live-объекте."""
        if not card:
            return
        if hasattr(self, "current_session") and self.current_session:
            cd = self.current_session.get("character_data")
            card_id = getattr(card, "id", None)
            card_name = getattr(card, "name", None)
            if isinstance(cd, dict) and ((card_id and cd.get("id") == card_id) or (not cd.get("id") and card_name and cd.get("name") == card_name)):
                cd["name"] = card_name
                cd["visual_dna"] = getattr(card, "visual_dna", "")

                identity_fields = (
                    "description",
                    "personality",
                    "scenario",
                    "system_prompt",
                    "mes_example",
                    "first_mes",
                    "description_ru",
                    "personality_ru",
                    "scenario_ru",
                    "system_prompt_ru",
                    "mes_example_ru",
                    "first_mes_ru",
                )
                for field in identity_fields:
                    if hasattr(card, field):
                        cd[field] = getattr(card, field)
                    elif field == "system_prompt_ru":
                        cd[field] = getattr(card, field, "")

                if hasattr(card, "avatar_path") and getattr(card, "avatar_path", None) is not None:
                    cd["avatar_path"] = str(card.avatar_path) if card.avatar_path else ""

                for greet_field in ("alternate_greetings", "alternate_greetings_ru"):
                    if hasattr(card, greet_field) and getattr(card, greet_field) is not None:
                        cd[greet_field] = getattr(card, greet_field)

                if hasattr(self, "active_character") and self.active_character:
                    if (card_id and getattr(self.active_character, "id", None) == card_id) or (card_name and getattr(self.active_character, "name", None) == card_name):
                        self.active_character.name = card_name
                        self.active_character.visual_dna = getattr(card, "visual_dna", "")
                        for field in identity_fields:
                            if hasattr(card, field):
                                setattr(self.active_character, field, getattr(card, field))
                            elif field == "system_prompt_ru":
                                setattr(self.active_character, field, getattr(card, field, ""))

                        if hasattr(card, "avatar_path") and getattr(card, "avatar_path", None) is not None:
                            self.active_character.avatar_path = card.avatar_path

                        for greet_field in ("alternate_greetings", "alternate_greetings_ru"):
                            if hasattr(card, greet_field) and getattr(card, greet_field) is not None:
                                setattr(self.active_character, greet_field, getattr(card, greet_field))

                        if hasattr(self, "char_name_lbl") and self.char_name_lbl:
                            self.char_name_lbl.setText(f"🎭 {card_name}")
                if hasattr(self, "_save_current_session"):
                    self._save_current_session()
                try:
                    from services.rp_cast_manager import RPCastManager
                    sess_id = self.current_session.get("id")
                    if sess_id:
                        cast_data = RPCastManager.load_cast(sess_id)
                        if isinstance(cast_data, dict) and "companion_state" in cast_data:
                            cast_data["companion_state"]["visual_dna"] = getattr(card, "visual_dna", "")
                            RPCastManager.save_cast(sess_id, cast_data)
                except Exception as e_cast:
                    logger.debug(f"Could not update cast visual_dna on card save: {e_cast}")

    def _import_character(self):
        """Opens Characters Gallery dialog for companion selection."""
        self._open_characters_gallery_dialog()

    def _translate_character_card_task(self, card: CharacterCard, on_complete=None, on_progress=None):
        """Пайплайн асинхронной локализации карточки персонажа на русский язык."""
        sections = []
        if getattr(card, "description", None) and card.description.strip():
            sections.append(f"===DESCRIPTION===\n{card.description.strip()}")
        if getattr(card, "personality", None) and card.personality.strip():
            sections.append(f"===PERSONALITY===\n{card.personality.strip()}")
        if getattr(card, "scenario", None) and card.scenario.strip():
            sections.append(f"===SCENARIO===\n{card.scenario.strip()}")
        if getattr(card, "mes_example", None) and card.mes_example.strip():
            sections.append(f"===MES_EXAMPLE===\n{card.mes_example.strip()}")
        if getattr(card, "first_mes", None) and card.first_mes.strip():
            sections.append(f"===FIRST_MES===\n{card.first_mes.strip()}")

        if hasattr(card, "alternate_greetings") and card.alternate_greetings:
            for idx, alt_greet in enumerate(card.alternate_greetings):
                if alt_greet and alt_greet.strip():
                    sections.append(f"===ALT_GREETING_{idx+1}===\n{alt_greet.strip()}")

        if not sections:
            if on_complete:
                try:
                    on_complete(False, {})
                except TypeError:
                    on_complete(False)
            return

        raw_payload = "\n".join(sections)
        bridge = CardProgressBridge()
        if on_progress:
            bridge.progress_changed.connect(on_progress)

        sys_prompt = (
            "Ты — профессиональный художественный переводчик литературной прозы и ролевых игр на русский язык.\n"
            "Переведи каждую секцию карточки персонажа на богатый, атмосферный русский язык, сохраняя стиль и характер.\n"
            "СТРОГО СОХРАНЯЙ ВСЕ РАЗДЕЛИТЕЛИ ===SECTION=== В СВОЕМ ОТВЕТЕ! Переведи все приветствия ===FIRST_MES=== и ===ALT_GREETING_X=== без исключений."
        )

        def _worker_fn(text: str = None, task_card: CharacterCard = None) -> dict:
            # 1. Если задан коллбэк прогресса — используем слот-конвейер
            if on_progress:
                target_card = task_card or card
                def _slot_cb(cur: int, tot: int, slot_key: str):
                    bridge.progress_changed.emit(cur, tot, slot_key)

                return translate_card_slots_sequential(
                    card=target_card,
                    lm_client=self.lm_client,
                    on_progress=_slot_cb
                )

            # 2. Монолитный вызов для тестов и обратной совместимости
            payload_text = text if text is not None else raw_payload
            resp = self.lm_client.chat_completion(
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": payload_text}],
                temperature=0.25,
                max_tokens=8192,
                emit_signal=False,
                is_roleplay=False
            )
            return {"raw_reply": resp}

        def _on_done(task_id, result):
            if isinstance(result, dict) and "raw_reply" not in result:
                res_dict = result
            else:
                reply = result.get("raw_reply", "") if isinstance(result, dict) else str(result)
                parts = re.split(r'===+\s*([A-Za-z0-9_А-Яа-яЁё\s]+)\s*===+', reply, flags=re.IGNORECASE)
                res_dict = {}
                key_aliases = {
                    "DESCRIPTION": "DESCRIPTION", "ОПИСАНИЕ": "DESCRIPTION",
                    "PERSONALITY": "PERSONALITY", "ХАРАКТЕР": "PERSONALITY", "ЛИЧНОСТЬ": "PERSONALITY",
                    "SCENARIO": "SCENARIO", "СЦЕНАРИЙ": "SCENARIO", "ОКРУЖЕНИЕ": "SCENARIO",
                    "MES_EXAMPLE": "MES_EXAMPLE", "ПРИМЕРЫ": "MES_EXAMPLE", "ПРИМЕРЫ ДИАЛОГОВ": "MES_EXAMPLE", "ДИАЛОГИ": "MES_EXAMPLE",
                    "FIRST_MES": "FIRST_MES", "FIRST_MESSAGE": "FIRST_MES", "FIRST MESSAGE": "FIRST_MES",
                    "GREETING": "FIRST_MES", "ПЕРВОЕ СООБЩЕНИЕ": "FIRST_MES", "ПРИВЕТСТВИЕ": "FIRST_MES", "ВСТУПЛЕНИЕ": "FIRST_MES"
                }
                if len(parts) >= 3:
                    for i in range(1, len(parts), 2):
                        raw_header = parts[i].upper().strip()
                        clean_header = re.sub(r'[\s_]+', ' ', raw_header).strip()
                        underscore_header = re.sub(r'\s+', '_', clean_header)

                        canonical_key = (
                            key_aliases.get(raw_header)
                            or key_aliases.get(clean_header)
                            or key_aliases.get(underscore_header)
                        )
                        if not canonical_key:
                            alt_match = re.match(r'^(?:ALT[_\s]*GREETING|АЛЬТ(?:ЕРНАТИВНОЕ)?[_\s]*ПРИВЕТСТВИЕ)[_\s]*(\d+)$', raw_header, flags=re.IGNORECASE)
                            if alt_match:
                                canonical_key = f"ALT_GREETING_{alt_match.group(1)}"
                            else:
                                canonical_key = raw_header

                        val = parts[i+1].strip()
                        val = re.sub(r'^[\*\s`#_]+(?:\r?\n|$)', '', val)
                        val = re.sub(r'(?:\r?\n|^)[\*\s`#_]+$', '', val).strip()
                        if val and canonical_key:
                            res_dict[canonical_key] = val

            if not res_dict:
                self.statusBar().showMessage(f"Не удалось распознать перевод для карточки '{card.name}'.", 5000)
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
            name=f"🌐 Локализация карточки: {card.name}",
            task_type="translation",
            func=_worker_fn,
            kwargs={"text": raw_payload, "task_card": card},
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

    def _restore_roleplay_character_from_session(self, char_data: Optional[Dict[str, Any]]) -> Optional[CharacterCard]:
        """Восстанавливает объект CharacterCard через канонический from_dict()."""
        if not char_data or not isinstance(char_data, dict):
            return None

        card = CharacterCard.from_dict(char_data)

        # Защитная гидратация visual_dna из каста сессии, если поле пустое
        sess_id = self.current_session.get("id") if self.current_session else None
        if not card.visual_dna and sess_id:
            try:
                from services.rp_cast_manager import RPCastManager
                cast_data = RPCastManager.load_cast(sess_id)
                card.visual_dna = (cast_data.get("companion_state") or {}).get("visual_dna", "")
            except Exception:
                pass

        return card
