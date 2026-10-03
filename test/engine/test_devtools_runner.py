import importlib.util
import io
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import developer


def test_engine_uses_shipped_discovery(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("devtools_als", ROOT / "engine" / "als.py")
    als = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(als)
    monkeypatch.setattr(developer.plugin_module("live_app"), "resources", lambda: tmp_path)
    assert als.live_resources() == tmp_path


def test_json_session_keeps_verified_path_association(monkeypatch):
    sets = developer.plugin_module("live_sets")
    opened = {}
    def open_set(path, on_unsaved):
        opened["path"] = path
        return {"path": path, "policy": on_unsaved}
    def save_as(name, directory):
        return {"source": opened["path"], "name": name, "directory": directory}
    monkeypatch.setattr(sets, "open_set", open_set)
    monkeypatch.setattr(sets, "save_as", save_as)
    requests = [{"action": "open_set", "path": 'C:/a quote"/song.als', "on_unsaved": "cancel"},
                {"action": "save_as", "name": "Next", "directory": "C:/Music"}]
    output = io.StringIO()
    developer.serve(io.StringIO("\n".join(map(json.dumps, requests))), output)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[1]["result"]["source"] == requests[0]["path"]
    assert responses[0]["result"]["policy"] == "cancel"


def test_unknown_title_save_refuses_without_reconstructing_path(monkeypatch):
    sets = developer.plugin_module("live_sets")
    monkeypatch.setattr(sets, "_current_path", lambda: None)
    output = io.StringIO()
    developer.serve(io.StringIO(json.dumps({"action": "save_as", "name": "New", "directory": "unused"})), output)
    response = json.loads(output.getvalue())
    assert response["ok"] is False
    assert "file is unknown" in response["error"]


def test_developer_setup_reuses_shipped_cli(monkeypatch):
    cli = developer.plugin_module("cli")
    called = []
    monkeypatch.setattr(cli, "setup", lambda args: called.append(args) or 0)
    assert developer.main(["install-live", "--remote-scripts", "C:/custom", "--no-native"]) == 0
    assert called[0].remote_scripts == "C:/custom"
    assert called[0].no_native is True


def test_native_build_reuses_shipped_builder(monkeypatch, tmp_path):
    cli = developer.plugin_module("cli")
    monkeypatch.setattr(cli, "build_native", lambda: [])
    assert developer.main(["build-native"]) == 0


def test_install_hermes_copies_without_symlinks_and_refuses_overwrite(monkeypatch, tmp_path):
    import subprocess
    import pytest
    paths = developer.plugin_module("platform_paths")
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "plugin-data" / "actual-assistant-engineer")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append(args) or types.SimpleNamespace(returncode=0))
    assert developer.main(["install-hermes"]) == 0
    target = tmp_path / "plugins" / "actual-assistant-engineer"
    assert (target / "cli.py").read_bytes() == (ROOT / "plugin/actual-assistant-engineer/cli.py").read_bytes()
    assert calls == [["hermes", "plugins", "enable", "actual-assistant-engineer"]]
    with pytest.raises(FileExistsError):
        developer.main(["install-hermes"])
    assert len(calls) == 1
