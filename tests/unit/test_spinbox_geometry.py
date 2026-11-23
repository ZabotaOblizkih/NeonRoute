"""
tests/unit/test_spinbox_geometry.py
===================================
Модульные тесты для проверки синхронизации геометрии внутреннего QLineEdit (qt_spinbox_lineedit)
в спинбоксах NeonRoute (SamplingSpinBox, SamplingDoubleSpinBox, FluentSpinBox, FluentDoubleSpinBox,
CompactDoubleSpinBox / CompactSpinBox) при активных таблицах стилей qfluentwidgets и кастомных размерах.

Критерии:
1. lineEdit().width() > 20
2. lineEdit().isVisible() is True
3. Значения ('0,70', '99', '25', '1,25', '1024') не обрезаются
4. Шаги stepUp / stepDown, границы minimum / maximum и отключение wheelEvent
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QEvent, QPoint, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

from ui.common.spinbox_utils import (
    sync_spinbox_lineedit_geometry,
    attach_spinbox_geometry_sync,
    SpinBoxGeometrySyncMixin,
    FluentDoubleSpinBox,
    FluentSpinBox,
    FluentCompactDoubleSpinBox,
    FluentCompactSpinBox,
)
from ui.dialogs.model_sampling_dialog import (
    SamplingDoubleSpinBox as SamplingDoubleSpinBoxDialog,
    SamplingSpinBox as SamplingSpinBoxDialog,
    ModelSamplingDialog,
)
from ui.models_manager.widgets import (
    SamplingDoubleSpinBox as SamplingDoubleSpinBoxMM,
    SamplingSpinBox as SamplingSpinBoxMM,
)
from ui.models_manager.adetailer_dialog import ADetailerSettingsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.mark.parametrize("width", [80, 82, 90, 93, 105, 120, 180])
def test_sampling_double_spinbox_dialog_geometry(qapp, width):
    """Проверяет, что SamplingDoubleSpinBox из dialogs при любых ширинах сохраняет lineEdit > 20 px."""
    spin = SamplingDoubleSpinBoxDialog()
    spin.setRange(0.0, 2.0)
    spin.setValue(0.70)
    spin.resize(width, 33)
    spin.show()
    qapp.processEvents()

    le = spin.lineEdit()
    assert le is not None
    assert le.isVisible() is True
    assert le.width() > 20, f"Ширина lineEdit ({le.width()} px) <= 20 px при ширине спинбокса {width} px"
    assert "0,70" in le.text() or "0.70" in le.text() or "0.7" in le.text()
    spin.deleteLater()


@pytest.mark.parametrize("width", [80, 82, 90, 93, 105, 120, 180])
def test_sampling_spinbox_dialog_geometry(qapp, width):
    """Проверяет, что SamplingSpinBox из dialogs при любых ширинах сохраняет lineEdit > 20 px."""
    spin = SamplingSpinBoxDialog()
    spin.setRange(256, 4096)
    spin.setValue(1024)
    spin.resize(width, 33)
    spin.show()
    qapp.processEvents()

    le = spin.lineEdit()
    assert le is not None
    assert le.isVisible() is True
    assert le.width() > 20, f"Ширина lineEdit ({le.width()} px) <= 20 px при ширине спинбокса {width} px"
    assert le.text() == "1024"
    spin.deleteLater()


def test_models_manager_sampling_spinboxes_geometry_at_fixed_width(qapp):
    """Проверяет компактные спинбоксы ModelsManager при фиксированной ширине 82 px."""
    spin_double = SamplingDoubleSpinBoxMM()
    spin_double.setRange(1.0, 15.0)
    spin_double.setValue(6.0)
    spin_double.setFixedWidth(82)
    spin_double.show()
    qapp.processEvents()

    le_double = spin_double.lineEdit()
    assert le_double.isVisible() is True
    assert le_double.width() > 20, f"DoubleSpinBox lineEdit width {le_double.width()} <= 20"
    assert "6" in le_double.text()

    spin_int = SamplingSpinBoxMM()
    spin_int.setRange(5, 60)
    spin_int.setValue(25)
    spin_int.setFixedWidth(82)
    spin_int.show()
    qapp.processEvents()

    le_int = spin_int.lineEdit()
    assert le_int.isVisible() is True
    assert le_int.width() > 20, f"SpinBox lineEdit width {le_int.width()} <= 20"
    assert le_int.text() == "25"

    spin_double.deleteLater()
    spin_int.deleteLater()


def test_fluent_spinbox_classes_geometry_and_styles(qapp):
    """Проверяет базовые классы FluentDoubleSpinBox и FluentSpinBox."""
    s_double = FluentDoubleSpinBox()
    s_double.setValue(1.25)
    s_double.setFixedWidth(90)
    s_double.show()
    qapp.processEvents()

    assert s_double.lineEdit().isVisible() is True
    assert s_double.lineEdit().width() > 20
    assert "1,25" in s_double.lineEdit().text() or "1.25" in s_double.lineEdit().text()

    s_int = FluentSpinBox()
    s_int.setValue(99)
    s_int.setFixedWidth(90)
    s_int.show()
    qapp.processEvents()

    assert s_int.lineEdit().isVisible() is True
    assert s_int.lineEdit().width() > 20
    assert s_int.lineEdit().text() == "99"

    s_double.deleteLater()
    s_int.deleteLater()


def test_attach_spinbox_geometry_sync(qapp):
    """Проверяет подключение attach_spinbox_geometry_sync к стандартному спинбоксу qfluentwidgets."""
    from qfluentwidgets import DoubleSpinBox as RawDoubleSpinBox

    raw = RawDoubleSpinBox()
    raw.setFixedWidth(90)
    filter_obj = attach_spinbox_geometry_sync(raw)
    raw.show()
    qapp.processEvents()

    assert filter_obj is not None
    assert raw.lineEdit().isVisible() is True
    assert raw.lineEdit().width() > 20
    raw.deleteLater()


def test_step_up_step_down_and_wheel_event(qapp):
    """Проверяет сохранение шагов stepUp/stepDown, границ и блокировку wheelEvent."""
    spin = SamplingDoubleSpinBoxDialog()
    spin.setRange(0.10, 2.00)
    spin.setSingleStep(0.05)
    spin.setDecimals(2)
    spin.setValue(0.70)
    spin.show()
    qapp.processEvents()

    # StepUp
    spin.stepUp()
    assert spin.value() == 0.75

    # StepDown
    spin.stepDown()
    assert spin.value() == 0.70

    # Проверка нижней границы
    spin.setValue(0.10)
    spin.stepDown()
    assert spin.value() == 0.10

    # Проверка wheelEvent (должен игнорироваться)
    val_before = spin.value()
    wheel_ev = QWheelEvent(
        QPointF(10.0, 10.0),
        QPointF(10.0, 10.0),
        QPoint(0, 120),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False
    )
    spin.wheelEvent(wheel_ev)
    assert spin.value() == val_before
    spin.deleteLater()


def test_model_sampling_dialog_all_spinboxes_geometry(qapp):
    """Проверяет все 8 спинбоксов внутри реального ModelSamplingDialog."""
    params = {
        "temperature": 0.70,
        "min_p": 0.05,
        "top_p": 0.95,
        "repeat_penalty": 1.05,
        "dry_multiplier": 0.80,
        "xtc_probability": 0.20,
        "xtc_threshold": 0.10,
        "max_tokens": 1024,
    }
    dlg = ModelSamplingDialog(
        current_params=params,
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=True
    )
    dlg.show()
    qapp.processEvents()

    spinboxes = [
        dlg.spin_temp,
        dlg.spin_min_p,
        dlg.spin_top_p,
        dlg.spin_rep_pen,
        dlg.spin_dry,
        dlg.spin_xtc_prob,
        dlg.spin_xtc_thresh,
        dlg.spin_max_tokens,
    ]

    for spin in spinboxes:
        le = spin.lineEdit()
        assert le is not None
        assert le.isVisible() is True
        assert le.width() > 20, f"Spinbox {spin} lineEdit width {le.width()} <= 20"
        assert len(le.text().strip()) > 0, f"Spinbox {spin} lineEdit text is empty"

    dlg.deleteLater()


def test_adetailer_dialog_all_spinboxes_geometry(qapp):
    """Проверяет все 6 спинбоксов внутри ADetailerSettingsDialog."""
    dlg = ADetailerSettingsDialog(current_pipeline_params={}, is_xl=True, parent=None)
    dlg.show()
    qapp.processEvents()

    spinboxes = [
        dlg.spin_face_conf,
        dlg.spin_face_denoise,
        dlg.spin_face_inpaint,
        dlg.spin_hands_conf,
        dlg.spin_hands_denoise,
        dlg.spin_hands_inpaint,
    ]

    for spin in spinboxes:
        le = spin.lineEdit()
        assert le is not None
        assert le.isVisible() is True
        assert le.width() > 20, f"ADetailer spinbox {spin} lineEdit width {le.width()} <= 20"
        assert len(le.text().strip()) > 0

    dlg.deleteLater()
