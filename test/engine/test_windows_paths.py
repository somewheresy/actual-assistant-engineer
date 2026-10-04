import ctypes
import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows native discovery and filesystem contracts"
)

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
P = importlib.import_module("actual-assistant-engineer.platform_paths")


def test_remote_scripts_uses_redirected_documents(tmp_path, monkeypatch):
    redirected = tmp_path / "OneDrive/Docs"

    def folder(hwnd, csidl, token, flags, output):
        assert csidl == 5
        output.value = str(redirected)
        return 0

    monkeypatch.setattr(ctypes.windll.shell32, "SHGetFolderPathW", folder)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("AAE_USER_LIBRARY", raising=False)
    monkeypatch.delenv("AAE_REMOTE_SCRIPTS", raising=False)
    assert P.remote_scripts() == redirected / "Ableton/User Library/Remote Scripts"


def test_library_cfg_numeric_version_and_explicit_override(tmp_path, monkeypatch):
    from xml.sax.saxutils import quoteattr

    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("AAE_USER_LIBRARY", raising=False)
    monkeypatch.delenv("AAE_REMOTE_SCRIPTS", raising=False)
    for version in ("12.9", "12.10"):
        cfg = (
            tmp_path
            / "Roaming/Ableton"
            / ("Live " + version)
            / "Preferences/Library.cfg"
        )
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "<Ableton><UserLibrary><LibraryProject><ProjectPath Value="
            + quoteattr(str(tmp_path))
            + '/><ProjectName Value="Library '
            + version
            + '"/></LibraryProject></UserLibrary></Ableton>'
        )
    assert P.remote_scripts() == tmp_path / "Library 12.10/Remote Scripts"
    monkeypatch.setenv("AAE_USER_LIBRARY", str(tmp_path / "Explicit"))
    assert P.remote_scripts() == tmp_path / "Explicit/Remote Scripts"
    monkeypatch.setenv("AAE_REMOTE_SCRIPTS", str(tmp_path / "Scripts"))
    assert P.remote_scripts() == tmp_path / "Scripts"
