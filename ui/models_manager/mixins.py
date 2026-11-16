"""
ui/models_manager/mixins.py
===========================
Модульные контроллер-миксины диалога ModelsManagerDialog:
- ModelPipelineFormMixin: дата-биндинг формы, синхронизация контролов рендера, пресеты, CRUD таблицы моделей.
- ForgeCivitaiSyncMixin: сканирование и авторегистрация моделей Forge, интеграция с Civitai и каталогом.
- FP8ConversionMixin: квантование моделей SDXL в нативный FP8, Name Affinity Guard и Atomic In-Place Swap.
"""
import os
import re
import sys
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QMessageBox, QDialog, QTableWidgetItem, QApplication
)
from qfluentwidgets import (
    InfoBar as _QFWInfoBar,
    InfoBarPosition as _QFWInfoBarPosition,
    MessageBox as _QFWMessageBox,
    FluentIcon as FIF,
)


class _DynamicModuleProxy:
    """Динамический прокси для прозрачной поддержки unittest.mock.patch('ui.models_manager_dialog.*')."""
    def __init__(self, attr_name: str, fallback):
        self._attr_name = attr_name
        self._fallback = fallback

    def __getattr__(self, item):
        mmd = sys.modules.get("ui.models_manager_dialog")
        target = getattr(mmd, self._attr_name, self._fallback) if mmd else self._fallback
        return getattr(target, item)

    def __call__(self, *args, **kwargs):
        mmd = sys.modules.get("ui.models_manager_dialog")
        target = getattr(mmd, self._attr_name, self._fallback) if mmd else self._fallback
        return target(*args, **kwargs)


InfoBar = _DynamicModuleProxy("InfoBar", _QFWInfoBar)
MessageBox = _DynamicModuleProxy("MessageBox", _QFWMessageBox)
InfoBarPosition = _DynamicModuleProxy("InfoBarPosition", _QFWInfoBarPosition)

from services.art_director.geometry import is_xl_model
from ui.models_manager.adetailer_dialog import ADetailerSettingsDialog
from ui.models_manager.widgets import (
    MODEL_PRESETS,
    normalize_checkpoint_stem,
    get_default_negative_prompt,
    create_active_checkbox_cell,
    create_delete_button_cell,
)
from ui.models_manager.workers import (
    ForgeScanWorker,
    CivitaiParamsWorker,
    FP8ConvertWorker,
    resolve_converter_script,
    verify_python,
    resolve_torch_python,
    resolve_checkpoint_path,
)

logger = logging.getLogger("NeonRoute.ModelsManager.Mixins")


# ============================================================================
# 1. ModelPipelineFormMixin: Контроллер формы и таблицы моделей
# ============================================================================

class ModelPipelineFormMixin:
    """Миксин управления формой рендера, таблицей моделей и пресетами."""

    def _on_steps_slider_changed(self, val: int):
        self.spin_steps.blockSignals(True)
        self.spin_steps.setValue(val)
        self.spin_steps.blockSignals(False)
        self._on_param_modified()

    def _on_steps_spin_changed(self, val: int):
        self.slider_steps.blockSignals(True)
        self.slider_steps.setValue(val)
        self.slider_steps.blockSignals(False)
        self._on_param_modified()

    def _on_cfg_slider_changed(self, val: int):
        self.spin_cfg.blockSignals(True)
        self.spin_cfg.setValue(round(val / 10.0, 1))
        self.spin_cfg.blockSignals(False)
        self._on_param_modified()

    def _on_cfg_spin_changed(self, val: float):
        self.slider_cfg.blockSignals(True)
        self.slider_cfg.setValue(int(round(val * 10)))
        self.slider_cfg.blockSignals(False)
        self._on_param_modified()

    def _on_hires_denoise_slider_changed(self, val: int):
        self.spin_hires_denoise.blockSignals(True)
        self.spin_hires_denoise.setValue(round(val / 100.0, 2))
        self.spin_hires_denoise.blockSignals(False)
        self._on_param_modified()

    def _on_hires_denoise_spin_changed(self, val: float):
        self.slider_hires_denoise.blockSignals(True)
        self.slider_hires_denoise.setValue(int(round(val * 100)))
        self.slider_hires_denoise.blockSignals(False)
        self._on_param_modified()

    def _on_clip_skip_slider_changed(self, val: int):
        self.spin_clip_skip.blockSignals(True)
        self.spin_clip_skip.setValue(val)
        self.spin_clip_skip.blockSignals(False)
        self._on_param_modified()

    def _on_clip_skip_spin_changed(self, val: int):
        self.slider_clip_skip.blockSignals(True)
        self.slider_clip_skip.setValue(val)
        self.slider_clip_skip.blockSignals(False)
        self._on_param_modified()

    def _on_param_modified(self):
        self.combo_presets.blockSignals(True)
        self.combo_presets.setCurrentIndex(0)  # Сброс в «Кастомный / Выбрать...»
        self.combo_presets.blockSignals(False)
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self._save_form_to_model(self.current_selected_row)

    def _on_preset_selected(self, index: int):
        """Реактивный слот пресетов генерации в один клик."""
        if index <= 0:
            return

        preset_text = self.combo_presets.currentText()
        matched_preset = None
        for p_name, p_data in MODEL_PRESETS.items():
            if p_name in preset_text:
                matched_preset = p_data
                break

        if not matched_preset:
            return

        arch = matched_preset["architecture"]
        sampler = matched_preset["sampler"]
        scheduler = matched_preset["scheduler"]
        steps = matched_preset["steps"]
        cfg = matched_preset["cfg_scale"]
        clip_skip = matched_preset["clip_skip"]
        enable_hr = matched_preset["enable_hr"]
        enable_ad = matched_preset["enable_adetailer"]

        widgets_to_block = [
            self.combo_arch, self.combo_sampler, self.combo_scheduler,
            self.spin_steps, self.slider_steps, self.spin_cfg,
            self.slider_cfg, self.spin_clip_skip, self.slider_clip_skip,
            self.switch_enable_hr
        ]
        for w in widgets_to_block:
            w.blockSignals(True)

        idx_arch = self.combo_arch.findText(arch)
        if idx_arch >= 0:
            self.combo_arch.setCurrentIndex(idx_arch)

        idx_s = self.combo_sampler.findText(sampler)
        if idx_s >= 0:
            self.combo_sampler.setCurrentIndex(idx_s)
        else:
            self.combo_sampler.addItem(sampler)
            self.combo_sampler.setCurrentText(sampler)

        idx_sc = self.combo_scheduler.findText(scheduler)
        if idx_sc >= 0:
            self.combo_scheduler.setCurrentIndex(idx_sc)
        else:
            self.combo_scheduler.addItem(scheduler)
            self.combo_scheduler.setCurrentText(scheduler)

        self.spin_steps.setValue(steps)
        self.slider_steps.setValue(steps)

        self.spin_cfg.setValue(cfg)
        self.slider_cfg.setValue(int(round(cfg * 10)))

        self.spin_clip_skip.setValue(clip_skip)
        self.slider_clip_skip.setValue(clip_skip)

        self.switch_enable_hr.setChecked(enable_hr)
        if 0 <= self.current_selected_row < len(self.row_widgets):
            cur_pipe = self.row_widgets[self.current_selected_row].setdefault("pipeline", {})
            cur_pipe["enable_adetailer"] = enable_ad
            self._update_ad_status_label(cur_pipe)

        for w in widgets_to_block:
            w.blockSignals(False)

        if 0 <= self.current_selected_row < len(self.row_widgets):
            self._save_form_to_model(self.current_selected_row)

    def _on_input_name_changed(self, text: str):
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self.row_widgets[self.current_selected_row]["name"] = text.strip()
            item = self.table.item(self.current_selected_row, 1)
            if item:
                item.setText(text.strip())

    def _on_combo_checkpoint_changed(self, text: str):
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self.row_widgets[self.current_selected_row]["checkpoint"] = text.strip()
            item = self.table.item(self.current_selected_row, 2)
            if item:
                item.setText(text.strip())
            self._update_fp8_button_state()

    def _on_table_cell_changed(self, row: int, col: int):
        if row == self.current_selected_row and 0 <= row < len(self.row_widgets):
            if col == 1:
                item = self.table.item(row, col)
                if item and item.text() != self.input_name.text():
                    self.input_name.setText(item.text())

    def _on_is_default_toggled(self, checked: bool):
        if 0 <= self.current_selected_row < len(self.row_widgets):
            self._save_form_to_model(self.current_selected_row)

    def _on_input_guidance_changed(self):
        """Валидация тегов подсказок LLM."""
        val = self.input_guidance.toPlainText()
        tags = [t.strip() for t in val.split(",") if t.strip()]
        if len(tags) > 8:
            truncated = ", ".join(tags[:8])
            self.input_guidance.blockSignals(True)
            self.input_guidance.setPlainText(truncated)
            self.input_guidance.blockSignals(False)
            InfoBar.warning(
                "Лимит подсказок",
                "Разрешено не более 8 ключевых тегов (лишние усечены).",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500
            )

    def _save_form_to_model(self, row_idx: int):
        if row_idx < 0 or row_idx >= len(self.row_widgets):
            return
        r = self.row_widgets[row_idx]
        r["name"] = self.input_name.text().strip()
        r["checkpoint"] = self.combo_checkpoint.currentText().strip()
        r["routing_description"] = self.input_desc.toPlainText().strip()
        guidance_val = self.input_guidance.toPlainText().strip()[:160]
        if "," in guidance_val:
            g_parts = [t.strip() for t in guidance_val.split(",") if t.strip()][:8]
            guidance_val = ", ".join(g_parts)
        r["llm_guidance"] = guidance_val
        r["negative_prompt"] = self.input_negative_prompt.toPlainText().strip()

        is_default = self.chk_is_default.isChecked()
        raw_triggers = [t.strip() for t in self.edit_style_triggers.text().split(",") if t.strip()]
        r["is_default"] = is_default
        r["style_triggers"] = raw_triggers

        if is_default:
            for idx, other_r in enumerate(self.row_widgets):
                if idx != row_idx:
                    other_r["is_default"] = False

        arch = "sdxl" if self.combo_arch.currentText().startswith("SDXL") else "sd15"
        r["architecture"] = arch

        existing_pipe = dict(r.get("pipeline", {}))
        r["pipeline"] = {
            **existing_pipe,
            "architecture": arch,
            "sampler": self.combo_sampler.currentText(),
            "scheduler": self.combo_scheduler.currentText(),
            "steps": self.spin_steps.value(),
            "cfg_scale": round(self.spin_cfg.value(), 1),
            "clip_skip": self.spin_clip_skip.value(),
            "hires_upscaler": self.combo_upscaler.currentText(),
            "hires_denoise": round(self.spin_hires_denoise.value(), 2),
            "enable_hr": self.switch_enable_hr.isChecked(),
            "enable_adetailer": r.get("pipeline", {}).get("enable_adetailer", True),
            "negative_prompt": self.input_negative_prompt.toPlainText().strip()
        }

    def _open_adetailer_dialog(self):
        if self.current_selected_row < 0 or self.current_selected_row >= len(self.row_widgets):
            return

        target_model = self.row_widgets[self.current_selected_row]
        pipe = target_model.setdefault("pipeline", {})
        if self.current_selected_row < len(self.models):
            self.models[self.current_selected_row].setdefault("pipeline", pipe)
        is_xl = "xl" in self.combo_arch.currentText().lower()

        dialog = ADetailerSettingsDialog(pipe, is_xl=is_xl, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pipe.update(dialog.result_params)
            if self.current_selected_row < len(self.models):
                self.models[self.current_selected_row]["pipeline"].update(dialog.result_params)
            self._update_ad_status_label(pipe)
            self._on_param_modified()

    def _update_ad_status_label(self, pipe: dict):
        if not pipe.get("enable_adetailer", True):
            self.lbl_ad_status.setText("ВЫКЛ")
            self.lbl_ad_status.setStyleSheet("color: #94A3B8;")
        else:
            face_s = pipe.get("adetailer_face_model", "v8m").replace("face_yolo", "").replace(".pt", "")
            hands_on = pipe.get("adetailer_hands_enabled", True)
            hand_s = pipe.get("adetailer_hands_model", "v9c").replace("hand_yolo", "").replace(".pt", "") if hands_on else "off"
            self.lbl_ad_status.setText(f"ВКЛ [{face_s}/{hand_s}]")
            accent = getattr(self, "_current_palette", {}).get("group_title", "#818CF8")
            self.lbl_ad_status.setStyleSheet(f"color: {accent}; font-weight: bold;")

    def _load_model_to_form(self, row_idx: int):
        if row_idx < 0 or row_idx >= len(self.row_widgets):
            return
        r = self.row_widgets[row_idx]

        widgets_to_block = [
            self.input_name, self.combo_checkpoint, self.combo_arch,
            self.combo_scheduler, self.combo_presets, self.combo_sampler,
            self.spin_steps, self.slider_steps, self.spin_cfg,
            self.slider_cfg, self.spin_clip_skip, self.slider_clip_skip,
            self.combo_upscaler, self.spin_hires_denoise, self.slider_hires_denoise,
            self.switch_enable_hr, self.input_negative_prompt,
            self.input_desc, self.input_guidance, self.chk_is_default,
            self.edit_style_triggers
        ]
        for w in widgets_to_block:
            w.blockSignals(True)

        self.input_name.setText(r.get("name", ""))

        ckpt = r.get("checkpoint", "").strip()
        idx_c = self.combo_checkpoint.findText(ckpt)

        # Фоллбэк 1: поиск эквивалентного чекпоинта по нормализованному стэму
        if idx_c == -1 and ckpt:
            target_norm = normalize_checkpoint_stem(ckpt)
            for i in range(self.combo_checkpoint.count()):
                item_norm = normalize_checkpoint_stem(self.combo_checkpoint.itemText(i))
                if target_norm and target_norm == item_norm:
                    idx_c = i
                    break

        # Фоллбэк 2: если файла нет в списке Forge, принудительно добавляем его
        if idx_c == -1 and ckpt:
            self.combo_checkpoint.addItem(ckpt)
            idx_c = self.combo_checkpoint.findText(ckpt)

        if idx_c >= 0:
            self.combo_checkpoint.setCurrentIndex(idx_c)
        else:
            self.combo_checkpoint.setEditText(ckpt)

        pipe = r.get("pipeline", {})

        # Архитектура с фоллбэком на эвристику is_xl_model
        arch_raw = r.get("architecture") or pipe.get("architecture") or ("sdxl" if is_xl_model(r) else "sd15")
        if str(arch_raw).lower().startswith("sdxl") or "pony" in str(arch_raw).lower() or (not r.get("architecture") and is_xl_model(r)):
            idx_arch = self.combo_arch.findText("SDXL / Pony")
        else:
            idx_arch = self.combo_arch.findText("SD 1.5")
        if idx_arch >= 0:
            self.combo_arch.setCurrentIndex(idx_arch)

        # Планировщик
        sched_val = pipe.get("scheduler", "Karras")
        idx_sc = self.combo_scheduler.findText(sched_val)
        if idx_sc >= 0:
            self.combo_scheduler.setCurrentIndex(idx_sc)
        elif sched_val:
            self.combo_scheduler.addItem(sched_val)
            self.combo_scheduler.setCurrentText(sched_val)

        # Семплер
        idx_s = self.combo_sampler.findText(pipe.get("sampler", "DPM++ 2M Karras"))
        if idx_s >= 0:
            self.combo_sampler.setCurrentIndex(idx_s)
        elif pipe.get("sampler"):
            self.combo_sampler.addItem(pipe["sampler"])
            self.combo_sampler.setCurrentText(pipe["sampler"])

        steps_val = pipe.get("steps", 25)
        self.spin_steps.setValue(steps_val)
        self.slider_steps.setValue(steps_val)

        cfg_val = pipe.get("cfg_scale", 6.0)
        self.spin_cfg.setValue(cfg_val)
        self.slider_cfg.setValue(int(round(cfg_val * 10)))

        clip_val = pipe.get("clip_skip", 1)
        self.spin_clip_skip.setValue(clip_val)
        self.slider_clip_skip.setValue(clip_val)

        idx_u = self.combo_upscaler.findText(pipe.get("hires_upscaler", "4x-UltraSharp"))
        if idx_u >= 0:
            self.combo_upscaler.setCurrentIndex(idx_u)
        elif pipe.get("hires_upscaler"):
            self.combo_upscaler.addItem(pipe["hires_upscaler"])
            self.combo_upscaler.setCurrentText(pipe["hires_upscaler"])

        denoise_val = pipe.get("hires_denoise", 0.32)
        self.spin_hires_denoise.setValue(denoise_val)
        self.slider_hires_denoise.setValue(int(round(denoise_val * 100)))

        # Тумблеры Hires.fix и ADetailer
        enable_hr = bool(pipe.get("enable_hr", False))
        self.switch_enable_hr.setChecked(enable_hr)
        self._update_ad_status_label(pipe)

        # Определение соответствия пресету
        curr_arch = self.combo_arch.currentText()
        curr_samp = self.combo_sampler.currentText()
        curr_sched = self.combo_scheduler.currentText()
        if (curr_arch.startswith("SDXL") and
            curr_samp == "LCM" and
            curr_sched == "Karras" and
            steps_val == 10 and
            abs(cfg_val - 1.3) < 0.05 and
            clip_val == 2 and
            not enable_hr and
            not pipe.get("enable_adetailer", True)):
            self.combo_presets.setCurrentIndex(1)
        elif (curr_arch.startswith("SDXL") and
              curr_samp == "DPM++ 2M" and
              curr_sched == "Karras" and
              steps_val == 28 and
              abs(cfg_val - 5.0) < 0.05 and
              clip_val == 2 and
              not enable_hr and
              not pipe.get("enable_adetailer", True)):
            self.combo_presets.setCurrentIndex(2)
        elif (not curr_arch.startswith("SDXL") and
              curr_samp == "DPM++ SDE Karras" and
              curr_sched == "Karras" and
              steps_val == 25 and
              abs(cfg_val - 5.5) < 0.05 and
              clip_val == 1 and
              enable_hr and
              pipe.get("enable_adetailer", True)):
            self.combo_presets.setCurrentIndex(3)
        else:
            self.combo_presets.setCurrentIndex(0)

        neg = pipe.get("negative_prompt", r.get("negative_prompt", ""))
        self.input_negative_prompt.setPlainText(neg)
        self.input_desc.setPlainText(r.get("routing_description", ""))
        self.input_guidance.setPlainText(r.get("llm_guidance", ""))

        self.chk_is_default.setChecked(bool(r.get("is_default", False)))
        triggers = r.get("style_triggers", [])
        self.edit_style_triggers.setText(", ".join(triggers) if isinstance(triggers, list) else str(triggers))

        for w in widgets_to_block:
            w.blockSignals(False)

        for s in (self.slider_steps, self.slider_cfg, self.slider_hires_denoise, self.slider_clip_skip):
            if hasattr(s, "_adjustHandlePos"):
                s._adjustHandlePos()

        self._update_fp8_button_state()

    def _populate_table(self):
        """Заполняет таблицу моделями и активирует первую строку."""
        self.table.setRowCount(0)
        self.row_widgets.clear()
        for m in self.models:
            self._add_model_row(m)

        if self.table.rowCount() > 0:
            self.table.selectRow(0)
            self.current_selected_row = 0
            self._load_model_to_form(0)

    def _add_model_row(self, model_data: Dict[str, Any]):
        """Добавляет строку модели с современными элементами управления."""
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self.table.setRowHeight(row_idx, 40)

        model_id = model_data.get("id") or f"model_{uuid.uuid4().hex[:6]}"
        is_active = model_data.get("active", model_data.get("is_active", True))
        name = model_data.get("name", "")
        checkpoint = model_data.get("checkpoint", "")
        desc = model_data.get("routing_description") or model_data.get("description", "")
        guidance = model_data.get("llm_guidance", "")
        neg_val = model_data.get("negative_prompt", "").strip() or get_default_negative_prompt(checkpoint, model_id)

        pipeline_data = model_data.get("pipeline")
        if not pipeline_data or not isinstance(pipeline_data, dict):
            pipeline_data = {
                "sampler": "DPM++ 2M Karras",
                "steps": 25,
                "cfg_scale": 6.0,
                "clip_skip": 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "negative_prompt": neg_val
            }

        # Col 0: Active in auto-router CheckBox
        chk_container, chk = create_active_checkbox_cell(parent_widget=self.table, is_active=is_active)
        self.table.setCellWidget(row_idx, 0, chk_container)

        # Col 1: Model Name
        item_name = QTableWidgetItem(name)
        item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row_idx, 1, item_name)

        # Col 2: Checkpoint
        item_ckpt = QTableWidgetItem(checkpoint)
        item_ckpt.setFlags(item_ckpt.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row_idx, 2, item_ckpt)

        # Col 3: Delete Button
        del_container, btn_del = create_delete_button_cell(
            parent_widget=self.table,
            on_delete=lambda _, r_id=model_id: self._remove_model_by_id(r_id)
        )
        self.table.setCellWidget(row_idx, 3, del_container)

        is_default = bool(model_data.get("is_default", False))
        style_triggers = model_data.get("style_triggers", [])
        if isinstance(style_triggers, str):
            style_triggers = [t.strip() for t in style_triggers.split(",") if t.strip()]
        elif isinstance(style_triggers, list):
            style_triggers = [str(t).strip() for t in style_triggers if str(t).strip()]
        else:
            style_triggers = []

        arch = model_data.get("architecture") or pipeline_data.get("architecture") or ("sdxl" if is_xl_model(model_data) else "sd15")
        if "architecture" not in pipeline_data:
            pipeline_data["architecture"] = arch

        row_info = {
            "id": model_id,
            "chk": chk,
            "name": name,
            "architecture": arch,
            "checkpoint": checkpoint,
            "routing_description": desc,
            "llm_guidance": guidance,
            "negative_prompt": neg_val,
            "is_default": is_default,
            "style_triggers": style_triggers,
            "pipeline": pipeline_data,
            "triggers": model_data.get("triggers", []),
            "item_name": item_name,
            "item_ckpt": item_ckpt
        }
        self.row_widgets.append(row_info)

    def _add_empty_model_row(self):
        """Добавляет пустой шаблон новой модели."""
        if self.current_selected_row >= 0:
            self._save_form_to_model(self.current_selected_row)

        new_ckpt = self.available_checkpoints[0] if self.available_checkpoints else ""
        is_xl = is_xl_model(new_ckpt) if new_ckpt else False
        arch = "sdxl" if is_xl else "sd15"
        new_data = {
            "id": f"model_{uuid.uuid4().hex[:6]}",
            "name": "Новая модель",
            "architecture": arch,
            "checkpoint": new_ckpt,
            "routing_description": "",
            "llm_guidance": "",
            "negative_prompt": get_default_negative_prompt(new_ckpt, ""),
            "is_default": False,
            "style_triggers": [],
            "active": True,
            "pipeline": {
                "architecture": arch,
                "sampler": "LCM" if is_xl else "DPM++ 2M Karras",
                "scheduler": "Karras",
                "steps": 10 if is_xl else 25,
                "cfg_scale": 1.3 if is_xl else 6.0,
                "clip_skip": 2 if is_xl else 1,
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "enable_hr": False,
                "enable_adetailer": False,
                "negative_prompt": get_default_negative_prompt(new_ckpt, "")
            }
        }
        self._add_model_row(new_data)
        new_row = self.table.rowCount() - 1
        self.table.selectRow(new_row)
        self.current_selected_row = new_row
        self._load_model_to_form(new_row)
        self.table.scrollToBottom()

    def _remove_model_by_id(self, model_id: str):
        """Удаляет модель из таблицы."""
        idx = next((i for i, r in enumerate(self.row_widgets) if r["id"] == model_id), -1)
        if idx == -1:
            return

        model_name = self.row_widgets[idx].get("name") or model_id
        reply = QMessageBox.question(
            self,
            "Удаление модели",
            f"Вы уверены, что хотите удалить модель '{model_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.table.removeRow(idx)
            self.row_widgets.pop(idx)
            if self.row_widgets:
                next_row = min(idx, len(self.row_widgets) - 1)
                self.table.selectRow(next_row)
                self.current_selected_row = next_row
                self._load_model_to_form(next_row)
            else:
                self.current_selected_row = -1


# ============================================================================
# 2. ForgeCivitaiSyncMixin: Контроллер интеграции с Forge и Civitai
# ============================================================================

class ForgeCivitaiSyncMixin:
    """Миксин фонового опроса чекпоинтов Forge и автонастройки через Civitai."""

    def _start_forge_scan(self, silent: bool = False):
        """Запускает фоновое сканирование Forge без блокировки главного потока интерфейса."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return

        self._scan_silent = silent
        if not silent:
            self.btn_refresh_forge.setEnabled(False)
            self.btn_refresh_forge.setText("Опрос Forge...")
            self.btn_refresh_forge.setIcon(FIF.SYNC)

        self._scan_worker = ForgeScanWorker(self.forge_client, parent=self)
        self._scan_worker.scan_completed.connect(self._on_forge_scan_completed)
        self._scan_worker.start()

    def _on_forge_scan_completed(self, raw_models: list, installed_list: list, upscalers: list):
        if not getattr(self, "_scan_silent", True):
            self.btn_refresh_forge.setEnabled(True)
            self.btn_refresh_forge.setText("Сканировать Forge")
            self.btn_refresh_forge.setIcon(FIF.SYNC)

        # 1. Обновление списка апскейлеров новыми данными из Forge
        if upscalers:
            curr_up = self.combo_upscaler.currentText()
            self.combo_upscaler.blockSignals(True)
            for u in upscalers:
                if self.combo_upscaler.findText(u) == -1:
                    self.combo_upscaler.addItem(u)
            idx_up = self.combo_upscaler.findText(curr_up)
            if idx_up >= 0:
                self.combo_upscaler.setCurrentIndex(idx_up)
            self.combo_upscaler.blockSignals(False)

        # 2. Сбор и сопоставление чекпоинтов
        found_checkpoints: List[str] = []
        if raw_models:
            for item in raw_models:
                if isinstance(item, dict):
                    fn = item.get("filename")
                    title = item.get("title")
                    model_name = item.get("model_name")
                    name = Path(fn).name if fn else (title or model_name)
                    if name and name not in found_checkpoints:
                        found_checkpoints.append(name)

        if installed_list:
            for cp in installed_list:
                cp_name = Path(cp).name
                if cp_name and cp_name not in found_checkpoints:
                    found_checkpoints.append(cp_name)

        if not found_checkpoints:
            found_checkpoints = list(self.available_checkpoints)

        self.available_checkpoints = found_checkpoints

        # 3. Обновление комбобокса чекпоинтов в форме редактора
        curr = self.combo_checkpoint.currentText().strip()
        self.combo_checkpoint.blockSignals(True)
        self.combo_checkpoint.clear()
        for cp in self.available_checkpoints:
            self.combo_checkpoint.addItem(cp)
        if curr and self.combo_checkpoint.findText(curr) == -1:
            self.combo_checkpoint.addItem(curr)
        idx = self.combo_checkpoint.findText(curr)
        if idx >= 0:
            self.combo_checkpoint.setCurrentIndex(idx)
        elif curr:
            self.combo_checkpoint.setEditText(curr)
        self.combo_checkpoint.blockSignals(False)

        # 4. Авторегистрация новых чекпоинтов
        existing_ckpts = {Path(r.get("checkpoint", "")).name.lower() for r in self.row_widgets}
        new_added = 0
        if raw_models or installed_list:
            for cp in found_checkpoints:
                cp_norm = normalize_checkpoint_stem(cp)

                # Проверяем, есть ли уже модель с таким нормализованным именем в существующих карточках
                matched_row = None
                for r in self.row_widgets:
                    existing_norm = normalize_checkpoint_stem(r.get("checkpoint", ""))
                    existing_id_norm = normalize_checkpoint_stem(r.get("id", "").replace("auto_", ""))
                    if cp_norm in (existing_norm, existing_id_norm):
                        matched_row = r
                        break

                if matched_row is not None:
                    if matched_row.get("checkpoint") != cp:
                        matched_row["checkpoint"] = cp
                    continue

                if Path(cp).name.lower() not in existing_ckpts:
                    stem = Path(cp).stem
                    stem_lower = stem.lower()
                    LIGHT_SPEED_MARKERS = ("light", "lightning", "turbo", "lcm", "dmd", "hyper")
                    is_fast = any(m in stem_lower for m in LIGHT_SPEED_MARKERS)
                    is_xl = is_xl_model(cp)
                    if is_xl:
                        xl_pipe = {
                            "architecture": "sdxl",
                            "sampler": "LCM",
                            "scheduler": "Karras",
                            "steps": 10,
                            "cfg_scale": 1.3,
                            "clip_skip": 2,
                            "hires_upscaler": "4x-UltraSharp",
                            "hires_denoise": 0.32,
                            "enable_hr": False,
                            "enable_adetailer": False,
                            "negative_prompt": get_default_negative_prompt(cp, stem)
                        }
                        if is_fast:
                            xl_pipe["adetailer_face_denoise"] = 0.30
                            xl_pipe["adetailer_hands_denoise"] = 0.36
                        new_model_data = {
                            "id": f"auto_{re.sub(r'[^a-zA-Z0-9_]', '_', stem.lower()).strip('_')}",
                            "name": stem,
                            "architecture": "sdxl",
                            "checkpoint": cp,
                            "routing_description": "Новая SDXL модель (требуется настройка описания для роутинга)",
                            "llm_guidance": "",
                            "negative_prompt": get_default_negative_prompt(cp, stem),
                            "active": True,
                            "pipeline": xl_pipe
                        }
                    else:
                        sd15_pipe = {
                            "architecture": "sd15",
                            "sampler": "DPM++ 2M Karras",
                            "scheduler": "Karras",
                            "steps": 25,
                            "cfg_scale": 6.0,
                            "clip_skip": 1,
                            "hires_upscaler": "4x-UltraSharp",
                            "hires_denoise": 0.32,
                            "enable_hr": False,
                            "enable_adetailer": False,
                            "negative_prompt": get_default_negative_prompt(cp, stem)
                        }
                        if is_fast:
                            sd15_pipe["adetailer_face_denoise"] = 0.30
                            sd15_pipe["adetailer_hands_denoise"] = 0.36
                        new_model_data = {
                            "id": f"auto_{re.sub(r'[^a-zA-Z0-9_]', '_', stem.lower()).strip('_')}",
                            "name": stem,
                            "architecture": "sd15",
                            "checkpoint": cp,
                            "routing_description": "Новая модель (требуется настройка описания для роутинга)",
                            "llm_guidance": "",
                            "negative_prompt": get_default_negative_prompt(cp, stem),
                            "active": True,
                            "pipeline": sd15_pipe
                        }
                    self._add_model_row(new_model_data)
                    existing_ckpts.add(Path(cp).name.lower())
                    new_added += 1

        if new_added > 0:
            self._save_models_to_json()

        if not getattr(self, "_scan_silent", True):
            msg = f"Успешно получено {len(found_checkpoints)} чекпоинтов из SD Forge."
            if new_added > 0:
                msg += f"\n\nАвтоматически добавлено новых моделей: {new_added}."
            QMessageBox.information(self, "Сканирование чекпоинтов Forge", msg)

        self._scan_worker = None

    def _scan_forge_checkpoints(self, silent: bool = False):
        """Псевдоним для обратной совместимости."""
        self._start_forge_scan(silent=silent)

    def _on_autofill_params_clicked(self):
        """Запрашивает параметры Civitai в неблокирующем QThread."""
        if self.current_selected_row < 0 or self.current_selected_row >= len(self.row_widgets):
            QMessageBox.warning(self, "Внимание", "Сначала выберите модель в списке.")
            return

        checkpoint = self.combo_checkpoint.currentText().strip()
        if not checkpoint:
            QMessageBox.warning(self, "Внимание", "Сначала выберите файл чекпоинта модели.")
            return

        self._autofill_target_row = self.current_selected_row
        self.btn_autofill_params.setEnabled(False)
        self.btn_autofill_params.setText("Опрос Civitai...")
        self.btn_autofill_params.setIcon(FIF.SYNC)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        self._civitai_worker = CivitaiParamsWorker(checkpoint, parent=self)
        self._civitai_worker.params_ready.connect(self._on_civitai_params_ready)
        self._civitai_worker.error_occurred.connect(self._on_civitai_params_error)
        self._civitai_worker.start()

    def _on_civitai_params_ready(self, params: dict):
        try:
            target_row = getattr(self, "_autofill_target_row", self.current_selected_row)
            if target_row < 0 or target_row >= len(self.row_widgets):
                return

            checkpoint_name = self.combo_checkpoint.currentText().strip() or "модели"
            status = params.get("status", "default")

            if status != "success":
                msg_text = (
                    f"Не удалось обнаружить параметры для модели «{checkpoint_name}» на Civitai.\n\n"
                    "Установить рекомендуемые настройки по умолчанию?"
                )
                dialog = MessageBox("Civitai: Параметры не найдены", msg_text, self)
                dialog.yesButton.setText("Да")
                dialog.cancelButton.setText("Нет")

                if not dialog.exec():
                    InfoBar.warning(
                        title="Отменено",
                        content="Текущие параметры модели оставлены без изменений.",
                        parent=self,
                        position=InfoBarPosition.TOP_RIGHT,
                        duration=3500
                    )
                    return

            is_active = (target_row == self.current_selected_row)
            if is_active:
                widgets_to_block = [
                    self.combo_sampler, self.spin_steps, self.slider_steps,
                    self.spin_cfg, self.slider_cfg, self.spin_clip_skip,
                    self.slider_clip_skip, self.combo_upscaler,
                    self.spin_hires_denoise, self.slider_hires_denoise,
                    self.input_negative_prompt
                ]
                for w in widgets_to_block:
                    w.blockSignals(True)

                idx_s = self.combo_sampler.findText(params.get("sampler", "DPM++ 2M Karras"))
                if idx_s >= 0:
                    self.combo_sampler.setCurrentIndex(idx_s)
                elif params.get("sampler"):
                    self.combo_sampler.addItem(params["sampler"])
                    self.combo_sampler.setCurrentText(params["sampler"])

                steps_val = params.get("steps", 25)
                self.spin_steps.setValue(steps_val)
                self.slider_steps.setValue(steps_val)

                cfg_val = params.get("cfg_scale", 6.0)
                self.spin_cfg.setValue(cfg_val)
                self.slider_cfg.setValue(int(round(cfg_val * 10)))

                clip_val = params.get("clip_skip", 1)
                self.spin_clip_skip.setValue(clip_val)
                self.slider_clip_skip.setValue(clip_val)

                idx_u = self.combo_upscaler.findText(params.get("hires_upscaler", "4x-UltraSharp"))
                if idx_u >= 0:
                    self.combo_upscaler.setCurrentIndex(idx_u)
                elif params.get("hires_upscaler"):
                    self.combo_upscaler.addItem(params["hires_upscaler"])
                    self.combo_upscaler.setCurrentText(params["hires_upscaler"])

                denoise_val = params.get("hires_denoise", 0.32)
                self.spin_hires_denoise.setValue(denoise_val)
                self.slider_hires_denoise.setValue(int(round(denoise_val * 100)))

                if params.get("negative_prompt"):
                    self.input_negative_prompt.setPlainText(params["negative_prompt"])

                for w in widgets_to_block:
                    w.blockSignals(False)

                self._save_form_to_model(target_row)
                r = self.row_widgets[target_row]
                pipe = r.setdefault("pipeline", {})
                if "adetailer_face_denoise" in params:
                    pipe["adetailer_face_denoise"] = params["adetailer_face_denoise"]
                if "adetailer_hands_denoise" in params:
                    pipe["adetailer_hands_denoise"] = params["adetailer_hands_denoise"]
            else:
                r = self.row_widgets[target_row]
                pipe = r.setdefault("pipeline", {})
                for k in ("sampler", "steps", "cfg_scale", "clip_skip", "hires_upscaler", "hires_denoise", "adetailer_face_denoise", "adetailer_hands_denoise"):
                    if k in params:
                        pipe[k] = params[k]
                if params.get("negative_prompt"):
                    pipe["negative_prompt"] = params["negative_prompt"]
                    r["negative_prompt"] = params["negative_prompt"]

            if status == "success":
                msg = params.get("message", "Параметры успешно получены из Civitai.")
                InfoBar.success(
                    title="Параметры Civitai загружены",
                    content=f"{msg}\nНажмите «Сохранить», чтобы зафиксировать изменения.",
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=5000
                )
            else:
                InfoBar.info(
                    title="Параметры по умолчанию",
                    content="В форму подставлены стандартные настройки. Нажмите «Сохранить», чтобы зафиксировать.",
                    parent=self,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=4500
                )

        except Exception as e:
            logger.error(f"[ForgeCivitaiSyncMixin] Сбой автонастройки: {e}", exc_info=True)
            InfoBar.error(
                title="Ошибка автонастройки",
                content=f"Произошла ошибка: {e}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000
            )
        finally:
            self._civitai_worker = None
            QApplication.restoreOverrideCursor()
            self.btn_autofill_params.setEnabled(True)
            self.btn_autofill_params.setText("Автонастройка (Civitai)")
            self.btn_autofill_params.setIcon(FIF.CLOUD_DOWNLOAD)

    def _on_civitai_params_error(self, err_msg: str):
        try:
            logger.error(f"[ForgeCivitaiSyncMixin] Сбой автонастройки: {err_msg}")
            InfoBar.error(
                title="Ошибка сети Civitai",
                content=f"Не удалось получить данные с сервера Civitai: {err_msg}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4500
            )
        finally:
            self._civitai_worker = None
            QApplication.restoreOverrideCursor()
            self.btn_autofill_params.setEnabled(True)
            self.btn_autofill_params.setText("Автонастройка (Civitai)")
            self.btn_autofill_params.setIcon(FIF.CLOUD_DOWNLOAD)

    def _open_civitai_catalog(self):
        """Открывает нативный браузер каталога Civitai."""
        from ui.dialogs.civitai_catalog_dialog import CivitaiCatalogDialog
        target_dir = None
        if self.available_checkpoints:
            installed_list = self.forge_client.get_installed_checkpoints()
            if installed_list:
                target_dir = Path(installed_list[0]).parent
        dlg = CivitaiCatalogDialog(target_dir=target_dir, parent=self)
        dlg.model_installed.connect(self._on_civitai_model_installed)
        try:
            dlg.exec()
        finally:
            try:
                dlg.model_installed.disconnect(self._on_civitai_model_installed)
            except Exception:
                pass

    def _on_civitai_model_installed(self, model_info: dict):
        """Автоматически регистрирует скачанную с Civitai модель в списке роутера."""
        ckpt_name = model_info.get("filename", "")
        if ckpt_name and ckpt_name not in self.available_checkpoints:
            self.available_checkpoints.append(ckpt_name)
            self.combo_checkpoint.addItem(ckpt_name)

        is_xl = is_xl_model(ckpt_name)
        arch = "sdxl" if is_xl else "sd15"
        new_data = {
            "id": f"civitai_{model_info.get('id', uuid.uuid4().hex[:6])}",
            "name": model_info.get("name", "Новая модель"),
            "architecture": arch,
            "checkpoint": ckpt_name,
            "routing_description": model_info.get("description", ""),
            "llm_guidance": model_info.get("guidance", ""),
            "negative_prompt": get_default_negative_prompt(ckpt_name, model_info.get("name", "")),
            "active": True,
            "triggers": model_info.get("triggers", []),
            "pipeline": {
                "architecture": arch,
                "sampler": model_info.get("sampler", "LCM" if is_xl else "DPM++ 2M Karras"),
                "scheduler": model_info.get("scheduler", "Karras"),
                "steps": model_info.get("steps", 10 if is_xl else 25),
                "cfg_scale": model_info.get("cfg_scale", 1.3 if is_xl else 6.0),
                "clip_skip": model_info.get("clip_skip", 2 if is_xl else 1),
                "hires_upscaler": "4x-UltraSharp",
                "hires_denoise": 0.32,
                "enable_hr": False,
                "enable_adetailer": False,
                "negative_prompt": get_default_negative_prompt(ckpt_name, model_info.get("name", ""))
            }
        }
        self._add_model_row(new_data)
        new_row = self.table.rowCount() - 1
        self.table.selectRow(new_row)
        self.current_selected_row = new_row
        self._load_model_to_form(new_row)
        self.table.scrollToBottom()


# ============================================================================
# 3. FP8ConversionMixin: Контроллер квантования SDXL FP8
# ============================================================================

class FP8ConversionMixin:
    """Миксин квантования моделей SDXL в FP8 e4m3fn с защитой целостности."""

    def _resolve_converter_script(self) -> Optional[Path]:
        return resolve_converter_script()

    def _verify_python(self, py_path: str) -> bool:
        return verify_python(py_path)

    def _resolve_torch_python(self) -> Optional[str]:
        return resolve_torch_python(parent_widget=self)

    def _resolve_selected_checkpoint_path(self, ckpt_name: str) -> Optional[Path]:
        return resolve_checkpoint_path(ckpt_name, forge_client=getattr(self, "forge_client", None))

    def _update_fp8_button_state(self):
        """Обновляет доступность кнопки конвертации FP8 (O(1))."""
        if not hasattr(self, "btn_convert_fp8"):
            return
        if self._fp8_worker is not None and self._fp8_worker.isRunning():
            self.btn_convert_fp8.setEnabled(False)
            return

        ckpt = self.combo_checkpoint.currentText().strip()
        is_safetensors = ckpt.lower().endswith(".safetensors")
        is_already_fp8 = any(k in ckpt.lower() for k in ("_fp8", "-fp8", "e4m3fn", "_quant"))

        self.btn_convert_fp8.setEnabled(bool(is_safetensors and not is_already_fp8))
        self.btn_convert_fp8.setToolTip("Конвертирует UNet модели SDXL в нативный FP8. Освобождает 4 ГБ VRAM.")

    def _on_start_fp8_conversion(self):
        """Запускает асинхронное сжатие выбранного чекпоинта SDXL в нативный FP8."""
        if self._fp8_worker is not None and self._fp8_worker.isRunning():
            return

        ckpt_name = self.combo_checkpoint.currentText().strip()
        if not ckpt_name:
            InfoBar.warning("Внимание", "Сначала выберите файл чекпоинта.", parent=self, position=InfoBarPosition.TOP)
            return

        # 1. Разрешение скрипта конвертера
        script_path = self._resolve_converter_script()
        if not script_path:
            InfoBar.error("Ошибка", "Скрипт convert_sdxl_to_fp8.py не найден в папках models/ или scripts/.", parent=self, position=InfoBarPosition.TOP)
            return

        # 2. Разрешение пути к исходному файлу чекпоинта
        ckpt_path = self._resolve_selected_checkpoint_path(ckpt_name)
        if not ckpt_path or not ckpt_path.is_file():
            InfoBar.error("Ошибка", f"Файл чекпоинта '{ckpt_name}' не найден на диске.", parent=self, position=InfoBarPosition.TOP)
            return

        # Pre-flight Size Guard
        try:
            file_size_gb = ckpt_path.stat().st_size / (1024 ** 3)
            if file_size_gb < 4.5:
                reply = QMessageBox.question(
                    self,
                    "Модель уже оптимизирована",
                    f"Чекпоинт '{ckpt_name}' уже имеет компактный размер ({file_size_gb:.1f} ГБ, типичный SDXL FP16 ~6.6 ГБ).\n\n"
                    f"Похоже, модель уже сжата в FP8.\nВы действительно хотите запустить сжатие повторно?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        except Exception as e:
            logger.debug(f"Pre-flight size check error: {e}")

        # 3. Разрешение интерпретатора Python с PyTorch
        python_exe = self._resolve_torch_python()
        if not python_exe:
            InfoBar.warning("Внимание", "Не найден интерпретатор Python с модулями PyTorch и safetensors.", parent=self, position=InfoBarPosition.TOP)
            return

        # Фиксация контекста задачи конвертации
        self._fp8_target_row = self.current_selected_row
        self._fp8_source_path = ckpt_path
        self._fp8_source_name = ckpt_name

        # Блокировка UI
        self.btn_convert_fp8.setEnabled(False)
        self.btn_convert_fp8.setText("⚡ Сжатие в процессе...")
        self.combo_checkpoint.setEnabled(False)
        self.table.setEnabled(False)
        if hasattr(self, "btn_add_model"):
            self.btn_add_model.setEnabled(False)
        if hasattr(self, "btn_remove_model"):
            self.btn_remove_model.setEnabled(False)
        self.progress_fp8.setVisible(True)
        self.progress_fp8.setRange(0, 0)
        self.lbl_fp8_status.setVisible(True)
        self.lbl_fp8_status.setText("Запуск конвертера FP8 (e4m3fn)...")
        QApplication.processEvents()

        self._fp8_worker = FP8ConvertWorker(python_exe, script_path, ckpt_path, parent=self)
        self._fp8_worker.progress_updated.connect(self._on_fp8_progress)
        self._fp8_worker.conversion_finished.connect(self._on_fp8_finished)
        self._fp8_worker.conversion_failed.connect(self._on_fp8_failed)
        self._fp8_worker.start()

    def _on_fp8_progress(self, msg: str):
        """Отображает текущий прогресс конвертации в UI."""
        self.lbl_fp8_status.setText(msg)
        match = re.search(r"(\d+)/(\d+)", msg)
        if match:
            cur, total = int(match.group(1)), int(match.group(2))
            self.progress_fp8.setRange(0, total)
            self.progress_fp8.setValue(cur)

    def _on_fp8_finished(self, new_file_path: str):
        """Обрабатывает успешное завершение конвертации в FP8 с поддержкой Atomic In-Place Swap."""
        new_path = Path(new_file_path)
        new_name = new_path.name

        source_path = getattr(self, "_fp8_source_path", None)
        target_row = getattr(self, "_fp8_target_row", self.current_selected_row)
        if not source_path:
            orig_name = getattr(self, "_fp8_source_name", "") or self.combo_checkpoint.currentText().strip()
            source_path = self._resolve_selected_checkpoint_path(orig_name)

        # 1. Валидация целостности созданного FP8 файла
        if not (new_path.is_file() and new_path.stat().st_size > 0):
            logger.error(f"[FP8] Сжатый файл отсутствует или пуст: {new_path}")
            self._on_fp8_failed("Созданный файл поврежден или имеет нулевой размер.")
            return

        final_checkpoint_name = new_name

        # 2. Обработка режима перезаписи (Atomic In-Place Swap) или сохранения исходника
        if self.chk_delete_source.isChecked() and source_path and source_path.is_file():
            # Security Guard: Проверка родства имен перед удалением (Name Affinity Guard)
            src_norm = normalize_checkpoint_stem(source_path.name)
            new_norm = normalize_checkpoint_stem(new_path.name)
            if src_norm != new_norm:
                logger.critical(
                    f"[FP8 Security Guard] ЗАПРЕЩЕНО удаление/замена: имена стэмов не совпадают! "
                    f"Исходник: '{source_path.name}' ({src_norm}) != Новый: '{new_path.name}' ({new_norm})"
                )
                InfoBar.error(
                    "Ошибка безопасности",
                    "Обнаружено несоответствие имен исходного и сжатого файла. Исходник сохранен.",
                    parent=self,
                    position=InfoBarPosition.TOP
                )
            else:
                # Безопасный Atomic In-Place Swap
                try:
                    deleted = False
                    try:
                        import send2trash
                        send2trash.send2trash(str(source_path))
                        deleted = True
                    except (ImportError, Exception):
                        try:
                            os.remove(source_path)
                            deleted = True
                        except Exception as e:
                            logger.warning(f"Не удалось удалить исходный файл {source_path}: {e}")

                    if deleted:
                        if new_path.resolve() != source_path.resolve():
                            os.replace(new_path, source_path)
                            final_checkpoint_name = source_path.name
                            logger.info(f"[FP8] Файл успешно заменен с сохранением исходного имени: {final_checkpoint_name}")
                except Exception as e:
                    logger.error(f"[FP8] Ошибка при замене исходного файла: {e}", exc_info=True)

        # 3. Вызов refresh_checkpoints в Forge
        if hasattr(self, "forge_client") and self.forge_client:
            try:
                self.forge_client.refresh_checkpoints()
            except Exception as e:
                logger.debug(f"refresh_checkpoints error: {e}")

        # 4. Обновление списков доступных чекпоинтов
        if final_checkpoint_name not in self.available_checkpoints:
            self.available_checkpoints.append(final_checkpoint_name)

        self.combo_checkpoint.blockSignals(True)
        if self.combo_checkpoint.findText(final_checkpoint_name) == -1:
            self.combo_checkpoint.addItem(final_checkpoint_name)
        idx_final = self.combo_checkpoint.findText(final_checkpoint_name)
        if idx_final >= 0:
            self.combo_checkpoint.setCurrentIndex(idx_final)
        else:
            self.combo_checkpoint.setEditText(final_checkpoint_name)
        self.combo_checkpoint.blockSignals(False)

        # 5. Обновление записи строго в строке target_row
        if 0 <= target_row < len(self.row_widgets):
            r = self.row_widgets[target_row]
            r["checkpoint"] = final_checkpoint_name
            item = self.table.item(target_row, 2)
            if item:
                item.setText(final_checkpoint_name)

        # Фиксация изменений в data/art_director_models.json
        if hasattr(self, "_save_models_to_json"):
            self._save_models_to_json()

        # 6. Автоматическое применение пресета SDXL (Turbo или Classic)
        turbo_markers = ("turbo", "dmd", "lightning", "hyper", "lcm")
        is_turbo = any(m in final_checkpoint_name.lower() or m in self.input_name.text().lower() for m in turbo_markers)
        if is_turbo:
            self.combo_presets.setCurrentIndex(1)
        else:
            self.combo_presets.setCurrentIndex(2)

        # 7. Восстановление состояния UI и разблокировка контролов
        self.table.setEnabled(True)
        self.combo_checkpoint.setEnabled(True)
        if hasattr(self, "btn_add_model"):
            self.btn_add_model.setEnabled(True)
        if hasattr(self, "btn_remove_model"):
            self.btn_remove_model.setEnabled(True)
        self.btn_convert_fp8.setText("⚡ Сжать в FP8 (e4m3fn)")
        self._update_fp8_button_state()
        self.progress_fp8.setVisible(False)
        self.lbl_fp8_status.setVisible(False)
        self._fp8_worker = None
        self._fp8_source_path = None
        self._fp8_source_name = ""
        self._fp8_target_row = -1

        InfoBar.success(
            "Конвертация завершена",
            f"Чекпоинт успешно оптимизирован в FP8: {final_checkpoint_name}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000
        )

    def _on_fp8_failed(self, err_msg: str):
        """Обрабатывает сбой конвертации в FP8 с полной разблокировкой UI."""
        self.table.setEnabled(True)
        self.combo_checkpoint.setEnabled(True)
        if hasattr(self, "btn_add_model"):
            self.btn_add_model.setEnabled(True)
        if hasattr(self, "btn_remove_model"):
            self.btn_remove_model.setEnabled(True)
        self.btn_convert_fp8.setText("⚡ Сжать в FP8 (e4m3fn)")
        self._update_fp8_button_state()
        self.progress_fp8.setVisible(False)
        self.lbl_fp8_status.setVisible(False)
        self._fp8_worker = None
        self._fp8_source_path = None
        self._fp8_source_name = ""
        self._fp8_target_row = -1

        logger.error(f"[FP8ConversionMixin] Сбой сжатия FP8:\n{err_msg}")
        try:
            w = MessageBox(
                "Сбой сжатия FP8",
                f"Не удалось сжать чекпоинт в FP8.\n\nДетали ошибки:\n{err_msg}\n\n(Полный лог сохранен в logs/neonroute.log)",
                self
            )
            w.yesButton.setText("Закрыть")
            w.cancelButton.setVisible(False)
            w.exec()
        except Exception:
            InfoBar.error(
                "Сбой сжатия FP8",
                f"Ошибка: {err_msg}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=8000
            )
