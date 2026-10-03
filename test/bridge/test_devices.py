"""Declarative device graphs: nested racks built exactly as described, readable and reconfigurable."""

SPEC = {
    "name": "Instrument Rack",
    "macros": 10,
    "macro_values": {"1": 64},
    "chains": [
        {"name": "Sub", "devices": [{"name": "Operator"}], "volume_db": -3},
        {
            "name": "Grit",
            "pan": 0.2,
            "devices": [
                {"name": "Wavetable"},
                {"name": "Audio Effect Rack", "chains": [{"name": "Dry"}, {"name": "Crush", "devices": [{"name": "Compressor", "params": {"Ratio": 4}}]}]},
            ],
        },
    ],
}


def test_build_device_builds_nested_racks_as_described(run, song):
    results, ok = run({"op": "build_device", "track": 0, "device": SPEC})
    assert ok, results
    rack = song.tracks[0].devices[0]
    assert [c.name for c in rack.chains] == ["Sub", "Grit"]
    assert rack.visible_macro_count == 10 and rack.parameters[1].value == 64
    grit = rack.chains[1]
    assert [d.name for d in grit.devices] == ["Wavetable", "Audio Effect Rack"]
    assert grit.mixer_device.panning.value == 0.2
    crush = grit.devices[1].chains[1]
    assert crush.devices[0].parameters[2].value == 4  # Ratio
    tree = results[0]
    assert tree["chains"][1]["devices"][1]["chains"][1]["devices"][0]["path"] == 'song.tracks["1-MIDI"].devices[0].chains[1].devices[1].chains[1].devices[0]'


def test_drum_rack_chains_take_notes_and_choke_groups(run, song):
    spec = {"name": "Drum Rack", "chains": [{"name": "Kick", "note": 36, "devices": [{"name": "Simpler"}]}, {"name": "Open Hat", "note": 46, "choke": 1}]}
    results, ok = run({"op": "build_device", "track": 1, "device": spec})
    assert ok
    chains = song.tracks[1].devices[0].chains
    assert [(c.name, c.in_note, c.choke_group) for c in chains] == [("Kick", 36, 0), ("Open Hat", 46, 1)]
    assert results[0]["chains"][1]["note"] == 46


def test_device_tree_and_configure_reach_devices_at_any_depth(run, song):
    run({"op": "build_device", "track": 0, "device": SPEC})
    results, ok = run({"op": "device_tree", "track": 0})
    path = results[0]["devices"][0]["chains"][1]["devices"][1]["chains"][1]["devices"][0]["path"]
    results, ok = run({"op": "configure", "path": path, "params": {"Threshold": -20}, "on": False})
    assert ok
    comp = song.tracks[0].devices[0].chains[1].devices[1].chains[1].devices[0]
    assert comp.parameters[1].value == -20 and comp.parameters[0].value == 0.0
    # Adding chains to an existing rack.
    run({"op": "configure", "path": 'song.tracks[0].devices[0]', "chains": [{"name": "Air", "devices": [{"name": "Reverb"}]}]})
    assert [c.name for c in song.tracks[0].devices[0].chains] == ["Sub", "Grit", "Air"]


def test_chains_on_a_non_rack_and_empty_specs_fail_clearly(run):
    results, ok = run({"op": "build_device", "track": 0, "device": {"name": "Compressor", "chains": [{"name": "x"}]}})
    assert not ok and "not a rack" in results[0]["error"]
    results, ok = run({"op": "build_device", "track": 0, "device": {"params": {}}})
    assert not ok and "needs name" in results[0]["error"]
