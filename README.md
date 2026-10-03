# Actual Assistant Engineer

A Hermes plugin that operates Ableton Live as an assistant engineer. It creates and saves Sets, builds any instrument or effect structure, writes and arranges MIDI, automates clips and arrangement lanes, sidechains and mixes, works inside VST3 plug-ins, lays songs out in the Arrangement with named sections, and checks its own work until the track is complete. Every musical decision comes from the model Hermes is running; the plugin supplies the hands and the checks.

macOS, Ableton Live 12.4+ (any edition). New here? Start with **[Getting started](docs/getting-started.md)**. Plan and status: [#1](https://github.com/somewheresy/actual-assistant-engineer/issues/1). Measurements: [docs/spike-results.md](docs/spike-results.md).

## Install

Everything ships in one plugin folder, [`plugin/actual-assistant-engineer`](plugin/actual-assistant-engineer): the Hermes plugin, the Live control surface, native helper sources, and the `hermes assistant-engineer` CLI.

```bash
hermes plugins install actual-assistant-engineer     # from the catalog; accept the pedalboard dependency
# or: hermes plugins install https://github.com/somewheresy/actual-assistant-engineer#plugin/actual-assistant-engineer
hermes assistant-engineer setup                                    # installs the Live control surface; builds helpers if swiftc exists
                                                    # add --index-plugins to cache every VST3's parameters up front
```

Then, once, in Live: **Settings → Tempo & MIDI → Control Surface → Hermes**. `hermes assistant-engineer status` checks Live, the control surface, the bridge, the helpers, and the VST host.

Recommended Hermes config (`$HERMES_HOME/config.yaml`, default `~/.hermes`), so the model calls the tools directly and can write parts in parallel:

```yaml
tools:
  tool_search:
    enabled: "off"
delegation:
  max_concurrent_children: 4
  oneshot_max_children: 4
```

Requirements: macOS; Ableton Live 12.4+; Hermes Agent 0.21+ with a tool-calling model; Accessibility permission for the app running Hermes (used only to click Live's own File menu items and dialog buttons). Xcode command line tools are optional (helpers for the MIDI performance port and window screenshots).

## Use

```bash
hermes chat -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer,delegation
```

Ask in plain language: *"make a future beat"*, *"split the kick onto its own track and sidechain everything from it"*, *"expose the synth's filter cutoff and automate it into the drop"*, *"run a QA pass and fix the three biggest issues"*. Hermes uses whatever model its config selects.

To keep it working until the track is finished, use a Hermes goal with the completeness gate:

```
/goal Make a progressive house track with a long build, a big breakdown and a drop, laid out in the Arrangement
/goal gate add hermes assistant-engineer review --require arrangement,locators,automation,sidechain,mix
```

`hermes assistant-engineer review` exits non-zero until the Set has no gaps (silent tracks, empty clips, parts missing from the arrangement, unnamed sections, missing automation/sidechain/mix), so the goal can't complete early. To continue an earlier session: `hermes chat --resume <session id> -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer,delegation`.

## Models

The plugin works with any tool-calling model Hermes runs, and it works best with frontier intelligence: composing, arranging, and mixing a whole track takes dozens of tool calls and real musical judgement, which frontier models handle in one to three minutes. Locally hosted models are still useful, especially for routine work inside a DAW: cleanup, labeling and coloring tracks and clips, gain staging, and fixing production errors and artifacts (stray or overlapping notes, clipping levels, disabled or misrouted devices, sends left open). Those tasks take a handful of tool calls, so a model running on your own machine handles them well and keeps your session fully local.

Locally hosted models are for iterative requests, not whole-song building. When Hermes runs a local model (a local runtime such as Ollama, llama.cpp, LM Studio, or vLLM, or any endpoint on this machine), the plugin tells it to take on scoped requests in the existing Set and to decline from-scratch song builds, pointing the producer to a frontier model (`/model`). The measured-run harness likewise refuses full-song runs on a local model unless `--allow-local` is passed.

### Tools

| Tool | What it does |
|---|---|
| `live_set` | New, open, save, save as. Never discards unsaved work unless told |
| `live_inspect` | Read the Set: tracks, devices, mixer, clips, scenes |
| `live_ops` | Batched edits, one undo step each: tracks, scenes, clips and notes (arrays or step patterns), declarative device graphs (`build_device`: any instrument/effect/rack structure, nested chains, drum pads, macros; `device_tree`, `configure` at any depth), parameters in display units (`"1.2 kHz"`, `"-6 dB"`, `"4:1"`), routing, sidechain, exact-dB mixer, clip automation, arrangement and locators, plus generic `get`/`set`/`call`/`describe` on anything in Live's object model |
| `live_browse` | Find instruments, effects, kits, presets, samples, plug-ins |
| `live_vst` | Inside VST3 plug-ins: every parameter by name, preset files on disk, programs, expose up to 128 parameters to Live, load `.vstpreset` files or parameter values as state |
| `live_arrangement_automation` | Arrangement-lane automation, which Live's API can't reach: list, read, write, delete, or `apply` many lanes in one save/reopen cycle |
| `live_review` | Completeness check: concrete gaps until the track is finished |
| `live_analyze` | Section-by-section QA: energy curve, content repeats, register clashes, levels |

## How it works

```
Hermes (any tool-calling model)
  └─ plugin/actual-assistant-engineer            8 tools, assistant-engineer skill, `hermes assistant-engineer` CLI
        │  owner-only Unix socket, newline-delimited JSON batches (no network ports)
        ▼
Ableton Live ── "Hermes" control surface (bundled; runs inside Live's Python)
        • each batch runs on Live's main thread as one undo step and returns read-back results
        • ~100 ms update tick; 200 edits ≈ 60 ms round trip; MIDI fast path ≈ 9 ms
```

What Live's scripting API can't reach goes through a translation layer between the Live object model and the Set file (`als_automation.py`, `als_plugins.py`): it locates tracks, devices, and parameters in the `.als` by their API names, infers each parameter's file units from its current value, edits the file, reopens the Set, and verifies through the API. VST3 parameter lists and state come from an offline host (`pedalboard`, a declared dependency). Nothing is configured per plug-in, vendor, or machine.

## Repository

- `plugin/actual-assistant-engineer/` — the product (what the catalog installs). `live/Hermes/` is the control surface: `ops.py` (core: tracks, scenes, clips, notes, devices, browser, transport, arrangement, locators), `ops_mix.py`, `ops_devices.py`, `ops_automation.py`, `ops_review.py`, `ops_lom.py`. `native/` holds the Swift helper sources.
- `src/` — Bun client, the `assistant-engineer` dev CLI, track measurement. `engine/als.py` — whole-Set generation from a song spec.
- `spike/` — probes and `run-song.ts`, the measured end-to-end harness.
- `test/` — bridge tests (fake Live), engine tests (Live-saved `.als`), integration suite (real Live).
- `catalog/` — the draft entry for the Hermes plugin catalog.

## Development

```bash
bun install
bun run install:hermes            # link the plugin folder into $HERMES_HOME/plugins and enable it
bun run install:live              # link the bundled control surface into Live's Remote Scripts
bun run build:native              # helpers into bin/ for the harness
bun run typecheck
bun run test:bridge               # bridge logic against an in-memory fake of Live (pytest via uv)
bun run test:engine               # Set-file translation layer against a Live-saved .als
bun run test:live                 # integration suite against the running Live ("Hermes IT" tracks only)
bun run assistant-engineer reload                # hot-reload the control surface after editing it
bun run assistant-engineer '<op json>' ...       # run ops as one batch; `bun run assistant-engineer events` streams Live events
bun run song --label "My Track" [--brief "..."] [--improve 2] [--discard]
```

`bun run song` opens a fresh Set, runs Hermes (the parent Hermes's model unless `--provider`/`--model` override it), gates on `review`, resumes with gaps and recovers from stalls and crashes, optionally runs QA passes, and saves the Set, an Arrangement screenshot, the transcript, and a metrics README to `~/Documents/Ableton Live Projects/Hermes Demos/runs/<label>/`. Without `--discard` it stops rather than drop an unsaved Set.

See [AGENTS.md](AGENTS.md) for conventions and safety rules.

## Status and limits

- Measured in the feasibility spike (#1): GLM-5.3 builds a complete, gated track in ~1–2 min; Claude Opus 5.5 in ~1.5–3.5 min with denser parts and automation.
- Live creates clip automation only on session clips; automate in Session View, then place clips into the Arrangement (copies keep their envelopes). Arrangement lanes go through `live_arrangement_automation`.
- Vendor-format presets and plug-in windows need Hermes `computer_use` (after granting its Accessibility and Screen Recording permission); `.vstpreset` files and parameter values load without the UI.
- `live_set` covers Sets created or opened through it; Live's Save panel is never driven, so saving an untitled Set made by hand is left to the producer.
- No audio export or audio analysis yet; QA is based on the arrangement and MIDI.

## License

[Apache License 2.0](LICENSE). Copyright 2026 Actual Computer.
