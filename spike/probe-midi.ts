// Probe (b): MIDI fast path latency. Sends channel-16 MIDI through the hermes-midi
// virtual source and times how long until Live reports the resulting change.
import { LiveClient, type LiveEvent } from "../src/live/client";

const NAME = "Hermes Probe";
const midi = Bun.spawn(["./bin/hermes-midi", "Hermes Performance"], { stdin: "pipe", stdout: "pipe" });
const lines = (async function* () {
  const reader = midi.stdout.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n")) >= 0) {
      yield buf.slice(0, i);
      buf = buf.slice(i + 1);
    }
  }
})();
const next = async () => (await lines.next()).value as string;
console.log(await next()); // "ready ..."
await Bun.sleep(1500); // let Live rebind the reappearing port

const send = async (hex: string) => {
  midi.stdin.write(hex + "\n");
  midi.stdin.flush();
  return Number((await next()).split(" ")[1]);
};

const live = await new LiveClient().connect();
const events = await new LiveClient().connect();
const waiters: ((e: LiveEvent) => void)[] = [];
events.onEvent = (e) => waiters.splice(0).forEach((w) => w(e));
await events.subscribe();
const nextEvent = (pred: (e: LiveEvent) => boolean, ms = 2000) =>
  new Promise<LiveEvent | undefined>((resolve) => {
    const t = setTimeout(() => resolve(undefined), ms);
    const w = (e: LiveEvent) => (pred(e) ? (clearTimeout(t), resolve(e)) : waiters.push(w));
    waiters.push(w);
  });

const { index: track } = await live.run<{ ok: boolean; index: number }>({ op: "create_track", kind: "midi", name: NAME });
await live.run({ op: "perf_bind", bindings: { note: { 60: { action: "mute", track, expect: NAME } }, cc: { 7: { action: "volume", track, expect: NAME, min: 0, max: 0.85 } } } });

// Discrete gesture: note -> mute toggle. Latency = Live emit time - MIDI send time.
const lat: number[] = [];
for (let i = 0; i < 30; i++) {
  const waiting = nextEvent((e) => e.kind === "track" && e.prop === "mute" && e.track === track);
  const sent = await send("9f 3c 7f");
  const e = await waiting;
  if (e) lat.push((e.t - sent) * 1000);
  await Bun.sleep(30);
}
lat.sort((a, b) => a - b);
console.log(`note->mute x${lat.length}/30: median ${lat[lat.length >> 1]?.toFixed(2)}ms p95 ${lat[Math.floor(lat.length * 0.95) - 1]?.toFixed(2)}ms max ${lat.at(-1)?.toFixed(2)}ms`);

// Continuous gesture: 128-step fader sweep as fast as possible; final value must land.
let volEvents = 0;
events.onEvent = (e) => {
  if (e.kind === "track" && e.prop === "volume" && e.track === track) volEvents++;
  waiters.splice(0).forEach((w) => w(e));
};
const t0 = performance.now();
for (let v = 0; v < 128; v++) {
  midi.stdin.write(`bf 07 ${v.toString(16).padStart(2, "0")}\n`);
  await next();
}
await Bun.sleep(300);
const { tracks } = await live.run<{ ok: boolean; tracks: { index: number; volume: number }[] }>({ op: "overview", clips: false });
const vol = tracks.find((t) => t.index === track)?.volume;
console.log(`cc sweep: 128 msgs in ${(performance.now() - t0).toFixed(0)}ms, ${volEvents} volume events, final ${vol?.toFixed(4)} (expect 0.8500)`);

await live.run({ op: "perf_bind", bindings: {} });
await live.run({ op: "delete_track", track, expect: NAME });
live.close();
events.close();
midi.kill();
