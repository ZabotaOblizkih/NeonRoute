"""
Unit tests for ui/common/toast_helper.py.
Verifies show_toast, toast_success, toast_info, toast_warning, toast_error,
default durations, position, and safe parent resolution.
"""

import pytest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import InfoBarPosition
from ui.common.toast_helper import (
    show_toast,
    toast_success,
    toast_info,
    toast_warning,
    toast_error,
    DEFAULT_DURATIONS,
    DEFAULT_POSITION,
)


def test_toast_helper_shortcuts_dispatch(qapp):
    """Verifies that shortcuts call correct methods on custom InfoBar mock."""
    mock_bar = MagicMock()
    parent = QWidget()

    # Success
    toast_success(parent, "Success Title", "Success Body", info_bar_cls=mock_bar)
    mock_bar.success.assert_called_once()
    _, kwargs = mock_bar.success.call_args
    assert kwargs["title"] == "Success Title"
    assert kwargs["content"] == "Success Body"
    assert kwargs["duration"] == DEFAULT_DURATIONS["success"]
    assert kwargs["position"] == DEFAULT_POSITION
    assert kwargs["parent"] == parent

    # Info
    toast_info(parent, "Info Title", "Info Body", info_bar_cls=mock_bar)
    mock_bar.info.assert_called_once()
    _, kwargs = mock_bar.info.call_args
    assert kwargs["title"] == "Info Title"
    assert kwargs["duration"] == DEFAULT_DURATIONS["info"]

    # Warning
    toast_warning(parent, "Warning Title", "Warning Body", info_bar_cls=mock_bar)
    mock_bar.warning.assert_called_once()
    _, kwargs = mock_bar.warning.call_args
    assert kwargs["title"] == "Warning Title"
    assert kwargs["duration"] == DEFAULT_DURATIONS["warning"]

    # Error
    toast_error(parent, "Error Title", "Error Body", info_bar_cls=mock_bar)
    mock_bar.error.assert_called_once()
    _, kwargs = mock_bar.error.call_args
    assert kwargs["title"] == "Error Title"
    assert kwargs["duration"] == DEFAULT_DURATIONS["error"]


def test_toast_helper_custom_duration_and_position(qapp):
    """Verifies custom duration and position overrides."""
    mock_bar = MagicMock()
    parent = QWidget()

    show_toast(
        parent=parent,
        title="Custom",
        content="Custom Body",
        kind="warning",
        duration=9999,
        position=InfoBarPosition.BOTTOM_RIGHT,
        is_closable=False,
        info_bar_cls=mock_bar,
    )

    mock_bar.warning.assert_called_once()
    _, kwargs = mock_bar.warning.call_args
    assert kwargs["duration"] == 9999
    assert kwargs["position"] == InfoBarPosition.BOTTOM_RIGHT
    assert kwargs["isClosable"] is False


def test_toast_helper_none_parent_fallback(qapp):
    """Verifies that None parent doesn't crash and dispatches cleanly."""
    mock_bar = MagicMock()

    res = show_toast(
        parent=None,
        title="No Parent",
        content="Safe fallback",
        kind="info",
        info_bar_cls=mock_bar,
    )
    mock_bar.info.assert_called_once()
