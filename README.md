# Actual Assistant Engineer

A Hermes plugin that operates Ableton Live as an assistant engineer: it inspects Sets, picks instruments, kits, and presets, writes and arranges MIDI, builds FX chains, automates, sidechains, mixes, lays songs out in the Arrangement with named sections, and checks its own work until the track is complete. All musical decisions come from the model Hermes is running; the plugin supplies the hands and the checks.

macOS only. Plan and status: [#1](https://github.com/actual-computer/actual-assistant-engineer/issues/1). Measurements: [docs/spike-results.md](docs/spike-results.md).

## How it works

```
Hermes (session model: GLM-5.3, Claude Opus 5.5, ...)
  └─ plugin/actual-assistant-engineer   tools: live_set, live_inspect, live_ops, live_browse, live_review,
                                        live_analyze, live_arrangement_automation
        │  Unix socket (0600, no TCP port), newline-delimited JSON batches
        ▼
Ableton Live ── "Hermes" control surface (live/Hermes, runs inside Live's Python)
        • each batch runs on Live's main thread as ONE undo step, with read-back results
        • ~100 ms update tick; 200 edits ≈ 60 ms round trip
        • CoreMIDI fast path for performance gestures (~9 ms), via native/hermes-midi
```

- `live/Hermes/` — the control surface. `ops.py` (tracks, scenes, clips, notes and step patterns, devices, browser, transport, arrangement, locators), `ops_mix.py` (insert/delete devices, params by display value like `"1.2 kHz"`/`"-6 dB"`/`"4:1"`, routing, sidechain, one-call mixer with exact dB), `ops_automation.py` (clip envelopes), `ops_review.py` (completeness review and section-by-section QA analysis), `ops_devices.py` (declarative device graphs: any instrument/effect/rack structure, nested chains, drum pads; `device_tree`, `configure` at any depth), `ops_lom.py` (generic `get`/`set`/`call`/`describe` on any Live Object Model path, e.g. `song.tracks["BASS"].devices[0]`).
- `plugin/actual-assistant-engineer/` — the native Hermes plugin and its `assistant-engineer` skill. It also holds the file translation layer (`als_automation.py`): Live's API can't reach some features (arrangement automation, macro mappings), so those round-trip through the Set file, locating tracks, devices, and parameters by their LOM names and inferring each parameter's file units from its current value.
- `src/` — Bun client (`src/live/client.ts`), the `aae` CLI, and track measurement.
- `engine/als.py` — writes complete Live Sets from a song spec (template-based `.als` generation).
- `spike/` — probes and `run-song.ts`, the end-to-end harness that builds, gates, measures, screenshots, and saves a track per run.
- `native/` — `hermes-midi` (virtual MIDI source for performance) and `window-id` (captures Live's window for screenshots).

## Requirements

- macOS, Ableton Live 12.4+ (any edition; built and tested on 12.4.5 Suite)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.21+, with a model configured (tool calling required)
- [Bun](https://bun.sh), [uv](https://docs.astral.sh/uv/) with Python 3.13, Xcode command line tools (`swiftc`)
- Accessibility permission for the app that runs Hermes (Terminal, etc.), used only to click Live's own menu items and dialog buttons for Set files

## Install

```bash
git clone git@github.com:actual-computer/actual-assistant-engineer.git
cd actual-assistant-engineer
bun install
bun run build:native            # bin/hermes-midi, bin/window-id
bun run install:live            # links live/Hermes into Live's User Library Remote Scripts
bun run install:hermes          # links the plugin into $HERMES_HOME/plugins and enables it
```

Then:

1. **Live** — restart Live, open *Settings → Tempo & MIDI*, and set a *Control Surface* slot to **Hermes**. The bridge socket appears at `~/Library/Application Support/ActualAssistantEngineer/live.sock`. Check it with `bun run aae '{"op":"info"}'`.
2. **Hermes config** (`$HERMES_HOME/config.yaml`, default `~/.hermes`) — give the model the tools directly and allow parallel subagents:
   ```yaml
   tools:
     tool_search:
       enabled: "off"
   delegation:
     max_concurrent_children: 4
     oneshot_max_children: 4
   ```
3. Verify: `hermes plugins doctor actual-assistant-engineer` should report 7 tools.

Optional performance path: run `bin/hermes-midi` (publishes the "Hermes Performance" MIDI port), then in Live set the Hermes control surface's *Input* to **Hermes Performance** and turn off that port's *Track* input.

## Use

Open a Set in Live (or let Hermes create one), then:

```bash
hermes chat -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer,delegation
```

and ask in plain language: *"Make a future beat"*, *"split the kick onto its own track and sidechain everything from it"*, *"run a QA pass and fix the three biggest issues"*. Hermes uses whatever model its config selects.

To keep it working until the track is genuinely finished, use a Hermes goal with the completeness gate:

```
/goal Make a progressive house track with a long build, a big breakdown and a drop, laid out in the Arrangement
/goal gate add cd /path/to/actual-assistant-engineer && bun run aae review --require arrangement,locators,automation,sidechain,mix
```

`aae review` exits non-zero until the Set has no gaps (silent tracks, empty clips, parts missing from the arrangement, unnamed sections, missing automation/sidechain/mix), so the goal cannot complete early.

To continue a previous run, resume its session: `hermes chat --resume <session id> -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer,delegation` (the id is in each run's `run.json`).

### Measured runs

`bun run song --label "My Track" [--brief "..."] [--improve 2] [--discard]` opens a fresh Set, runs Hermes (the parent Hermes's model unless `--provider`/`--model` are given), gates on `review`, resumes with the gaps and recovers from stalls and crashes (up to 6 rounds), optionally runs QA improvement passes, and writes to `~/Documents/Ableton Live Projects/Hermes Demos/runs/<label>/`: the saved Set, an Arrangement screenshot, the transcript, and a README with wall time, model calls, tokens, time spent in Live, and complexity (tracks, devices, clips, notes, sections, automation, sidechains, sends, energy curve). `--discard` drops whatever unsaved Set is open; without it the harness stops instead.

## Development

```bash
bun run typecheck
bun run test:bridge                 # bridge logic against an in-memory fake of Live (pytest)
bun run test:engine                 # Set-file translation layer against a Live-saved .als (pytest)
bun run test:live                   # integration suite against the running Live (uses only "Hermes IT" tracks)
bun run aae reload                  # hot-reload the control surface after editing live/Hermes
bun run aae '<op json>' ...         # run ops as one batch; `bun run aae events` streams Live events
```

See [AGENTS.md](AGENTS.md) for conventions and safety rules.

## Status and limits

- Built and measured in the feasibility spike (#1): GLM-5.3 builds a complete, gated progressive house track in ~1–2 min; Claude Opus 5.5 in ~1.5–3.5 min with richer parts and automation.
- Live creates clip automation only on session clips; automate in Session View, then place clips into the Arrangement (copies keep their envelopes). Track-lane arrangement automation goes through `live_arrangement_automation` (save, edit the file, reopen).
- Large VST instruments expose no parameters until configured in Live; built-in instruments and presets are fully controllable.
- `live_set` covers Sets created or opened through it; Live's Save panel is never driven, so saving an untitled Set made by hand is left to the producer.
- No audio export or audio analysis yet; QA is based on the arrangement and MIDI.
