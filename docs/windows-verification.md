# Windows verification checkpoint

This is a qualification checkpoint, not a completed full-support certification.

## Executed successfully

Host: Windows 11 ARM64. Ableton Live 12.4.6 Trial x64 installed from the official Ableton CDN; installer Authenticode signature was valid and identified Ableton AG. The installer reported success and requested a restart; no automatic restart was performed.

- Python 3.11: `uv run --python 3.11 --with pytest python -m pytest -q test/bridge test/engine` — **162 passed, 2 skipped**.
- Native ARM64 Python 3.14: same suites — **162 passed, 2 skipped**. Skips are platform-specific, not substitutes for actual Live tests.
- Bun 1.4.2 x64: `bun test test/models.test.ts test/transport.test.ts test/devtools.test.ts` — **17 passed, 0 failed** on the final rerun. An earlier run hit the endpoint ACL subprocess timeout during heavy installer activity; the isolated failure and complete suite rerun passed. This cold/load sensitivity remains worth monitoring.
- `bun run typecheck` — exit 0 (uses Bun's own architecture for the TypeScript native compiler wrapper).
- `hermes plugins validate <plugin directory>` — passed manifest, dependency, capability-registration, declared-tools, collision, and security checks.
- Offline VST worker probe — pedalboard 0.9.25 running in an isolated x86_64 Python host under Prism.
- Isolated x64 UIA imports — pywinauto 0.6.9 and psutil loaded successfully.
- Actual Live executable and built-in template discovery — correct installed paths, excluding running installers.
- Actual Live launched under Prism; process-scoped UIA read its `Untitled` Set title and the embedded trial activation dialog.
- Control surface copied into the user's Live User Library and verified byte-for-byte against the source Python files.

The transport suite uses real Windows sockets/ACLs and both Python and TypeScript clients; only the Live object model is faked. It covers authentication, malformed/forged responses, partial frames, backpressure, stale/unsafe discovery, lock ownership, unknown-outcome errors, and bounded unauthenticated connection retention.

## Not yet verified

The trial is awaiting the user's Ableton account activation. The following are not claimed passed:

- Selecting the Hermes control surface inside activated Live and receiving a real `info` response.
- Real Live track/clip creation and edits, browser/device loading, mixer read-back and audible playback.
- New/open/save/save-as, explicit save/discard/cancel prompts and same-Set reopen through actual Windows controls.
- Real arrangement automation save/edit/reopen round trip and third-party VST parameter/state round trip.
- macOS regression execution on actual macOS.
- Optional Windows replacements for the macOS-only Swift MIDI/screenshot helpers.
- Catalog publication/installation of a reviewed new pin. The old pinned macOS build must not advertise Windows.

No changes have been pushed upstream. Do not distribute this checkpoint as fully qualified Windows support until the real-Live checks above are complete.
