# Actual Assistant Engineer

Hermes operates Ableton Live as your assistant engineer. Ask in plain language — *"make a future beat"*, *"split the kick onto its own track and sidechain everything from it"*, *"automate the filter into the drop"* — and it builds, arranges, processes, mixes, and checks the track in your Live Set. Every musical decision comes from the model your Hermes runs; the plugin gives it hands and checks.

macOS and Windows, Ableton Live 12.4+ (any edition). Windows qualification status and prerequisites: [Windows setup](windows.md). Step-by-step guide: [Getting started](https://github.com/somewheresy/actual-assistant-engineer/blob/main/docs/getting-started.md).

## Install

```bash
hermes plugins install actual-assistant-engineer     # accept the pedalboard dependency
hermes assistant-engineer setup                                    # installs the Live control surface
```

Then, once, in Live: **Settings → Tempo & MIDI → Control Surface → Hermes**. Check everything with:

```bash
hermes assistant-engineer status
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

macOS's optional Swift helpers need Xcode command line tools. Windows ships stdlib `screenshot`, `midi-ports`, and explicit-port `midi-send` CLI commands; the MIDI loopback driver is a separate prerequisite. See [Windows setup](windows.md).

## Use

```bash
hermes chat -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer
```

To keep Hermes working until the track is finished, use a goal with the completeness gate:

```
/goal Make a progressive house track with a long build, a big breakdown and a drop, laid out in the Arrangement
/goal gate add hermes assistant-engineer review --require arrangement,locators,automation,sidechain,mix
```

## Models

The plugin works with any tool-calling model Hermes runs, and it works best with frontier intelligence: composing, arranging, and mixing a whole track takes dozens of tool calls and real musical judgement, which frontier models handle in one to three minutes. Locally hosted models are still useful, especially for routine work inside a DAW: cleanup, labeling and coloring tracks and clips, gain staging, and fixing production errors and artifacts (stray or overlapping notes, clipping levels, disabled or misrouted devices, sends left open). Those tasks take a handful of tool calls, so a model running on your own machine handles them well and keeps your session fully local.

Locally hosted models are for iterative requests, not whole-song building. When Hermes runs a local model (a local runtime such as Ollama, llama.cpp, LM Studio, or vLLM, or any endpoint on this machine), the plugin tells it to take on scoped requests in the existing Set and to decline from-scratch song builds, pointing the producer to a frontier model (`/model`). The measured-run harness likewise refuses full-song runs on a local model unless `--allow-local` is passed.

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

- macOS: an owner-only Unix socket. Windows: an exclusive ephemeral `127.0.0.1` listener with HMAC-authenticated requests/responses and a protected current-user endpoint file under `%LOCALAPPDATA%/ActualAssistantEngineer`. No remote listener; no fixed port.
- Files: a verified control-surface copy in Live's User Library (previous versions are backed up), Set files it creates/saves, and profile-scoped setup metadata/parameter cache under Hermes plugin data.
- Subprocesses: macOS uses `osascript`/`open`; Windows uses process-scoped UI Automation invocation, explicit Live executable launch, and (if needed) isolated x64 workers. The offline VST3 host (`pedalboard`) loads installed plug-ins. No synthesized global keystrokes.
- Windows uses local loopback networking and a generated local transport key (not an account credential). If `uv` must provision an isolated VST/UIA worker, its first use downloads Python/packages. Subsequent control is local.

## License

Apache License 2.0 (see `LICENSE`). Copyright 2026 Actual Computer.
