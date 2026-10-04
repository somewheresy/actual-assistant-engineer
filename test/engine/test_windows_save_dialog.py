import importlib
import sys
from types import SimpleNamespace
from test_windows_sets import windows_module
import pytest


def test_filename_change_notifies_combo_and_verifies_readback(monkeypatch):
    W=windows_module()
    calls=[]
    state={'value':''}
    class Value:
        @property
        def CurrentValue(self): return state['value']
    edit=SimpleNamespace(handle=10, element_info=SimpleNamespace(process_id=42), iface_value=Value())
    def send(h,m,w,l,*args):
        calls.append((h,m,w,l))
        if m==12: state['value']=__import__('ctypes').wstring_at(l)
    gui=SimpleNamespace(SendMessageTimeout=send,PostMessage=send,GetParent=lambda h:{10:11,11:12}[h],GetDlgCtrlID=lambda h:1001 if h==10 else 0)
    monkeypatch.setitem(sys.modules,'win32gui',gui)
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=lambda h:(1,42)))
    W.set_filename(edit,'C:\\Tests\\New.als',42)
    assert state['value']=='C:\\Tests\\New.als'
    assert any(h==11 and m==273 for h,m,w,l in calls)
    assert any(h==12 and m==273 for h,m,w,l in calls)


def test_filename_refuses_foreign_native_control(monkeypatch):
    W=windows_module()
    edit=SimpleNamespace(handle=10, element_info=SimpleNamespace(process_id=42))
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(GetParent=lambda h:11))
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=lambda h:(1,99)))
    with pytest.raises(W.UIError,match='another process'):
        W.set_filename(edit,'C:\\Tests\\New.als',42)
