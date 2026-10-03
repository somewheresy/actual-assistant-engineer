"""Token-lean tool results. Model output time and context dominate a session, so results carry
what the model acts on and nothing else: rounded floats, no nulls or empty fields, no per-op
"ok": true echoes, one-line track summaries, and parameters as name -> display text."""

def compact(obj, depth=0):
    if isinstance(obj, float):
        return round(obj, 2) if abs(obj) >= 100 else round(obj, 4)
    if isinstance(obj, list):
        return [compact(v, depth + 1) for v in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if v is None or v == [] or v == {} or (k == "ok" and v is True and depth > 0):
                continue
            out[k] = compact(v, depth + 1)
        return out
    return obj


def track_line(t):
    """One line per track: index, name, kind, level, flags, device chain, clips by slot."""
    parts = ["%d %s" % (t["index"], t["name"]), "midi" if t.get("midi") else "audio", t.get("level") or ""]
    if t.get("pan"):
        parts.append("pan %+.2f" % t["pan"])
    parts += [f for f in ("mute", "solo") if t.get(f)]
    if t.get("devices"):
        parts.append("devices: " + " > ".join(d["name"] for d in t["devices"]))
    clips = t.get("clips") or {}
    if clips:
        parts.append("clips: " + " ".join("%s:%s(%gb)" % (slot, c.get("name") or "-", c["length"] / 4) for slot, c in clips.items()))
    if t.get("playing_slot", -1) >= 0:
        parts.append("playing slot %d" % t["playing_slot"])
    return " | ".join(p for p in parts if p)


def overview(info, ov):
    return {
        "live": {k: info.get(k) for k in ("live_version", "tempo", "signature", "is_playing")},
        "tracks": [track_line(t) for t in ov["tracks"]],
        "returns": [r["name"] for r in ov.get("returns", [])],
        "scenes": ["%d %s" % (s["index"], s["name"]) for s in ov.get("scenes", []) if s.get("name")] or "%d unnamed" % len(ov.get("scenes", [])),
    }


def device_params(dev, query=None, limit=40, ranges=False):
    """A device's parameters as {name: display}, optionally filtered and with ranges."""
    params = dev.get("params", [])
    if query:
        terms = query.lower().split()
        params = [p for p in params if all(t in p["name"].lower() for t in terms)]
    shown = params[:limit]
    if ranges:
        listed = {p["name"]: {k: p.get(k) for k in ("display", "value", "min", "max", "items")} for p in shown}
    else:
        listed = {p["name"]: p.get("display", p.get("value")) for p in shown}
    out = {"device": dev.get("name"), "class": dev.get("class"), "params": listed}
    if len(params) > limit:
        out["more"] = "%d more; pass params_query to filter or a higher params_limit" % (len(params) - limit)
    return out
