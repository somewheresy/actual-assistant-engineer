"""Core batch semantics: the contract every op relies on."""


def test_batch_is_one_undo_step_and_stops_at_first_failure(run, song):
    results, ok = run({"op": "create_track", "name": "A"}, {"op": "set_track", "track": "missing", "name": "x"}, {"op": "create_track", "name": "B"})
    assert not ok
    assert [r["ok"] for r in results] == [True, False]
    assert [t.name for t in song.tracks][-1] == "A"
    assert song.undo_steps == 1 and song.undo_depth == 0


def test_refs_resolve_against_earlier_results_and_fail_cleanly(run, song):
    results, ok = run({"op": "create_track", "name": "Lead"}, {"op": "create_clip", "track": "$0.index", "slot": 1, "length": 8})
    assert ok and song.tracks[results[0]["index"]].clip_slots[1].clip.length == 8
    results, ok = run({"op": "transport", "tempo": 124}, {"op": "create_clip", "track": "$0.index", "slot": 0, "length": 4})
    assert not ok and "op 0 returned" in results[1]["error"]
    results, ok = run({"op": "create_clip", "track": "$5.index", "slot": 0, "length": 4})
    assert not ok and "refer to tracks by name" in results[0]["error"]


def test_unknown_op_and_expect_guard(run, song):
    results, ok = run({"op": "nope"})
    assert not ok and "unknown op" in results[0]["error"]
    results, ok = run({"op": "set_track", "track": 0, "expect": "Bass", "name": "WRONG"})
    assert not ok and song.tracks[0].name == "1-MIDI"


def test_patterns_hits_accents_holds_and_arrays(run, song):
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 4})
    results, ok = run({"op": "add_notes", "track": 0, "slot": 0, "patterns": {"36": "x.X.", "45": "x--."}, "notes": [[60, 1, 0.5, 90]]})
    assert ok and results[0]["added"] == 4
    notes = sorted((n.pitch, n.start_time, n.duration, n.velocity) for n in song.tracks[0].clip_slots[0].clip.notes)
    assert notes == [(36, 0.0, 0.25, 100), (36, 0.5, 0.25, 120), (45, 0.0, 0.75, 100), (60, 1.0, 0.5, 90)]


def test_arrange_scenes_fills_sections_and_names_locators(run, song):
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 4}, {"op": "create_clip", "track": 0, "slot": 1, "length": 8}, {"op": "create_clip", "track": 1, "slot": 1, "length": 3})
    song.scenes[0].name, song.scenes[1].name = "Intro", "Drop"
    results, ok = run({"op": "arrange_scenes", "sections": [{"scene": 0, "bars": 2}, {"scene": 1, "bars": 4}]})
    assert ok and results[0]["end"] == 24.0
    starts = [c.start_time for c in song.tracks[0].arrangement_clips]
    assert starts == [0.0, 4.0, 8.0, 16.0]
    # 3-beat clip repeated into a 16-beat section: 5 full copies plus a new 1-beat clip ending at 24.
    t1 = song.tracks[1].arrangement_clips
    assert len(t1) == 6 and (t1[-1].start_time, t1[-1].end_time) == (23.0, 24.0)
    assert [(c.time, c.name) for c in song.cue_points] == [(0.0, "Intro"), (8.0, "Drop")]


def test_lom_paths_get_set_call_and_refuse_private(run, song):
    results, ok = run(
        {"op": "get", "path": 'song.tracks["2-MIDI"].mixer_device.volume', "props": ["value"]},
        {"op": "set", "path": "song.tracks[0].mixer_device.panning", "prop": "value", "value": -0.5},
        {"op": "call", "path": "song.tracks[0]", "method": "insert_device", "args": ["Compressor", 0]},
    )
    assert ok and results[0]["values"]["value"] == 0.85
    assert song.tracks[0].mixer_device.panning.value == -0.5
    assert results[2]["result"]["name"] == "Compressor"
    for bad in ({"op": "get", "path": "song._data"}, {"op": "call", "path": "song", "method": "add_tempo_listener"}, {"op": "get", "path": "os.system"}):
        results, ok = run(bad)
        assert not ok


def test_display_values_map_through_live_labels(ctx):
    from Hermes.ops import param_value, parse_display
    import fake_live

    assert parse_display("2.5 kHz") == (2500.0, "hz") and parse_display("-6 dB") == (-6.0, "db") and parse_display("-inf dB")[0] == float("-inf")
    # A fake frequency knob: raw 0..1 maps to 20 Hz..20 kHz exponentially, labelled like Live.
    knob = fake_live.DeviceParameter("Frequency", 0.5)
    knob.str_for_value = lambda v: "%.1f Hz" % (20 * 1000 ** v) if 20 * 1000 ** v < 1000 else "%.2f kHz" % (20 * 1000 ** v / 1000)
    raw = param_value(knob, "500 Hz")
    assert abs(20 * 1000 ** raw - 500) < 1
    assert param_value(knob, 0.25) == 0.25 and param_value(knob, 7) == 1.0
    try:
        param_value(knob, "-6 dB")
        assert False
    except Exception as e:
        assert "displays values like" in str(e)
