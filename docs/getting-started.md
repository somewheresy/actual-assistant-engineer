# Getting started

This guide takes you from nothing to Hermes building a finished track in Ableton Live, then shows how to keep improving it. Plan on about ten minutes, most of it the one-time setup.

## 1. What you need

- A Mac with **Ableton Live 12.4 or later** (any edition: Intro, Standard, Suite).
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.21 or later**, set up with a model that supports tool calling. Check with `hermes --version`.
- Optional: Xcode command line tools (`xcode-select --install`), for the MIDI performance port and screenshots.

## 2. Install the plugin

```bash
hermes plugins install actual-assistant-engineer
```

Hermes asks to install one Python dependency (`pedalboard`, used to read VST3 plug-ins offline). Accept it. Until the plugin is in the catalog, install from GitHub instead:

```bash
hermes plugins install https://github.com/actual-computer/actual-assistant-engineer#plugin/actual-assistant-engineer
```

## 3. Connect Live

```bash
hermes aae setup
```

This puts the **Hermes** control surface into Live's User Library (`~/Music/Ableton/User Library/Remote Scripts/Hermes`) and builds the optional helpers if Xcode tools are installed.

Then, once:

1. Quit and reopen Live, so it sees the new control surface.
2. Open **Settings → Tempo & MIDI**.
3. In an empty **Control Surface** slot, choose **Hermes**. Leave Input and Output as they are.

Check the connection:

```bash
hermes aae status
```

You want `"bridge": true` and your Live version. If the bridge is `false`, see [Troubleshooting](#troubleshooting).

## 4. Tune Hermes (recommended)

Add this to `~/.hermes/config.yaml`. It lets the model call the Live tools directly instead of searching for them first, and lets it write parts in parallel:

```yaml
tools:
  tool_search:
    enabled: "off"
delegation:
  max_concurrent_children: 4
  oneshot_max_children: 4
```

Which model? Frontier models give the best results for writing and arranging whole tracks, and a fast one (100+ tokens a second) builds a full track in one to three minutes. A locally hosted model works well for routine DAW work, such as cleanup, labeling, gain staging, and fixing production errors and artifacts, and keeps everything on your machine.

## 5. Make your first track

Open Live with a new Set, then start Hermes with the assistant-engineer skill and the Live tools:

```bash
hermes chat -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer,delegation
```

Ask for what you want, the way you'd brief an engineer:

> Make a progressive house track around 124 BPM with a long build, a big emotional breakdown, and a drop. Pick the sounds, write the parts, add filter sweeps and sidechain from the kick, mix it, and lay it out in the Arrangement with named sections.

Watch Live while it works: tracks and devices appear, clips fill in, scenes and locators get named. When it's done it tells you what it built. Press play in the Arrangement to hear it.

Short briefs work too ("make a future beat"); you'll get something smaller. Name a length or sections when you want a full song.

## 6. Make sure it finishes

For bigger requests, use a Hermes goal with the completeness gate, so Hermes can't stop until the track really is complete:

```
/goal Make a progressive house track with a long build, a big breakdown and a drop, laid out in the Arrangement
/goal gate add hermes aae review --require arrangement,locators,automation,sidechain,mix
```

`hermes aae review` lists concrete gaps (a track missing from the Arrangement, unnamed sections, no automation, nothing balanced) and Hermes keeps working through them. Drop requirements you don't care about from `--require`.

## 7. Keep improving it

Stay in the same session and keep asking:

- *"Split the kick onto its own track and sidechain everything from it."*
- *"The breakdown feels empty. Add a pad swell and a filter sweep into the second build."*
- *"Run a QA pass: analyze the arrangement and fix the three biggest issues."*
- *"Expose the synth's filter cutoff and automate it into the drop."*
- *"Save this as 'Night Drive v2'."*

To pick up later, resume the session: `hermes chat --resume <session id> -s actual-assistant-engineer:assistant-engineer -t actual_assistant_engineer,delegation` (`hermes sessions list` shows ids).

Every change Hermes makes in one step is a single undo in Live, so **Cmd+Z** walks back through its edits.

## 8. Saving and Sets

Hermes saves through Live's own File menu. Ask it to *"start a new Set called …"*, *"save"*, or *"save as …"*. New Sets go to `~/Documents/Ableton Live Projects/Hermes/` unless you name another folder. If the open Set has unsaved changes, Hermes won't throw them away: it saves them or asks you first.

## 9. Plug-ins

Hermes can work with any VST3 you have installed:

- It reads every parameter of a plug-in by name, and can **expose** the ones it needs (up to 128) so it can set and automate them like Live's own devices.
- It finds **preset files** on disk, switches **programs** the plug-in shows Live, and loads `.vstpreset` files or exact parameter values as the plug-in's state.
- Presets in a vendor's own format, and anything only reachable in the plug-in's window, need Hermes **computer use**. Turn it on with `hermes computer-use permissions grant` and approve the prompts in System Settings.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `hermes aae status` shows `"control_surface_installed": false` | Run `hermes aae setup`. |
| `"bridge": false` | Quit and reopen Live, then select **Hermes** under Settings → Tempo & MIDI → Control Surface. |
| Hermes says Live isn't connected | Same as above; the Hermes control surface must be selected in the Set you have open. |
| `"vst_host": false` | Reinstall with dependencies: `hermes plugins install actual-assistant-engineer --yes-deps`. |
| A save or new-Set request asks about unsaved changes | Tell Hermes to save them, or to discard them if that's what you want. |
| Hermes calls `tool_search` before every Live tool | Add the `tools.tool_search.enabled: "off"` setting from step 4. |
| A big plug-in shows only "Device On" | Ask Hermes to expose the parameters you need; Live only shows configured ones. |

## Uninstall

```bash
hermes plugins remove actual-assistant-engineer
unlink "$HOME/Music/Ableton/User Library/Remote Scripts/Hermes"   # removes only the control surface link
```

Then clear the Hermes slot in Live's Control Surface settings.
