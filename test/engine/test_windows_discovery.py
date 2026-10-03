"""Windows discovery/setup/host contracts; no installed Live required."""

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows native discovery and filesystem contracts"
)

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
APP = importlib.import_module("actual-assistant-engineer.live_app")


def test_configured_executable_and_real_resource_layout(tmp_path, monkeypatch):
    exe = tmp_path / "Custom Live" / "Program" / "Ableton Live 12 Intro.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    template = exe.parent.parent / "Resources/Builtin/Templates/DefaultLiveSet.als"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"set")
    monkeypatch.setenv("AAE_LIVE_PATH", str(exe))
    APP.bundle.cache_clear()
    assert APP.bundle() == exe
    assert APP.default_set() == template


def test_installed_versions_are_numeric_across_windows_roots(tmp_path, monkeypatch):
    monkeypatch.delenv("AAE_LIVE_PATH", raising=False)
    monkeypatch.setattr(
        APP.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": "[]", "returncode": 0})(),
    )
    expected = None
    for env, version, edition in [
        ("ProgramData", "12.9", "Suite"),
        ("ProgramFiles", "12.10", "Intro"),
        ("ProgramW6432", "11.99", "Standard"),
    ]:
        base = tmp_path / env
        monkeypatch.setenv(env, str(base))
        exe = (
            base
            / "Ableton"
            / ("Live " + version + " " + edition)
            / "Program"
            / ("Ableton Live " + version + " " + edition + ".exe")
        )
        exe.parent.mkdir(parents=True)
        exe.touch()
        if version == "12.10":
            expected = exe
    APP.bundle.cache_clear()
    assert APP.bundle() == expected


def test_running_custom_install_is_preferred_and_helpers_ignored(tmp_path, monkeypatch):
    import json

    exe = tmp_path / "Portable" / "Program" / "Ableton Live 11 Lite.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.delenv("AAE_LIVE_PATH", raising=False)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [
                        {"ExecutablePath": str(exe.with_name("Ableton Index.exe"))},
                        {"ExecutablePath": str(exe)},
                    ]
                ),
            },
        )()

    monkeypatch.setattr(APP.subprocess, "run", run)
    APP.bundle.cache_clear()
    assert APP.bundle() == exe
    assert "Get-CimInstance" in calls[0][-1]


def test_missing_template_and_invalid_override_are_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("AAE_LIVE_PATH", str(tmp_path / "missing.exe"))
    APP.bundle.cache_clear()
    with pytest.raises(FileNotFoundError, match="AAE_LIVE_PATH"):
        APP.bundle()
    exe = tmp_path / "Program/Ableton Live 12 Standard.exe"
    exe.parent.mkdir()
    exe.touch()
    monkeypatch.setenv("AAE_LIVE_PATH", str(exe))
    with pytest.raises(FileNotFoundError, match="template"):
        APP.default_set()


def test_discovery_does_not_cache_an_outdated_application(tmp_path, monkeypatch):
    for name in ("Ableton Live 11 Lite.exe", "Ableton Live 12 Standard.exe"):
        path = tmp_path / name
        path.touch()
        monkeypatch.setenv("AAE_LIVE_PATH", str(path))
        assert APP.bundle() == path


def test_stopped_custom_install_from_standard_uninstall_metadata(tmp_path, monkeypatch):
    import types

    exe = tmp_path / "Audio Apps/Live/Program/Ableton Live 12 Suite.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()

    class Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def openkey(root, name, *args):
        return Key(name)

    def enumkey(key, index):
        if index:
            raise OSError("end")
        return "Installed Product"

    def query(key, name):
        return {
            "DisplayName": "Ableton Live 12 Suite",
            "InstallLocation": str(exe.parent.parent),
        }[name], 1

    registry = types.SimpleNamespace(
        HKEY_LOCAL_MACHINE=1,
        HKEY_CURRENT_USER=2,
        KEY_READ=1,
        KEY_WOW64_64KEY=2,
        KEY_WOW64_32KEY=4,
        OpenKey=openkey,
        EnumKey=enumkey,
        QueryValueEx=query,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.delenv("AAE_LIVE_PATH", raising=False)
    for env in ("ProgramData", "ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(env, str(tmp_path / "empty"))
    monkeypatch.setattr(
        APP.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(stdout="[]", returncode=0),
    )
    standard = (
        tmp_path / "empty/Ableton/Live 11.9 Suite/Program/Ableton Live 11 Suite.exe"
    )
    standard.parent.mkdir(parents=True)
    standard.touch()
    assert APP.bundle() == exe


def test_plugin_storage_follows_active_profile(tmp_path, monkeypatch):
    CLI = importlib.import_module("actual-assistant-engineer.cli")
    for profile in ("alpha", "beta"):
        home = tmp_path / profile
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert CLI.helpers_dir() == home / "plugin-data/actual-assistant-engineer/bin"
