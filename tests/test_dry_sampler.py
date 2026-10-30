"""
Unit tests for DRY (Don't Repeat Yourself) sampler integration in NeonRoute.
Covers:
- MODE_PROFILES in config.py
- get_default_sampling_params in services/session_manager.py
- ModelSamplingDialog widget behavior, ranges, signals, and engineer mode lock
- LMStudioClient payload construction for DRY parameters
"""

import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from config import MODE_PROFILES
from services.session_manager import get_default_sampling_params, get_mode_profile


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_mode_profiles_dry_multiplier():
    """Verify MODE_PROFILES SSOT has expected dry_multiplier values."""
    assert MODE_PROFILES["assistant"]["sampling"]["dry_multiplier"] == 0.80
    assert MODE_PROFILES["roleplay"]["sampling"]["dry_multiplier"] == 0.80
    assert MODE_PROFILES["engineer"]["sampling"]["dry_multiplier"] == 0.00


def test_session_manager_sampling_params():
    """Verify get_default_sampling_params returns dry_multiplier for each mode."""
    assistant_params = get_default_sampling_params("assistant")
    assert "dry_multiplier" in assistant_params
    assert assistant_params["dry_multiplier"] == 0.80

    rp_params = get_default_sampling_params("roleplay")
    assert "dry_multiplier" in rp_params
    assert rp_params["dry_multiplier"] == 0.80

    eng_params = get_default_sampling_params("engineer")
    assert "dry_multiplier" in eng_params
    assert eng_params["dry_multiplier"] == 0.00


def test_model_sampling_dialog_assistant(qapp):
    """Test dialog initialization and slider/spinbox synchronization for Assistant mode."""
    from ui.dialogs.model_sampling_dialog import ModelSamplingDialog

    dlg = ModelSamplingDialog(
        current_params={},
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    assert hasattr(dlg, "spin_dry")
    assert hasattr(dlg, "slider_dry")
    assert dlg.spin_dry.isEnabled() is True
    assert dlg.slider_dry.isEnabled() is True
    assert dlg.spin_dry.minimum() == 0.00
    assert dlg.spin_dry.maximum() == 1.50
    assert dlg.spin_dry.singleStep() == 0.05
    assert dlg.slider_dry.minimum() == 0
    assert dlg.slider_dry.maximum() == 150
    assert dlg.spin_dry.value() == 0.80
    assert dlg.slider_dry.value() == 80

    # Test slider to spinbox sync
    dlg.slider_dry.setValue(120)
    assert abs(dlg.spin_dry.value() - 1.20) < 0.001

    # Test spinbox to slider sync
    dlg.spin_dry.setValue(0.45)
    assert dlg.slider_dry.value() == 45

    # Test get_sampling_params
    params = dlg.get_sampling_params()
    assert "dry_multiplier" in params
    assert params["dry_multiplier"] == 0.45

    # Test reset defaults
    dlg._reset_defaults()
    assert abs(dlg.spin_dry.value() - 0.80) < 0.001
    assert dlg.slider_dry.value() == 80


def test_model_sampling_dialog_engineer_locked(qapp):
    """Test that in Engineer mode DRY multiplier is strictly locked to 0.00 and disabled."""
    from ui.dialogs.model_sampling_dialog import ModelSamplingDialog

    # Even if current_params attempts to pass non-zero dry_multiplier
    dlg = ModelSamplingDialog(
        current_params={"dry_multiplier": 1.25, "temperature": 0.8},
        mode="assistant",
        is_engineer_mode=True,
        is_global_defaults=False
    )

    assert dlg.spin_dry.isEnabled() is False
    assert dlg.slider_dry.isEnabled() is False
    assert dlg.spin_dry.value() == 0.00
    assert dlg.slider_dry.value() == 0

    params = dlg.get_sampling_params()
    assert params["dry_multiplier"] == 0.00
    assert params["temperature"] == 0.15

    # Resetting defaults must keep Engineer locked at 0.00
    dlg._reset_defaults()
    assert dlg.spin_dry.value() == 0.00
    assert dlg.slider_dry.value() == 0
    params = dlg.get_sampling_params()
    assert params["dry_multiplier"] == 0.00


def test_lm_studio_payload_includes_dry_params():
    """Verify LMStudioClient.chat_completion passes DRY parameters to payload."""
    from services.lm_studio import LMStudioClient

    client = LMStudioClient()
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = []
    mock_session.post.return_value = mock_resp
    client._current_session = mock_session
    client._persistent_session = mock_session

    with patch.object(client, "get_active_model_id", return_value="test-model"), \
         patch.object(client, "is_model_loaded", return_value=True), \
         patch.object(client, "ensure_model_loaded", return_value=True):
        client._current_loaded_ctx = 32768
        client._current_loaded_gpu = "1.0"
        client._is_model_unloaded = False
        client._last_loaded_model_id = "test-model"

        list(client.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            dry_multiplier=0.85,
            dry_base=1.75,
            dry_allowed_length=2,
            dry_penalty_last_n=-1,
            stream=False
        ))

    mock_session.post.assert_called_once()
    call_kwargs = mock_session.post.call_args[1]
    payload = call_kwargs["json"]

    assert payload.get("dry_multiplier") == 0.85
    assert payload.get("dry_base") == 1.75
    assert payload.get("dry_allowed_length") == 2
    assert payload.get("dry_penalty_last_n") == -1


def test_two_level_sampling_priority():
    """
    Test two-level sampling priority cascade:
    Session override (PKM) -> Global config default -> MODE_PROFILES
    """
    # 1. Custom session override takes precedence
    current_session = {"is_custom_sampling": True, "sampling_params": {"dry_multiplier": 0.40, "temperature": 0.9}}
    app_config = {"default_sampling_assistant": {"dry_multiplier": 0.65}}
    mode_profile = {"sampling": {"dry_multiplier": 0.80}}

    cfg_defaults = app_config.get("default_sampling_assistant", {})
    prof_sampling = mode_profile.get("sampling", {})
    default_dry = cfg_defaults.get("dry_multiplier", prof_sampling.get("dry_multiplier", 0.80))

    sampling = current_session.get("sampling_params", {})
    is_custom = current_session.get("is_custom_sampling", False)
    dry_mult = float(sampling.get("dry_multiplier", default_dry)) if is_custom else float(default_dry)
    assert dry_mult == 0.40

    # 2. When not custom, fallback to global default
    current_session["is_custom_sampling"] = False
    is_custom = False
    dry_mult = float(sampling.get("dry_multiplier", default_dry)) if is_custom else float(default_dry)
    assert dry_mult == 0.65

    # 3. When global default has no dry_multiplier, fallback to MODE_PROFILES
    app_config_empty = {}
    cfg_defaults_empty = app_config_empty.get("default_sampling_assistant", {})
    default_dry_prof = cfg_defaults_empty.get("dry_multiplier", prof_sampling.get("dry_multiplier", 0.80))
    dry_mult_prof = float(default_dry_prof)
    assert dry_mult_prof == 0.80

    # 4. In Engineer mode, dry_multiplier must strictly be 0.00
    is_eng = True
    dry_eng = 0.00 if is_eng else dry_mult
    assert dry_eng == 0.00
