// End-to-end song run: fresh Set -> Hermes builds a track from a brief -> metrics, complexity,
// saved Set, Arrangement screenshot, and a report. Every run leaves a finished, inspectable track.
// usage: bun run spike/run-song.ts --label "Prog House 3" [--brief "..."] [--discard]
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { parseArgs } from "node:util";
import { LiveClient } from "../src/live/client";
import { measure } from "../src/song/measure";
import { openSet, saveSetAs, screenshotLive } from "./open-set";

const { values: args } = parseArgs({
  options: {
    label: { type: "string", default: `Hermes Run ${new Date().toISOString().slice(0, 16)}` },
    brief: {
      type: "string",
      default:
        "Make me a progressive house track in the Live Set that's open. Around 124 BPM, with a long build, a big emotional breakdown, and a drop. Pick the sounds, write the parts, add FX and automation (filter sweeps, risers, sidechain compression from the kick), mix it, and lay it out in the Arrangement as a finished song with named sections. Tell me what you made when you're done.",
    },
    home: { type: "string", default: process.env.HERMES_HOME },
    discard: { type: "boolean", default: false },
    budget: { type: "string", default: "1800" },
  },
});
if (!args.home) throw new Error("pass --home <HERMES_HOME> (configured with the model and plugin)");

const runDir = `${homedir()}/Documents/Ableton Live Projects/Hermes Demos/runs/${args.label!.replace(/[^\w .-]+/g, "")}`;
mkdirSync(runDir, { recursive: true });
const template = "/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als";
const scratch = `${runDir}/start.als`;
await Bun.write(scratch, Bun.file(template));
const { live: opener } = await openSet(scratch, args.discard ? "dont-save" : "cancel");
const baseline = new Set((await opener.run<{ ok: boolean; tracks: { name: string }[] }>({ op: "overview", clips: false })).tracks.map((t) => t.name));
opener.close();

// Run Hermes.
const transcript = `${runDir}/transcript.jsonl`;
const t0 = Date.now();
const proc = Bun.spawn(
  ["hermes", "chat", "--oneshot", "-q", args.brief!, "-s", "actual-assistant-engineer:assistant-engineer", "-t", "actual_assistant_engineer,delegation",
    "--reasoning", "low", "--format", "stream-json", "--max-turns", "150", "--run-budget", args.budget!],
  { env: { ...process.env, HERMES_HOME: args.home }, stdout: Bun.file(transcript), stderr: "pipe" },
);
await proc.exited;
const wallSeconds = (Date.now() - t0) / 1000;

// Agent metrics from the transcript and Hermes' log (model calls carry token counts).
const events = (await Bun.file(transcript).text()).trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
const session = events.find((e) => e.type === "system")?.session_id as string;
const report = events.find((e) => e.type === "result")?.text ?? "(no final report)";
const toolCalls: Record<string, number> = {};
for (const e of events) if (e.type === "tool_use") toolCalls[e.name] = (toolCalls[e.name] ?? 0) + 1;
const log = (await Bun.file(`${args.home}/logs/agent.log`).text()).split("\n").filter((l) => l.includes(session));
const sum = (re: RegExp) => log.reduce((a, l) => a + Number(re.exec(l)?.[1] ?? 0), 0);
const agent = {
  model: events.find((e) => e.type === "system")?.model,
  session,
  modelCalls: log.filter((l) => l.includes("API call #")).length,
  modelSeconds: Math.round(sum(/latency=([\d.]+)s/)),
  inputTokens: sum(/ in=(\d+)/),
  outputTokens: sum(/ out=(\d+)/),
  toolCalls,
  toolSeconds: Math.round(sum(/tool \w+ completed \(([\d.]+)s/) * 10) / 10,
  subagents: log.filter((l) => /delegate_task completed/.test(l)).length,
};

// Complexity, read from Live itself.
const live = await new LiveClient().connect();
const complexity = await measure(live, baseline);
await live.run({ op: "show_view", view: "Arranger" });
// Zoom the timeline out so the screenshot shows the whole song (NavDirection.left = zoom out).
await live.batch(Array.from({ length: 30 }, () => ({ op: "call", path: "app.view", method: "zoom_view", args: [2, "Arranger", false] })));
live.close();
await Bun.sleep(500);
const shot = screenshotLive(`${runDir}/arrangement.png`);
const saved = await saveSetAs(runDir, args.label!);

const result = { label: args.label, brief: args.brief, wallSeconds: Math.round(wallSeconds), agent, complexity, saved, screenshot: shot, report };
await Bun.write(`${runDir}/run.json`, JSON.stringify(result, null, 2));
const c = complexity;
const md = `# ${args.label}

**Wall time ${Math.floor(wallSeconds / 60)}m ${Math.round(wallSeconds % 60)}s** · ${agent.model} · ${agent.modelCalls} model calls (${agent.modelSeconds}s, ${agent.outputTokens.toLocaleString()} output tokens) · ${Object.values(toolCalls).reduce((a, b) => a + b, 0)} tool calls (${agent.toolSeconds}s in Live) · ${agent.subagents} subagents

| Complexity | |
|---|---|
| Tempo | ${c.tempo} BPM |
| Tracks / instruments / effects (plug-ins) | ${c.tracks} / ${c.instruments} / ${c.effects} (${c.plugins}) |
| Clips: session / arrangement | ${c.sessionClips} / ${c.arrangementClips} |
| Notes / distinct pitches | ${c.notes} / ${c.distinctPitches} |
| Arrangement | ${c.arrangementBars} bars, ${Math.floor(c.arrangementSeconds / 60)}:${String(c.arrangementSeconds % 60).padStart(2, "0")} |
| Sections | ${c.locators.join(" → ") || "none"} |
| Automation envelopes / sidechains / active sends | ${c.automationEnvelopes} / ${c.sidechains} / ${c.activeSends} |

${Object.entries(c.devicesByTrack).map(([t, d]) => `- **${t}**: ${d.join(" → ")}`).join("\n")}

![Arrangement](arrangement.png)

Set: \`${saved}\`

## Hermes' report
${report}
`;
await Bun.write(`${runDir}/README.md`, md);
console.log(md);
