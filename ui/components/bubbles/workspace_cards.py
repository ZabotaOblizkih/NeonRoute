"""
Interactive file cards, console output spoilers, and generation info accordions.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from config import WORKSPACE_DIR
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QApplication, QSizePolicy
)

def _get_selectable_text_browser():
    from ui.components.chat_bubble import SelectableTextBrowser
    return SelectableTextBrowser

logger = logging.getLogger("NeonRoute.WorkspaceCards")


class WorkspaceFileCardWidget(QFrame):
    run_script_requested = pyqtSignal(str)

    def __init__(self, file_path: Path | str, on_run: Optional[Callable] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.file_path = Path(file_path)
        self.on_run = on_run
        if on_run:
            self.run_script_requested.connect(on_run)
        self._init_ui()

    def _init_ui(self):
        self.setObjectName("WorkspaceFileCard")
        self.setStyleSheet("""
            QFrame#WorkspaceFileCard {
                background-color: rgba(15, 23, 42, 0.85);
                border: 1px solid rgba(0, 240, 255, 0.35);
                border-radius: 8px;
                padding: 6px 10px;
                margin-top: 4px;
                margin-bottom: 4px;
            }
            QFrame#WorkspaceFileCard:hover {
                border-color: #00F0FF;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        icon_lbl = QLabel(self)
        is_py = self.file_path.suffix.lower() == ".py"
        icon_lbl.setText("🐍" if is_py else "📄")
        icon_lbl.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        layout.addWidget(icon_lbl)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        name_lbl = QLabel(self.file_path.name, self)
        name_lbl.setStyleSheet("color: #00F0FF; font-weight: bold; font-size: 12px; background: transparent; border: none;")
        info_layout.addWidget(name_lbl)

        try:
            rel_path = self.file_path.relative_to(WORKSPACE_DIR)
            display_path = f"workspace / {rel_path}"
        except Exception:
            display_path = str(self.file_path)
            if len(display_path) > 40:
                display_path = "..." + display_path[-37:]

        path_lbl = QLabel(display_path, self)
        path_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent; border: none;")
        path_lbl.setToolTip(str(self.file_path))
        info_layout.addWidget(path_lbl)

        layout.addLayout(info_layout, stretch=1)

        btn_open = QPushButton("📂 В проводник", self)
        btn_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.8);
                color: #E2E8F0;
                border: 1px solid rgba(148, 163, 184, 0.3);
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #00F0FF;
                color: #00F0FF;
            }
        """)
        btn_open.clicked.connect(self._open_in_explorer)
        layout.addWidget(btn_open)

        if not is_py:
            btn_view = QPushButton("📄 Открыть", self)
            btn_view.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_view.setStyleSheet("""
                QPushButton {
                    background-color: rgba(6, 78, 59, 0.7);
                    color: #34D399;
                    border: 1px solid #10B981;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #10B981;
                    color: #022C22;
                }
            """)
            btn_view.clicked.connect(lambda: os.startfile(str(self.file_path.resolve())))
            layout.addWidget(btn_view)

        if is_py:
            self.btn_run = QPushButton("▶ Запустить", self)
            self.btn_run.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_run.setStyleSheet("""
                QPushButton {
                    background-color: rgba(6, 78, 59, 0.7);
                    color: #34D399;
                    border: 1px solid #10B981;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #10B981;
                    color: #022C22;
                }
            """)
            try:
                self.btn_run.clicked.disconnect()
            except Exception:
                pass
            self.btn_run.clicked.connect(lambda: self.run_script_requested.emit(str(self.file_path)))
            layout.addWidget(self.btn_run)

    def _open_in_explorer(self):
        try:
            if self.file_path.exists():
                subprocess.Popen(f'explorer.exe /select,"{self.file_path.resolve()}"')
            else:
                parent_dir = self.file_path.parent
                if parent_dir.exists():
                    subprocess.Popen(f'explorer.exe "{parent_dir.resolve()}"')
        except Exception as e:
            logger.error(f"Failed to open explorer for {self.file_path}: {e}")


class ConsoleOutputSpoilerWidget(QFrame):
    def __init__(
        self,
        title: str = "Консоль вывода",
        output: str = "",
        is_error: bool = False,
        return_code: int = 0,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.title = title
        self.output = output.strip() if output else "(нет вывода)"
        self.is_error = is_error or (return_code != 0)
        self.return_code = return_code
        self.is_expanded = True
        self._init_ui()

    def _init_ui(self):
        self.setObjectName("ConsoleSpoilerFrame")
        self.setStyleSheet("""
            QFrame#ConsoleSpoilerFrame {
                background-color: rgba(10, 14, 23, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                margin-top: 4px;
                margin-bottom: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_header_text()
        self.toggle_btn.clicked.connect(self._toggle_collapse)
        layout.addWidget(self.toggle_btn)

        browser_cls = _get_selectable_text_browser()
        self.console_browser = browser_cls(self)
        self.console_browser.setReadOnly(True)
        self.console_browser.setOpenExternalLinks(False)
        self.console_browser.setPlainText(self.output)
        self.console_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #07090E;
                color: #A7F3D0;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11px;
                line-height: 1.4;
                border: 1px solid rgba(0, 240, 255, 0.2);
                border-radius: 4px;
                padding: 6px;
            }
        """ if not self.is_error else """
            QTextBrowser {
                background-color: #0E0709;
                color: #FCA5A5;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11px;
                line-height: 1.4;
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 4px;
                padding: 6px;
            }
        """)

        line_count = max(len(self.output.splitlines()), 1)
        target_height = min(max(line_count * 18 + 16, 40), 220)
        self.console_browser.setFixedHeight(target_height)

        layout.addWidget(self.console_browser)
        self.console_browser.setVisible(self.is_expanded)

    def _update_header_text(self):
        arrow = "▼" if self.is_expanded else "▶"
        status_text = f"✔ Код: {self.return_code}" if not self.is_error else f"✖ Ошибка (код {self.return_code})"
        status_color = "#34D399" if not self.is_error else "#F87171"
        self.toggle_btn.setText(f"{arrow} [{self.title}]  •  {status_text}")
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {status_color};
                text-align: left;
                font-size: 11px;
                font-weight: bold;
                border: none;
                padding: 3px 6px;
            }}
            QPushButton:hover {{
                color: #00F0FF;
            }}
        """)

    def _toggle_collapse(self):
        self.is_expanded = not self.is_expanded
        self.console_browser.setVisible(self.is_expanded)
        self._update_header_text()


class GenerationInfoAccordionWidget(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, params: dict, max_width: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.params = params or {}
        self.max_width = max_width
        self.is_expanded = False
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background: transparent; border: none;")
        self.setMinimumWidth(320)
        if self.max_width:
            self.setMaximumWidth(self.max_width)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.toggle_btn = QPushButton("ℹ️ Инфо генерации ▸", self)
        self.toggle_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(18, 24, 38, 0.65);
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
                text-align: left;
            }
            QPushButton:hover {
                background-color: rgba(56, 189, 248, 0.18);
                color: #00F0FF;
                border-color: #00F0FF;
            }
        """)
        self.toggle_btn.clicked.connect(self._toggle_collapse)
        layout.addWidget(self.toggle_btn)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("GenInfoContent")
        self.content_frame.setStyleSheet("""
            QFrame#GenInfoContent {
                background-color: rgba(10, 14, 23, 0.90);
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-left: 3px solid #00F0FF;
                border-radius: 6px;
                padding: 4px;
            }
            QLabel {
                color: #CBD5E1;
                font-size: 11px;
                background: transparent;
                border: none;
            }
        """)
        cf_layout = QVBoxLayout(self.content_frame)
        cf_layout.setContentsMargins(8, 6, 8, 6)
        cf_layout.setSpacing(4)

        ckpt = self.params.get("checkpoint", "SD Forge")
        seed = self.params.get("seed", -1)
        sampler = self.params.get("sampler_name", "DPM++ 2M")
        steps = self.params.get("steps", 28)
        denoise = self.params.get("denoising_strength", 0.35)
        prompt = self.params.get("positive_prompt", "").strip()

        lbl_ckpt = QLabel(f"🎨 <b>Модель:</b> {ckpt}", self.content_frame)
        lbl_ckpt.setStyleSheet("color: #38BDF8; font-size: 11px;")
        lbl_ckpt.setWordWrap(True)
        cf_layout.addWidget(lbl_ckpt)

        lbl_seed = QLabel(f"🌱 <b>Сид:</b> {seed}", self.content_frame)
        lbl_seed.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cf_layout.addWidget(lbl_seed)

        lbl_settings = QLabel(f"⚙️ <b>Сэмплер:</b> {sampler} • {steps} steps • Denoise {denoise}", self.content_frame)
        lbl_settings.setWordWrap(True)
        cf_layout.addWidget(lbl_settings)

        if prompt:
            p_header = QHBoxLayout()
            p_header.setSpacing(4)
            lbl_p_title = QLabel("📝 <b>Prompt:</b>", self.content_frame)
            lbl_p_title.setStyleSheet("color: #F8FAFC; font-weight: bold; font-size: 11px;")
            p_header.addWidget(lbl_p_title)
            p_header.addStretch()

            self.btn_copy_prompt = QPushButton("📋 Копировать", self.content_frame)
            self.btn_copy_prompt.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_copy_prompt.setStyleSheet("""
                QPushButton {
                    background-color: rgba(30, 41, 59, 0.8);
                    color: #00F0FF;
                    border: 1px solid rgba(0, 240, 255, 0.4);
                    border-radius: 3px;
                    padding: 2px 6px;
                    font-size: 10px;
                }
                QPushButton:hover {
                    background-color: #00F0FF;
                    color: #050B14;
                }
            """)
            self.btn_copy_prompt.clicked.connect(self._copy_prompt)
            p_header.addWidget(self.btn_copy_prompt)
            cf_layout.addLayout(p_header)

            browser_cls = _get_selectable_text_browser()
            self.prompt_browser = browser_cls(self.content_frame)
            self.prompt_browser.setReadOnly(True)
            self.prompt_browser.setPlainText(prompt)
            self.prompt_browser.setStyleSheet("""
                QTextBrowser {
                    background-color: rgba(14, 20, 32, 0.8);
                    color: #94A3B8;
                    font-family: Consolas, 'Courier New', monospace;
                    font-size: 10px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    padding: 4px;
                }
            """)
            self.prompt_browser.setMinimumHeight(55)
            self.prompt_browser.setMaximumHeight(110)
            self.prompt_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            cf_layout.addWidget(self.prompt_browser)

        neg_prompt = self.params.get("negative_prompt", "").strip()
        if neg_prompt:
            np_header = QHBoxLayout()
            np_header.setSpacing(4)
            lbl_np_title = QLabel("🚫 <b>Negative Prompt:</b>", self.content_frame)
            lbl_np_title.setStyleSheet("color: #FB7185; font-weight: bold; font-size: 11px;")
            np_header.addWidget(lbl_np_title)
            np_header.addStretch()

            self.btn_copy_neg = QPushButton("📋 Копировать", self.content_frame)
            self.btn_copy_neg.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_copy_neg.setStyleSheet("""
                QPushButton {
                    background-color: rgba(30, 41, 59, 0.8);
                    color: #FB7185;
                    border: 1px solid rgba(251, 113, 133, 0.4);
                    border-radius: 3px;
                    padding: 2px 6px;
                    font-size: 10px;
                }
                QPushButton:hover {
                    background-color: #FB7185;
                    color: #050B14;
                }
            """)
            self.btn_copy_neg.clicked.connect(self._copy_negative_prompt)
            np_header.addWidget(self.btn_copy_neg)
            cf_layout.addLayout(np_header)

            browser_cls = _get_selectable_text_browser()
            self.neg_browser = browser_cls(self.content_frame)
            self.neg_browser.setReadOnly(True)
            self.neg_browser.setPlainText(neg_prompt)
            self.neg_browser.setStyleSheet("""
                QTextBrowser {
                    background-color: rgba(14, 20, 32, 0.8);
                    color: #94A3B8;
                    font-family: Consolas, 'Courier New', monospace;
                    font-size: 10px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    padding: 4px;
                }
            """)
            self.neg_browser.setMinimumHeight(55)
            self.neg_browser.setMaximumHeight(110)
            self.neg_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            cf_layout.addWidget(self.neg_browser)

        layout.addWidget(self.content_frame)
        self.content_frame.setVisible(False)

    def _toggle_collapse(self):
        self.is_expanded = not self.is_expanded
        self.content_frame.setVisible(self.is_expanded)
        arrow = "▾" if self.is_expanded else "▸"
        self.toggle_btn.setText(f"ℹ️ Инфо генерации {arrow}")
        self.updateGeometry()
        self.toggled.emit(self.is_expanded)

    def update_adaptive_palette(self, palette: dict):
        if not palette:
            return
        card_bg = palette.get("card", "#1E293B")
        card_hover = palette.get("card_hover", "#334155")
        accent = palette.get("accent", "#00F0FF")
        card_border = palette.get("card_border", "#334155")
        text_sec = palette.get("text_secondary", "#94A3B8")
        text_pri = palette.get("text_primary", "#F8FAFC")
        term_bg = palette.get("terminal_bg", "#0F172A")

        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {card_bg};
                color: {accent};
                border: 1px solid {card_border};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {card_hover};
                border-color: {accent};
            }}
        """)
        self.content_frame.setStyleSheet(f"""
            QFrame#GenInfoContent {{
                background-color: {term_bg};
                border: 1px solid {card_border};
                border-left: 3px solid {accent};
                border-radius: 8px;
                padding: 6px;
            }}
            QLabel {{
                color: {text_sec};
                font-size: 11px;
                background: transparent;
                border: none;
            }}
        """)
        btn_copy_qss = f"""
            QPushButton {{
                background-color: {card_bg};
                color: {accent};
                border: 1px solid {card_border};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {card_hover};
                border-color: {accent};
            }}
        """
        if hasattr(self, "btn_copy_prompt") and self.btn_copy_prompt:
            self.btn_copy_prompt.setStyleSheet(btn_copy_qss)
        if hasattr(self, "btn_copy_neg") and self.btn_copy_neg:
            self.btn_copy_neg.setStyleSheet(btn_copy_qss)

        browser_qss = f"""
            QTextBrowser {{
                background-color: rgba(10, 14, 23, 0.7);
                color: {text_pri};
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11px;
                border: 1px solid {card_border};
                border-radius: 6px;
                padding: 6px;
            }}
        """
        if hasattr(self, "prompt_browser") and self.prompt_browser:
            self.prompt_browser.setStyleSheet(browser_qss)
        if hasattr(self, "neg_browser") and self.neg_browser:
            self.neg_browser.setStyleSheet(browser_qss)

    def _copy_prompt(self):
        prompt = self.params.get("positive_prompt", "")
        if prompt:
            QApplication.clipboard().setText(prompt)
            self.btn_copy_prompt.setText("✔ Скопировано")
            QTimer.singleShot(1800, lambda: self.btn_copy_prompt.setText("📋 Копировать"))

    def _copy_negative_prompt(self):
        neg_prompt = self.params.get("negative_prompt", "")
        if neg_prompt:
            QApplication.clipboard().setText(neg_prompt)
            self.btn_copy_neg.setText("✔ Скопировано")
            QTimer.singleShot(1800, lambda: self.btn_copy_neg.setText("📋 Копировать"))
