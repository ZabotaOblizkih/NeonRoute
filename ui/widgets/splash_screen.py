"""
SmoothSplashScreen — лёгкая анимированная заставка для NeonRoute.

Показывается немедленно после QApplication(), до тяжёлого импорта MainWindow.
Поддерживает анимированный GIF через QMovie (гарантированная совместимость без
внешних плагинов). Изолирована от глобального NEON_THEME_QSS через objectName.
Плавно растворяется через windowOpacity после готовности главного окна.
"""

import sys
import time
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PyQt6.QtGui import QMovie, QPainter
from PyQt6.QtWidgets import QApplication, QWidget


class _ImportAnimationPulse:
    """
    Прокачивает цикл событий Qt каждые ~30 мс во время синхронного импорта
    модулей, чтобы анимация GIF не замерзала пока грузится ui.main_window.
    Монтируется в sys.meta_path как PEP-302 finder (возвращает None — не
    перехватывает реальный импорт, только качает event loop).
    """

    def __init__(self, splash: "SmoothSplashScreen") -> None:
        self._splash = splash

    def find_spec(self, fullname, path, target=None):  # noqa: ANN001
        self._splash.pump_animation()
        return None  # pass-through — не мешаем нормальному импорту


class SmoothSplashScreen(QWidget):
    """
    Frameless, translucent animated splash screen.

    Жизненный цикл:
        splash = SmoothSplashScreen(SPLASH_PATH)
        splash.show()
        splash.attach_import_pulse()   # качаем event loop во время import
        app.processEvents()

        # ... тяжёлый импорт и инициализация ...

        window = MainWindow()
        window.showMaximized()
        splash.fade_out_and_close()    # плавное растворение + deleteLater
    """

    def __init__(self, asset_path: Path) -> None:
        super().__init__()
        self._closing = False
        self._pumping = False
        self._last_pump = 0.0
        self._anim: QPropertyAnimation | None = None
        self._import_hook: _ImportAnimationPulse | None = None

        # Имя для CSS-изоляции от глобального NEON_THEME_QSS
        self.setObjectName("SmoothSplashScreen")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # Перебиваем глобальный QWidget { background-color: #0A0D14; } из NEON_THEME_QSS
        self.setStyleSheet(
            "#SmoothSplashScreen { background: transparent; border: none; }"
        )

        self.movie = QMovie(str(asset_path), parent=self)
        self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self.movie.frameChanged.connect(self._on_frame_changed)
        self.movie.start()

        # Задаём размер виджета по первому кадру
        first_frame = self.movie.currentPixmap()
        if not first_frame.isNull():
            self.setFixedSize(first_frame.size())

        self._center_on_screen()

    # ------------------------------------------------------------------
    # Позиционирование
    # ------------------------------------------------------------------

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()  # device-independent px (PassThrough HiDPI)
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

    # ------------------------------------------------------------------
    # Анимация кадров
    # ------------------------------------------------------------------

    def _on_frame_changed(self) -> None:
        pixmap = self.movie.currentPixmap()
        if not pixmap.isNull() and self.size() != pixmap.size():
            self.setFixedSize(pixmap.size())
            self._center_on_screen()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        pixmap = self.movie.currentPixmap()
        if pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, pixmap)

    # ------------------------------------------------------------------
    # Import pulse — поддержание 30 FPS во время синхронного импорта
    # ------------------------------------------------------------------

    def attach_import_pulse(self) -> None:
        """Вставляет fake-finder в sys.meta_path для прокачки event loop."""
        if self._import_hook is None:
            self._import_hook = _ImportAnimationPulse(self)
            sys.meta_path.insert(0, self._import_hook)

    def detach_import_pulse(self) -> None:
        """Убирает fake-finder из sys.meta_path."""
        if self._import_hook is not None:
            try:
                sys.meta_path.remove(self._import_hook)
            except ValueError:
                pass
            self._import_hook = None

    def pump_animation(self) -> None:
        """Вызывается из _ImportAnimationPulse при каждом import-событии."""
        if self._pumping or self._closing:
            return
        now = time.monotonic()
        if now - self._last_pump >= 0.03:   # не чаще 30 FPS
            self._last_pump = now
            self._pumping = True
            try:
                QApplication.processEvents()
            finally:
                self._pumping = False

    # ------------------------------------------------------------------
    # Завершение
    # ------------------------------------------------------------------

    def fade_out_and_close(self, duration_ms: int = 450) -> None:
        """Плавное растворение windowOpacity 1.0 → 0.0, затем close + deleteLater."""
        if self._closing:
            return
        self._closing = True
        self.detach_import_pulse()

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(duration_ms)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._cleanup)
        self._anim.start()

    def _cleanup(self) -> None:
        self.movie.stop()
        self.close()
        self.deleteLater()
