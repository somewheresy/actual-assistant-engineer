"""`hermes aae ...`: set up, check, and gate the plugin without the source repo.

  hermes aae setup [--remote-scripts DIR] [--no-native]   install the Live control surface (+ helpers)
  hermes aae status                                        what's installed and connected
  hermes aae review --require arrangement,locators,...    completeness gate (exit 1 until complete)
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import live_app, live_client

HERE = Path(__file__).resolve().parent
SURFACE = HERE / "live" / "Hermes"
NATIVE = HERE / "native"
REMOTE_SCRIPTS = Path.home() / "Music" / "Ableton" / "User Library" / "Remote Scripts"
REQUIREMENTS = ("arrangement", "locators", "automation", "sidechain", "mix")
SELECT_HINT = 'In Live: Settings > Tempo & MIDI > Control Surface, choose "Hermes" in an empty slot (once).'


def helpers_dir():
    try:
        from plugins.plugin_storage import plugin_data_dir

        d = Path(plugin_data_dir("actual-assistant-engineer")) / "bin"
    except Exception:
        d = Path.home() / "Library" / "Application Support" / "ActualAssistantEngineer" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup(args):
    target_root = Path(args.remote_scripts).expanduser() if args.remote_scripts else REMOTE_SCRIPTS
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / "Hermes"
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        backup = target.with_name("Hermes.backup")
        shutil.move(str(target), str(backup))
        print("moved an existing Hermes folder to %s" % backup)
    target.symlink_to(SURFACE, target_is_directory=True)
    print("control surface: %s -> %s" % (target, SURFACE))
    if not args.no_native:
        built = build_native()
        print("helpers: %s" % (", ".join(built) if built else "skipped (no swiftc; only needed for the MIDI performance path and screenshots)"))
    try:
        print("Live: %s" % live_app.bundle())
    except FileNotFoundError as e:
        print("Live: not found (%s)" % e)
    print("bridge: %s" % ("connected" if _bridge_ok() else "not connected yet. Restart Live, then " + SELECT_HINT[3:]))
    return 0


def build_native():
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
    except FileNotFoundError:
        report["live"] = None
    link = REMOTE_SCRIPTS / "Hermes"
    report["control_surface_installed"] = link.exists()
    report["control_surface_path"] = str(link.resolve()) if link.exists() else None
    report["bridge"] = _bridge_ok()
    if report["bridge"]:
        info = live_client.batch([{"op": "info"}], timeout=5.0, undo_step=False)["results"][0]
        report["live_version"] = info.get("live_version")
    report["helpers"] = sorted(p.name for p in helpers_dir().iterdir()) if helpers_dir().exists() else []
    try:
        import pedalboard  # noqa: F401

        report["vst_host"] = True
    except ImportError:
        report["vst_host"] = False
    print(json.dumps(report, indent=2))
    if not report["control_surface_installed"]:
        print("next: hermes aae setup")
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
    sub = parser.add_subparsers(dest="aae_command", required=True)
    s = sub.add_parser("setup", help="install the Live control surface and build optional helpers")
    s.add_argument("--remote-scripts", help="Live's Remote Scripts folder (default: User Library)")
    s.add_argument("--no-native", action="store_true", help="skip building the Swift helpers")
    s.set_defaults(aae_func=setup)
    st = sub.add_parser("status", help="show what's installed and whether Live is connected")
    st.set_defaults(aae_func=status)
    rv = sub.add_parser("review", help="completeness gate for /goal: exit 1 until the Set has no gaps")
    rv.add_argument("--require", default="", help="comma list: " + ",".join(REQUIREMENTS))
    rv.add_argument("--min-sections", type=int)
    rv.set_defaults(aae_func=review)


def handle(args):
    sys.exit(args.aae_func(args))
