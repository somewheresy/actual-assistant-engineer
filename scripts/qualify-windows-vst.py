"""Explicit real-VST qualification; uses a new dedicated Set and exact browser path."""
import importlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'))
S=importlib.import_module('actual-assistant-engineer.live_sets')
C=importlib.import_module('actual-assistant-engineer.live_client')
V=importlib.import_module('actual-assistant-engineer.live_vst')
name,directory=sys.argv[1:3]
report=[]
def record(step,result):
    report.append({'step':step,'result':result});print(json.dumps(report[-1]),flush=True)
record('new',S.new(name,directory,on_unsaved='cancel'))
result=C.batch([{'op':'create_track','kind':'midi','name':'Hermes IT VST'}, {'op':'browser_load','track':'Hermes IT VST','root':'plugins','path':['Michael Willis and Rob vd Berg','Dragonfly Hall Reverb']}])
assert result['ok'],result
record('load_vst',result)
record('initial_params',C.batch([{'op':'device_params','track':'Hermes IT VST','device':0}]))
record('save',S.save())
record('offline_params',V.params('Dragonfly Hall Reverb',limit=1000))
record('state_roundtrip',V.load_state('Hermes IT VST',0,values={'Dry Level':0.25}))
after=C.batch([{'op':'device_params','track':'Hermes IT VST','device':0}])
record('after_state',after)
actual=next(p for p in after['results'][0]['params'] if p['name']=='Dry Level')['value']
assert abs(actual-0.25)<0.00001, ('VST state did not reach Live',actual)
record('expose',V.expose('Hermes IT VST',0,['Dry Level']))
record('after_expose',C.batch([{'op':'device_params','track':'Hermes IT VST','device':0}]))
record('save_final',S.save())
p=Path(directory)/(name+'-verification.json');p.write_text(json.dumps(report,indent=2),encoding='utf-8');print('REPORT',p)
