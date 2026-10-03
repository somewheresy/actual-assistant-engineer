"""Arrangement automation in .als files, against a Set saved by Live 12.4.5."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin" / "actual-assistant-engineer"))
import als_automation as A  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "prog-house-opus.als"


@pytest.fixture
def tree(tmp_path):
    return A.load(FIXTURE)


def test_locates_mixer_and_device_parameters_by_lom_style_names(tree):
    bass = A.track_named(tree, "Bass")
    assert A.param_element(bass, "volume").tag == "Volume"
    assert A.param_element(bass, "send:A").tag == "Send"
    assert A.param_element(bass, "1:Frequency").tag == "Filter_Frequency"  # Auto Filter is device 1
    assert A.param_element(bass, "2:Threshold").tag == "Threshold"  # Compressor
    assert A.param_element(bass, "2:Device On").tag == "On"
    with pytest.raises(ValueError, match="no parameter"):
        A.param_element(bass, "2:Nonexistent Knob")


def test_reads_existing_envelope_written_by_live(tree):
    bass = A.track_named(tree, "Bass")
    targets = A.list_targets(bass)
    assert targets, "fixture has arrangement automation on Bass"
    param = A.param_element(bass, "1:Frequency")
    pts = A.read(bass, param)
    assert pts is None or all(isinstance(t, float) for t, _ in pts)


def test_write_read_delete_round_trip_survives_save(tree, tmp_path):
    bass = A.track_named(tree, "Bass")
    vol = A.param_element(bass, "volume")
    A.write(bass, vol, [(0, 0.2), (64, 1.0), (32, 0.5)])
    out = tmp_path / "out.als"
    A.save(tree, out)
    again = A.track_named(A.load(out), "Bass")
    vol2 = A.param_element(again, "volume")
    assert A.read(again, vol2) == [(0.0, 0.2), (32.0, 0.5), (64.0, 1.0)]
    assert "Volume" in A.list_targets(again)
    assert A.delete(again, vol2) and A.read(again, vol2) is None


def test_unit_mode_inference():
    assert A.unit_mode(0.5, 0.5, 50.0) == "raw"
    assert A.unit_mode(9999.99, 0.8997, 10000.0) == "display"
    assert A.unit_mode(0.6, 0.85, 0.0, is_volume=True) == "gain"
