"""Process-scoped Windows accessibility for Live, never keyboard/mouse input.

UIA support is a runtime capability, not an assumption: inaccessible, ambiguous,
localized or unsupported controls fail closed. English title/control labels need
validation against the installed Live version. Dependencies are imported lazily.
"""
import os
import re
from dataclasses import dataclass


class UIError(RuntimeError):
    pass


class WindowNotReady(UIError):
    """Enumeration succeeded but no recognized main window exists yet."""


@dataclass(frozen=True)
class Snapshot:
    name: str
    identity: tuple
    window: object
    dialogs: tuple
    modified: bool = True


def set_filename(edit, path, pid):
    """Set the common dialog's native edit and notify its owning combo.

    UIA ValuePattern alone changes the visible text but Live's shell dialog can
    retain the old filename. These are HWND-scoped control messages, not keys.
    """
    import win32gui
    import win32process
    handle = edit.handle
    if edit.element_info.process_id != pid or win32process.GetWindowThreadProcessId(handle)[1] != pid:
        raise UIError('filename control belongs to another process')
    combo = win32gui.GetParent(handle)
    sink = win32gui.GetParent(combo)
    for hwnd in (combo, sink):
        if not hwnd or win32process.GetWindowThreadProcessId(hwnd)[1] != pid:
            raise UIError('filename parent belongs to another process')
    import ctypes
    text = ctypes.create_unicode_buffer(str(path))
    win32gui.SendMessageTimeout(handle, 12, 0, ctypes.addressof(text), 2, 5000)  # WM_SETTEXT
    if edit.iface_value.CurrentValue != str(path):
        raise UIError('Live Save dialog did not accept the requested filename')
    win32gui.SendMessageTimeout(handle, 185, 1, 0, 2, 5000)  # EM_SETMODIFY
    win32gui.PostMessage(combo, 273, (768 << 16) | win32gui.GetDlgCtrlID(handle), handle)  # EN_CHANGE
    win32gui.PostMessage(sink, 273, (5 << 16) | win32gui.GetDlgCtrlID(combo), combo)  # CBN_EDITCHANGE


class LiveUI:
    def __init__(self, executable, *, processes=None, connect=None):
        self.executable = executable
        self._processes = processes or self._native_processes
        self._connect = connect or self._native_connect

    @staticmethod
    def _native_processes():
        try:
            import psutil
        except ImportError as exc:
            raise UIError("Windows Live accessibility requires psutil and pywinauto in Hermes' Python environment") from exc
        return (p.info for p in psutil.process_iter(attrs=["pid", "name", "exe", "create_time"]))

    @staticmethod
    def _native_connect(pid):
        try:
            from pywinauto import Application
        except ImportError as exc:
            raise UIError("Windows Live accessibility requires pywinauto (UIA) in Hermes' Python environment") from exc
        return Application(backend="uia").connect(process=pid, timeout=5)

    def snapshot(self):
        try:
            return self._snapshot()
        except UIError:
            raise
        except Exception as exc:
            raise UIError("cannot inspect Live accessibility: %s" % exc) from exc

    def _snapshot(self):
        processes = list(self._processes())
        if any(not p.get("exe") and (p.get("name") or "").casefold() == self.executable.name.casefold()
               for p in processes):
            raise UIError("cannot verify executable path of a Live process (access denied)")
        expected = os.path.normcase(os.path.abspath(self.executable))
        matches = [p for p in processes if p.get("exe") and
                   os.path.normcase(os.path.abspath(p["exe"])) == expected]
        if not matches:
            return None
        if len(matches) != 1:
            raise WindowNotReady("multiple processes for Live; waiting for the forwarding launcher to exit (close extra instances if this persists)")
        process = matches[0]
        windows = [w for w in self._connect(process["pid"]).windows() if w.is_visible()]
        for w in windows:
            self._scope(w, process["pid"])
        main = [w for w in windows if set_name(w.window_text()) and not w.iface_window.CurrentIsModal]
        if not main:
            raise WindowNotReady("Live has no recognized main Set window; loading, minimized, or blocked by another window")
        if len(main) != 1:
            raise UIError("Live's main Set window is inaccessible or ambiguous")
        window = main[0]
        descendants = window.descendants()
        nested = [w for w in descendants if w.is_visible() and (
            (w.element_info.control_type == "Window" and w.iface_window.CurrentIsModal)
            # Live's licensing/save overlays can be native child Pane HWNDs,
            # not UIA WindowPattern modals. Never mistake these for a clear Set.
            or (w.element_info.control_type == "Pane" and w.element_info.handle
                and w.descendants(control_type="TitleBar")))]
        dialogs = []
        for w in [w for w in windows if w is not window] + nested:
            self._scope(w, process["pid"])
            # Live reports its floating VST3 editor host as modal, even though it
            # is not a blocking Set dialog. Vendor child dialogs remain visible.
            if getattr(w, 'class_name', lambda: '')() == 'Vst3PlugWindow':
                continue
            children = [n for n in w.descendants() if n.is_visible()]
            for child in children:
                self._scope(child, process["pid"])
            texts = [n.window_text() for n in children if n.element_info.control_type == "Text"]
            buttons = tuple(n.window_text() for n in children if n.element_info.control_type == "Button")
            dialogs.append(Dialog(" ".join(t for t in texts if t) or w.window_text() or "unreadable Live dialog", buttons, w))
        if not dialogs and not window.is_enabled():
            raise UIError("Live's main window is disabled but its blocking dialog is inaccessible")
        return Snapshot(set_name(window.window_text()),
                        (process["pid"], process["create_time"], window.element_info.handle),
                        window, tuple(dialogs), window.window_text().split(" - Ableton Live", 1)[0].rstrip().endswith("*"))

    def menu(self, item):
        try:
            state = self.snapshot()
            if state is None or state.dialogs:
                raise UIError("Live must have one accessible Set and no dialogs before a menu action")
            file_menu = self._unique(state.window.descendants(control_type="MenuItem"), "File", state.identity[0])
            file_menu.iface_expand_collapse.Expand()
            # Native UIA menus can be separate top-level windows of the same PID.
            nodes = []
            for root in self._connect(state.identity[0]).windows():
                self._scope(root, state.identity[0])
                nodes.extend(root.descendants(control_type="MenuItem"))
            target = self._unique(nodes, item, state.identity[0])
            target.iface_invoke.Invoke()
        except UIError:
            raise
        except Exception as exc:
            raise UIError("cannot invoke Live File > %s through UIA: %s" % (item, exc)) from exc

    def save_file(self, path, overwrite=False):
        """Complete Live's native Save As panel using exact process-bound controls."""
        import time
        import win32gui
        import win32process
        from pathlib import Path
        target_path = Path(path).resolve()
        if target_path.suffix.lower() != '.als' or not target_path.parent.is_dir():
            raise UIError('Save target must be an .als in an existing directory')
        if target_path.exists() and not overwrite:
            raise UIError('refusing to overwrite existing Set')
        state = self.snapshot()
        if state is None:
            raise UIError('Live is not running')
        pid = state.identity[0]
        def native_button(node):
            self._scope(node, pid)
            if not node.handle or win32process.GetWindowThreadProcessId(node.handle)[1] != pid:
                raise UIError('native save button belongs to another process')
            win32gui.PostMessage(node.handle, 245, 0, 0)  # BM_CLICK, no cursor/focus
        panels = [d.window for d in state.dialogs if d.window.window_text() == 'Save Live Set As:']
        if len(panels) != 1:
            raise UIError('expected one Live Save As panel')
        panel = panels[0]
        edit = self._unique(panel.descendants(control_type='Edit'), 'File name:', pid)
        set_filename(edit, str(target_path), pid)
        time.sleep(0.15)  # let the native combo consume its change notifications
        native_button(self._unique(panel.descendants(control_type='Button'), 'Save', pid))
        confirmed = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = self.snapshot()
            if state is None or state.identity[0] != pid:
                raise UIError('Live changed process during save; outcome unknown')
            if state.name == target_path.stem and not state.dialogs and target_path.is_file():
                return {'path': str(target_path), 'saved': True}
            for dialog in state.dialogs:
                if dialog.window.window_text() == 'Confirm Save As':
                    if not overwrite or confirmed or target_path.name not in dialog.text or 'already exists' not in dialog.text:
                        raise UIError('unexpected overwrite confirmation; no response sent')
                    native_button(self._unique(dialog.window.descendants(control_type='Button'), 'Yes', pid))
                    confirmed = True
            time.sleep(0.15)
        raise UIError('Live Save As did not finish; outcome unknown, inspect before retrying')

    def click(self, label, expected_text=None):
        try:
            state = self.snapshot()
            if state is None or len(state.dialogs) != 1:
                raise UIError("expected exactly one Live dialog")
            dialog = state.dialogs[0]
            if expected_text is not None and dialog.text != expected_text:
                raise UIError("Live dialog changed before the response; inspect it")
            target = self._unique(dialog.window.descendants(control_type="Button"), label, state.identity[0])
            target.iface_invoke.Invoke()
        except UIError:
            raise
        except Exception as exc:
            raise UIError("cannot invoke Live dialog button %s through UIA: %s" % (label, exc)) from exc

    def _unique(self, nodes, label, pid):
        matches = [n for n in nodes if n.is_visible() and
                   n.window_text().split("\t", 1)[0].replace("&", "").strip() == label]
        if len(matches) != 1:
            raise UIError("Live UIA control %r is missing or ambiguous" % label)
        target = matches[0]
        self._scope(target, pid)
        if not target.is_enabled():
            raise UIError("Live UIA control %r is disabled" % label)
        return target

    @staticmethod
    def _scope(node, pid):
        if node.element_info.process_id != pid:
            raise UIError("accessibility element belongs to another process")


@dataclass(frozen=True)
class Dialog:
    text: str
    buttons: tuple
    window: object


def set_name(title):
    """Strip only Live's application suffix and its modified marker."""
    match = re.fullmatch(r"(.+) - Ableton Live \d+(?:\.\d+)*(?: (?:Suite|Standard|Intro|Lite|Trial))?", title)
    if not match:
        return None
    name = match.group(1).rstrip().removesuffix("*").rstrip()
    return name.removesuffix(".als") or None
