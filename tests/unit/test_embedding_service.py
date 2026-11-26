"""
tests/unit/test_embedding_service.py
====================================
Isolated unit & UI integration tests for embedding resolution, exact TextualInversion
Civitai search, atomic .pt/.safetensors downloading, and user confirmation flow.
"""
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from core.forge_locator import resolve_forge_embeddings_directory
from services.embedding_service import (
    download_embedding_file,
    extract_embedding_candidates,
    find_local_embedding,
    normalize_embedding_key,
    resolve_missing_embeddings,
    search_civitai_embedding,
)
from services.forge_client import ForgeClient
from ui.models_manager_dialog import ModelsManagerDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_resolve_forge_embeddings_dir_and_client_no_hardcode(tmp_path, monkeypatch):
    """Проверяет приоритет --embeddings-dir, штатные пути и отсутствие хардкода в ForgeClient."""
    forge_root = tmp_path / "MyForge"
    custom_emb = tmp_path / "CustomEmbeddings"
    custom_emb.mkdir(parents=True)
    (custom_emb / "easynegative.safetensors").write_bytes(b"emb")

    launcher = forge_root / "run.bat"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(f'@echo off\nset COMMANDLINE_ARGS=--embeddings-dir "{custom_emb}"\n', encoding="utf-8")

    resolved = resolve_forge_embeddings_directory(forge_root=forge_root, launcher_path=launcher)
    assert resolved == custom_emb.resolve()

    monkeypatch.setattr("services.asset_validator.AssetValidator.resolve_embeddings_dirs", classmethod(lambda cls: [custom_emb]))
    client = ForgeClient(base_url="http://127.0.0.1:1")
    monkeypatch.setattr("services.forge_client.requests.get", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("offline")))
    installed = client.get_installed_embeddings()
    assert "easynegative" in installed


def test_extract_embedding_candidates_filtering_and_catalog_priority():
    """Проверяет парсинг весов/скобок, отсев стоп-слов и приоритет каталога."""
    neg = "(easynegative:1.2), [badhandv4], worst quality, low quality, blurry, 3d, cgi, watermark, bad, easy, fast, low, bad-hands-5"
    catalog_embs = [{"name": "FastNegativeV2", "architecture": "sd15", "civitai_version_id": 555}]

    cands = extract_embedding_candidates(neg, catalog_embeddings=catalog_embs)
    keys = [c["key"] for c in cands]

    assert "fastnegativev2" in keys
    assert "easynegative" in keys
    assert "badhandv4" in keys
    assert "badhands5" in keys
    assert cands[0]["source"] == "catalog"
    # Стоп-слова и обычные теги отсеяны
    for noise in ("worstquality", "lowquality", "blurry", "3d", "cgi", "watermark", "bad", "easy", "fast", "low"):
        assert noise not in keys


def test_local_embedding_skips_network_search(tmp_path, monkeypatch):
    """Если эмбеддинг уже есть на диске (.pt/.safetensors), сетевой поиск не выполняется."""
    emb_dir = tmp_path / "embeddings"
    emb_dir.mkdir()
    (emb_dir / "bad-hands-5.pt").write_bytes(b"pt-data")

    assert find_local_embedding("bad-hands-5", search_dirs=[emb_dir]) is not None
    monkeypatch.setattr(
        "services.embedding_service.requests.get",
        lambda *a, **kw: pytest.fail("Network should not be called for local embedding!"),
    )

    res = resolve_missing_embeddings(
        "bad-hands-5, worst quality",
        architecture="sd15",
        search_dirs=[emb_dir],
    )
    assert len(res["installed"]) == 1
    assert res["ready_to_download"] == []


def test_local_embedding_in_subfolder_and_webui_dir_skips_network(tmp_path, monkeypatch):
    """
    Проверяет, что эмбеддинг (например negativeXL_D) в подпапке embeddings/SDXL/
    или в динамически найденном webui_dir/embeddings распознаётся локально и не предлагается к скачиванию.
    """
    forge_root = tmp_path / "ForgePack"
    webui_dir = forge_root / "webui_custom" / "webui"
    emb_sub = webui_dir / "embeddings" / "SDXL"
    emb_sub.mkdir(parents=True)
    (emb_sub / "negativeXL_D.safetensors").write_bytes(b"xl-neg")

    monkeypatch.setattr("services.asset_validator.AssetValidator.resolve_forge_root", classmethod(lambda cls: forge_root))
    monkeypatch.setattr("services.asset_validator.AssetValidator.resolve_forge_launcher", classmethod(lambda cls, r=None: None))
    monkeypatch.setattr("services.asset_validator.AssetValidator.resolve_webui_dir", classmethod(lambda cls, r=None: webui_dir))
    monkeypatch.setattr("services.embedding_service.requests.get", lambda *a, **kw: pytest.fail("Should not query Civitai!"))

    res = resolve_missing_embeddings("negativeXL_D, worst quality", architecture="sdxl")
    assert len(res["installed"]) == 1
    assert res["installed"][0]["name"] == "negativeXL_D"
    assert res["ready_to_download"] == []
    assert res["ambiguous"] == []


def test_empty_webui_dir_still_discovers_webui_star_embeddings(tmp_path, monkeypatch):
    """Если в forge_root есть пустая папка webui/, но реальный Forge лежит в webui_*/webui/, эмбеддинг находится локально."""
    forge_root = tmp_path / "ForgeRoot"
    (forge_root / "webui").mkdir(parents=True)
    real_emb = forge_root / "webui_bundle_xyz" / "webui" / "embeddings"
    real_emb.mkdir(parents=True)
    (real_emb / "negativeXL_D.safetensors").write_bytes(b"xl-neg")

    monkeypatch.setattr("services.asset_validator.AssetValidator.resolve_forge_root", classmethod(lambda cls: forge_root))
    monkeypatch.setattr("services.asset_validator.AssetValidator.resolve_forge_launcher", classmethod(lambda cls, r=None: None))
    monkeypatch.setattr("services.embedding_service.requests.get", lambda *a, **kw: pytest.fail("Should not query Civitai!"))

    res = resolve_missing_embeddings("negativeXL_D", architecture="sdxl")
    assert len(res["installed"]) == 1
    assert res["ready_to_download"] == []


def test_civitai_textual_inversion_exact_match_arch_and_ambiguous(monkeypatch):
    """
    Проверяет:
    - поиск строго types=TextualInversion;
    - отсев 'badhands' при поиске 'badhandv4' и отсев несовместимой архитектуры SDXL при запросе sd15;
    - статус 'ambiguous' при нескольких точных совпадениях.
    """
    captured_params = {}

    def _mock_get(url, params=None, **kwargs):
        captured_params.update(params or {})
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "items": [
                {
                    "name": "badhands",
                    "type": "TextualInversion",
                    "modelVersions": [{
                        "id": 1, "name": "v1", "baseModel": "SD 1.5",
                        "files": [{"name": "badhands.pt", "downloadUrl": "https://civitai.com/api/download/models/1"}],
                    }],
                },
                {
                    "name": "badhandv4",
                    "type": "TextualInversion",
                    "modelVersions": [{
                        "id": 2, "name": "v4", "baseModel": "SDXL 1.0",  # wrong arch for sd15
                        "files": [{"name": "badhandv4_xl.pt", "downloadUrl": "https://civitai.com/api/download/models/2"}],
                    }],
                },
                {
                    "name": "badhandv4",
                    "type": "TextualInversion",
                    "modelVersions": [{
                        "id": 3, "name": "v4", "baseModel": "SD 1.5",
                        "files": [{"name": "badhandv4.pt", "downloadUrl": "https://civitai.com/api/download/models/3"}],
                    }],
                },
            ]
        }
        return resp

    monkeypatch.setattr("services.embedding_service.requests.get", _mock_get)
    res = search_civitai_embedding("badhandv4", architecture="sd15")
    assert captured_params.get("types") == "TextualInversion"
    assert res["status"] == "found"
    assert res["candidate"]["filename"] == "badhandv4.pt"
    assert normalize_embedding_key("badhandv4") != normalize_embedding_key("badhands")

    # Ambiguous case: 2 matching items
    def _mock_ambig(url, params=None, **kwargs):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "items": [
                {
                    "name": "FastNegativeV2",
                    "type": "TextualInversion",
                    "modelVersions": [{"id": 10, "name": "v2", "baseModel": "SD 1.5", "files": [{"name": "FastNegativeV2.pt", "downloadUrl": "https://civitai.com/api/download/models/10"}]}],
                },
                {
                    "name": "FastNegativeV2",
                    "type": "TextualInversion",
                    "modelVersions": [{"id": 11, "name": "v2-alt", "baseModel": "SD 1.5", "files": [{"name": "FastNegativeV2.safetensors", "downloadUrl": "https://civitai.com/api/download/models/11"}]}],
                },
            ]
        }
        return resp

    monkeypatch.setattr("services.embedding_service.requests.get", _mock_ambig)
    amb_res = search_civitai_embedding("FastNegativeV2", architecture="sd15")
    assert amb_res["status"] == "ambiguous"
    assert len(amb_res["candidates"]) == 2


def test_download_embedding_file_pt_and_safetensors_atomic_and_sha256(tmp_path, monkeypatch):
    """Проверяет скачивание .pt/.safetensors через .part, проверку SHA-256 и SSRF-защиту."""
    payload = b"embedding-binary-content"
    expected_sha = hashlib.sha256(payload).hexdigest()

    fake_resp = MagicMock()
    fake_resp.url = "https://civitai.com/api/download/models/123"
    fake_resp.headers = {"content-length": str(len(payload))}
    fake_resp.iter_content.return_value = [payload]
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    monkeypatch.setattr("services.embedding_service.requests.get", lambda *a, **kw: fake_resp)

    dest = tmp_path / "embeddings" / "badhandv4.pt"
    saved = download_embedding_file("https://civitai.com/api/download/models/123", dest, expected_sha256=expected_sha)
    assert saved.exists() and saved.read_bytes() == payload
    assert not dest.with_name(f"{dest.name}.part").exists()

    with pytest.raises(ValueError, match="SSRF"):
        download_embedding_file("https://evil.example.com/bad.pt", dest)


def test_ui_embedding_confirmation_required_before_download(qapp, tmp_path, monkeypatch):
    """Проверяет, что при отказе пользователя скачивание не запускается, а при подтверждении — запускается."""
    monkeypatch.setattr("services.art_director_config.ART_DIRECTOR_MODELS_PATH", tmp_path / "models.json")
    monkeypatch.setattr("config.ART_DIRECTOR_MODELS_PATH", tmp_path / "models.json")
    monkeypatch.setattr("services.model_router.DEFAULT_DATA_PATH", tmp_path / "models.json")

    with patch.object(ModelsManagerDialog, "_start_forge_scan"):
        dialog = ModelsManagerDialog()

    resolved_payload = {
        "installed": [],
        "ready_to_download": [{
            "name": "easynegative",
            "version_name": "v2",
            "architecture": "sd15",
            "filename": "easynegative.safetensors",
            "download_url": "https://civitai.com/api/download/models/99",
            "source": "civitai",
        }],
        "ambiguous": [],
        "not_found": [],
    }

    decline_dlg = MagicMock()
    decline_dlg.exec.return_value = False
    with patch("ui.models_manager_dialog.InfoBar"), patch("ui.models_manager_dialog.MessageBox", return_value=decline_dlg), patch.object(dialog, "_start_embeddings_download") as mock_dl:
        dialog._on_embeddings_resolved(resolved_payload)
        mock_dl.assert_not_called()

    accept_dlg = MagicMock()
    accept_dlg.exec.return_value = True
    with patch("ui.models_manager_dialog.InfoBar"), patch("ui.models_manager_dialog.MessageBox", return_value=accept_dlg), patch.object(dialog, "_start_embeddings_download") as mock_dl:
        dialog._on_embeddings_resolved(resolved_payload)
        mock_dl.assert_called_once()

    dialog.reject()
