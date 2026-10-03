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
    // The parent Hermes: its configured model and provider are used unless --provider/--model override them.
    home: { type: "string", default: process.env.HERMES_HOME ?? `${homedir()}/.hermes` },
    discard: { type: "boolean", default: false },
    budget: { type: "string", default: "1800" },
    provider: { type: "string" },
    improve: { type: "string", default: "0" },
    model: { type: "string" },
  },
});

const runDir = `${homedir()}/Documents/Ableton Live Projects/Hermes Demos/runs/${args.label!.replace(/[^\w .-]+/g, "")}`;
mkdirSync(runDir, { recursive: true });
const template = "/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als";
const scratch = `${runDir}/start.als`;
await Bun.write(scratch, Bun.file(template));
const { live: opener } = await openSet(scratch, args.discard ? "dont-save" : "cancel");
const baseline = new Set((await opener.run<{ ok: boolean; tracks: { name: string }[] }>({ op: "overview", clips: false })).tracks.map((t) => t.name));
opener.close();

// Run Hermes in a goal loop: build, gate on live_review, resume with the gaps, and survive
// crashes and stalls (a silent process is killed and the session resumed) within a round cap.
const require = { arrangement: true, locators: true, automation: true, sidechain: true, mix: true, min_sections: 5 };
const transcript = `${runDir}/transcript.jsonl`;
const stderrLog = `${runDir}/hermes.stderr.log`;
const agentLog = `${args.home}/logs/agent.log`;
const logStart = Bun.file(agentLog).size;
const STALL_MS = 240_000;
const MAX_ROUNDS = 6;
const rounds: { round: number; reason: string; exit: number | null; stalled: boolean; seconds: number; gaps: string[] }[] = [];

async function hermes(query: string, resume?: string) {
  const cmd = ["hermes", "chat", "--oneshot", "-q", query, "-s", "actual-assistant-engineer:assistant-engineer", "-t", "actual_assistant_engineer,delegation",
    "--reasoning", "low", "--format", "stream-json", "--max-turns", "150", "--run-budget", args.budget!];
  if (resume) cmd.push("--resume", resume);
  if (args.provider) cmd.push("--provider", args.provider);
  if (args.model) cmd.push("-m", args.model);
  const out = Bun.file(transcript).writer();
  const err = Bun.file(stderrLog).writer();
  const proc = Bun.spawn(cmd, { env: { ...process.env, HERMES_HOME: args.home }, stdout: "pipe", stderr: "pipe" });
  let last = Date.now();
  const pump = async (stream: ReadableStream<Uint8Array>, sink: { write(chunk: Uint8Array): unknown }) => {
    for await (const chunk of stream) (sink.write(chunk), (last = Date.now()));
  };
  const watchdog = setInterval(() => {
    // Hermes' own log grows during long model calls even when the transcript is quiet.
    if (Bun.file(agentLog).size !== logSize) (logSize = Bun.file(agentLog).size, (last = Date.now()));
    if (Date.now() - last > STALL_MS) (stalled = true, proc.kill());
  }, 5_000);
  let logSize = Bun.file(agentLog).size, stalled = false;
  await Promise.all([pump(proc.stdout, out), pump(proc.stderr, err), proc.exited]);
  clearInterval(watchdog);
  await out.end();
  await err.end();
  return { exit: proc.exitCode, stalled };
}

const t0 = Date.now();
let session: string | undefined;
let query = args.brief!;
for (let round = 1; round <= MAX_ROUNDS; round++) {
  const r0 = Date.now();
  const { exit, stalled } = await hermes(query, session);
  session ??= (await Bun.file(transcript).text()).split("\n").map((l) => { try { return JSON.parse(l); } catch { return {}; } }).find((e) => e.type === "system")?.session_id;
  const gate = new LiveClient();
  let gaps: string[];
  try {
    await gate.connect();
    gaps = (await gate.run<{ ok: boolean; gaps: string[] }>({ op: "review", require }, 60_000)).gaps;
  } catch (e) {
    gaps = [`could not review the Set: ${(e as Error).message}`];
  } finally {
    gate.close();
  }
  const reason = round === 1 ? "brief" : rounds.at(-1)!.stalled || rounds.at(-1)!.exit ? "recover" : "gaps";
  rounds.push({ round, reason, exit, stalled, seconds: Math.round((Date.now() - r0) / 1000), gaps });
  console.error(`round ${round}: exit ${exit}${stalled ? " (stalled, killed)" : ""}, ${gaps.length} gaps`);
  if (!gaps.length || !session) break;
  const interrupted = stalled || exit !== 0;
  query = `${interrupted ? "You were interrupted mid-build. First call live_review and live_inspect to see what already exists, then continue from there instead of rebuilding.\n" : ""}live_review still reports these gaps against the brief:\n- ${gaps.join("\n- ")}\nFix every gap, call live_review again, and only report when it returns complete.`;
}
// QA rounds: analysis -> the model's own improvements -> gate again. Complexity is recorded per pass.
const passes: { pass: number; seconds: number; complexity: Awaited<ReturnType<typeof measure>>; energy: number[]; repeats: number; clashes: number; gaps: string[] }[] = [];
const snapshot = async (pass: number, seconds: number) => {
  const l = await new LiveClient().connect();
  const complexity = await measure(l, baseline);
  const a = await l.run<{ ok: boolean; energy_curve: number[]; unchanged_repeats: number; register_clashes: unknown[] }>({ op: "analyze", ignore_tracks: [...baseline] }, 60_000);
  const gaps = (await l.run<{ ok: boolean; gaps: string[] }>({ op: "review", require }, 60_000)).gaps;
  l.close();
  passes.push({ pass, seconds, complexity, energy: a.energy_curve, repeats: a.unchanged_repeats, clashes: a.register_clashes.length, gaps });
  console.error(`pass ${pass}: ${complexity.tracks} tracks, ${complexity.notes} notes, ${complexity.automationEnvelopes} automation, energy ${a.energy_curve.join("/")}, ${a.unchanged_repeats} repeats, ${a.register_clashes.length} clashes, ${gaps.length} gaps`);
  return a;
};
let analysis = await snapshot(0, Math.round((Date.now() - t0) / 1000));
for (let pass = 1; pass <= Number(args.improve) && session; pass++) {
  const p0 = Date.now();
  await hermes(`QA pass ${pass}. Here is live_analyze for the current arrangement:\n${JSON.stringify(analysis)}\nAs the producer's QA engineer, pick the 3-5 changes with the biggest musical impact for the brief (energy arc into the drop and out of the breakdown, variation where sections repeat the same clips, register clashes, movement via automation, transitions, mix balance). Make them, then confirm with live_analyze and live_review (it must stay complete), and report what you changed and why.`, session);
  analysis = await snapshot(pass, Math.round((Date.now() - p0) / 1000));
}
const wallSeconds = (Date.now() - t0) / 1000;

// Agent metrics: everything Hermes logged during this run (main session and its subagents).
const events = (await Bun.file(transcript).text()).trim().split("\n").filter(Boolean).flatMap((l) => { try { return [JSON.parse(l)]; } catch { return []; } });
const report = events.filter((e) => e.type === "result").at(-1)?.text ?? "(no final report)";
const toolCalls: Record<string, number> = {};
for (const e of events) if (e.type === "tool_use") toolCalls[e.name] = (toolCalls[e.name] ?? 0) + 1;
const log = (await Bun.file(agentLog).text()).slice(logStart).split("\n");
const sum = (re: RegExp) => log.reduce((a, l) => a + Number(re.exec(l)?.[1] ?? 0), 0);
const agent = {
  model: events.find((e) => e.type === "system")?.model,
  session,
  rounds,
  modelCalls: log.filter((l) => l.includes("API call #")).length,
  modelSeconds: Math.round(sum(/latency=([\d.]+)s/)),
  inputTokens: sum(/ in=(\d+)/),
  outputTokens: sum(/ out=(\d+)/),
  toolCalls,
  toolSeconds: Math.round(sum(/tool \w+ completed \(([\d.]+)s/) * 10) / 10,
  subagents: new Set(log.map((l) => /\[(\d{8}_\d{6}_\w+)\]/.exec(l)?.[1]).filter((id) => id && id !== session)).size,
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

const result = { label: args.label, brief: args.brief, wallSeconds: Math.round(wallSeconds), agent, complexity, passes, saved, screenshot: shot, report };
await Bun.write(`${runDir}/run.json`, JSON.stringify(result, null, 2));
const c = complexity;
const md = `# ${args.label}

**Wall time ${Math.floor(wallSeconds / 60)}m ${Math.round(wallSeconds % 60)}s** in ${rounds.length} round(s) (${rounds.map((r) => `${r.reason} ${r.seconds}s${r.stalled ? " stalled" : ""} → ${r.gaps.length} gaps`).join("; ")}) · ${agent.model} · ${agent.modelCalls} model calls (${agent.modelSeconds}s, ${agent.outputTokens.toLocaleString()} output tokens) · ${Object.values(toolCalls).reduce((a, b) => a + b, 0)} tool calls (${agent.toolSeconds}s in Live) · ${agent.subagents} subagents

| Complexity | |
|---|---|
| Tempo | ${c.tempo} BPM |
| Tracks / instruments / effects (plug-ins) | ${c.tracks} / ${c.instruments} / ${c.effects} (${c.plugins}) |
| Clips: session / arrangement | ${c.sessionClips} / ${c.arrangementClips} |
| Notes / distinct pitches | ${c.notes} / ${c.distinctPitches} |
| Arrangement | ${c.arrangementBars} bars, ${Math.floor(c.arrangementSeconds / 60)}:${String(c.arrangementSeconds % 60).padStart(2, "0")} |
| Sections | ${c.locators.join(" → ") || "none"} |
| Automation envelopes / sidechains / active sends | ${c.automationEnvelopes} / ${c.sidechains} / ${c.activeSends} |
| Completeness gate | ${rounds.at(-1)!.gaps.length ? "**incomplete**: " + rounds.at(-1)!.gaps.join("; ") : "passed"} |

${passes.length > 1 ? `| QA pass | time | tracks | notes | automation | energy by section | unchanged repeats | register clashes | gaps |\n|---|---|---|---|---|---|---|---|---|\n${passes.map((p) => `| ${p.pass || "build"} | ${p.seconds}s | ${p.complexity.tracks} | ${p.complexity.notes} | ${p.complexity.automationEnvelopes} | ${p.energy.join(" / ")} | ${p.repeats} | ${p.clashes} | ${p.gaps.length} |`).join("\n")}\n\n` : ""}${Object.entries(c.devicesByTrack).map(([t, d]) => `- **${t}**: ${d.join(" → ")}`).join("\n")}

![Arrangement](arrangement.png)

Set: \`${saved}\`

## Hermes' report
${report}
`;
await Bun.write(`${runDir}/README.md`, md);
console.log(md);
