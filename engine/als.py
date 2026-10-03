"""Generate a complete Live Set (.als) from a song spec.

Starts from a Live-saved template so device XML (instrument racks, drum kits,
returns) is always Live's own, then rewrites tempo, scenes, clips, notes, and
clip envelopes. Usage: python als.py spec.json out.als
"""

import copy
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

def live_resources():
    """App-Resources of the newest installed Ableton Live (any edition)."""
    apps = sorted(p for base in (Path("/Applications"), Path.home() / "Applications") for p in base.glob("Ableton Live*.app"))
    if not apps:
        raise FileNotFoundError("no Ableton Live app found")
    return apps[-1] / "Contents" / "App-Resources"


TEMPLATES = {
    "quick-start-song": lambda: live_resources() / "Core Library/Templates/Quick Start Song.als",
}
POINTEE_TAGS = {"AutomationTarget", "ModulationTarget", "Pointee"}


def load(path):
    with gzip.open(path) as f:
        return ET.parse(f)


def save(tree, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)
    with gzip.open(path, "wb") as f:
        f.write(data)


class Ids:
    """Allocates pointee ids above the template's NextPointeeId."""

    def __init__(self, liveset):
        self.node = liveset.find("NextPointeeId")
        self.next = int(self.node.get("Value"))

    def take(self):
        self.next += 1
        return str(self.next - 1)

    def remap(self, elem):
        for e in elem.iter():
            if e.tag in POINTEE_TAGS and "Id" in e.attrib:
                e.set("Id", self.take())

    def commit(self):
        self.node.set("Value", str(self.next))


def track_name(track):
    return track.find("Name/EffectiveName").get("Value")


def set_value(elem, path, value):
    node = elem.find(path)
    if node is None:
        raise ValueError("missing %s" % path)
    node.set("Value", str(value))


def slots(track):
    return track.find("DeviceChain/MainSequencer/ClipSlotList")


def resize_scenes(liveset, count, ids):
    """Make exactly `count` scenes and clip slots on every track, emptying all slots."""
    scenes = liveset.find("Scenes")
    proto_scene = copy.deepcopy(scenes[0])
    for s in list(scenes):
        scenes.remove(s)
    for i in range(count):
        s = copy.deepcopy(proto_scene)
        s.set("Id", str(i))
        ids.remap(s)
        scenes.append(s)
    for track in liveset.find("Tracks"):
        lst = slots(track)
        if lst is None or len(lst) == 0:
            continue
        proto = copy.deepcopy(lst[0])
        proto.find("ClipSlot/Value").clear()
        for s in list(lst):
            lst.remove(s)
        for i in range(count):
            s = copy.deepcopy(proto)
            s.set("Id", str(i))
            lst.append(s)
        free = track.find("DeviceChain/FreezeSequencer/ClipSlotList")
        if free is not None:
            proto = copy.deepcopy(free[0])
            proto.find("ClipSlot/Value").clear()
            for s in list(free):
                free.remove(s)
            for i in range(count):
                s = copy.deepcopy(proto)
                s.set("Id", str(i))
                free.append(s)


def build_notes(clip, notes):
    keytracks = clip.find("Notes/KeyTracks")
    proto = copy.deepcopy(keytracks[0])
    proto.find("Notes").clear()
    for k in list(keytracks):
        keytracks.remove(k)
    by_pitch = {}
    for n in notes:
        by_pitch.setdefault(int(n["pitch"]), []).append(n)
    note_id = 1
    for i, pitch in enumerate(sorted(by_pitch)):
        kt = copy.deepcopy(proto)
        kt.set("Id", str(i))
        kt.find("MidiKey").set("Value", str(pitch))
        container = kt.find("Notes")
        for n in sorted(by_pitch[pitch], key=lambda n: n["start"]):
            ET.SubElement(
                container,
                "MidiNoteEvent",
                Time=repr(float(n["start"])),
                Duration=repr(float(n["duration"])),
                Velocity=repr(float(n.get("velocity", 100))),
                OffVelocity="64",
                NoteId=str(note_id),
            )
            note_id += 1
        keytracks.append(kt)
    clip.find("Notes/NoteIdGenerator/NextId").set("Value", str(note_id))


def build_envelopes(clip, envelopes, track, proto_env):
    container = clip.find("Envelopes/Envelopes")
    container.clear()
    for i, env in enumerate(envelopes):
        target = resolve_target(track, env["target"])
        e = copy.deepcopy(proto_env)
        e.set("Id", str(i))
        e.find("EnvelopeTarget/PointeeId").set("Value", target)
        events = e.find("Automation/Events")
        events.clear()
        # Live stores a default value at a far-negative time before the first point.
        points = [(t, envelope_value(env["target"], v)) for t, v in env["points"]]
        ET.SubElement(events, "FloatEvent", Id="0", Time="-63072000", Value=repr(float(points[0][1])))
        for j, (t, v) in enumerate(points, start=1):
            ET.SubElement(events, "FloatEvent", Id=str(j), Time=repr(float(t)), Value=repr(float(v)))
        container.append(e)


def set_tempo(liveset, bpm):
    tempo = liveset.find("MainTrack/DeviceChain/Mixer/Tempo")
    tempo.find("Manual").set("Value", str(bpm))
    # Templates also hold the tempo as an automation default event, which wins on load.
    target = tempo.find("AutomationTarget").get("Id")
    for env in liveset.iter("AutomationEnvelope"):
        if env.find("EnvelopeTarget/PointeeId").get("Value") == target:
            for ev in env.iter("FloatEvent"):
                ev.set("Value", str(bpm))


def envelope_value(target, value):
    # Mixer volume is stored as linear gain; specs give it in dB.
    return 10 ** (value / 20) if target == "volume" else value


def resolve_target(track, target):
    """Map a target name to the AutomationTarget id of a track parameter."""
    paths = {
        "volume": "DeviceChain/Mixer/Volume/AutomationTarget",
        "pan": "DeviceChain/Mixer/Pan/AutomationTarget",
    }
    if target in paths:
        return track.find(paths[target]).get("Id")
    if target.startswith("macro:"):
        n = int(target[6:])
        dev = track.find("DeviceChain/DeviceChain/Devices")[0]
        return dev.find("MacroControls.%d/AutomationTarget" % n).get("Id")
    raise ValueError("unknown automation target %r" % target)


def generate(spec):
    tree = load(TEMPLATES[spec.get("template", "quick-start-song")]())
    liveset = tree.getroot().find("LiveSet")
    ids = Ids(liveset)
    tracks = {track_name(t): t for t in liveset.find("Tracks")}

    proto_clip = copy.deepcopy(liveset.find(".//MidiClip"))
    proto_env = copy.deepcopy(liveset.find(".//ClipEnvelope"))

    set_tempo(liveset, spec["tempo"])
    sections = spec["sections"]
    resize_scenes(liveset, len(sections), ids)
    for scene, section in zip(liveset.find("Scenes"), sections):
        scene.find("Name").set("Value", section["name"])
        # Template scenes carry their own tempo; launching one would override the song tempo.
        scene.find("Tempo").set("Value", str(section.get("tempo", spec["tempo"])))
        scene.find("IsTempoEnabled").set("Value", "true" if "tempo" in section else "false")
        if "color" in section:
            scene.find("Color").set("Value", str(section["color"]))

    for part in spec["parts"]:
        track = tracks.get(part["track"])
        if track is None:
            raise ValueError("template has no track %r (have %s)" % (part["track"], ", ".join(tracks)))
        if "name" in part:
            set_value(track, "Name/UserName", part["name"])
        for si, section in enumerate(sections):
            body = part["clips"].get(section["name"])
            if body is None:
                continue
            beats = section["bars"] * 4
            clip = copy.deepcopy(proto_clip)
            ids.remap(clip)
            clip.set("Time", "0")
            set_value(clip, "Name", body.get("name", section["name"]))
            set_value(clip, "Color", body.get("color", part.get("color", 36)))
            set_value(clip, "CurrentStart", 0)
            set_value(clip, "CurrentEnd", beats)
            for k in ("LoopStart", "HiddenLoopStart", "StartRelative"):
                set_value(clip, "Loop/" + k, 0)
            for k in ("LoopEnd", "OutMarker", "HiddenLoopEnd"):
                set_value(clip, "Loop/" + k, beats)
            set_value(clip, "Loop/LoopOn", "true")
            build_notes(clip, body["notes"])
            build_envelopes(clip, body.get("envelopes", []), track, proto_env)
            slot = slots(track)[si]
            slot.find("ClipSlot/Value").append(clip)

    ids.commit()
    return tree


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    save(generate(spec), out)
    print(out)


if __name__ == "__main__":
    main()
