"""Local-model detection and steering: endpoint/provider based, never model names."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
M = importlib.import_module("actual-assistant-engineer.models")


def test_local_endpoints_and_providers():
    for url in ("http://127.0.0.1:8080/v1", "http://localhost:11434", "http://[::1]:1234/v1", "http://studio.local:8000", "http://0.0.0.0:5000"):
        assert M.is_loopback(url), url
    for url in ("https://api.actual.inc/v1", "https://api.anthropic.com", "http://10.0.0.5:8080", "", None):
        assert not M.is_loopback(url), url
    assert M.is_local("ollama", None) and M.is_local("lmstudio", "") and M.is_local("actual", "http://127.0.0.1:8080/v1")
    assert not M.is_local("actual", "https://api.actual.inc/v1") and not M.is_local("anthropic", "https://api.anthropic.com")


def test_steering_only_for_local_sessions(monkeypatch):
    monkeypatch.setattr(M, "configured_model", lambda: ("actual", "http://127.0.0.1:8080/v1", "qwen"))
    text = M.steering_section({"provider": "actual", "model": "qwen3-27b"})
    assert "qwen3-27b" in text and "Do not compose or build a whole song" in text
    # A session switched to a hosted provider isn't judged by the configured local endpoint.
    assert M.steering_section({"provider": "anthropic", "model": "claude-opus-5-5"}) == ""
    monkeypatch.setattr(M, "configured_model", lambda: ("anthropic", "https://api.anthropic.com", "claude-opus-5-5"))
    assert M.steering_section({"provider": "anthropic"}) == ""
    assert M.steering_section({"provider": "ollama", "model": "llama"}) != ""
