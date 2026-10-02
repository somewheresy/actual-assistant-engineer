"""Batched Live Object Model operations.

A batch is a list of ops executed in order inside one undo step. Any string
argument of the form "$N.key" is replaced by results[N]["key"], so a batch can
create a track and fill it without a round trip. Ops that target an object
accept "expect" (a name) and fail instead of touching a different object.
"""

import re
import time
import traceback

import Live

OPS = {}
REF = re.compile(r"^\$(\d+)\.(\w+)$")


def op(name):
    def wrap(fn):
        OPS[name] = fn
        return fn

    return wrap


class OpError(Exception):
    pass


class Context:
    def __init__(self, surface):
        self.surface = surface
        self._listeners = []

    @property
    def song(self):
        return self.surface.song()

    @property
    def app(self):
        return Live.Application.get_application()

    # --- batches ---------------------------------------------------------

    def run_batch(self, batch, undo_step):
        results = []
        song = self.song
        if undo_step and batch:
            song.begin_undo_step()
        try:
            for i, raw in enumerate(batch):
                args = _resolve(raw, results)
                name = args.pop("op", None)
                fn = OPS.get(name)
                if fn is None:
                    results.append({"ok": False, "error": "unknown op %r" % name})
                    return results, False
                try:
                    out = fn(self, **args) or {}
                    out["ok"] = True
                    results.append(out)
                except OpError as e:
                    results.append({"ok": False, "error": str(e), "op": name, "index": i})
                    return results, False
                except Exception as e:
                    self.surface.log_message("Hermes op %s failed: %s" % (name, traceback.format_exc()))
                    results.append({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "op": name, "index": i})
                    return results, False
            return results, True
        finally:
            if undo_step and batch:
                song.end_undo_step()

    # --- targets ---------------------------------------------------------

    def track(self, ref, expect=None):
        song = self.song
        if ref == "master":
            t = song.master_track
        elif isinstance(ref, str) and ref.startswith("return:"):
            t = _index(song.return_tracks, int(ref[7:]), "return track")
        else:
            t = _index(song.tracks, ref, "track")
        if expect is not None and t.name != expect:
            raise OpError("track %r is %r, expected %r" % (ref, t.name, expect))
        return t

    def scene(self, ref, expect=None):
        s = _index(self.song.scenes, ref, "scene")
        if expect is not None and s.name != expect:
            raise OpError("scene %r is %r, expected %r" % (ref, s.name, expect))
        return s

    def slot(self, track, slot, expect=None):
        return _index(self.track(track, expect).clip_slots, slot, "clip slot")

    def clip(self, track, slot, expect=None):
        cs = self.slot(track, slot, expect)
        if not cs.has_clip:
            raise OpError("no clip at track %r slot %r" % (track, slot))
        return cs.clip

    def device(self, track, device, expect=None):
        return _index(self.track(track, expect).devices, device, "device")

    # --- performance (MIDI path) -----------------------------------------

    def perform(self, binding, value):
        if not binding:
            return
        try:
            PERFORM[binding["action"]](self, binding, value)
        except Exception:
            self.surface.log_message("Hermes perform failed: %s" % traceback.format_exc())

    # --- listeners -------------------------------------------------------

    def attach_listeners(self):
        song = self.song
        self._add(song, "tracks", lambda: (self.emit("tracks_changed"), self._rebind_tracks()))
        self._add(song, "scenes", lambda: self.emit("scenes_changed"))
        self._add(song, "is_playing", lambda: self.emit("transport", is_playing=self.song.is_playing))
        self._add(song, "tempo", lambda: self.emit("tempo", tempo=self.song.tempo))
        self._track_listeners = []
        self._rebind_tracks()

    def _rebind_tracks(self):
        for obj, name, cb in getattr(self, "_track_listeners", []):
            _remove(obj, name, cb)
        self._track_listeners = []
        for i, t in enumerate(self.song.tracks):
            for prop in ("playing_slot_index", "fired_slot_index", "mute", "solo"):
                cb = _track_cb(self, t, i, prop)
                if _try_add(t, prop, cb):
                    self._track_listeners.append((t, prop, cb))
            vol = t.mixer_device.volume
            cb = _vol_cb(self, vol, i)
            if _try_add(vol, "value", cb):
                self._track_listeners.append((vol, "value", cb))

    def _add(self, obj, prop, cb):
        if _try_add(obj, prop, cb):
            self._listeners.append((obj, prop, cb))

    def detach_listeners(self):
        for obj, name, cb in self._listeners + getattr(self, "_track_listeners", []):
            _remove(obj, name, cb)
        self._listeners = []
        self._track_listeners = []

    def emit(self, kind, **data):
        self.surface.emit(dict(data, kind=kind))


# --- helpers ---------------------------------------------------------------


def _resolve(value, results):
    if isinstance(value, dict):
        return {k: _resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, results) for v in value]
    if isinstance(value, str):
        m = REF.match(value)
        if m:
            n, key = int(m.group(1)), m.group(2)
            if n >= len(results) or key not in results[n]:
                raise OpError("unresolvable reference %s" % value)
            return results[n][key]
    return value


def _index(seq, ref, what):
    seq = list(seq)
    if isinstance(ref, int):
        if not -len(seq) <= ref < len(seq):
            raise OpError("%s %d out of range (have %d)" % (what, ref, len(seq)))
        return seq[ref]
    matches = [x for x in seq if getattr(x, "name", None) == ref]
    if len(matches) != 1:
        raise OpError("%s named %r matched %d" % (what, ref, len(matches)))
    return matches[0]


def _try_add(obj, prop, cb):
    adder = getattr(obj, "add_%s_listener" % prop, None)
    if adder is None:
        return False
    adder(cb)
    return True


def _remove(obj, prop, cb):
    try:
        if getattr(obj, "%s_has_listener" % prop)(cb):
            getattr(obj, "remove_%s_listener" % prop)(cb)
    except Exception:
        pass


def _track_cb(ctx, track, i, prop):
    return lambda: ctx.emit("track", track=i, prop=prop, value=getattr(track, prop))


def _vol_cb(ctx, param, i):
    return lambda: ctx.emit("track", track=i, prop="volume", value=param.value)


def _param(p):
    out = {
        "name": p.name,
        "value": p.value,
        "min": p.min,
        "max": p.max,
        "quantized": p.is_quantized,
    }
    if p.is_quantized:
        out["items"] = list(p.value_items)
    return out


def _color(c):
    return "#%06x" % c


def _note(n):
    return {
        "pitch": n.pitch,
        "start": n.start_time,
        "duration": n.duration,
        "velocity": n.velocity,
        "mute": n.mute,
    }


def _clip_summary(clip):
    return {
        "name": clip.name,
        "color": _color(clip.color),
        "length": clip.length,
        "midi": clip.is_midi_clip,
        "looping": clip.looping,
        "playing": clip.is_playing,
    }


def _track_summary(t, i, slots):
    d = {
        "index": i,
        "name": t.name,
        "color": _color(t.color),
        "midi": t.has_midi_input,
        "mute": t.mute,
        "solo": t.solo,
        "volume": t.mixer_device.volume.value,
        "pan": t.mixer_device.panning.value,
        "devices": [{"name": d.name, "class": d.class_name} for d in t.devices],
        "playing_slot": t.playing_slot_index,
    }
    if slots:
        d["clips"] = {str(j): _clip_summary(s.clip) for j, s in enumerate(t.clip_slots) if s.has_clip}
    return d


# --- set / identity ----------------------------------------------------------


@op("info")
def _info(ctx):
    app, song = ctx.app, ctx.song
    return {
        "protocol": 1,
        "live_version": "%d.%d.%d" % (app.get_major_version(), app.get_minor_version(), app.get_bugfix_version()),
        "set_id": song.get_data("aae.set_id", None),
        "tempo": song.tempo,
        "signature": [song.signature_numerator, song.signature_denominator],
        "is_playing": song.is_playing,
        "tracks": len(song.tracks),
        "scenes": len(song.scenes),
        "tick": ctx.surface.tick_stats(),
    }


@op("set_identity")
def _set_identity(ctx, set_id):
    ctx.song.set_data("aae.set_id", set_id)
    return {"set_id": ctx.song.get_data("aae.set_id", None)}


@op("overview")
def _overview(ctx, clips=True):
    song = ctx.song
    return {
        "tempo": song.tempo,
        "signature": [song.signature_numerator, song.signature_denominator],
        "tracks": [_track_summary(t, i, clips) for i, t in enumerate(song.tracks)],
        "returns": [{"index": i, "name": t.name} for i, t in enumerate(song.return_tracks)],
        "scenes": [{"index": i, "name": s.name, "color": _color(s.color)} for i, s in enumerate(song.scenes)],
    }


@op("ping")
def _ping(ctx):
    return {"t": time.time()}


@op("tick_stats")
def _tick_stats(ctx):
    return ctx.surface.tick_stats()


# --- transport ---------------------------------------------------------------


@op("transport")
def _transport(ctx, play=None, tempo=None, position=None, loop=None, loop_start=None, loop_length=None, metronome=None):
    song = ctx.song
    if tempo is not None:
        song.tempo = float(tempo)
    if position is not None:
        song.current_song_time = float(position)
    if loop is not None:
        song.loop = bool(loop)
    if loop_start is not None:
        song.loop_start = float(loop_start)
    if loop_length is not None:
        song.loop_length = float(loop_length)
    if metronome is not None:
        song.metronome = bool(metronome)
    if play is True:
        song.start_playing()
    elif play is False:
        song.stop_playing()
    return {"tempo": song.tempo, "is_playing": song.is_playing, "position": song.current_song_time}


@op("stop_all_clips")
def _stop_all(ctx, quantized=True):
    ctx.song.stop_all_clips(quantized)
    return {}


# --- tracks ------------------------------------------------------------------


@op("create_track")
def _create_track(ctx, kind="midi", index=-1, name=None, color=None):
    song = ctx.song
    create = {"midi": song.create_midi_track, "audio": song.create_audio_track, "return": song.create_return_track}[kind]
    if kind == "return":
        create()
        t, idx = song.return_tracks[-1], len(song.return_tracks) - 1
    else:
        create(index)
        idx = index if index >= 0 else len(song.tracks) - 1
        t = song.tracks[idx]
    if name is not None:
        t.name = name
    if color is not None:
        t.color = int(color.lstrip("#"), 16) if isinstance(color, str) else int(color)
    return {"index": idx, "name": t.name}


@op("delete_track")
def _delete_track(ctx, track, expect=None):
    t = ctx.track(track, expect)
    ctx.song.delete_track(list(ctx.song.tracks).index(t))
    return {}


@op("set_track")
def _set_track(ctx, track, expect=None, name=None, color=None, mute=None, solo=None, arm=None, volume=None, pan=None, sends=None):
    t = ctx.track(track, expect)
    if name is not None:
        t.name = name
    if color is not None:
        t.color = int(color.lstrip("#"), 16) if isinstance(color, str) else int(color)
    if mute is not None:
        t.mute = bool(mute)
    if solo is not None:
        t.solo = bool(solo)
    if arm is not None and t.can_be_armed:
        t.arm = bool(arm)
    if volume is not None:
        t.mixer_device.volume.value = float(volume)
    if pan is not None:
        t.mixer_device.panning.value = float(pan)
    for i, v in enumerate(sends or []):
        if v is not None:
            t.mixer_device.sends[i].value = float(v)
    return _track_summary(t, list(ctx.song.tracks).index(t) if t in list(ctx.song.tracks) else -1, False)


@op("stop_track")
def _stop_track(ctx, track, expect=None):
    ctx.track(track, expect).stop_all_clips()
    return {}


# --- scenes ------------------------------------------------------------------


@op("create_scene")
def _create_scene(ctx, index=-1, name=None):
    song = ctx.song
    song.create_scene(index)
    idx = index if index >= 0 else len(song.scenes) - 1
    if name is not None:
        song.scenes[idx].name = name
    return {"index": idx}


@op("set_scene")
def _set_scene(ctx, scene, expect=None, name=None, color=None):
    s = ctx.scene(scene, expect)
    if name is not None:
        s.name = name
    if color is not None:
        s.color = int(color.lstrip("#"), 16) if isinstance(color, str) else int(color)
    return {"name": s.name}


@op("fire_scene")
def _fire_scene(ctx, scene, expect=None):
    ctx.scene(scene, expect).fire()
    return {}


# --- clips & notes ---------------------------------------------------------


@op("create_clip")
def _create_clip(ctx, track, slot, length, expect=None, name=None, color=None, replace=False):
    cs = ctx.slot(track, slot, expect)
    if cs.has_clip:
        if not replace:
            raise OpError("slot %r on track %r already has a clip" % (slot, track))
        cs.delete_clip()
    cs.create_clip(float(length))
    if name is not None:
        cs.clip.name = name
    if color is not None:
        cs.clip.color = int(color.lstrip("#"), 16) if isinstance(color, str) else int(color)
    return _clip_summary(cs.clip)


@op("delete_clip")
def _delete_clip(ctx, track, slot, expect=None):
    cs = ctx.slot(track, slot, expect)
    if cs.has_clip:
        cs.delete_clip()
    return {}


@op("add_notes")
def _add_notes(ctx, track, slot, notes, expect=None):
    clip = ctx.clip(track, slot, expect)
    specs = tuple(
        Live.Clip.MidiNoteSpecification(
            pitch=int(n["pitch"]),
            start_time=float(n["start"]),
            duration=float(n["duration"]),
            velocity=float(n.get("velocity", 100)),
            mute=bool(n.get("mute", False)),
        )
        for n in notes
    )
    clip.add_new_notes(specs)
    return {"added": len(specs), "total": len(clip.get_notes_extended(0, 128, 0.0, clip.length))}


@op("get_notes")
def _get_notes(ctx, track, slot, expect=None):
    clip = ctx.clip(track, slot, expect)
    return {"length": clip.length, "notes": [_note(n) for n in clip.get_notes_extended(0, 128, 0.0, clip.length)]}


@op("clear_notes")
def _clear_notes(ctx, track, slot, expect=None):
    clip = ctx.clip(track, slot, expect)
    clip.remove_notes_extended(0, 128, 0.0, clip.length)
    return {}


@op("fire_clip")
def _fire_clip(ctx, track, slot, expect=None):
    ctx.slot(track, slot, expect).fire()
    return {}


@op("set_clip")
def _set_clip(ctx, track, slot, expect=None, name=None, color=None, looping=None, loop_start=None, loop_end=None):
    clip = ctx.clip(track, slot, expect)
    if name is not None:
        clip.name = name
    if color is not None:
        clip.color = int(color.lstrip("#"), 16) if isinstance(color, str) else int(color)
    if looping is not None:
        clip.looping = bool(looping)
    if loop_end is not None:
        clip.loop_end = float(loop_end)
    if loop_start is not None:
        clip.loop_start = float(loop_start)
    return _clip_summary(clip)


# --- devices & parameters ----------------------------------------------------


@op("device_params")
def _device_params(ctx, track, device, expect=None):
    d = ctx.device(track, device, expect)
    return {"name": d.name, "class": d.class_name, "params": [_param(p) for p in d.parameters]}


@op("set_param")
def _set_param(ctx, track, device, param, value, expect=None):
    d = ctx.device(track, device, expect)
    p = _index(d.parameters, param, "parameter")
    if not p.is_enabled:
        raise OpError("parameter %r is disabled" % p.name)
    p.value = max(p.min, min(p.max, float(value)))
    return _param(p)


# --- browser -----------------------------------------------------------------

BROWSER_ROOTS = ("instruments", "audio_effects", "midi_effects", "drums", "sounds", "plugins", "samples", "user_library", "packs")


def _browser_root(ctx, name):
    if name not in BROWSER_ROOTS:
        raise OpError("unknown browser root %r" % name)
    return getattr(ctx.app.browser, name)


@op("browser_list")
def _browser_list(ctx, root, path=()):
    item = _browser_root(ctx, root)
    for part in path:
        item = _index(item.children, part, "browser item")
    return {
        "items": [
            {"name": c.name, "folder": c.is_folder, "loadable": c.is_loadable, "device": c.is_device}
            for c in item.children
        ]
    }


@op("browser_search")
def _browser_search(ctx, root, query, limit=25, max_depth=6):
    q = query.lower()
    found = []
    stack = [(_browser_root(ctx, root), [])]
    visited = 0
    while stack and len(found) < limit and visited < 20000:
        item, path = stack.pop()
        for c in item.children:
            visited += 1
            p = path + [c.name]
            if c.is_loadable and q in c.name.lower():
                found.append({"path": p, "name": c.name, "device": c.is_device})
                if len(found) >= limit:
                    break
            if c.is_folder and len(p) < max_depth:
                stack.append((c, p))
    return {"items": found, "visited": visited}


@op("browser_load")
def _browser_load(ctx, track, root, path, expect=None):
    t = ctx.track(track, expect)
    item = _browser_root(ctx, root)
    for part in path:
        item = _index(item.children, part, "browser item")
    if not item.is_loadable:
        raise OpError("%r is not loadable" % "/".join(path))
    ctx.song.view.selected_track = t
    before = len(t.devices)
    ctx.app.browser.load_item(item)
    return {"loaded": item.name, "devices_before": before}


# --- performance bindings ------------------------------------------------


@op("perf_bind")
def _perf_bind(ctx, bindings):
    ctx.surface.set_perf_bindings(bindings)
    return {"notes": len(bindings.get("note", {})), "ccs": len(bindings.get("cc", {}))}


def _scaled(b, value, p):
    lo, hi = b.get("min", p.min), b.get("max", p.max)
    return lo + (hi - lo) * value / 127.0


def _p_volume(ctx, b, value):
    p = ctx.track(b["track"], b.get("expect")).mixer_device.volume
    p.value = _scaled(b, value, p)


def _p_param(ctx, b, value):
    p = ctx.device(b["track"], b["device"], b.get("expect")).parameters[b["param"]]
    p.value = _scaled(b, value, p)


def _p_crossfader(ctx, b, value):
    p = ctx.song.master_track.mixer_device.crossfader
    p.value = _scaled(b, value, p)


def _p_toggle(prop):
    def fn(ctx, b, value):
        t = ctx.track(b["track"], b.get("expect"))
        setattr(t, prop, not getattr(t, prop))

    return fn


PERFORM = {
    "fire_scene": lambda ctx, b, v: ctx.scene(b["scene"], b.get("expect")).fire(),
    "fire_clip": lambda ctx, b, v: ctx.slot(b["track"], b["slot"], b.get("expect")).fire(),
    "stop_track": lambda ctx, b, v: ctx.track(b["track"], b.get("expect")).stop_all_clips(),
    "stop_all": lambda ctx, b, v: ctx.song.stop_all_clips(True),
    "transport_stop": lambda ctx, b, v: ctx.song.stop_playing(),
    "mute": _p_toggle("mute"),
    "solo": _p_toggle("solo"),
    "volume": _p_volume,
    "param": _p_param,
    "crossfader": _p_crossfader,
}


# --- introspection (spike) -------------------------------------------------

PATH = re.compile(r"(\w+)(?:\[(-?\d+)\])?")


@op("inspect")
def _inspect(ctx, path):
    """Resolve a dotted path like song.tracks[0].devices[1] and list its API."""
    roots = {"song": ctx.song, "app": ctx.app}
    parts = path.split(".")
    m = PATH.fullmatch(parts[0])
    obj = roots.get(m.group(1)) if m else None
    if obj is None:
        raise OpError("path must start with song or app")
    if m.group(2) is not None:
        obj = list(obj)[int(m.group(2))]
    for part in parts[1:]:
        m = PATH.fullmatch(part)
        if not m or m.group(1).startswith("_"):
            raise OpError("bad path segment %r" % part)
        obj = getattr(obj, m.group(1))
        if m.group(2) is not None:
            obj = list(obj)[int(m.group(2))]
    attrs = [a for a in dir(obj) if not a.startswith("_")]
    return {"type": type(obj).__name__, "repr": repr(obj)[:200], "attrs": attrs}
