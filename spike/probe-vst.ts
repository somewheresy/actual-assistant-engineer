// Probe (d): load VST3s through Live's browser, discover exposed parameters,
// change one, and verify it through read-back and Live's display string.
import { LiveClient } from "../src/live/client";

type Param = { name: string; value: number; min: number; max: number; quantized: boolean };
const PLUGINS: { vendor: string; name: string; kind: "midi" | "audio" }[] = [
  { vendor: "Xfer Records", name: "Serum 2", kind: "midi" },
  { vendor: "Arturia", name: "CMI V", kind: "midi" },
  { vendor: "Kilohearts", name: "kHs Chorus", kind: "audio" },
];

const live = await new LiveClient().connect();
const keep = process.argv.includes("--keep");
for (const p of PLUGINS) {
  const trackName = `Hermes VST ${p.name}`;
  const { index: track } = await live.run<{ ok: boolean; index: number }>({ op: "create_track", kind: p.kind, name: trackName });
  const t0 = performance.now();
  await live.run({ op: "browser_load", track, expect: trackName, root: "plugins", path: ["VST3", p.vendor, p.name] }, 30_000);
  // Plug-in instantiation completes after load_item returns; poll until the device appears.
  let params: Param[] = [];
  let device = "";
  for (let i = 0; i < 100 && !params.length; i++) {
    const r = await live.batch([{ op: "device_params", track, device: 0, expect: trackName }]);
    const d = r.results[0];
    if (r.ok && d) {
      params = d.params as Param[];
      device = `${d.name} (${d.class})`;
    } else await Bun.sleep(100);
  }
  const loadMs = performance.now() - t0;
  // Pick the first continuous parameter after "Device On".
  const idx = params.findIndex((x, i) => i > 0 && !x.quantized && x.max > x.min);
  let change = "no continuous parameter exposed";
  if (idx > 0) {
    const target = params[idx]!;
    const want = target.min + (target.max - target.min) * 0.37;
    const set = await live.run<{ ok: boolean } & Param>({ op: "set_param", track, device: 0, param: idx, value: want, expect: trackName });
    const back = await live.run<{ ok: boolean; params: Param[] }>({ op: "device_params", track, device: 0, expect: trackName });
    const now = back.params[idx]!.value;
    change = `"${target.name}" ${target.value.toFixed(3)} -> ${now.toFixed(3)} (asked ${want.toFixed(3)}, ${Math.abs(now - want) < 1e-3 ? "exact" : "MISMATCH"}, set reported ${set.value.toFixed(3)})`;
  }
  console.log(`${p.name}: ${device} loaded in ${loadMs.toFixed(0)}ms, ${params.length} params exposed; ${change}`);
  console.log(`  first params: ${params.slice(0, 8).map((x) => x.name).join(" | ")}`);
  if (!keep) await live.run({ op: "delete_track", track, expect: trackName });
}
live.close();
