"""Locate the installed Ableton Live: whichever edition is running, else the newest installed.
Nothing about editions or install paths is assumed."""

import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def _registered_executables():
    """Read standard Windows uninstall metadata; no Ableton-specific registry schema."""
    import winreg
    result = []
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as root:
                    index = 0
                    while True:
                        try:
                            key = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root, key) as product:
                                display = winreg.QueryValueEx(product, "DisplayName")[0]
                                if not re.match(r"^Ableton Live\b", display, re.I):
                                    continue
                                location = winreg.QueryValueEx(product, "InstallLocation")[0]
                                if location:
                                    base = Path(os.path.expandvars(location))
                                    result.extend(p for pattern in ("Program/Ableton Live*.exe", "Ableton Live*.exe")
                                                  for p in base.glob(pattern) if p.is_file())
                        except OSError:
                            continue
            except OSError:
                continue
    return result


@lru_cache(maxsize=0)  # retain cache_clear API; process/install/profile may change
def bundle():
    """Live .app on macOS, executable on Windows; AAE_LIVE_PATH overrides discovery."""
    configured = os.environ.get("AAE_LIVE_PATH")
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if sys.platform == "win32":
            if not path.is_file() or path.suffix.lower() != ".exe":
                raise FileNotFoundError("AAE_LIVE_PATH must name an existing Ableton Live .exe: %s" % path)
        elif not path.is_dir():
            raise FileNotFoundError("AAE_LIVE_PATH must name an existing Live .app: %s" % path)
        return path
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
                 "Get-CimInstance Win32_Process -Filter \"Name LIKE 'Ableton Live%.exe'\" | "
                 "Select-Object ExecutablePath | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8", timeout=10)
            rows = json.loads(result.stdout or "[]") if result.returncode == 0 else []
            for row in ([rows] if isinstance(rows, dict) else rows or []):
                value = row.get("ExecutablePath")
                if value:
                    path = Path(value)
                    if re.fullmatch(r"Ableton Live.*\.exe", path.name, re.I) and path.is_file():
                        return path
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        candidates = _registered_executables()
        for env in ("ProgramData", "ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                candidates.extend(p for p in (Path(base) / "Ableton").glob("Live*/Program/Ableton Live*.exe") if p.is_file())
        if not candidates:
            raise FileNotFoundError("no Ableton Live executable found; install Live or set AAE_LIVE_PATH to its .exe")
        def version(path):
            versions = re.findall(r"Live\s+(\d+(?:\.\d+)*)", path.parent.parent.name + " " + path.name, re.I)
            return max((tuple(int(n) for n in v.split(".")) for v in versions), default=()), str(path)
        return max(candidates, key=version)
    try:
        ps = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=5).stdout
        for line in ps.splitlines():
            m = re.search(r"^(.*?/Ableton Live[^/]*\.app)/Contents/MacOS/Live$", line.strip())
            if m:
                return Path(m.group(1))
    except Exception:
        pass
    candidates = []
    for base in (Path("/Applications"), Path.home() / "Applications"):
        candidates += sorted(base.glob("Ableton Live*.app"))
    if not candidates:
        raise FileNotFoundError("no Ableton Live app found in /Applications or ~/Applications")
    return candidates[-1]


def name():
    """Application name for `open -a`."""
    return bundle().stem


def resources():
    app = bundle()
    return app.parent.parent / "Resources" if sys.platform == "win32" else app / "Contents" / "App-Resources"


def default_set():
    """Live's built-in empty Set template."""
    path = resources() / "Builtin" / "Templates" / "DefaultLiveSet.als"
    if not path.is_file():
        raise FileNotFoundError("Live built-in template is missing: %s; repair the installation or choose an explicit template" % path)
    return path
