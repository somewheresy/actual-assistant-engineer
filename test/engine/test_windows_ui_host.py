"""The ARM64 Hermes process may use a separate x64 UIA worker."""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2] / 'plugin'))
S = importlib.import_module('actual-assistant-engineer.live_sets')


def test_windows_uia_falls_back_to_isolated_worker(monkeypatch):
    monkeypatch.setattr(S, '_windows_ui', lambda: (_ for _ in ()).throw(ImportError('pywin32 unavailable')))
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'result': {'name': 'Song', 'identity': [5, 6, 7], 'dialogs': [{'text': 'Save changes?', 'buttons': ['Save', "Don't Save", 'Cancel']}]}}), stderr='')
    monkeypatch.setattr(S.subprocess, 'run', run)
    monkeypatch.setattr(S.shutil, 'which', lambda name: 'C:/tools/uv.exe')
    monkeypatch.setattr(S.live_app, 'bundle', lambda: Path('C:/Live/Ableton Live 12.exe'))
    state = S._win_call('snapshot')
    assert state.name == 'Song'
    assert state.identity == (5, 6, 7)
    assert state.dialogs[0].text == 'Save changes?'
    cmd, kw = calls[0]
    assert 'cpython-3.11-windows-x86_64-none' in cmd
    assert 'pywinauto' in cmd
    assert json.loads(kw['input'])['method'] == 'snapshot'
    assert kw['timeout'] > 0


def test_worker_failure_never_looks_like_no_dialog(monkeypatch):
    monkeypatch.setattr(S, '_windows_ui', lambda: (_ for _ in ()).throw(ImportError('unavailable')))
    monkeypatch.setattr(S.shutil, 'which', lambda name: 'uv')
    monkeypatch.setattr(S.live_app, 'bundle', lambda: Path('C:/Live/Ableton Live 12.exe'))
    monkeypatch.setattr(S.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({'error': 'UIA inaccessible', 'kind': 'UIError'}), stderr=''))
    import pytest
    with pytest.raises(S.SetError, match='UIA inaccessible'):
        S._win_call('snapshot')
