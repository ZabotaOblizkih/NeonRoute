import os
import re
import time
import shutil
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import (
    QMessageBox, QInputDialog, QDialog, QFileDialog, QLabel
)
from PyQt6.QtGui import QPixmap, QCursor

from config import (
    EXPORTS_DIR, OUTPUTS_DIR, AUDIO_DIR, BACKGROUNDS_DIR
)
from utils.config_manager import ConfigManager
from services.session_manager import SessionManager
from services.workspace_launcher import WorkspaceLauncher

from ui.components.chat_bubble import ChatMessageWidget
from ui.dialogs.clean_cache_dialog import CleanCacheDialog
from ui.dialogs.background_dialog import BackgroundSettingsDialog
from ui.dialogs.media_gallery_dialog import ChatMediaGalleryDialog
from ui.dialogs.user_settings_dialog import UserSettingsDialog
from ui.dialogs.about_dialog import AboutDialog
from ui.dialogs.help_dialog import HelpDialog
from ui.dialogs.model_sampling_dialog import ModelSamplingDialog
from ui.log_console import LogConsoleDialog

logger = logging.getLogger("NeonRoute.DialogController")


class DialogControllerMixin:
    """Миксин для открытия диалоговых окон, настроек и экшенов меню."""

    def _open_user_settings_dialog(self, initial_tab: int = 0):
        try:
            dlg = UserSettingsDialog(initial_tab=initial_tab, parent=self)
            dlg.settings_applied.connect(self._on_settings_applied)
            dlg.profiles_updated.connect(self._on_user_profiles_updated)
            dlg.exec()
        except Exception as e:
            logger.error(f"[NeonRoute] Ошибка открытия окна настроек Эларии: {e}", exc_info=True)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Ошибка настроек", f"Не удалось открыть настройки профиля:\n{e}")

    def _on_settings_applied(self, new_config: dict):
        self.app_config = new_config
        if hasattr(self, "chat_controller"):
            self.chat_controller.update_config(new_config)

        # Синхронизация модели через сигнал
        selected_model = new_config.get("active_model")
        if selected_model and hasattr(self, "lm_combo") and self.lm_combo:
            idx = self.lm_combo.findText(selected_model)
            if idx >= 0:
                self.lm_combo.setCurrentIndex(idx)
            else:
                self.lm_combo.addItem(selected_model)
                self.lm_combo.setCurrentIndex(self.lm_combo.count() - 1)

    def _on_user_profiles_updated(self):
        self.app_config = ConfigManager.load_config()
        self._update_header_for_current_state()

        # 1. Мгновенно обновляем аватар пользователя во всех баблах активного чата
        new_user_avatar = self._get_user_avatar_path()
        for w in self._get_chat_message_widgets():
            if getattr(w, "is_user", False):
                w.update_avatar(new_user_avatar)

        # 2. Если ролевой чат на стадии завязки (нет ходов игрока) — реактивно обновляем {{user}} в приветствии
        if (
            self.current_mode == "roleplay"
            and self.active_character
            and self.current_session
            and len(self.current_session.get("messages", [])) == 1
            and not self.current_session["messages"][0].get("is_user")
        ):
            from services.macro_engine import MacroEngine
            u_name = self._get_active_user_name(is_roleplay=True)
            pool = self.active_character.get_greetings_pool()
            idx = self.current_session.get("active_greeting_idx", 0)
            if pool and 0 <= idx < len(pool):
                raw_eff = pool[idx]["effective"]
                raw_ru = pool[idx]["ru"]
            else:
                raw_eff = self.active_character.first_mes_ru or self.active_character.first_mes or ""
                raw_ru = self.active_character.first_mes_ru or ""

            if raw_eff:
                updated_greeting = MacroEngine.process(raw_eff, user_name=u_name, char_name=self.active_character.name)
                updated_ru = MacroEngine.process(raw_ru, user_name=u_name, char_name=self.active_character.name) if raw_ru else None
                widgets = self._get_chat_message_widgets()
                if widgets:
                    widgets[0].set_text(updated_greeting)
                    if updated_ru:
                        widgets[0].translated_text = updated_ru
                        if widgets[0].is_showing_translation:
                            widgets[0]._update_rendered_text()
                if self.chat_history:
                    self.chat_history[0]["content"] = updated_greeting
                self.current_session["messages"][0]["text"] = updated_greeting
                if updated_ru:
                    self.current_session["messages"][0]["translated_text"] = updated_ru
                self._save_current_session()

        self.statusBar().showMessage("Личные настройки профиля обновлены и применены к чату.", 3000)

    def _on_action_export_clicked(self):
        """Action for Export button: exports active session to exports folder and opens explorer."""
        try:
            self._save_current_session()
            if self.current_session and self.current_session.get("chat_history"):
                import json
                s_id = str(self.current_session.get("id", "session"))
                title = self.current_session.get("title", "chat")
                safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "session"
                exp_json = EXPORTS_DIR / f"{safe_title}_{s_id[:8]}_{int(time.time())}.json"
                with open(exp_json, "w", encoding="utf-8") as f:
                    json.dump(self.current_session, f, ensure_ascii=False, indent=2)
                self.statusBar().showMessage(f"Сессия экспортирована: {exp_json.name}", 3000)
            os.startfile(str(EXPORTS_DIR))
        except Exception as e:
            logger.error(f"Error on export action: {e}")
            try:
                os.startfile(str(EXPORTS_DIR))
            except Exception:
                pass

    def _on_action_audio_clicked(self):
        """Action for Audio button: triggers TTS for last assistant message, or opens audio folder."""
        try:
            target_widget = None
            target_text = ""
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and isinstance(w, ChatMessageWidget) and not getattr(w, "is_user", True):
                    target_widget = w
                    target_text = getattr(w, "translated_text", None) or getattr(w, "raw_text", "")
                    break
            if target_widget and target_text:
                self._enqueue_tts_task(target_text, target_widget)
            else:
                os.startfile(str(AUDIO_DIR))
        except Exception as e:
            logger.error(f"Error on audio action: {e}")
            try:
                os.startfile(str(AUDIO_DIR))
            except Exception:
                pass

    def _open_clean_cache_dialog(self):
        """Opens cache cleanup dialog."""
        dialog = CleanCacheDialog(self)
        dialog.exec()

    def _open_background_settings_dialog(self):
        """Opens compact background and dimming customization dialog."""
        dialog = BackgroundSettingsDialog(
            current_path=self.background_path,
            current_dim_alpha=self.background_dim_opacity,
            parent=self
        )
        dialog.background_changed.connect(self._on_background_changed)
        dialog.dimming_changed.connect(self._on_background_dimming_changed)
        dialog.background_reset.connect(self._on_background_reset)
        dialog.exec()

    def _on_background_changed(self, file_path: str):
        src_path = Path(file_path)
        if not src_path.exists():
            return

        BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
        dest_filename = f"bg_{src_path.name}"
        dest_path = BACKGROUNDS_DIR / dest_filename
        try:
            if src_path.resolve() != dest_path.resolve():
                shutil.copy2(src_path, dest_path)
        except Exception as e:
            logger.error(f"Failed to copy background image: {e}")
            dest_path = src_path

        self.background_path = str(dest_path)
        self.background_pixmap = QPixmap(str(dest_path))
        self.app_config["background_path"] = self.background_path
        ConfigManager.save_config(self.app_config)

        if self.current_session:
            self.current_session["background_path"] = self.background_path
            self._save_current_session()

        self._update_background_cache()
        self.update()
        self.statusBar().showMessage("Фон окна успешно обновлен.", 3000)

    def _on_background_dimming_changed(self, alpha: int):
        self.background_dim_opacity = max(0, min(240, alpha))
        self.app_config["background_dim_opacity"] = self.background_dim_opacity
        ConfigManager.save_config(self.app_config)

        if self.current_session:
            self.current_session["bg_dim_alpha"] = self.background_dim_opacity
            self._save_current_session()

        self._update_background_cache()
        self.update()

    def _on_background_reset(self):
        """Resets background to default dark cyberpunk theme."""
        self.background_path = None
        self.background_pixmap = None
        self.app_config["background_path"] = None
        ConfigManager.save_config(self.app_config)

        if self.current_session:
            self.current_session["background_path"] = None
            self._save_current_session()

        self._bg_render_cache = None
        self.update()
        self.statusBar().showMessage("Фон окна сброшен к стандартной теме.", 3000)

    def _open_log_console_dialog(self):
        """Opens live log viewer dialog."""
        if self._log_console_dialog is None:
            self._log_console_dialog = LogConsoleDialog(self)
        self._log_console_dialog.show()
        self._log_console_dialog.raise_()
        self._log_console_dialog.activateWindow()

    def _open_help_dialog(self):
        """Opens Help / User Guide dialog."""
        dialog = HelpDialog(self)
        dialog.exec()

    def _open_about_dialog(self):
        """Opens About dialog."""
        dialog = AboutDialog(self)
        dialog.exec()

    def open_dependency_dialog(self):
        from ui.dialogs.dependency_dialog import DependencyDialog
        dlg = DependencyDialog(parent=self)
        dlg.exec()

    def _open_global_model_sampling_dialog(self):
        """Opens ModelSamplingDialog for setting persistent defaults in config.json."""
        from services.session_manager import get_default_sampling_params
        from utils.config_manager import ConfigManager

        is_eng = self.is_engineer_mode_active()
        active_mode = "engineer" if is_eng else self.current_mode
        current_defaults = get_default_sampling_params(active_mode)

        dlg = ModelSamplingDialog(
            current_params=current_defaults,
            mode=self.current_mode,
            is_engineer_mode=is_eng,
            is_global_defaults=True,
            parent=self
        )
        if dlg.exec():
            new_params = dlg.get_sampling_params()
            cfg_key = f"default_sampling_{active_mode}"
            if not hasattr(self, "app_config") or self.app_config is None:
                self.app_config = {}
            self.app_config[cfg_key] = new_params
            from services.config_manager import ConfigManager
            ConfigManager.save_config(self.app_config)
            if hasattr(self, "chat_controller"):
                self.chat_controller.update_config(self.app_config)
            logger.info(
                f"[ConfigManager] [GLOBAL-DEFAULT] Обновлены дефолты для '{cfg_key}' в config.json: "
                f"temp={new_params.get('temperature')}, min_p={new_params.get('min_p')}, max_tok={new_params.get('max_tokens')}"
            )

            # Если у текущей открытой сессии того же режима is_custom_sampling равен False, сразу обновляем
            if self.current_session and not self.current_session.get("is_custom_sampling", False):
                sess_mode = self.current_session.get("mode", "assistant")
                sess_effective_mode = "engineer" if (sess_mode == "assistant" and is_eng) else sess_mode
                if sess_effective_mode == active_mode:
                    self.current_session["sampling_params"] = dict(new_params)
                    if "is_dm_mode" in new_params:
                        self.current_session["is_dm_mode"] = new_params["is_dm_mode"]
                    self._save_current_session()
                    logger.info(f"[SessionManager] Текущая сессия {self.current_session.get('id')} синхронизирована с новыми дефолтами {active_mode}.")

            self.statusBar().showMessage(
                f"Глобальные параметры для режима '{active_mode}' сохранены в config.json (MaxTok: {new_params['max_tokens']}).",
                4000
            )

    def _open_model_sampling_dialog(self):
        """Opens ModelSamplingDialog to adjust sampling strictly for the active session."""
        if not self.current_session:
            return
        current_params = dict(self.current_session.get("sampling_params") or {})
        if "is_dm_mode" in self.current_session:
            current_params["is_dm_mode"] = self.current_session.get("is_dm_mode", False)
        is_eng = self.is_engineer_mode_active()
        dlg = ModelSamplingDialog(
            current_params=current_params,
            mode=self.current_mode,
            is_engineer_mode=is_eng,
            is_global_defaults=False,
            parent=self
        )
        if dlg.exec():
            new_params = dlg.get_sampling_params()
            if self.current_session:
                self.current_session["sampling_params"] = new_params
                self.current_session["is_custom_sampling"] = not getattr(dlg, "was_reset_to_defaults", False)
                if "is_dm_mode" in new_params:
                    self.current_session["is_dm_mode"] = new_params["is_dm_mode"]
                self._save_current_session()
                logger.info(
                    f"[SessionManager] [CHAT-OVERRIDE] Установлены индивидуальные параметры для сессии "
                    f"'{self.current_session.get('id')}': temp={new_params.get('temperature')}, "
                    f"min_p={new_params.get('min_p')}, max_tok={new_params.get('max_tokens')} (is_custom={self.current_session['is_custom_sampling']})"
                )

            self.statusBar().showMessage(
                f"Параметры сессии сохранены (MaxTok: {new_params['max_tokens']}).",
                4000
            )

    def _on_open_chat_files_clicked(self):
        sess_id = self.current_session.get("id") if self.current_session else None
        WorkspaceLauncher.open_chat_folder(sess_id, mode=self.current_mode)

    def _on_media_clicked(self):
        try:
            self._open_chat_media_gallery()
        except Exception as e:
            logger.error(f"Error in _on_media_clicked: {e}", exc_info=True)

    def _on_rename_clicked(self):
        try:
            self._rename_character_or_dialog()
        except Exception as e:
            logger.error(f"Error in _on_rename_clicked: {e}", exc_info=True)

    def _rename_character_or_dialog(self):
        current_name = ""
        if self.current_mode == "roleplay" and self.active_character:
            current_name = self.active_character.name
        elif self.current_session:
            current_name = self.current_session.get("title", "")

        new_name, ok = QInputDialog.getText(
            self, "Переименовать", "Новое имя:", text=current_name
        )
        if ok and new_name.strip():
            new_name = new_name.strip()
            if self.current_mode == "roleplay" and self.active_character:
                self.active_character.name = new_name
                self.char_name_lbl.setText(new_name)
                if self.current_session and "character_data" in self.current_session and self.current_session["character_data"]:
                    self.current_session["character_data"]["name"] = new_name

            if self.current_session:
                self.current_session["title"] = new_name
                self._save_current_session()
                sess_id = self.current_session["id"]
                if sess_id in self._session_items:
                    _, w = self._session_items[sess_id]
                    w.title_lbl.setText(new_name)

            self._update_header_for_current_state()
            self.statusBar().showMessage(f"Имя изменено на '{new_name}'.", 4000)

    def _open_chat_media_gallery(self):
        # Агрегация всех сгенерированных артов из всех чатов
        all_images = []
        sessions_dir = OUTPUTS_DIR / "sessions"
        if sessions_dir.exists():
            for img_p in sessions_dir.rglob("*"):
                if img_p.is_file() and img_p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    all_images.append(img_p)
        for img_p in OUTPUTS_DIR.glob("*"):
            if img_p.is_file() and img_p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                all_images.append(img_p)

        dlg = ChatMediaGalleryDialog(
            session_id="all_sessions",
            session_title="Общая галерея",
            image_paths=all_images,
            parent=self
        )
        dlg.exec()


__all__ = ["DialogControllerMixin"]
