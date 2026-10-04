"""Offline VST3 host (pedalboard): list a plug-in's parameters and produce its state.

Runs in its own process via `uv run --with pedalboard python vst_host.py ...` so the Hermes
plugin keeps no heavy dependencies. Output is JSON on stdout.

  params <plugin.vst3> [--plugin NAME]                       -> [{index, name, value, text}]
  state  <plugin.vst3> [--plugin NAME] [--preset FILE.vstpreset] [--set NAME=VALUE ...]
                                                             -> {processor_hex, controller_hex}

State is returned as the two VST3 blobs Live stores in a Set (ProcessorState and
ControllerState), unwrapped from JUCE's VST3PluginState container.
"""

import argparse
import base64
import json
import re
import sys
import struct
from pathlib import Path


def windows_binary(path):
    """Resolve the module inside a standard Windows VST3 bundle."""
    path = Path(path)
    if path.is_dir():
        binaries = sorted(path.glob("Contents/*-win/*.vst3"))
        # Prefer x64 when a bundle supplies several architectures (Prism compatible).
        binaries.sort(key=lambda p: "x86_64-win" not in p.parts)
        if not binaries:
            raise ValueError("no Windows PE binary found in VST3 bundle: %s" % path)
        path = binaries[0]
    return path


def binary_architecture(path):
    """Read PE machine type without loading native code."""
    path = windows_binary(path)
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("not a Windows PE binary: %s" % path)
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        pe = stream.read(6)
    if len(pe) != 6 or pe[:4] != b"PE\0\0":
        raise ValueError("invalid Windows PE header: %s" % path)
    machine = struct.unpack_from("<H", pe, 4)[0]
    arch = {0x8664: "x86_64", 0xAA64: "aarch64", 0x14C: "x86"}.get(machine)
    if not arch:
        raise ValueError("unsupported PE machine 0x%x in %s" % (machine, path))
    return arch


def load(path, name):
    import pedalboard

    if sys.platform == "win32":
        path = str(windows_binary(path))
    return pedalboard.load_plugin(path, plugin_name=name) if name else pedalboard.load_plugin(path)


def flush_state(plugin):
    """Apply queued VST3 processor changes before serializing state.

    Processing is offline/in-memory only; no audio device is opened or MIDI note
    sent. Some processors keep old values in raw_state until a process block.
    """
    if plugin.is_instrument:
        plugin.process([], duration=0.01, sample_rate=44100, num_channels=2, reset=False)
    else:
        import numpy as np
        plugin.process(np.zeros((2, 64), dtype=np.float32), sample_rate=44100, reset=False)


def vst3_blobs(raw):
    """JUCE wraps state as copyXmlToBinary(<VST3PluginState><IComponent>b64</IComponent>
    <IEditController>b64</IEditController></VST3PluginState>); extract both blobs."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    def grab(tag):
        m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), text)
        if not m:
            return b""
        b64 = m.group(1).strip()
        # JUCE stores the size as "<n>." before the base64 payload.
        if "." in b64[:12]:
            b64 = b64.split(".", 1)[1]
        return _juce_b64(b64)
    return grab("IComponent"), grab("IEditController")


JUCE_B64 = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"


def _juce_b64(s):
    """Decode JUCE MemoryBlock::toBase64Encoding (6-bit groups, LSB first, custom alphabet)."""
    out = bytearray((len(s) * 6 + 7) // 8)
    bit = 0
    for ch in s:
        v = JUCE_B64.find(ch)
        if v < 0:
            continue
        for i in range(6):
            if v & (1 << i):
                out[bit // 8] |= 1 << (bit % 8)
            bit += 1
    return bytes(out[: bit // 8])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["params", "state", "probe"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--plugin")
    ap.add_argument("--preset")
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args()
    if a.command == "probe":
        import pedalboard
        import platform
        json.dump({"python": sys.executable, "pedalboard": pedalboard.__version__,
                   "architecture": binary_architecture(sys.executable) if sys.platform == "win32" else platform.machine()}, sys.stdout)
        return
    if not a.path:
        ap.error("path is required for params/state")
    p = load(a.path, a.plugin)
    if a.command == "params":
        out = []
        for i, (key, param) in enumerate(p.parameters.items()):
            try:
                text = str(param.string_value)
            except Exception:
                text = None
            out.append({"index": i, "name": getattr(param, "name", key), "key": key, "value": param.raw_value, "text": text})
        json.dump(out, sys.stdout)
        return
    if a.preset:
        p.load_preset(a.preset)
    for kv in a.set:
        k, v = kv.split("=", 1)
        param = p.parameters[k]
        param.raw_value = float(v)
    if a.set or a.preset:
        flush_state(p)
    proc, ctrl = vst3_blobs(p.raw_state)
    if not proc:
        raise SystemExit("could not extract VST3 component state from the host's state")
    json.dump({"processor_hex": proc.hex().upper(), "controller_hex": ctrl.hex().upper(), "name": p.name}, sys.stdout)


if __name__ == "__main__":
    main()
