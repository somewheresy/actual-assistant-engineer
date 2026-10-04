"""Opt-in real Live qualification on task-owned Sets; never run automatically."""
import importlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin'))
S = importlib.import_module('actual-assistant-engineer.live_sets')
C = importlib.import_module('actual-assistant-engineer.live_client')
A = importlib.import_module('actual-assistant-engineer.live_arrangement')

if len(sys.argv) != 3:
    raise SystemExit('usage: qualify-windows-live.py <new unique Set name> <output directory>')
name, directory = sys.argv[1:]
report = []
def record(step, result):
    report.append({'step': step, 'result': result})
    print(json.dumps(report[-1]), flush=True)

record('before', S.info())
record('new', S.new(name, directory, on_unsaved='cancel'))
res = C.batch([
    {'op':'create_track','kind':'midi','name':'Hermes IT Roundtrip'},
    {'op':'create_clip','track':'Hermes IT Roundtrip','slot':0,'length':4,'name':'Test clip'},
    {'op':'add_notes','track':'Hermes IT Roundtrip','slot':0,'notes':[[60,0,1,90],[64,1,1,85]]},
    {'op':'mixer','tracks':{'Hermes IT Roundtrip':{'volume_db':-9,'pan':0.25}}},
])
assert res['ok'], res
record('edits',res)
record('save', S.save())
record('save_as', S.save_as(name+' Copy',directory))
res=C.batch([{'op':'get_notes','track':'Hermes IT Roundtrip','slot':0}])
assert res['ok'] and len(res['results'][0]['notes']) == 2, res
record('notes_after_save_as',res)
record('arrangement_automation', A.write('Hermes IT Roundtrip', 'volume', [[0, '-9 dB'], [4, '-15 dB']]))
readback = A.read('Hermes IT Roundtrip', 'volume')
assert readback['automated'] and len(readback['points']) >= 2, readback
record('arrangement_readback', readback)
record('final',S.info())
output=Path(directory)/(name+'-verification.json')
output.write_text(json.dumps(report,indent=2),encoding='utf-8')
print('REPORT',output)
