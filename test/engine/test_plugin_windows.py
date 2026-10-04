"""Platform registration and tool dispatch contracts (no Live needed)."""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2] / 'plugin'))
P = importlib.import_module('actual-assistant-engineer')


def test_windows_registers_all_tools_and_setup_without_live(monkeypatch):
    monkeypatch.setattr(P.platform, 'system', lambda: 'Windows')
    tools, commands = [], []
    ctx = SimpleNamespace(
        register_tool=lambda **kw: tools.append(kw),
        register_system_prompt_section=lambda *a, **kw: None,
        register_cli_command=lambda *a, **kw: commands.append(a),
        register_skill=lambda *a, **kw: None,
    )
    P.register(ctx)
    assert {t['name'] for t in tools} == set(P.SCHEMAS)
    assert commands[0][0] == 'assistant-engineer'


def test_new_set_preserves_name_directory_and_unsaved_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(P.live_sets, 'new', lambda *a, **kw: calls.append((a, kw)) or {})
    P.live_set({'action': 'new', 'name': 'Windows Song', 'directory': 'C:/Music', 'on_unsaved': 'cancel'})
    assert calls == [(('Windows Song', 'C:/Music', 'cancel'), {})]


def test_vst_dispatch_does_not_shadow_implementation(monkeypatch):
    module = importlib.import_module('actual-assistant-engineer.live_vst')
    monkeypatch.setattr(module, 'catalog', lambda query=None: [{'name': 'test synth'}])
    import json
    assert json.loads(P.live_vst({'action': 'catalog'}))['plugins'] == [{'name': 'test synth'}]
