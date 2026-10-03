"""Lean results: compact notes from the bridge, and the plugin's result compaction."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
C = importlib.import_module("actual-assistant-engineer.compact")


def test_get_notes_rows_by_default_objects_on_request(run, song):
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 4}, {"op": "add_notes", "track": 0, "slot": 0, "notes": [[60, 0.333333, 0.5, 90], {"pitch": 62, "start": 1, "duration": 1, "mute": True}]})
    results, ok = run({"op": "get_notes", "track": 0, "slot": 0})
    assert sorted(results[0]["notes"]) == [[60, 0.3333, 0.5, 90.0], [62, 1.0, 1.0, 100.0, 1]]
    results, ok = run({"op": "get_notes", "track": 0, "slot": 0, "format": "objects"})
    assert {n["pitch"] for n in results[0]["notes"]} == {60, 62}


def test_compact_rounds_and_drops_noise_but_keeps_top_level_ok():
    out = C.compact({"ok": True, "results": [{"ok": True, "value": 0.30000001192092896, "x": None, "items": []}, {"ok": False, "error": "e"}]})
    assert out == {"ok": True, "results": [{"value": 0.3}, {"ok": False, "error": "e"}]}


def test_track_line_and_param_filter():
    t = {"index": 2, "name": "BASS", "midi": True, "level": "-6.0 dB", "pan": 0.0, "mute": True, "devices": [{"name": "Operator"}, {"name": "Compressor"}], "clips": {"0": {"name": "Verse", "length": 16}}, "playing_slot": -1}
    assert C.track_line(t) == "2 BASS | midi | -6.0 dB | mute | devices: Operator > Compressor | clips: 0:Verse(4b)"
    dev = {"name": "Auto Filter", "class": "AutoFilter2", "params": [{"name": "Frequency", "display": "1.20 kHz", "value": 0.59}, {"name": "Resonance", "display": "40 %", "value": 0.4}]}
    assert C.device_params(dev, "freq") == {"device": "Auto Filter", "class": "AutoFilter2", "params": {"Frequency": "1.20 kHz"}}
    assert "more" in C.device_params(dev, limit=1)
