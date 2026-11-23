"""
Mobile Server QR & Management Dialog for NeonRoute.
Provides interactive QR-code scanning, network interface selection, URL copying,
and server lifecycle controls in Windows 11 Fluent Design (FramelessDialog, FluentTitleBar).
"""

from typing import List, Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QApplication,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
    SubtitleLabel,
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
)

from mobile_server.api import get_mobile_server_urls, stop_mobile_server_thread
from ui.common.icon_utils import render_qr_pixmap
from ui.common.palette_utils import extract_adaptive_palette
from ui.common.toast_helper import toast_success


class MobileServerDialog(FramelessDialog):
    """
    Модальное окно управления мобильным сервером и отображения QR-кода
    в стиле Windows 11 Fluent Design.
    """

    def __init__(self, parent=None, urls: Optional[List[str]] = None):
        super().__init__(parent=parent)
        self.setObjectName("MobileServerDialog")
        self.setWindowTitle("Мобильный сервер NeonRoute")
        self.resize(440, 540)
        self.setMinimumSize(400, 500)

        self.urls: List[str] = urls or get_mobile_server_urls()
        self.active_url: str = self.urls[0] if self.urls else "http://127.0.0.1:8000"
        self.stop_server_requested: bool = False

        # 1. Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(44)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.accept)

        self._init_ui()
        self._apply_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent() if hasattr(p, "parent") else None
        return None

    def _apply_palette(self):
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap) if pixmap else {
            "bg": "#0D1117",
            "card": "#161B22",
            "border": "#30363D",
            "card_border": "#21262D",
            "accent": "#00F0FF",
            "text": "#F8FAFC",
            "text_secondary": "#94A3B8",
            "btn_hover": "#00D2DF",
            "btn_hover_text": "#0A0D14",
            "card_hover": "#1F242C",
        }

        self.setStyleSheet(f"""
            MobileServerDialog#MobileServerDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#MobileServerBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
        """)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("MobileServerBodyWidget")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(24, 12, 24, 20)
        body_layout.setSpacing(14)
        body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 1. Заголовок и пояснение
        self.title_lbl = SubtitleLabel("Мобильный сервер активен", self.body_widget)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_layout.addWidget(self.title_lbl)

        self.hint_lbl = BodyLabel(
            "Отсканируйте QR-код камерой телефона (в одной сети Wi-Fi или VPN), "
            "чтобы открыть NeonRoute в браузере:",
            self.body_widget,
        )
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; line-height: 1.4;")
        body_layout.addWidget(self.hint_lbl)

        # 2. QR-код в белом защитном контейнере
        self.qr_frame = QFrame(self.body_widget)
        self.qr_frame.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border-radius: 14px; padding: 6px; }"
        )
        qr_layout = QVBoxLayout(self.qr_frame)
        qr_layout.setContentsMargins(12, 12, 12, 12)
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.qr_label = QLabel(self.qr_frame)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setPixmap(render_qr_pixmap(self.active_url, module_px=6, quiet_modules=2))
        qr_layout.addWidget(self.qr_label)

        qr_row = QHBoxLayout()
        qr_row.addStretch(1)
        qr_row.addWidget(self.qr_frame)
        qr_row.addStretch(1)
        body_layout.addLayout(qr_row)

        # 3. Текстовый URL
        self.url_lbl = BodyLabel(self.active_url, self.body_widget)
        self.url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_lbl.setStyleSheet("color: #00F0FF; font-weight: 600; font-size: 14px;")
        body_layout.addWidget(self.url_lbl)

        # 4. Выбор сетевого интерфейса (если доступно несколько IP)
        if len(self.urls) > 1:
            self.addr_combo = ComboBox(self.body_widget)
            self.addr_combo.addItems(self.urls)
            self.addr_combo.setCurrentIndex(0)
            self.addr_combo.currentTextChanged.connect(self._on_addr_changed)
            body_layout.addWidget(self.addr_combo)

        # 5. Кнопка копирования
        self.btn_copy = PushButton(FIF.COPY, "Копировать ссылку", self.body_widget)
        self.btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy.clicked.connect(self._copy_url)
        body_layout.addWidget(self.btn_copy)

        body_layout.addStretch(1)

        # 6. Нижний ряд кнопок («Остановить сервер» и «Готово»)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_stop = PushButton("Остановить сервер", self.body_widget)
        self.btn_stop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_stop.setStyleSheet("""
            PushButton {
                color: #EF4444;
                border: 1px solid rgba(239, 68, 68, 0.4);
                border-radius: 6px;
                padding: 6px 14px;
            }
            PushButton:hover {
                background-color: rgba(239, 68, 68, 0.15);
                border-color: #EF4444;
                color: #F87171;
            }
        """)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(self.btn_stop)

        self.btn_done = PrimaryPushButton("Готово", self.body_widget)
        self.btn_done.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_done.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_done)

        body_layout.addLayout(btn_row)
        root_layout.addWidget(self.body_widget)

    def _on_addr_changed(self, new_url: str):
        if not new_url:
            return
        self.active_url = new_url
        self.url_lbl.setText(new_url)
        self.qr_label.setPixmap(render_qr_pixmap(new_url, module_px=6, quiet_modules=2))

    def _copy_url(self):
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.url_lbl.text())
        parent_for_toast = self.parent() or self
        toast_success(
            parent_for_toast,
            title="Скопировано",
            content=self.url_lbl.text(),
        )

    def _on_stop_clicked(self):
        self.stop_server_requested = True
        self.reject()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            # Закрытие по Escape оставляет сервер активным в фоне
            self.accept()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        # Гарантированный сброс любых залипших курсоров
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        super().closeEvent(event)

    def reject(self):
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        super().reject()

    def accept(self):
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        super().accept()
