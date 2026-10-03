"""Arrangement (track) automation inside a Live Set file.

Live's scripting API can't read or write arrangement automation, so this edits the .als:
envelopes live on each track as AutomationEnvelope elements that point (PointeeId) at a
parameter's AutomationTarget id. Parameters are located generically:
  target = "volume" | "pan" | "send:<letter>" | "<device index or name>:<parameter name>"
where a device parameter's LOM name is matched to its XML element by normalized name.
Values are in the file's own units for that parameter; callers convert (see unit_mode).
"""

import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_TIME = "-63072000"  # Live stores each envelope's start value at this time


def load(path):
    with gzip.open(path) as f:
        return ET.parse(f)


def save(tree, path):
    data = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    with gzip.open(path, "wb") as f:
        f.write(data)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def tracks(tree):
    """XML track elements in LOM song.tracks order (returns and master excluded)."""
    ls = tree.getroot().find("LiveSet")
    return [t for t in ls.find("Tracks") if t.tag != "ReturnTrack"]


def track_named(tree, ref):
    ts = tracks(tree)
    if isinstance(ref, int):
        return ts[ref]
    hits = [t for t in ts if t.find("Name/EffectiveName").get("Value") == ref]
    if len(hits) != 1:
        raise ValueError("track %r matched %d tracks" % (ref, len(hits)))
    return hits[0]


def _params(element):
    """(tag, element) for every automatable parameter directly on a device or mixer element."""
    return [(c.tag, c) for c in element if c.find("AutomationTarget") is not None]


def param_element(track, target, lom_names=None):
    """Find the XML parameter element for a target. lom_names (the device's LOM parameter
    names, in order) disambiguate device parameters whose XML tags differ from display names."""
    mixer = track.find("DeviceChain/Mixer")
    if target == "volume":
        return mixer.find("Volume")
    if target == "pan":
        return mixer.find("Pan")
    if target.startswith("send:"):
        sends = mixer.findall("Sends/TrackSendHolder/Send")
        return sends[ord(target[5:].upper()) - 65]
    device_ref, name = target.split(":", 1)
    devices = list(track.find("DeviceChain/DeviceChain/Devices"))
    if device_ref.isdigit():
        device = devices[int(device_ref)]
    else:
        hits = [d for d in devices if (d.find("UserName") is not None and d.find("UserName").get("Value") == device_ref) or _norm(d.tag) == _norm(device_ref)]
        if not hits:
            raise ValueError("no device %r on this track (have %s); use its index" % (device_ref, ", ".join(d.tag for d in devices)))
        device = hits[0]
    return match_param(device, name, lom_names)


def match_param(device, name, lom_names=None):
    m = re.fullmatch(r"macro\s*(\d+)", name.strip(), re.I)
    if m:  # rack macros
        return device.find("MacroControls.%d" % (int(m.group(1)) - 1))
    if _norm(name) in ("deviceon", "on"):
        return device.find("On")
    candidates = _params(device)
    want = _norm(name)
    for rule in (lambda tag: tag == want, lambda tag: tag.endswith(want), lambda tag: want in tag):
        hits = [el for tag, el in candidates if rule(_norm(tag))]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1 and lom_names:
            # Same-named parameters: align by position among LOM parameters with this name.
            idx = [i for i, n in enumerate(lom_names) if _norm(n) == want]
            if idx:
                return hits[min(len(hits) - 1, sum(1 for n in lom_names[: idx[0]] if want in _norm(n)))]
        if len(hits) > 1:
            return hits[0]
    raise ValueError("no parameter matching %r on %s (has %s)" % (name, device.tag, ", ".join(t for t, _ in candidates[:40])))


def _envelopes(track):
    return track.find("AutomationEnvelopes/Envelopes")


def _target_id(param):
    return param.find("AutomationTarget").get("Id")


def find_envelope(track, param):
    pid = _target_id(param)
    for env in _envelopes(track):
        if env.find("EnvelopeTarget/PointeeId").get("Value") == pid:
            return env
    return None


def read(track, param):
    """[(beat, value), ...] excluding the start-value event; None when not automated."""
    env = find_envelope(track, param)
    if env is None:
        return None
    return [(float(e.get("Time")), float(e.get("Value"))) for e in env.iter("FloatEvent") if e.get("Time") != DEFAULT_TIME]


def write(track, param, points, start_value=None):
    """Replace the parameter's arrangement envelope with breakpoints [(beat, value), ...] in file units."""
    delete(track, param)
    envs = _envelopes(track)
    env = ET.SubElement(envs, "AutomationEnvelope", Id=str(_next_id(envs)))
    ET.SubElement(ET.SubElement(env, "EnvelopeTarget"), "PointeeId", Value=_target_id(param))
    auto = ET.SubElement(env, "Automation")
    events = ET.SubElement(auto, "Events")
    pts = sorted((float(t), float(v)) for t, v in points)
    first = pts[0][1] if pts else float(param.find("Manual").get("Value"))
    ET.SubElement(events, "FloatEvent", Id="0", Time=DEFAULT_TIME, Value=repr(first if start_value is None else start_value))
    for i, (t, v) in enumerate(pts, start=1):
        ET.SubElement(events, "FloatEvent", Id=str(i), Time=repr(t), Value=repr(v))
    tv = ET.SubElement(auto, "AutomationTransformViewState")
    ET.SubElement(tv, "IsTransformPending", Value="false")
    ET.SubElement(tv, "TimeAndValueTransforms")
    return env


def delete(track, param):
    env = find_envelope(track, param)
    if env is not None:
        _envelopes(track).remove(env)
        return True
    return False


def list_targets(track):
    """Every automated parameter on the track as (xml path description, AutomationTarget id)."""
    ids = {env.find("EnvelopeTarget/PointeeId").get("Value") for env in _envelopes(track)}
    out = []
    for el in track.iter():
        at = el.find("AutomationTarget")
        if at is not None and at.get("Id") in ids:
            out.append(el.tag)
    return out


def _next_id(envs):
    return max([int(e.get("Id")) for e in envs] or [-1]) + 1


def manual_value(param):
    return float(param.find("Manual").get("Value"))


def unit_mode(manual, lom_value, display_number, is_volume=False):
    """How the file stores this parameter, inferred from its current value in both worlds:
    "raw" (same as the LOM value), "display" (the number Live displays, base units), or "gain"
    (linear gain for dB faders)."""
    if is_volume:
        return "gain"
    if abs(manual - lom_value) <= 1e-4 * max(1.0, abs(lom_value)):
        return "raw"
    if display_number is not None and abs(manual - display_number) <= 0.02 * max(1.0, abs(display_number)):
        return "display"
    return "raw"


def path_of(set_path):
    return Path(set_path).expanduser()
