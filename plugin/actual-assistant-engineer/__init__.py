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
- add_notes {track, slot, notes?: [[pitch, start, duration, velocity], ...], patterns?: {"<pitch>": "x.X-..."}, step?: 0.25, velocity?: 100, accent?: 120, expect?}
    patterns write rhythmic parts compactly, one character per step from beat 0: x hit, X accented hit, - hold the previous note one more step, . rest. step is in beats (0.25 = 16ths, 0.5 = 8ths). notes and patterns can be combined.
- get_notes {track, slot} / clear_notes {track, slot} / delete_clip {track, slot}
- set_clip {track, slot, name?, color?, looping?, loop_start?, loop_end?}
- clip_envelope {track, slot, target: "volume"|"pan"|"<device>:<param>", times: [beats]} -> sampled values
- device_params {track, device} -> parameter names, values, ranges
- set_param {track, device, param (index or name), value}
- drum_pads {track, device?} -> [{note, name}] for a Drum Rack
- browser_load {track, root, path: [names...]} loads a browser item (from live_browse) onto the track
- select_device {track, device}
Devices, FX, mixing (device = index or exact name; parameter values may be raw numbers or display values like "500 Hz", "-6 dB", "40 %"):
- insert_device {track, name, index?} inserts a native Live device by name ("Compressor", "Glue Compressor", "EQ Eight", "Auto Filter", "Reverb", "Delay", "Echo", "Saturator", "Utility", "Limiter", "Chorus-Ensemble", "Phaser-Flanger", "Operator", "Wavetable", "Drift", "Drum Rack"...)
- delete_device {track, device} / duplicate_device {track, device} / device_on {track, device, on}
- set_params {track, device, values: {"<param name>": value, ...}} -> new values with display strings
- sidechain {track, device, source} keys a Compressor/Glue Compressor from another track (source must produce audio; insert the compressor in an earlier live_ops call than this one)
- set_routing {track, output?, input?, output_channel?, input_channel?} (display names, e.g. output "A-Reverb")
- mixer {tracks: {"<track>": {volume_db?, pan?, sends?: {"A": 0-1}, mute?, solo?}}, crossfader?} sets many tracks in one op; volume_db is exact
Automation (clip envelopes; on a session clip via slot, or an arrangement clip via arrangement = its index in `arrangement`):
- automate {track, slot|arrangement, target, points: [[beat, value], ...], curve?: "linear"|"step", clear?} target: "volume" | "pan" | "send:A" | "<device>:<param>" e.g. "Auto Filter:Frequency" with points [[0, "200 Hz"], [32, "8 kHz"]]
- read_automation {track, slot|arrangement, target, times} / clear_automation {track, slot|arrangement, target?} / list_automation {track, slot|arrangement}
Arrangement (timeline, positions in beats from the song start):
- arrangement {tracks?} -> clips per track (index, name, start, end) and locators
- arrangement_clip {track, start, length, name?, notes?, patterns?, step?} writes a MIDI clip directly on the timeline
- place_clip {track, slot, start, length?} copies a session clip onto the timeline, repeated to fill length
- arrange_scenes {sections: [{scene, bars, name?}], locators?: true} lays scenes out in order: every track's clip in that scene's row repeats for the section, and a named locator marks each section start
- clear_arrangement {track?, start?, end?} / locator {time, name} / show_view {view: "Arranger"|"Session"} / back_to_arranger {}
Anything else Live exposes (generic; paths start at song or app, select items by index or exact name: song.tracks["BASS"].devices[0].parameters["Threshold"]):
- get {path, props?: [...]} / set {path, prop, value} / call {path, method, args?: [...]} / describe {path} -> props and methods
`track` is an index or exact name ("master", "return:0" also work); `scene`/`slot` are indexes.
Refer to tracks you create by their name in later ops (names are exact-match). "$N.key" also works: N is the position of an earlier op in this same ops list (0-based) and key a field of its result, e.g. "$2.index".
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


def live_review(args, **_):
    require = {k: True for k in ("arrangement", "locators", "automation", "sidechain", "mix") if args.get(k)}
    for k in ("min_sections", "min_bars"):
        if args.get(k):
            require[k] = int(args[k])
    res = _call([{"op": "review", "require": require}], timeout=60.0)
    return _result(res["results"][0] if res.get("ok") else res)


def live_analyze(args, **_):
    res = _call([{"op": "analyze", "ignore_tracks": args.get("ignore_tracks", [])}], timeout=60.0)
    return _result(res["results"][0] if res.get("ok") else res)


def live_browse(args, **_):
    searches = args.get("searches")
    if searches:
        ops = [{"op": "browser_search", "root": q["root"], "query": q["query"], "limit": q.get("limit", 15)} for q in searches]
        res = _call(ops, timeout=60.0)
        if not res.get("ok") and not res.get("results"):
            return _result(res)
        out = []
        for q, r in zip(searches, res.get("results", [])):
            hits = ["/".join(i["path"]) for i in r.get("items", [])] if r.get("ok") else r.get("error")
            out.append({"root": q["root"], "query": q["query"], "paths": hits})
        return _result({"results": out})
    root, path = args.get("root"), args.get("path") or []
    if not root:
        return _result({"ok": False, "error": "pass searches, or root (+ path) to list a folder"})
    res = _call([{"op": "browser_list", "root": root, "path": path}], timeout=30.0)
    if not res.get("ok"):
        return _result(res)
    items = res["results"][0].get("items", [])
    limit = args.get("limit", 40)
    out = {"root": root, "path": path, "items": [i["name"] + ("/" if i["folder"] else "") for i in items[:limit]]}
    if len(items) > limit:
        out["more"] = len(items) - limit
    return _result(out)


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
        "description": "Find instruments, effects, drum kits, presets, samples, and VST/AU plug-ins in Live's browser. Pass several `searches` at once (each matches every word against the item's folder path and name) to gather all your candidate sounds in one call; or pass `root` (+ `path`) to list a folder (names ending in / are folders). Results are paths like \"Bass/Basic FM House Bass.adg\"; load one with live_ops browser_load {root, path: [\"Bass\", \"Basic FM House Bass.adg\"]}. Roots: sounds (instrument presets by category), drums (kits and hits), instruments, audio_effects, midi_effects, plugins, samples, user_library, packs.",
        "parameters": {
            "type": "object",
            "properties": {
                "searches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["root", "query"],
                        "properties": {"root": {"type": "string", "enum": ROOTS}, "query": {"type": "string"}, "limit": {"type": "integer"}},
                    },
                },
                "root": {"type": "string", "enum": ROOTS},
                "path": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
        },
    },
}

SCHEMAS["live_review"] = {
    "name": "live_review",
    "description": "Check the Set for completeness against what the producer asked for. Returns complete=true or a list of concrete gaps (empty clips, silent tracks, tracks missing from the arrangement, unnamed or missing section locators, no automation/sidechain/mix when required) plus arrangement length and sections. Call it after each phase and ALWAYS before reporting that you're done; if it lists gaps, fix them and call it again until complete.",
    "parameters": {
        "type": "object",
        "properties": {
            "arrangement": {"type": "boolean", "description": "the song must be laid out in the Arrangement"},
            "locators": {"type": "boolean", "description": "sections must be marked with named locators"},
            "automation": {"type": "boolean"},
            "sidechain": {"type": "boolean"},
            "mix": {"type": "boolean", "description": "levels must have been balanced"},
            "min_sections": {"type": "integer"},
            "min_bars": {"type": "integer"},
        },
    },
}

SCHEMAS["live_analyze"] = {
    "name": "live_analyze",
    "description": "QA analysis of the arrangement, section by section (from the locators): which tracks play, notes per bar, pitch range, automated parameters, and whether a part repeats the exact clips of an earlier section; plus the energy curve across sections, register clashes (two parts overlapping by an octave or more), and each track's level. Use it to find what to improve: flat energy, unchanged repeats, crowded registers, parts that never develop.",
    "parameters": {"type": "object", "properties": {"ignore_tracks": {"type": "array", "items": {"type": "string"}, "description": "track names to leave out (e.g. untouched template tracks)"}}},
}

HANDLERS = {"live_inspect": live_inspect, "live_ops": live_ops, "live_browse": live_browse, "live_review": live_review, "live_analyze": live_analyze}


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
