"""In-memory stand-in for Live's Python API, enough to unit-test the bridge's own logic.

Covers the objects the ops touch: Song, Track, ClipSlot, Clip (notes and
envelopes), Device/DeviceParameter, Scene, CuePoint, routing types, and the
`Live` module surface (Clip.MidiNoteSpecification, Application, MidiMap).
Extend it alongside new ops; keep behavior faithful to Live where tests rely on it.
"""

import sys
import types


class Listenable:
    """Live-style add_/remove_/_has_listener methods for any property."""

    def __getattr__(self, name):
        for prefix in ("add_", "remove_"):
            if name.startswith(prefix) and name.endswith("_listener"):
                prop = name[len(prefix) : -len("_listener")]
                store = self.__dict__.setdefault("_listeners", {}).setdefault(prop, [])
                return (lambda cb: store.append(cb)) if prefix == "add_" else (lambda cb: store.remove(cb))
        if name.endswith("_has_listener"):
            prop = name[: -len("_has_listener")]
            return lambda cb: cb in self.__dict__.get("_listeners", {}).get(prop, [])
        raise AttributeError(name)


class MidiNoteSpecification:
    def __init__(self, pitch, start_time, duration, velocity=100, mute=False):
        self.pitch, self.start_time, self.duration, self.velocity, self.mute = pitch, start_time, duration, velocity, mute


class Note(MidiNoteSpecification):
    pass


class DeviceParameter(Listenable):
    def __init__(self, name, value=0.0, min=0.0, max=1.0, quantized=False, items=()):
        self.name, self.original_name = name, name
        self.value, self.min, self.max = value, min, max
        self.is_quantized, self.value_items, self.is_enabled = quantized, list(items), True

    def str_for_value(self, v):
        return "%.2f" % v


class AutomationEnvelope:
    def __init__(self):
        self.steps = []  # (time, duration, value)

    def insert_step(self, time, duration, value):
        self.steps.append((time, duration, value))

    def value_at_time(self, t):
        before = [s for s in self.steps if s[0] <= t]
        return before[-1][2] if before else None


class Clip(Listenable):
    def __init__(self, length, is_midi=True, start_time=0.0):
        self.name, self.color = "", 0
        self.length, self.is_midi_clip = float(length), is_midi
        self.looping, self.loop_start, self.loop_end = True, 0.0, float(length)
        self.start_time, self.end_time = float(start_time), float(start_time) + float(length)
        self.is_playing, self.is_triggered = False, False
        self.notes = []
        self.envelopes = {}

    def add_new_notes(self, specs):
        self.notes.extend(Note(s.pitch, s.start_time, s.duration, s.velocity, s.mute) for s in specs)

    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self.notes if from_pitch <= n.pitch < from_pitch + pitch_span and from_time <= n.start_time < from_time + time_span]

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        gone = set(map(id, self.get_notes_extended(from_pitch, pitch_span, from_time, time_span)))
        self.notes = [n for n in self.notes if id(n) not in gone]

    def automation_envelope(self, param):
        return self.envelopes.get(id(param))

    def create_automation_envelope(self, param):
        return self.envelopes.setdefault(id(param), AutomationEnvelope())

    def clear_envelope(self, param):
        self.envelopes.pop(id(param), None)

    def clear_all_envelopes(self):
        self.envelopes.clear()

    def fire(self):
        self.is_triggered = True


class ClipSlot(Listenable):
    def __init__(self):
        self.clip = None

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        self.clip = Clip(length)

    def delete_clip(self):
        self.clip = None

    def fire(self):
        if self.clip:
            self.clip.fire()


class RoutingType:
    def __init__(self, display_name, attached_object=None):
        self.display_name, self.attached_object = display_name, attached_object


class Device(Listenable):
    def __init__(self, name, class_name=None, params=(), can_have_drum_pads=False):
        self.name, self.class_name = name, class_name or name
        self.parameters = [DeviceParameter("Device On", 1.0, quantized=True)] + list(params)
        self.can_have_drum_pads, self.drum_pads = can_have_drum_pads, []
        self.is_active = True


class MixerDevice:
    def __init__(self, sends=2):
        self.volume = DeviceParameter("Track Volume", 0.85)
        self.panning = DeviceParameter("Track Panning", 0.0, -1.0, 1.0)
        self.sends = [DeviceParameter("Send %s" % chr(65 + i)) for i in range(sends)]
        self.crossfader = DeviceParameter("Crossfader", 0.0, -1.0, 1.0)


class Track(Listenable):
    def __init__(self, name, scenes=8, midi=True):
        self.name, self.color = name, 0
        self.has_midi_input, self.has_audio_input = midi, not midi
        self.mute = self.solo = self.arm = False
        self.can_be_armed = True
        self.mixer_device = MixerDevice()
        self.devices = []
        self.clip_slots = [ClipSlot() for _ in range(scenes)]
        self.arrangement_clips = []
        self.playing_slot_index = self.fired_slot_index = -1
        self.output_routing_type = RoutingType("Master")
        self.available_output_routing_types = [RoutingType("Master"), RoutingType("Sends Only")]
        self.input_routing_type = RoutingType("All Ins")
        self.available_input_routing_types = [RoutingType("All Ins"), RoutingType("No Input")]

    def stop_all_clips(self):
        self.playing_slot_index = -1

    def insert_device(self, name, index=-1):
        d = Device(name)
        self.devices.insert(len(self.devices) if index < 0 else index, d)
        return d

    def delete_device(self, index):
        del self.devices[index]

    def create_midi_clip(self, start, length):
        c = Clip(length, start_time=start)
        self.arrangement_clips.append(c)
        self.arrangement_clips.sort(key=lambda x: x.start_time)
        return c

    def duplicate_clip_to_arrangement(self, clip, time):
        c = Clip(clip.length, clip.is_midi_clip, start_time=time)
        c.name, c.notes = clip.name, list(clip.notes)
        self.arrangement_clips.append(c)
        self.arrangement_clips.sort(key=lambda x: x.start_time)
        return c

    def delete_clip(self, clip):
        self.arrangement_clips.remove(clip)


class Scene(Listenable):
    def __init__(self, name=""):
        self.name, self.color = name, 0
        self.fired = 0

    def fire(self):
        self.fired += 1


class CuePoint:
    def __init__(self, time, name=""):
        self.time, self.name = time, name


class SongView:
    def __init__(self):
        self.selected_track = None
        self.selected_device = None

    def select_device(self, d):
        self.selected_device = d


class Song(Listenable):
    def __init__(self, tracks=2, scenes=8):
        self.tempo, self.signature_numerator, self.signature_denominator = 120.0, 4, 4
        self.is_playing, self.current_song_time = False, 0.0
        self.loop, self.loop_start, self.loop_length, self.metronome = False, 0.0, 16.0, False
        self.tracks = [Track("%d-MIDI" % (i + 1), scenes) for i in range(tracks)]
        self.return_tracks = [Track("A-Reverb", 0), Track("B-Delay", 0)]
        self.master_track = Track("Main", 0)
        self.scenes = [Scene() for _ in range(scenes)]
        self.cue_points = []
        self.view = SongView()
        self.undo_depth = 0
        self.undo_steps = 0
        self.data = {}

    # undo
    def begin_undo_step(self):
        self.undo_depth += 1

    def end_undo_step(self):
        self.undo_depth -= 1
        self.undo_steps += 1

    # tracks / scenes
    def create_midi_track(self, index=-1):
        t = Track("MIDI", len(self.scenes))
        self.tracks.insert(len(self.tracks) if index < 0 else index, t)

    def create_audio_track(self, index=-1):
        t = Track("Audio", len(self.scenes), midi=False)
        self.tracks.insert(len(self.tracks) if index < 0 else index, t)

    def create_return_track(self):
        self.return_tracks.append(Track("Return", 0))

    def delete_track(self, index):
        del self.tracks[index]

    def create_scene(self, index=-1):
        self.scenes.insert(len(self.scenes) if index < 0 else index, Scene())
        for t in self.tracks:
            t.clip_slots.insert(len(t.clip_slots) if index < 0 else index, ClipSlot())

    # transport
    def start_playing(self):
        self.is_playing = True

    def stop_playing(self):
        self.is_playing = False

    def stop_all_clips(self, quantized=True):
        for t in self.tracks:
            t.stop_all_clips()

    def set_or_delete_cue(self):
        hit = [c for c in self.cue_points if abs(c.time - self.current_song_time) < 1e-9]
        if hit:
            self.cue_points.remove(hit[0])
        else:
            self.cue_points.append(CuePoint(self.current_song_time))
            self.cue_points.sort(key=lambda c: c.time)

    def get_data(self, key, default=None):
        return self.data.get(key, default)

    def set_data(self, key, value):
        self.data[key] = value


class Application:
    def __init__(self):
        self.browser = types.SimpleNamespace()
        self.view = types.SimpleNamespace(shown=None, show_view=lambda v: setattr(self.view, "shown", v))

    def get_major_version(self):
        return 12

    def get_minor_version(self):
        return 4

    def get_bugfix_version(self):
        return 5


_app = Application()


def install():
    """Register a fake `Live` module in sys.modules (call before importing bridge ops)."""
    live = types.ModuleType("Live")
    live.Clip = types.SimpleNamespace(MidiNoteSpecification=MidiNoteSpecification)
    live.Application = types.SimpleNamespace(get_application=lambda: _app)
    live.MidiMap = types.SimpleNamespace(forward_midi_note=lambda *a: None, forward_midi_cc=lambda *a: None)
    sys.modules["Live"] = live
    framework = types.ModuleType("_Framework")
    surface = types.ModuleType("_Framework.ControlSurface")
    surface.ControlSurface = type("ControlSurface", (), {"__init__": lambda self, c: None})
    framework.ControlSurface = surface
    sys.modules["_Framework"], sys.modules["_Framework.ControlSurface"] = framework, surface
    return live


class FakeSurface:
    """What ops.Context needs from the control surface."""

    def __init__(self, song):
        self._song = song
        self.logs = []
        self.events = []
        self.bindings = None

    def song(self):
        return self._song

    def log_message(self, msg):
        self.logs.append(msg)

    def emit(self, event):
        self.events.append(event)

    def tick_stats(self):
        return {"samples": 0}

    def set_perf_bindings(self, bindings):
        self.bindings = bindings
