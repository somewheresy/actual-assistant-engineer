# Agent notes

## Layout

- `live/Hermes/` runs inside Ableton Live's embedded Python 3.11: standard library only, no threads, all Live Object Model access on Live's main thread (the bridge does this for you). Ops register with `@op("name")` in `ops.py` or an extension module listed in `live/Hermes/__init__.py` `EXTENSIONS`.
- `plugin/actual-assistant-engineer/` is the Hermes plugin (Python, runs in the Hermes process). Tool descriptions and `skills/assistant-engineer/SKILL.md` are the model's manual: keep them accurate when ops change.
- `src/` and `spike/` are Bun/TypeScript. Use `bun`, never npm. Python tooling uses `uv`.

## Working on the bridge

- After editing `live/Hermes`, run `bun run aae reload` (hot-swaps code in the running Live). Live caches modules, so reselecting the control surface alone does not reload them.
- Every op that edits an existing user track accepts `expect` (its current name); keep that guard on new ops.
- A batch is one undo step and stops at the first failing op. Ops must raise `OpError` with an actionable message (what was wrong, what is available); the model recovers from these messages.
- Live applies some changes a tick later (song position, new cue points, routing options on just-inserted devices). Defer follow-up work with `surface.schedule_message` instead of assuming same-tick effects.
- Never read properties of a Live object after deleting it.

## Tests

- `bun run test:bridge` — pure bridge logic against `test/bridge/fake_live.py`. Extend the fake alongside new ops; keep it faithful where tests depend on it.
- `bun run test:live` — the integration suite against the real Live. It must only create, edit, and delete tracks named `Hermes IT …`, and restore what it changes. Verify by reading state back, not by `ok` flags.
- Test custom logic likely to break (batch semantics, references, value parsing, arrangement placement, review/analysis); don't test Live itself.

## Safety

- Never send synthesized keystrokes. They go to whatever app has focus, not necessarily Live. Use Live's menu items and dialog buttons through accessibility (`click menu item`, button descriptions), file operations, or `open`.
- Never discard unsaved work unless the producer explicitly chose to. `live_set` stops on "Save changes?" unless told `save`/`discard`; the harness only discards with `--discard`.
- Don't modify tracks that existed before a task unless asked; untouched template tracks stay untouched.
- Screenshots capture only Live's own window (`bin/window-id` + `screencapture -l`), never a screen region.
- Keep credentials out of the repo; Hermes reads them from `$HERMES_HOME/.env`.

## Workflow

- Track work in GitHub issues; reference `#N` in commits. Branches: `<issue>/<short-description>`, one worktree per branch and per agent.
- Update `README.md` and this file when install steps, tools, ops, or workflows change.

## Generality

- Nothing in code or config may assume a particular machine, Live edition, install path, plug-in, vendor, or preset format. Discover at runtime: the running/installed Live (`live_app.py`, `src/live/app.ts`), installed VST3s, preset folders, parameter lists, device types (`DeviceType`), browser contents.
- Plug-in support is generic: parameters and state come from an offline host, preset files from standard and vendor folders, and anything vendor-specific goes through the plug-in's own UI (computer use), never through per-plug-in tables.
- Specific names belong only in examples and recorded results, not in logic or tests' expectations about this machine.
