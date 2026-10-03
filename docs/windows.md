# Windows port: installation and qualification

This branch adds Windows code paths; it is not yet a claim of completed Ableton GUI qualification. See the verification table below. macOS retains its Unix socket transport.

## Requirements

- Ableton Live 12.4+ for Windows, licensed or trial-activated. Any edition.
- Hermes 0.21+ on Windows. Native ARM64 Hermes can communicate with x64 Live under Prism; IPC does not require matching architectures.
- `uv` on PATH for isolated offline VST hosting and for x64 UI Automation dependencies when native wheels are unavailable. First use can download Python and packages; warm/provision before an offline demo.
- English Live accessibility labels for automated Set/menu operations. Missing, ambiguous, localized, or inaccessible controls fail closed, without keyboard fallback.
- Hermes and Live running as the **same Windows user**, preferably both non-elevated. Do not run Live as Administrator to work around a permissions problem.

Ableton's current [system requirements](https://help.ableton.com/hc/en-us/articles/115001663530-Live-Minimum-System-Requirements) list Windows ARM via Prism. This is not a claim that all audio drivers or third-party VSTs support every ARM machine.

## Installation

Install the Windows branch/build of the plugin through Hermes. The catalog's old `f0e68b0` pin is macOS-only and must not be advertised as Windows-compatible. Do not change the catalog platform list without pinning a reviewed commit containing this port.

Then run in PowerShell:

```powershell
hermes assistant-engineer setup
hermes assistant-engineer status --probe-vst
```

Setup resolves Live's User Library from preferences (or redirected Windows Documents), copies the bundled `Hermes` surface, verifies the copy, and keeps unique backups when replacing a previous installation. Re-run setup after plugin updates. It does not need Windows symlink privileges.

Open Live and select **Settings → Tempo & MIDI → Control Surface → Hermes** in an empty slot, then rerun status. Restart Live after replacing the script; module caching can otherwise leave the old bridge running.

For custom locations, set session-local environment overrides before setup (PowerShell):

```powershell
$env:AAE_LIVE_PATH = 'D:\Audio\Live 12 Suite\Program\Ableton Live 12 Suite.exe'
$env:AAE_USER_LIBRARY = 'D:\Music\User Library'
hermes assistant-engineer setup
```

Alternatively `setup --remote-scripts 'D:\Music\User Library\Remote Scripts'` records the destination for later status checks. `AAE_REMOTE_SCRIPTS` takes precedence. These are optional location overrides, not credentials. Remove a session override with `Remove-Item Env:AAE_LIVE_PATH`.

## Transport and security

Windows uses an **exclusive ephemeral IPv4 loopback port**, not a fixed public listener. Live writes `%LOCALAPPDATA%\ActualAssistantEngineer\live.endpoint.json` with a fresh random key. The directory and files are protected with a current-user-only Windows DACL. Both Python and TypeScript clients validate local discovery and authenticate requests and responses with HMAC-SHA256. The token is not sent in requests. Requests, subscriptions, and reloads require authentication.

The endpoint file is sensitive: do not paste it into chat, attach it to bug reports, or share its contents. No firewall opening is required. Local same-user code is within the trust boundary; this is not isolation from arbitrary code already running as the producer.

A lifetime OS lock prevents another Live instance from stealing the endpoint. Close extra Live instances. A timeout or broken connection after sending a mutation means **outcome unknown**: inspect before retrying; mutations are not automatically replayed.

## Set safety

- Set automation verifies Live's executable/process and window identity.
- Windows UIA uses menu/button patterns, not synthesized global shortcuts or foreground mouse clicks.
- Unsaved work defaults to cancel. Save/discard requires an explicit policy; save needs an associated on-disk file.
- Same-name Sets that cannot be distinguished safely are rejected instead of silently associating the wrong file.
- Unsupported UIA controls, modal dialogs, and permission failures produce actionable errors rather than pretending there is no dialog.

## VSTs and ARM64

VST3 discovery includes system Common Files and per-user Programs/Common locations. `AAE_VST3_PATHS` and `AAE_PRESET_PATHS` accept semicolon-separated extra directories on Windows.

Offline hosting inspects the PE architecture of a `.vst3` binary/bundle and selects a matching Python/pedalboard host. An ARM64 Hermes process can therefore use an isolated x64 host for an x64 VST. `status --probe-vst` tests host startup; it does **not** prove an individual commercial VST is licensed or compatible.

## Verification status

- Python regression, Windows ACL, real loopback transport, setup/discovery fixtures, Set safety, and worker dispatch tests: exercised on this Windows host.
- UIA dependency import under x64 Python/Prism: exercised.
- Real Ableton bridge selection, create/open/save/dialog controls, arrangement round trip, third-party VST load/state, and audible playback: **pending live qualification**.
- macOS runtime regression: platform-mocked tests only on this host; actual macOS execution still required.
- Swift MIDI-performance and screenshot helpers remain macOS-only. Their absence does not disable the eight core tools. Windows performance input can use the authenticated socket and Live's existing MIDI input path; no bundled Windows virtual-MIDI driver is installed.
- Audio export/analysis were not implemented upstream and are not added by this OS port.

Do not label these pending checks passed because the unit suite or manifest validator passed.
