"""
Chat Session List Item Widget for NeonRoute Sidebar.
Renders circular avatars for both Assistant (NeonRoute avatar) and Roleplay (Character avatar).
Uses QFluentWidgets InfoBadge for mode chips, TransparentToolButton for delete, and RoundMenu for context menu.
"""

from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QCursor, QColor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy
from qfluentwidgets import (
    InfoBadge,
    TransparentToolButton,
    FluentIcon as FIF,
    RoundMenu,
    Action,
    ToolTipFilter,
    ToolTipPosition,
)

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

        self._full_title: str = title
        self._setup_ui(title)

    def sizeHint(self) -> QSize:
        return QSize(240, 42)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 38)

    def _setup_ui(self, title: str):
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 8, 2)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 1. Circular Avatar (28x28)
        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setFixedSize(28, 28)
        self.avatar_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.avatar_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 2. Текст названия диалога, идеально отцентрованный по вертикали
        self.title_lbl = QLabel(title, self)
        self.title_lbl.setMinimumHeight(20)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title_lbl.setStyleSheet("color: #F1F5F9; font-weight: 500; font-size: 13px; background: transparent;")

        self._apply_theme_and_avatar()

        layout.addWidget(self.title_lbl, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 3. Compact Delete button with soft hover
        self.btn_del = TransparentToolButton(FIF.DELETE, self)
        self.btn_del.setFixedSize(24, 24)
        self.btn_del.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_del.setToolTip("Удалить диалог")
        self.btn_del.installEventFilter(
            ToolTipFilter(self.btn_del, showDelay=300, position=ToolTipPosition.TOP)
        )
        self.btn_del.setStyleSheet("""
            TransparentToolButton {
                border-radius: 4px;
                padding: 2px;
            }
            TransparentToolButton:hover {
                background-color: rgba(239, 68, 68, 0.2);
            }
        """)
        self.btn_del.clicked.connect(lambda checked=False: self.delete_requested.emit(self.session_id))
        layout.addWidget(self.btn_del)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _apply_theme_and_avatar(self):
        raw_mode = str(self.mode or "").lower()
        raw_type = str(self.session_type or "").lower()
        is_engineer = (raw_type == "engineering" or raw_mode in ("engineer", "engineering"))
        is_rp = (raw_mode in ("roleplay", "rp")) and not is_engineer
        p = Path(self.avatar_path) if self.avatar_path and Path(self.avatar_path).exists() else None

        title_qss = "color: #F1F5F9; font-weight: 500; font-size: 13px; background: transparent;"
        self.title_lbl.setStyleSheet(title_qss)

        if is_engineer:
            pix = render_circular_avatar(p, "Инженер", size=28, is_user=False)
            self.avatar_lbl.setPixmap(pix)
        elif is_rp:
            name_seed = self.char_name or "Roleplay"
            pix = render_circular_avatar(p, name_seed, size=28, is_user=False)
            self.avatar_lbl.setPixmap(pix)
        else:
            # Ассистент
            pix = render_circular_avatar(p, "Элария", size=28, is_user=False)
            self.avatar_lbl.setPixmap(pix)

    def update_avatar(self, avatar_path: Optional[str]):
        self.avatar_path = avatar_path
        self._apply_theme_and_avatar()

    def _elide_title(self):
        if not hasattr(self, "title_lbl") or not hasattr(self, "_full_title"):
            return
        fm = self.title_lbl.fontMetrics()
        avail_w = max(10, self.title_lbl.width())
        elided = fm.elidedText(self._full_title, Qt.TextElideMode.ElideRight, avail_w)
        self.title_lbl.setText(elided)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_title()

    def update_title(self, title: str):
        self._full_title = title
        self._elide_title()

    def update_mode(self, mode: str, char_name: Optional[str] = None, avatar_path: Optional[str] = None, session_type: Optional[str] = None):
        self.mode = mode
        if session_type is not None:
            self.session_type = session_type
        self.char_name = char_name
        if avatar_path is not None:
            self.avatar_path = avatar_path
        self._apply_theme_and_avatar()

    def _show_context_menu(self, pos):
        menu = RoundMenu(parent=self)

        act_rename = Action(FIF.EDIT, "✏️ Переименовать", self)
        act_rename.triggered.connect(lambda: self.rename_requested.emit(self.session_id))
        menu.addAction(act_rename)

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
                    sub_menu = RoundMenu("👤 Персонаж игрока", self)
                    sub_menu.setIcon(FIF.PEOPLE)
                    for pid, pdata in personas.items():
                        pname = pdata.get("name", "Игрок")
                        act = Action(pname, self)
                        act.setCheckable(True)
                        if pid == active_p_id:
                            act.setChecked(True)
                        act.triggered.connect(
                            lambda checked=False, target_id=pid: self.change_persona_requested.emit(self.session_id, target_id)
                        )
                        sub_menu.addAction(act)
                    menu.addMenu(sub_menu)
            except Exception:
                pass

        menu.addSeparator()
        act_del = Action(FIF.DELETE, "🗑️ Удалить диалог", self)
        act_del.triggered.connect(lambda: self.delete_requested.emit(self.session_id))
        menu.addAction(act_del)

        menu.exec(QCursor.pos())
