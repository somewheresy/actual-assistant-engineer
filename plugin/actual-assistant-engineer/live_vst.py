"""VST3 plug-ins: find them, their parameters, and their presets; switch programs; expose any
parameter to Live; load preset files or parameter values as the plug-in's state.

Layers, cheapest first:
  1. programs exposed to Live (PluginDevice.presets / selected_preset_index), via the bridge
  2. preset files on disk (.vstpreset loaded in an offline host, state written into the Set)
  3. any parameter by name: written into the Set as one of Live's exposed parameters
Driving a plug-in's own window (vendor-format presets, custom pages) is computer use.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import als_automation as A
from . import als_plugins as P
from . import live_client, live_sets, platform_paths

HOST = Path(__file__).with_name("vst_host.py")
VST3_DIRS = None  # optional override; defaults are resolved at call time
PRESET_DIRS = None
LOADABLE = {".vstpreset"}  # the standard VST3 preset format; vendor formats need the plug-in's own browser
NOT_PRESETS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".txt", ".md", ".rtf", ".html", ".json", ".xml", ".plist", ".db",
               ".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a", ".zip", ".dmg", ".pkg", ".app", ".log", ".ds_store", ""}
CACHE = None


class VstError(Exception):
    pass


def catalog(query=None):
    out, seen = [], set()
    for base in VST3_DIRS if VST3_DIRS is not None else platform_paths.vst3_dirs():
        for root, dirs, files in os.walk(base):
            for name in sorted(dirs + files):
                p = Path(root) / name
                if p.suffix.lower() != ".vst3":
                    continue
                if name in dirs:
                    dirs.remove(name)  # never index a bundle's internal binary twice
                key = os.path.normcase(str(p.resolve()))
                if key not in seen and (not query or query.lower() in p.stem.lower()):
                    seen.add(key)
                    out.append({"name": p.stem, "path": str(p)})
    return sorted(out, key=lambda p: (p["name"].lower(), p["path"]))


def _key(name):
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _bundle(plugin):
    """The .vst3 bundle for a plug-in name as Live shows it (spacing/punctuation-insensitive)."""
    hits = [c for c in catalog() if _key(c["name"]) == _key(plugin)] or [c for c in catalog() if _key(plugin) in _key(c["name"])]
    if not hits:
        raise VstError("no VST3 named %r installed" % plugin)
    return hits[0]["path"]


def _python(bundle=None):
    """Select a host matching the plug-in binary, not the OS or Hermes architecture."""
    import importlib.util
    import shutil
    import sys
    from .vst_host import binary_architecture

    target = None
    compatible = True
    if sys.platform == "win32":
        try:
            current = binary_architecture(sys.executable)
            target = binary_architecture(bundle) if bundle else "x86_64"
        except (OSError, ValueError) as exc:
            raise VstError(str(exc)) from exc
        compatible = target == current
    if compatible and importlib.util.find_spec("pedalboard") is not None:
        return [sys.executable]
    uv = shutil.which("uv")
    if uv:
        python = "cpython-3.11-windows-%s-none" % target if target else "3.11"
        return [uv, "run", "-q", "--no-project", "--isolated", "--python", python, "--with", "pedalboard", "python"]
    raise VstError("offline VST host needs %s Python with pedalboard; install uv to provision a compatible host (Windows ARM64 can run an x86_64 host under Prism)" % (target or "compatible"))


def _host(*args, plugin=None, timeout=180):
    """Run the offline host. Bundles holding several plug-ins need one named: retry with the
    plug-in's own name when the host says so."""
    cmd = [*_python(args[1] if len(args) > 1 else None), str(HOST), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if r.returncode != 0 and "contains" in r.stderr and "plugin_name" in r.stderr and plugin and "--plugin" not in args:
            r = subprocess.run(cmd + ["--plugin", plugin], capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VstError("offline VST host timed out; the plug-in may require licensing or a visible UI") from exc
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise VstError(" ".join(msg[-4:]) or "plug-in host failed")
    return json.loads(r.stdout)


def params(plugin, query=None, bundle_plugin=None, limit=60):
    """All of a plug-in's parameters (cached per plug-in), optionally filtered by name."""
    cache = CACHE if CACHE is not None else platform_paths.data_dir() / "cache/vst"
    cache.mkdir(parents=True, exist_ok=True)
    bundle = Path(_bundle(plugin))
    identity = json.dumps([str(bundle.resolve()), bundle.stat().st_mtime_ns, bundle_plugin or plugin])
    key = cache / (hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json")
    if key.exists():
        data = json.loads(key.read_text())
    else:
        args = ["params", _bundle(plugin)] + (["--plugin", bundle_plugin] if bundle_plugin else [])
        data = _host(*args, plugin=plugin)
        key.write_text(json.dumps(data))
    if query:
        terms = query.lower().split()
        data = [p for p in data if all(t in p["name"].lower() for t in terms)]
    return {"plugin": bundle_plugin or plugin, "count": len(data), "params": data[:limit] if limit else data}


def presets(plugin, query=None, limit=50):
    """Preset files on disk whose path mentions the plug-in (vendor folders), by name/category."""
    words = [w for w in plugin.lower().replace("-", " ").split() if len(w) > 1]
    found, seen = [], set()
    for base in PRESET_DIRS if PRESET_DIRS is not None else platform_paths.preset_dirs():
        if not base.exists():
            continue
        depth0 = len(base.parts)
        for root, dirs, files in os.walk(base):
            if len(Path(root).parts) - depth0 > 7:
                dirs[:] = []
                continue
            lower = root.lower()
            if not all(w in lower for w in words):
                if base == Path.home() / "Documents" and len(Path(root).parts) - depth0 > 2:
                    dirs[:] = []
                continue
            for f in files:
                ext = Path(f).suffix.lower()
                if f.startswith(".") or ext in NOT_PRESETS:
                    continue
                if not query or all(t in (root + "/" + f).lower() for t in query.lower().split()):
                    path = os.path.join(root, f)
                    key = os.path.normcase(os.path.realpath(path))
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append({"name": Path(f).stem, "path": path, "loadable": ext in LOADABLE, "format": ext[1:]})
                    if len(found) >= limit:
                        return {"plugin": plugin, "presets": found, "truncated": True}
    return {"plugin": plugin, "presets": found}


def _bridge(op):
    res = live_client.batch([op], timeout=30.0, undo_step=False)
    r = (res.get("results") or [{}])[0]
    if not res.get("ok"):
        raise VstError(r.get("error") or res.get("error") or "bridge error")
    return r


def _device_index(track, device):
    tree = _bridge({"op": "device_tree", "track": track})
    names = [d["name"] for d in tree["devices"]]
    if isinstance(device, int):
        return device
    if device in names:
        return names.index(device)
    raise VstError("no device %r on %s (have %s)" % (device, track, ", ".join(names)))


def programs(track, device):
    i = _device_index(track, device)
    r = _bridge({"op": "get", "path": 'song.tracks["%s"].devices[%d]' % (track, i), "props": ["name", "presets", "selected_preset_index"]})
    v = r["values"]
    return {"device": v["name"], "programs": v.get("presets") or [], "selected": v.get("selected_preset_index")}


def select_program(track, device, program):
    i = _device_index(track, device)
    progs = programs(track, i)["programs"]
    idx = program if isinstance(program, int) else next((k for k, n in enumerate(progs) if n == program), None)
    if idx is None:
        raise VstError("no program %r (this plug-in exposes %d programs to Live)" % (program, len(progs)))
    _bridge({"op": "set", "path": 'song.tracks["%s"].devices[%d]' % (track, i), "prop": "selected_preset_index", "value": idx})
    return programs(track, i)


def _edit_set(track, device_index, edit):
    """Save the Set, apply edit(plugin_device_element) to its file, reopen it."""
    path = live_sets._current_path()
    if not path:
        raise VstError("the open Set's file is unknown: open or create it with live_set first")
    live_sets.save()
    tree = A.load(path)
    dev = P.plugin_device(tree, track, device_index)
    result = edit(dev)
    A.save(tree, path)
    live_sets.open_set(path, "cancel")
    return result


def expose(track, device, names, bundle_plugin=None):
    """Make these plug-in parameters (by name) Live-exposed, so set_params/automation reach them."""
    i = _device_index(track, device)
    plugin = _bridge({"op": "device_tree", "track": track})["devices"][i]["name"]
    catalog_params = params(plugin, bundle_plugin=bundle_plugin, limit=100000)["params"]
    by_name = {p["name"].lower(): p for p in catalog_params}
    chosen, missing = [], []
    for n in names:
        p = by_name.get(n.lower()) or next((q for q in catalog_params if n.lower() in q["name"].lower()), None)
        (chosen.append((p["index"], p["name"])) if p else missing.append(n))
    if missing:
        raise VstError("no parameters named %s on %s (search with action params)" % (missing, plugin))
    _edit_set(track, i, lambda dev: P.expose(dev, chosen))
    after = _bridge({"op": "device_params", "track": track, "device": i})
    shown = [p["name"] for p in after["params"]]
    return {"device": plugin, "requested": [n for _, n in chosen], "exposed_now": shown}


def load_state(track, device, preset=None, values=None, bundle_plugin=None):
    """Replace the plug-in's state with a .vstpreset file and/or parameter values (by host name)."""
    i = _device_index(track, device)
    plugin = _bridge({"op": "device_tree", "track": track})["devices"][i]["name"]
    if preset and not preset.lower().endswith(".vstpreset"):
        raise VstError("only .vstpreset files load offline; %s needs the plug-in's own browser (computer use)" % Path(preset).name)
    args = ["state", _bundle(plugin)] + (["--plugin", bundle_plugin] if bundle_plugin else [])
    if preset:
        args += ["--preset", preset]
    catalog_params = params(plugin, bundle_plugin=bundle_plugin, limit=100000)["params"] if values else []
    for name, v in (values or {}).items():
        p = next((q for q in catalog_params if q["name"].lower() == name.lower()), None)
        if not p:
            raise VstError("no parameter %r on %s" % (name, plugin))
        args += ["--set", "%s=%s" % (p["key"], v)]
    blobs = _host(*args, plugin=plugin)
    _edit_set(track, i, lambda dev: P.set_state(dev, blobs["processor_hex"], blobs["controller_hex"]))
    verified = {}
    if values:
        after = _bridge({"op": "device_params", "track": track, "device": i})
        available = {p["name"].casefold(): p for p in after.get("params", [])}
        for name, expected in values.items():
            param = available.get(name.casefold())
            if param is None:
                raise VstError("state applied, but read-back is unavailable for %r; expose it and inspect before retrying" % name)
            lo, hi = param.get("min", 0), param.get("max", 1)
            actual = (param["value"] - lo) / (hi - lo) if hi != lo else param["value"]
            if abs(actual - float(expected)) > 1e-5:
                raise VstError("state applied, but read-back for %r is %s, expected %s; inspect before retrying" % (name, actual, expected))
            verified[name] = actual
    return {"device": plugin, "loaded": preset or "parameter values", "bytes": len(blobs["processor_hex"]) // 2,
            "verified_parameters": verified,
            "verification": "requested values read back from Live" if values else "preset state reopened; no expected values supplied for read-back"}
