"""
Unit tests for ModelSamplingDialog in NeonRoute.
Verifies Windows 11 Fluent Design refactoring, adaptive palette, slider/spinbox signals synchronization,
removal of DM mode button, and mode-specific behavior (Engineer mode lock, defaults reset).
"""

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
from PyQt6.QtWidgets import QApplication

from ui.dialogs.model_sampling_dialog import (
    ModelSamplingDialog,
    SamplingSlider,
    SamplingDoubleSpinBox,
    SamplingSpinBox,
)


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def sample_params():
    return {
        "temperature": 0.70,
        "min_p": 0.05,
        "top_p": 0.95,
        "repeat_penalty": 1.05,
        "dry_multiplier": 0.80,
        "xtc_probability": 0.20,
        "xtc_threshold": 0.10,
        "max_tokens": 1024,
    }


def test_model_sampling_dialog_initialization(qapp, sample_params):
    """Проверяет создание диалога, заголовок, FluentTitleBar и отсутствие чекбокса DM режима."""
    dlg = ModelSamplingDialog(
        current_params=sample_params,
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=True
    )
    dlg.show()

    assert "Глобальные параметры по умолчанию" in dlg.windowTitle()
    assert dlg.isModal() is True
    assert dlg.titleBar is not None
    assert dlg.titleBar.height() == 46

    # Проверка, что min/max кнопки скрыты, а closeBtn видна
    if hasattr(dlg.titleBar, "minBtn") and dlg.titleBar.minBtn:
        assert dlg.titleBar.minBtn.isHidden()
    if hasattr(dlg.titleBar, "maxBtn") and dlg.titleBar.maxBtn:
        assert dlg.titleBar.maxBtn.isHidden()
    if hasattr(dlg.titleBar, "closeBtn") and dlg.titleBar.closeBtn:
        assert dlg.titleBar.closeBtn.isVisible()

    # Проверяем, что кнопка/чекбокс "Режим гейммастер" аккуратно убрана из диалога
    assert not hasattr(dlg, "chk_dm_mode")

    # Проверяем наличие всех 8 регуляторов параметров сэмплинга
    assert hasattr(dlg, "spin_temp") and hasattr(dlg, "slider_temp")
    assert hasattr(dlg, "spin_min_p") and hasattr(dlg, "slider_min_p")
    assert hasattr(dlg, "spin_top_p") and hasattr(dlg, "slider_top_p")
    assert hasattr(dlg, "spin_rep_pen") and hasattr(dlg, "slider_rep_pen")
    assert hasattr(dlg, "spin_dry") and hasattr(dlg, "slider_dry")
    assert hasattr(dlg, "spin_xtc_prob") and hasattr(dlg, "slider_xtc_prob")
    assert hasattr(dlg, "spin_xtc_thresh") and hasattr(dlg, "slider_xtc_thresh")
    assert hasattr(dlg, "spin_max_tokens") and hasattr(dlg, "slider_max_tokens")

    dlg.deleteLater()


def test_model_sampling_dialog_signals_synchronization(qapp, sample_params):
    """Проверяет двустороннюю синхронизацию слайдеров и спинбоксов без потери точности."""
    dlg = ModelSamplingDialog(
        current_params=sample_params,
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    # 1. Temperature: слайдер -> спинбокс и спинбокс -> слайдер
    dlg.slider_temp.setValue(125)
    assert abs(dlg.spin_temp.value() - 1.25) < 0.001

    dlg.spin_temp.setValue(0.85)
    assert dlg.slider_temp.value() == 85

    # 2. Min-P: слайдер -> спинбокс и спинбокс -> слайдер
    dlg.slider_min_p.setValue(8)
    assert abs(dlg.spin_min_p.value() - 0.08) < 0.001

    dlg.spin_min_p.setValue(0.15)
    assert dlg.slider_min_p.value() == 15

    # 3. Top-P
    dlg.slider_top_p.setValue(90)
    assert abs(dlg.spin_top_p.value() - 0.90) < 0.001

    dlg.spin_top_p.setValue(0.75)
    assert dlg.slider_top_p.value() == 75

    # 4. Repetition Penalty
    dlg.slider_rep_pen.setValue(118)
    assert abs(dlg.spin_rep_pen.value() - 1.18) < 0.001

    dlg.spin_rep_pen.setValue(1.10)
    assert dlg.slider_rep_pen.value() == 110

    # 5. DRY Multiplier
    dlg.slider_dry.setValue(65)
    assert abs(dlg.spin_dry.value() - 0.65) < 0.001

    dlg.spin_dry.setValue(1.20)
    assert dlg.slider_dry.value() == 120

    # 6. XTC Probability
    dlg.slider_xtc_prob.setValue(45)
    assert abs(dlg.spin_xtc_prob.value() - 0.45) < 0.001

    dlg.spin_xtc_prob.setValue(0.35)
    assert dlg.slider_xtc_prob.value() == 35

    # 7. XTC Threshold
    dlg.slider_xtc_thresh.setValue(22)
    assert abs(dlg.spin_xtc_thresh.value() - 0.22) < 0.001

    dlg.spin_xtc_thresh.setValue(0.08)
    assert dlg.slider_xtc_thresh.value() == 8

    # 8. Max Tokens (снаппинг к 64)
    dlg.slider_max_tokens.setValue(2048)
    assert dlg.spin_max_tokens.value() == 2048

    dlg.spin_max_tokens.setValue(1536)
    assert dlg.slider_max_tokens.value() == 1536

    dlg.deleteLater()


def test_model_sampling_dialog_engineer_mode_locks(qapp, sample_params):
    """Проверяет жесткую фиксацию DRY и XTC на 0.00 в режиме Инженера при открытой температуре."""
    dlg = ModelSamplingDialog(
        current_params=sample_params,
        mode="engineer",
        is_engineer_mode=True,
        is_global_defaults=False
    )

    # Температура должна быть разблокирована
    assert dlg.spin_temp.isEnabled() is True
    assert dlg.slider_temp.isEnabled() is True

    # DRY и XTC probability должны быть заблокированы на 0.00
    assert dlg.spin_dry.value() == 0.00
    assert dlg.spin_dry.isEnabled() is False
    assert dlg.slider_dry.isEnabled() is False

    assert dlg.spin_xtc_prob.value() == 0.00
    assert dlg.spin_xtc_prob.isEnabled() is False
    assert dlg.slider_xtc_prob.isEnabled() is False

    # get_sampling_params гарантированно возвращает 0.00 для DRY и XTC
    res = dlg.get_sampling_params()
    assert res["dry_multiplier"] == 0.00
    assert res["xtc_probability"] == 0.00

    dlg.deleteLater()


def test_model_sampling_dialog_get_params_and_dm_mode_preservation(qapp):
    """Проверяет корректность возвращаемого словаря параметров и сохранение is_dm_mode."""
    params_with_dm = {
        "temperature": 1.10,
        "min_p": 0.05,
        "top_p": 0.95,
        "repeat_penalty": 1.02,
        "dry_multiplier": 0.80,
        "xtc_probability": 0.30,
        "xtc_threshold": 0.10,
        "max_tokens": 4096,
        "is_dm_mode": True,
    }

    dlg = ModelSamplingDialog(
        current_params=params_with_dm,
        mode="roleplay",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    out = dlg.get_sampling_params()
    assert out["temperature"] == 1.10
    assert out["min_p"] == 0.05
    assert out["top_p"] == 0.95
    assert out["repeat_penalty"] == 1.02
    assert out["dry_multiplier"] == 0.80
    assert out["xtc_probability"] == 0.30
    assert out["xtc_threshold"] == 0.10
    assert out["max_tokens"] == 4096
    # Флаг аккуратно сохранен, хотя кнопки в GUI больше нет
    assert out["is_dm_mode"] is True

    dlg.deleteLater()


def test_model_sampling_dialog_reset_defaults(qapp, sample_params):
    """Проверяет сброс значений к значениям по умолчанию для текущего режима."""
    dlg = ModelSamplingDialog(
        current_params=sample_params,
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    # Меняем параметры
    dlg.spin_temp.setValue(1.80)
    dlg.spin_min_p.setValue(0.18)

    # Нажимаем сброс
    dlg.btn_reset.clicked.emit()
    assert dlg.was_reset_to_defaults is True

    # Для ассистента дефолты: temp 0.70, min_p 0.05
    assert abs(dlg.spin_temp.value() - 0.70) < 0.001
    assert abs(dlg.spin_min_p.value() - 0.05) < 0.001

    dlg.deleteLater()


def test_model_sampling_dialog_adaptive_palette_from_wallpaper(qapp, sample_params):
    """Проверяет адаптацию цветовой палитры под тон обоев."""
    dlg = ModelSamplingDialog(
        current_params=sample_params,
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    # Создаем тестовую картинку обоев (бирюзовый цвет)
    pix = QPixmap(100, 100)
    pix.fill(QColor(0, 240, 255))
    dlg._resolve_pixmap = lambda: pix

    dlg.update_adaptive_palette()
    assert "ModelSamplingDialog#ModelSamplingDialog" in dlg.styleSheet()
    assert "QFrame.ParamCard" in dlg.styleSheet()
    assert "DoubleSpinBox" in dlg.styleSheet()

    dlg.deleteLater()


def test_model_sampling_dialog_escape_key_closes(qapp, sample_params):
    """Проверяет закрытие диалога по клавише Escape."""
    dlg = ModelSamplingDialog(
        current_params=sample_params,
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    dlg.reject = MagicMock()
    escape_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dlg.keyPressEvent(escape_event)

    dlg.reject.assert_called_once()
    dlg.deleteLater()


def test_dialog_controller_open_model_sampling_dialog_no_name_error(qapp, monkeypatch):
    """Проверяет, что вызов _open_model_sampling_dialog и _open_global_model_sampling_dialog не падает с NameError (QApplication)."""
    from PyQt6.QtWidgets import QMainWindow
    from ui.controllers.dialog_controller import DialogControllerMixin

    class DummyWindow(DialogControllerMixin, QMainWindow):
        def __init__(self):
            super().__init__()
            self.current_session = {
                "id": "sess_1",
                "mode": "assistant",
                "sampling_params": {"temperature": 0.70, "min_p": 0.05, "max_tokens": 1024},
            }
            self.current_mode = "assistant"
            self.app_config = {}

        def is_engineer_mode_active(self):
            return False

        def _save_current_session(self):
            pass

    win = DummyWindow()

    # Мокаем ModelSamplingDialog.exec чтобы окно не висело модально в тесте
    mock_exec = MagicMock(return_value=False)
    monkeypatch.setattr("ui.dialogs.model_sampling_dialog.ModelSamplingDialog.exec", mock_exec)

    # 1. Вызов настроек модели активной сессии (вызываемый по клику ПКМ на аватарку)
    win._open_model_sampling_dialog()
    assert mock_exec.called

    # 2. Вызов глобальных настроек модели
    mock_exec.reset_mock()
    win._open_global_model_sampling_dialog()
    assert mock_exec.called

    win.deleteLater()

