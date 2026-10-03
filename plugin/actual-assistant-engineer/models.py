"""Steer locally hosted models to iterative DAW work.

Whole-song building takes dozens of tool calls and long, dense outputs; local models are too
slow for it but handle scoped requests well. A model counts as local when its provider is a
local runtime or its endpoint is on this machine. Detection uses only what Hermes reports and
its config, never model names.
"""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

LOCAL_PROVIDERS = {"ollama", "llamacpp", "llama.cpp", "llama-cpp", "lmstudio", "lm-studio", "vllm", "local", "mlx"}

STEERING = (
    "You are running on a locally hosted model ({model}). Take on iterative, scoped requests in the "
    "producer's Set: cleanup, labeling and coloring, gain staging, fixing production errors and artifacts "
    "(stray or overlapping notes, clipping levels, disabled or misrouted devices, open sends), and small "
    "edits to existing parts, devices, and automation. Do not compose or build a whole song or full "
    "arrangement from scratch: say that whole-song builds need a frontier model (the producer can switch "
    "with /model) and offer a scoped first step instead."
)


def is_loopback(url):
    host = (urlparse(url).hostname or "").lower() if url else ""
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback or ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def is_local(provider, base_url):
    return (provider or "").lower() in LOCAL_PROVIDERS or is_loopback(base_url)


def configured_model():
    """(provider, base_url, model) from the active Hermes config, best effort."""
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    try:
        import yaml

        cfg = yaml.safe_load((home / "config.yaml").read_text()) or {}
    except Exception:
        return None, None, None
    m = cfg.get("model") or {}
    return m.get("provider"), m.get("base_url"), m.get("default")


def steering_section(info):
    """System prompt section: steering text for local models, nothing otherwise."""
    provider, base_url, _ = configured_model()
    session_provider = (info or {}).get("provider") or provider
    # The configured endpoint only describes this session when it uses the configured provider.
    url = base_url if (session_provider or "") == (provider or "") else None
    if not is_local(session_provider, url):
        return ""
    return STEERING.format(model=(info or {}).get("model") or "local model")
