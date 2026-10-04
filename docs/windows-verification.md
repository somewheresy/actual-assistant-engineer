# Windows verification checkpoint

This is a qualification checkpoint, not a completed full-support certification.

## Executed successfully

Host: Windows 11 ARM64. Ableton Live 12.4.6 Trial x64 installed from the official Ableton CDN; installer Authenticode signature was valid and identified Ableton AG. The installer reported success and requested a restart; no automatic restart was performed.

- Python 3.11: `uv run --python 3.11 --with pytest python -m pytest -q test/bridge test/engine` — **176 passed, 2 skipped**.
- Native ARM64 Python 3.14: same suites — **176 passed, 2 skipped**. Skips are platform-specific, not substitutes for actual Live tests.
- Bun 1.4.2 x64: `bun test test/models.test.ts test/transport.test.ts test/devtools.test.ts` — **17 passed, 0 failed** on the final rerun. An earlier run hit the endpoint ACL subprocess timeout during heavy installer activity; the isolated failure and complete suite rerun passed. This cold/load sensitivity remains worth monitoring.
- `bun run typecheck` — exit 0 (uses Bun's own architecture for the TypeScript native compiler wrapper).
- `hermes plugins validate <plugin directory>` — passed manifest, dependency, capability-registration, declared-tools, collision, and security checks.
- Installed/enabled the development build in an isolated Hermes home. Actual `hermes assistant-engineer status --probe-vst` successfully dispatched through Hermes and reported Live discovered, control surface installed, VST host ready, bridge not yet selected. The normal user Hermes profile was not modified.
- Offline VST worker probe — pedalboard 0.9.25 running in an isolated x86_64 Python host under Prism.
- Isolated x64 UIA imports — pywinauto 0.6.9 and psutil loaded successfully.
- Actual Live executable and built-in template discovery — correct installed paths, excluding running installers.
- Actual Live launched under Prism; process-scoped UIA read its `Untitled` Set title and the embedded trial activation dialog.
- Control surface copied into the user's Live User Library and verified byte-for-byte against the source Python files.

The transport suite uses real Windows sockets/ACLs and both Python and TypeScript clients; only the Live object model is faked. It covers authentication, malformed/forged responses, partial frames, backpressure, stale/unsafe discovery, lock ownership, unknown-outcome errors, and bounded unauthenticated connection retention.

## Real Live qualification completed

After trial activation, selected the Hermes control surface and verified authenticated `info` and `overview` against Live 12.4.6.

- `bun test test/integration/live.test.ts`: **15 passed, 0 failed**, 49 assertions against actual Live. Covers transport, identity guards, tracks, MIDI clips/notes, scenes, browser/device loading, device parameters, routing, sidechain, mixing, clip automation, arrangement/locators, generic object model access, batch error semantics, and performance bindings.
- `scripts/qualify-windows-live.py`: real new/edit/save/save-as/reopen, MIDI note read-back, and arrangement-lane save/edit/reopen/read-back passed. The requested -9 dB and -15 dB envelope points round-tripped as gain values.
- `scripts/qualify-windows-vst.py`: real Dragonfly Hall Reverb 3.2.10 x64 loaded in Live, 20 offline parameters enumerated, state changed and reopened, Dry Level read back as 0.25, parameter exposure verified. Repeated successfully from native ARM64 Python 3.14 with isolated x64 UIA/VST workers under Prism.
- Actual Live revealed missing ctypes in its embedded Python. Added a tested PowerShell/.NET ACL fallback using built-in _winapi/msvcrt; authentication and per-user protections remain intact.
- Fixed and regression-tested: transient forwarding processes, native Save As filename notifications, stock-template format upgrade with pre-upgrade backup, idempotent save, same-Set endpoint rotation detection, VST editor classification, VST bundle binary resolution, and queued state flush/read-back.

Evidence on the test machine is under `spark-demo/aae-live-tests`: `Windows Full Roundtrip-verification.json`, `Windows ARM64 VST Final-verification.json`, and the associated saved Sets. The user's earlier unsaved Untitled Set was saved and preserved separately before restarting.

## Remaining qualification boundaries

- Audible output quality has not been judged by listening; the tests verify Live state/playback control rather than sound quality.
- Arbitrary commercial/vendor plug-ins and preset formats are not universally certified; Dragonfly is the representative tested VST3.
- macOS regression execution on actual macOS is still needed.
- Optional Windows MIDI/screenshot helpers are a separate qualification step.
- No changes have been pushed upstream. Catalog publication still requires a reviewed new commit pin; the original macOS pin must not advertise Windows.

The test effect was installed under the user's local Programs/Common/VST3 directory. Live's VST3 system scan was enabled and its previously unset custom folder pointed to that per-user directory for this test; no existing custom-folder setting was overwritten.
