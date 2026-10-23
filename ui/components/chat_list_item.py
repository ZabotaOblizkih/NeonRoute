"""
Chat Session List Item Widget for NeonRoute Sidebar
Renders circular avatars for both Assistant (NeonRoute avatar) and Roleplay (Character avatar).
"""

from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMenu

from ui.components.chat_bubble import render_circular_avatar


class ChatSessionItemWidget(QWidget):
    delete_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    change_persona_requested = pyqtSignal(str, str)

    def __init__(
        self,
        session_id: str,
        title: str,
        mode: str = "assistant",
        char_name: Optional[str] = None,
        avatar_path: Optional[str] = None,
        session_type: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.session_id = session_id
        self.mode = mode
        self.session_type = session_type
        self.char_name = char_name
        self.avatar_path = avatar_path

        self._setup_ui(title)

    def _setup_ui(self, title: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        # 1. Circular Avatar (30x30)
        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setFixedSize(30, 30)
        self.avatar_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.avatar_lbl)

        # 2. Text layout (Title + Badge)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_layout.addWidget(self.title_lbl)

        self.badge_lbl = QLabel(self)
        self.badge_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_layout.addWidget(self.badge_lbl)

        self._apply_theme_and_avatar()

        layout.addLayout(text_layout, stretch=1)

        # 3. Delete button
        self.btn_del = QPushButton("🗑️", self)
        self.btn_del.setFixedSize(20, 20)
        self.btn_del.setToolTip("Удалить диалог")
        self.btn_del.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_del.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #64748B;
                font-size: 11px;
                padding: 0px;
            }
            QPushButton:hover {
                color: #EF4444;
                background-color: #2D151B;
                border-radius: 4px;
            }
        """)
        self.btn_del.clicked.connect(lambda: self.delete_requested.emit(self.session_id))
        layout.addWidget(self.btn_del)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _apply_theme_and_avatar(self):
        raw_mode = str(self.mode or "").lower()
        raw_type = str(self.session_type or "").lower()
        is_engineer = (raw_type == "engineering" or raw_mode in ("engineer", "engineering"))
        is_rp = (raw_mode in ("roleplay", "rp")) and not is_engineer
        p = Path(self.avatar_path) if self.avatar_path and Path(self.avatar_path).exists() else None

        if is_engineer:
            pix = render_circular_avatar(p, "Инженер", size=30, is_user=False)
            self.avatar_lbl.setPixmap(pix)
            self.title_lbl.setStyleSheet("color: #F59E0B; font-weight: bold; font-size: 12px; background: transparent;")
            self.badge_lbl.setText("🛠️ Инженер")
            self.badge_lbl.setStyleSheet("color: #FBBF24; font-size: 10px; background: transparent;")
        elif is_rp:
            name_seed = self.char_name or "Roleplay"
            pix = render_circular_avatar(p, name_seed, size=30, is_user=False)
            self.avatar_lbl.setPixmap(pix)
            self.title_lbl.setStyleSheet("color: #E879F9; font-weight: bold; font-size: 12px; background: transparent;")
            badge_text = f"🎭 {self.char_name}" if self.char_name else "🎭 Ролевой чат"
            self.badge_lbl.setText(badge_text)
            self.badge_lbl.setStyleSheet("color: #C084FC; font-size: 10px; background: transparent;")
        else:
            # Ассистент: если персональный аватар для сессии не задан,
            # p равен None и отрисовывается аккуратный бейдж с инициалом «Э»
            pix = render_circular_avatar(p, "Элария", size=30, is_user=False)
            self.avatar_lbl.setPixmap(pix)
            self.title_lbl.setStyleSheet("color: #38BDF8; font-weight: bold; font-size: 12px; background: transparent;")
            self.badge_lbl.setText("⚡ Ассистент")
            self.badge_lbl.setStyleSheet("color: #00F0FF; font-size: 10px; background: transparent;")

    def update_avatar(self, avatar_path: Optional[str]):
        self.avatar_path = avatar_path
        self._apply_theme_and_avatar()

    def update_title(self, title: str):
        self.title_lbl.setText(title)

    def update_mode(self, mode: str, char_name: Optional[str] = None, avatar_path: Optional[str] = None, session_type: Optional[str] = None):
        self.mode = mode
        if session_type is not None:
            self.session_type = session_type
        self.char_name = char_name
        if avatar_path is not None:
            self.avatar_path = avatar_path
        self._apply_theme_and_avatar()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #121824;
                color: #E2E8F0;
                border: 1px solid #1E283D;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1C263B;
                color: #00F0FF;
            }
        """)
        act_rename = menu.addAction("✏️ Переименовать")
        act_rename.triggered.connect(lambda: self.rename_requested.emit(self.session_id))

        if self.mode == "roleplay":
            try:
                from services.session_manager import SessionManager
                profiles = SessionManager.load_user_profiles()
                rp = profiles.get("rp_settings", {})
                personas = rp.get("personas", {})
                sess = SessionManager.load_session(self.session_id)
                active_p_id = (sess.get("user_persona_id") if sess else None) or rp.get("active_persona_id", "persona_default")
                if active_p_id not in personas:
                    active_p_id = rp.get("active_persona_id") or (next(iter(personas.keys())) if personas else None)

                if personas:
                    sub_menu = menu.addMenu("👤 Персонаж игрока")
                    for pid, pdata in personas.items():
                        pname = pdata.get("name", "Игрок")
                        act = sub_menu.addAction(pname)
                        act.setCheckable(True)
                        if pid == active_p_id:
                            act.setChecked(True)
                        act.triggered.connect(lambda checked=False, target_id=pid: self.change_persona_requested.emit(self.session_id, target_id))
            except Exception:
                pass

        menu.addSeparator()
        act_del = menu.addAction("🗑️ Удалить диалог")
        act_del.triggered.connect(lambda: self.delete_requested.emit(self.session_id))
        menu.exec(QCursor.pos())