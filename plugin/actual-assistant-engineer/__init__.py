"""Actual Assistant Engineer: Hermes tools for operating Ableton Live."""

import json
import platform
from pathlib import Path

from . import cli, compact, live_arrangement, live_client, live_sets, models
from . import live_vst as vst_ops

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
- get_notes {track, slot} -> notes as [pitch, start, duration, velocity] rows / clear_notes {track, slot} / delete_clip {track, slot}
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
    return json.dumps(compact.compact(obj), separators=(",", ":"), default=str)


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
    info, ov = res["results"][0], res["results"][1]
    out = {"live": info, "set": ov} if args.get("detail") == "full" else compact.overview(info, ov)
    if track is not None:
        target = next((t for t in ov["tracks"] if t["index"] == track or t["name"] == track), None)
        count = len(target["devices"]) if target else 0
        # All devices in one round trip.
        r = _call([{"op": "device_params", "track": track, "device": i} for i in range(count)]) if count else {"results": []}
        out["track_devices"] = [
            compact.device_params(d, args.get("params_query"), args.get("params_limit", 40), args.get("ranges", False)) if d.get("ok", True) and "params" in d else {"error": d.get("error")}
            for d in r.get("results", [])
        ]
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


def live_set(args, **_):
    action = args.get("action")
    try:
        if action == "info":
            out = live_sets.info()
        elif action == "new":
            out = live_sets.new(args.get("name", "Untitled"), args.get("directory"), args.get("on_unsaved", "cancel"))
        elif action == "open":
            out = live_sets.open_set(args["path"], args.get("on_unsaved", "cancel"))
        elif action == "save":
            out = live_sets.save()
        elif action == "save_as":
            out = live_sets.save_as(args["name"], args.get("directory"))
        else:
            return _result({"ok": False, "error": "action must be info, new, open, save, or save_as"})
    except (live_sets.SetError, KeyError) as e:
        return _result({"ok": False, "error": str(e)})
    return _result(dict(out, ok=True))


def live_arrangement_automation(args, **_):
    action, track, target = args.get("action"), args.get("track"), args.get("target")
    try:
        if action == "list":
            out = live_arrangement.list_automated(track, args.get("path"))
        elif action == "read":
            out = live_arrangement.read(track, target, args.get("path"))
        elif action == "write":
            out = live_arrangement.write(track, target, args.get("points") or [], args.get("path"))
        elif action == "delete":
            out = live_arrangement.delete(track, target, args.get("path"))
        elif action == "apply":
            out = live_arrangement.apply(args.get("lanes") or [], args.get("path"))
        else:
            return _result({"ok": False, "error": "action must be list, read, write, delete, or apply"})
    except (live_arrangement.AutomationError, live_sets.SetError, ValueError, KeyError) as e:
        return _result({"ok": False, "error": str(e)})
    return _result(dict(out, ok=True))


def live_vst(args, **_):
    a = args.get("action")
    try:
        if a == "catalog":
            out = {"plugins": vst_ops.catalog(args.get("query"))}
        elif a == "params":
            out = vst_ops.params(args["plugin"], args.get("query"), args.get("bundle_plugin"))
        elif a == "presets":
            out = vst_ops.presets(args["plugin"], args.get("query"))
        elif a == "programs":
            out = vst_ops.programs(args["track"], args["device"])
        elif a == "select_program":
            out = vst_ops.select_program(args["track"], args["device"], args["program"])
        elif a == "expose":
            out = vst_ops.expose(args["track"], args["device"], args["params"], args.get("bundle_plugin"))
        elif a == "load_state":
            out = vst_ops.load_state(args["track"], args["device"], args.get("preset"), args.get("values"), args.get("bundle_plugin"))
        else:
            return _result({"ok": False, "error": "unknown action"})
    except (vst_ops.VstError, live_sets.SetError, ValueError, KeyError) as e:
        return _result({"ok": False, "error": str(e)})
    return _result(dict(out, ok=True))


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
        "description": "Read the open Ableton Live Set: one line per track (index, name, level, device chain, clips by slot), returns, scenes, tempo. Pass `track` to also get its devices' parameters as name -> display value (filter with params_query; ranges for raw values and choices). Call this before editing and after edits to verify.",
        "parameters": {
            "type": "object",
            "properties": {
                "track": {"description": "Track index or name to detail devices/parameters for", "type": ["integer", "string"]},
                "clips": {"type": "boolean", "description": "Include clip summaries (default true)"},
                "detail": {"type": "string", "enum": ["summary", "full"], "description": "summary (default): one line per track; full: every field"},
                "params_query": {"type": "string", "description": "with track: only parameters whose names contain these words"},
                "params_limit": {"type": "integer", "description": "with track: parameters per device (default 40)"},
                "ranges": {"type": "boolean", "description": "with track: include raw value, min, max, and choices for each parameter"},
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

SCHEMAS["live_set"] = {
    "name": "live_set",
    "description": "Manage Live Set files. info: the open Set's name and path. new: start a new Set (Live's default template). open: open a .als file. save: save the open Set. save_as: save under a new name into <directory>/<name> Project/ (default directory ~/Documents/Ableton Live Projects/Hermes). new/open replace the open Set: if it has unsaved changes Live asks first, and you must pass on_unsaved — \"save\" (keep the producer's work; preferred), \"discard\" (only when the producer said so), or \"cancel\" (default: stops and reports). After new/open the Live tools work on the new Set.",
    "parameters": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["info", "new", "open", "save", "save_as"]},
            "path": {"type": "string", "description": "open: path to the .als"},
            "name": {"type": "string", "description": "new/save_as: Set name"},
            "directory": {"type": "string", "description": "new/save_as: folder for the Project (default ~/Documents/Ableton Live Projects/Hermes)"},
            "on_unsaved": {"type": "string", "enum": ["save", "discard", "cancel"]},
        },
    },
}

SCHEMAS["live_arrangement_automation"] = {
    "name": "live_arrangement_automation",
    "description": "Arrangement (track-lane) automation, which Live's API can't reach directly: list, read, write (create or replace), or delete the envelope for one parameter on the timeline. target: \"volume\" | \"pan\" | \"send:A\" | \"<device index or name>:<parameter>\" (e.g. \"Auto Filter:Frequency\", \"0:Macro 1\"). points: [[beat, value], ...] with raw numbers or display values (\"200 Hz\", \"-6 dB\"). Each write/delete saves the Set, edits its file, and reopens it (a few seconds): use action apply with lanes: [{track, target, points} or {track, target, delete: true}] to change many lanes in ONE cycle. Requires a Set opened or created with live_set. For clip envelopes use live_ops automate instead.",
    "parameters": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["list", "read", "write", "delete", "apply"]},
            "lanes": {"type": "array", "items": {"type": "object"}, "description": "apply: [{track, target, points} | {track, target, delete: true}]"},
            "track": {"type": "string"},
            "target": {"type": "string"},
            "points": {"type": "array", "items": {"type": "array"}},
            "path": {"type": "string", "description": "the Set file, if it wasn't opened with live_set"},
        },
    },
}

SCHEMAS["live_vst"] = {
    "name": "live_vst",
    "description": "Work inside VST3 plug-ins. catalog: installed VST3s. params: a plug-in's full parameter list by name (thousands for big synths), searchable. presets: preset files on disk for a plug-in (loadable=true for .vstpreset; vendor formats need the plug-in's own browser via computer use). programs / select_program: presets the plug-in exposes to Live directly. expose: make named parameters controllable from Live (then set_params, automate, macros work on them; up to 128). load_state: load a .vstpreset and/or set parameter values (by params name) as the plug-in's state. expose and load_state save the Set, edit its file, and reopen it. bundle_plugin picks one plug-in inside a .vst3 bundle that contains several (normally inferred from the device name).",
    "parameters": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["catalog", "params", "presets", "programs", "select_program", "expose", "load_state"]},
            "plugin": {"type": "string"}, "bundle_plugin": {"type": "string"}, "query": {"type": "string"},
            "track": {"type": "string"}, "device": {"type": ["string", "integer"]},
            "program": {"type": ["string", "integer"]},
            "params": {"type": "array", "items": {"type": "string"}},
            "preset": {"type": "string"}, "values": {"type": "object"},
        },
    },
}

HANDLERS = {"live_set": live_set, "live_vst": live_vst, "live_arrangement_automation": live_arrangement_automation, "live_inspect": live_inspect, "live_ops": live_ops, "live_browse": live_browse, "live_review": live_review, "live_analyze": live_analyze}


def register(ctx):
    if platform.system() not in ("Darwin", "Windows"):
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
    ctx.register_system_prompt_section("actual-assistant-engineer.local-model", models.steering_section, max_chars=1200)
    ctx.register_cli_command("assistant-engineer", "Actual Assistant Engineer: setup, status, review gate", cli.configure, cli.handle,
                             description="Install the Live control surface, check the connection, and gate /goal on track completeness.")
    skills = Path(__file__).parent / "skills"
    for child in sorted(skills.iterdir()):
        if (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
