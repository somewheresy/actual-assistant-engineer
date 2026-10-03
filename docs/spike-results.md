# Feasibility spike results (#1)

Machine: MacBook Pro (M5), macOS 27, Ableton Live 12 Suite 12.4.5 (bridge first verified on 12.4.2), 64-sample buffer at 48 kHz. Reproduce with `bun run spike/<probe>.ts` while Live runs with the Hermes control surface selected.

## (a) Control-surface bridge — `spike/probe-batch.ts`

| Measure | Result |
|---|---|
| Update tick (socket path) | median 100.0 ms, p95 110 ms |
| Ping round trip | median 100 ms (one tick) |
| Build batch: track + 8 clips + 512 notes (17 ops, ~30 KB) | exec 59 ms, round trip 158 ms |
| 200 small mutations in one batch | exec 13 ms, round trip 63 ms |
| Read back 8 clips / 512 notes | exec 1.2 ms, round trip 89 ms, all notes verified |
| `expect` identity guard | rejects a mismatched name without mutating |

Findings:
- Python control surfaces work on every Live edition; Live's embedded Python 3.11 has `socket`/`selectors`, so the bridge is stdlib-only.
- There is no faster scheduler than the ~100 ms `update_display` tick available to scripts; batching is what makes the socket path fast.
- Bun sockets don't buffer writes, and macOS Unix sockets default to ~8 KB buffers. Without a client write queue and an in-tick partial-read wait, a 30 KB batch took 3+ ticks (465 ms).
- Live caches script modules across control-surface reselection; `create_instance` reloads them, and `aae reload` hot-swaps code.
- One batch = one undo step (`begin_undo_step`/`end_undo_step`).

## (b) MIDI performance path — `spike/probe-midi.ts`

`native/hermes-midi` publishes a CoreMIDI virtual source; Live routes it to the Hermes surface's input (Track input off so notes never reach instruments).

| Measure | Result |
|---|---|
| Note → mute toggle, MIDI send to Live listener event | median 8.8 ms, p95 9.9 ms, max 19 ms (30/30) |
| 128-step CC fader sweep | final value exact (0.8500); Live coalesced listener events to 2 |
| Helper restart | Live re-binds the port by name automatically |

The performance path is ~11x faster than the socket path and never involves a model.

## (c) Whole-Set generation (`.als`) — `spike/probe-song-file.ts`

`engine/als.py` turns a song spec into a complete Live Set, starting from Live's own *Quick Start Song* template (so instrument racks, the drum kit, and returns are Live-authored XML). It rewrites tempo, scenes, clip slots, clips, notes, and clip envelopes, allocating automation ids above the template's `NextPointeeId`.

| Measure | Result |
|---|---|
| Fixture: 4 sections, 3 parts, 10 clips, 484 notes, 1 volume envelope | opened in Live 12.4.5 with no repair/upgrade prompts |
| Verification through the bridge | tempo, scene names, clip lengths, every note (pitch/start/duration/velocity), and envelope values exact |
| Playback | Chorus scene launched; Drums, Keys, Bass playing |
| Generate `.als` | 0.53 s |
| Open in Live + bridge serving the new Set | 2.6 s |

Findings:
- The template's tempo lives in two places (`Tempo/Manual` and a default automation event); the automation event wins on load.
- Mixer volume in `.als` is linear gain while the LOM uses a normalised fader value; specs use dB and verification compares Live's own display strings.
- Clip envelopes written into the file are real Live automation (readable through `Clip.automation_envelope`), so filter sweeps/macros are reachable without a LOM write API.
- Opening a Set replaces the current one. Live's "Save changes?" alert is reachable through accessibility (buttons carry descriptions), so it can be answered without moving the cursor. The probe only discards with `--discard`; the product must save or ask.
- Set-to-playable mechanics take ~3 s, so model composition time dominates the five-minute target.
- Template scenes carry their own tempo (Quick Start Song: 74 BPM on every scene), so launching a scene silently changed the song tempo. The writer now disables scene tempo unless a section sets one, and the probe re-checks tempo after a scene launch.

## (d) VST plug-ins — `spike/probe-vst.ts`

| Plug-in (VST3) | Load via browser | Parameters exposed to the LOM | Set + read back |
|---|---|---|---|
| Kilohearts kHs Chorus (effect) | 0.4 s | 7 (all) | exact |
| Xfer Serum 2 (instrument) | 0.4 s | 1 ("Device On") | needs Configure |
| Arturia CMI V (instrument) | 5.8 s | 1 ("Device On") | needs Configure |

Findings:
- Browser loading works for any installed VST3 by path (`plugins/VST3/<vendor>/<name>`). Only VST3 is enabled on this machine.
- Live auto-exposes parameters only for small plug-ins; large instruments expose nothing until parameters are configured. Configured parameters persist in the Set as `PluginFloatParameter` entries (`ParameterId` = plug-in parameter index, `VisualIndex` = slot), so once a plug-in's parameter ids are known, the `.als` writer can pre-configure up to 128 of them, or map them to rack macros.
- Plug-in preset browsers and unexposed controls need computer use. Hermes' `cua-driver` (0.21.0, bundled at `~/.hermes/tools/`) is installed but has no Accessibility/Screen Recording grant yet; that grant is a user step (`hermes computer-use permissions grant`). Its telemetry defaulted to on and has been disabled.

## (f) Agentic song building through Hermes

The plugin (`plugin/actual-assistant-engineer`) gives the session's model three tools — `live_inspect`, `live_ops` (batched ops, one undo step per call), `live_browse` — plus the assistant-engineer skill. All musical decisions are the model's; nothing composes deterministically.

Brief (identical in every run): *"Make me a progressive house track in the Live Set that's open. Around 124 BPM, with a long build, a big emotional breakdown, and a drop. Pick the sounds, write the parts, arrange it into scenes I can launch, and give me a rough mix."* Start state: Live's factory default Set.

| Run | Model | Wall time | Result |
|---|---|---|---|
| 1 | Qwen3.8-27B Q4 (local daemon), reasoning default | 34 min (budget hit) | tempo + 2 tracks loaded, no clips; honest status report |
| 2 | same, reasoning none | stopped at 15 min | still browsing (11 calls) |
| 3 | GLM-5.3 (Actual cluster relay), first tool surface | stopped at ~6 min | built tracks, then one model call spent >3.5 min writing notes; a bad `$N` ref stalled 60 s |
| 4 | GLM-5.3, improved tools (below) | **3 min 20 s** | 6 tracks with presets + effect chains, 8 named scenes (INTRO → GROOVE → BUILD 1 → BUILD 2 → BREAKDOWN → BUILD 3 → DROP → OUTRO), 32 clips, rough mix; auditioned the drop; report verified against the Set |

Profile of run 3: 13 model calls = 105 s, 18k output tokens; all Live tool calls together = 2.4 s. Live is never the bottleneck — model turns and output tokens are. Changes that produced run 4:
- Model: GLM-5.3 on the Actual cluster (~190 tok/s; 4 concurrent requests take the same time as 1) vs local Qwen 27B (~10-20 tok/s, one request at a time).
- Bridge answers every request (a bad reference used to raise outside the per-op handler and leave the client waiting 60 s).
- `add_notes` step patterns (`{"36": "x...x...x...x..."}`) for rhythmic parts; compact `[pitch, start, duration, velocity]` arrays otherwise.
- `live_browse` runs several searches per call and matches every word across the item's path (run 3 used 10 sequential browse turns, several empty).
- Hermes config: `tool_search.enabled: off` (plugin tools were hidden behind tool_search/tool_describe), `--reasoning low`.
- Delegation to 4 parallel subagents is enabled (`delegation.max_concurrent_children: 4`, `oneshot_max_children: 4`); run 4 hit the one-shot default cap of 2 and wrote the parts itself, so parallel part-writing is still untested.

Remaining speed work: parallel subagents per part, and smaller read-backs (one tool result added ~12k tokens of context late in run 4).
