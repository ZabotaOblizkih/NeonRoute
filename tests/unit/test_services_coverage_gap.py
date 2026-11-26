"""
Compact, high-signal unit tests closing coverage gaps in critical services:
- services.card_translator (tag masking, repetition guard, CJK Quality Gate)
- services.chub_client (promo sanitization, token estimation, search_characters & WAF guard)
- services.search_tokenizer (RU/EN Porter stemming, compound decomposition, BM25 tokenization)
- services.file_generator (real .xlsx and .docx generation in tmp_path)
"""

from unittest.mock import MagicMock
import pytest
import requests

from services.card_translator import (
    mask_card_tags,
    unmask_card_tags,
    sanitize_repetition,
    split_text_hierarchical,
    translate_text_slot,
)
from services.chub_client import ChubClient, sanitize_card_description
from services.search_tokenizer import SearchTokenizer
from services.file_generator import FileGenerator


# ---------------------------------------------------------------------------
# 1. CardTranslator & CJK Quality Gate
# ---------------------------------------------------------------------------

def test_card_translator_tags_and_repetition():
    src = "Hello {{char}}, meet <user> and [IMAGE_PROMPT: neon city]."
    masked, tag_map = mask_card_tags(src)
    assert "{{char}}" not in masked and "<user>" not in masked
    assert unmask_card_tags(masked.replace("__NR_TAG_0__", "_NR TAG 0_"), tag_map) == src

    cleaned, looped = sanitize_repetition("Хороший текст." + (" ОК" * 10))
    assert looped is True
    assert cleaned == "Хороший текст."

    chunks = split_text_hierarchical("Para 1\n\nPara 2\n\nPara 3", max_chars=14)
    assert len(chunks) >= 2


def test_card_translator_cjk_quality_gate_retries():
    lm_client = MagicMock()
    lm_client.chat_completion.side_effect = [
        "Она вошла в комнату 房间 и осмотрелась.",  # 1st attempt contains CJK
        "Она вошла в комнату и осмотрелась вокруг.",  # 2nd attempt clean Russian
    ]
    res = translate_text_slot("She entered the room {{char}}.", "personality", lm_client)
    assert lm_client.chat_completion.call_count == 2
    assert "房间" not in res
    assert "осмотрелась" in res


# ---------------------------------------------------------------------------
# 2. ChubClient & Description Sanitization
# ---------------------------------------------------------------------------

def test_chub_sanitize_description_and_estimate_tokens():
    raw = (
        "A cyberpunk detective searching for lost memories in Neo-Tokyo.\n\n"
        "More cards:\n"
        "https://chub.ai/users/author\n"
        "- Bot A\n"
        "Created by AuthorX"
    )
    cleaned, notes = sanitize_card_description(raw)
    assert "cyberpunk detective" in cleaned
    assert "https://chub.ai" not in cleaned
    assert any("chub.ai" in n for n in notes)

    assert ChubClient.estimate_tokens("") == 0
    assert ChubClient.estimate_tokens("Привет мир") > 0


@pytest.mark.parametrize("status_code,headers,body,json_data,expect_exc", [
    (200, {"Content-Type": "application/json"}, '{"nodes":[]}', {"nodes": [{"id": 1, "name": "Aria"}], "total": 1}, None),
    (200, {"Content-Type": "text/html"}, "<html>Cloudflare</html>", None, "Cloudflare"),
    (503, {"Content-Type": "text/plain"}, "Service Unavailable", None, "код ошибки 503"),
])
def test_chub_search_characters_responses(status_code, headers, body, json_data, expect_exc):
    client = ChubClient()
    resp = MagicMock(status_code=status_code, headers=headers, text=body)
    if json_data is not None:
        resp.json.return_value = json_data
    client.session.get = MagicMock(return_value=resp)

    if expect_exc:
        with pytest.raises(RuntimeError, match=expect_exc):
            client.search_characters(query="aria")
    else:
        cards, total = client.search_characters(query="aria")
        assert total == 1
        assert len(cards) == 1


# ---------------------------------------------------------------------------
# 3. SearchTokenizer (RU/EN Stemming & BM25 Tokenization)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word,expected_prefix", [
    ("нейросетей", "нейросет"),
    ("программированием", "программирован"),
    ("running", "runn"),
    ("connections", "connection"),
])
def test_search_tokenizer_stemming(word, expected_prefix):
    stemmed = SearchTokenizer.stem(word)
    assert stemmed.startswith(expected_prefix[:5])


def test_search_tokenizer_compound_and_tokenize():
    parts = SearchTokenizer.decompose_compound_word("видеопамять")
    assert parts == ["видео", "память"]

    tokens = SearchTokenizer.tokenize("Видеопамять GPU running fast!")
    assert "видео" in tokens
    assert "gpu" in tokens
    assert "runn" in tokens


# ---------------------------------------------------------------------------
# 4. FileGenerator (.xlsx and .docx)
# ---------------------------------------------------------------------------

def test_file_generator_creates_valid_xlsx_and_docx(tmp_path, monkeypatch):
    monkeypatch.setattr("services.file_generator.EXPORTS_DIR", tmp_path)

    md_table = "| Имя | Значение |\n| --- | --- |\n| VRAM | 8192 |\n| RAM | 16384 |"
    xlsx_path = FileGenerator.create_excel("Отчет по памяти", md_table)
    assert xlsx_path.is_file()
    assert xlsx_path.suffix == ".xlsx"
    assert xlsx_path.read_bytes()[:2] == b"PK"

    docx_md = "# Заголовок\n\nОбычный **жирный** текст.\n\n" + md_table
    docx_path = FileGenerator.create_word("Документ", docx_md)
    assert docx_path.is_file()
    assert docx_path.suffix == ".docx"
    assert docx_path.read_bytes()[:2] == b"PK"
