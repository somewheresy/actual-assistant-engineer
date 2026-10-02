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
