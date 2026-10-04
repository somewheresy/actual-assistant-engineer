import importlib
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[2]/'plugin'))
V=importlib.import_module('actual-assistant-engineer.live_vst')


def prepare(monkeypatch,value):
    monkeypatch.setattr(V,'_device_index',lambda *a:0)
    monkeypatch.setattr(V,'_bridge',lambda op:{'devices':[{'name':'Example'}]} if op['op']=='device_tree' else {'params':[{'name':'Dry','value':value,'min':0,'max':1}]})
    monkeypatch.setattr(V,'params',lambda *a,**kw:{'params':[{'name':'Dry','key':'dry','index':0}]})
    monkeypatch.setattr(V,'_bundle',lambda name:'Example.vst3')
    monkeypatch.setattr(V,'_host',lambda *a,**kw:{'processor_hex':'00','controller_hex':''})
    monkeypatch.setattr(V,'_edit_set',lambda *a:None)


def test_load_state_refuses_false_success_when_live_keeps_old_value(monkeypatch):
    prepare(monkeypatch,0.8)
    with pytest.raises(V.VstError,match='read-back'):
        V.load_state('Track',0,values={'Dry':0.25})


def test_load_state_reports_verified_values(monkeypatch):
    prepare(monkeypatch,0.25)
    result=V.load_state('Track',0,values={'Dry':0.25})
    assert result['verified_parameters']=={'Dry':0.25}
