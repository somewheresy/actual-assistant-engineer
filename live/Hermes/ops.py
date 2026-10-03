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
                name = raw.get("op") if isinstance(raw, dict) else None
                fn = OPS.get(name)
                if fn is None:
                    results.append({"ok": False, "error": "unknown op %r" % name, "index": i})
                    return results, False
                try:
                    args = _resolve(raw, results)
                    args.pop("op", None)
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
                if n >= len(results):
                    raise OpError("unresolvable reference %s: $N is the Nth op in this batch (0-based) and only %d ops ran before this one; refer to tracks by name instead" % (value, len(results)))
                have = sorted(k for k in results[n] if k != "ok")
                raise OpError("unresolvable reference %s: op %d returned %s" % (value, n, have))
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


UNITS = {"khz": 1000.0, "hz": 1.0, "db": 1.0, "%": 1.0, "ms": 1.0, "s": 1000.0}
RATIO = re.compile(r"^\s*(inf|\d+(?:\.\d+)?)\s*:\s*1\s*$", re.I)
DISPLAY = re.compile(r"^\s*(-?inf|-?\d+(?:\.\d+)?)\s*(khz|hz|db|%|ms|s)?\s*$", re.I)


def parse_display(text):
    """'2.5 kHz' -> (2500.0, 'hz'); '-6 dB' -> (-6.0, 'db'); None when not a number with a known unit."""
    ratio = RATIO.match(str(text))
    if ratio:
        x = ratio.group(1).lower()
        return (float("inf") if x == "inf" else float(x)), "ratio"
    m = DISPLAY.match(str(text))
    if not m:
        return None
    unit = (m.group(2) or "").lower()
    number = -float("inf") if m.group(1).lower() == "-inf" else float("inf") if m.group(1).lower() == "inf" else float(m.group(1))
    family = {"khz": "hz", "s": "ms"}.get(unit, unit)
    return number * UNITS.get(unit, 1.0), family


def _quantized_value(p, value, target):
    """Stepped parameters: match a choice by its text, else the choice nearest in value and unit."""
    items = [str(i) for i in p.value_items]
    if value in items:
        return float(items.index(value))
    if target is not None:
        scored = []
        for i, item in enumerate(items):
            parsed = parse_display(item) or parse_display(item + " " + target[1]) if target[1] else parse_display(item)
            if parsed and (not target[1] or parsed[1] in (target[1], "")):
                scored.append((abs(parsed[0] - target[0]), i))
        if scored:
            return float(min(scored)[1])
    raise OpError("%r takes one of: %s" % (p.name, ", ".join(items)))


def param_value(p, value):
    """Raw parameter value for a number (raw) or a display string like "500 Hz" / "-6 dB" / "40 %".

    Display strings are matched against Live's own labels (str_for_value) by bisection,
    which assumes the label grows with the value, true for Live's continuous parameters.
    """
    if not isinstance(value, str):
        return max(p.min, min(p.max, float(value)))
    target = parse_display(value)
    if p.is_quantized and list(p.value_items):
        return _quantized_value(p, value, target)
    if target is None:
        raise OpError("cannot read %r as a value for %r (give a number in %s..%s or a display value like %r)" % (value, p.name, p.min, p.max, p.str_for_value(p.max)))
    want, family = target
    shown = lambda v: parse_display(p.str_for_value(v))
    top = shown(p.max)
    if top is None or (family and top[1] != family):
        raise OpError("%r displays values like %r, not %r" % (p.name, p.str_for_value(p.max), value))
    lo, hi = p.min, p.max
    for _ in range(40):
        mid = (lo + hi) / 2
        cur = shown(mid)
        if cur is None:
            break
        if cur[0] < want:
            lo = mid
        else:
            hi = mid
    return hi


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
        # type: Live's DeviceType (1 instrument, 2 audio effect, 4 MIDI effect).
        "devices": [{"name": d.name, "class": d.class_name, "type": int(d.type)} for d in t.devices],
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
    if isinstance(sends, dict):  # {"A": 0.3} as well as [0.3, ...]
        sends = {(ord(k.upper()) - 65 if isinstance(k, str) and len(k) == 1 and k.isalpha() else int(k)): v for k, v in sends.items()}.items()
    else:
        sends = enumerate(sends or [])
    for i, v in sends:
        if v is not None:
            p = _index(t.mixer_device.sends, i, "send")
            p.value = param_value(p, v)
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


def _note_spec(n):
    """Accept {pitch, start, duration, velocity?} or compact [pitch, start, duration, velocity?]."""
    if isinstance(n, (list, tuple)):
        n = dict(zip(("pitch", "start", "duration", "velocity"), n))
    return Live.Clip.MidiNoteSpecification(
        pitch=int(n["pitch"]),
        start_time=float(n["start"]),
        duration=float(n["duration"]),
        velocity=float(n.get("velocity", 100)),
        mute=bool(n.get("mute", False)),
    )


def _pattern_notes(patterns, step, velocity, accent):
    """Expand {pitch: "x.X-..."} step strings: x hit, X accent, - hold previous, . rest."""
    out = []
    for pitch, pattern in patterns.items():
        note = None
        for i, c in enumerate(pattern):
            if c == "-" and note is not None:
                note["duration"] += step
                continue
            note = None
            if c in "xX":
                note = {"pitch": int(pitch), "start": i * step, "duration": step, "velocity": accent if c == "X" else velocity}
                out.append(note)
    return out


@op("add_notes")
def _add_notes(ctx, track, slot, notes=(), patterns=None, step=0.25, velocity=100, accent=120, expect=None):
    clip = ctx.clip(track, slot, expect)
    all_notes = list(notes) + (_pattern_notes(patterns, float(step), velocity, accent) if patterns else [])
    specs = tuple(_note_spec(n) for n in all_notes)
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


@op("clip_envelope")
def _clip_envelope(ctx, track, slot, target, times, expect=None):
    """Sample a clip envelope for a mixer target ("volume"/"pan") or device param."""
    clip = ctx.clip(track, slot, expect)
    t = ctx.track(track, expect)
    if target in ("volume", "pan"):
        param = t.mixer_device.volume if target == "volume" else t.mixer_device.panning
    else:
        dev, idx = target.split(":")
        param = t.devices[int(dev)].parameters[int(idx)]
    env = clip.automation_envelope(param)
    if env is None:
        return {"present": False}
    values = [env.value_at_time(float(x)) for x in times]
    return {"present": True, "values": values, "display": [param.str_for_value(v) for v in values]}


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


# --- arrangement ---------------------------------------------------------------


def _arr_clip_summary(i, c):
    return {"index": i, "name": c.name, "start": c.start_time, "end": c.end_time, "midi": c.is_midi_clip}


def _mark(ctx, time, name):
    """Queue a named locator. Live applies a song-position change on the next tick and registers
    a new cue point after that, so each locator takes three ticks: move, add, name."""
    queue = ctx.surface.__dict__.setdefault("_locator_queue", [])
    queue.append((float(time), name))
    if len(queue) == 1:
        _locator_step(ctx, "move")


def _locator_step(ctx, phase):
    queue = ctx.surface.__dict__.get("_locator_queue") or []
    if not queue:
        return
    song = ctx.song
    time, name = queue[0]
    at = [c for c in song.cue_points if abs(c.time - time) < 1e-6]
    if phase == "move":
        song.current_song_time = time
        nxt = "add"
    elif phase == "add":
        if (name is None) == bool(at):  # add when missing, or delete when name is None and one exists
            song.set_or_delete_cue()
        nxt = "name"
    else:
        for c in at if name is not None else []:
            c.name = name
        queue.pop(0)
        nxt = "move"
    if queue:
        ctx.surface.schedule_message(1, lambda: _locator_step(ctx, nxt))


@op("arrangement")
def _arrangement(ctx, tracks=None):
    """Arrangement timeline: clips per track (beats) and locators."""
    song = ctx.song
    idx = range(len(song.tracks)) if tracks is None else [list(song.tracks).index(ctx.track(t)) for t in tracks]
    return {
        "length": song.song_length if hasattr(song, "song_length") else None,
        "locators": [{"name": c.name, "time": c.time} for c in song.cue_points],
        "tracks": [
            {"index": i, "name": song.tracks[i].name, "clips": [_arr_clip_summary(j, c) for j, c in enumerate(song.tracks[i].arrangement_clips)]}
            for i in idx
        ],
    }


@op("arrangement_clip")
def _arrangement_clip(ctx, track, start, length, name=None, color=None, notes=(), patterns=None, step=0.25, velocity=100, accent=120, expect=None):
    """Write a new MIDI clip directly onto the arrangement timeline."""
    t = ctx.track(track, expect)
    clip = t.create_midi_clip(float(start), float(length))
    if name is not None:
        clip.name = name
    if color is not None:
        clip.color = int(color.lstrip("#"), 16) if isinstance(color, str) else int(color)
    all_notes = list(notes) + (_pattern_notes(patterns, float(step), velocity, accent) if patterns else [])
    if all_notes:
        clip.add_new_notes(tuple(_note_spec(n) for n in all_notes))
    return {"start": clip.start_time, "end": clip.end_time, "notes": len(all_notes)}


def _place(track, clip, start, bars_beats):
    """Copy a session clip onto the timeline, repeating it to fill the span exactly."""
    placed, t = 0, float(start)
    end = float(start) + float(bars_beats)
    step = clip.length
    while t < end - 1e-6:
        remaining = end - t
        if remaining >= step - 1e-6 or not clip.is_midi_clip:
            track.duplicate_clip_to_arrangement(clip, t)
        else:
            # A partial copy: Live can't shorten an arrangement clip, so write a new one of the exact length.
            part = track.create_midi_clip(t, remaining)
            part.name, part.color = clip.name, clip.color
            notes = [n for n in clip.get_notes_extended(0, 128, 0.0, remaining)]
            part.add_new_notes(tuple(
                Live.Clip.MidiNoteSpecification(pitch=n.pitch, start_time=n.start_time, duration=min(n.duration, remaining - n.start_time), velocity=n.velocity, mute=n.mute)
                for n in notes
            ))
        placed += 1
        t += step
    return placed


@op("place_clip")
def _place_clip(ctx, track, slot, start, length=None, expect=None):
    """Copy a session clip into the arrangement at `start`, repeated to fill `length` beats."""
    t = ctx.track(track, expect)
    clip = ctx.clip(track, slot, expect)
    return {"placed": _place(t, clip, start, length or clip.length)}


@op("arrange_scenes")
def _arrange_scenes(ctx, sections, locators=True):
    """Lay session scenes out on the timeline: sections = [{scene, bars}] in play order.
    Every track's clip in that scene row is repeated to fill the section; empty slots stay silent."""
    song = ctx.song
    t, out = 0.0, []
    for sec in sections:
        scene = ctx.scene(sec["scene"])
        row = list(song.scenes).index(scene)
        beats = float(sec["bars"]) * song.signature_numerator
        placed = 0
        for track in song.tracks:
            slots = list(track.clip_slots)
            if row < len(slots) and slots[row].has_clip:
                placed += _place(track, slots[row].clip, t, beats)
        if locators:
            _mark(ctx, t, sec.get("name") or scene.name)
        out.append({"scene": scene.name, "start": t, "beats": beats, "clips": placed})
        t += beats
    song.current_song_time = 0.0
    return {"sections": out, "end": t}


@op("clear_arrangement")
def _clear_arrangement(ctx, track=None, start=0.0, end=None, expect=None):
    """Delete arrangement clips that start within [start, end) on one track or all tracks."""
    song = ctx.song
    tracks = [ctx.track(track, expect)] if track is not None else list(song.tracks)
    n = 0
    for tr in tracks:
        for c in list(tr.arrangement_clips):
            if c.start_time >= float(start) and (end is None or c.start_time < float(end)):
                tr.delete_clip(c)
                n += 1
    return {"deleted": n}


@op("locator")
def _locator(ctx, time, name):
    _mark(ctx, time, name)
    return {"time": float(time), "name": name}


@op("delete_locator")
def _delete_locator(ctx, time):
    _mark(ctx, time, None)
    return {"time": float(time)}


@op("show_view")
def _show_view(ctx, view):
    """Bring Live's Arranger or Session view to the front."""
    if view not in ("Arranger", "Session"):
        raise OpError("view must be Arranger or Session")
    ctx.app.view.show_view(view)
    return {}


@op("back_to_arranger")
def _back_to_arranger(ctx):
    """Make the arrangement play again after session clips took over."""
    ctx.song.back_to_arranger = False
    return {}


# --- devices & parameters ----------------------------------------------------


@op("device_params")
def _device_params(ctx, track, device, expect=None):
    d = ctx.device(track, device, expect)
    return {"name": d.name, "class": d.class_name, "params": [_param(p) for p in d.parameters]}


@op("drum_pads")
def _drum_pads(ctx, track, device=0, expect=None):
    """List a Drum Rack's filled pads (MIDI note -> pad name)."""
    d = ctx.device(track, device, expect)
    rack = _find_drum_rack(d)
    if rack is None:
        raise OpError("%r has no Drum Rack inside it" % d.name)
    out = {"pads": [{"note": p.note, "name": p.name} for p in rack.drum_pads if p.chains]}
    if rack is not d:
        out["rack"] = rack.name
    return out


def _find_drum_rack(device, depth=0):
    """The device itself if it's a Drum Rack, else the first one nested in its chains."""
    if device.can_have_drum_pads:
        return device
    if depth > 4 or not getattr(device, "can_have_chains", False):
        return None
    for chain in device.chains:
        for inner in chain.devices:
            found = _find_drum_rack(inner, depth + 1)
            if found is not None:
                return found
    return None


@op("set_param")
def _set_param(ctx, track, device, param, value, expect=None):
    d = ctx.device(track, device, expect)
    p = _index(d.parameters, param, "parameter")
    if not p.is_enabled:
        raise OpError("parameter %r is disabled" % p.name)
    p.value = param_value(p, value)
    return dict(_param(p), display=p.str_for_value(p.value))


@op("select_device")
def _select_device(ctx, track, device, expect=None):
    """Select a track and device so Live's UI (and plug-in window toggles) target it."""
    t = ctx.track(track, expect)
    d = _index(t.devices, device, "device")
    ctx.song.view.selected_track = t
    ctx.song.view.select_device(d)
    return {"name": d.name}


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
    terms = query.lower().split()
    found = []
    stack = [(_browser_root(ctx, root), [])]
    visited = 0
    while stack and len(found) < limit and visited < 20000:
        item, path = stack.pop()
        for c in item.children:
            visited += 1
            p = path + [c.name]
            # Every word must appear somewhere in the item's path ("house kit" finds House/.../X Kit.adg).
            haystack = " ".join(p).lower()
            if c.is_loadable and all(t in haystack for t in terms):
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
