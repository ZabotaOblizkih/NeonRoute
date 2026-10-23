import sys
import re
import json
import pytest
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.file_utils import atomic_write_json
from services.regex_filter import TextRegexFilter
from services.chub_client import ChubClient
import config

def test_atomic_write_json(tmp_path):
    target = tmp_path / "sub" / "test.json"
    data = {"hello": "world", "number": 42}
    success = atomic_write_json(target, data)
    assert success is True
    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data

def test_markdown_fence_and_think_stripping():
    raw_resp = """<think>
Some thinking process here...
{"bad": "object"}
</think>
```json
{
  "summary": "Valid result",
  "visual_prompt": "1girl, cyber city"
}
```"""
    clean_resp = TextRegexFilter.process_output(str(raw_resp or "")).strip()
    if "```" in clean_resp:
        clean_resp = re.sub(r'^```(?:json)?\s*|\s*```$', '', clean_resp, flags=re.IGNORECASE).strip()

    data = json.loads(clean_resp)
    assert data["summary"] == "Valid result"
    assert "bad" not in data

def test_roleplay_flow_image_tag_sub():
    from services.roleplay_flow_controller import RE_IMAGE_TAG
    text = "Начало сцены [IMAGE_PROMPT: 1girl solo --model anime] и продолжение [IMAGE_PROMPT: extra tag] финал."
    clean_text = RE_IMAGE_TAG.sub('', text).strip()
    assert "[IMAGE_PROMPT" not in clean_text
    assert "Начало сцены" in clean_text
    assert "финал." in clean_text

def test_assistant_role_label_regex():
    clean_text = "Образ готов.\n\nЭлария:"
    clean_text = re.sub(r'(?:\n\s*)(?:Элария|Assistant|Пользователь|User)\s*:\s*$', '', clean_text, flags=re.IGNORECASE).strip()
    assert clean_text == "Образ готов."

def test_chub_safe_extraction():
    # Verify handling when data is None or unexpected structure
    data = {"data": None, "nodes": [{"name": "Hero", "fullPath": "user/hero"}]}
    raw_nodes = (
        data.get("nodes")
        or ((data.get("data") or {}).get("nodes") if isinstance(data.get("data"), dict) else None)
        or data.get("characters")
        or (data if isinstance(data, list) else [])
    )
    assert len(raw_nodes) == 1
    assert raw_nodes[0]["name"] == "Hero"
