"""Tests for governance_review.py — the three-model adversarial review pipeline.

Focus: severity parsing, finding extraction, and the Ollama lane's reliability
guards (CLAUDE.md "Local LLM Routing"): reasoning-field fallback for thinking
models (deepseek-r1 is the review model) and empty output treated as failure —
an empty review recorded as success is exactly the silent-malformed-response
failure that killed the local tier for a month.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import governance_review as gr


# ------------------------------------------------------ severity parsing

def test_count_severity_counts_markers_per_line():
    text = "[CRITICAL] a\nprose\n[HIGH] b\n[HIGH] c\n[LOW] d"
    assert gr._count_severity(text) == {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 0, "LOW": 1}


def test_count_severity_empty_text():
    assert gr._count_severity("") == {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}


def test_extract_findings_groups_continuation_lines():
    text = "[HIGH] first issue\nmore detail here\n[LOW] second issue"
    findings = gr._extract_findings(text)
    assert len(findings) == 2
    assert findings[0]["severity"] == "HIGH"
    assert "more detail here" in findings[0]["text"]
    assert findings[1]["severity"] == "LOW"


def test_extract_findings_flushes_trailing_finding():
    findings = gr._extract_findings("[CRITICAL] last one\ntail line")
    assert len(findings) == 1
    assert findings[0]["severity"] == "CRITICAL"
    assert findings[0]["text"].endswith("tail line")


def test_extract_findings_ignores_unmarked_text():
    assert gr._extract_findings("no findings here\njust prose") == []


# ------------------------------------------------------ ollama review lane

def _openai_style_response(message: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"choices": [{"message": message}]}).encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    return resp


def test_call_ollama_returns_content():
    with patch.object(gr.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _openai_style_response({"content": "[LOW] nit"})
        assert gr._call_ollama("code", "f.py") == "[LOW] nit"


def test_call_ollama_carries_explicit_timeout():
    with patch.object(gr.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _openai_style_response({"content": "ok"})
        gr._call_ollama("code", "f.py")
    assert urlopen.call_args.kwargs.get("timeout") == 300


def test_call_ollama_reads_reasoning_when_content_empty():
    # deepseek-r1 can return empty content with the review in the reasoning
    # field — the documented CLAUDE.md guard for thinking models.
    with patch.object(gr.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _openai_style_response(
            {"content": "", "reasoning": "[HIGH] real finding"}
        )
        assert gr._call_ollama("code", "f.py") == "[HIGH] real finding"


def test_call_ollama_empty_response_is_failure_not_success():
    # An empty review must raise (landing in _review_file's errors dict),
    # never be recorded as a successful zero-finding review.
    with patch.object(gr.urllib.request, "urlopen") as urlopen:
        urlopen.return_value = _openai_style_response({"content": "   "})
        with pytest.raises(Exception):
            gr._call_ollama("code", "f.py")


# ------------------------------------------------------ parallel dispatch

def test_review_file_records_missing_keys_as_errors():
    with patch.object(gr, "_call_ollama", return_value="[LOW] x"):
        out = gr._review_file("f.py", "code", gemini_key=None, openai_key=None)
    assert out["errors"]["gemini-2.0-flash"] == "GEMINI_API_KEY not set"
    assert out["errors"]["gpt-4o"] == "OPENAI_API_KEY not set"
    # the local lane name tracks the configured Ollama review model — don't pin it
    assert list(out["results"].values()) == ["[LOW] x"]


def test_review_file_backend_exception_lands_in_errors():
    with patch.object(gr, "_call_ollama", side_effect=ValueError("empty response")):
        out = gr._review_file("f.py", "code", gemini_key=None, openai_key=None)
    assert "empty response" in out["errors"].values()
    assert out["results"] == {}
