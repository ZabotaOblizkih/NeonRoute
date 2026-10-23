"""
Avatar & Persona Controller (Mixin) for NeonRoute MainWindow.
Handles avatar context menu actions, lightbox popups, avatar path resolution,
interactive cropping, permanent avatar updates, and turn-based emotional expression switching
across all 3 UI zones (Header, Chat Bubbles, Sidebar) and all 3 application modes.
"""

import shutil
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any, Union

from PyQt6.QtCore import Qt, QPoint, QRect, QSize
from PyQt6.QtGui import QPixmap, QIcon, QAction, QCursor
from PyQt6.QtWidgets import QMenu, QFileDialog, QMessageBox, QApplication, QLabel

import config
from config import CHARACTERS_DIR
ASSETS_DIR = getattr(config, "ASSETS_DIR", getattr(config, "BASE_DIR", Path(".")) / "assets")
from services.session_manager import SessionManager
from ui.dialogs.avatar_lightbox_dialog import AvatarLightboxDialog
from ui.components.chat_bubble import render_circular_avatar

try:
    from ui.dialogs.avatar_crop_dialog import AvatarCropDialog
except ImportError:
    AvatarCropDialog = None

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("NeonRoute.AvatarController")


class AvatarControllerMixin:
    """
    Единый миксин управления аватарами для MainWindow.
    Синхронизирует 3 зоны: Шапку (верх), Баблы (чат), Сайдбар (боковое меню диалогов).
    """

    # =========================================================================
    # 1. Разрешение путей к аватарам (Resolution)
    # =========================================================================

    def _get_current_avatar_path(self) -> Optional[Path]:
        """
        Строго сессионное разрешение аватарки собеседника для всех 3 режимов.
        Никогда не вызывает утечек между сессиями или режимами.
        """
        mode = getattr(self, "current_mode", "assistant")
        sess = getattr(self, "current_session", None)

        # 1. Режим 'Ролевой чат'
        if mode == "roleplay":
            # А. Активный эмоциональный спрайт текущей сессии
            if sess and sess.get("current_emotion_avatar"):
                p_emo = Path(sess["current_emotion_avatar"])
                if p_emo.exists():
                    return p_emo
            # Б. Сессионный аватар карточки
            if sess and sess.get("character_data"):
                av = sess["character_data"].get("avatar_path")
                if av and Path(av).exists():
                    return Path(av)
            # В. Базовый аватар из объекта карточки
            if getattr(self, "active_character", None) and getattr(self.active_character, "avatar_path", None):
                p_card = Path(self.active_character.avatar_path)
                if p_card.exists():
                    return p_card
            return None

        # 2. Режим 'Инженер'
        is_eng = getattr(self, "_engineering_mode_active", False) or (
            hasattr(self, "is_engineer_mode_active") and self.is_engineer_mode_active()
        )
        if is_eng:
            if sess:
                sess_av = sess.get("assistant_avatar_path")
                if sess_av and Path(sess_av).exists():
                    return Path(sess_av)
            for cand in (ASSETS_DIR / "elaria_engineer.png", ASSETS_DIR / "elaria_avatar.png", CHARACTERS_DIR / "elaria.png"):
                if cand.exists():
                    return cand
            return None

        # 3. Режим 'Ассистент' (Диалог)
        if sess:
            sess_av = sess.get("assistant_avatar_path")
            if sess_av and Path(sess_av).exists():
                return Path(sess_av)
        for cand in (ASSETS_DIR / "elaria_avatar.png", CHARACTERS_DIR / "elaria.png"):
            if cand.exists():
                return cand
        return None

    def _get_user_avatar_path(self) -> Optional[Path]:
        """Разрешает аватар активной персоны пользователя."""
        sess_persona_id = self.current_session.get("user_persona_id") if getattr(self, "current_session", None) else None
        persona = SessionManager.get_active_user_persona(
            is_roleplay=(getattr(self, "current_mode", "") == "roleplay"),
            session_persona_id=sess_persona_id
        )
        av = persona.get("avatar_path")
        if av and Path(av).exists():
            return Path(av)

        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            cand = CHARACTERS_DIR / f"user_avatar{ext}"
            if cand.exists():
                return cand
        return None

    # =========================================================================
    # 2. Синхронизация аватаров по 3-м зонам UI
    # =========================================================================

    def _update_expression_avatar(self, sprite_path: Union[str, Path]):
        """
        Динамическое переключение эмоционального спрайта персонажа (Turn Expression):
        - Зона 1 (Шапка): мгновенно показывает текущую эмоцию.
        - Зона 2 (Чат): обновляет ТОЛЬКО текущий бабл сообщения (_turn_bubble). Прошлые баблы сохраняют свои эмоции!
        - Зона 3 (Сайдбар): обновляет иконку текущего диалога в левом списке.
        Базовый файл карточки на диске НЕ затрагивается.
        """
        if not sprite_path or not getattr(self, "current_session", None):
            return
        p = Path(sprite_path)
        if not p.exists():
            return

        str_path = str(p.resolve())
        is_rp = (getattr(self, "current_mode", "") == "roleplay" and getattr(self, "active_character", None))
        char_name = self.active_character.name if is_rp else "Элария"

        # Зона 1: Шапка (верхнее окно)
        if hasattr(self, "header_avatar_lbl") and self.header_avatar_lbl:
            round_pix = render_circular_avatar(str_path, char_name, size=48, is_user=False)
            self.header_avatar_lbl.setPixmap(round_pix)
            self.header_avatar_lbl.setText("")

        # Зона 2: Бабл сообщения текущего хода
        if getattr(self, "_turn_bubble", None) is not None:
            try:
                self._turn_bubble.update_avatar(str_path)
            except Exception:
                pass

        # Зона 3: Элемент диалога в левом сайдбаре
        sess_id = self.current_session.get("id")
        if sess_id and hasattr(self, "_session_items") and sess_id in self._session_items:
            _, item_widget = self._session_items[sess_id]
            item_widget.update_mode(getattr(self, "current_mode", "roleplay"), char_name, str_path)

        # Сохранение активной эмоции СТРОГО в текущую сессию
        self.current_session["current_emotion_avatar"] = str_path
        if "messages" in self.current_session and self.current_session["messages"]:
            for m in reversed(self.current_session["messages"]):
                if not m.get("is_user"):
                    m["avatar_path"] = str_path
                    break
        self._save_current_session()

    # Алиас для обратной совместимости
    _update_companion_expression = _update_expression_avatar

    def _propagate_avatar_update(self, new_avatar_path: Optional[Union[str, Path]]):
        """
        Постоянная смена аватара в сессии (пользователь выбрал новый файл):
        - Зона 1: Шапка чата.
        - Зона 2: ВСЕ баблы сообщений собеседника в активном чате.
        - Зона 3: Иконка текущего диалога в левом сайдбаре.
        """
        p_str = str(Path(new_avatar_path).resolve()) if new_avatar_path and Path(new_avatar_path).exists() else None

        # Зона 1: Шапка
        if hasattr(self, "header_avatar_lbl") and self.header_avatar_lbl:
            title = self.header_name_lbl.text() if hasattr(self, "header_name_lbl") else "Элария"
            round_pix = render_circular_avatar(p_str, title, size=48, is_user=False)
            self.header_avatar_lbl.setPixmap(round_pix)
            self.header_avatar_lbl.setText("")

        if hasattr(self, "_update_header_for_current_state"):
            self._update_header_for_current_state()

        # Зона 2: Все баблы чата
        if hasattr(self, "chat_layout"):
            from ui.components.chat_bubble import ChatMessageWidget
            for i in range(self.chat_layout.count()):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and isinstance(w, ChatMessageWidget) and not getattr(w, "is_user", True):
                    w.update_avatar(p_str)

        # Зона 3: Элемент в сайдбаре
        sess_id = self.current_session.get("id") if getattr(self, "current_session", None) else None
        if sess_id and hasattr(self, "_session_items") and sess_id in self._session_items:
            _, item_widget = self._session_items[sess_id]
            is_rp = (getattr(self, "current_mode", "") == "roleplay" and getattr(self, "active_character", None))
            char_name = self.active_character.name if is_rp else "Элария"
            item_widget.update_mode(getattr(self, "current_mode", "assistant"), char_name, p_str)

        # Синхронизация сохранённых сообщений сессии
        if getattr(self, "current_session", None) and "messages" in self.current_session:
            for m in self.current_session["messages"]:
                if not m.get("is_user"):
                    m["avatar_path"] = p_str
            self._save_current_session()

    # =========================================================================
    # 3. Интерактивная смена и сброс аватара
    # =========================================================================

    def _on_change_avatar_clicked(self):
        try:
            self._change_character_avatar()
        except Exception as e:
            logger.error(f"Error in _on_change_avatar_clicked: {e}", exc_info=True)

    def _change_character_avatar(self):
        """Интерактивный выбор и кадрирование аватара строго в контексте текущей сессии."""
        if not getattr(self, "current_session", None):
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите аватарку", "", "Изображения (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_path:
            return
        p = Path(file_path)
        if not p.exists():
            return

        avatars_dir = CHARACTERS_DIR / "session_avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)

        sess_id = self.current_session.get("id", "temp")
        is_rp = (getattr(self, "current_mode", "") == "roleplay" and getattr(self, "active_character", None))
        prefix = "rp" if is_rp else "asst"
        dest_path = (avatars_dir / f"sess_{sess_id}_{prefix}.png").resolve()

        cropped_saved = False
        try:
            from ui.dialogs.avatar_crop_dialog import AvatarCropDialog
            if AvatarCropDialog:
                crop_dlg = AvatarCropDialog(source_image_path=str(p), destination_path=str(dest_path), parent=self)
                if crop_dlg.exec():
                    if dest_path.exists() and dest_path.stat().st_size > 0:
                        cropped_saved = True
                else:
                    return
        except Exception as e:
            logger.debug(f"Crop dialog fallback: {e}")

        if not cropped_saved or not dest_path.exists():
            try:
                pix = render_circular_avatar(str(p), size=256)
                pix.save(str(dest_path), "PNG")
                cropped_saved = True
            except Exception:
                shutil.copy2(p, dest_path)
                cropped_saved = True

        abs_dest_str = str(dest_path.resolve())

        if is_rp:
            char_name = self.active_character.name or "Персонаж"
            cd = self.current_session.setdefault("character_data", {})
            cd["avatar_path"] = abs_dest_str
            self.current_session["current_emotion_avatar"] = abs_dest_str
            self._save_current_session()
            self.statusBar().showMessage(f"Аватар для чата '{char_name}' обновлен.", 4000)
        else:
            self.current_session["assistant_avatar_path"] = abs_dest_str
            self._save_current_session()
            self.statusBar().showMessage("Аватар ассистента для этого чата обновлен.", 4000)

        if hasattr(self, "header_controller") and self.header_controller:
            self.header_controller.sync_context(self.current_mode, self.active_character, self.current_session)
        self._propagate_avatar_update(dest_path)

    def _on_reset_avatar_clicked(self):
        try:
            self._reset_character_avatar()
        except Exception as e:
            logger.error(f"Error in _on_reset_avatar_clicked: {e}", exc_info=True)

    def _reset_character_avatar(self):
        """Сброс аватара строго в рамках текущей сессии к дефолтному."""
        if not getattr(self, "current_session", None):
            return

        sess_id = self.current_session.get("id")
        self.current_session["current_emotion_avatar"] = None

        if getattr(self, "current_mode", "") == "roleplay":
            orig_av = None
            if getattr(self, "active_character", None) and getattr(self.active_character, "avatar_path", None):
                orig_av = str(Path(self.active_character.avatar_path).resolve())

            cd = self.current_session.setdefault("character_data", {})
            cd["avatar_path"] = orig_av
            self._save_current_session()

            char_name = self.active_character.name if self.active_character else "Персонаж"
            if sess_id and hasattr(self, "_session_items") and sess_id in self._session_items:
                _, item_w = self._session_items[sess_id]
                item_w.update_mode("roleplay", char_name, orig_av)

            self._propagate_avatar_update(Path(orig_av) if orig_av else None)
            self.statusBar().showMessage(f"Аватар персонажа '{char_name}' сброшен к исходному.", 4000)
        else:
            self.current_session["assistant_avatar_path"] = None
            self._save_current_session()

            if sess_id and hasattr(self, "_session_items") and sess_id in self._session_items:
                _, item_w = self._session_items[sess_id]
                item_w.update_mode("assistant", "Элария", None)

            self._propagate_avatar_update(None)
            self.statusBar().showMessage("Аватар ассистента в этом чате сброшен.", 4000)

    # =========================================================================
    # 4. Меню и просмотр аватара
    # =========================================================================

    def _open_avatar_lightbox(self, avatar_path: Path):
        try:
            dlg = AvatarLightboxDialog(avatar_path, self)
            dlg.showFullScreen()
            dlg.exec()
        except Exception as e:
            logger.error(f"Error opening avatar lightbox: {e}", exc_info=True)

    def _on_header_avatar_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            av_path = self._get_current_avatar_path()
            if av_path and av_path.exists():
                self._open_avatar_lightbox(av_path)
            else:
                self._show_avatar_menu(QPoint(0, self.header_avatar_lbl.height()))
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_avatar_menu(QPoint(0, self.header_avatar_lbl.height()))
        else:
            QLabel.mousePressEvent(self.header_avatar_lbl, event)

    def _show_avatar_menu(self, pos):
        try:
            menu = QMenu(self)
            self._active_avatar_menu = menu
            menu.setStyleSheet("""
                QMenu {
                    background-color: rgba(24, 26, 34, 0.94);
                    color: #F1F5F9;
                    border: 1px solid rgba(255, 255, 255, 0.14);
                    border-radius: 8px;
                    padding: 6px;
                }
                QMenu::item {
                    padding: 6px 18px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QMenu::item:selected {
                    background-color: rgba(0, 240, 255, 0.16);
                    color: #00F0FF;
                }
                QMenu::separator {
                    height: 1px;
                    background-color: rgba(255, 255, 255, 0.08);
                    margin: 4px 6px;
                }
            """)

            act_change = menu.addAction("🖼️ Изменить аватар...")
            act_change.triggered.connect(self._on_change_avatar_clicked)

            act_sampling = menu.addAction("⚙️ Настройки модели...")
            act_sampling.triggered.connect(self._open_model_sampling_dialog)

            act_search = menu.addAction("🔍 Поиск по сообщениям")
            act_search.triggered.connect(self._on_search_clicked)

            act_media = menu.addAction("🖼️ Медиа галерея")
            act_media.triggered.connect(self._on_media_clicked)

            act_files = menu.addAction("📂 Файлы чата в проводнике")
            act_files.triggered.connect(self._on_open_chat_files_clicked)

            menu.addSeparator()

            act_reset = menu.addAction("❌ Сбросить аватар")
            act_reset.triggered.connect(self._on_reset_avatar_clicked)

            if getattr(self, "current_mode", "") == "roleplay":
                act_rename = menu.addAction("✏️ Переименовать...")
                act_rename.triggered.connect(self._on_rename_clicked)

            target_widget = getattr(self, "header_avatar_lbl", None)
            if isinstance(pos, QPoint) and target_widget:
                global_pos = target_widget.mapToGlobal(pos)
            else:
                global_pos = QCursor.pos()

            menu.exec(global_pos)
        except Exception as e:
            logger.error(f"Error opening avatar menu: {e}", exc_info=True)

    _show_header_context_menu = _show_avatar_menu
