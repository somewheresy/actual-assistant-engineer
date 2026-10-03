"""`hermes assistant-engineer ...`: set up, check, and gate the plugin without the source repo.

  hermes assistant-engineer setup [--remote-scripts DIR] [--no-native]   install the Live control surface (+ helpers)
  hermes assistant-engineer status                                        what's installed and connected
  hermes assistant-engineer review --require arrangement,locators,...    completeness gate (exit 1 until complete)
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import live_app, live_client, platform_paths

HERE = Path(__file__).resolve().parent
SURFACE = HERE / "live" / "Hermes"
NATIVE = HERE / "native"
def remote_scripts(explicit=None):
    if explicit:
        return Path(os.path.expandvars(explicit)).expanduser()
    if os.environ.get("AAE_REMOTE_SCRIPTS") or os.environ.get("AAE_USER_LIBRARY"):
        return platform_paths.remote_scripts()
    saved = platform_paths.data_dir() / "setup.json"
    if saved.is_file():
        return Path(json.loads(saved.read_text(encoding="utf-8"))["remote_scripts"])
    return platform_paths.remote_scripts()

REQUIREMENTS = ("arrangement", "locators", "automation", "sidechain", "mix")
SELECT_HINT = 'In Live: Settings > Tempo & MIDI > Control Surface, choose "Hermes" in an empty slot (once).'


def helpers_dir():
    d = platform_paths.data_dir() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _surface_files(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def install_surface(target_root):
    """Stage first; preserve every previous installation without symlink privileges."""
    if not (SURFACE / "__init__.py").is_file():
        raise FileNotFoundError("bundled Hermes control surface is missing: %s" % SURFACE)
    source = SURFACE.resolve()
    destination = target_root.resolve() / "Hermes"
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("control surface source and target must not overlap")
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / "Hermes"
    linked = target.is_symlink() or (target.exists() and bool(getattr(target.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))
    if target.is_dir() and not linked and _surface_files(target) == _surface_files(SURFACE):
        return target
    stage_root = Path(tempfile.mkdtemp(prefix=".Hermes-stage-", dir=target_root))
    stage = stage_root / "Hermes"
    backup = None
    try:
        shutil.copytree(SURFACE, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if _surface_files(stage) != _surface_files(SURFACE):
            raise OSError("control surface copy verification failed")
        if target.exists() or target.is_symlink():
            backup = Path(tempfile.mkdtemp(prefix="Hermes.backup-", dir=target_root)) / "Hermes"
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if backup is not None:
                backup.rename(target)
            raise
        if backup:
            print("preserved existing Hermes at %s" % backup)
    finally:
        shutil.rmtree(stage_root)
    return target


def setup(args):
    target_root = remote_scripts(args.remote_scripts)
    target = install_surface(target_root)
    data = platform_paths.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "setup.json").write_text(json.dumps({"remote_scripts": str(target_root.resolve())}), encoding="utf-8")
    print("control surface: %s (copied)" % target)
    if not args.no_native:
        built = build_native()
        print("helpers: %s" % (", ".join(built) if built else "unavailable (macOS-only Swift helpers)" if sys.platform != "darwin" else "skipped (no swiftc)"))
    try:
        print("Live: %s" % live_app.bundle())
    except FileNotFoundError as e:
        print("Live: not found (%s)" % e)
    print("bridge: %s" % ("connected" if _bridge_ok() else "not connected yet. Restart Live, then " + SELECT_HINT[3:]))
    if args.index_plugins:
        index_plugins()
    return 0


def index_plugins():
    """Cache every installed VST3's parameter list now, so lookups in a session are instant."""
    from importlib import import_module
    live_vst = import_module(".live_vst", __package__)

    plugins = live_vst.catalog()
    ok = 0
    for i, p in enumerate(plugins, 1):
        try:
            live_vst.params(p["name"], limit=0)
            ok += 1
            print("[%d/%d] %s" % (i, len(plugins), p["name"]))
        except Exception as e:
            print("[%d/%d] %s: skipped (%s)" % (i, len(plugins), p["name"], str(e)[:120]))
    print("indexed %d of %d plug-ins" % (ok, len(plugins)))


def build_native():
    if sys.platform != "darwin":
        return []
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return []
    out, built = helpers_dir(), []
    for src in sorted(NATIVE.glob("*/main.swift")):
        exe = out / src.parent.name
        r = subprocess.run([swiftc, "-O", str(src), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode == 0:
            built.append(exe.name)
        else:
            print("could not build %s: %s" % (src.parent.name, r.stderr.strip().splitlines()[-1:]), file=sys.stderr)
    return built


def _bridge_ok():
    if not live_client.available():
        return False
    try:
        return bool(live_client.batch([{"op": "ping"}], timeout=3.0, undo_step=False).get("ok"))
    except Exception:
        return False


def status(args):
    report = {}
    try:
        report["live"] = str(live_app.bundle())
    except FileNotFoundError as exc:
        report["live"] = None
        report["live_error"] = str(exc)
    link = remote_scripts(getattr(args, "remote_scripts", None)) / "Hermes"
    report["control_surface_installed"] = (link / "__init__.py").is_file()
    report["native_helpers_supported"] = sys.platform == "darwin"
    report["control_surface_path"] = str(link.resolve()) if link.exists() else None
    report["bridge"] = _bridge_ok()
    if report["bridge"]:
        info = live_client.batch([{"op": "info"}], timeout=5.0, undo_step=False)["results"][0]
        report["live_version"] = info.get("live_version")
    report["helpers"] = sorted(p.name for p in helpers_dir().iterdir()) if helpers_dir().exists() else []
    from importlib import import_module
    live_vst = import_module(".live_vst", __package__)
    report["vst_host"] = False
    report["vst_host_status"] = "not probed; use status --probe-vst (may download Python/pedalboard via uv)"
    try:
        report["vst_host_command"] = live_vst._python()
        if getattr(args, "probe_vst", False):
            report["vst_host_details"] = live_vst._host("probe")
            report["vst_host"] = True
            report["vst_host_status"] = "ready; individual plug-in compatibility not verified"
    except (live_vst.VstError, OSError) as exc:
        report["vst_host_status"] = "unavailable"
        report["vst_host_error"] = str(exc)
    print(json.dumps(report, indent=2))
    if not report["control_surface_installed"]:
        print("next: hermes assistant-engineer setup")
    elif not report["bridge"]:
        print("next: " + SELECT_HINT)
    return 0 if report["bridge"] else 1


def review(args):
    require = {k: True for k in (args.require or "").split(",") if k in REQUIREMENTS}
    if args.min_sections:
        require["min_sections"] = args.min_sections
    if not _bridge_ok():
        print("Live bridge not connected. " + SELECT_HINT)
        return 2
    r = live_client.batch([{"op": "review", "require": require}], timeout=60.0, undo_step=False)["results"][0]
    if r.get("complete"):
        a = r["arrangement"]
        print("complete: %g bars, sections %s" % (a["bars"], " > ".join(l["name"] for l in a["locators"])))
        return 0
    print("gaps:\n- " + "\n- ".join(r.get("gaps") or [r.get("error", "review failed")]))
    return 1


def configure(parser):
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("setup", help="install the Live control surface and build optional helpers")
    s.add_argument("--remote-scripts", help="Live's Remote Scripts folder (default: User Library)")
    s.add_argument("--no-native", action="store_true", help="skip building the Swift helpers")
    s.add_argument("--index-plugins", action="store_true", help="cache every installed VST3's parameters now (takes a few seconds per plug-in)")
    s.set_defaults(run=setup)
    st = sub.add_parser("status", help="show what's installed and whether Live is connected")
    st.add_argument("--probe-vst", action="store_true", help="verify offline host; may provision Python/pedalboard via uv")
    st.set_defaults(run=status)
    rv = sub.add_parser("review", help="completeness gate for /goal: exit 1 until the Set has no gaps")
    rv.add_argument("--require", default="", help="comma list: " + ",".join(REQUIREMENTS))
    rv.add_argument("--min-sections", type=int)
    rv.set_defaults(run=review)


def handle(args):
    sys.exit(args.run(args))
