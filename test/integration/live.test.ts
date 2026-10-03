// Integration suite: every bridge op family against the real Live, verified by read-back.
// Runs only when Live is up with the Hermes control surface; uses only "Hermes IT " tracks.
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { LiveClient, SOCK_PATH, type Op } from "../../src/live/client";

const P = "Hermes IT ";
const M = `${P}Midi`, A = `${P}Audio`, K = `${P}Kick`, S = `${P}Synth`;
let live: LiveClient;
let tempo = 120;
let scenesBefore = 0;

const run = async <T = Record<string, any>>(op: Op) => (await live.run(op)) as T & Record<string, any>;
const overview = () => run<{ tracks: any[]; scenes: any[]; tempo: number }>({ op: "overview" });
const track = async (name: string) => (await overview()).tracks.find((t) => t.name === name);
const cleanup = async () => {
  const ov = await overview();
  const ops = ov.tracks.filter((t) => t.name.startsWith(P)).map((t) => ({ op: "delete_track", track: t.name, expect: t.name }));
  if (ops.length) await live.batch(ops);
};

describe.skipIf(!existsSync(SOCK_PATH))("Hermes bridge against Live", () => {
  beforeAll(async () => {
    live = await new LiveClient().connect();
    await cleanup();
    const ov = await overview();
    tempo = ov.tempo;
    scenesBefore = ov.scenes.length;
    await live.batch([
      { op: "create_track", kind: "midi", name: M },
      { op: "create_track", kind: "audio", name: A },
      { op: "create_track", kind: "midi", name: K },
      { op: "create_track", kind: "midi", name: S },
      { op: "insert_device", track: K, name: "Operator" },
      { op: "insert_device", track: S, name: "Operator" },
      { op: "insert_device", track: S, name: "Auto Filter" },
      { op: "insert_device", track: S, name: "Compressor" },
    ]);
  });

  afterAll(async () => {
    await live.batch([{ op: "transport", play: false, tempo }, { op: "stop_all_clips" }]);
    await live.batch([{ op: "clear_arrangement", track: M }]);
    await cleanup();
    const ov = await overview();
    for (let i = ov.scenes.length - 1; i >= scenesBefore; i--) await run({ op: "call", path: "song", method: "delete_scene", args: [i] });
    live.close();
  });

  test("info, ping, tick", async () => {
    const info = await run({ op: "info" });
    expect(info.live_version).toMatch(/^12\./);
    expect((await run({ op: "tick_stats" })).median_ms).toBeGreaterThan(50);
  });

  test("transport sets tempo and loop", async () => {
    await run({ op: "transport", tempo: 123.5, loop: true, loop_start: 4, loop_length: 8 });
    const v = (await run({ op: "get", path: "song", props: ["tempo", "loop", "loop_start", "loop_length"] })).values;
    expect(v).toEqual({ tempo: 123.5, loop: true, loop_start: 4, loop_length: 8 });
    await run({ op: "transport", loop: false });
  });

  test("set_track edits are read back and expect guards the wrong track", async () => {
    await run({ op: "set_track", track: M, expect: M, color: "#ff0000", mute: true, pan: -0.5, volume: 0.6 });
    const t = await track(M);
    expect([t.mute, t.pan, Math.round(t.volume * 100)]).toEqual([true, -0.5, 60]);
    const bad = await live.batch([{ op: "set_track", track: M, expect: "someone else", name: "WRONG" }]);
    expect(bad.ok).toBe(false);
    expect((await track(M)).name).toBe(M);
    await run({ op: "set_track", track: M, mute: false });
  });

  test("clips: arrays, patterns with holds, read-back, loop, clear, delete", async () => {
    await live.batch([
      { op: "create_clip", track: M, slot: 0, length: 4, name: "IT clip" },
      { op: "add_notes", track: M, slot: 0, notes: [[60, 0, 1, 90]], patterns: { "36": "x...X...", "45": "x--." } },
      { op: "set_clip", track: M, slot: 0, loop_end: 2 },
    ]);
    const notes = (await run({ op: "get_notes", track: M, slot: 0 })).notes.map((n: number[]) => n.slice(0, 4)).sort();
    expect(notes).toEqual([[36, 0, 0.25, 100], [36, 1, 0.25, 120], [45, 0, 0.75, 100], [60, 0, 1, 90]].sort());
    expect((await track(M)).clips["0"].name).toBe("IT clip");
    await run({ op: "clear_notes", track: M, slot: 0 });
    expect((await run({ op: "get_notes", track: M, slot: 0 })).notes).toHaveLength(0);
    await run({ op: "delete_clip", track: M, slot: 0 });
    expect((await track(M)).clips?.["0"]).toBeUndefined();
  });

  test("scenes: create, name, fire, stop", async () => {
    const { index } = await run({ op: "create_scene", name: "IT Scene" });
    expect((await overview()).scenes[index].name).toBe("IT Scene");
    await live.batch([{ op: "create_clip", track: S, slot: index, length: 4 }, { op: "add_notes", track: S, slot: index, patterns: { "48": "x.x.x.x." } }]);
    await run({ op: "fire_scene", scene: index });
    await Bun.sleep(1500);
    expect((await track(S)).playing_slot).toBe(index);
    await run({ op: "stop_all_clips", quantized: false });
    await run({ op: "transport", play: false });
  });

  test("browser: search, load an instrument, and read a drum kit's pads", async () => {
    const found = await run({ op: "browser_search", root: "instruments", query: "operator" });
    expect(found.items.map((i: any) => i.name)).toContain("Operator");
    await run({ op: "browser_load", track: M, root: "instruments", path: ["Operator"] });
    expect((await track(M)).devices.map((d: any) => d.name)).toContain("Operator");
    const kits = await run({ op: "browser_search", root: "drums", query: "909 kit" });
    await run({ op: "browser_load", track: M, root: "drums", path: kits.items[0].path });
    await Bun.sleep(500);
    const drums = (await track(M)).devices.findIndex((d: any) => d.class === "DrumGroupDevice");
    const pads = (await run({ op: "drum_pads", track: M, device: drums })).pads;
    expect(pads.some((p: any) => p.note === 36)).toBe(true);
  });

  test("devices: params by name and display value, on/off, duplicate, delete", async () => {
    const set = await run({ op: "set_params", track: S, device: "Auto Filter", values: { Frequency: "1.2 kHz", Resonance: "40 %" } });
    expect(set.Frequency.display).toBe("1.20 kHz");
    expect(set.Resonance.display).toBe("40 %");
    await run({ op: "device_on", track: S, device: "Auto Filter", on: false });
    expect((await run({ op: "get", path: `song.tracks["${S}"].devices["Auto Filter"]`, props: ["is_active"] })).values.is_active).toBe(false);
    await run({ op: "device_on", track: S, device: "Auto Filter", on: true });
    await run({ op: "duplicate_device", track: S, device: "Compressor" });
    expect((await track(S)).devices.filter((d: any) => d.name === "Compressor")).toHaveLength(2);
    await run({ op: "delete_device", track: S, device: 3 });
    expect((await track(S)).devices.map((d: any) => d.name)).toEqual(["Operator", "Auto Filter", "Compressor"]);
  });

  test("sidechain keys the compressor from another track", async () => {
    const r = await run({ op: "sidechain", track: S, device: "Compressor", source: K });
    expect(r.routing).toBe(K);
    const v = await run({ op: "get", path: `song.tracks["${S}"].devices["Compressor"]`, props: ["input_routing_type"] });
    expect(v.values.input_routing_type.display_name).toBe(K);
  });

  test("mixer sets exact dB, pan, and sends on several tracks", async () => {
    await run({ op: "mixer", tracks: { [S]: { volume_db: -12.5, pan: 0.25, sends: { A: 0.3 } }, [K]: { volume_db: -3 } } });
    const label = async (t: string) => (await run({ op: "call", path: `song.tracks["${t}"].mixer_device.volume`, method: "str_for_value", args: [{ path: `song.tracks["${t}"].mixer_device.volume.value` }] })).result;
    expect(await label(S)).toBe("-12.5 dB");
    expect(await label(K)).toBe("-3.0 dB");
    const send = await run({ op: "get", path: `song.tracks["${S}"].mixer_device.sends[0]`, props: ["value"] });
    expect(send.values.value).toBeCloseTo(0.3, 5);
  });

  test("routing changes a track's output", async () => {
    const r = await run({ op: "set_routing", track: A, output: "Sends Only" });
    expect(r.output).toBe("Sends Only");
    await run({ op: "set_routing", track: A, output: "Main" });
  });

  test("automation: display-value sweep and stepped send throw round-trip", async () => {
    await run({ op: "create_clip", track: S, slot: 1, length: 16 });
    await run({ op: "automate", track: S, slot: 1, target: "Auto Filter:Frequency", points: [[0, "200 Hz"], [16, "8 kHz"]] });
    await run({ op: "automate", track: S, slot: 1, target: "send:A", points: [[0, 0], [12, 0.8]], curve: "step" });
    const sweep = await run({ op: "read_automation", track: S, slot: 1, target: "Auto Filter:Frequency", times: [0, 15.9] });
    expect(sweep.display).toEqual(["200 Hz", expect.stringMatching(/^7\.\d+ kHz$/)]);
    const thr = await run({ op: "read_automation", track: S, slot: 1, target: "send:A", times: [6, 13] });
    expect(thr.values[0]).toBeCloseTo(0, 5);
    expect(thr.values[1]).toBeCloseTo(0.8, 5);
    expect((await run({ op: "list_automation", track: S, slot: 1 })).targets.sort()).toEqual(["Auto Filter:Frequency", "send:A"]);
    await run({ op: "clear_automation", track: S, slot: 1 });
    expect((await run({ op: "list_automation", track: S, slot: 1 })).targets).toEqual([]);
  });

  test("arrangement: write, place, lay out scenes with locators, clear", async () => {
    await live.batch([{ op: "create_clip", track: M, slot: 2, length: 8 }, { op: "add_notes", track: M, slot: 2, patterns: { "60": "x.......x......." }, step: 0.5 }]);
    const a = await run({ op: "arrangement_clip", track: M, start: 64, length: 4, name: "IT direct", patterns: { "48": "x..." } });
    expect([a.start, a.end, a.notes]).toEqual([64, 68, 1]);
    await run({ op: "place_clip", track: M, slot: 2, start: 80, length: 12 });
    const arr = await run({ op: "arrangement", tracks: [M] });
    const spans = arr.tracks[0].clips.map((c: any) => [c.start, c.end]);
    expect(spans).toEqual([[64, 68], [80, 88], [88, 92]]);
    // Locators take three Live ticks each (move, add, name), so wait for the queue to drain.
    const before = arr.locators.length;
    await run({ op: "locator", time: 96, name: "IT Mark" });
    await Bun.sleep(600);
    expect((await run({ op: "arrangement" })).locators.some((l: any) => l.name === "IT Mark" && l.time === 96)).toBe(true);
    await run({ op: "locator", time: 96, name: "IT Mark 2" }); // existing locator: renamed, not duplicated
    await Bun.sleep(600);
    const renamed = (await run({ op: "arrangement" })).locators;
    expect(renamed.length).toBe(before + 1);
    expect(renamed.find((l: any) => l.time === 96).name).toBe("IT Mark 2");
    await run({ op: "delete_locator", time: 96 });
    await Bun.sleep(600);
    expect((await run({ op: "arrangement" })).locators.length).toBe(before);
    await run({ op: "clear_arrangement", track: M, start: 60 });
    expect((await run({ op: "arrangement", tracks: [M] })).tracks[0].clips).toHaveLength(0);
  });

  test("generic LOM access: describe, call, set, private names refused", async () => {
    const d = await run({ op: "describe", path: `song.tracks["${S}"].devices["Compressor"]` });
    expect(d.props).toContain("input_routing_type");
    await run({ op: "set", path: `song.tracks["${S}"].devices["Compressor"].parameters["Ratio"]`, prop: "value", value: 0.75 });
    expect((await run({ op: "get", path: `song.tracks["${S}"].devices["Compressor"].parameters["Ratio"]`, props: ["value"] })).values.value).toBeCloseTo(0.75, 5);
    expect((await live.batch([{ op: "get", path: "song._data" }])).ok).toBe(false);
    expect((await live.batch([{ op: "call", path: "song", method: "add_tempo_listener" }])).ok).toBe(false);
  });

  test("batches: refs, stop at first failure, fast error answers", async () => {
    const r = await live.batch([{ op: "create_track", kind: "midi", name: `${P}Ref` }, { op: "create_clip", track: "$0.index", slot: 0, length: 4 }, { op: "nope" }, { op: "create_clip", track: `${P}Ref`, slot: 1, length: 4 }]);
    expect(r.ok).toBe(false);
    expect(r.results.map((x) => x.ok)).toEqual([true, true, false]);
    expect(Object.keys((await track(`${P}Ref`)).clips)).toEqual(["0"]);
    const t0 = performance.now();
    const bad = await live.batch([{ op: "create_clip", track: "$7.index", slot: 0, length: 4 }]);
    expect(bad.ok).toBe(false);
    expect(performance.now() - t0).toBeLessThan(1000);
  });

  test("performance bindings bind and clear", async () => {
    expect((await run({ op: "perf_bind", bindings: { note: { 60: { action: "stop_all" } } } })).notes).toBe(1);
    expect((await run({ op: "perf_bind", bindings: {} })).notes).toBe(0);
  });
});
