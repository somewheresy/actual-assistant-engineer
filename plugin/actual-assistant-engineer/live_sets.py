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
import time
from pathlib import Path

from . import live_client

DEFAULT_DIR = Path.home() / "Documents" / "Ableton Live Projects" / "Hermes"
LIVE_APP = "Ableton Live 12 Suite"
TEMPLATE = Path("/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als")


class SetError(Exception):
    pass


def _osa(script, timeout=15):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _ui(script):
    return _osa('tell application "System Events" to tell process "Live" to ' + script)


def _menu(item):
    out = _ui('click menu item "%s" of menu "File" of menu bar 1' % item)
    if not out:
        raise SetError("could not use Live's File > %s (is Live running, and accessibility allowed?)" % item)


def _main_title():
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


_opened = {}  # Set name -> path for Sets opened or created through this module


def _current_path():
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
    path = Path(path).expanduser()
    if not path.exists():
        raise SetError("no Set at %s" % path)
    subprocess.run(["open", "-a", LIVE_APP, str(path)], check=True)
    time.sleep(1.0)
    answered = _handle_dialogs(on_unsaved)
    live = _wait_for_bridge(path.stem)
    _opened[path.stem] = str(path)
    return {"name": path.stem, "path": str(path), "answered": answered, "tracks": live.get("tracks")}


def new(name, directory=None, on_unsaved="cancel"):
    folder, path = _project_path(name, directory or DEFAULT_DIR)
    if path.exists():
        raise SetError("%s already exists; pick another name or open it" % path)
    (folder / "Ableton Project Info").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, path)
    return open_set(path, on_unsaved)


def save():
    path = _current_path()
    if not path:
        raise SetError("the open Set wasn't created or opened by Hermes, so its file is unknown; use save_as with a name")
    before = Path(path).stat().st_mtime
    _menu("Save Live Set")
    _handle_dialogs("cancel")
    for _ in range(40):
        if Path(path).stat().st_mtime > before:
            return {"name": Path(path).stem, "path": path, "saved": True}
        time.sleep(0.25)
    raise SetError("Live did not write %s" % path)


def save_as(name, directory=None):
    """Save the open Set, then continue under a new name in its own Project folder."""
    save()
    folder, path = _project_path(name, directory or DEFAULT_DIR)
    if path.exists():
        raise SetError("%s already exists; pick another name" % path)
    (folder / "Ableton Project Info").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_current_path(), path)
    out = open_set(path, "cancel")
    return dict(out, saved=True)
