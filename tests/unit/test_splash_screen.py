"""
Unit-тесты для модуля заставки NeonRoute (ui/widgets/splash_screen.py).
Проверяют работу SmoothSplashScreen (in-process режим), фабрику launch_isolated_splash,
прокачку событий import_pulse и изолированный жизненный цикл.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

import config
from ui.widgets.splash_screen import (
    SmoothSplashScreen,
    IsolatedSplashScreen,
    launch_isolated_splash,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_app = QApplication.instance() or QApplication([])


def test_in_process_smooth_splash_screen():
    splash_path = config.SPLASH_PATH
    if not splash_path.exists():
        pytest.skip("splash.gif отсутствует в assets")

    splash = SmoothSplashScreen(splash_path)
    try:
        assert splash.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert splash.windowFlags() & Qt.WindowType.SplashScreen
        assert splash.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Проверка инициализации QMovie
        assert splash.movie is not None
        assert Path(splash.movie.fileName()).resolve() == splash_path.resolve()

        # Проверка хука импорта
        splash.attach_import_pulse()
        assert splash._import_hook is not None
        splash.pump_animation()
        splash.detach_import_pulse()
        assert splash._import_hook is None
    finally:
        splash.fade_out_and_close(duration_ms=0)


def test_launch_isolated_splash_factory():
    splash_path = config.SPLASH_PATH
    if not splash_path.exists():
        pytest.skip("splash.gif отсутствует в assets")

    # В тестовом окружении фабрика возвращает in-process SmoothSplashScreen
    splash = launch_isolated_splash(splash_path)
    try:
        assert hasattr(splash, "fade_out_and_close")
        assert hasattr(splash, "pump_animation")
    finally:
        splash.fade_out_and_close(duration_ms=0)


def test_isolated_splash_screen_subprocess_lifecycle(tmp_path):
    splash_path = config.SPLASH_PATH
    if not splash_path.exists():
        pytest.skip("splash.gif отсутствует в assets")

    # Тестируем запуск изолированного подпроцесса с имитацией production (без PYTEST_CURRENT_TEST)
    with patch.dict(os.environ, {"QT_QPA_PLATFORM": ""}, clear=False):
        isolated = IsolatedSplashScreen(splash_path)
        try:
            assert isolated._process is not None
            assert isolated._process.poll() is None  # Процесс активно работает
            assert isolated._close_flag_file is not None
        finally:
            isolated.fade_out_and_close(duration_ms=50)
            if isolated._process:
                isolated._process.wait(timeout=3)
                assert isolated._process.returncode == 0
