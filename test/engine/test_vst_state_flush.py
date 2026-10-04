import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).parents[2]/'plugin'))
V=importlib.import_module('actual-assistant-engineer.vst_host')


def test_offline_state_flush_processes_silence_without_reset(monkeypatch):
    calls=[]
    audio=object()
    monkeypatch.setitem(sys.modules,'numpy',SimpleNamespace(zeros=lambda shape,dtype:audio,float32=object()))
    plugin=SimpleNamespace(is_instrument=False,process=lambda *a,**kw:calls.append((a,kw)))
    V.flush_state(plugin)
    assert calls==[((audio,),{'sample_rate':44100,'reset':False})]


def test_instrument_state_flush_sends_no_notes():
    calls=[]
    plugin=SimpleNamespace(is_instrument=True,process=lambda *a,**kw:calls.append((a,kw)))
    V.flush_state(plugin)
    assert calls==[(([],),{'duration':0.01,'sample_rate':44100,'num_channels':2,'reset':False})]
