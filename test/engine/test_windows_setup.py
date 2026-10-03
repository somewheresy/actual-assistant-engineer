import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows native discovery and filesystem contracts"
)

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
C = importlib.import_module("actual-assistant-engineer.cli")


def test_setup_copies_idempotently_and_preserves_each_backup(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text("v1")
    monkeypatch.setattr(C, "SURFACE", source)
    monkeypatch.setattr(C, "_bridge_ok", lambda: False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("AAE_LIVE_PATH", str(tmp_path / "absent.exe"))
    C.live_app.bundle.cache_clear()
    target_root = tmp_path / "User Library/Remote Scripts"
    args = SimpleNamespace(
        remote_scripts=str(target_root), no_native=True, index_plugins=False
    )
    assert C.setup(args) == 0
    target = target_root / "Hermes"
    assert not target.is_symlink()
    assert (target / "__init__.py").read_text() == "v1"
    assert C.setup(args) == 0
    assert not list(target_root.glob("Hermes.backup*"))
    for version in ("v2", "v3"):
        (source / "__init__.py").write_text(version)
        assert C.setup(args) == 0
        assert (target / "__init__.py").read_text() == version
    backups = list(target_root.glob("Hermes.backup*/**/__init__.py"))
    assert sorted(p.read_text() for p in backups) == ["v1", "v2"]
    cap = []
    monkeypatch.setattr("builtins.print", lambda text: cap.append(text))
    assert C.status(SimpleNamespace()) == 1
    import json

    report = json.loads(cap[0])
    assert report["control_surface_path"] == str(target.resolve())
    assert report["live"] is None
    assert "AAE_LIVE_PATH" in report["live_error"]
    assert report["native_helpers_supported"] is False


def test_surface_copy_failure_preserves_existing_install(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text("new")
    target = tmp_path / "scripts/Hermes"
    target.mkdir(parents=True)
    (target / "__init__.py").write_text("old")
    monkeypatch.setattr(C, "SURFACE", source)

    def fail(*args, **kwargs):
        raise PermissionError("copy denied")

    monkeypatch.setattr(C.shutil, "copytree", fail)
    with pytest.raises(PermissionError, match="copy denied"):
        C.install_surface(target.parent)
    assert (target / "__init__.py").read_text() == "old"
    assert not list(target.parent.glob(".Hermes-stage*"))


def test_setup_rejects_overlapping_source_target(tmp_path, monkeypatch):
    (tmp_path / "__init__.py").touch()
    monkeypatch.setattr(C, "SURFACE", tmp_path)
    monkeypatch.setattr(
        C.shutil, "copytree", lambda *a, **k: pytest.fail("recursive copy attempted")
    )
    with pytest.raises(ValueError, match="overlap"):
        C.install_surface(tmp_path / "scripts")


def test_existing_junction_is_backed_up_and_replaced_with_real_copy(
    tmp_path, monkeypatch
):
    import _winapi

    source = tmp_path / "source"
    source.mkdir()
    (source / "__init__.py").write_text("v1")
    root = tmp_path / "scripts"
    root.mkdir()
    target = root / "Hermes"
    _winapi.CreateJunction(str(source), str(target))
    monkeypatch.setattr(C, "SURFACE", source)
    C.install_surface(root)
    (source / "__init__.py").write_text("v2")
    assert (target / "__init__.py").read_text() == "v1"
    assert list(root.glob("Hermes.backup*"))


def test_windows_does_not_build_swift_even_when_compiler_exists(monkeypatch):
    monkeypatch.setattr(C.shutil, "which", lambda name: "swiftc.exe")
    monkeypatch.setattr(
        C.subprocess,
        "run",
        lambda *a, **k: pytest.fail("macOS helper invoked on Windows"),
    )
    assert C.build_native() == []


def test_status_vst_probe_reports_failure_without_claiming_capability(
    tmp_path, monkeypatch, capsys
):
    import argparse
    import json

    V = importlib.import_module("actual-assistant-engineer.live_vst")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("AAE_REMOTE_SCRIPTS", str(tmp_path / "scripts"))
    monkeypatch.setenv("AAE_LIVE_PATH", str(tmp_path / "missing.exe"))
    C.live_app.bundle.cache_clear()
    monkeypatch.setattr(C, "_bridge_ok", lambda: False)

    def fail(*a, **k):
        raise V.VstError("no compatible pedalboard wheel")

    monkeypatch.setattr(V, "_host", fail)
    parser = argparse.ArgumentParser()
    C.configure(parser)
    args = parser.parse_args(["status", "--probe-vst"])
    assert C.status(args) == 1
    report, _ = json.JSONDecoder().raw_decode(capsys.readouterr().out)
    assert report["vst_host"] is False
    assert report["vst_host_error"] == "no compatible pedalboard wheel"
