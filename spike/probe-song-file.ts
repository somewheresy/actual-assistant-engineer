// Probe (c) end to end: spec -> .als -> open in Live -> verify -> play. Prints stage timings.
// Pass --discard to answer "Don't Save" for the currently open Set (fixture sessions only).
import { mkdirSync } from "node:fs";
import { fixtureSong } from "./song-fixture";
import { openSet } from "./open-set";

const dir = `${process.env.TMPDIR ?? "/tmp"}/hermes-sets/${Date.now()}`;
mkdirSync(dir, { recursive: true });
const spec = `${dir}/spec.json`, out = `${dir}/Hermes Fixture ${Date.now() % 100000}.als`;

const t0 = performance.now();
await Bun.write(spec, JSON.stringify(fixtureSong));
const gen = Bun.spawnSync(["python3", "engine/als.py", spec, out]);
if (gen.exitCode !== 0) throw new Error(gen.stderr.toString());
const tGen = performance.now() - t0;

const { live, dialog, ms: tOpen } = await openSet(out, process.argv.includes("--discard") ? "dont-save" : "cancel");
live.close();

const verify = Bun.spawnSync(["bun", "run", "spike/probe-als.ts"]);
console.log(verify.stdout.toString().trim());
console.log(`generate ${tGen.toFixed(0)}ms, open+bridge ${tOpen.toFixed(0)}ms, total ${(performance.now() - t0).toFixed(0)}ms${dialog ? ` (answered: ${dialog})` : ""}`);
process.exit(verify.exitCode ?? 1);
