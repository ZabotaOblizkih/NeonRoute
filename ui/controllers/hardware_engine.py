"""
Hardware & Backend Engine Controller (Mixin) for NeonRoute MainWindow.
Handles process lifecycles (LM Studio, SD WebUI Forge), health monitoring,
hardware telemetry, model discovery/switching, VRAM memory juggling,
and Forge art generation tasks.
"""

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import QInputDialog

from config import MemoryStrategy, OUTPUTS_DIR, EMERGENCY_VRAM_THRESHOLD_MB, FORGE_LAUNCHER_PATH
from services.task_queue import InferenceTask
from services.session_manager import SessionManager
from services.art_director import GenerationPlan
from services.model_router import normalize_model_choice
from utils.config_manager import ConfigManager
from ui.workers.model_discovery_worker import ModelDiscoveryWorker
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.HardwareEngine")


class HardwareEngineMixin:
    """
    Единый фасад оборудования и бэкендов для MainWindow.
    """
    app_config = {}
    _is_locating_forge: bool = False
    current_session = None

    # -------------------------------------------------------------------------
    # 1. Мониторинг здоровья серверов и телеметрия VRAM/RAM
    # -------------------------------------------------------------------------
    @pyqtSlot(str, str)
    def _on_server_state_changed(self, old_state: str, new_state: str):
        logger.info(f"[ServerState] {old_state} -> {new_state}")
        norm = (new_state or "").lower()
        if norm == "ready":
            self.pill_lm.set_status(True, "llama-server Online")
            self.btn_lms_toggle.setText("⚡ Стоп LLM")
            self.btn_lms_toggle.setToolTip("Остановить локальный сервер llama-server.exe (порт 1234)")
        elif norm in ("starting", "restoring", "switching_model"):
            self.pill_lm.set_status(False, f"Запуск ({new_state})...")
            self.btn_lms_toggle.setText("⚡ Стоп LLM")
            self.btn_lms_toggle.setToolTip("Локальный сервер llama-server.exe запускается...")
        elif norm in ("stopped", "error"):
            self.pill_lm.set_status(False, "Offline")
            self.btn_lms_toggle.setText("⚡ Старт LLM")
            self.btn_lms_toggle.setToolTip("Запустить / остановить локальный сервер llama-server.exe (порт 1234)")
        elif norm == "evicted_for_forge":
            self.pill_lm.set_status(False, "Выгружен для Forge")
            self.btn_lms_toggle.setText("⚡ Старт LLM")
            self.btn_lms_toggle.setToolTip("LLM выгружен из VRAM для работы Forge. Нажмите для ручного возврата.")

    @pyqtSlot(dict)
    def _on_health_updated(self, health: dict):
        # 1. LM Studio
        lm_ok = health.get("lm_ok", False)
        lm_msg = health.get("lm_msg", "LM Studio Offline")
        self.pill_lm.set_status(lm_ok, lm_msg)
        pm_state = getattr(self.process_manager, "server_state", None) if hasattr(self, "process_manager") else None
        is_lms_up = lm_ok or (pm_state is not None and getattr(pm_state, "name", "") in ("READY", "STARTING"))
        if is_lms_up:
            self.btn_lms_toggle.setText("⚡ Стоп LLM")
            self.btn_lms_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #2D1A15;
                    color: #FB923C;
                    border: 1px solid #F97316;
                    border-radius: 4px;
                    padding: 2px 7px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #F97316;
                    color: #FFFFFF;
                }
            """)
        else:
            self.btn_lms_toggle.setText("⚡ Старт LLM")
            self.btn_lms_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #111A28;
                    color: #38BDF8;
                    border: 1px solid #1E3A5F;
                    border-radius: 4px;
                    padding: 2px 7px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    border-color: #38BDF8;
                    background-color: #17253B;
                }
            """)

        # 2. Forge
        forge_ok = health.get("forge_ok", False)
        forge_msg = health.get("forge_msg", "SD Forge Offline")
        self.pill_forge.set_status(forge_ok, forge_msg)

        if forge_ok:
            self._forge_launching = False
            self.btn_forge_toggle.setText("⏹ Остановить Forge")
            self.btn_forge_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #2D1120;
                    color: #FF007F;
                    border: 1px solid #FF007F;
                    border-radius: 4px;
                    padding: 2px 9px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #FF007F;
                    color: #FFFFFF;
                }
            """)
        elif getattr(self, "_forge_launching", False):
            self.btn_forge_toggle.setText("⏳ Запуск Forge... (ожидание 7860)")
            self.btn_forge_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #2D2311;
                    color: #FBBF24;
                    border: 1px solid #F59E0B;
                    border-radius: 4px;
                    padding: 2px 9px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #F59E0B;
                    color: #050B14;
                }
            """)
        else:
            self.btn_forge_toggle.setText("▶ Запустить Forge")
            self.btn_forge_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #111A28;
                    color: #00F0FF;
                    border: 1px solid #00F0FF;
                    border-radius: 4px;
                    padding: 2px 9px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #00F0FF;
                    color: #050B14;
                }
            """)

        # 3. F5-TTS
        tts_ok = health.get("tts_ok", False)
        tts_msg = health.get("tts_msg", "F5-TTS Offline")
        self.pill_tts.set_status(tts_ok, tts_msg)

    def prompt_locate_forge(self, parent_widget=None) -> bool:
        """
        Интерактивный диалог выбора рабочей директории SD WebUI Forge:
        1. Спрашивает пользователя, хочет ли он указать папку вручную.
        2. При отказе возвращает False, генерация отключается, приложение работает дальше.
        3. При согласии открывает выбор папки, валидирует маркеры запуска и сохраняет путь.
        """
        if getattr(self, "_is_locating_forge", False):
            return False
        self._is_locating_forge = True
        try:
            from PyQt6.QtWidgets import QMessageBox, QFileDialog, QWidget
            from core.forge_locator import is_valid_forge_dir, update_server_config_forge_root
            import config

            target_parent = parent_widget or (self if isinstance(self, QWidget) else None)

            msg_box = QMessageBox(target_parent)
            msg_box.setWindowTitle("SD WebUI Forge не обнаружен")
            msg_box.setText(
                "Не удалось автоматически найти установку SD WebUI Forge на ваших дисках.\n\n"
                "Хотите указать папку вручную?"
            )
            msg_box.setIcon(QMessageBox.Icon.Question)
            btn_locate = msg_box.addButton("Указать папку...", QMessageBox.ButtonRole.AcceptRole)
            btn_cancel = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            msg_box.exec()

            if msg_box.clickedButton() != btn_locate:
                if hasattr(self, "statusBar") and self.statusBar():
                    self.statusBar().showMessage("SD WebUI Forge не настроен (генерация отключена)", 5000)
                return False

            while True:
                selected_dir = QFileDialog.getExistingDirectory(
                    target_parent,
                    "Выберите директорию установки SD WebUI Forge",
                    str(Path.home())
                )
                if not selected_dir:
                    if hasattr(self, "statusBar") and self.statusBar():
                        self.statusBar().showMessage("SD WebUI Forge не настроен (генерация отключена)", 5000)
                    return False

                if is_valid_forge_dir(selected_dir):
                    update_server_config_forge_root(selected_dir)
                    if hasattr(self, "btn_forge_toggle") and self.btn_forge_toggle:
                        self.btn_forge_toggle.setToolTip(f"Запустить SD WebUI Forge ({config.FORGE_LAUNCHER_PATH})")
                    if hasattr(self, "statusBar") and self.statusBar():
                        self.statusBar().showMessage(f"SD WebUI Forge настроен: {config.FORGE_ROOT_DIR}", 4000)
                    return True
                else:
                    retry_box = QMessageBox(target_parent)
                    retry_box.setWindowTitle("Файлы запуска не найдены")
                    retry_box.setIcon(QMessageBox.Icon.Warning)
                    retry_box.setText(
                        "В выбранной папке не найдены файлы запуска (forge_launcher.bat или webui-user.bat).\n\n"
                        "Хотите попробовать снова?"
                    )
                    btn_retry = retry_box.addButton("Попробовать снова", QMessageBox.ButtonRole.AcceptRole)
                    btn_abort = retry_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
                    retry_box.exec()
                    if retry_box.clickedButton() != btn_retry:
                        if hasattr(self, "statusBar") and self.statusBar():
                            self.statusBar().showMessage("SD WebUI Forge не настроен (генерация отключена)", 5000)
                        return False
        finally:
            self._is_locating_forge = False

    def _toggle_forge_server(self):
        if self.process_manager.is_forge_running() or self.pill_forge.is_online:
            self._forge_launching = False
            self.statusBar().showMessage("Остановка SD WebUI Forge и освобождение VRAM...")
            self.process_manager.stop_forge_async()
            self.btn_forge_toggle.setText("▶ Запустить Forge")
        else:
            import config
            if not config.FORGE_LAUNCHER_PATH.exists():
                if not self.prompt_locate_forge():
                    self._forge_launching = False
                    self.btn_forge_toggle.setText("▶ Запустить Forge")
                    return
            self._forge_launching = True
            self.statusBar().showMessage(f"Запуск SD WebUI Forge ({config.FORGE_LAUNCHER_PATH})...")
            self.btn_forge_toggle.setText("⏳ Запуск Forge... (ожидание 7860)")
            self.btn_forge_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #2D2311;
                    color: #FBBF24;
                    border: 1px solid #F59E0B;
                    border-radius: 4px;
                    padding: 2px 9px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #F59E0B;
                    color: #050B14;
                }
            """)
            self.process_manager.start_forge_async()

    def _toggle_lms_server(self):
        if self.process_manager.is_lm_studio_running() or self.pill_lm.is_online:
            self.statusBar().showMessage("Остановка LM Studio сервера...")
            if hasattr(self, "_cancel_lms_reload") and callable(self._cancel_lms_reload):
                self._cancel_lms_reload()
            if hasattr(self, "task_queue") and self.task_queue:
                self.task_queue.abort_current()
                if hasattr(self.task_queue, "clear_queue") and callable(self.task_queue.clear_queue):
                    self.task_queue.clear_queue()
            if hasattr(self, "lm_client") and self.lm_client:
                self.lm_client.abort()
            self.process_manager.stop_lm_studio_async()
        else:
            self.statusBar().showMessage("Запуск LM Studio сервера (lms server start)...")
            self.process_manager.start_lm_studio_async()

    def _on_process_action_completed(self, action_name: str, success: bool, message: str):
        if "lms_load" in action_name:
            if not self.is_busy:
                self.btn_send.setEnabled(True)
            if success:
                # Синхронизация состояния синглтона LMStudioClient для исключения повторного сброса
                if hasattr(self, "lm_client") and self.lm_client and getattr(self, "active_lms_model", ""):
                    self.lm_client._last_loaded_model_id = self.active_lms_model
                    self.lm_client._is_model_unloaded = False
                    is_rp = (getattr(self, "current_mode", "assistant") == "roleplay")
                    is_eng = getattr(self, "is_engineer_mode_active", lambda: False)()
                    m_key = "engineer" if is_eng else ("roleplay" if is_rp else "assistant")
                    def_ctx = 28672 if is_eng else (24576 if is_rp else 14336)
                    self.lm_client._current_loaded_ctx = self.app_config.get(f"context_length_{m_key}", def_ctx)
                self.statusBar().showMessage(f"✅ Модель '{self.active_lms_model}' готова к работе.", 5000)
            else:
                self.statusBar().showMessage(f"⚠️ {message}", 6000)
        elif action_name == "lms_load_fallback":
            self.statusBar().showMessage(f"⚠️ {message}", 7000)
            self._append_message("Система", f"⚠️ **Внимание:** {message}", is_user=False, is_error=True)
            return
        elif action_name == "lms_stop":
            self.statusBar().showMessage(message, 5000)
            self.pill_lm.set_status(False, "LM Studio Offline")
            self.btn_lms_toggle.setText("⚡ Старт LLM")
            self.btn_lms_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #111A28;
                    color: #00F0FF;
                    border: 1px solid #00F0FF;
                    border-radius: 4px;
                    padding: 2px 7px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #00F0FF;
                    color: #050B14;
                }
            """)
        elif action_name == "lms_start" and success:
            self.statusBar().showMessage(message, 5000)
            QTimer.singleShot(1500, self._refresh_models)
        elif "lms" in action_name and success:
            self.statusBar().showMessage(message, 5000)
        elif "forge" in action_name:
            self.statusBar().showMessage(message, 5000)
            if not success:
                self._forge_launching = False
                if hasattr(self, "btn_forge_toggle") and self.btn_forge_toggle:
                    self.btn_forge_toggle.setText("▶ Запустить Forge")
                    self.btn_forge_toggle.setStyleSheet("""
                        QPushButton {
                            background-color: #111A28;
                            color: #00F0FF;
                            border: 1px solid #00F0FF;
                            border-radius: 4px;
                            padding: 2px 9px;
                            font-size: 11px;
                            font-weight: bold;
                        }
                        QPushButton:hover {
                            background-color: #00F0FF;
                            color: #050B14;
                        }
                    """)
            else:
                QTimer.singleShot(3000, self._refresh_forge_models)
        else:
            self.statusBar().showMessage(message, 5000)

    def _on_hardware_stats(self, stats: dict):
        v_used = stats.get("vram_used_mb", 0)
        v_total = stats.get("vram_total_mb", 8192)
        v_pct = stats.get("vram_percent", 0.0)
        temp = stats.get("gpu_temp_c")
        temp_str = f" • {temp}°C" if temp else ""

        v_free = max(0, v_total - v_used)
        color = "#00FF66" if v_free >= 1200 else ("#FFE600" if v_free >= EMERGENCY_VRAM_THRESHOLD_MB else "#FF007F")
        self.lbl_vram.setText(f"VRAM: {v_used} / {v_total} MB ({v_pct}%){temp_str}")
        self.lbl_vram.setStyleSheet(f"color: {color}; font-weight: bold; margin-right: 15px;")

        r_used = stats.get("ram_used_gb", 0.0)
        r_total = stats.get("ram_total_gb", 16.0)
        r_pct = stats.get("ram_percent", 0.0)
        self.lbl_ram.setText(f"RAM: {r_used} / {r_total} GB ({r_pct}%)")

    # -------------------------------------------------------------------------
    # 2. Обнаружение, выбор и настройка моделей
    # -------------------------------------------------------------------------
    def _refresh_models(self):
        if getattr(self, "_discovery_worker", None) and self._discovery_worker.isRunning():
            return
        if hasattr(self, "btn_refresh_models") and self.btn_refresh_models:
            self.btn_refresh_models.setEnabled(False)
        self.statusBar().showMessage("Поиск доступных моделей в фоне...", 2000)
        self._discovery_worker = ModelDiscoveryWorker(self.process_manager, self.lm_client, self.forge_client)
        self._discovery_worker.lms_models_ready.connect(self._on_lms_models_discovered)
        self._discovery_worker.forge_models_ready.connect(self._on_forge_models_discovered)
        self._discovery_worker.start()

    def _on_lms_models_discovered(self, all_models: list):
        if hasattr(self, "btn_refresh_models") and self.btn_refresh_models:
            self.btn_refresh_models.setEnabled(True)
        self._is_refreshing_models = True
        try:
            if hasattr(self, "lm_combo") and self.lm_combo:
                current_choice = self.lm_combo.currentText().strip()
                self.lm_combo.blockSignals(True)
                self.lm_combo.clear()
                if all_models:
                    self.lm_combo.addItems(all_models)
                    target = current_choice or getattr(self, "active_lms_model", "") or all_models[0]
                    idx = self.lm_combo.findText(target)
                    if idx >= 0:
                        self.lm_combo.setCurrentIndex(idx)
                    else:
                        self.lm_combo.setCurrentIndex(0)
                else:
                    self.lm_combo.addItem("default (авто)")
                self.lm_combo.blockSignals(False)
            if all_models:
                self.statusBar().showMessage(f"Список моделей LM Studio обновлен ({len(all_models)} доступно).", 2500)
        finally:
            self._is_refreshing_models = False
            if hasattr(self, "models_refreshed"):
                self.models_refreshed.emit()

    def _on_lms_model_activated(self, index: int):
        if self._is_refreshing_models:
            return
        if not (hasattr(self, "lm_combo") and self.lm_combo):
            return
        model_name = self.lm_combo.currentText().strip()
        if not model_name or model_name.startswith("default"):
            return

        self.active_lms_model = model_name
        self.app_config["active_model"] = model_name
        ConfigManager.save_config(self.app_config)

        if self.current_session and not self.current_session.get("is_model_locked", False):
            self.current_session["model_id"] = model_name
            self._save_current_session()

        self.btn_send.setEnabled(False)
        self.statusBar().showMessage(f"⏳ Загрузка модели '{model_name}' в память...")
        device_mode = self.app_config.get("compute_device_mode", "Auto")
        self.process_manager.load_lms_model_async(model_name, device_mode=device_mode)

    def _refresh_forge_models(self):
        if getattr(self, "_discovery_worker", None) and self._discovery_worker.isRunning():
            return
        self._discovery_worker = ModelDiscoveryWorker(self.process_manager, self.lm_client, self.forge_client)
        self._discovery_worker.lms_models_ready.connect(self._on_lms_models_discovered)
        self._discovery_worker.forge_models_ready.connect(self._on_forge_models_discovered)
        self._discovery_worker.start()

    def _on_forge_model_changed(self, text: str):
        self.selected_forge_checkpoint = text
        if text.startswith("✨"):
            self.statusBar().showMessage("Режим генерации: ✨ Арт-директор (Авто)", 3000)
        else:
            self.statusBar().showMessage(f"Модель для генерации зафиксирована: {text}", 4000)

    def _on_forge_models_discovered(self, items: list):
        combined = ["✨ Арт-директор (Авто)"]
        profiles = []
        if hasattr(self, "art_director") and self.art_director and hasattr(self.art_director, "router") and self.art_director.router:
            profiles = getattr(self.art_director.router, "profiles", [])
        if not profiles:
            try:
                from services.art_director_config import load_art_director_models
                profiles = load_art_director_models()
            except Exception:
                profiles = []

        for prof in profiles:
            if isinstance(prof, dict) and prof.get("active", True) and prof.get("name"):
                name = prof["name"]
                if name not in combined:
                    combined.append(name)

        self.available_forge_checkpoints = combined
        if hasattr(self, "forge_combo"):
            cur = self.forge_combo.currentText()
            self.forge_combo.blockSignals(True)
            self.forge_combo.clear()
            self.forge_combo.addItems(combined)
            idx = self.forge_combo.findText(cur)
            if idx >= 0:
                self.forge_combo.setCurrentIndex(idx)
            self.forge_combo.blockSignals(False)

    def _select_forge_model_dialog(self):
        items = list(getattr(self, "available_forge_checkpoints", ["✨ Арт-директор (Авто)"]))
        if "✨ Арт-директор (Авто)" not in items:
            items.insert(0, "✨ Арт-директор (Авто)")

        current_idx = 0
        if hasattr(self, "selected_forge_checkpoint") and self.selected_forge_checkpoint in items:
            current_idx = items.index(self.selected_forge_checkpoint)

        chosen, ok = QInputDialog.getItem(
            self,
            "Выбор чекпоинта SD WebUI Forge",
            "Выберите модель Forge (или оставьте Авто для Арт-директора):",
            items,
            current_idx,
            False
        )
        if ok and chosen:
            self.selected_forge_checkpoint = chosen
            if hasattr(self, "forge_combo"):
                self.forge_combo.blockSignals(True)
                idx = self.forge_combo.findText(chosen)
                if idx >= 0:
                    self.forge_combo.setCurrentIndex(idx)
                self.forge_combo.blockSignals(False)
            self.statusBar().showMessage(f"Выбрана модель Forge: {chosen}", 5000)


    def _open_models_manager_dialog(self):
        from ui.models_manager_dialog import ModelsManagerDialog
        dialog = ModelsManagerDialog(self.forge_client, self)
        if hasattr(dialog, "models_updated"):
            dialog.models_updated.connect(self._refresh_forge_models)
        dialog.exec()
        self.art_director.reload_models()
        self._refresh_forge_models()
        self.statusBar().showMessage("Конфигурация моделей Арт-директора обновлена.", 4000)

    def _open_llm_engine_settings_dialog(self):
        """Открывает диалог настройки движка LLM (llama-server)."""
        from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog
        dlg = LLMEngineSettingsDialog(self)
        dlg.exec()

    def _perform_hardware_kv_autotune(self):
        """
        Однократный аппаратный автотюн KV-кэша и Flash Attention при первом старте.
        """
        try:
            from services.layer_budgeter import LayerBudgeter
            from utils.config_manager import ConfigManager
            from qfluentwidgets import InfoBar

            cfg = ConfigManager.load_config()
            if cfg.get("hardware_autotune_done", False):
                return

            profile = LayerBudgeter.detect_hardware_kv_profile()
            cfg["kv_cache_type"] = profile.get("recommended_kv", "q8_0")
            cfg["flash_attention_enabled"] = profile.get("recommended_fa", True)
            cfg["hardware_autotune_done"] = True
            ConfigManager.save_config(cfg)
            self.app_config = cfg

            if profile.get("supported", False):
                InfoBar.success(
                    title="Аппаратный автотюн GPU",
                    content=f"Обнаружена {profile['gpu_name']} (CC {profile['compute_cap']}). Активирован скоростной буфер Q8_0 и Flash Attention (экономия 50% VRAM).",
                    duration=6000,
                    parent=self
                )
            else:
                InfoBar.info(
                    title="Аппаратный автотюн GPU",
                    content=f"Видеокарта {profile['gpu_name']} не поддерживает Flash Attention. Выставлен стандартный режим FP16.",
                    duration=6000,
                    parent=self
                )
            logger.info(f"[HardwareEngine] Аппаратный автотюн выполнен: {profile}")
        except Exception as e:
            logger.warning(f"[HardwareEngine] Ошибка выполнения аппаратного автотюна: {e}")

    # -------------------------------------------------------------------------
    # 3. Генерация арта и жонглинг 8GB VRAM
    # -------------------------------------------------------------------------
    def _on_action_art_clicked(self):
        try:
            target_widget = None
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and isinstance(w, ChatMessageWidget) and not getattr(w, "is_user", True):
                    target_widget = w
                    break
            if target_widget:
                v_intent = getattr(target_widget, "visual_intent", None)
                target_text = getattr(target_widget, "translated_text", None) or getattr(target_widget, "raw_text", "")
                if v_intent:
                    self._on_scene_art_requested(widget=target_widget, visual_intent=v_intent)
                elif target_text:
                    self._on_scene_art_requested(text=target_text, widget=target_widget)
                else:
                    os.startfile(str(OUTPUTS_DIR))
            else:
                os.startfile(str(OUTPUTS_DIR))
        except Exception as e:
            logger.error(f"Error on art action: {e}")

    def _on_forge_generation_progress(self, stage_name: str, percent: int):
        if hasattr(self, "header_controller"):
            self.header_controller.set_forge_progress(stage_name, percent)


    def _is_forge_available(self) -> bool:
        """
        Неблокирующая проверка доступности Forge перед любыми манипуляциями с VRAM.
        Проверяет ProcessManager.is_forge_running() или ForgeClient.get_instance().is_server_ready(timeout=0.3).
        """
        try:
            if hasattr(self, "process_manager") and self.process_manager:
                if hasattr(self.process_manager, "is_forge_running") and self.process_manager.is_forge_running():
                    return True
        except Exception:
            pass

        try:
            if hasattr(self, "forge_client") and self.forge_client:
                if hasattr(self.forge_client, "is_server_ready") and self.forge_client.is_server_ready(timeout=0.3):
                    return True
        except Exception:
            pass

        try:
            from services.forge_client import ForgeClient
            if hasattr(ForgeClient, "get_instance"):
                fc = ForgeClient.get_instance()
                if fc and hasattr(fc, "is_server_ready") and fc.is_server_ready(timeout=0.3):
                    return True
        except Exception:
            pass

        return False

    def _notify_forge_offline(self, log_label: str = "генерации арта", show_chat_error: bool = False):
        logger.info(f"[HardwareEngine] Пропуск {log_label}: SD WebUI Forge оффлайн.")
        if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
            try:
                self.statusBar().showMessage("Генерация арта пропущена (Forge выключен)", 5000)
            except Exception:
                pass
        if show_chat_error and hasattr(self, "_append_message"):
            user_msg = (
                "⚠️ **SD WebUI Forge не запущен или еще загружается.**\n\n"
                "Запустите Forge через кнопку в нижней панели и дождитесь зеленого индикатора."
            )
            sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Элария"
            self._append_message(sender_name, user_msg, is_user=False, is_error=True)

    def _finalize_forge_generation(self):
        try:
            from services.vram_arbiter import VRAMArbiter
            VRAMArbiter.get_instance().release_diffusion_for_llm(force=True)
        except Exception:
            try:
                if hasattr(self, "forge_client") and self.forge_client:
                    if getattr(self.forge_client, "_checkpoint_unloaded", False) is not True:
                        self.forge_client.unload_checkpoint()
                        self.forge_client.free_vram(force=True)
            except Exception as _e_unl:
                logger.debug(f"[ForgeClient] Ошибка выгрузки чекпоинта: {_e_unl}")
        if hasattr(self, "_reload_active_lms_model_async"):
            self._reload_active_lms_model_async(delay=1.2)

    def _scene_art_pipeline(
        self,
        v_payload: Optional[Dict[str, Any]] = None,
        scene_text: Optional[str] = None,
        ckpt: Optional[str] = None
    ) -> Dict[str, Any]:
        if not self._is_forge_available():
            self._notify_forge_offline("генерации арта сцены")
            return {"image_path": None, "plan": None, "checkpoint": None, "skipped": True}

        self._cancel_lms_reload()
        captured_mode = self.get_effective_mode() if hasattr(self, "get_effective_mode") else getattr(self, "current_mode", "assistant")
        captured_char = self.active_character if (captured_mode == "roleplay" and hasattr(self, "active_character")) else None
        active_char_name = getattr(captured_char, "name", None) if captured_char else None
        sess_id = self.current_session.get("id") if getattr(self, "current_session", None) else None

        from services.assistant_flow import run_forge_art_generation

        raw_prompt = scene_text or ""
        lm_for_plan = self.lm_client if hasattr(self, "lm_client") else None
        if captured_mode in ("roleplay", "rp") and not v_payload:
            raw_prompt = self._generate_ephemeral_rp_art_prompt(
                sess_id=sess_id,
                last_assistant_message=scene_text or "",
                target_model_id=ckpt or "realistic",
            )
            lm_for_plan = None

        try:
            gen_res = run_forge_art_generation(
                forge_client=self.forge_client,
                art_director=self.art_director,
                session_id=sess_id,
                mode=captured_mode,
                visual_intent=v_payload,
                raw_prompt=raw_prompt,
                preferred_style=ckpt,
                lm_client=lm_for_plan,
                character=None,
                character_name=None if (captured_mode in ("roleplay", "rp") and not v_payload) else active_char_name,
                unload_after=False,
            )
        finally:
            self._finalize_forge_generation()

        if gen_res.get("is_ambiguous"):
            return gen_res

        logger.info(f"Scene Art selected checkpoint: '{gen_res.get('checkpoint')}'")
        return {
            "image_path": gen_res.get("image_path"),
            "plan": gen_res.get("plan"),
            "checkpoint": gen_res.get("checkpoint"),
        }

    def _on_scene_art_requested(
        self,
        text: Optional[str] = None,
        widget: Optional[ChatMessageWidget] = None,
        preferred_style: Optional[str] = None,
        character_name: Optional[str] = None,
        visual_intent: Optional[Dict[str, Any]] = None
    ):
        if not self._is_forge_available():
            if widget and hasattr(widget, "set_scene_art_busy"):
                widget.set_scene_art_busy(False)
            self._notify_forge_offline("генерации арта сцены")
            return

        self._cancel_lms_reload()
        if hasattr(self, "task_queue") and self.task_queue:
            self.task_queue.abort_tasks_by_type("memory")

        v_intent = visual_intent or (getattr(widget, "visual_intent", None) if widget is not None else None)

        norm_combo = normalize_model_choice(getattr(self, "selected_forge_checkpoint", None))
        norm_pref = normalize_model_choice(preferred_style)
        active_pref_style = norm_combo or norm_pref

        sess_id = self.current_session.get("id") if self.current_session else None
        sess_images_dir = SessionManager.get_session_images_dir(sess_id) if sess_id else None

        captured_mode = self.get_effective_mode() if hasattr(self, "get_effective_mode") else getattr(self, "current_mode", "assistant")
        captured_char = self.active_character if (captured_mode == "roleplay" and hasattr(self, "active_character")) else None
        active_char_name = character_name
        if not active_char_name and captured_mode == "roleplay" and captured_char:
            active_char_name = getattr(captured_char, "name", None)

        raw_scene_text = (text or "").strip()
        needs_llm_gen = bool((captured_mode in ("roleplay", "rp") and not v_intent) or (not v_intent and re.search(r"[а-яА-ЯёЁ]", raw_scene_text)))

        if widget and hasattr(widget, "set_scene_art_busy"):
            widget.set_scene_art_busy(True, "⏳ Генерация...")

        task = InferenceTask(
            name="🎨 Иллюстрация сцены",
            task_type="scene_art",
            func=self._scene_art_pipeline,
            kwargs={
                "v_payload": v_intent,
                "scene_text": raw_scene_text,
                "ckpt": active_pref_style
            },
            abort_fn=self.forge_client.interrupt if hasattr(self, "forge_client") and self.forge_client else None,
            metadata={
                "is_scene_art": True,
                "session_id": sess_id,
                "target_widget": widget,
                "preferred_style": active_pref_style,
                "visual_intent": v_intent,
                "needs_llm_prompt_gen": needs_llm_gen
            }
        )
        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _generate_scene_art_async(self, *args, **kwargs):
        """Алиас для _on_scene_art_requested."""
        return self._on_scene_art_requested(*args, **kwargs)

    def _enqueue_image_task(self, prompt: str):
        if not self._is_forge_available():
            self._notify_forge_offline("ручной генерации арта", show_chat_error=True)
            return

        checkpoint = normalize_model_choice(getattr(self, "selected_forge_checkpoint", None))
        sess_id = self.current_session.get("id") if self.current_session else None
        sess_images_dir = SessionManager.get_session_images_dir(sess_id) if sess_id else None

        captured_mode = self.get_effective_mode() if hasattr(self, "get_effective_mode") else getattr(self, "current_mode", "assistant")
        captured_char = self.active_character if (captured_mode == "roleplay" and hasattr(self, "active_character")) else None
        active_char_name = None
        if captured_mode == "roleplay" and captured_char:
            active_char_name = getattr(captured_char, "name", None)

        def _image_worker(raw_prompt: str, ckpt: Optional[str] = None) -> Dict[str, Any]:
            if not self._is_forge_available():
                self._notify_forge_offline("ручной генерации арта")
                return {"image_path": None, "plan": None, "checkpoint": None, "skipped": True}
            self._cancel_lms_reload()
            available_models = self.forge_client.get_models()
            plan = self.art_director.plan_generation(
                raw_prompt=raw_prompt,
                preferred_style=ckpt,
                lm_client=self.lm_client,
                character=captured_char if captured_mode == "roleplay" else None,
                available_models=available_models,
                character_name=active_char_name,
                session_id=sess_id,
                mode=captured_mode
            )
            if plan.is_ambiguous:
                return {"image_path": None, "plan": plan, "checkpoint": None, "is_ambiguous": True}

            from services.vram_arbiter import VRAMArbiter
            VRAMArbiter.get_instance().release_llm_for_diffusion()

            logger.info(
                f"Art Director Plan: style={plan.style}, target='{plan.model_title}', "
                f"size={plan.width}x{plan.height}, prompt='{plan.positive_prompt[:60]}...'"
            )

            kwargs = plan.to_forge_kwargs(output_dir=sess_images_dir, session_id=sess_id)
            img_path = self.forge_client.text_to_image(**kwargs)

            self._finalize_forge_generation()
            return {
                "image_path": img_path,
                "plan": plan,
                "checkpoint": plan.target_checkpoint
            }

        has_cyrillic = bool(re.search(r"[а-яА-ЯёЁ]", prompt))
        task = InferenceTask(
            name=f"🎨 Арт: {prompt[:25]}...",
            task_type="image",
            func=_image_worker,
            kwargs={"raw_prompt": prompt, "ckpt": checkpoint},
            abort_fn=self.forge_client.interrupt,
            metadata={"prompt": prompt, "needs_llm_prompt_gen": has_cyrillic}
        )
        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _enqueue_direct_image_task(self, prompt: str):
        if not self._is_forge_available():
            self._notify_forge_offline("директ-генерации арта", show_chat_error=True)
            return

        checkpoint = normalize_model_choice(getattr(self, "selected_forge_checkpoint", None))
        sess_id = self.current_session.get("id") if self.current_session else None
        sess_images_dir = SessionManager.get_session_images_dir(sess_id) if sess_id else None

        def _direct_image_worker(raw_prompt: str, ckpt: Optional[str] = None) -> Dict[str, Any]:
            if not self._is_forge_available():
                self._notify_forge_offline("директ-генерации арта")
                return {"image_path": None, "plan": None, "checkpoint": None, "skipped": True}
            self._cancel_lms_reload()
            available_models = self.forge_client.get_models()
            plan = self.art_director.build_direct_plan(
                raw_prompt=raw_prompt,
                preferred_style=ckpt,
                lm_client=self.lm_client,
                available_models=available_models
            )
            if plan.is_ambiguous:
                return {"image_path": None, "plan": plan, "checkpoint": None, "is_ambiguous": True}

            from services.vram_arbiter import VRAMArbiter
            VRAMArbiter.get_instance().release_llm_for_diffusion()

            logger.info(
                f"Direct Art Plan: style={plan.style}, target='{plan.model_title}', "
                f"size={plan.width}x{plan.height}, prompt='{plan.positive_prompt[:60]}...'"
            )

            kwargs = plan.to_forge_kwargs(output_dir=sess_images_dir, session_id=sess_id)
            img_path = self.forge_client.text_to_image(**kwargs)

            self._finalize_forge_generation()
            return {
                "image_path": img_path,
                "plan": plan,
                "checkpoint": plan.target_checkpoint
            }

        has_cyrillic = bool(re.search(r"[а-яА-ЯёЁ]", prompt))
        task = InferenceTask(
            name=f"⚡ Директ-арт: {prompt[:25]}...",
            task_type="image",
            func=_direct_image_worker,
            kwargs={"raw_prompt": prompt, "ckpt": checkpoint},
            abort_fn=self.forge_client.interrupt,
            metadata={"prompt": prompt, "is_direct": True, "needs_llm_prompt_gen": has_cyrillic}
        )
        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    # -------------------------------------------------------------------------
    # 4. Координация таймеров возврата LLM
    # -------------------------------------------------------------------------
    def _reload_active_lms_model_async(self, delay: float = 3.0):
        self._sig_reload_lms.emit(delay)

    def _cancel_lms_reload(self):
        self._sig_cancel_lms.emit()

    @pyqtSlot(float)
    def _do_reload_active_lms_timer(self, delay: float):
        self._do_cancel_lms_reload()

        has_pending_art = self.task_queue.has_pending_tasks(("scene_art", "image", "scene_background")) if hasattr(self, "task_queue") and self.task_queue else False
        if has_pending_art:
            logger.info("[MainWindow] Возврат LLM отменён: в очереди ожидают задачи Forge.")
            return

        self._lms_reload_timer = QTimer(self)
        self._lms_reload_timer.setSingleShot(True)
        self._lms_reload_timer.timeout.connect(self._execute_lms_reload)
        self._lms_reload_timer.start(int(delay * 1000))

    @pyqtSlot()
    def _do_cancel_lms_reload(self):
        if hasattr(self, "_lms_reload_timer") and self._lms_reload_timer and self._lms_reload_timer.isActive():
            self._lms_reload_timer.stop()
            logger.info("[MainWindow] Отложенная загрузка LLM успешно отменена до выгрузки.")

    def _execute_lms_reload(self):
        if getattr(self, "is_busy", False):
            return

        if hasattr(self, "task_queue") and self.task_queue:
            if self.task_queue.current_task is not None:
                return
            if self.task_queue.has_pending_tasks(("scene_art", "image", "scene_background")):
                return

        is_rp = (getattr(self, "current_mode", "assistant") == "roleplay")
        is_eng = (not is_rp) and (self.is_engineer_mode_active() if hasattr(self, "is_engineer_mode_active") else False)

        # Точное определение целевой модели под текущий режим
        if is_eng:
            target_model = self.app_config.get("engineer_model_name") or self.app_config.get("engineer_model")
        elif is_rp:
            char_data = (self.current_session.get("character_data") or {}) if self.current_session else {}
            target_model = (
                char_data.get("character_model")
                or self.app_config.get("rp_model_name")
                or (self.current_session.get("model_id") if self.current_session else None)
                or self.app_config.get("active_model")
            )
        else:
            target_model = (
                self.app_config.get("assistant_model_name")
                or (self.current_session.get("model_id") if self.current_session else None)
                or self.app_config.get("active_model")
            )

        target_model = (target_model or "").strip()
        if not target_model or target_model.startswith("default"):
            target_model = getattr(self, "active_lms_model", "")

        if not target_model or target_model.startswith("default"):
            return

        # Определение целевого контекста: 28K для Инженера, 24K для РП, 14K для Ассистента
        if is_eng:
            ctx_len = self.app_config.get("context_length_engineer", 28672)
        elif is_rp:
            ctx_len = self.app_config.get("context_length_roleplay", 24576)
        else:
            ctx_len = self.app_config.get("context_length_assistant", 14336)

        if hasattr(self, "lm_client") and self.lm_client and hasattr(self, "task_queue") and self.task_queue:
            from services.task_queue import PRIORITY_LOW
            task = InferenceTask(
                name="Возврат LLM в VRAM",
                task_type="llm",
                func=self.lm_client.ensure_model_loaded,
                kwargs={
                    "preferred_model": target_model,
                    "context_length": ctx_len,
                    "is_engineer": is_eng,
                    "is_roleplay": is_rp
                },
                priority=PRIORITY_LOW,
                metadata={"silent": True}
            )
            self._active_tasks[task.id] = task
            self.task_queue.enqueue(task)


    # -------------------------------------------------------------------------
    # 5. Мобильный шлюз (FastAPI Mobile Server + QR-код подключения)
    # -------------------------------------------------------------------------
    def _sync_mobile_server_button(self, is_running: bool):
        if hasattr(self, "sidebar") and self.sidebar and hasattr(self.sidebar, "update_mobile_server_state"):
            self.sidebar.update_mobile_server_state(is_running)

    def _on_mobile_server_clicked(self):
        """Запускает встроенный мобильный сервер (если остановлен) и показывает QR-код подключения."""
        from mobile_server.api import is_mobile_server_running, start_mobile_server_thread
        from qfluentwidgets import InfoBar, InfoBarPosition

        if not is_mobile_server_running():
            if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
                self.statusBar().showMessage("Запуск мобильного сервера...", 3000)
            ok, msg = start_mobile_server_thread()
            self._sync_mobile_server_button(ok)
            if not ok:
                InfoBar.error(
                    title="Мобильный сервер",
                    content=msg,
                    duration=5000,
                    position=InfoBarPosition.TOP_RIGHT,
                    parent=self,
                )
                return
            if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
                self.statusBar().showMessage(msg, 4000)
        else:
            self._sync_mobile_server_button(True)

        self._show_mobile_server_qr_dialog()

    def _show_mobile_server_qr_dialog(self):
        """Отображает по центру окна Fluent-диалог с QR-кодом для подключения телефона."""
        from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
        from qfluentwidgets import (
            BodyLabel,
            ComboBox,
            FluentIcon as FIF,
            InfoBar,
            InfoBarPosition,
            MessageBoxBase,
            PushButton,
            SubtitleLabel,
        )
        from mobile_server.api import get_mobile_server_urls, stop_mobile_server_thread
        from ui.common.icon_utils import render_qr_pixmap

        urls = get_mobile_server_urls()
        active_url = urls[0]

        parent_win = self if isinstance(self, QWidget) else None
        dialog = MessageBoxBase(parent_win)
        dialog.widget.setMinimumWidth(380)

        title_lbl = SubtitleLabel("Мобильный сервер активен", dialog)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dialog.viewLayout.addWidget(title_lbl)

        hint_lbl = BodyLabel(
            "Отсканируйте QR-код камерой телефона (в одной сети Wi-Fi или VPN), "
            "чтобы открыть NeonRoute в браузере:",
            dialog,
        )
        hint_lbl.setWordWrap(True)
        hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_lbl.setStyleSheet("color: #94A3B8; font-size: 12px;")
        dialog.viewLayout.addWidget(hint_lbl)

        qr_frame = QFrame(dialog)
        qr_frame.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border-radius: 14px; padding: 10px; }"
        )
        qr_layout = QVBoxLayout(qr_frame)
        qr_layout.setContentsMargins(12, 12, 12, 12)
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        qr_label = QLabel(qr_frame)
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_label.setPixmap(render_qr_pixmap(active_url, module_px=7, quiet_modules=3))
        qr_layout.addWidget(qr_label)

        qr_row = QHBoxLayout()
        qr_row.addStretch(1)
        qr_row.addWidget(qr_frame)
        qr_row.addStretch(1)
        dialog.viewLayout.addLayout(qr_row)

        url_lbl = BodyLabel(active_url, dialog)
        url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_lbl.setStyleSheet("color: #00F0FF; font-weight: 600; font-size: 14px;")
        dialog.viewLayout.addWidget(url_lbl)

        if len(urls) > 1:
            addr_combo = ComboBox(dialog)
            addr_combo.addItems(urls)
            addr_combo.setCurrentIndex(0)

            def _on_addr_changed(new_url: str):
                url_lbl.setText(new_url)
                qr_label.setPixmap(render_qr_pixmap(new_url, module_px=7, quiet_modules=3))

            addr_combo.currentTextChanged.connect(_on_addr_changed)
            dialog.viewLayout.addWidget(addr_combo)

        btn_copy = PushButton(FIF.COPY, "Копировать ссылку", dialog)

        def _copy_url():
            cb = QApplication.clipboard()
            if cb is not None:
                cb.setText(url_lbl.text())
            InfoBar.success(
                title="Скопировано",
                content=url_lbl.text(),
                duration=2000,
                position=InfoBarPosition.TOP_RIGHT,
                parent=parent_win,
            )

        btn_copy.clicked.connect(_copy_url)
        dialog.viewLayout.addWidget(btn_copy)

        dialog.yesButton.setText("Готово")
        dialog.cancelButton.setText("Остановить сервер")

        if not dialog.exec():
            stop_mobile_server_thread()
            self._sync_mobile_server_button(False)
            if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
                self.statusBar().showMessage("Мобильный сервер остановлен.", 3000)


def __getattr__(name: str):
    if name == "ModelsManagerDialog":
        from ui.models_manager_dialog import ModelsManagerDialog
        globals()["ModelsManagerDialog"] = ModelsManagerDialog
        return ModelsManagerDialog
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
