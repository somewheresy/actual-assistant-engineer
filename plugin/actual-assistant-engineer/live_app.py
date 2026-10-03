"""Locate the installed Ableton Live: whichever edition is running, else the newest installed.
Nothing about editions or install paths is assumed."""

import re
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def bundle():
    """Path to the Live .app bundle."""
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
    return bundle() / "Contents" / "App-Resources"


def default_set():
    """Live's built-in empty Set template."""
    return resources() / "Builtin" / "Templates" / "DefaultLiveSet.als"
