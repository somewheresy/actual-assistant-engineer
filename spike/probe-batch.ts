// Probe (a): round-trip latency and batch throughput through the Hermes control surface.
import { LiveClient, type Op } from "../src/live/client";

const live = await new LiveClient().connect();
const stats = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const q = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))]!.toFixed(1);
  return `median ${q(0.5)}ms p95 ${q(0.95)}ms max ${s[s.length - 1]!.toFixed(1)}ms`;
};
const timed = async (ops: Op[]) => {
  const t0 = performance.now();
  const res = await live.batch(ops, { timeoutMs: 30_000 });
  return { res, rtt: performance.now() - t0 };
};

// 1. Ping round trips (bounded below by the ~100ms update tick).
const pings: number[] = [];
for (let i = 0; i < 30; i++) pings.push((await timed([{ op: "ping" }])).rtt);
console.log(`ping x30: ${stats(pings)}`);

// 2. One batch: create track + 8 clips x 64 notes + names/colors (~25 ops, 512 notes).
const NAME = "Hermes Probe";
const notes = (root: number) =>
  Array.from({ length: 64 }, (_, i) => ({ pitch: root + [0, 3, 7, 10][i % 4]!, start: i * 0.25, duration: 0.2, velocity: 80 + (i % 4) * 10 }));
const build: Op[] = [{ op: "create_track", kind: "midi", name: NAME, color: "#ff8c00" }];
for (let s = 0; s < 8; s++) {
  build.push({ op: "create_clip", track: "$0.index", slot: s, length: 16, name: `probe ${s}`, expect: NAME });
  build.push({ op: "add_notes", track: "$0.index", slot: s, notes: notes(48 + s), expect: NAME });
}
const b = await timed(build);
console.log(`build batch: ${build.length} ops, ok=${b.res.ok}, exec ${b.res.exec_ms}ms, recv ${(b.res as unknown as {recv_ms:number}).recv_ms}ms, rtt ${b.rtt.toFixed(1)}ms`);
if (!b.res.ok) console.log(JSON.stringify(b.res.results.at(-1)));
const track = b.res.results[0]?.index as number;

// 3. 200 small mutations in one batch.
const muts: Op[] = Array.from({ length: 200 }, (_, i) => ({ op: "set_clip", track, slot: i % 8, name: `probe ${i % 8}.${i}`, expect: NAME }));
const m = await timed(muts);
console.log(`200-op batch: ok=${m.res.ok}, exec ${m.res.exec_ms}ms, rtt ${m.rtt.toFixed(1)}ms`);

// 4. Read back and verify.
const rb = await timed(Array.from({ length: 8 }, (_, s) => ({ op: "get_notes", track, slot: s, expect: NAME })));
const counts = rb.res.results.map((r) => (r.notes as unknown[]).length);
console.log(`read-back: ${counts.join(",")} notes, exec ${rb.res.exec_ms}ms, rtt ${rb.rtt.toFixed(1)}ms, all 64: ${counts.every((c) => c === 64)}`);

// 5. Identity guard: a mismatched expect must fail without mutating.
const guard = await live.batch([{ op: "set_track", track, expect: "Not This Track", name: "WRONG" }]);
console.log(`expect guard rejected: ${!guard.ok} (${guard.results[0]?.error})`);

// 6. Cleanup.
const del = await live.batch([{ op: "delete_track", track, expect: NAME }]);
console.log(`cleanup ok=${del.ok}`);
live.close();
