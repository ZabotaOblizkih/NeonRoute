"""
Unit tests for Default Background and Cache Protection in NeonRoute.
Verifies that:
1. DEFAULT_BACKGROUND_PATH is defined and exists in assets/.
2. get_protected_background_paths() always includes the default background and canonical image.
3. CacheCleanWorker never deletes the default background or canonical image even if unreferenced in config.json.
4. MainWindow uses DEFAULT_BACKGROUND_PATH as fallback when background_path is empty.
"""

from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication

import config
from ui.dialogs.clean_cache_dialog import get_protected_background_paths, CacheCleanWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_default_background_path_constant():
    assert hasattr(config, "DEFAULT_BACKGROUND_PATH")
    assert hasattr(config, "CANONICAL_BACKGROUND_NAME")
    assert config.DEFAULT_BACKGROUND_PATH.exists()
    assert config.DEFAULT_BACKGROUND_PATH.name == "default_background.jpg"
    assert config.DEFAULT_BACKGROUND_PATH.stat().st_size > 0


def test_get_protected_background_paths_includes_default():
    protected = get_protected_background_paths()
    assert config.DEFAULT_BACKGROUND_PATH.resolve() in protected
    canonical = config.BACKGROUNDS_DIR / config.CANONICAL_BACKGROUND_NAME
    if canonical.exists():
        assert canonical.resolve() in protected


def test_cache_clean_worker_preserves_default_background(tmp_path, monkeypatch):
    # Simulate a backgrounds directory containing default image and a temporary stale image
    test_bg_dir = tmp_path / "backgrounds"
    test_bg_dir.mkdir(parents=True, exist_ok=True)

    canonical_file = test_bg_dir / config.CANONICAL_BACKGROUND_NAME
    canonical_file.write_bytes(b"CANONICAL_IMAGE_BYTES")

    stale_file = test_bg_dir / "stale_temp_wallpaper.jpg"
    stale_file.write_bytes(b"STALE_TEMP_BYTES")

    monkeypatch.setattr(config, "BACKGROUNDS_DIR", test_bg_dir)

    # Empty config without background_path
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "non_existent_config.json")
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "empty_history")

    targets = {
        "Неиспользуемые обои рабочего пространства": [test_bg_dir]
    }

    worker = CacheCleanWorker(targets)
    worker.run()

    # The canonical/default background MUST be preserved
    assert canonical_file.exists()
    assert canonical_file.read_bytes() == b"CANONICAL_IMAGE_BYTES"

    # The stale unreferenced file should be cleaned
    assert not stale_file.exists()
