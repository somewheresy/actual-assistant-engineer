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
            raise UIError("multiple processes for Live; close the extra instance")
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
        nested = [w for w in descendants if w.element_info.control_type == "Window" and
                  w.is_visible() and w.iface_window.CurrentIsModal]
        dialogs = []
        for w in [w for w in windows if w is not window] + nested:
            self._scope(w, process["pid"])
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
                        window, tuple(dialogs))

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
