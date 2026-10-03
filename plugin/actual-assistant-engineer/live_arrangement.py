"""Arrangement (track) automation: create, read, update, delete through the Set file.

Live's scripting API has no access to arrangement automation, so each change saves the Set,
edits its envelopes in the .als (als_automation.py), reopens it, and verifies through
the LOM that the parameter now reports automation. Values may be raw numbers or display
strings; the file's units for each parameter are inferred from its current value.
"""

from . import als_automation as A
from . import live_client, live_sets


class AutomationError(Exception):
    pass


def _bridge(op):
    res = live_client.batch([op], timeout=30.0, undo_step=False)
    r = (res.get("results") or [{}])[0]
    if not res.get("ok"):
        raise AutomationError(r.get("error") or res.get("error") or "bridge error")
    return r


def _xml_target(target, info):
    if ":" in target and not target.startswith("send:"):
        return "%d:%s" % (info["device_index"], target.split(":", 1)[1])
    return target


def _to_file(value_raw, display, info, mode):
    if mode == "gain":
        parsed = A_parse(display)
        return 0.0 if parsed is None or parsed == float("-inf") else 10 ** (parsed / 20)
    if mode == "display":
        parsed = A_parse(display)
        return value_raw if parsed is None else parsed
    return value_raw


def A_parse(text):
    import re
    m = re.match(r"\s*(-?inf|-?\d+(?:\.\d+)?)\s*(k)?", str(text), re.I)
    if not m:
        return None
    if "inf" in m.group(1).lower():
        return -float("inf")
    return float(m.group(1)) * (1000 if m.group(2) else 1)


def _set_path(path=None):
    p = path or live_sets._current_path()
    if not p:
        raise AutomationError("the open Set's file is unknown: open or create it with live_set first (or pass path)")
    return p


def write(track, target, points, path=None):
    info = _bridge({"op": "param_info", "track": track, "target": target, "values": [v for _, v in points]})
    set_path = _set_path(path)
    live_sets.save()
    tree = A.load(set_path)
    tr = A.track_named(tree, track)
    param = A.param_element(tr, _xml_target(target, info), info.get("lom_names"))
    mode = A.unit_mode(A.manual_value(param), info["value"], info["display_number"], info["is_volume"])
    file_points = [(beat, _to_file(raw, disp, info, mode)) for (beat, _), raw, disp in zip(points, info["raw_values"], info["displays"])]
    A.write(tr, param, file_points)
    A.save(tree, set_path)
    live_sets.open_set(set_path, "cancel")
    after = _bridge({"op": "param_info", "track": track, "target": target})
    if not after.get("automation_state"):
        raise AutomationError("wrote the envelope but Live does not report %s as automated after reopening" % target)
    return {"target": info["name"], "points": len(points), "units": mode, "displays": info["displays"], "automation_state": after["automation_state"]}


def read(track, target, path=None):
    info = _bridge({"op": "param_info", "track": track, "target": target})
    set_path = _set_path(path)
    live_sets.save()
    tree = A.load(set_path)
    tr = A.track_named(tree, track)
    param = A.param_element(tr, _xml_target(target, info), info.get("lom_names"))
    pts = A.read(tr, param)
    mode = A.unit_mode(A.manual_value(param), info["value"], info["display_number"], info["is_volume"])
    return {"target": info["name"], "automated": pts is not None, "points": pts or [], "units": mode}


def delete(track, target, path=None):
    info = _bridge({"op": "param_info", "track": track, "target": target})
    set_path = _set_path(path)
    live_sets.save()
    tree = A.load(set_path)
    tr = A.track_named(tree, track)
    param = A.param_element(tr, _xml_target(target, info), info.get("lom_names"))
    removed = A.delete(tr, param)
    if removed:
        A.save(tree, set_path)
        live_sets.open_set(set_path, "cancel")
    return {"target": info["name"], "deleted": removed}


def list_automated(track, path=None):
    set_path = _set_path(path)
    live_sets.save()
    return {"track": track, "automated": A.list_targets(A.track_named(A.load(set_path), track))}
