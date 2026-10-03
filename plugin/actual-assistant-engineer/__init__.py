"""Actual Assistant Engineer: Hermes tools for operating Ableton Live."""

import json
import platform
from pathlib import Path

from . import live_client

TOOLSET = "actual_assistant_engineer"

OPS_DOC = """Ops (all positions in beats; 1 bar of 4/4 = 4 beats; pitches are MIDI numbers, 60 = C3 middle C):
- transport {play?, tempo?, position?, loop?, loop_start?, loop_length?, metronome?}
- stop_all_clips {}
- create_track {kind: "midi"|"audio"|"return", index?: -1, name?, color?: "#rrggbb"} -> {index}
- delete_track {track, expect}
- set_track {track, expect?, name?, color?, mute?, solo?, arm?, volume? (0-1, 0.85 = 0 dB), pan? (-1..1), sends?: [0-1...]}
- create_scene {index?: -1, name?} -> {index}
- set_scene {scene, expect?, name?, color?}
- fire_scene {scene} / fire_clip {track, slot} / stop_track {track}
- create_clip {track, slot, length (beats), name?, color?, replace?: false, expect?}
- add_notes {track, slot, notes: [{pitch, start, duration, velocity?}], expect?}
- get_notes {track, slot} / clear_notes {track, slot} / delete_clip {track, slot}
- set_clip {track, slot, name?, color?, looping?, loop_start?, loop_end?}
- clip_envelope {track, slot, target: "volume"|"pan"|"<device>:<param>", times: [beats]} -> sampled values
- device_params {track, device} -> parameter names, values, ranges
- set_param {track, device, param (index or name), value}
- drum_pads {track, device?} -> [{note, name}] for a Drum Rack
- browser_load {track, root, path: [names...]} loads a browser item (from live_browse) onto the track
- select_device {track, device}
`track` is an index or exact name ("master", "return:0" also work); `scene`/`slot` are indexes.
Any string "$N.key" is replaced by result N's key, e.g. create_track then create_clip {track: "$0.index", ...}.
Pass expect: "<current name>" on edits to existing user tracks so a stale index fails instead of editing the wrong track."""


def _result(obj):
    return json.dumps(obj, separators=(",", ":"), default=str)


def _call(ops, timeout=30.0):
    try:
        return live_client.batch(ops, timeout=timeout)
    except (live_client.LiveUnavailable, TimeoutError) as e:
        return {"ok": False, "error": str(e)}


def live_inspect(args, **_):
    track = args.get("track")
    res = _call([{"op": "info"}, {"op": "overview", "clips": args.get("clips", True)}])
    if not res.get("ok"):
        return _result(res)
    info, overview = res["results"][0], res["results"][1]
    out = {"live": info, "set": overview}
    if track is not None:
        devices = []
        target = next((t for t in overview["tracks"] if t["index"] == track or t["name"] == track), None)
        for i, _d in enumerate(target["devices"] if target else []):
            r = _call([{"op": "device_params", "track": track, "device": i}])
            devices.append(r["results"][0] if r.get("ok") else {"error": r.get("error") or r["results"][0].get("error")})
        out["track_devices"] = devices
    return _result(out)


def live_ops(args, **_):
    ops = args.get("ops") or []
    if not ops:
        return _result({"ok": False, "error": "ops is empty"})
    return _result(_call(ops, timeout=60.0))


def live_browse(args, **_):
    root, query, path = args["root"], args.get("query"), args.get("path") or []
    op = {"op": "browser_search", "root": root, "query": query, "limit": args.get("limit", 25)} if query else {"op": "browser_list", "root": root, "path": path}
    res = _call([op], timeout=30.0)
    return _result(res["results"][0] if res.get("ok") else res)


ROOTS = ["instruments", "audio_effects", "midi_effects", "drums", "sounds", "plugins", "samples", "user_library", "packs"]

SCHEMAS = {
    "live_inspect": {
        "name": "live_inspect",
        "description": "Read the open Ableton Live Set: Live version, tempo, tracks (devices, mixer, clips per slot), returns, and scenes. Pass `track` to also get every device's parameters on that track. Call this before editing and after edits to verify.",
        "parameters": {
            "type": "object",
            "properties": {
                "track": {"description": "Track index or name to detail devices/parameters for", "type": ["integer", "string"]},
                "clips": {"type": "boolean", "description": "Include clip summaries (default true)"},
            },
        },
    },
    "live_ops": {
        "name": "live_ops",
        "description": "Edit and play the Live Set. Runs a list of ops in order as ONE undo step and returns each op's result; stops at the first failing op. Batch related edits (e.g. a track, its clips, and their notes) into one call.\n" + OPS_DOC,
        "parameters": {
            "type": "object",
            "required": ["ops"],
            "properties": {"ops": {"type": "array", "items": {"type": "object", "required": ["op"], "properties": {"op": {"type": "string"}}}}},
        },
    },
    "live_browse": {
        "name": "live_browse",
        "description": "Find instruments, effects, drum kits, presets, samples, and VST/AU plug-ins in Live's browser. With `query`, searches loadable items by name under `root`; otherwise lists the children at `path`. Load results with live_ops browser_load using the same root and path.",
        "parameters": {
            "type": "object",
            "required": ["root"],
            "properties": {
                "root": {"type": "string", "enum": ROOTS},
                "query": {"type": "string"},
                "path": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
        },
    },
}

HANDLERS = {"live_inspect": live_inspect, "live_ops": live_ops, "live_browse": live_browse}


def register(ctx):
    if platform.system() != "Darwin":
        return
    for name, schema in SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=HANDLERS[name],
            check_fn=live_client.available,
            emoji="🎛️",
        )
    skills = Path(__file__).parent / "skills"
    for child in sorted(skills.iterdir()):
        if (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
