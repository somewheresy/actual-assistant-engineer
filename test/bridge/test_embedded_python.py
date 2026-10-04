"""Exercise the actual Windows listener without ctypes, as Live embeds Python."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows Live runtime')
def test_private_listener_without_ctypes(tmp_path):
    module = Path(__file__).parents[2] / 'plugin/actual-assistant-engineer/live/Hermes/transport.py'
    program = '''
import sys,importlib.util,importlib.abc
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='ctypes' or fullname.startswith('ctypes.'):
   raise ModuleNotFoundError("Live does not bundle ctypes")
sys.meta_path.insert(0,Block())
spec=importlib.util.spec_from_file_location('embedded_transport',sys.argv[1])
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
listener=t.Listener(sys.argv[2])
e=t.read_endpoint(sys.argv[2])
assert e['host']=='127.0.0.1' and e['port']>0
assert len(e['token'])==64
try:
 t.Listener(sys.argv[2])
 raise AssertionError('second listener stole ownership')
except OSError:
 pass
listener.close()
assert not __import__('os').path.exists(t.os.path.join(sys.argv[2],'live.endpoint.json'))
print('private listener without ctypes: ok')
'''
    result = subprocess.run([sys.executable, '-c', program, str(module), str(tmp_path/'private')],capture_output=True,text=True,timeout=90)
    assert result.returncode == 0, result.stderr
    assert 'without ctypes: ok' in result.stdout
