"""
Unit tests for services/forge_patcher.py.
Verifies status detection (NEEDS_PATCH, PATCHED, INCOMPATIBLE, NOT_FOUND),
backup creation in .neonroute_backup/original/, idempotency, and clean restore.
"""

import pytest
from pathlib import Path
from services.forge_patcher import ForgePatcher, ForgePatchStatus

STOCK_API_PY = '''
class Api:
    def __init__(self):
        self.add_api_route("/sdapi/v1/unload-checkpoint", self.unloadapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/reload-checkpoint", self.reloadapi, methods=["POST"])

    def unloadapi(self):
        sd_models.unload_model_weights()
        return {}

    def reloadapi(self):
        sd_models.send_model_to_device(shared.sd_model)
        return {}

    def set_config(self, req: dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v, is_api=True)
        shared.opts.save(shared.config_filename)

        return

    def get_sd_models(self):
        return [{"title": x.title, "model_name": x.model_name, "hash": x.shorthash, "sha256": x.sha256, "filename": x.filename} for x in sd_models.checkpoints_list.values()]
'''

STOCK_MODELS_PY = '''
class SDModelItem(BaseModel):
    title: str = Field(title="Title")
    model_name: str = Field(title="Model name")
    hash: Optional[str] = Field(title="Short hash")
    sha256: Optional[str] = Field(title="sha256 hash")
    filename: str = Field(title="Filename")
    config: Optional[str] = Field(title="Config file")
'''

STOCK_SD_MODELS_PY = '''
def reload_model_weights(sd_model=None, info=None, forced_reload=False):
    pass

def unload_model_weights(sd_model=None, info=None):
    pass

def apply_token_merging(sd_model, token_merging_ratio):
    pass
'''


def _create_synthetic_forge(root_dir: Path, stock: bool = True) -> Path:
    """Helper to create synthetic Forge folder structure in tmp_path."""
    webui_dir = root_dir / "webui"
    (webui_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)

    if stock:
        (webui_dir / "modules" / "api" / "api.py").write_text(STOCK_API_PY, encoding="utf-8")
        (webui_dir / "modules" / "api" / "models.py").write_text(STOCK_MODELS_PY, encoding="utf-8")
        (webui_dir / "modules" / "sd_models.py").write_text(STOCK_SD_MODELS_PY, encoding="utf-8")
    else:
        (webui_dir / "modules" / "api" / "api.py").write_text("# Corrupt API\nprint('foreign')", encoding="utf-8")
        (webui_dir / "modules" / "api" / "models.py").write_text("# Corrupt Models\n", encoding="utf-8")
        (webui_dir / "modules" / "sd_models.py").write_text("# Corrupt SD\n", encoding="utf-8")

    return root_dir


def test_forge_patcher_not_found(tmp_path):
    """Verifies NOT_FOUND on non-existent or empty folder."""
    status, msg = ForgePatcher.check_status(tmp_path / "empty_dir")
    assert status == ForgePatchStatus.NOT_FOUND


def test_forge_patcher_needs_patch_on_stock_forge(tmp_path):
    """Verifies stock Forge 2.0 is correctly detected as NEEDS_PATCH."""
    forge_dir = _create_synthetic_forge(tmp_path / "stock_forge", stock=True)
    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH
    assert ForgePatcher.has_backup(forge_dir) is False


def test_forge_patcher_apply_backup_and_idempotency(tmp_path):
    """
    Verifies full lifecycle:
    1. Stock Forge detected as NEEDS_PATCH.
    2. apply_patch creates backup in .neonroute_backup/original/ and modifies files.
    3. Status becomes PATCHED.
    4. Second apply_patch call is idempotent and preserves the original backup.
    """
    forge_dir = _create_synthetic_forge(tmp_path / "test_forge", stock=True)
    webui_dir = ForgePatcher.find_forge_webui_dir(forge_dir)

    # 1. Apply patch
    ok, msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True
    assert ForgePatcher.has_backup(forge_dir) is True

    # Check that backup contains the original stock content
    backup_api = webui_dir / ".neonroute_backup" / "original" / "modules" / "api" / "api.py"
    assert backup_api.is_file()
    assert STOCK_API_PY.strip() == backup_api.read_text(encoding="utf-8").strip()

    # Check that patched files contain the modifications
    status, _ = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.PATCHED

    api_text = (webui_dir / "modules" / "api" / "api.py").read_text(encoding="utf-8")
    assert "/sdapi/v1/free-vram" in api_text
    assert "def free_vram_api(" in api_text
    assert "refresh_model_loading_parameters" in api_text
    assert '"config": None' in api_text

    models_text = (webui_dir / "modules" / "api" / "models.py").read_text(encoding="utf-8")
    assert 'config: Optional[str] = Field(default=None' in models_text

    sd_text = (webui_dir / "modules" / "sd_models.py").read_text(encoding="utf-8")
    assert "memory_management.unload_all_models()" in sd_text
    assert "model_data.sd_model = None" in sd_text
    assert "model_data.forge_hash = ''" in sd_text

    # 2. Idempotency test: calling apply_patch again
    backup_mtime_before = backup_api.stat().st_mtime
    ok2, msg2 = ForgePatcher.apply_patch(forge_dir)
    assert ok2 is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED
    # Ensure backup was NOT overwritten
    assert backup_api.stat().st_mtime == backup_mtime_before


def test_forge_patcher_restore_backup(tmp_path):
    """Verifies restore_backup cleanly returns files to stock state."""
    forge_dir = _create_synthetic_forge(tmp_path / "restore_forge", stock=True)

    # Apply patch
    ForgePatcher.apply_patch(forge_dir)
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    # Restore from backup
    ok, msg = ForgePatcher.restore_backup(forge_dir)
    assert ok is True
    status, _ = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH

    # Verify content restored
    webui_dir = ForgePatcher.find_forge_webui_dir(forge_dir)
    api_restored = (webui_dir / "modules" / "api" / "api.py").read_text(encoding="utf-8")
    assert "/sdapi/v1/free-vram" not in api_restored


def test_forge_patcher_incompatible(tmp_path):
    """Verifies foreign/corrupt Forge files are identified as INCOMPATIBLE and not touched."""
    forge_dir = _create_synthetic_forge(tmp_path / "corrupt_forge", stock=False)
    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.INCOMPATIBLE

    ok, res_msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is False
    assert "Отклонено" in res_msg


STOCK_WEBUI_USER_BAT = """@echo off

set PYTHON=
set GIT=
set VENV_DIR=
set COMMANDLINE_ARGS=

call webui.bat
"""

CUSTOM_WEBUI_USER_BAT = """@echo off

set PYTHON=
set GIT=
set VENV_DIR=
set COMMANDLINE_ARGS=--medvram --opt-sdp-attention --theme dark

call webui.bat
"""


def test_forge_patcher_webui_user_bat_stock_empty(tmp_path):
    """Verifies stock webui-user.bat with empty COMMANDLINE_ARGS is detected, backed up, patched, and restored."""
    forge_dir = _create_synthetic_forge(tmp_path / "stock_bat_forge", stock=True)
    webui_dir = forge_dir / "webui"
    bat_file = webui_dir / "webui-user.bat"
    bat_file.write_text(STOCK_WEBUI_USER_BAT, encoding="utf-8")

    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH
    assert "webui-user.bat" in msg

    # Apply patch
    ok, apply_msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    # Check backup created
    backup_bat = webui_dir / ".neonroute_backup" / "original" / "webui-user.bat"
    assert backup_bat.is_file()
    assert backup_bat.read_text(encoding="utf-8").strip() == STOCK_WEBUI_USER_BAT.strip()

    # Check patched contents
    patched_txt = bat_file.read_text(encoding="utf-8")
    assert "[NeonRoute]" in patched_txt
    assert 'set COMMANDLINE_ARGS=%*' in patched_txt
    assert "--api" in patched_txt
    assert "call webui.bat %*" in patched_txt

    # Restore backup
    ok_res, res_msg = ForgePatcher.restore_backup(forge_dir)
    assert ok_res is True
    assert bat_file.read_text(encoding="utf-8").strip() == STOCK_WEBUI_USER_BAT.strip()
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.NEEDS_PATCH


def test_forge_patcher_webui_user_bat_with_user_flags(tmp_path):
    """Verifies webui-user.bat preserving user flags while ensuring --api and %* forwarding."""
    forge_dir = _create_synthetic_forge(tmp_path / "custom_bat_forge", stock=True)
    webui_dir = forge_dir / "webui"
    bat_file = webui_dir / "webui-user.bat"
    bat_file.write_text(CUSTOM_WEBUI_USER_BAT, encoding="utf-8")

    # Apply patch
    ok, _ = ForgePatcher.apply_patch(forge_dir)
    assert ok is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    # Verify user flags are preserved in fallback and --api is injected
    patched_txt = bat_file.read_text(encoding="utf-8")
    assert "--medvram" in patched_txt
    assert "--opt-sdp-attention" in patched_txt
    assert "--api" in patched_txt

    # Restore
    ok_res, _ = ForgePatcher.restore_backup(forge_dir)
    assert ok_res is True
    assert bat_file.read_text(encoding="utf-8").strip() == CUSTOM_WEBUI_USER_BAT.strip()


def test_forge_patcher_set_config_intermediate_lines_and_custom_indent(tmp_path):
    """
    Verifies that _insert_refresh_in_set_config correctly handles intermediate lines/comments
    and aligns with a non-standard return indentation (e.g., 12 spaces inside 'if req:').
    """
    forge_dir = _create_synthetic_forge(tmp_path / "custom_set_config_forge", stock=True)
    webui_dir = forge_dir / "webui"
    api_path = webui_dir / "modules" / "api" / "api.py"

    custom_api_py = STOCK_API_PY.replace(
        """    def set_config(self, req: dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v, is_api=True)
        shared.opts.save(shared.config_filename)

        return""",
        """    def set_config(self, req: dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v, is_api=True)
        shared.opts.save(shared.config_filename)
        # Custom logging hook and intermediate logic
        logger.debug("Saved options to %s", shared.config_filename)
        if req:
            return {"status": "ok"}
        return None"""
    )
    api_path.write_text(custom_api_py, encoding="utf-8")

    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH

    ok, apply_msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    patched_api = api_path.read_text(encoding="utf-8")
    assert "refresh_model_loading_parameters" in patched_api

    # Refresh block must be indented with 12 spaces directly preceding 'return {"status": "ok"}'
    assert "            if checkpoint_name is not None or \"sd_vae\" in req:" in patched_api
    assert "                refresh_model_loading_parameters()" in patched_api
    assert "            return {\"status\": \"ok\"}" in patched_api

    # Clean restore
    ok_res, _ = ForgePatcher.restore_backup(forge_dir)
    assert ok_res is True
    assert api_path.read_text(encoding="utf-8") == custom_api_py
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.NEEDS_PATCH


def test_forge_patcher_atomic_protection_on_invalid_models_or_sd_models(tmp_path):
    """
    Verifies that apply_patch is atomic:
    If api.py is valid but models.py or sd_models.py cannot be patched,
    no files on disk remain in a partially patched state.
    """
    # 1. Test case: models.py is corrupt/unrecognized
    forge_dir = _create_synthetic_forge(tmp_path / "corrupt_models_forge", stock=True)
    webui_dir = forge_dir / "webui"
    api_path = webui_dir / "modules" / "api" / "api.py"
    models_path = webui_dir / "modules" / "api" / "models.py"
    models_path.write_text("# Corrupt models.py without SDModelItem config field\n", encoding="utf-8")

    orig_api = api_path.read_text(encoding="utf-8")
    status, _ = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.INCOMPATIBLE

    ok, msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is False
    assert "Отклонено" in msg
    # api.py on disk must remain pristine
    assert api_path.read_text(encoding="utf-8") == orig_api
    assert "/sdapi/v1/free-vram" not in api_path.read_text(encoding="utf-8")

    # 2. Test case: check_status passes (header matches), but sd_models body cannot be matched
    forge_dir2 = _create_synthetic_forge(tmp_path / "corrupt_sd_body_forge", stock=True)
    webui_dir2 = forge_dir2 / "webui"
    api_path2 = webui_dir2 / "modules" / "api" / "api.py"
    models_path2 = webui_dir2 / "modules" / "api" / "models.py"
    sd_path2 = webui_dir2 / "modules" / "sd_models.py"

    # Header matches SD_UNLOAD_DEF, but body is custom/unrecognized
    unrecognized_sd_py = """def reload_model_weights(sd_model=None, info=None, forced_reload=False):
    pass

def unload_model_weights(sd_model=None, info=None):
    return foreign_function_call(sd_model)
"""
    sd_path2.write_text(unrecognized_sd_py, encoding="utf-8")

    orig_api2 = api_path2.read_text(encoding="utf-8")
    orig_models2 = models_path2.read_text(encoding="utf-8")

    status2, _ = ForgePatcher.check_status(forge_dir2)
    assert status2 == ForgePatchStatus.NEEDS_PATCH

    ok2, msg2 = ForgePatcher.apply_patch(forge_dir2)
    assert ok2 is False
    assert "Не удалось найти тело unload_model_weights" in msg2

    # None of the files on disk should be partially patched
    assert api_path2.read_text(encoding="utf-8") == orig_api2
    assert "/sdapi/v1/free-vram" not in api_path2.read_text(encoding="utf-8")
    assert models_path2.read_text(encoding="utf-8") == orig_models2
    assert "default=None" not in models_path2.read_text(encoding="utf-8")
    assert sd_path2.read_text(encoding="utf-8") == unrecognized_sd_py


SYSINFO_DELEGATED_API_PY = '''
class Api:
    def __init__(self):
        self.add_api_route("/sdapi/v1/unload-checkpoint", self.unloadapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/reload-checkpoint", self.reloadapi, methods=["POST"])

    def unloadapi(self):
        sd_models.unload_model_weights()
        return {}

    def reloadapi(self):
        sd_models.send_model_to_device(shared.sd_model)
        return {}

    def set_config(self, req: dict[str, Any]):
        from modules.sysinfo import set_config
        set_config(req)

    def get_sd_models(self):
        return [{"title": x.title, "model_name": x.model_name, "hash": x.shorthash, "sha256": x.sha256, "filename": x.filename} for x in sd_models.checkpoints_list.values()]
'''

EXISTING_UNLOAD_SD_MODELS_PY = '''
def reload_model_weights(sd_model=None, info=None, forced_reload=False):
    pass

def unload_model_weights(sd_model=None, info=None):
    from backend import memory_management
    memory_management.unload_all_models()
    return

def apply_token_merging(sd_model, token_merging_ratio):
    pass
'''


def test_forge_patcher_sysinfo_delegated_set_config_and_existing_unload_all_models(tmp_path):
    """
    Verifies support for Forge 2.0 builds where:
    1. Api.set_config delegates directly to modules.sysinfo.set_config(req)
       without local checkpoint_name or shared.opts.save.
    2. unload_model_weights already contains basic backend.memory_management.unload_all_models()
       and a return statement (instead of bare pass).
    Verifies detection, atomic patch application, idempotency, and clean restore.
    """
    forge_dir = tmp_path / "forge_delegated"
    webui_dir = forge_dir / "webui"
    (webui_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)

    api_file = webui_dir / "modules" / "api" / "api.py"
    models_file = webui_dir / "modules" / "api" / "models.py"
    sd_file = webui_dir / "modules" / "sd_models.py"

    api_file.write_text(SYSINFO_DELEGATED_API_PY, encoding="utf-8")
    models_file.write_text(STOCK_MODELS_PY, encoding="utf-8")
    sd_file.write_text(EXISTING_UNLOAD_SD_MODELS_PY, encoding="utf-8")

    # 1. Detection
    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH
    assert "API free-vram/refresh" in msg
    assert "sd_models unload" in msg

    # 2. Apply patch
    ok, apply_msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True, f"apply_patch failed: {apply_msg}"
    assert ForgePatcher.has_backup(forge_dir) is True

    # 3. Verify patched content
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    patched_api = api_file.read_text(encoding="utf-8")
    assert "/sdapi/v1/free-vram" in patched_api
    assert "def free_vram_api(" in patched_api
    assert "refresh_model_loading_parameters" in patched_api
    # Safe retrieval of checkpoint_name without relying on an existing local var
    assert 'req.get("sd_model_checkpoint", None)' in patched_api
    assert '"config": None' in patched_api

    patched_models = models_file.read_text(encoding="utf-8")
    assert 'config: Optional[str] = Field(default=None, title="Config file")' in patched_models

    patched_sd = sd_file.read_text(encoding="utf-8")
    assert "model_data.forge_hash = ''" in patched_sd
    assert "memory_management.soft_empty_cache()" in patched_sd
    assert "gc.collect()" in patched_sd

    # 4. Idempotency
    backup_api = webui_dir / ".neonroute_backup" / "original" / "modules" / "api" / "api.py"
    backup_mtime = backup_api.stat().st_mtime
    ok2, _ = ForgePatcher.apply_patch(forge_dir)
    assert ok2 is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED
    assert backup_api.stat().st_mtime == backup_mtime

    # 5. Clean restore
    ok_res, res_msg = ForgePatcher.restore_backup(forge_dir)
    assert ok_res is True
    assert api_file.read_text(encoding="utf-8").strip() == SYSINFO_DELEGATED_API_PY.strip()
    assert models_file.read_text(encoding="utf-8").strip() == STOCK_MODELS_PY.strip()
    assert sd_file.read_text(encoding="utf-8").strip() == EXISTING_UNLOAD_SD_MODELS_PY.strip()
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.NEEDS_PATCH


def test_forge_patcher_prepatched_config_fields(tmp_path):
    """
    Verifies that if models.py or api.py already have config=None populated,
    the patcher respects them gracefully without failing or corrupting syntax.
    """
    forge_dir = tmp_path / "forge_prepatched_config"
    webui_dir = forge_dir / "webui"
    (webui_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)

    api_file = webui_dir / "modules" / "api" / "api.py"
    models_file = webui_dir / "modules" / "api" / "models.py"
    sd_file = webui_dir / "modules" / "sd_models.py"

    # api.py has get_sd_models with config: None already
    prepatched_api = STOCK_API_PY.replace('"filename": x.filename}', '"filename": x.filename, "config": None}')
    # models.py already has default=None
    prepatched_models = STOCK_MODELS_PY.replace(
        'config: Optional[str] = Field(title="Config file")',
        'config: Optional[str] = Field(default=None, title="Config file")'
    )

    api_file.write_text(prepatched_api, encoding="utf-8")
    models_file.write_text(prepatched_models, encoding="utf-8")
    sd_file.write_text(STOCK_SD_MODELS_PY, encoding="utf-8")

    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH
    # models config=None should NOT be listed in missing
    assert "models config=None" not in msg
    assert "API free-vram/refresh" in msg
    assert "sd_models unload" in msg

    ok, apply_msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    # Restoring returns to prepatched state
    ok_res, _ = ForgePatcher.restore_backup(forge_dir)
    assert ok_res is True
    assert api_file.read_text(encoding="utf-8").strip() == prepatched_api.strip()
    assert models_file.read_text(encoding="utf-8").strip() == prepatched_models.strip()


def test_forge_patcher_get_sd_models_getattr_config_and_delegated_set_config(tmp_path):
    """
    Verifies support for Forge 2.0 builds where:
    1. get_sd_models() already contains "config": getattr(x, 'config', None) instead of literal None.
    2. set_config delegates to modules.sysinfo.set_config(req).
    3. unload_model_weights contains unload_all_models() and return.
    Flow: NEEDS_PATCH -> apply_patch() == True -> PATCHED -> idempotent -> clean restore.
    """
    forge_dir = tmp_path / "forge_getattr_config"
    webui_dir = forge_dir / "webui"
    (webui_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)

    api_file = webui_dir / "modules" / "api" / "api.py"
    models_file = webui_dir / "modules" / "api" / "models.py"
    sd_file = webui_dir / "modules" / "sd_models.py"

    getattr_api_py = SYSINFO_DELEGATED_API_PY.replace(
        '"filename": x.filename}',
        '"filename": x.filename, "config": getattr(x, \'config\', None)}'
    )

    api_file.write_text(getattr_api_py, encoding="utf-8")
    models_file.write_text(STOCK_MODELS_PY, encoding="utf-8")
    sd_file.write_text(EXISTING_UNLOAD_SD_MODELS_PY, encoding="utf-8")

    # 1. Detection: needs patch (missing free-vram route, refresh_block in set_config, models.py, sd_models.py)
    status, msg = ForgePatcher.check_status(forge_dir)
    assert status == ForgePatchStatus.NEEDS_PATCH
    assert "API free-vram/refresh" in msg
    assert "sd_models unload" in msg
    assert "models config=None" in msg

    # 2. Apply patch
    ok, apply_msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True, f"apply_patch failed: {apply_msg}"
    assert ForgePatcher.has_backup(forge_dir) is True

    # 3. Verify status PATCHED and patched files
    status_after, _ = ForgePatcher.check_status(forge_dir)
    assert status_after == ForgePatchStatus.PATCHED

    patched_api = api_file.read_text(encoding="utf-8")
    assert "/sdapi/v1/free-vram" in patched_api
    assert "def free_vram_api(" in patched_api
    assert "refresh_model_loading_parameters" in patched_api
    # Existing getattr was preserved without error or duplicate keys
    assert '"config": getattr(x, \'config\', None)' in patched_api

    patched_models = models_file.read_text(encoding="utf-8")
    assert 'config: Optional[str] = Field(default=None, title="Config file")' in patched_models

    patched_sd = sd_file.read_text(encoding="utf-8")
    assert "model_data.forge_hash = ''" in patched_sd
    assert "memory_management.soft_empty_cache()" in patched_sd

    # 4. Idempotency test
    backup_api = webui_dir / ".neonroute_backup" / "original" / "modules" / "api" / "api.py"
    backup_mtime = backup_api.stat().st_mtime
    ok2, _ = ForgePatcher.apply_patch(forge_dir)
    assert ok2 is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED
    assert backup_api.stat().st_mtime == backup_mtime

    # 5. Clean restore
    ok_res, res_msg = ForgePatcher.restore_backup(forge_dir)
    assert ok_res is True
    assert api_file.read_text(encoding="utf-8").strip() == getattr_api_py.strip()
    assert models_file.read_text(encoding="utf-8").strip() == STOCK_MODELS_PY.strip()
    assert sd_file.read_text(encoding="utf-8").strip() == EXISTING_UNLOAD_SD_MODELS_PY.strip()
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.NEEDS_PATCH



