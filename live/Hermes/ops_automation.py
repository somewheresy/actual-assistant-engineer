"""Clip automation: write, read, and clear envelope breakpoints on session and arrangement clips."""

from .ops import OpError, _index, op, param_value

DEFAULT_RESOLUTION = 0.0625


def _ref(x):
    """Numeric strings select by index, anything else by exact name."""
    if isinstance(x, str) and x.lstrip("-").isdigit():
        return int(x)
    return x


def resolve_target(track, target):
    """Map "volume" | "pan" | "send:X" | "device:param" onto a DeviceParameter."""
    if not isinstance(target, str):
        raise OpError("target must be a string")
    mixer = track.mixer_device
    if target == "volume":
        return mixer.volume
    if target == "pan":
        return mixer.panning
    if target.startswith("send:"):
        ref = target[5:]
        if len(ref) == 1 and ref.isalpha():
            idx = ord(ref[0].upper()) - 65
        else:
            idx = _ref(ref)
        if not isinstance(idx, int) or not 0 <= idx < len(mixer.sends):
            raise OpError("send %r out of range (have %d)" % (ref, len(mixer.sends)))
        return mixer.sends[idx]
    if ":" in target:
        dev, param = target.split(":", 1)
        d = _index(track.devices, _ref(dev), "device")
        return _index(d.parameters, _ref(param), "parameter")
    raise OpError("target must be volume, pan, send:<letter>, or <device>:<parameter>")


def ramp_steps(points, curve="linear", resolution=DEFAULT_RESOLUTION):
    """Expand [[beat, value], ...] into (time, duration, value) steps, sorted by beat.

    "step" holds each value until the next point; "linear" interpolates in
    resolution-sized steps. The last point is held for one `resolution` window.
    """
    if curve not in ("linear", "step"):
        raise OpError("curve must be linear or step")
    resolution = float(resolution)
    if resolution <= 0:
        raise OpError("resolution must be positive")
    pts = []
    for p in points:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise OpError("points must be [beat, value] pairs")
        pts.append((float(p[0]), float(p[1])))
    pts.sort(key=lambda p: p[0])
    steps = []
    for i, (t, v) in enumerate(pts):
        if i + 1 == len(pts):
            steps.append((t, resolution, v))
            continue
        t2, v2 = pts[i + 1]
        if curve == "step" or v == v2 or t2 - t <= resolution:
            steps.append((t, t2 - t, v))
            continue
        tt = t
        while tt < t2 - 1e-9:
            steps.append((tt, min(resolution, t2 - tt), v + (v2 - v) * (tt - t) / (t2 - t)))
            tt += resolution
    return steps


def _select(ctx, track, slot, arrangement, expect=None):
    """Exactly one of slot (session) or arrangement (index) picks the clip."""
    if (slot is None) == (arrangement is None):
        raise OpError("pass exactly one of slot (session clip slot) or arrangement (clip index)")
    t = ctx.track(track, expect)
    if slot is not None:
        cs = _index(t.clip_slots, slot, "clip slot")
        if not cs.has_clip:
            raise OpError("no clip at track %r slot %r" % (track, slot))
        return t, cs.clip
    return t, _index(t.arrangement_clips, arrangement, "arrangement clip")


def _candidate_params(track):
    """Every (target label, parameter) this track can automate."""
    mixer = track.mixer_device
    out = [("volume", mixer.volume), ("pan", mixer.panning)]
    out += [("send:%s" % chr(65 + i), s) for i, s in enumerate(mixer.sends)]
    for d in track.devices:
        out += [("%s:%s" % (d.name, p.name), p) for p in d.parameters]
    return out


@op("automate")
def _automate(ctx, track, slot=None, arrangement=None, target=None, points=(), curve="linear", resolution=DEFAULT_RESOLUTION, clear=False, expect=None):
    """Write an automation envelope from [beat, value] points."""
    if target is None:
        raise OpError("target is required")
    t, clip = _select(ctx, track, slot, arrangement, expect)
    param = resolve_target(t, target)
    # Points may use display values ("500 Hz", "-6 dB"); convert to raw before ramping.
    points = [[b, param_value(param, v)] for b, v in points]
    steps = ramp_steps(points, curve, resolution)
    # Hold the last value to the clip's end; past its last step Live falls back to the base value.
    if steps:
        last_t, _, last_v = steps[-1]
        steps[-1] = (last_t, max(float(resolution), clip.length - last_t), last_v)
    if clear:
        clip.clear_envelope(param)
    if clip.automation_envelope(param) is None:
        try:
            clip.create_automation_envelope(param)
        except RuntimeError as e:
            if arrangement is not None:
                raise OpError("Live can only create automation on session clips: automate the session clip (slot), then place it with place_clip/arrange_scenes; the copies keep its automation (%s)" % e)
            raise
    env = clip.automation_envelope(param)
    lo, hi = param.min, param.max
    for time, duration, value in steps:
        env.insert_step(time, duration, max(lo, min(hi, value)))
    return {"target": param.name, "points": len(points), "steps": len(steps)}


@op("read_automation")
def _read_automation(ctx, track, slot=None, arrangement=None, target=None, times=(), expect=None):
    """Sample an envelope at clip-relative beats."""
    if target is None:
        raise OpError("target is required")
    t, clip = _select(ctx, track, slot, arrangement, expect)
    param = resolve_target(t, target)
    env = clip.automation_envelope(param)
    if env is None:
        return {"present": False, "values": [], "display": []}
    # Live's value_at_time(0) returns the parameter's base value, not the envelope's first step.
    values = [env.value_at_time(max(float(x), 1e-6)) for x in times]
    return {"present": True, "values": values, "display": [param.str_for_value(v) for v in values]}


@op("clear_automation")
def _clear_automation(ctx, track, slot=None, arrangement=None, target=None, expect=None):
    """Clear one target's envelope, or all of the clip's envelopes when target is omitted."""
    t, clip = _select(ctx, track, slot, arrangement, expect)
    if target is None:
        clip.clear_all_envelopes()
        return {"cleared": "all"}
    clip.clear_envelope(resolve_target(t, target))
    return {"cleared": target}


@op("list_automation")
def _list_automation(ctx, track, slot=None, arrangement=None, expect=None):
    """Targets that currently have envelopes on the clip."""
    t, clip = _select(ctx, track, slot, arrangement, expect)
    return {"targets": [label for label, p in _candidate_params(t) if clip.automation_envelope(p) is not None]}
