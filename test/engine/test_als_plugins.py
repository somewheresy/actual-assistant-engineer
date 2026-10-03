"""VST3 PluginDevice editing on a minimal Live-shaped element."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "plugin"))
import importlib

P = importlib.import_module("actual-assistant-engineer.als_plugins")

SLOT = '<PluginFloatParameter Id="{i}"><ParameterName Value="" /><ParameterId Value="-1" /><VisualIndex Value="1073741823" /></PluginFloatParameter>'
DEVICE = (
    '<PluginDevice><PluginDesc><Vst3PluginInfo><Name Value="Example Synth" /><Preset><Vst3Preset>'
    "<ProcessorState>AA</ProcessorState><ControllerState>BB</ControllerState></Vst3Preset></Preset></Vst3PluginInfo></PluginDesc>"
    "<ParameterList>%s</ParameterList></PluginDevice>" % "".join(SLOT.format(i=i) for i in range(4))
)


def test_expose_fills_slots_in_order_and_clears_the_rest():
    d = ET.fromstring(DEVICE)
    P.expose(d, [(812, "Filter 1 Freq"), (3, "Amp")])
    assert P.exposed(d) == [(812, "Filter 1 Freq"), (3, "Amp")]
    assert [s.find("VisualIndex").get("Value") for s in d.find("ParameterList")][:3] == ["0", "1", "1073741823"]
    P.expose(d, [(5, "X")])
    assert P.exposed(d) == [(5, "X")]


def test_expose_refuses_more_than_live_shows():
    d = ET.fromstring(DEVICE)
    try:
        P.expose(d, [(i, str(i)) for i in range(5)])
        assert False
    except ValueError as e:
        assert "at most 4" in str(e)


def test_set_state_replaces_both_blobs():
    d = ET.fromstring(DEVICE)
    P.set_state(d, "58666572", "CAFE")
    assert P.state(d) == (["58666572"], ["CAFE"])
    assert P.plugin_name(d) == "Example Synth"
