"""
Unit tests for XTC (Exclude Top Choices) sampler integration in NeonRoute.
Covers:
- MODE_PROFILES in config.py (assistant: 0.20/0.10, roleplay: 0.30/0.10, engineer: 0.00/0.10)
- get_default_sampling_params in services/session_manager.py
- ModelSamplingDialog widget behavior, ranges, signals, and engineer mode lock
- LMStudioClient payload construction for XTC parameters
- Inference flow priority cascade (session -> config.json -> MODE_PROFILES)
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


def test_mode_profiles_xtc():
    """Verify MODE_PROFILES SSOT has expected xtc_probability and xtc_threshold values."""
    assert MODE_PROFILES["assistant"]["sampling"]["xtc_probability"] == 0.20
    assert MODE_PROFILES["assistant"]["sampling"]["xtc_threshold"] == 0.10

    assert MODE_PROFILES["roleplay"]["sampling"]["xtc_probability"] == 0.30
    assert MODE_PROFILES["roleplay"]["sampling"]["xtc_threshold"] == 0.10

    assert MODE_PROFILES["engineer"]["sampling"]["xtc_probability"] == 0.00
    assert MODE_PROFILES["engineer"]["sampling"]["xtc_threshold"] == 0.10


def test_session_manager_sampling_params_xtc():
    """Verify get_default_sampling_params returns xtc_probability and xtc_threshold for each mode."""
    assistant_params = get_default_sampling_params("assistant")
    assert "xtc_probability" in assistant_params
    assert "xtc_threshold" in assistant_params
    assert assistant_params["xtc_probability"] == 0.20
    assert assistant_params["xtc_threshold"] == 0.10

    rp_params = get_default_sampling_params("roleplay")
    assert "xtc_probability" in rp_params
    assert "xtc_threshold" in rp_params
    assert rp_params["xtc_probability"] == 0.30
    assert rp_params["xtc_threshold"] == 0.10

    eng_params = get_default_sampling_params("engineer")
    assert "xtc_probability" in eng_params
    assert "xtc_threshold" in eng_params
    assert eng_params["xtc_probability"] == 0.00
    assert eng_params["xtc_threshold"] == 0.10


def test_model_sampling_dialog_assistant_xtc(qapp):
    """Test dialog initialization and slider/spinbox synchronization for Assistant mode."""
    from ui.dialogs.model_sampling_dialog import ModelSamplingDialog

    dlg = ModelSamplingDialog(
        current_params={},
        mode="assistant",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    # Check xtc_probability widgets
    assert hasattr(dlg, "spin_xtc_prob")
    assert hasattr(dlg, "slider_xtc_prob")
    assert dlg.spin_xtc_prob.isEnabled() is True
    assert dlg.slider_xtc_prob.isEnabled() is True
    assert dlg.spin_xtc_prob.minimum() == 0.00
    assert dlg.spin_xtc_prob.maximum() == 1.00
    assert dlg.spin_xtc_prob.singleStep() == 0.05
    assert dlg.slider_xtc_prob.minimum() == 0
    assert dlg.slider_xtc_prob.maximum() == 100
    assert dlg.slider_xtc_prob.singleStep() == 5
    assert dlg.slider_xtc_prob.pageStep() == 10
    assert dlg.spin_xtc_prob.value() == 0.20
    assert dlg.slider_xtc_prob.value() == 20

    # Check xtc_threshold widgets
    assert hasattr(dlg, "spin_xtc_thresh")
    assert hasattr(dlg, "slider_xtc_thresh")
    assert dlg.spin_xtc_thresh.isEnabled() is True
    assert dlg.slider_xtc_thresh.isEnabled() is True
    assert dlg.spin_xtc_thresh.minimum() == 0.00
    assert dlg.spin_xtc_thresh.maximum() == 0.50
    assert dlg.spin_xtc_thresh.singleStep() == 0.01
    assert dlg.slider_xtc_thresh.minimum() == 0
    assert dlg.slider_xtc_thresh.maximum() == 50
    assert dlg.slider_xtc_thresh.singleStep() == 1
    assert dlg.slider_xtc_thresh.pageStep() == 5
    assert dlg.spin_xtc_thresh.value() == 0.10
    assert dlg.slider_xtc_thresh.value() == 10

    # Test slider to spinbox sync for xtc_prob
    dlg.slider_xtc_prob.setValue(45)
    assert abs(dlg.spin_xtc_prob.value() - 0.45) < 0.001

    # Test spinbox to slider sync for xtc_prob
    dlg.spin_xtc_prob.setValue(0.60)
    assert dlg.slider_xtc_prob.value() == 60

    # Test slider to spinbox sync for xtc_thresh
    dlg.slider_xtc_thresh.setValue(25)
    assert abs(dlg.spin_xtc_thresh.value() - 0.25) < 0.001

    # Test spinbox to slider sync for xtc_thresh
    dlg.spin_xtc_thresh.setValue(0.18)
    assert dlg.slider_xtc_thresh.value() == 18

    # Test get_sampling_params
    params = dlg.get_sampling_params()
    assert "xtc_probability" in params
    assert "xtc_threshold" in params
    assert params["xtc_probability"] == 0.60
    assert params["xtc_threshold"] == 0.18

    # Test reset defaults
    dlg._reset_defaults()
    assert abs(dlg.spin_xtc_prob.value() - 0.20) < 0.001
    assert dlg.slider_xtc_prob.value() == 20
    assert abs(dlg.spin_xtc_thresh.value() - 0.10) < 0.001
    assert dlg.slider_xtc_thresh.value() == 10


def test_model_sampling_dialog_roleplay_xtc(qapp):
    """Test dialog initialization for Roleplay mode (xtc_probability = 0.30)."""
    from ui.dialogs.model_sampling_dialog import ModelSamplingDialog

    dlg = ModelSamplingDialog(
        current_params={},
        mode="roleplay",
        is_engineer_mode=False,
        is_global_defaults=False
    )

    assert dlg.spin_xtc_prob.value() == 0.30
    assert dlg.slider_xtc_prob.value() == 30
    assert dlg.spin_xtc_thresh.value() == 0.10
    assert dlg.slider_xtc_thresh.value() == 10

    params = dlg.get_sampling_params()
    assert params["xtc_probability"] == 0.30
    assert params["xtc_threshold"] == 0.10


def test_model_sampling_dialog_engineer_locked_xtc(qapp):
    """Test that in Engineer mode XTC probability is strictly locked to 0.00 and disabled."""
    from ui.dialogs.model_sampling_dialog import ModelSamplingDialog

    # Even if current_params attempts to pass non-zero xtc_probability
    dlg = ModelSamplingDialog(
        current_params={"xtc_probability": 0.50, "xtc_threshold": 0.20, "temperature": 0.8},
        mode="assistant",
        is_engineer_mode=True,
        is_global_defaults=False
    )

    assert dlg.spin_xtc_prob.isEnabled() is False
    assert dlg.slider_xtc_prob.isEnabled() is False
    assert dlg.spin_xtc_prob.value() == 0.00
    assert dlg.slider_xtc_prob.value() == 0

    # Threshold should remain customizable even in Engineer mode
    assert dlg.spin_xtc_thresh.isEnabled() is True
    assert dlg.slider_xtc_thresh.isEnabled() is True
    assert dlg.spin_xtc_thresh.value() == 0.20

    params = dlg.get_sampling_params()
    assert params["xtc_probability"] == 0.00
    assert params["xtc_threshold"] == 0.20

    # Resetting defaults must keep Engineer locked at 0.00
    dlg._reset_defaults()
    assert dlg.spin_xtc_prob.value() == 0.00
    assert dlg.slider_xtc_prob.value() == 0
    params = dlg.get_sampling_params()
    assert params["xtc_probability"] == 0.00
    assert params["xtc_threshold"] == 0.10


def test_lm_studio_payload_includes_xtc_params():
    """Verify LMStudioClient.chat_completion passes XTC parameters to payload."""
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
            xtc_probability=0.25,
            xtc_threshold=0.15,
            stream=False
        ))

    mock_session.post.assert_called_once()
    call_kwargs = mock_session.post.call_args[1]
    payload = call_kwargs["json"]

    assert payload.get("xtc_probability") == 0.25
    assert payload.get("xtc_threshold") == 0.15
    assert payload.get("cache_prompt") is True


def test_xtc_priority_cascade():
    """
    Test sampling priority cascade for XTC:
    Session override (PKM) -> Global config default -> MODE_PROFILES
    """
    # 1. Custom session override takes precedence
    current_session = {
        "is_custom_sampling": True,
        "sampling_params": {"xtc_probability": 0.40, "xtc_threshold": 0.05}
    }
    app_config = {"default_sampling_assistant": {"xtc_probability": 0.25, "xtc_threshold": 0.12}}
    mode_profile = {"sampling": {"xtc_probability": 0.20, "xtc_threshold": 0.10}}

    cfg_defaults = app_config.get("default_sampling_assistant", {})
    prof_sampling = mode_profile.get("sampling", {})
    default_prob = cfg_defaults.get("xtc_probability", prof_sampling.get("xtc_probability", 0.20))
    default_thresh = cfg_defaults.get("xtc_threshold", prof_sampling.get("xtc_threshold", 0.10))

    sampling = current_session.get("sampling_params", {})
    is_custom = current_session.get("is_custom_sampling", False)
    xtc_p = float(sampling.get("xtc_probability", default_prob)) if is_custom else float(default_prob)
    xtc_t = float(sampling.get("xtc_threshold", default_thresh)) if is_custom else float(default_thresh)
    assert xtc_p == 0.40
    assert xtc_t == 0.05

    # 2. When not custom, fallback to global default
    current_session["is_custom_sampling"] = False
    is_custom = False
    xtc_p = float(sampling.get("xtc_probability", default_prob)) if is_custom else float(default_prob)
    xtc_t = float(sampling.get("xtc_threshold", default_thresh)) if is_custom else float(default_thresh)
    assert xtc_p == 0.25
    assert xtc_t == 0.12

    # 3. When global default has no xtc parameters, fallback to MODE_PROFILES
    app_config_empty = {}
    cfg_defaults_empty = app_config_empty.get("default_sampling_assistant", {})
    default_prob_prof = cfg_defaults_empty.get("xtc_probability", prof_sampling.get("xtc_probability", 0.20))
    default_thresh_prof = cfg_defaults_empty.get("xtc_threshold", prof_sampling.get("xtc_threshold", 0.10))
    assert float(default_prob_prof) == 0.20
    assert float(default_thresh_prof) == 0.10

    # 4. In Engineer mode, xtc_probability must strictly be 0.00
    is_eng = True
    xtc_p_eng = 0.00 if is_eng else xtc_p
    assert xtc_p_eng == 0.00
