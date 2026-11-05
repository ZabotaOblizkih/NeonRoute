import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

def test_01_direct_command_parser():
    from services.direct_command_parser import parse_direct_command_params, parse_user_negative_trigger
    cmd = "cyberpunk city, neon rain --steps 35 --cfg 6.5 --denoise 0.45 --hires on --ad on --seed 4242 --model realistic --ar wide --negative bad quality, blurry"
    pos, neg, params = parse_direct_command_params(cmd)
    assert pos == "cyberpunk city, neon rain"
    assert neg == "bad quality, blurry"
    assert params["steps"] == 35
    assert params["cfg_scale"] == 6.5
    assert params["enable_hr"] is True
    assert params["enable_adetailer"] is True
    assert params["seed"] == 4242
    assert params["model_override"] == "realistic"
    assert params["width"] == 768

    has_neg, p_text, n_text = parse_user_negative_trigger("portrait of girl negative: ugly")
    assert has_neg is True
    assert p_text == "portrait of girl"
    assert n_text == "ugly"

def test_02_sandbox_guard_is_safe_path():
    from services.sandbox_guard import _is_safe_path
    ws_mock = Path("C:/NeonRoute/workspace").resolve()
    assert _is_safe_path(ws_mock / "script.py", ws_mock, allow_ws_root=True) is True
    assert _is_safe_path(ws_mock, ws_mock, allow_ws_root=False) is False
    assert _is_safe_path(Path("C:/Windows/System32/cmd.exe"), ws_mock) is False

def test_03_sandbox_runner_validate():
    from services.sandbox_runner import validate_module_syntax
    assert validate_module_syntax("def hello():\n    return 42\n") is True
    assert validate_module_syntax("def broken(:\n    return") is False

def test_04_asset_validator(tmp_path):
    fake_forge = tmp_path / "webui_forge_cu121_torch231" / "webui"
    sd_dir = fake_forge / "models" / "Stable-diffusion"
    ad_dir = fake_forge / "models" / "adetailer"
    sd_dir.mkdir(parents=True)
    ad_dir.mkdir(parents=True)

    (sd_dir / "realisticVisionV60_v60B1_vae.safetensors").write_bytes(b"dummy")
    (ad_dir / "face_yolov8n.pt").write_bytes(b"dummy")

    with patch("services.asset_validator.FORGE_ROOT_DIR", tmp_path), \
         patch("services.asset_validator.FORGE_BASE_DIR", fake_forge), \
         patch("services.asset_validator.MODELS_SD_DIR", sd_dir), \
         patch("services.asset_validator.MODELS_ADETAILER_DIR", ad_dir):
        
        from services.asset_validator import AssetValidator
        report = AssetValidator.validate_all_assets()
        assert report["forge_installed"] is True
        assert report["checkpoints_found"]["Realistic Vision v6.0"] is True
        assert report["adetailer_found"]["face_yolov8n.pt"] is True
        assert report["adetailer_found"]["hand_yolov8n.pt"] is False
        assert report["all_critical_present"] is False
        assert AssetValidator.is_adetailer_available("face_yolov8n.pt") is not None
        AssetValidator.log_validation_summary()

def test_05_memory_extractor():
    from services.memory_extractor import MemoryExtractor
    assert MemoryExtractor._has_user_fact_potential("что ты умеешь делать?") is False
    assert MemoryExtractor._has_user_fact_potential("я работаю инженером") is True

    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = '{"facts": ["Пользователь работает инженером"]}'
    with patch("services.memory_extractor.memory_db") as mock_db:
        mock_db.add_fact.return_value = 1
        history = [{"role": "user", "text": "Я инженер"}]
        MemoryExtractor._run_extraction(history, lm_client=mock_lm)
        assert mock_db.add_fact.called

def test_06_engineer_diagnostics():
    from services.engineer_diagnostics import clean_traceback, extract_error_fingerprint, diagnose_runtime_error, extract_crash_slice
    fake_tb = "Traceback (most recent call last):\\n  File \"sandbox_runner.py\", line 1\n  File \"task.py\", line 12\nZeroDivisionError"
    cleaned = clean_traceback(fake_tb)
    assert "sandbox_runner.py" not in cleaned
    assert "task.py" in cleaned
    assert "ZeroDivisionError" in cleaned

    diag = diagnose_runtime_error("KeyError: 'no style with name FancyTitle'")
    assert "В python-docx нельзя указывать вымышленные стили" in diag

    diag_arg = diagnose_runtime_error("the following arguments are required: --input-file")
    assert "[СБОЙ АВТОНОМНОГО ЗАПУСКА CLI]" in diag_arg

    code = "x = 1\nres = x / 0\n"
    crash_slice = extract_crash_slice(code, "File 'test.py', line 2, in <module>", filename="test.py", window=1)
    assert "> " in crash_slice

def test_07_engineer_review(tmp_path):
    from services.engineer_review import ReviewPipeline
    ws_path = tmp_path / "workspace"
    ws_path.mkdir()
    (ws_path / "main.py").write_text("print('hello')", encoding="utf-8")

    mock_send = MagicMock()
    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = "Код выглядит чисто."
    plan_mock = MagicMock()
    plan_mock.target_artifact = "main.py"

    git_mock_module = MagicMock()
    git_mock_module.GitSandbox.checkpoint.return_value = True

    # Мокаем локальный импорт git_sandbox
    with patch.dict("sys.modules", {"services.git_sandbox": git_mock_module}):
        res = ReviewPipeline.run(
            plan=plan_mock,
            task_prompt="Проверь код",
            ws_dir=ws_path,
            client=mock_lm,
            target_model="local-model",
            safe_send_message=mock_send
        )
        assert res["success"] is True
        assert mock_send.called

        res_commit = ReviewPipeline.run(
            plan=plan_mock,
            task_prompt="Сделай коммит 'Init'",
            ws_dir=ws_path,
            client=mock_lm,
            target_model="local-model",
            safe_send_message=mock_send
        )
        assert res_commit["success"] is True
        assert "Git-чекпоинт" in res_commit["message"]
