import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows native discovery and filesystem contracts"
)

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
V = importlib.import_module("actual-assistant-engineer.live_vst")
P = importlib.import_module("actual-assistant-engineer.platform_paths")
H = importlib.import_module("actual-assistant-engineer.vst_host")


def test_windows_vst3_catalog_recurses_vendor_and_user_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CommonProgramW6432", str(tmp_path / "Common"))
    monkeypatch.setenv("COMMONPROGRAMFILES", str(tmp_path / "Common"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    paths = [
        tmp_path / "Common/VST3/Vendor/Synth.vst3",
        tmp_path / "Local/Programs/Common/VST3/User.vst3",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert {p["path"] for p in V.catalog()} == {str(p) for p in paths}


def test_explicit_custom_vst_and_preset_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("AAE_VST3_PATHS", str(tmp_path / "Custom Plug-ins"))
    monkeypatch.setenv("AAE_PRESET_PATHS", str(tmp_path / "Custom Presets"))
    assert tmp_path / "Custom Plug-ins" in P.vst3_dirs()
    assert tmp_path / "Custom Presets" in P.preset_dirs()


def test_windows_presets_include_standard_system_and_redirected_user(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "Data"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(P, "documents", lambda: tmp_path / "Redirected")
    preset = tmp_path / "Redirected/VST3 Presets/ExampleSynth/Pad.vstpreset"
    preset.parent.mkdir(parents=True)
    preset.touch()
    assert V.presets("ExampleSynth")["presets"][0]["path"] == str(preset)
    assert tmp_path / "Data/VST3 Presets" in P.preset_dirs()
    assert tmp_path / "Roaming/VST3 Presets" in P.preset_dirs()
    assert len(V.presets("ExampleSynth")["presets"]) == 1


def test_parameter_cache_is_profile_scoped_and_filename_safe(tmp_path, monkeypatch):
    plugin = tmp_path / "Synth.vst3"
    plugin.touch()
    monkeypatch.setattr(V, "_bundle", lambda name: str(plugin))
    calls = []

    def host(*args, **kwargs):
        calls.append(args)
        return [{"name": "Cutoff", "index": 0}]

    monkeypatch.setattr(V, "_host", host)
    for profile in ("alpha", "beta"):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / profile))
        assert V.params("Synth", bundle_plugin="Part:1/Layer\\A", limit=0)[
            "params"
        ] == [{"name": "Cutoff", "index": 0}]
        assert (
            len(
                list(
                    (
                        tmp_path
                        / profile
                        / "plugin-data/actual-assistant-engineer/cache/vst"
                    ).glob("*.json")
                )
            )
            == 1
        )
        V.params("Synth", bundle_plugin="Part:1/Layer\\A")
    assert len(calls) == 2


def pe(path, machine):
    import struct

    data = bytearray(134)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_pe_architecture_reads_binary_not_os_name(tmp_path):
    assert H.binary_architecture(pe(tmp_path / "x64.vst3", 0x8664)) == "x86_64"
    assert H.binary_architecture(pe(tmp_path / "arm.vst3", 0xAA64)) == "aarch64"
    bundle = tmp_path / "Bundled.vst3"
    pe(bundle / "Contents/x86_64-win/Bundled.vst3", 0x8664)
    assert H.binary_architecture(bundle) == "x86_64"
    broken = tmp_path / "broken.vst3"
    broken.write_bytes(b"bad")
    with pytest.raises(ValueError, match="PE"):
        H.binary_architecture(broken)


def test_arm64_hermes_selects_x64_interpreter_for_x64_plugin(tmp_path, monkeypatch):
    import shutil
    import importlib.util

    arm_python = pe(tmp_path / "python-arm.exe", 0xAA64)
    plugin = pe(tmp_path / "Synth.vst3", 0x8664)
    monkeypatch.setattr(sys, "executable", str(arm_python))
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(shutil, "which", lambda name: "uv.exe")
    command = V._python(str(plugin))
    assert command[:2] == ["uv.exe", "run"]
    assert command[command.index("--python") + 1] == "cpython-3.11-windows-x86_64-none"
    assert "--no-project" in command and "--isolated" in command
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(V.VstError, match="x86_64"):
        V._python(str(plugin))


def test_host_selects_plugin_architecture_and_reports_timeout(monkeypatch):
    selected = []
    monkeypatch.setattr(
        V, "_python", lambda bundle=None: selected.append(bundle) or ["python.exe"]
    )

    def run(*args, **kwargs):
        raise V.subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(V.subprocess, "run", run)
    with pytest.raises(V.VstError, match="timed out"):
        V._host("params", "Synth.vst3", timeout=1)
    assert selected == ["Synth.vst3"]


def test_host_probe_reports_actual_interpreter_and_pedalboard(monkeypatch, capsys):
    import json
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules, "pedalboard", SimpleNamespace(__version__="test-wheel")
    )
    monkeypatch.setattr(sys, "argv", ["vst_host.py", "probe"])
    H.main()
    report = json.loads(capsys.readouterr().out)
    assert report["pedalboard"] == "test-wheel"
    assert report["python"] == sys.executable
    assert report["architecture"] == H.binary_architecture(sys.executable)
