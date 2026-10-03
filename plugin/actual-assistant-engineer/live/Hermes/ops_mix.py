"""Devices, FX chains, routing, sidechain, and mixer state."""

import math

from .ops import OpError, _index, op, param_value

UNITY = 0.85


def db_to_fader(db):
    """dB -> Live's fader value (0.85 = 0 dB, 1.0 = +6 dB, 0 = -inf).

    Approximates Live's curve with dB = 40*log10(value/0.85) below unity,
    clamped at -70 dB; integration tests compare against Live's display.
    """
    if db is None:
        raise OpError("db must be a number")
    db = float(db)
    if db <= -70.0:
        return 0.0
    if db >= 6.0:
        return 1.0
    if db >= 0.0:
        return UNITY + (1.0 - UNITY) * (db / 6.0)
    return UNITY * (10.0 ** (db / 40.0))


def fader_to_db(value):
    """Fader value -> dB (inverse of db_to_fader; -inf becomes -70)."""
    value = float(value)
    if value <= 0.0:
        return -70.0
    if value >= 1.0:
        return 6.0
    if value >= UNITY:
        return 6.0 * (value - UNITY) / (1.0 - UNITY)
    return max(-70.0, 40.0 * math.log10(value / UNITY))


def _display_db(param, value):
    """Parse Live's own label for a fader value ("-12.0 dB", "-inf dB"); None if not a dB label."""
    label = param.str_for_value(value)
    if "dB" not in label:
        return None
    text = label.replace("dB", "").strip()
    if text.startswith("-inf"):
        return -float("inf")
    try:
        return float(text)
    except ValueError:
        return None


def fader_for_db(param, db):
    """Exact fader value for a dB target, found by bisecting Live's display curve; falls back to the approximation."""
    db = float(db)
    if _display_db(param, param.max) is None:
        return db_to_fader(db)
    lo, hi = param.min, param.max
    for _ in range(40):
        mid = (lo + hi) / 2
        shown = _display_db(param, mid)
        if shown is None:
            return db_to_fader(db)
        if shown < db:
            lo = mid
        else:
            hi = mid
    return hi


def _routing(obj, prop, name, what):
    options = list(getattr(obj, "available_%ss" % prop) or [])
    for o in options:
        if o.display_name == name:
            setattr(obj, prop, o)
            return o.display_name
    raise OpError("unknown %s %r (have %s)" % (what, name, ", ".join(o.display_name for o in options) or "none"))


def _device_index(track, device):
    devices = list(track.devices)
    if isinstance(device, int):
        return _index(devices, device, "device"), device
    d = _index(devices, device, "device")
    return d, devices.index(d)


def _set_param_value(p, value):
    p.value = max(p.min, min(p.max, float(value)))
    return p


# --- devices -----------------------------------------------------------------


@op("insert_device")
def _insert_device(ctx, track, name=None, index=-1, expect=None, device=None):
    name = name or device  # models often say device=
    if not name:
        raise OpError("insert_device needs name (a native device like \"Compressor\")")
    t = ctx.track(track, expect)
    devices = list(t.devices)
    idx = len(devices) if index is None or index < 0 else index
    d = t.insert_device(name, idx)
    return {"index": idx, "name": d.name, "class": d.class_name}


@op("delete_device")
def _delete_device(ctx, track, device, expect=None):
    t = ctx.track(track, expect)
    d, idx = _device_index(t, device)
    name = d.name  # the object is invalid once Live deletes the device
    t.delete_device(idx)
    return {"deleted": name}


@op("duplicate_device")
def _duplicate_device(ctx, track, device, expect=None):
    t = ctx.track(track, expect)
    d, idx = _device_index(t, device)
    t.duplicate_device(idx)
    return {"index": idx + 1, "name": list(t.devices)[idx + 1].name}


@op("set_params")
def _set_params(ctx, track, device, values, expect=None):
    d = ctx.device(track, device, expect)
    out = {}
    for key, value in values.items():
        p = _find_param(d, key)
        p.value = param_value(p, value)
        out[p.name] = {"value": p.value, "display": p.str_for_value(p.value)}
    return out


def _find_param(d, key):
    """Param by name or index; unknown names fail listing what the device has."""
    if isinstance(key, int) or (isinstance(key, str) and key.lstrip("-").isdigit()):
        return _index(d.parameters, int(key), "parameter")
    names = [p.name for p in d.parameters]
    for p in d.parameters:
        if p.name == key or p.original_name == key:
            return p
    raise OpError("device %r has no parameter %r (available: %s)" % (d.name, key, ", ".join(names)))


@op("device_on")
def _device_on(ctx, track, device, on, expect=None):
    d = ctx.device(track, device, expect)
    _set_param_value(d.parameters[0], 1.0 if on else 0.0)
    return {"name": d.name, "on": bool(d.parameters[0].value)}


# --- routing -----------------------------------------------------------------


@op("set_routing")
def _set_routing(ctx, track, output=None, input=None, output_channel=None, input_channel=None, expect=None):
    t = ctx.track(track, expect)
    out = {}
    if output is not None:
        out["output"] = _routing(t, "output_routing_type", output, "output routing")
    if input is not None:
        out["input"] = _routing(t, "input_routing_type", input, "input routing")
    if output_channel is not None:
        out["output_channel"] = _routing(t, "output_routing_channel", output_channel, "output channel")
    if input_channel is not None:
        out["input_channel"] = _routing(t, "input_routing_channel", input_channel, "input channel")
    return out


@op("sidechain")
def _sidechain(ctx, track, device, source, on=True, expect=None):
    d = ctx.device(track, device, expect)
    if not hasattr(d, "available_input_routing_types"):
        raise OpError("device %r has no input routing (not a sidechain-capable device)" % d.name)
    src = ctx.track(source)
    types_ = [t for t in (d.available_input_routing_types or []) if t.attached_object is src or t.display_name == src.name]
    if not types_:
        have = ", ".join(t.display_name for t in d.available_input_routing_types or []) or "none"
        hint = " A MIDI track is only a source once it has an instrument producing audio." if getattr(src, "has_midi_input", False) and not list(src.devices) else ""
        raise OpError("no input routing from track %r (available: %s).%s Newly inserted devices list routing options from the next batch on." % (source, have, hint))
    d.input_routing_type = types_[0]
    if hasattr(d, "available_input_routing_channels") and d.available_input_routing_channels:
        d.input_routing_channel = d.available_input_routing_channels[0]
    out = {"source": src.name, "routing": d.input_routing_type.display_name}
    if on is not None:
        for p in d.parameters:
            if "s/c" in p.name.lower() or "sidechain" in p.name.lower():
                _set_param_value(p, 1.0 if on else 0.0)
                out["sidechain_on"] = bool(p.value)
                break
        else:
            out["sidechain_on"] = None
    return out


# --- mixer ---------------------------------------------------------------------


def _find_send(t, name):
    """Send by name ("A", "Send A") or index ("0", 0, "A")."""
    sends = list(t.mixer_device.sends)
    if isinstance(name, int) or (isinstance(name, str) and name.lstrip("-").isdigit()):
        return _index(sends, int(name), "send")
    if isinstance(name, str) and len(name) == 1 and name.isalpha() and ord(name.upper()) - 65 < len(sends):
        return sends[ord(name.upper()) - 65]  # "A" = first send, as labelled in Live's mixer
    for p in sends:
        if p.name == name or p.name.replace("Send ", "") == name:
            return p
    raise OpError("track %r has no send %r (available: %s)" % (t.name, name, ", ".join(p.name.replace("Send ", "") for p in sends) or "none"))


def _apply_mixer(t, spec):
    out = {}
    if "volume_db" in spec and spec["volume_db"] is not None:
        _set_param_value(t.mixer_device.volume, fader_for_db(t.mixer_device.volume, spec["volume_db"]))
    elif spec.get("volume") is not None:
        _set_param_value(t.mixer_device.volume, spec["volume"])
    if spec.get("pan") is not None:
        _set_param_value(t.mixer_device.panning, spec["pan"])
    for name, v in (spec.get("sends") or {}).items():
        p = _find_send(t, name)
        _set_param_value(p, v)
    if spec.get("mute") is not None:
        t.mute = bool(spec["mute"])
    if spec.get("solo") is not None:
        t.solo = bool(spec["solo"])
    shown = _display_db(t.mixer_device.volume, t.mixer_device.volume.value)
    out["volume_db"] = round(shown if shown is not None else fader_to_db(t.mixer_device.volume.value), 2)
    out["pan"] = t.mixer_device.panning.value
    out["mute"] = t.mute
    out["solo"] = t.solo
    return out


@op("mixer")
def _mixer(ctx, tracks, crossfader=None):
    if not isinstance(tracks, dict) or not tracks:
        raise OpError("tracks must be a non-empty {track: settings} object")
    out = {}
    for ref, spec in tracks.items():
        expect = spec.pop("expect", None) if isinstance(spec, dict) else None
        out[str(ref)] = _apply_mixer(ctx.track(ref, expect), spec)
    if crossfader is not None:
        p = ctx.song.master_track.mixer_device.crossfader
        _set_param_value(p, crossfader)
        out["master"] = {"crossfader": p.value}
    return out
