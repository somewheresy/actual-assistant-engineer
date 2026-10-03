# Actual Assistant Engineer

Hermes operates Ableton Live as your assistant engineer. Ask in plain language — *"make a future beat"*, *"split the kick onto its own track and sidechain everything from it"*, *"automate the filter into the drop"* — and it builds, arranges, processes, mixes, and checks the track in your Live Set. Every musical decision comes from the model your Hermes runs; the plugin gives it hands and checks.

macOS, Ableton Live 12.4+ (any edition).

## Install

```bash
hermes plugins install actual-assistant-engineer     # accept the pedalboard dependency
hermes aae setup                                    # installs the Live control surface
```

Then, once, in Live: **Settings → Tempo & MIDI → Control Surface → Hermes**. Check everything with:

```bash
hermes aae status
```

Recommended Hermes config (`~/.hermes/config.yaml`) so the model calls the tools directly and can write parts in parallel:

```yaml
tools:
  tool_search:
    enabled: "off"
delegation:
  max_concurrent_children: 4
  oneshot_max_children: 4
```

Building the optional helpers (MIDI performance port, window screenshots) needs Xcode command line tools; `setup` skips them if `swiftc` is missing.

## Use

```bash
hermes chat -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer
```

To keep Hermes working until the track is finished, use a goal with the completeness gate:

```
/goal Make a progressive house track with a long build, a big breakdown and a drop, laid out in the Arrangement
/goal gate add hermes aae review --require arrangement,locators,automation,sidechain,mix
```

## Tools

| Tool | What it does |
|---|---|
| `live_set` | New, open, save, save as (never discards unsaved work unless told) |
| `live_inspect` | Read the Set: tracks, devices, mixer, clips, scenes |
| `live_ops` | Edit and play in batches (one undo step each): tracks, scenes, clips and notes, devices and racks built declaratively at any depth, parameters in display units, routing, sidechain, mixer, clip automation, arrangement, locators, and generic access to anything in Live's object model |
| `live_browse` | Find instruments, effects, kits, presets, samples, plug-ins |
| `live_vst` | Inside VST3 plug-ins: every parameter by name, presets on disk, programs, expose parameters to Live, load `.vstpreset`/values as state |
| `live_arrangement_automation` | Read and write arrangement-lane automation (through the Set file; Live's API can't reach it) |
| `live_review` | Completeness check: lists concrete gaps until the track is finished |
| `live_analyze` | Section-by-section QA: energy curve, repeats, register clashes, levels |

## What it touches

- A Unix socket (`~/Library/Application Support/ActualAssistantEngineer/live.sock`, owner-only) between Hermes and Live; no network ports.
- Files: the control surface link in Live's User Library, Set files it creates or saves (default `~/Documents/Ableton Live Projects/Hermes`), and a parameter cache in `~/Library/Caches/ActualAssistantEngineer`.
- Subprocesses: `osascript` (clicks Live's own File menu items and dialog buttons through Accessibility — never keystrokes), `open`, and an offline VST3 host (`pedalboard`) that loads installed plug-ins to read parameters and state.
- No network access and no credentials.
