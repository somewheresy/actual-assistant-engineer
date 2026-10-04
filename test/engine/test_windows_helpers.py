import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('aae_windows_helpers_test', ROOT / 'plugin/actual-assistant-engineer/windows_helpers.py')


def helpers():
    assert Path(SPEC.origin).exists(), 'Windows helpers not implemented'
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


class Capture:
    def __init__(self): self.captured = []
    def windows(self): return [(77, 123, 'C:/Live.exe')]
    def verify(self, hwnd, pid, exe):
        if (hwnd, pid, exe) != (77, 123, 'C:/Live.exe'): raise OSError('wrong PID or executable')
    def capture(self, hwnd, pid, exe):
        self.verify(hwnd, pid, exe)
        self.captured.append(hwnd)
        return 1, 1, b'\x01\x02\x03\x00'


def test_target_capture_and_wrong_pid(tmp_path):
    h = helpers()
    api = Capture()
    path = tmp_path / 'live.bmp'
    with pytest.raises(OSError): h.screenshot(path, pid=999, executable='C:/Live.exe', api=api)
    assert not api.captured
    assert h.screenshot(path, executable='C:/Live.exe', api=api) == str(path.resolve())
    assert path.read_bytes()[:2] == b'BM'
    assert api.captured == [77]


def test_black_capture_is_failure(tmp_path):
    h = helpers()
    api = Capture()
    api.capture = lambda *args: (1, 1, b'\x00\x00\x00\xff')
    with pytest.raises(OSError, match='black'): h.screenshot(tmp_path / 'black.bmp', executable='C:/Live.exe', api=api)
    assert not (tmp_path / 'black.bmp').exists()


def test_printwindow_failure_releases_gdi():
    h = helpers()
    api = h.CaptureAPI.__new__(h.CaptureAPI)
    events = []
    pixels = h.C.create_string_buffer(4)
    api.set_dpi = lambda value: events.append(('dpi', value)) or -1
    api.verify = lambda *args: None
    def rect(hwnd, ptr):
        ptr._obj.right, ptr._obj.bottom = 1, 1
        return True
    def dib(dc, info, mode, ptr, section, offset):
        ptr._obj.value = h.C.addressof(pixels)
        return 30
    api.rect, api.dib = rect, dib
    api.get_dc = lambda hwnd: 10
    api.create_dc = lambda dc: 20
    api.select = lambda dc, obj: events.append(('select', obj)) or 40
    api.print_window = lambda *args: False
    api.delete_object = lambda obj: events.append(('bitmap', obj))
    api.delete_dc = lambda dc: events.append(('dc', dc))
    api.release_dc = lambda hwnd, dc: events.append(('release', dc))
    with pytest.raises(OSError, match='PrintWindow failed'): api.capture(77, 123, 'Live.exe')
    assert events == [('dpi', -4), ('select', 30), ('select', 40), ('bitmap', 30), ('dc', 20), ('release', 10), ('dpi', -1)]


def test_cli_and_developer_dispatch(monkeypatch, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location('aae_helpers_developer', ROOT / 'scripts/developer.py')
    dev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev)
    h = dev.plugin_module('windows_helpers')
    monkeypatch.setattr(h, 'screenshot', lambda path, **kw: str(path))
    assert dev.dispatch({'action': 'screenshot', 'path': 'live.bmp'}) == 'live.bmp'
    import argparse
    parser = argparse.ArgumentParser()
    dev.plugin_module('cli').configure(parser)
    for argv in (['screenshot', 'live.bmp'], ['midi-ports'], ['midi-send', '90 3c 00', '--port', 'Loop']):
        args = parser.parse_args(argv)
        assert callable(args.run)
    args = parser.parse_args(['screenshot', 'live.bmp'])
    assert args.run(args) == 0
    assert 'live.bmp' in capsys.readouterr().out


class Midi:
    def __init__(self): self.closed = []
    def ports(self): return [{'id': 0, 'name': 'Microsoft GS Wavetable Synth'}, {'id': 1, 'name': 'Loop'}]
    def open(self, port): return port + 100
    def send(self, handle, message): raise OSError('send failed')
    def close(self, handle): self.closed.append(handle)


def test_explicit_midi_and_cleanup(monkeypatch):
    h = helpers()
    api = Midi()
    monkeypatch.delenv('AAE_MIDI_PORT', raising=False)
    with pytest.raises(ValueError): h.midi_send('90 3c 00', api=api)
    with pytest.raises(OSError): h.midi_send('90 3c 00', port='Loop', api=api)
    assert api.closed == [101]
    api.ports = lambda: [{'id': 1, 'name': 'Loop'}, {'id': 2, 'name': 'Loop'}]
    with pytest.raises(ValueError): h.midi_send('90 3c 00', port='Loop', api=api)


def test_short_message_validation():
    h = helpers()
    assert h.short_message('90 3c 00') == 0x003c90
    assert h.short_message('c0 01') == 0x01c0
    for message in ('', '3c 00', '90 80 00', '90 3c', 'c0 01 02', 'f0 01 f7', 'f9', '90 zz 00'):
        with pytest.raises(ValueError):
            h.short_message(message)
