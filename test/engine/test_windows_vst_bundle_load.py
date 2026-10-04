import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).parents[2]/'plugin'))
V=importlib.import_module('actual-assistant-engineer.vst_host')


def test_windows_offline_host_loads_bundle_binary_not_directory(tmp_path,monkeypatch):
    bundle=tmp_path/'Example.vst3'
    binary=bundle/'Contents/x86_64-win/Example.vst3'
    binary.parent.mkdir(parents=True);binary.write_bytes(b'MZ')
    calls=[]
    monkeypatch.setattr(V.sys,'platform','win32')
    monkeypatch.setitem(sys.modules,'pedalboard',SimpleNamespace(load_plugin=lambda path,**kw:calls.append((path,kw)) or 'loaded'))
    assert V.load(str(bundle),None)=='loaded'
    assert calls==[(str(binary),{})]
