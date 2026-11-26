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
                    self._check_and_prompt_forge_patch(selected_dir)
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

    def _check_forge_onboarding(self):
        """
        Умный одноразовый онбординг SD WebUI Forge.
        Срабатывает только на машинах с GPU NVIDIA (VRAM >= 3800 МБ) при отсутствии настроенного Forge.
        """
        if "PYTEST_CURRENT_TEST" in os.environ:
            return

        cfg = ConfigManager.load_config()
        if cfg.get("forge_onboarding_shown", False):
            return

        from core.forge_locator import is_valid_forge_dir
        import config
        if is_valid_forge_dir(config.FORGE_ROOT_DIR):
            return

        # Проверка видеокарты: строго NVIDIA и VRAM >= 3800 МБ
        sys_mon = getattr(self, "system_monitor", None)
        if not sys_mon or not getattr(sys_mon, "nvml_initialized", False):
            return

        from services.layer_budgeter import LayerBudgeter
        profile = LayerBudgeter.detect_hardware_kv_profile()
        vendor = str(profile.get("vendor", "")).lower()
        cc_str = str(profile.get("compute_cap", "N/A"))
        if vendor != "nvidia" or cc_str == "N/A":
            return

        vram_mb = sys_mon.get_total_vram_mb()
        if vram_mb < 3800:
            return

        # Помечаем онбординг как показанный сразу перед отображением
        ConfigManager.update_setting("forge_onboarding_shown", True)
        if hasattr(self, "app_config") and isinstance(self.app_config, dict):
            self.app_config["forge_onboarding_shown"] = True

        from ui.dialogs.forge_onboarding_dialog import ForgeOnboardingDialog
        from ui.common.toast_helper import toast_info

        dlg = ForgeOnboardingDialog(
            gpu_name=profile.get("gpu_name", "NVIDIA GeForce"),
            vram_mb=vram_mb,
            compute_cap=cc_str,
            parent=self
        )
        dlg.exec()

        if dlg.choice == "download":
            from ui.dialogs.forge_settings_dialog import ForgeSettingsDialog
            settings_dlg = ForgeSettingsDialog(parent=self, auto_start_download=True)
            settings_dlg.exec()
        elif dlg.choice == "locate":
            self.prompt_locate_forge()
        else:
            toast_info(
                self,
                "SD Forge",
                "Подключить Forge можно позже: Параметры и настройки → Система → SD Forge..."
            )

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
            self._check_and_prompt_forge_patch()
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

    def _can_start_llm_engine(self) -> bool:
        """
        Проверяет возможность запуска LLM сервера.
        Возвращает False, если server_state == ServerState.ERROR и ни llama-server.exe,
        ни LM Studio не обнаружены на диске.
        """
        if not hasattr(self, "process_manager") or not self.process_manager:
            return False
        from services.process_manager import ServerState
        pm_state = getattr(self.process_manager, "server_state", None)
        if pm_state == ServerState.ERROR:
            has_headless = self.process_manager.has_headless_server()
            has_lms = bool(getattr(self.process_manager, "_lms_exe", None) or self.process_manager._locate_lm_studio_app())
            if not has_headless and not has_lms:
                return False
        return True

    def _check_and_restart_lms_periodic(self):
        """
        Периодическая проверка и безопасный перезапуск LLM сервера (каждые 30 секунд).
        Предотвращает запуск start_lm_studio_async(), если server_state == ServerState.ERROR
        и ни llama-server.exe, ни LM Studio не появились на диске.
        """
        if not hasattr(self, "process_manager") or not self.process_manager:
            return
        if self.process_manager.is_lm_studio_running():
            return
        if not self._can_start_llm_engine():
            logger.debug("[HardwareEngine] LLM сервер в состоянии ERROR и бинарники отсутствуют на диске. Перезапуск пропущен.")
            return

        has_headless = self.process_manager.has_headless_server()
        has_lms = bool(getattr(self.process_manager, "_lms_exe", None) or self.process_manager._locate_lm_studio_app())
        if has_headless or has_lms:
            logger.info("[HardwareEngine] Обнаружен доступный рантайм LLM, попытка запуска start_lm_studio_async()...")
            self.process_manager.start_lm_studio_async()

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
            if not self._can_start_llm_engine():
                err_msg = (
                    "Движок llama-server не найден в папке bin/. "
                    "Запустите install.bat или скачайте движок в меню «Параметры и настройки → Движок LLM»."
                )
                if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
                    self.statusBar().showMessage(err_msg, 6000)
                from ui.common.toast_helper import toast_warning
                toast_warning(self, "Движок LLM", err_msg)
                return
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
                    target = current_choice or getattr(self, "active_lms_model", "") or self.app_config.get("active_model", "") or all_models[0]
                    idx = self.lm_combo.findText(target)
                    if idx >= 0:
                        self.lm_combo.setCurrentIndex(idx)
                    else:
                        self.lm_combo.setCurrentIndex(0)
                    chosen_model = self.lm_combo.currentText().strip()
                    if chosen_model and not chosen_model.startswith("default"):
                        self.active_lms_model = chosen_model
                        self.app_config["active_model"] = chosen_model
                        ConfigManager.update_setting("active_model", chosen_model)
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

    def _open_forge_settings_dialog(self):
        """Открывает диалог настройки SD WebUI Forge (пути, FP8, VRAM)."""
        from ui.dialogs.forge_settings_dialog import ForgeSettingsDialog
        dlg = ForgeSettingsDialog(self)
        dlg.exec()

    def _check_and_prompt_forge_patch(self, forge_root: Optional[Path | str] = None) -> bool:
        """
        Проверяет статус патчей Forge и, если требуется (NEEDS_PATCH),
        предлагает применить их в стилизованном Fluent-диалоге с возможностью
        запомнить отказ ('Больше не предлагать для этой папки').
        Вызывается ТОЛЬКО при явном запуске или настройке Forge.
        """
        try:
            import config
            from pathlib import Path
            from PyQt6.QtWidgets import QWidget
            from services.forge_patcher import ForgePatcher, ForgePatchStatus
            from utils.config_manager import ConfigManager
            from ui.common.toast_helper import toast_success, toast_info

            target_root = forge_root or config.FORGE_ROOT_DIR
            if not target_root:
                return False

            status, _ = ForgePatcher.check_status(target_root)
            if status != ForgePatchStatus.NEEDS_PATCH:
                return False

            # Проверяем, не добавлен ли путь в список игнорируемых
            cfg = ConfigManager.load_config()
            raw_ignored = cfg.get("forge_patch_ignored_paths", []) or []
            ignored_paths = {os.path.normcase(os.path.abspath(str(p))) for p in raw_ignored if p}
            norm_target = os.path.normcase(os.path.abspath(str(target_root)))
            if norm_target in ignored_paths:
                return False

            # Показываем стилизованный Fluent-диалог
            from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel, CheckBox
            parent_widget = self if isinstance(self, QWidget) else None

            class _ForgePatchPromptDialog(MessageBoxBase):
                def __init__(self, parent=None):
                    super().__init__(parent)
                    self.titleLabel = SubtitleLabel("Рекомендуется патч совместимости Forge", self)
                    self.viewLayout.addWidget(self.titleLabel)

                    self.contentLabel = BodyLabel(
                        "Обнаружен стандартный SD WebUI Forge. Чтобы модели переключались на лету "
                        "и освобождалась видеопамять для LLM (важно для 8 ГБ VRAM), рекомендуется "
                        "применить патч совместимости.\n\n"
                        "Оригинальные файлы будут сохранены в .neonroute_backup/original/.",
                        self
                    )
                    self.contentLabel.setWordWrap(True)
                    self.contentLabel.setStyleSheet("color: #94A3B8; font-size: 12px; line-height: 1.4;")
                    self.viewLayout.addWidget(self.contentLabel)

                    self.cb_never = CheckBox("Больше не предлагать для этой папки", self)
                    self.viewLayout.addWidget(self.cb_never)

                    self.yesButton.setText("Применить патч")
                    self.cancelButton.setText("Позже")
                    self.widget.setMinimumWidth(440)

            dlg = _ForgePatchPromptDialog(parent_widget)
            if dlg.exec():
                ok, res_msg = ForgePatcher.apply_patch(target_root)
                if ok:
                    toast_success(
                        self,
                        title="Патч Forge применён",
                        content="Патчи совместимости успешно установлены. Резервная копия сохранена в .neonroute_backup/original/."
                    )
                    return True
            else:
                if hasattr(dlg, "cb_never") and dlg.cb_never and dlg.cb_never.isChecked():
                    if norm_target not in ignored_paths:
                        raw_ignored.append(str(Path(target_root).resolve()))
                        cfg["forge_patch_ignored_paths"] = raw_ignored
                        ConfigManager.save_config(cfg)
                        ignored_paths.add(norm_target)
                    toast_info(
                        self,
                        title="Патч Forge отложен",
                        content="Вы всегда сможете применить его позже: Параметры и настройки → Система → SD Forge..."
                    )
        except Exception as e:
            logger.debug(f"[HardwareEngine] Ошибка при проверке/предложении патча Forge: {e}")
        return False

    def _perform_hardware_kv_autotune(self):
        """
        Однократный аппаратный автотюн KV-кэша и Flash Attention при первом старте.
        """
        try:
            from services.layer_budgeter import LayerBudgeter
            from utils.config_manager import ConfigManager
            from ui.common.toast_helper import toast_success, toast_info

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
                toast_success(
                    self,
                    title="Аппаратный автотюн GPU",
                    content=f"Обнаружена {profile['gpu_name']} (CC {profile['compute_cap']}). Активирован скоростной буфер Q8_0 и Flash Attention (экономия 50% VRAM).",
                )
            else:
                toast_info(
                    self,
                    title="Аппаратный автотюн GPU",
                    content=f"Видеокарта {profile['gpu_name']} не поддерживает Flash Attention. Выставлен стандартный режим FP16.",
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
            need_force = not getattr(getattr(self, "forge_client", None), "_checkpoint_unloaded", False)
            VRAMArbiter.get_instance().release_diffusion_for_llm(force=need_force)
        except Exception:
            try:
                if hasattr(self, "forge_client") and self.forge_client:
                    if getattr(self.forge_client, "_checkpoint_unloaded", False) is not True:
                        self.forge_client.unload_checkpoint()
                        self.forge_client.free_vram(force=True)
            except Exception as _e_unl:
                logger.debug(f"[ForgeClient] Ошибка выгрузки чекпоинта: {_e_unl}")
            try:
                import time
                from services.vram_arbiter import VRAMArbiter, GPUOwner
                arb = VRAMArbiter.get_instance()
                arb._last_diffusion_release_ts = time.monotonic()
                arb._current_owner = GPUOwner.NONE
            except Exception:
                pass
        if hasattr(self, "_reload_active_lms_model_async"):
            self._reload_active_lms_model_async(delay=1.2)

    def _scene_art_pipeline(
        self,
        v_payload: Optional[Any] = None,
        scene_text: Optional[str] = None,
        ckpt: Optional[str] = None,
        widget: Optional[Any] = None,
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
        from services.art_director.contracts import VisualIntent

        raw_prompt = scene_text or ""
        lm_for_plan = self.lm_client if hasattr(self, "lm_client") else None
        if captured_mode in ("roleplay", "rp") and not v_payload:
            rp_res = self._generate_ephemeral_rp_art_prompt(
                sess_id=sess_id,
                last_assistant_message=scene_text or "",
                target_model_id=ckpt or "realistic",
            )
            lm_for_plan = None
            if isinstance(rp_res, VisualIntent):
                v_payload = rp_res
                raw_prompt = rp_res.prompt
                if widget is not None:
                    widget.visual_intent = rp_res
                    if hasattr(widget, "show_art_loading_placeholder"):
                        widget.show_art_loading_placeholder(aspect_ratio=rp_res.aspect_ratio, prompt=rp_res.prompt)
            else:
                raw_prompt = rp_res

        if not v_payload and not (raw_prompt or "").strip():
            logger.warning("[SceneArt] Генерация отменена: не удалось сформировать визуальный промпт сцены (пустой результат LLM).")
            return {"image_path": None, "plan": None, "checkpoint": None, "error": "Не удалось сформировать описание сцены"}

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

        from services.art_director.contracts import VisualIntent
        if isinstance(v_intent, VisualIntent) and widget is not None:
            widget.visual_intent = v_intent
            if hasattr(widget, "show_art_loading_placeholder"):
                widget.show_art_loading_placeholder(aspect_ratio=v_intent.aspect_ratio, prompt=v_intent.prompt)

        task = InferenceTask(
            name="🎨 Иллюстрация сцены",
            task_type="scene_art",
            func=self._scene_art_pipeline,
            kwargs={
                "v_payload": v_intent,
                "scene_text": raw_scene_text,
                "ckpt": active_pref_style,
                "widget": widget,
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
    # 4. Координация таймеров возврата LLM (делегируется в единый headless VRAMArbiter)
    # -------------------------------------------------------------------------
    def _reload_active_lms_model_async(self, delay: float = 3.0):
        from services.vram_arbiter import VRAMArbiter
        sess = getattr(self, "current_session", None)
        sess_id = sess.get("id") if isinstance(sess, dict) else None
        mode = getattr(self, "current_mode", None)
        fallback = getattr(self, "active_lms_model", None)
        VRAMArbiter.get_instance().schedule_llm_reload(
            delay=delay,
            session_id=sess_id,
            mode=mode,
            fallback_model=fallback,
        )

    def _cancel_lms_reload(self):
        from services.vram_arbiter import VRAMArbiter
        VRAMArbiter.get_instance().cancel_llm_reload(reason="ui_cancel")

    @pyqtSlot(float)
    def _do_reload_active_lms_timer(self, delay: float):
        self._reload_active_lms_model_async(delay=delay)

    @pyqtSlot()
    def _do_cancel_lms_reload(self):
        self._cancel_lms_reload()


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
                from ui.common.toast_helper import toast_error
                toast_error(
                    self,
                    title="Мобильный сервер",
                    content=msg,
                )
                return
            if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
                self.statusBar().showMessage(msg, 4000)
        else:
            self._sync_mobile_server_button(True)

        self._show_mobile_server_qr_dialog()

    def _show_mobile_server_qr_dialog(self):
        """Отображает по центру окна Fluent-диалог с QR-кодом для подключения телефона."""
        from PyQt6.QtWidgets import QWidget
        from ui.dialogs.mobile_server_dialog import MobileServerDialog
        from mobile_server.api import stop_mobile_server_thread

        parent_win = self if isinstance(self, QWidget) else None
        dialog = MobileServerDialog(parent=parent_win)
        try:
            dialog.exec()
            if dialog.stop_server_requested:
                stop_mobile_server_thread()
                self._sync_mobile_server_button(False)
                if hasattr(self, "statusBar") and callable(self.statusBar) and self.statusBar():
                    self.statusBar().showMessage("Мобильный сервер остановлен.", 3000)
        finally:
            dialog.deleteLater()


def __getattr__(name: str):
    if name == "ModelsManagerDialog":
        from ui.models_manager_dialog import ModelsManagerDialog
        globals()["ModelsManagerDialog"] = ModelsManagerDialog
        return ModelsManagerDialog
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
