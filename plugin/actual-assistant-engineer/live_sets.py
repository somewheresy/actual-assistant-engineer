"""Live Set files: info, new, open, save, save as.

Live's scripting API has no file operations. These use only targeted means — Live's own
menu items and dialog buttons through accessibility, file copies, and `open` — and never
synthesized keystrokes, which would land in whatever app has focus.

Every Set Hermes creates has a file from the start: `new` lays out a Project folder with a
copy of the default template and opens it, so saving never needs Live's Save panel.
Unsaved work is never discarded implicitly: the caller's on_unsaved decides.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import live_app, live_client

DEFAULT_DIR = Path.home() / "Documents" / "Ableton Live Projects" / "Hermes"


class SetError(Exception):
    pass


class _WindowNotReady(SetError):
    pass


def _is_windows():
    return sys.platform == "win32"


def _windows_ui():
    # Force dependency resolution before returning the native adapter.
    import pywinauto  # noqa: F401
    import psutil  # noqa: F401
    from .windows_ui import LiveUI
    return LiveUI(live_app.bundle())


def _win_call(method, *args, **kwargs):
    from .windows_ui import UIError, WindowNotReady
    try:
        try:
            ui = _windows_ui()
        except ImportError:
            return _win_worker(method, *args, **kwargs)
        return getattr(ui, method)(*args, **kwargs)
    except WindowNotReady as exc:
        raise _WindowNotReady(str(exc)) from exc
    except (UIError, OSError) as exc:
        raise SetError(str(exc)) from exc


def _win_worker(method, *args, **kwargs):
    """Use x64 UIA dependencies under Prism when native wheels are unavailable."""
    import json
    from types import SimpleNamespace
    uv = shutil.which("uv")
    if not uv:
        raise SetError("Windows accessibility needs pywinauto/psutil or uv for an isolated x64 worker")
    command = [uv, "run", "-q", "--no-project", "--isolated", "--python",
               "cpython-3.11-windows-x86_64-none", "--with", "pywinauto", "--with", "psutil",
               "python", str(Path(__file__).with_name("windows_ui_host.py"))]
    request = {"exe": str(live_app.bundle()), "method": method, "args": args, "kwargs": kwargs}
    try:
        result = subprocess.run(command, input=json.dumps(request), capture_output=True,
                                text=True, encoding="utf-8", timeout=120)
        if result.returncode:
            raise SetError("Windows UIA worker failed: " + result.stderr[-1000:])
        reply = json.loads(result.stdout)
        if "error" in reply:
            error = _WindowNotReady if reply.get("kind") == "WindowNotReady" else SetError
            raise error(reply["error"])
        state = reply["result"]
        if method == "snapshot" and state is not None:
            return SimpleNamespace(name=state["name"], identity=tuple(state["identity"]),
                                   dialogs=tuple(SimpleNamespace(**d) for d in state["dialogs"]))
        return state
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        raise SetError("Windows UIA worker failed; outcome unknown, inspect Live before retrying: %s" % exc) from exc


def _osa(script, timeout=15):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _ui(script):
    return _osa('tell application "System Events" to tell process "Live" to ' + script)


def _menu(item):
    if _is_windows():
        return _win_call("menu", item)
    out = _ui('click menu item "%s" of menu "File" of menu bar 1' % item)
    if not out:
        raise SetError("could not use Live's File > %s (is Live running, and accessibility allowed?)" % item)


def _main_title():
    if _is_windows():
        state = _win_call("snapshot")
        return state.name if state else None
    for name in _ui("get name of windows").split(", "):
        if name and name not in ("Settings", "Save", "Open"):
            return name
    return None


def _dialog():
    """Text of Live's modal alert, if one is showing."""
    if "AXDialog" not in _ui("get subrole of windows"):
        return ""
    return _ui("get value of static text 1 of group 1 of window 1") or " ".join(
        t for t in _ui("get value of every static text of window 1").split(", ") if t and t != "missing value")


def _click(prefix):
    _ui('click (first button of group 1 of window 1 whose description starts with "%s")' % prefix)


def _handle_dialogs(on_unsaved, deadline=20.0):
    if _is_windows():
        return _handle_windows_dialogs(on_unsaved, deadline)
    answered = []
    end = time.time() + deadline
    while time.time() < end:
        text = _dialog()
        if not text:
            return answered
        if text.startswith("This action will stop audio"):
            _click("OK")
        elif text.startswith("Save changes"):
            if on_unsaved == "discard":
                _click("Don")
            elif on_unsaved == "save" and _current_path():
                _click("Save")
            else:
                _click("Cancel")
                hint = "" if on_unsaved != "save" else " This Set has no file yet, so it can't be saved without Live's Save panel; save it by hand or pass on_unsaved \"discard\" if the producer agrees."
                raise SetError("Live asked: %s Pass on_unsaved \"save\" or \"discard\".%s" % (text, hint))
        elif "outside of a Project folder" in text:
            _click("Cancel")
            raise SetError("this Set has no Project folder; use save_as with a name")
        else:
            raise SetError("unexpected Live dialog: %s" % text)
        answered.append(text)
        time.sleep(0.6)
    raise SetError("Live kept showing dialogs")


def _handle_windows_dialogs(on_unsaved, deadline):
    answered = []
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        state = _win_call("snapshot")
        if state is None or not state.dialogs:
            return answered
        if len(state.dialogs) != 1:
            raise SetError("multiple Live dialogs; inspect Live before continuing")
        dialog = state.dialogs[0]
        path = None
        cancel_error = None
        if dialog.text.startswith("Save changes"):
            if on_unsaved == "discard":
                label = "Don't Save"
            elif on_unsaved == "save" and (path := _current_path()):
                label = "Save"
                before = Path(path).stat().st_mtime_ns
            else:
                label = "Cancel"
                cancel_error = "Live asked: %s; unsaved work kept. Choose save or discard explicitly; save requires a known file." % dialog.text
        elif dialog.text.startswith("This action will stop audio"):
            label = "OK"
        elif "outside of a Project folder" in dialog.text:
            label = "Cancel"
            cancel_error = "this Set has no Project folder; use save_as with a name"
        else:
            raise SetError("unexpected Live dialog: %s" % dialog.text)
        _win_call("click", label, expected_text=dialog.text)
        # Read back dismissal. Never invoke the same still-visible dialog twice.
        while time.monotonic() < end:
            after = _win_call("snapshot")
            if after is None:
                raise SetError("Live disappeared while answering a dialog; outcome unknown")
            if not any(d.text == dialog.text for d in after.dialogs):
                break
            time.sleep(0.1)
        else:
            raise SetError("Live did not dismiss its dialog; outcome unknown")
        if path:
            while Path(path).stat().st_mtime_ns == before and time.monotonic() < end:
                time.sleep(0.1)
            if Path(path).stat().st_mtime_ns == before:
                raise SetError("Live did not write %s; save outcome unknown" % path)
        if cancel_error:
            raise SetError(cancel_error)
        answered.append(dialog.text)
    raise SetError("Live kept showing dialogs")


_opened = {}  # Set name -> path for Sets opened or created through this module
_windows_opened = None  # Only the active (process/window identity, name, exact path).


def _current_path():
    global _windows_opened
    if _is_windows():
        state = _win_call("snapshot")
        if _windows_opened:
            identity, name, path = _windows_opened
            if state and state.identity == identity and state.name == name and Path(path).is_file():
                return path
            _windows_opened = None
        return None
    title = _main_title()
    path = _opened.get(title)
    return path if path and Path(path).exists() else None


def _wait_for_bridge(title, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        if _main_title() == title and live_client.available():
            try:
                res = live_client.batch([{"op": "info"}], timeout=5.0, undo_step=False)
                if res.get("ok"):
                    return res["results"][0]
            except Exception:
                pass
        time.sleep(0.5)
    raise SetError("Live did not come back with the Hermes control surface in time")


def _project_path(name, directory):
    safe = "".join(c for c in name if c not in '/:\\').strip() or "Untitled"
    folder = Path(directory).expanduser() / ("%s Project" % safe)
    return folder, folder / ("%s.als" % safe)


def info():
    title = _main_title()
    return {"name": title, "path": _current_path(), "live_running": title is not None}


def open_set(path, on_unsaved="cancel"):
    if _is_windows():
        return _open_windows(path, on_unsaved)
    path = Path(path).expanduser()
    if not path.exists():
        raise SetError("no Set at %s" % path)
    subprocess.run(["open", "-a", str(live_app.bundle()), str(path)], check=True)
    time.sleep(1.0)
    answered = _handle_dialogs(on_unsaved)
    live = _wait_for_bridge(path.stem)
    _opened[path.stem] = str(path)
    return {"name": path.stem, "path": str(path), "answered": answered, "tracks": live.get("tracks")}


def _open_windows(path, on_unsaved, timeout=60.0):
    global _windows_opened
    try:
        return _open_windows_impl(path, on_unsaved, timeout)
    except (SetError, OSError) as exc:
        # An uncertain transition must not leave a same-name path association.
        _windows_opened = None
        raise SetError(str(exc)) from exc


def _open_windows_impl(path, on_unsaved, timeout):
    global _windows_opened
    if on_unsaved not in ("cancel", "save", "discard"):
        raise SetError("on_unsaved must be cancel, save, or discard")
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".als":
        raise SetError("no Set at %s" % path)
    previous = _win_call("snapshot")
    if previous and previous.dialogs:
        raise SetError("Live already has a dialog; resolve it before opening another Set")
    if previous and previous.name == path.stem and _current_path() != str(path):
        raise SetError("cannot distinguish same-name Sets by Live's title; open a differently named Set first")
    try:
        process = subprocess.Popen([str(live_app.bundle()), str(path)], shell=False)
    except OSError as exc:
        raise SetError("cannot launch Live: %s" % exc) from exc
    answered = []
    transitioned = previous is None or previous.name != path.stem
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if process.poll() not in (None, 0):
            raise SetError("Live launch failed; inspect Live before retrying")
        try:
            state = _win_call("snapshot")
        except _WindowNotReady:
            # No interaction or success until a recognized window is readable.
            time.sleep(0.1)
            continue
        if state and state.dialogs:
            answered.extend(_handle_windows_dialogs(on_unsaved, max(0, end - time.monotonic())))
            continue
        connected = live_client.available()
        if not connected:
            transitioned = True
        if transitioned and state and state.name == path.stem and connected:
            try:
                res = live_client.batch([{"op": "info"}], timeout=5.0, undo_step=False)
            except (live_client.LiveUnavailable, OSError, TimeoutError):
                res = {}
            if res.get("ok") and res.get("results"):
                after = _win_call("snapshot")
                if not after or after.identity != state.identity or after.name != state.name or after.dialogs:
                    raise SetError("Live changed during open verification; inspect Live before retrying")
                _windows_opened = (state.identity, state.name, str(path))
                return {"name": path.stem, "path": str(path), "answered": answered, "tracks": res["results"][0].get("tracks")}
        time.sleep(0.1)
    raise SetError("Live did not confirm the requested Set and Hermes bridge in time; inspect Live before retrying")


def new(name, directory=None, on_unsaved="cancel"):
    if _is_windows() and on_unsaved not in ("cancel", "save", "discard"):
        raise SetError("on_unsaved must be cancel, save, or discard")
    folder, path = _project_path(name, directory or DEFAULT_DIR)
    if path.exists():
        raise SetError("%s already exists; pick another name or open it" % path)
    (folder / "Ableton Project Info").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(live_app.default_set(), path)
    return open_set(path, on_unsaved)


def save():
    path = _current_path()
    if not path:
        raise SetError("the open Set wasn't created or opened by Hermes, so its file is unknown; use save_as with a name")
    before = Path(path).stat().st_mtime_ns
    _menu("Save Live Set")
    _handle_dialogs("cancel")
    for _ in range(40):
        if _is_windows():
            _handle_dialogs("cancel")
            if _current_path() != path:
                raise SetError("Live's active document changed during save; outcome unknown")
        if Path(path).stat().st_mtime_ns > before:
            return {"name": Path(path).stem, "path": path, "saved": True}
        time.sleep(0.25)
    raise SetError("Live did not write %s" % path)


def save_as(name, directory=None):
    """Save the open Set, then continue under a new name in its own Project folder."""
    saved = save()
    folder, path = _project_path(name, directory or DEFAULT_DIR)
    if path.exists():
        raise SetError("%s already exists; pick another name" % path)
    source = _current_path()
    if _is_windows() and source != saved["path"]:
        raise SetError("Live's active document changed during save_as; no copy made")
    (folder / "Ableton Project Info").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, path)
    out = open_set(path, "cancel")
    return dict(out, saved=True)
