"""Windows Set safety tests; fake UIA boundary, real adapter logic."""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
S = importlib.import_module("actual-assistant-engineer.live_sets")


def windows_module():
    assert (Path(S.__file__).parent / "windows_ui.py").exists(), "Windows UI adapter is missing"
    return importlib.import_module("actual-assistant-engineer.windows_ui")


def test_normalize_live_titles_without_truncating_set_name():
    W = windows_module()
    assert W.set_name("My - Set* - Ableton Live 12 Suite") == "My - Set"
    assert W.set_name("My Set.als - Ableton Live 11 Standard") == "My Set"
    assert W.set_name("Untitled - Ableton Live 12 Trial") == "Untitled"
    assert W.set_name("Settings") is None
    assert W.set_name("My Set - Not Ableton Live") is None


class Node:
    def __init__(self, name, kind="Window", pid=42, children=(), modal=False):
        from types import SimpleNamespace
        self.name, self.children, self.calls = name, list(children), []
        self.element_info = SimpleNamespace(control_type=kind, process_id=pid, handle=100)
        self.iface_window = SimpleNamespace(CurrentIsModal=modal)
        self.iface_invoke = SimpleNamespace(Invoke=lambda: self.calls.append("invoke"))
        self.iface_expand_collapse = SimpleNamespace(Expand=lambda: self.calls.append("expand"))

    def window_text(self):
        return self.name

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def descendants(self, control_type=None):
        nodes = [n for child in self.children for n in [child, *child.descendants()]]
        return [n for n in nodes if control_type is None or n.element_info.control_type == control_type]


def ui_for(W, windows, processes=None):
    from types import SimpleNamespace
    calls = []
    def connect(pid):
        calls.append(pid)
        return SimpleNamespace(windows=lambda: windows)
    ui = W.LiveUI(Path("C:/Apps/Live.exe"),
                  processes=lambda: processes or [{"pid": 42, "name": "Live.exe", "exe": "C:/Apps/Live.exe", "create_time": 3}],
                  connect=connect)
    return ui, calls


def test_snapshot_selects_exact_executable_not_matching_window_title():
    W = windows_module()
    assert hasattr(W, "LiveUI"), "Process-scoped adapter missing"
    ui, calls = ui_for(W, [Node("Actual* - Ableton Live 12 Suite")], [
        {"pid": 99, "name": "Live.exe", "exe": "C:/Other/Live.exe", "create_time": 2},
        {"pid": 42, "name": "Live.exe", "exe": "C:/Apps/Live.exe", "create_time": 3},
    ])
    state = ui.snapshot()
    assert state.name == "Actual"
    assert state.identity == (42, 3, 100)
    assert state.dialogs == ()
    assert calls == [42]


def test_transient_forwarding_process_is_retryable_but_not_selected():
    W = windows_module()
    ui, _ = ui_for(W, [Node("Song - Ableton Live 12 Suite")], [
        {"pid": 42, "name": "Live.exe", "exe": "C:/Apps/Live.exe", "create_time": 3},
        {"pid": 43, "name": "Live.exe", "exe": "C:/Apps/Live.exe", "create_time": 4},
    ])
    with pytest.raises(W.WindowNotReady, match="multiple processes"):
        ui.snapshot()


@pytest.mark.parametrize("case", ["foreign", "denied", "read_error", "duplicate"])
def test_snapshot_fails_closed(case):
    W = windows_module()
    main = Node("Song - Ableton Live 12 Suite", pid=99 if case == "foreign" else 42)
    processes = [{"pid": 42, "name": "Live.exe", "exe": "C:/Apps/Live.exe", "create_time": 3}]
    if case == "denied":
        processes[0]["exe"] = None
    if case == "duplicate":
        processes.append(dict(processes[0], pid=43))
    if case == "read_error":
        def fail():
            raise OSError("UIA denied")
        main.descendants = fail
    ui, _ = ui_for(W, [main], processes)
    with pytest.raises(W.UIError):
        ui.snapshot()


def test_vst_editor_window_is_not_a_save_prompt():
    W = windows_module()
    plugin = Node('Effect/Track', modal=True, children=[Node('Close', 'Button')])
    plugin.class_name = lambda: 'Vst3PlugWindow'
    ui, _ = ui_for(W, [Node('Song - Ableton Live 12 Suite'), plugin])
    assert ui.snapshot().dialogs == ()


def test_snapshot_reads_modal_text_and_does_not_hide_unknown_windows():
    W = windows_module()
    alert = Node("Ableton Live", modal=True, children=[Node("Save changes to Song?", "Text"), Node("Cancel", "Button")])
    ui, _ = ui_for(W, [Node("Song - Ableton Live 12 Suite"), alert])
    state = ui.snapshot()
    assert len(state.dialogs) == 1
    assert "Save changes to Song?" in state.dialogs[0].text
    assert state.dialogs[0].buttons == ("Cancel",)


def test_menu_uses_scoped_patterns_and_exact_label():
    W = windows_module()
    save = Node("Save Live Set\tCtrl+S", "MenuItem")
    other = Node("Save Live Set As...", "MenuItem")
    file_menu = Node("&File", "MenuItem", children=[save, other])
    ui, _ = ui_for(W, [Node("Song - Ableton Live 12 Suite", children=[file_menu])])
    assert hasattr(ui, "menu"), "Targeted menu Invoke missing"
    ui.menu("Save Live Set")
    assert file_menu.calls == ["expand"]
    assert save.calls == ["invoke"]
    assert other.calls == []


def test_windows_yes_no_save_dialog_uses_native_labels(windows_sets, monkeypatch):
    W, ui, main, windows = windows_sets
    dialog = Node('Live', modal=True, children=[Node('Save changes to Song?', 'Text'), Node('Yes', 'Button'), Node('No', 'Button'), Node('Cancel', 'Button')])
    windows.append(dialog)
    def click(method, *args, **kwargs):
        if method == 'snapshot':
            return ui.snapshot()
        assert method == 'click' and args == ('No',)
        windows.remove(dialog)
    monkeypatch.setattr(S, '_win_call', click)
    assert S._handle_windows_dialogs('discard', 1) == ['Save changes to Song?']


def test_dialog_button_uses_exact_invoke_not_prefix():
    W = windows_module()
    cancel, save = Node("Cancel", "Button"), Node("Save", "Button")
    alert = Node("Ableton Live", modal=True, children=[Node("Save changes?", "Text"), cancel, save])
    ui, _ = ui_for(W, [Node("Song - Ableton Live 12 Suite"), alert])
    assert hasattr(ui, "click"), "Targeted button Invoke missing"
    ui.click("Cancel")
    assert cancel.calls == ["invoke"]
    assert save.calls == []


def test_default_backend_loads_uia_lazily_and_connects_only_verified_pid(monkeypatch):
    from types import SimpleNamespace
    W = windows_module()
    calls = []
    main = Node("Song - Ableton Live 12 Suite")
    process = SimpleNamespace(info={"pid": 42, "name": "Live.exe", "exe": "C:/Apps/Live.exe", "create_time": 3})
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=lambda **kwargs: [process]))
    class Application:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def connect(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(windows=lambda: [main])
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=Application))
    ui = W.LiveUI(Path("C:/Apps/Live.exe"))
    assert ui.snapshot().name == "Song"
    assert calls == [{"backend": "uia"}, {"process": 42, "timeout": 5}]


@pytest.mark.parametrize("case", ["foreign", "duplicate", "disabled", "pattern_missing", "changed"])
def test_dialog_refuses_unsafe_buttons(case):
    W = windows_module()
    button = Node("Cancel", "Button", pid=99 if case == "foreign" else 42)
    if case == "disabled":
        button.is_enabled = lambda: False
    if case == "pattern_missing":
        button.iface_invoke = None
    children = [Node("Save changes?", "Text"), button]
    if case == "duplicate":
        children.append(Node("Cancel", "Button"))
    ui, _ = ui_for(W, [Node("Song - Ableton Live 12 Suite"), Node("Live", children=children)])
    with pytest.raises(W.UIError):
        ui.click("Cancel", expected_text="different" if case == "changed" else "Save changes?")
    assert button.calls == []


@pytest.fixture
def windows_sets(monkeypatch):
    W = windows_module()
    main = Node("Song* - Ableton Live 12 Suite")
    windows = [main]
    ui, _ = ui_for(W, windows)
    monkeypatch.setattr(S, "_is_windows", lambda: True, raising=False)
    monkeypatch.setattr(S, "_windows_ui", lambda: ui, raising=False)
    monkeypatch.setattr(S, "_windows_opened", None, raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("macOS automation used on Windows")
    monkeypatch.setattr(S, "_osa", forbidden)
    return W, ui, main, windows


def test_save_clean_known_set_is_idempotent(windows_sets, monkeypatch, tmp_path):
    W, ui, main, windows = windows_sets
    main.name = 'Song - Ableton Live 12 Suite'
    path = tmp_path / 'Song.als'
    path.write_bytes(b'saved')
    S._windows_opened = (ui.snapshot().identity, 'Song', str(path))
    monkeypatch.setattr(S, '_menu', lambda *a: pytest.fail('clean Set has disabled Save menu'))
    assert S.save() == {'name':'Song', 'path':str(path), 'saved':True, 'unchanged':True}


def test_windows_info_reads_normalized_process_scoped_title(windows_sets):
    assert S.info() == {"name": "Song", "path": None, "live_running": True}


def test_windows_open_launches_nonblocking_and_tracks_exact_active_path(windows_sets, monkeypatch, tmp_path):
    from types import SimpleNamespace
    _, ui, main, windows = windows_sets
    path = tmp_path / "New Song.als"
    path.write_bytes(b"set")
    calls = []
    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        main.name = "New Song - Ableton Live 12 Suite"
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(S.subprocess, "Popen", launch)
    monkeypatch.setattr(S.subprocess, "run", lambda *a, **k: pytest.fail("blocking launch"))
    monkeypatch.setattr(S.live_app, "bundle", lambda: Path("C:/Apps/Live.exe"))
    monkeypatch.setattr(S.live_client, "available", lambda: True)
    monkeypatch.setattr(S.live_client, "batch", lambda *a, **k: {"ok": True, "results": [{"tracks": 5}]})
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    out = S.open_set(path)
    assert calls == [(["C:\\Apps\\Live.exe" if sys.platform == "win32" else "C:/Apps/Live.exe", str(path.resolve())], {"shell": False})]
    assert out == {"name": "New Song", "path": str(path.resolve()), "answered": [], "tracks": 5}
    assert S._current_path() == str(path.resolve())
    # A manual document switch invalidates provenance; do not revive old name maps.
    main.name = "Other - Ableton Live 12 Suite"
    assert S._current_path() is None
    main.name = "New Song - Ableton Live 12 Suite"
    assert S._current_path() is None


@pytest.mark.parametrize("policy,known,choice,error", [
    ("cancel", False, "Cancel", True), ("save", False, "Cancel", True),
    ("save", True, "Save", False), ("discard", False, "Don't Save", False),
])
def test_windows_unsaved_policy_invokes_only_authorized_choice(windows_sets, monkeypatch, tmp_path, policy, known, choice, error):
    import os
    W, ui, main, windows = windows_sets
    path = tmp_path / "Song.als"
    path.write_bytes(b"old")
    if known:
        monkeypatch.setattr(S, "_windows_opened", (ui.snapshot().identity, "Song", str(path)))
    buttons = [Node(label, "Button") for label in ("Cancel", "Save", "Don't Save")]
    alert = Node("Live", children=[Node("Save changes to Song?", "Text"), *buttons])
    windows.append(alert)
    for button in buttons:
        def invoke(button=button):
            button.calls.append("invoke")
            windows.remove(alert)
            if button.name == "Save":
                before = path.stat().st_mtime_ns
                os.utime(path, ns=(before + 1000000000, before + 1000000000))
        button.iface_invoke.Invoke = invoke
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    if error:
        with pytest.raises(S.SetError, match="unsaved|no file|Save changes"):
            S._handle_dialogs(policy)
    else:
        assert S._handle_dialogs(policy) == ["Save changes to Song?"]
    assert {b.name: b.calls for b in buttons} == {b.name: ["invoke"] if b.name == choice else [] for b in buttons}


@pytest.mark.parametrize("policy,expected", [("cancel", "Cancel"), ("discard", "Don't Save")])
def test_open_handles_late_unsaved_dialog_before_reporting_success(windows_sets, monkeypatch, tmp_path, policy, expected):
    from types import SimpleNamespace
    _, ui, main, windows = windows_sets
    path = tmp_path / "New.als"
    path.write_bytes(b"set")
    buttons = [Node("Cancel", "Button"), Node("Don't Save", "Button")]
    alert = Node("Live", children=[Node("Save changes?", "Text"), *buttons])
    def invoke(button):
        button.calls.append("invoke")
        windows.remove(alert)
        if button.name == "Don't Save":
            main.name = "New - Ableton Live 12 Suite"
    for button in buttons:
        button.iface_invoke.Invoke = lambda b=button: invoke(b)
    monkeypatch.setattr(S.live_app, "bundle", lambda: Path("C:/Apps/Live.exe"))
    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(S.live_client, "available", lambda: True)
    monkeypatch.setattr(S.live_client, "batch", lambda *a, **k: {"ok": True, "results": [{"tracks": 1}]})
    sleeps = []
    def sleep(seconds):
        if not sleeps:
            windows.append(alert)
        sleeps.append(seconds)
    monkeypatch.setattr(S.time, "sleep", sleep)
    if policy == "cancel":
        with pytest.raises(S.SetError, match="unsaved"):
            S._open_windows(path, policy, timeout=0.1)
    else:
        assert S._open_windows(path, policy, timeout=0.1)["answered"] == ["Save changes?"]
    assert [b.name for b in buttons if b.calls] == [expected]


@pytest.mark.parametrize("case", ["invalid_policy", "not_als", "preexisting_dialog", "same_name_unknown"])
def test_open_rejects_unsafe_requests_before_launch(windows_sets, monkeypatch, tmp_path, case):
    _, ui, main, windows = windows_sets
    path = tmp_path / ("Song.txt" if case == "not_als" else "Song.als")
    path.write_bytes(b"set")
    if case == "preexisting_dialog":
        windows.append(Node("unexpected modal"))
    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: pytest.fail("unsafe launch"))
    with pytest.raises(S.SetError):
        S.open_set(path, "typo" if case == "invalid_policy" else "cancel")


@pytest.mark.parametrize("effect", ["write", "no_write", "wrong_set", "late_dialog"])
def test_windows_save_requires_same_document_and_file_readback(windows_sets, monkeypatch, tmp_path, effect):
    import os
    _, ui, main, windows = windows_sets
    path = tmp_path / "Song.als"
    path.write_bytes(b"set")
    monkeypatch.setattr(S, "_windows_opened", (ui.snapshot().identity, "Song", str(path)))
    save = Node("Save Live Set", "MenuItem")
    main.children.append(Node("File", "MenuItem", children=[save]))
    def invoke():
        save.calls.append("invoke")
        if effect != "no_write":
            before = path.stat().st_mtime_ns
            os.utime(path, ns=(before + 1000000000, before + 1000000000))
        if effect == "wrong_set":
            main.name = "Other - Ableton Live 12 Suite"
        if effect == "late_dialog":
            windows.append(Node("Unknown modal"))
    save.iface_invoke.Invoke = invoke
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    if effect == "write":
        assert S.save() == {"name": "Song", "path": str(path), "saved": True}
    else:
        with pytest.raises(S.SetError):
            S.save()
    assert save.calls == ["invoke"]


@pytest.mark.parametrize("reconnect", [False, True])
def test_same_path_reopen_requires_observed_bridge_transition(windows_sets, monkeypatch, tmp_path, reconnect):
    from types import SimpleNamespace
    _, ui, main, windows = windows_sets
    path = tmp_path / "Song.als"
    path.write_bytes(b"set")
    monkeypatch.setattr(S, "_windows_opened", (ui.snapshot().identity, "Song", str(path)))
    monkeypatch.setattr(S.live_app, "bundle", lambda: Path("C:/Apps/Live.exe"))
    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: SimpleNamespace(poll=lambda: None))
    availability = iter([False, True]) if reconnect else iter([])
    monkeypatch.setattr(S.live_client, "available", lambda: next(availability, True))
    monkeypatch.setattr(S.live_client, "batch", lambda *a, **k: {"ok": True, "results": [{"tracks": 1}]})
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    if reconnect:
        assert S._open_windows(path, "cancel", timeout=0.05)["path"] == str(path)
    else:
        with pytest.raises(S.SetError, match="confirm"):
            S._open_windows(path, "cancel", timeout=0.05)
        assert S._current_path() is None


def test_open_rechecks_window_after_bridge_response(windows_sets, monkeypatch, tmp_path):
    from types import SimpleNamespace
    _, ui, main, windows = windows_sets
    path = tmp_path / "New.als"
    path.write_bytes(b"set")
    monkeypatch.setattr(S.live_app, "bundle", lambda: Path("C:/Apps/Live.exe"))
    def launch(*a, **k):
        main.name = "New - Ableton Live 12 Suite"
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(S.subprocess, "Popen", launch)
    monkeypatch.setattr(S.live_client, "available", lambda: True)
    def reply(*a, **k):
        windows.append(Node("Unknown modal"))
        return {"ok": True, "results": [{"tracks": 1}]}
    monkeypatch.setattr(S.live_client, "batch", reply)
    with pytest.raises(S.SetError):
        S._open_windows(path, "cancel", timeout=0.1)
    assert S._windows_opened is None


@pytest.mark.parametrize("text,button,raises", [
    ("This action will stop audio. Continue?", "OK", False),
    ("This Set is outside of a Project folder", "Cancel", True),
    ("Unexpected destructive question", "OK", True),
])
def test_windows_known_non_save_dialogs_and_unknown_fail_closed(windows_sets, monkeypatch, text, button, raises):
    _, ui, main, windows = windows_sets
    target = Node(button, "Button")
    alert = Node("Live", children=[Node(text, "Text"), target])
    windows.append(alert)
    def invoke():
        target.calls.append("invoke")
        windows.remove(alert)
    target.iface_invoke.Invoke = invoke
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    if raises:
        with pytest.raises(S.SetError):
            S._handle_dialogs("discard")
    else:
        assert S._handle_dialogs("cancel") == [text]
    assert target.calls == ([] if text.startswith("Unexpected") else ["invoke"])


def test_new_rejects_invalid_policy_before_creating_files(windows_sets, monkeypatch, tmp_path):
    template = tmp_path / "template.als"
    template.write_bytes(b"template")
    monkeypatch.setattr(S.live_app, "default_set", lambda: template)
    with pytest.raises(S.SetError):
        S.new("New", tmp_path, "typo")
    assert not (tmp_path / "New Project").exists()


def test_save_as_refuses_source_switch_before_copy(windows_sets, monkeypatch, tmp_path):
    path = tmp_path / "Song.als"
    path.write_bytes(b"set")
    monkeypatch.setattr(S, "save", lambda: {"name": "Song", "path": str(path), "saved": True})
    monkeypatch.setattr(S, "_current_path", lambda: None)
    with pytest.raises(S.SetError, match="changed"):
        S.save_as("Copy", tmp_path)
    assert not (tmp_path / "Copy Project" / "Copy.als").exists()


def test_disabled_main_without_readable_dialog_is_not_no_dialog():
    W = windows_module()
    main = Node("Song - Ableton Live 12 Suite")
    main.is_enabled = lambda: False
    ui, _ = ui_for(W, [main])
    with pytest.raises(W.UIError, match="disabled"):
        ui.snapshot()


def test_dialog_query_propagates_accessibility_errors(windows_sets, monkeypatch):
    W, ui, _, _ = windows_sets
    def fail():
        raise W.UIError("UIA unavailable")
    monkeypatch.setattr(ui, "snapshot", fail)
    with pytest.raises(S.SetError, match="UIA unavailable"):
        S._handle_dialogs("discard")


def test_stuck_dialog_is_never_invoked_twice(windows_sets, monkeypatch):
    _, ui, main, windows = windows_sets
    button = Node("Don't Save", "Button")
    windows.append(Node("Live", children=[Node("Save changes?", "Text"), button]))
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    with pytest.raises(S.SetError, match="dismiss"):
        S._handle_dialogs("discard", deadline=0.01)
    assert button.calls == ["invoke"]


def test_macos_open_retains_existing_targeted_launcher(monkeypatch, tmp_path):
    from types import SimpleNamespace
    path = tmp_path / "Mac Set.als"
    path.write_bytes(b"set")
    calls = []
    monkeypatch.setattr(S, "_is_windows", lambda: False)
    monkeypatch.setattr(S.live_app, "bundle", lambda: Path("/Applications/Ableton Live.app"))
    monkeypatch.setattr(S.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    monkeypatch.setattr(S.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(S, "_handle_dialogs", lambda policy: [])
    monkeypatch.setattr(S, "_wait_for_bridge", lambda title: {"tracks": 3})
    monkeypatch.setattr(S, "_opened", {})
    assert S.open_set(path)["tracks"] == 3
    assert calls == [(["open", "-a", str(Path("/Applications/Ableton Live.app")), str(path)], {"check": True})]
    assert S._opened["Mac Set"] == str(path)


def test_new_and_save_as_copy_real_files_without_overwriting(windows_sets, monkeypatch, tmp_path):
    template = tmp_path / "template.als"
    template.write_bytes(b"template contents")
    monkeypatch.setattr(S.live_app, "default_set", lambda: template)
    opened = []
    def opened_set(path, on_unsaved):
        opened.append((Path(path), on_unsaved))
        return {"path": str(path), "name": Path(path).stem}
    monkeypatch.setattr(S, "open_set", opened_set)
    S.new("First", tmp_path)
    source = tmp_path / "First Project" / "First.als"
    assert source.read_bytes() == template.read_bytes()
    assert (source.parent / "Ableton Project Info").is_dir()
    monkeypatch.setattr(S, "save", lambda: {"path": str(source), "saved": True})
    monkeypatch.setattr(S, "_current_path", lambda: str(source))
    assert S.save_as("Second", tmp_path)["saved"] is True
    target = tmp_path / "Second Project" / "Second.als"
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(S.SetError, match="already exists"):
        S.save_as("Second", tmp_path)
    with pytest.raises(S.SetError, match="already exists"):
        S.new("First", tmp_path)
    assert len(opened) == 2


def test_open_waits_for_window_creation_without_masking_uia_errors(windows_sets, monkeypatch, tmp_path):
    from types import SimpleNamespace
    _, ui, main, windows = windows_sets
    path = tmp_path / "New.als"
    path.write_bytes(b"set")
    monkeypatch.setattr(S.live_app, "bundle", lambda: Path("C:/Apps/Live.exe"))
    def launch(*a, **k):
        windows.clear()
        return SimpleNamespace(poll=lambda: None)
    def sleep(seconds):
        main.name = "New - Ableton Live 12 Suite"
        windows[:] = [main]
    monkeypatch.setattr(S.subprocess, "Popen", launch)
    monkeypatch.setattr(S.time, "sleep", sleep)
    monkeypatch.setattr(S.live_client, "available", lambda: True)
    monkeypatch.setattr(S.live_client, "batch", lambda *a, **k: {"ok": True, "results": [{"tracks": 1}]})
    assert S._open_windows(path, "cancel", timeout=0.1)["name"] == "New"
