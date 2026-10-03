"""OS locations and profile-scoped storage, resolved at call time."""

import os
import re
import xml.etree.ElementTree as ET
import sys
from pathlib import Path


def data_dir():
    try:
        from plugins.plugin_storage import plugin_data_dir
    except ImportError:
        try:
            from hermes_constants import get_hermes_home
        except ImportError:
            home = Path(
                os.environ.get("HERMES_HOME")
                or (
                    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
                    / "hermes"
                    if sys.platform == "win32"
                    else Path.home() / ".hermes"
                )
            )
        else:
            home = get_hermes_home()
        return home / "plugin-data" / "actual-assistant-engineer"
    return Path(plugin_data_dir("actual-assistant-engineer"))


def preset_dirs():
    extra = [
        Path(os.path.expandvars(p)).expanduser()
        for p in os.environ.get("AAE_PRESET_PATHS", "").split(os.pathsep)
        if p
    ]
    if sys.platform == "win32":
        roots = [
            Path(os.environ[name]) / "VST3 Presets"
            for name in ("PROGRAMDATA", "APPDATA")
            if os.environ.get(name)
        ]
        return extra + roots + [documents() / "VST3 Presets", documents()]
    return extra + [
        Path("/Library/Audio/Presets"),
        Path.home() / "Library/Audio/Presets",
        documents(),
    ]


def vst3_dirs():
    extra = [
        Path(os.path.expandvars(p)).expanduser()
        for p in os.environ.get("AAE_VST3_PATHS", "").split(os.pathsep)
        if p
    ]
    if sys.platform == "win32":
        common = os.environ.get("CommonProgramW6432") or os.environ.get(
            "COMMONPROGRAMFILES"
        )
        roots = [Path(common) / "VST3"] if common else []
        roots.append(
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "Programs/Common/VST3"
        )
        return extra + roots
    return extra + [
        Path("/Library/Audio/Plug-Ins/VST3"),
        Path.home() / "Library/Audio/Plug-Ins/VST3",
    ]


def documents():
    if sys.platform == "win32":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PERSONAL resolves Windows' redirected Documents known folder.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) != 0:
            raise OSError(
                "Cannot resolve Windows Documents; set AAE_USER_LIBRARY or --remote-scripts"
            )
        return Path(buffer.value)
    return Path.home() / "Documents"


def remote_scripts():
    override = os.environ.get("AAE_REMOTE_SCRIPTS")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    override = os.environ.get("AAE_USER_LIBRARY")
    if override:
        return Path(os.path.expandvars(override)).expanduser() / "Remote Scripts"
    prefs = (
        Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Ableton"
        if sys.platform == "win32"
        else Path.home() / "Library/Preferences/Ableton"
    )
    versions = sorted(
        prefs.glob("Live *"),
        key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)),
        reverse=True,
    )
    for version in versions:
        for cfg in (version / "Preferences/Library.cfg", version / "Library.cfg"):
            if not cfg.is_file():
                continue
            try:
                xml = ET.parse(cfg)
                project = xml.find(".//UserLibrary/LibraryProject")
                if project is None:
                    continue
                parent = project.find("ProjectPath")
                name = project.find("ProjectName")
                if parent is not None and name is not None:
                    base = Path(
                        os.path.expandvars(parent.get("Value", ""))
                    ).expanduser()
                    leaf = name.get("Value", "")
                    if (
                        base.is_absolute()
                        and leaf
                        and Path(leaf).name == leaf
                        and leaf not in (".", "..")
                    ):
                        return base / leaf / "Remote Scripts"
            except (OSError, ET.ParseError) as exc:
                raise OSError(
                    "Cannot read %s; set AAE_USER_LIBRARY or --remote-scripts: %s"
                    % (cfg, exc)
                ) from exc
    base = documents() if sys.platform == "win32" else Path.home() / "Music"
    return base / "Ableton" / "User Library" / "Remote Scripts"
