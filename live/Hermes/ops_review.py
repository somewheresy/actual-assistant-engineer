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


def _clip_notes(clip):
    return list(clip.get_notes_extended(0, 128, 0.0, clip.length)) if clip.is_midi_clip else []


@op("analyze")
def _analyze(ctx, ignore_tracks=()):
    """Section-by-section facts about the arrangement for QA: who plays, how densely, in what
    register, how much repeats, where automation moves, plus overlaps between parts."""
    song = ctx.song
    beats_per_bar = song.signature_numerator
    cues = sorted((c.time, c.name) for c in song.cue_points)
    tracks = [t for t in song.tracks if t.name not in ignore_tracks and list(t.arrangement_clips)]
    end = max([c.end_time for t in tracks for c in t.arrangement_clips] or [0.0])
    if not cues or cues[0][0] > 0:
        cues = [(0.0, "(start)")] + cues
    bounds = [(t, n, cues[i + 1][0] if i + 1 < len(cues) else end) for i, (t, n) in enumerate(cues)]
    sections = []
    seen_clips = {}
    for start, name, stop in bounds:
        if stop <= start:
            continue
        bars = (stop - start) / beats_per_bar
        parts = []
        for t in tracks:
            clips = [c for c in t.arrangement_clips if c.start_time < stop and c.end_time > start]
            if not clips:
                continue
            notes, lo, hi, automated, names = 0, 127, 0, 0, set()
            for c in clips:
                ns = _clip_notes(c)
                notes += len(ns)
                if ns:
                    lo, hi = min(lo, min(n.pitch for n in ns)), max(hi, max(n.pitch for n in ns))
                automated += _envelopes(t, c)
                names.add(c.name)
            key = (t.name, tuple(sorted(names)))
            repeated_from = seen_clips.get(key)
            seen_clips.setdefault(key, name)
            parts.append({
                "track": t.name,
                "notes_per_bar": round(notes / bars, 1) if bars else 0,
                "range": [lo, hi] if notes else None,
                "automated_params": automated,
                "same_clips_as": repeated_from,
            })
        energy = sum(p["notes_per_bar"] for p in parts)
        sections.append({"name": name, "start_bar": start / beats_per_bar + 1, "bars": bars, "tracks": len(parts), "energy": round(energy, 1), "parts": parts})
    # Register clashes: pairs of melodic parts whose ranges overlap by an octave or more in the same section.
    clashes = []
    for s in sections:
        ranged = [p for p in s["parts"] if p["range"] and p["range"][1] - p["range"][0] > 0]
        for i, a in enumerate(ranged):
            for b in ranged[i + 1:]:
                overlap = min(a["range"][1], b["range"][1]) - max(a["range"][0], b["range"][0])
                if overlap >= 12:
                    clashes.append({"section": s["name"], "tracks": [a["track"], b["track"]], "overlap_semitones": overlap})
    mix = {t.name: t.mixer_device.volume.str_for_value(t.mixer_device.volume.value) for t in tracks}
    return {
        "bars": end / beats_per_bar,
        "sections": sections,
        "energy_curve": [s["energy"] for s in sections],
        "unchanged_repeats": sum(1 for s in sections for p in s["parts"] if p["same_clips_as"]),
        "register_clashes": clashes,
        "mix": mix,
    }
