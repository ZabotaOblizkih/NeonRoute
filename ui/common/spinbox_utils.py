"""
ui/common/spinbox_utils.py
==========================
Единый переиспользуемый модуль синхронизации геометрии внутреннего QLineEdit
(qt_spinbox_lineedit) для спинбоксов QFluentWidgets и Qt.

Предотвращает сжатие текстового поля ввода до 1 px при активных таблицах стилей
qfluentwidgets (FluentStyleSheet.SPIN_BOX, кастомные QSS-паддинги и компактные размеры),
гарантируя, что lineEdit().width() > 20 и lineEdit().isVisible() is True.
"""

from PyQt6.QtCore import Qt, QRect, QEvent, QObject
from PyQt6.QtWidgets import QAbstractSpinBox
from qfluentwidgets import DoubleSpinBox, SpinBox, CompactDoubleSpinBox, CompactSpinBox


def sync_spinbox_lineedit_geometry(spinbox: QAbstractSpinBox) -> None:
    """
    Синхронизирует геометрию внутреннего QLineEdit (qt_spinbox_lineedit)
    с учётом ширины кнопок управления/отступов, границ и паддингов.

    Гарантирует:
    1. lineEdit().width() > 20
    2. lineEdit().isVisible() is True
    3. Отсутствие перекрытия между текстом и кнопками инкремента/декремента.
    """
    if not isinstance(spinbox, QAbstractSpinBox):
        return

    le = spinbox.lineEdit()
    if not le:
        return

    w = spinbox.width()
    h = spinbox.height()
    if w <= 0 or h <= 0:
        return

    left = 8
    top = 2
    edit_h = max(16, h - 2 * top)
    right_limit = w - 8

    # 1. Сценарий CompactSpinBoxBase (одна компактная кнопка compactSpinButton справа)
    if hasattr(spinbox, "compactSpinButton") and spinbox.compactSpinButton:
        btn = spinbox.compactSpinButton
        if btn.isVisible():
            btn_w = btn.width() if btn.width() > 0 else (btn.sizeHint().width() if btn.sizeHint().isValid() else 26)
            btn_x = btn.x() if (0 < btn.x() < w) else (w - btn_w)
            right_limit = min(right_limit, btn_x - 2)

    # 2. Сценарий InlineSpinBoxBase (две кнопки upButton и downButton в hBoxLayout справа)
    elif hasattr(spinbox, "upButton") and hasattr(spinbox, "downButton"):
        up = spinbox.upButton
        dn = spinbox.downButton
        up_vis = bool(up and up.isVisible())
        dn_vis = bool(dn and dn.isVisible())
        if up_vis or dn_vis:
            up_w = up.width() if (up and up.width() > 0) else 31
            dn_w = dn.width() if (dn and dn.width() > 0) else 31
            spacing = spinbox.hBoxLayout.spacing() if hasattr(spinbox, "hBoxLayout") else 5
            margin_r = spinbox.hBoxLayout.contentsMargins().right() if hasattr(spinbox, "hBoxLayout") else 4
            expected_x = w - (up_w + dn_w + spacing + margin_r)
            up_x = up.x() if (up and 0 < up.x() < w) else expected_x
            dn_x = dn.x() if (dn and 0 < dn.x() < w) else (expected_x + up_w + spacing)
            btn_x = min(up_x, dn_x)
            right_limit = min(right_limit, btn_x - 2)

    avail_w = max(24, right_limit - left)
    edit_w = min(avail_w, max(24, w - left - 2))

    le.setGeometry(QRect(left, top, edit_w, edit_h))
    le.setVisible(True)


class SpinBoxGeometrySyncMixin:
    """
    Миксин синхронизации геометрии внутреннего QLineEdit для спинбоксов.
    Обновляет геометрию при resizeEvent, showEvent и смене стилей/шрифтов.
    """

    def resizeEvent(self, event):
        super().resizeEvent(event)
        sync_spinbox_lineedit_geometry(self)

    def showEvent(self, event):
        super().showEvent(event)
        sync_spinbox_lineedit_geometry(self)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange, QEvent.Type.PaletteChange):
            sync_spinbox_lineedit_geometry(self)


class SpinBoxGeometryEventFilter(QObject):
    """
    EventFilter для подключения синхронизации геометрии к уже существующим виджетам.
    """

    def __init__(self, spinbox: QAbstractSpinBox, parent: QObject = None):
        super().__init__(parent or spinbox)
        self.spinbox = spinbox

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.StyleChange, QEvent.Type.FontChange):
            sync_spinbox_lineedit_geometry(self.spinbox)
        elif watched is self.spinbox.lineEdit() and event.type() == QEvent.Type.Resize:
            if watched.width() <= 20:
                sync_spinbox_lineedit_geometry(self.spinbox)
        return False


def attach_spinbox_geometry_sync(spinbox: QAbstractSpinBox) -> SpinBoxGeometryEventFilter:
    """
    Подключает автоматическую синхронизацию геометрии к произвольному спинбоксу.
    """
    filter_obj = SpinBoxGeometryEventFilter(spinbox)
    spinbox.installEventFilter(filter_obj)
    le = spinbox.lineEdit()
    if le:
        le.installEventFilter(filter_obj)
    # Сохраняем ссылку на объекте во избежание сборки мусора
    spinbox._geom_sync_filter = filter_obj
    sync_spinbox_lineedit_geometry(spinbox)
    return filter_obj


class SamplingDoubleSpinBoxMixin(SpinBoxGeometrySyncMixin):
    """
    Базовая логика для числовых счетчиков сэмплинга с плавающей точкой:
    - ClickFocus
    - Отключение wheelEvent (защита от случайного скролла)
    - Точный шаг и гарантированное округление до decimals() с соблюдением границ minimum/maximum
    - Синхронизация геометрии lineEdit()
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._rebind_step_buttons()

    def _rebind_step_buttons(self):
        if hasattr(self, "downButton") and hasattr(self.downButton, "clicked"):
            try:
                self.downButton.clicked.disconnect()
            except Exception:
                pass
            self.downButton.clicked.connect(self.stepDown)
        if hasattr(self, "upButton") and hasattr(self.upButton, "clicked"):
            try:
                self.upButton.clicked.disconnect()
            except Exception:
                pass
            self.upButton.clicked.connect(self.stepUp)

    def stepDown(self):
        step = self.singleStep()
        decimals = getattr(self, "decimals", lambda: 2)()
        new_val = max(self.minimum(), self.value() - step)
        self.setValue(round(new_val, decimals))

    def stepUp(self):
        step = self.singleStep()
        decimals = getattr(self, "decimals", lambda: 2)()
        new_val = min(self.maximum(), self.value() + step)
        self.setValue(round(new_val, decimals))

    def wheelEvent(self, event):
        event.ignore()


class SamplingSpinBoxMixin(SpinBoxGeometrySyncMixin):
    """
    Базовая логика для целочисленных счетчиков сэмплинга:
    - ClickFocus
    - Отключение wheelEvent (защита от случайного скролла)
    - Соблюдение границ minimum/maximum и шага singleStep
    - Синхронизация геометрии lineEdit()
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._rebind_step_buttons()

    def _rebind_step_buttons(self):
        if hasattr(self, "downButton") and hasattr(self.downButton, "clicked"):
            try:
                self.downButton.clicked.disconnect()
            except Exception:
                pass
            self.downButton.clicked.connect(self.stepDown)
        if hasattr(self, "upButton") and hasattr(self.upButton, "clicked"):
            try:
                self.upButton.clicked.disconnect()
            except Exception:
                pass
            self.upButton.clicked.connect(self.stepUp)

    def stepDown(self):
        step = self.singleStep()
        new_val = max(self.minimum(), self.value() - step)
        self.setValue(int(new_val))

    def stepUp(self):
        step = self.singleStep()
        new_val = min(self.maximum(), self.value() + step)
        self.setValue(int(new_val))

    def wheelEvent(self, event):
        event.ignore()


# Синхронизированные версии стандартных спинбоксов
class FluentDoubleSpinBox(SpinBoxGeometrySyncMixin, DoubleSpinBox):
    """DoubleSpinBox с синхронизацией геометрии lineEdit."""
    pass


class FluentSpinBox(SpinBoxGeometrySyncMixin, SpinBox):
    """SpinBox с синхронизацией геометрии lineEdit."""
    pass


class FluentCompactDoubleSpinBox(SpinBoxGeometrySyncMixin, CompactDoubleSpinBox):
    """CompactDoubleSpinBox с синхронизацией геометрии lineEdit."""
    pass


class FluentCompactSpinBox(SpinBoxGeometrySyncMixin, CompactSpinBox):
    """CompactSpinBox с синхронизацией геометрии lineEdit."""
    pass


# Специализированные классы спинбоксов сэмплинга
class SamplingDoubleSpinBox(SamplingDoubleSpinBoxMixin, DoubleSpinBox):
    """Поле числового ввода с плавающей точкой для диалогов сэмплинга."""
    pass


class SamplingSpinBox(SamplingSpinBoxMixin, SpinBox):
    """Поле целочисленного ввода для диалогов сэмплинга."""
    pass


class CompactSamplingDoubleSpinBox(SamplingDoubleSpinBoxMixin, CompactDoubleSpinBox):
    """Компактное поле числового ввода с плавающей точкой."""
    pass


class CompactSamplingSpinBox(SamplingSpinBoxMixin, CompactSpinBox):
    """Компактное поле целочисленного ввода."""
    pass
