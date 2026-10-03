"""Declarative device graphs: build any instrument/effect structure, read any device tree.

A device spec is recursive and carries no presets of its own:
  {"name": "<native device>"} or {"browser": {"root": ..., "path": [...]}}   # what to insert
  "params": {"<param>": value or "display value"}, "on": bool,
  "chains": [{"name", "devices": [<spec>...], "volume_db", "pan", "mute", "note", "choke"}],
  "macros": <visible macro count>, "macro_values": {"1": value, ...}
Racks nest to any depth; whatever the model describes is what gets built.
"""

from .ops import OpError, _index, op, param_value
from .ops_lom import resolve
from .ops_mix import _find_param, fader_for_db


def _apply(device, spec):
    for key, value in (spec.get("params") or {}).items():
        p = _find_param(device, key)
        p.value = param_value(p, value)
    if spec.get("on") is not None:
        device.parameters[0].value = 1.0 if spec["on"] else 0.0
    if spec.get("macros") and hasattr(device, "visible_macro_count"):
        while device.visible_macro_count < int(spec["macros"]):
            before = device.visible_macro_count
            device.add_macro()
            if device.visible_macro_count == before:
                break
    for key, value in (spec.get("macro_values") or {}).items():
        macro = _find_param(device, "Macro %s" % key if str(key).isdigit() else key)
        macro.value = param_value(macro, value)
    for i, chain_spec in enumerate(spec.get("chains") or []):
        if not getattr(device, "can_have_chains", False):
            raise OpError("%r is not a rack, so it can't have chains" % device.name)
        device.insert_chain(len(device.chains))
        _build_chain(list(device.chains)[-1], chain_spec)


def _build_chain(chain, spec):
    if spec.get("name"):
        chain.name = spec["name"]
    for j, child in enumerate(spec.get("devices") or []):
        _insert(chain, child, j)
    mixer = chain.mixer_device
    if spec.get("volume_db") is not None:
        mixer.volume.value = fader_for_db(mixer.volume, spec["volume_db"])
    if spec.get("pan") is not None:
        mixer.panning.value = param_value(mixer.panning, spec["pan"])
    if spec.get("mute") is not None:
        chain.mute = bool(spec["mute"])
    if spec.get("note") is not None:  # Drum Rack pad that triggers this chain
        chain.in_note = int(spec["note"])
    if spec.get("choke") is not None:
        chain.choke_group = int(spec["choke"])


def _insert(container, spec, index):
    """Insert a device into a track or chain from a native name or a browser path, then configure it."""
    if spec.get("name") and not spec.get("browser"):
        device = container.insert_device(spec["name"], index)
    elif spec.get("browser"):
        device = _load_browser(container, spec["browser"], index)
    else:
        raise OpError("a device spec needs name (native device) or browser {root, path}")
    _apply(device, spec)
    return device


def _load_browser(container, browser, index):
    """Load a browser item (preset, plug-in, kit) into a track's device chain."""
    from .ops import _browser_root

    ctx = _CTX[0]
    item = _browser_root(ctx, browser["root"])
    for part in browser["path"]:
        item = _index(item.children, part, "browser item")
    if not hasattr(container, "clip_slots"):
        raise OpError("browser items load onto tracks; inside racks use native device names")
    devices = list(container.devices)
    ctx.song.view.selected_track = container
    if devices:
        ctx.song.view.select_device(devices[-1])  # Live loads after the selected device: append
    before = len(devices)
    ctx.app.browser.load_item(item)
    after = list(container.devices)
    if len(after) <= before:
        raise OpError("Live did not load %s (it may finish on the next batch: inspect, then configure it)" % "/".join(browser["path"]))
    added = [d for d in after if d not in devices]
    return added[0] if added else after[-1]


_CTX = [None]


def tree(device, path, depth=0):
    out = {"name": device.name, "class": device.class_name, "path": path, "on": bool(device.parameters[0].value) if list(device.parameters) else None}
    if getattr(device, "can_have_chains", False) and depth < 8:
        out["chains"] = []
        for i, c in enumerate(device.chains):
            cp = "%s.chains[%d]" % (path, i)
            node = {"name": c.name, "path": cp, "devices": [tree(d, "%s.devices[%d]" % (cp, j), depth + 1) for j, d in enumerate(c.devices)]}
            if getattr(device, "can_have_drum_pads", False):
                node["note"] = c.in_note
            out["chains"].append(node)
        if hasattr(device, "visible_macro_count"):
            out["macros"] = [{"name": p.name, "value": p.str_for_value(p.value)} for p in list(device.parameters)[1:1 + device.visible_macro_count]]
    return out


@op("build_device")
def _build_device(ctx, track, device, index=-1, expect=None):
    """Build a device (and, for racks, its whole chain tree) from a spec onto a track."""
    _CTX[0] = ctx
    t = ctx.track(track, expect)
    idx = len(list(t.devices)) if index is None or index < 0 else index
    built = _insert(t, device, idx)
    pos = list(t.devices).index(built)
    return tree(built, 'song.tracks["%s"].devices[%d]' % (t.name, pos))


@op("device_tree")
def _device_tree(ctx, track, expect=None):
    """Every device on a track, including racks' chains and nested devices, each with its LOM path."""
    t = ctx.track(track, expect)
    return {"track": t.name, "devices": [tree(d, 'song.tracks["%s"].devices[%d]' % (t.name, i)) for i, d in enumerate(t.devices)]}


@op("configure")
def _configure(ctx, path, params=None, on=None, macros=None, macro_values=None, chains=None):
    """Apply a spec to an existing device at any depth (path from device_tree): params, on/off,
    macros, and extra chains appended to a rack."""
    _CTX[0] = ctx
    d = resolve(ctx, path)
    if not hasattr(d, "parameters"):
        raise OpError("%s is not a device" % path)
    _apply(d, {"params": params, "on": on, "macros": macros, "macro_values": macro_values, "chains": chains})
    return tree(d, path)
