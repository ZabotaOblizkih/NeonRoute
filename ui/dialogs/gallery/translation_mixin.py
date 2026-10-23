"""
Mixin for Character Gallery: Card and Field Translation Pipeline.
"""

import logging
from pathlib import Path
from typing import Optional, Callable, Any

from PyQt6.QtWidgets import (
    QTextEdit, QPushButton, QMessageBox, QApplication
)

from services.tavern_parser import CharacterCard
from services.task_queue import InferenceTask
from services.card_translator import (
    CardTranslator,
    mask_card_tags,
    unmask_card_tags,
)

logger = logging.getLogger("NeonRoute.CharactersGallery.TranslationMixin")


class GalleryTranslationMixin:
    def _toggle_card_language(self):
        """Swaps displayed text between original and _ru translation for all fields."""
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        self._showing_ru = not getattr(self, "_showing_ru", True)

        if not self._showing_ru:
            self.btn_toggle_lang.setText("🌐 RU / EN [EN]")
            self.btn_toggle_lang.setToolTip("Отображается: Оригинал (EN). Нажмите для переключения на русский перевод (RU).")
            self.input_desc.setPlainText(card.description or "")
            self.input_personality.setPlainText(card.personality or "")
            self.input_scenario.setPlainText(card.scenario or "")
            self.input_mes_example.setPlainText(card.mes_example or "")
        else:
            self.btn_toggle_lang.setText("🌐 RU / EN [RU]")
            self.btn_toggle_lang.setToolTip("Отображается: Русский перевод (RU). Нажмите для переключения на оригинал (EN).")
            self.input_desc.setPlainText(card.description_ru or card.description or "")
            self.input_personality.setPlainText(card.personality_ru or card.personality or "")
            self.input_scenario.setPlainText(card.scenario_ru or card.scenario or "")
            self.input_mes_example.setPlainText(card.mes_example_ru or card.mes_example or "")

        self._update_greeting_display()

    def _toggle_field_lang(self, text_edit: QTextEdit, field_key: str):
        """Toggles a single field between original and RU translation."""
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        current_val = text_edit.toPlainText().strip()
        ru_val = getattr(card, f"{field_key}_ru", "") or ""
        orig_val = getattr(card, field_key, "") or ""
        if current_val == ru_val and orig_val:
            text_edit.setPlainText(orig_val)
        elif orig_val and ru_val:
            text_edit.setPlainText(ru_val)
        elif ru_val:
            text_edit.setPlainText(ru_val)
        elif orig_val:
            text_edit.setPlainText(orig_val)

    def _execute_translation_pipeline(
        self,
        source_text: str,
        system_prompt: str,
        btn: QPushButton,
        on_success,
        max_chunk_tokens: int = 600
    ):
        parent_win = self.parent()
        if not parent_win or not hasattr(parent_win, "task_queue") or not hasattr(parent_win, "lm_client"):
            QMessageBox.warning(self, "Ошибка", "Модуль инференса недоступен.")
            return

        btn.setEnabled(False)
        orig_btn_text = btn.text()
        btn.setText("⏳ ...")

        def _on_done(task_id, result):
            btn.setEnabled(True)
            res_dict = result if isinstance(result, dict) else {"final_text": str(result), "had_cjk_issue": False}
            final_text = res_dict.get("final_text", "")
            had_cjk = res_dict.get("had_cjk_issue", False)

            if had_cjk:
                btn.setText("⚠️ Проверьте текст")
            else:
                btn.setText(orig_btn_text)

            if final_text.strip():
                on_success(final_text)

        def _on_fail(task_id, err):
            btn.setEnabled(True)
            btn.setText(orig_btn_text)
            logger.error(f"[CardTranslator] Ошибка перевода: {err}")
            # Мягкое уведомление без блокирующего QMessageBox
            btn.setText("❌ Сбой")

        task = CardTranslator.create_translation_task(
            source_text=source_text,
            system_prompt=system_prompt,
            lm_client=parent_win.lm_client,
            on_done=_on_done,
            on_fail=_on_fail,
            max_chunk_tokens=max_chunk_tokens,
            task_name=f"🌐 Перевод: {orig_btn_text}"
        )
        self._ensure_task_queue_connected()
        if hasattr(parent_win, "_active_tasks") and isinstance(parent_win._active_tasks, dict):
            parent_win._active_tasks[task.id] = task
        self._active_dialog_tasks[task.id] = {
            "on_done": _on_done,
            "on_fail": _on_fail,
            "abort_fn": getattr(task, "abort_fn", getattr(parent_win.lm_client, "abort", None))
        }
        parent_win.task_queue.enqueue(task)

    def _translate_field(self, text_edit: QTextEdit, field_key: str, btn: QPushButton):
        """Translates a single field to Russian via robust literary pipeline."""
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        text = getattr(card, field_key, "") or text_edit.toPlainText().strip()
        if not text:
            return

        sys_prompt = CardTranslator.build_field_prompt(field_key)

        def _apply_field(translated_text: str):
            if self.active_entry and self.active_entry["card"] == card:
                self._showing_ru = True
                if hasattr(self, "btn_toggle_lang"):
                    self.btn_toggle_lang.setText("🌐 RU / EN")
                text_edit.setPlainText(translated_text)
                ru_attr = f"{field_key}_ru"
                if hasattr(card, ru_attr):
                    setattr(card, ru_attr, translated_text)
                TavernParser.save_card_translations(card)
                self._save_changes(silent=True)

        self._execute_translation_pipeline(text, sys_prompt, btn, _apply_field)

    def _translate_mes_example(self):
        """Translates card.mes_example to Russian via pipeline."""
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        source_text = card.mes_example or self.input_mes_example.toPlainText().strip()
        if not source_text:
            return

        sys_prompt = CardTranslator.build_field_prompt("mes_example")

        def _apply_ex(translated_text: str):
            card.mes_example_ru = translated_text
            if self.active_entry and self.active_entry["card"] == card:
                self._showing_ru = True
                if hasattr(self, "btn_toggle_lang"):
                    self.btn_toggle_lang.setText("🌐 RU / EN")
                self.input_mes_example.setPlainText(translated_text)
            TavernParser.save_card_translations(card)
            self._save_changes(silent=True)

        self._execute_translation_pipeline(source_text, sys_prompt, self.btn_tr_ex, _apply_ex)

    def _translate_first_mes(self):
        """Translates currently open greeting variant to Russian via pipeline."""
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]

        pool = card.get_greetings_pool()
        if pool and self.current_greeting_idx < len(pool) and pool[self.current_greeting_idx]["orig"]:
            source_text = pool[self.current_greeting_idx]["orig"]
        else:
            source_text = self.input_first_mes.toPlainText().strip()

        if not source_text:
            return

        sys_prompt = CardTranslator.build_field_prompt("first_mes")

        def _apply_first_mes(translated_text: str):
            card.set_greeting_variant(self.current_greeting_idx, translated_text, is_ru=True)
            if self.active_entry and self.active_entry["card"] == card:
                self._showing_ru = True
                if hasattr(self, "btn_toggle_lang"):
                    self.btn_toggle_lang.setText("🌐 RU / EN")
                self.input_first_mes.setPlainText(translated_text)
            TavernParser.save_card_translations(card)
            self._save_changes(silent=True)

        self._execute_translation_pipeline(source_text, sys_prompt, self.btn_tr_first_mes, _apply_first_mes)

    def _on_translate_all_clicked(self):
        """Translates all character card fields via parent window task."""
        if not self.active_entry:
            QMessageBox.warning(self, "Внимание", "Пожалуйста, выберите персонажа.")
            return

        parent_win = self.parent()
        if not parent_win or not hasattr(parent_win, "_translate_character_card_task"):
            QMessageBox.warning(self, "Ошибка", "Метод перевода недоступен.")
            return

        card: CharacterCard = self.active_entry["card"]

        # Sync inputs to card
        self._save_current_greeting_text()
        card.name = self.input_name.text().strip() or card.name
        card.visual_dna = self.input_visual_dna.toPlainText().strip()
        card.description = self.input_desc.toPlainText().strip()
        card.personality = self.input_personality.toPlainText().strip()
        card.scenario = self.input_scenario.toPlainText().strip()
        card.system_prompt = self.input_system_prompt.toPlainText().strip()
        card.mes_example = self.input_mes_example.toPlainText().strip()

        self.btn_translate_all.setEnabled(False)
        self.btn_translate_all.setText("⏳ Перевод карточки...")

        def _on_all_done(success: bool, res_dict=None):
            self.btn_translate_all.setEnabled(True)
            self.btn_translate_all.setText("🌐 Перевести всю карточку (RU)")
            if success:
                self._showing_ru = True
                if hasattr(self, "btn_toggle_lang"):
                    self.btn_toggle_lang.setText("🌐 RU / EN")
                if self.active_entry and self.active_entry["card"] == card:
                    self.input_desc.setPlainText(card.description_ru or "")
                    self.input_personality.setPlainText(card.personality_ru or "")
                    self.input_scenario.setPlainText(card.scenario_ru or "")
                    self.input_mes_example.setPlainText(card.mes_example_ru or "")
                    # Обновить текущее отображаемое первое сообщение с учетом активной страницы приветствий:
                    self._update_greeting_display()
                    self._save_changes(silent=True)
                QMessageBox.information(self, "Успех", f"Карточка '{card.name}' успешно переведена на русский язык!")
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось перевести карточку.")

        parent_win._translate_character_card_task(card, on_complete=_on_all_done)

