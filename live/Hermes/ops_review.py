"""Completeness review: objective facts about the Set and the gaps between it and a finished track.

Reports state only (empty clips, silent tracks, missing arrangement or locators...). Musical
judgement stays with the caller; `require` turns brief-level asks into checkable gaps.
"""

from .ops import op


def _midi_has_instrument(track):
    return any(d.type == 1 for d in track.devices)  # Live.Device.DeviceType.instrument


def _clip_gaps(label, clip):
    if clip.is_midi_clip and not clip.get_notes_extended(0, 128, 0.0, clip.length):
        return ["%s is an empty MIDI clip" % label]
    return []


@op("review")
def _review(ctx, require=None, ignore_tracks=()):
    """require: {arrangement, locators, automation, sidechain, mix: bool, min_sections: int, min_bars: int}."""
    song = ctx.song
    req = require or {}
    gaps, tracks = [], []
    automation = sidechains = 0
    arr_end = 0.0
    for i, t in enumerate(song.tracks):
        if t.name in ignore_tracks:
            continue
        session = [(j, s.clip) for j, s in enumerate(t.clip_slots) if s.has_clip]
        arrangement = list(t.arrangement_clips)
        if not session and not arrangement and not list(t.devices):
            continue  # untouched template track
        for j, c in session:
            gaps += _clip_gaps("%s slot %d" % (t.name, j), c)
            automation += _envelopes(t, c)
        for c in arrangement:
            gaps += _clip_gaps("%s arrangement clip at beat %g" % (t.name, c.start_time), c)
            automation += _envelopes(t, c)
            arr_end = max(arr_end, c.end_time)
        if t.has_midi_input and not _midi_has_instrument(t) and (session or arrangement):
            gaps.append("MIDI track %s has clips but no instrument" % t.name)
        if not session and not arrangement:
            gaps.append("track %s has devices but no clips" % t.name)
        if req.get("arrangement") and session and not arrangement:
            gaps.append("track %s has session clips but nothing in the arrangement" % t.name)
        off = [d.name for d in t.devices if not d.is_active]
        for d in t.devices:
            routing = getattr(d, "input_routing_type", None)
            if routing is not None and routing.display_name not in ("", "No Input"):
                sidechains += 1
        tracks.append({"name": t.name, "session_clips": len(session), "arrangement_clips": len(arrangement), "devices_off": off})
    locators = sorted(((c.time, c.name) for c in song.cue_points))
    bars = arr_end / song.signature_numerator
    if req.get("arrangement") and arr_end == 0:
        gaps.append("the arrangement is empty")
    if req.get("locators") and arr_end and not locators:
        gaps.append("the arrangement has no section locators")
    if locators and arr_end and locators[0][0] > 0:
        gaps.append("no locator at the song start (first is at beat %g)" % locators[0][0])
    unnamed = [t for t, n in locators if not n or n.isdigit()]
    if unnamed:
        gaps.append("locators without section names at beats %s" % ", ".join("%g" % t for t in unnamed))
    if req.get("min_sections") and arr_end and len(locators) < req["min_sections"]:
        gaps.append("only %d sections marked; the brief needs at least %d" % (len(locators), req["min_sections"]))
    if req.get("min_bars") and arr_end and bars < req["min_bars"]:
        gaps.append("arrangement is %g bars; the brief needs at least %d" % (bars, req["min_bars"]))
    if req.get("automation") and automation == 0:
        gaps.append("no automation envelopes on any clip")
    if req.get("sidechain") and sidechains == 0:
        gaps.append("no sidechain compression is routed")
    if req.get("mix") and all(abs(t.mixer_device.volume.value - 0.85) < 1e-4 for t in song.tracks if t.name in [x["name"] for x in tracks]):
        gaps.append("every track is still at 0 dB; nothing has been balanced")
    return {
        "complete": not gaps,
        "gaps": gaps,
        "arrangement": {"bars": bars, "seconds": round(arr_end * 60 / song.tempo), "locators": [{"beat": t, "name": n} for t, n in locators]},
        "automation_envelopes": automation,
        "sidechains": sidechains,
        "tracks": tracks,
    }


def _envelopes(track, clip):
    params = [track.mixer_device.volume, track.mixer_device.panning] + list(track.mixer_device.sends)
    for d in track.devices:
        params += list(d.parameters)
    return sum(1 for p in params if clip.automation_envelope(p) is not None)
