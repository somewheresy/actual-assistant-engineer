"""Automation ops: ramp math, target resolution, and envelope round trips."""

import pytest

import fake_live
from Hermes.ops_automation import ramp_steps, resolve_target


# --- ramp math --------------------------------------------------------------


def test_ramp_linear_interpolates():
    steps = ramp_steps([[0, 0], [4, 1]], "linear", 1)
    assert steps == [(0.0, 1.0, 0.0), (1.0, 1.0, 0.25), (2.0, 1.0, 0.5), (3.0, 1.0, 0.75), (4.0, 1.0, 1.0)]
    fine = ramp_steps([[0, 0], [0.5, 1]], "linear", 0.0625)
    assert len(fine) == 9 and abs(fine[4][2] - 0.5) < 1e-9  # t=0.25 is near the midpoint


def test_ramp_step_holds_values():
    steps = ramp_steps([[0, 0.2], [2, 0.8], [3, 0.4]], "step")
    assert steps == [(0.0, 2.0, 0.2), (2.0, 1.0, 0.8), (3.0, 0.0625, 0.4)]


def test_ramp_single_point_and_unsorted():
    assert ramp_steps([[1, 5]], "linear") == [(1.0, 0.0625, 5.0)]
    steps = ramp_steps([[2, 1], [0, 0]], "step")
    assert steps == [(0.0, 2.0, 0.0), (2.0, 0.0625, 1.0)]


def test_ramp_rejects_bad_input():
    with pytest.raises(Exception):
        ramp_steps([[0, 1]], "spline")
    with pytest.raises(Exception):
        ramp_steps([[0, 1]], "linear", 0)
    with pytest.raises(Exception):
        ramp_steps([[0, 1, 2]])


# --- target resolution --------------------------------------------------------


@pytest.fixture
def track(song):
    t = song.tracks[0]
    t.devices.append(fake_live.Device("Auto Filter", params=[fake_live.DeviceParameter("Frequency", 120.0, 20.0, 20000.0)]))
    return t


def test_resolve_mixer_targets(track):
    assert resolve_target(track, "volume") is track.mixer_device.volume
    assert resolve_target(track, "pan") is track.mixer_device.panning
    assert resolve_target(track, "send:A") is track.mixer_device.sends[0]
    assert resolve_target(track, "send:1") is track.mixer_device.sends[1]
    assert resolve_target(track, "send:b") is track.mixer_device.sends[1]


def test_resolve_device_targets(track):
    freq = track.devices[0].parameters[1]
    assert resolve_target(track, "Auto Filter:Frequency") is freq
    assert resolve_target(track, "0:1") is freq
    assert resolve_target(track, "0:Frequency") is freq
    assert resolve_target(track, "Auto Filter:1") is freq


def test_resolve_failures(track):
    for bad in ("nope", "send:C", "0:9", "Nope:Frequency", "Auto Filter:Nope", ""):
        with pytest.raises(Exception):
            resolve_target(track, bad)


# --- ops -------------------------------------------------------------------


def test_automate_and_read_round_trip_session(run, song):
    run({"op": "create_clip", "track": 0, "slot": 1, "length": 8})
    r, ok = run({"op": "automate", "track": 0, "slot": 1, "target": "volume", "points": [[0, 0.5], [4, 1.0]]})
    assert ok and r[0]["target"] == "Track Volume" and r[0]["points"] == 2 and r[0]["steps"] > 2
    r, ok = run({"op": "read_automation", "track": 0, "slot": 1, "target": "volume", "times": [0, 2, 4]})
    assert ok and r[0]["present"] and r[0]["values"][0] == 0.5 and abs(r[0]["values"][1] - 0.75) < 1e-6 and r[0]["values"][2] == 1.0
    assert r[0]["display"] == ["%.2f" % v for v in r[0]["values"]]


def test_automate_arrangement_clip(run, song):
    run({"op": "arrangement_clip", "track": 0, "start": 4, "length": 8})
    r, ok = run({"op": "automate", "track": 0, "arrangement": 0, "target": "pan", "points": [[0, -1], [8, 1]], "curve": "step"})
    assert ok and r[0]["steps"] == 2
    r, ok = run({"op": "read_automation", "track": 0, "arrangement": 0, "target": "pan", "times": [1, 7, 9]})
    assert ok and r[0]["values"] == [-1.0, -1.0, 1.0]


def test_automate_rewrites_overlapping_points_and_clamps(run, song):
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 8})
    run({"op": "automate", "track": 0, "slot": 0, "target": "volume", "points": [[0, 5], [2, 1]], "curve": "step"})
    r, ok = run({"op": "read_automation", "track": 0, "slot": 0, "target": "volume", "times": [0, 1, 3]})
    assert ok and r[0]["values"] == [1.0, 1.0, 1.0]  # clamped to max=1
    run({"op": "automate", "track": 0, "slot": 0, "target": "volume", "points": [[1, 0.2]], "curve": "step", "clear": True})
    r, ok = run({"op": "read_automation", "track": 0, "slot": 0, "target": "volume", "times": [1, 3]})
    assert ok and r[0]["values"] == [0.2, 0.2]


def test_automate_device_param_by_name(run, song):
    song.tracks[0].insert_device("Auto Filter")
    song.tracks[0].devices[0].parameters.append(fake_live.DeviceParameter("Frequency", 120.0, 20.0, 20000.0))
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 4})
    r, ok = run({"op": "automate", "track": 0, "slot": 0, "target": "Auto Filter:Frequency", "points": [[0, 100], [4, 200]]})
    assert ok and r[0]["target"] == "Frequency"
    r, ok = run({"op": "read_automation", "track": 0, "slot": 0, "target": "0:1", "times": [2]})
    assert ok and abs(r[0]["values"][0] - 150.0) < 1e-6


def test_automate_requires_exactly_one_clip_selector(run, song):
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 4})
    r, ok = run({"op": "automate", "track": 0, "target": "volume", "points": [[0, 0.5]]})
    assert not ok and "exactly one" in r[0]["error"]
    r, ok = run({"op": "automate", "track": 0, "slot": 0, "arrangement": 0, "target": "volume", "points": [[0, 0.5]]})
    assert not ok and "exactly one" in r[0]["error"]


def test_read_absent_envelope(run, song):
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 4})
    r, ok = run({"op": "read_automation", "track": 0, "slot": 0, "target": "volume", "times": [0]})
    assert ok and not r[0]["present"] and r[0]["values"] == []


def test_clear_and_list(run, song):
    song.tracks[0].insert_device("Auto Filter")
    song.tracks[0].devices[0].parameters.append(fake_live.DeviceParameter("Frequency", 120.0, 20.0, 20000.0))
    run({"op": "create_clip", "track": 0, "slot": 0, "length": 8})
    run({"op": "automate", "track": 0, "slot": 0, "target": "volume", "points": [[0, 0.5]]})
    run({"op": "automate", "track": 0, "slot": 0, "target": "send:A", "points": [[0, 0.5]]})
    run({"op": "automate", "track": 0, "slot": 0, "target": "Auto Filter:Frequency", "points": [[0, 100]]})
    r, ok = run({"op": "list_automation", "track": 0, "slot": 0})
    assert ok and set(r[0]["targets"]) == {"volume", "send:A", "Auto Filter:Frequency"}
    r, ok = run({"op": "clear_automation", "track": 0, "slot": 0, "target": "volume"})
    assert ok and r[0]["cleared"] == "volume"
    r, ok = run({"op": "read_automation", "track": 0, "slot": 0, "target": "volume", "times": [0]})
    assert ok and not r[0]["present"]
    r, ok = run({"op": "list_automation", "track": 0, "slot": 0})
    assert ok and set(r[0]["targets"]) == {"send:A", "Auto Filter:Frequency"}
    r, ok = run({"op": "clear_automation", "track": 0, "slot": 0})
    assert ok and r[0]["cleared"] == "all"
    r, ok = run({"op": "list_automation", "track": 0, "slot": 0})
    assert ok and r[0]["targets"] == []
