"""VST3 plug-ins inside a Live Set file: replace a plug-in's state, and choose which of its
parameters Live exposes (Live shows at most 128, the "configured" parameters).

A PluginDevice stores its state as hex ProcessorState/ControllerState blobs and its exposed
parameters as PluginFloatParameter slots (ParameterId -1 = unused slot).
"""

from . import als_automation as A


def plugin_device(tree, track_ref, device_index):
    track = A.track_named(tree, track_ref)
    devices = list(track.find("DeviceChain/DeviceChain/Devices"))
    if not 0 <= device_index < len(devices):
        raise ValueError("device %d out of range (track has %d)" % (device_index, len(devices)))
    d = devices[device_index]
    if d.tag != "PluginDevice" or d.find("PluginDesc/Vst3PluginInfo") is None:
        raise ValueError("device %d is %s, not a VST3 plug-in" % (device_index, d.tag))
    return d


def plugin_name(device):
    return device.find("PluginDesc/Vst3PluginInfo/Name").get("Value")


def set_state(device, processor_hex, controller_hex):
    preset = device.find("PluginDesc/Vst3PluginInfo/Preset/Vst3Preset")
    preset.find("ProcessorState").text = processor_hex
    if controller_hex:
        preset.find("ControllerState").text = controller_hex


def state(device):
    preset = device.find("PluginDesc/Vst3PluginInfo/Preset/Vst3Preset")
    return (preset.find("ProcessorState").text or "").split(), (preset.find("ControllerState").text or "").split()


def expose(device, params):
    """params: [(plugin parameter index, name), ...] in the order Live should show them."""
    slots = list(device.find("ParameterList"))
    if len(params) > len(slots):
        raise ValueError("Live exposes at most %d parameters on this device" % len(slots))
    for k, slot in enumerate(slots):
        if k < len(params):
            index, name = params[k]
            slot.find("ParameterId").set("Value", str(int(index)))
            slot.find("ParameterName").set("Value", name)
            slot.find("VisualIndex").set("Value", str(k))
        else:
            slot.find("ParameterId").set("Value", "-1")
            slot.find("VisualIndex").set("Value", "1073741823")


def exposed(device):
    return [
        (int(s.find("ParameterId").get("Value")), s.find("ParameterName").get("Value"))
        for s in device.find("ParameterList")
        if int(s.find("ParameterId").get("Value")) >= 0
    ]
