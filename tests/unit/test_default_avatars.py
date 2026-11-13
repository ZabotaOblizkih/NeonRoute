import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap
import config
from ui.components.bubbles.avatar_renderer import (
    render_circular_avatar,
    get_default_avatar_path,
    is_default_avatar_path,
    _DEFAULT_AVATAR_IMAGE_CACHE,
)


@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture for offscreen test execution."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_default_avatar_paths_exist():
    assert config.DEFAULT_ASSISTANT_AVATAR.exists(), "assistant.jpg should exist"
    assert config.DEFAULT_ENGINEER_AVATAR.exists(), "engenier.jpg should exist"
    assert config.DEFAULT_ROLEPLAY_AVATAR.exists(), "RP_chat.jpg should exist"


def test_get_default_avatar_path():
    assert get_default_avatar_path("Элария") == config.DEFAULT_ASSISTANT_AVATAR
    assert get_default_avatar_path("Ассистент") == config.DEFAULT_ASSISTANT_AVATAR
    assert get_default_avatar_path("Инженер") == config.DEFAULT_ENGINEER_AVATAR
    assert get_default_avatar_path("Engineer") == config.DEFAULT_ENGINEER_AVATAR
    assert get_default_avatar_path("Серафина") == config.DEFAULT_ROLEPLAY_AVATAR
    assert get_default_avatar_path("Roleplay") == config.DEFAULT_ROLEPLAY_AVATAR


def test_is_default_avatar_path():
    assert is_default_avatar_path(config.DEFAULT_ASSISTANT_AVATAR) is True
    assert is_default_avatar_path(config.DEFAULT_ENGINEER_AVATAR) is True
    assert is_default_avatar_path(config.DEFAULT_ROLEPLAY_AVATAR) is True
    assert is_default_avatar_path(config.BASE_DIR / "dummy.png") is False


def test_render_circular_avatar_modes(qapp):
    _DEFAULT_AVATAR_IMAGE_CACHE.clear()

    # 1. Assistant default
    pix_asst = render_circular_avatar(None, "Элария", size=48)
    assert isinstance(pix_asst, QPixmap)
    assert not pix_asst.isNull()
    assert pix_asst.width() == 48 * 3
    assert pix_asst.height() == 48 * 3
    assert len(_DEFAULT_AVATAR_IMAGE_CACHE) == 1

    # Cache hit
    pix_asst_cached = render_circular_avatar(None, "Элария", size=48)
    assert not pix_asst_cached.isNull()
    assert pix_asst_cached.width() == 48 * 3
    assert pix_asst_cached.height() == 48 * 3

    # 2. Engineer default
    pix_eng = render_circular_avatar(None, "Инженер", size=48)
    assert isinstance(pix_eng, QPixmap)
    assert not pix_eng.isNull()

    # 3. Roleplay default
    pix_rp = render_circular_avatar(None, "Персонаж", size=48)
    assert isinstance(pix_rp, QPixmap)
    assert not pix_rp.isNull()

    # 4. User fallback (must NOT use default assistant/engineer/rp image)
    pix_user = render_circular_avatar(None, "User", size=48, is_user=True)
    assert isinstance(pix_user, QPixmap)
    assert not pix_user.isNull()

    # 5. Error fallback (must NOT use default assistant/engineer/rp image)
    pix_err = render_circular_avatar(None, "Error", size=48, is_error=True)
    assert isinstance(pix_err, QPixmap)
    assert not pix_err.isNull()
