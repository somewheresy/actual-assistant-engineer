"""Optional stdlib Windows helpers; no desktop capture or virtual MIDI driver.

screenshot(path, pid=None, hwnd=None) returns an absolute .bmp path (32-bit
uncompressed BMP). Existing files are never overwritten. PrintWindow may fail
or render black; neither is reported as success. No screen-region fallback.
MIDI sends only to an explicit port name (or AAE_MIDI_PORT); virtual sources
require a separately installed/configured loopback driver. No driver is installed.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import struct
import sys


def _dll(name):
    if sys.platform != 'win32':
        raise OSError('Windows native helpers require Windows')
    return C.WinDLL(name, use_last_error=True)


def _fn(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn


class MidiAPI:
    def __init__(self):
        dll = _dll('winmm')
        self.count = _fn(dll, 'midiOutGetNumDevs', W.UINT)
        self.caps = _fn(dll, 'midiOutGetDevCapsW', W.UINT, C.c_size_t, C.c_void_p, W.UINT)
        self._open = _fn(dll, 'midiOutOpen', W.UINT, C.POINTER(W.HANDLE), W.UINT, C.c_size_t, C.c_size_t, W.DWORD)
        self._send = _fn(dll, 'midiOutShortMsg', W.UINT, W.HANDLE, W.DWORD)
        self._close = _fn(dll, 'midiOutClose', W.UINT, W.HANDLE)

    @staticmethod
    def check(code):
        if code:
            raise OSError('WinMM MIDI error %s' % code)

    def ports(self):
        class Caps(C.Structure):
            _fields_ = [('mid', W.WORD), ('pid', W.WORD), ('version', W.DWORD), ('name', W.WCHAR * 32),
                        ('technology', W.WORD), ('voices', W.WORD), ('notes', W.WORD), ('channels', W.WORD), ('support', W.DWORD)]
        result = []
        for index in range(self.count()):
            caps = Caps()
            self.check(self.caps(index, C.byref(caps), C.sizeof(caps)))
            result.append({'id': index, 'name': caps.name, 'technology': caps.technology})
        return result

    def open(self, port):
        handle = W.HANDLE()
        self.check(self._open(C.byref(handle), port, 0, 0, 0))
        return handle

    def send(self, handle, message): self.check(self._send(handle, message))
    def close(self, handle): self.check(self._close(handle))


def midi_ports():
    return {'ports': MidiAPI().ports(), 'virtual_source_supported': False,
            'prerequisite': 'Live routing requires a separately installed/configured MIDI loopback driver; WinMM cannot create a virtual source.'}


def midi_send(message, port=None, *, api=None):
    packed = short_message(message)
    port = port if port is not None else os.environ.get('AAE_MIDI_PORT')
    if not port:
        raise ValueError('Select an explicit MIDI output name with --port or AAE_MIDI_PORT; no default synth')
    api = api or MidiAPI()
    matches = [p for p in api.ports() if p['name'] == port]
    if len(matches) != 1:
        raise ValueError('MIDI output name missing or ambiguous: %s' % port)
    handle = api.open(matches[0]['id'])
    try:
        api.send(handle, packed)
    finally:
        api.close(handle)
    return {'port': port, 'sent': message}


def _canonical(path):
    return os.path.normcase(os.path.realpath(path))


def screenshot(path, pid=None, hwnd=None, *, executable=None, api=None):
    """Capture one verified Live HWND to a NEW .bmp file; return absolute path."""
    path = Path(path).resolve()
    if path.suffix.lower() != '.bmp':
        raise ValueError('Windows screenshot path must end in .bmp (32-bit BMP)')
    if path.exists():
        raise FileExistsError(path)
    if executable is None:
        from . import live_app
        executable = live_app.bundle()
    executable = str(executable)
    api = api or CaptureAPI()
    matches = [(w, p) for w, p, exe in api.windows() if _canonical(exe) == _canonical(executable)
               and (pid is None or p == pid) and (hwnd is None or w == hwnd)]
    if len(matches) != 1:
        raise OSError('Live window missing, wrong PID/HWND, or ambiguous; select an exact Live PID/HWND')
    hwnd, pid = matches[0]
    api.verify(hwnd, pid, executable)
    width, height, pixels = api.capture(hwnd, pid, executable)
    if len(pixels) != width * height * 4 or not width or not height:
        raise OSError('invalid PrintWindow pixel buffer')
    if not any(pixels[0::4]) and not any(pixels[1::4]) and not any(pixels[2::4]):
        raise OSError('PrintWindow returned a black capture; no screenshot saved')
    header = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    with path.open('xb') as output:
        output.write(struct.pack('<2sIHHI', b'BM', 54 + len(pixels), 0, 0, 54) + header + pixels)
    return str(path)


class CaptureAPI:
    def __init__(self):
        u, g, k = _dll('user32'), _dll('gdi32'), _dll('kernel32')
        self.set_dpi = _fn(u, 'SetThreadDpiAwarenessContext', W.HANDLE, W.HANDLE)
        self.callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.enum = _fn(u, 'EnumWindows', W.BOOL, self.callback, W.LPARAM)
        self.visible = _fn(u, 'IsWindowVisible', W.BOOL, W.HWND)
        self.owner = _fn(u, 'GetWindow', W.HWND, W.HWND, W.UINT)
        self.pid = _fn(u, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
        self.open_process = _fn(k, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        self.query = _fn(k, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
        self.close_process = _fn(k, 'CloseHandle', W.BOOL, W.HANDLE)
        self.rect = _fn(u, 'GetWindowRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
        self.get_dc = _fn(u, 'GetWindowDC', W.HDC, W.HWND)
        self.release_dc = _fn(u, 'ReleaseDC', C.c_int, W.HWND, W.HDC)
        self.create_dc = _fn(g, 'CreateCompatibleDC', W.HDC, W.HDC)
        self.delete_dc = _fn(g, 'DeleteDC', W.BOOL, W.HDC)
        self.dib = _fn(g, 'CreateDIBSection', W.HBITMAP, W.HDC, C.c_void_p, W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD)
        self.select = _fn(g, 'SelectObject', W.HANDLE, W.HDC, W.HANDLE)
        self.delete_object = _fn(g, 'DeleteObject', W.BOOL, W.HANDLE)
        self.print_window = _fn(u, 'PrintWindow', W.BOOL, W.HWND, W.HDC, W.UINT)

    def identity(self, hwnd):
        pid = W.DWORD()
        if not self.pid(hwnd, C.byref(pid)):
            raise OSError('window no longer exists')
        process = self.open_process(0x1000, False, pid.value)
        if not process:
            raise OSError('cannot verify window process executable')
        try:
            size = W.DWORD(32768)
            name = C.create_unicode_buffer(size.value)
            if not self.query(process, 0, name, C.byref(size)):
                raise OSError('cannot query process executable')
            return pid.value, name.value
        finally:
            self.close_process(process)

    def windows(self):
        result = []
        def visit(hwnd, _):
            if self.visible(hwnd) and not self.owner(hwnd, 4):
                try:
                    pid, exe = self.identity(hwnd)
                    result.append((hwnd, pid, exe))
                except OSError:
                    pass  # Inaccessible foreign windows are never candidates.
            return True
        if not self.enum(self.callback(visit), 0):
            raise OSError('EnumWindows failed')
        return result

    def verify(self, hwnd, pid, executable):
        actual_pid, exe = self.identity(hwnd)
        if actual_pid != pid or _canonical(exe) != _canonical(executable):
            raise OSError('window PID/executable changed; refusing capture')

    def capture(self, hwnd, pid, executable):
        # Physical pixel dimensions, without changing the host process DPI mode.
        previous = self.set_dpi(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if not previous:
            raise OSError('cannot establish physical-pixel capture DPI context')
        try:
            return self._capture(hwnd, pid, executable)
        finally:
            self.set_dpi(previous)

    def _capture(self, hwnd, pid, executable):
        self.verify(hwnd, pid, executable)
        rect = W.RECT()
        if not self.rect(hwnd, C.byref(rect)):
            raise OSError('GetWindowRect failed')
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if not 0 < width <= 16384 or not 0 < height <= 16384:
            raise OSError('invalid window dimensions')
        dc = self.get_dc(hwnd)
        if not dc:
            raise OSError('GetWindowDC failed')
        memory = bitmap = previous = None
        try:
            memory = self.create_dc(dc)
            if not memory:
                raise OSError('CreateCompatibleDC failed')
            info = C.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0))
            bits = C.c_void_p()
            bitmap = self.dib(dc, info, 0, C.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise OSError('CreateDIBSection failed')
            previous = self.select(memory, bitmap)
            if not previous or previous == C.c_void_p(-1).value:
                previous = None
                raise OSError('SelectObject failed')
            C.memset(bits, 0, width * height * 4)
            self.verify(hwnd, pid, executable)
            if not self.print_window(hwnd, memory, 2):  # PW_RENDERFULLCONTENT, HWND only
                raise OSError('PrintWindow failed; no desktop fallback')
            self.verify(hwnd, pid, executable)
            return width, height, C.string_at(bits, width * height * 4)
        finally:
            if previous: self.select(memory, previous)
            if bitmap: self.delete_object(bitmap)
            if memory: self.delete_dc(memory)
            self.release_dc(hwnd, dc)


def short_message(text):
    try:
        data = bytes.fromhex(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('MIDI must be space-separated hexadecimal bytes') from exc
    if not data:
        raise ValueError('empty MIDI message')
    status = data[0]
    sizes = {0xF1: 2, 0xF2: 3, 0xF3: 2, 0xF6: 1, 0xF8: 1, 0xFA: 1, 0xFB: 1, 0xFC: 1, 0xFE: 1, 0xFF: 1}
    size = (2 if status >> 4 in (12, 13) else 3) if 0x80 <= status <= 0xEF else sizes.get(status)
    if size != len(data) or any(b > 127 for b in data[1:]):
        raise ValueError('invalid MIDI short message; SysEx and running status are unsupported')
    return int.from_bytes(data, 'little')
