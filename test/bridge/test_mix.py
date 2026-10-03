"""Devices, routing, sidechain, and mixer ops."""


def _make_sidechainable(run, song, track=0, source=1):
    run({"op": "insert_device", "track": track, "name": "Compressor"})
    src = song.tracks[source]
    song.tracks[track].devices[-1].available_input_routing_types = [
        type("RT", (), {"display_name": "No Input", "attached_object": None})(),
        type("RT", (), {"display_name": src.name, "attached_object": src})(),
    ]


# --- devices ---------------------------------------------------------------


def test_insert_delete_duplicate_device(run, song):
    results, ok = run(
        {"op": "insert_device", "track": 0, "name": "EQ Eight", "index": 0},
        {"op": "insert_device", "track": 0, "name": "Compressor"},
        {"op": "insert_device", "track": 1, "name": "Reverb", "expect": "2-MIDI"},
    )
    assert ok
    assert results[0] == {"ok": True, "index": 0, "name": "EQ Eight", "class": "EQ Eight"}
    assert results[1]["index"] == 1
    assert [d.name for d in song.tracks[0].devices] == ["EQ Eight", "Compressor"]
    results, ok = run({"op": "duplicate_device", "track": 0, "device": "EQ Eight"})
    assert ok and results[0]["index"] == 1
    assert [d.name for d in song.tracks[0].devices] == ["EQ Eight", "EQ Eight", "Compressor"]
    results, ok = run({"op": "delete_device", "track": 0, "device": 1})
    assert ok and results[0]["deleted"] == "EQ Eight"
    assert len(song.tracks[0].devices) == 2
    results, ok = run({"op": "delete_device", "track": 0, "device": "Nope"})
    assert not ok and "matched 0" in results[0]["error"]


def test_set_params_and_device_on(run, song):
    run({"op": "insert_device", "track": 0, "name": "Compressor"})
    results, ok = run({"op": "set_params", "track": 0, "device": 0, "values": {"Threshold": -12.5, "0": 0}})
    assert ok
    assert results[0]["Threshold"]["value"] == -12.5
    assert results[0]["Device On"]["value"] == 0.0
    assert song.tracks[0].devices[0].parameters[1].value == -12.5
    results, ok = run({"op": "set_params", "track": 0, "device": 0, "values": {"Ratio": 999}})
    assert ok and song.tracks[0].devices[0].parameters[2].value == 20.0
    results, ok = run({"op": "set_params", "track": 0, "device": 0, "values": {"Nope": 1}})
    assert not ok and "available" in results[0]["error"].lower() and "Threshold" in results[0]["error"]
    results, ok = run({"op": "device_on", "track": 0, "device": "Compressor", "on": False})
    assert ok and results[0]["on"] is False
    assert song.tracks[0].devices[0].parameters[0].value == 0.0


# --- routing -----------------------------------------------------------------


def test_set_routing(run, song):
    results, ok = run({"op": "set_routing", "track": 0, "output": "Sends Only", "output_channel": "Pre FX"})
    assert ok and results[0]["output"] == "Sends Only" and results[0]["output_channel"] == "Pre FX"
    assert song.tracks[0].output_routing_type.display_name == "Sends Only"
    results, ok = run({"op": "set_routing", "track": 0, "input": "No Input", "input_channel": "Mono In"})
    assert ok and results[0]["input"] == "No Input"
    results, ok = run({"op": "set_routing", "track": 0, "output": "Bogus"})
    assert not ok and "Master" in results[0]["error"] and "Sends Only" in results[0]["error"]


def test_sidechain(run, song):
    _make_sidechainable(run, song)
    results, ok = run({"op": "sidechain", "track": 0, "device": "Compressor", "source": "2-MIDI"})
    assert ok and results[0]["source"] == "2-MIDI" and results[0]["sidechain_on"] is True
    comp = song.tracks[0].devices[0]
    assert comp.input_routing_type.display_name == "2-MIDI"
    assert comp.parameters[3].value == 1.0
    results, ok = run({"op": "sidechain", "track": 0, "device": "Compressor", "source": "2-MIDI", "on": False})
    assert ok and comp.parameters[3].value == 0.0
    results, ok = run({"op": "sidechain", "track": 0, "device": "Compressor", "source": "1-MIDI"})
    assert not ok and "no input routing from track" in results[0]["error"]
    run({"op": "insert_device", "track": 1, "name": "Utility"})
    results, ok = run({"op": "sidechain", "track": 1, "device": "Utility", "source": "1-MIDI"})
    assert not ok and "no input routing" in results[0]["error"]


# --- mixer ---------------------------------------------------------------------


def test_db_conversion_round_trip():
    from Hermes.ops_mix import db_to_fader, fader_to_db

    assert db_to_fader(0) == 0.85
    assert db_to_fader(6) == 1.0
    assert db_to_fader(-70) == 0.0
    assert db_to_fader(-200) == 0.0
    for db in (-60, -48, -36, -24, -18, -12, -6, -3, 0, 3, 6):
        assert abs(fader_to_db(db_to_fader(db)) - db) < 1e-6
    assert abs(db_to_fader(-6) - 0.85 / 10 ** 0.15) < 1e-12


def test_mixer_edits_many_tracks_in_one_op(run, song):
    song.tracks[0].name = "Kick"
    results, ok = run(
        {"op": "mixer", "tracks": {
            "Kick": {"volume_db": -6, "pan": -0.25, "sends": {"A": 0.5, "B": 0.1}, "mute": True},
            "2-MIDI": {"volume_db": 3, "solo": True, "sends": {"A": 0}},
            "master": {"volume": 0.9},
        }}
    )
    assert ok
    kick, t2 = song.tracks[0], song.tracks[1]
    assert abs(kick.mixer_device.volume.value - 0.85 / 10 ** 0.15) < 1e-9
    assert kick.mixer_device.panning.value == -0.25
    assert kick.mixer_device.sends[0].value == 0.5 and kick.mixer_device.sends[1].value == 0.1
    assert kick.mute and not kick.solo
    assert t2.solo and abs(t2.mixer_device.volume.value - (0.85 + 0.15 * 0.5)) < 1e-9
    assert results[0]["master"]["volume_db"] == 2.0
    assert results[0]["Kick"]["volume_db"] == -6.0
    results, ok = run({"op": "mixer", "tracks": {}})
    assert not ok
    results, ok = run({"op": "mixer", "tracks": {"missing": {"pan": 0}}})
    assert not ok and "matched 0" in results[0]["error"]


def test_mixer_expect_guard(run, song):
    song.tracks[0].name = "Kick"
    results, ok = run({"op": "mixer", "tracks": {"Kick": {"expect": "Wrong", "pan": 0}}})
    assert not ok and song.tracks[0].mixer_device.panning.value == 0.0
