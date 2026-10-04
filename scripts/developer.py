"""Cross-platform development entrypoints; runtime logic stays in the shipped plugin."""
import importlib
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_aae_developer_plugin"


def plugin_module(name):
    # Avoid executing Hermes registration while using the standalone stdlib tools.
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT / "plugin" / "actual-assistant-engineer")]
        sys.modules[PACKAGE] = package
    return importlib.import_module("." + name, PACKAGE)


def dispatch(request):
    action = request["action"]
    if action in ("bundle", "resources", "default_set"):
        return str(getattr(plugin_module("live_app"), action)())
    if action == "screenshot":
        return plugin_module("windows_helpers").screenshot(request["path"], pid=request.get("pid"), hwnd=request.get("hwnd"))
    if action == "open_set":
        return plugin_module("live_sets").open_set(request["path"], request.get("on_unsaved", "cancel"))
    if action == "save_as":
        return plugin_module("live_sets").save_as(request["name"], request["directory"])
    raise ValueError("unsupported developer action: %s" % action)


def serve(source, destination):
    """One process owns the verified Set associations; never recreate them from titles."""
    for line in source:
        try:
            response = {"ok": True, "result": dispatch(json.loads(line))}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        print(json.dumps(response), file=destination, flush=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["serve"]:
        serve(sys.stdin, sys.stdout)
        return 0
    if argv == ["json"]:
        try:
            print(json.dumps({"ok": True, "result": dispatch(json.load(sys.stdin))}))
            return 0
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    if argv and argv[0] == "install-live":
        import argparse
        cli = plugin_module("cli")
        parser = argparse.ArgumentParser()
        cli.configure(parser)
        args = parser.parse_args(["setup", *argv[1:]])
        return args.run(args)
    if argv == ["build-native"]:
        import shutil
        cli = plugin_module("cli")
        built = cli.build_native()
        if not built:
            print("Native helpers unavailable: macOS and swiftc are required")
        else:
            (ROOT / "bin").mkdir(exist_ok=True)
            for name in built:
                shutil.copy2(cli.helpers_dir() / name, ROOT / "bin" / name)
        return 0
    if argv == ["install-hermes"]:
        import shutil
        import subprocess
        import tempfile
        # Local development copy, not invented `plugins install <directory>` syntax.
        # Resolve the active profile using the same shipped storage resolver.
        home = plugin_module("platform_paths").data_dir().parents[1]
        parent = home / "plugins"
        target = parent / "actual-assistant-engineer"
        parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise FileExistsError("refusing to replace existing plugin: %s; back it up explicitly first" % target)
        stage = Path(tempfile.mkdtemp(prefix=".aae-dev-", dir=parent))
        try:
            copy = stage / target.name
            shutil.copytree(ROOT / "plugin" / target.name, copy,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            copy.rename(target)
        finally:
            shutil.rmtree(stage)
        return subprocess.run(["hermes", "plugins", "enable", target.name], shell=False, check=True).returncode
    raise SystemExit("usage: developer.py {json|serve|install-live|install-hermes|build-native}")


if __name__ == "__main__":
    sys.exit(main())
