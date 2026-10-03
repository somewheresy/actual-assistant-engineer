// Measure what's in the open Live Set: a structural complexity profile of a finished track.
import type { LiveClient } from "../live/client";

type Track = { index: number; name: string; midi: boolean; devices: { name: string; class: string }[]; clips?: Record<string, { length: number }> };
type ArrClip = { index: number; start: number; end: number };

const INSTRUMENTS = /Operator|Analog|Wavetable|Drift|Meld|Simpler|Sampler|Collision|Tension|Electric|InstrumentGroup|DrumGroup|Impulse|Plugin/i;

export type Complexity = {
  tempo: number;
  tracks: number;
  instruments: number;
  effects: number;
  plugins: number;
  sessionClips: number;
  arrangementClips: number;
  notes: number;
  distinctPitches: number;
  scenes: number;
  sections: number;
  arrangementBars: number;
  arrangementSeconds: number;
  locators: string[];
  automationEnvelopes: number;
  sidechains: number;
  activeSends: number;
  devicesByTrack: Record<string, string[]>;
};

export async function measure(live: LiveClient, skipTracks: Set<string> = new Set()): Promise<Complexity> {
  const ov = await live.run<{ ok: boolean; tempo: number; signature: number[]; tracks: Track[]; scenes: { name: string }[] }>({ op: "overview" });
  const tracks = ov.tracks.filter((t) => !skipTracks.has(t.name) && (t.devices.length || Object.keys(t.clips ?? {}).length));
  const arr = await live.run<{ ok: boolean; locators: { name: string; time: number }[]; tracks: { index: number; clips: ArrClip[] }[] }>({ op: "arrangement" });

  let notes = 0, automation = 0, sidechains = 0, sends = 0;
  const pitches = new Set<number>();
  const reads = [];
  for (const t of tracks) {
    for (const slot of Object.keys(t.clips ?? {})) {
      reads.push({ op: "get_notes", track: t.index, slot: Number(slot) });
    }
    const a = arr.tracks.find((x) => x.index === t.index);
    reads.push({ op: "get", path: `song.tracks[${t.index}].mixer_device`, props: ["sends"] });
  }
  for (let i = 0; i < reads.length; i += 200) {
    const res = await live.batch(reads.slice(i, i + 200), { undoStep: false, timeoutMs: 60_000 });
    for (const r of res.results as Record<string, any>[]) {
      if (Array.isArray(r.notes)) for (const n of r.notes) (notes++, pitches.add(n.pitch));
      if (Array.isArray(r.values?.sends)) sends += r.values.sends.filter((s: { value?: number }) => (s.value ?? 0) > 0.001).length;
    }
  }

  // Automation and sidechains come from the bridge's review so the report and the gate never disagree.
  const review = await live.run<{ ok: boolean; automation_envelopes: number; sidechains: number }>({ op: "review", ignore_tracks: [...skipTracks] });
  automation = review.automation_envelopes;
  sidechains = review.sidechains;
  const arrClips = arr.tracks.flatMap((t) => (tracks.some((x) => x.index === t.index) ? t.clips : []));
  const endBeat = Math.max(0, ...arrClips.map((c) => c.end));
  const beatsPerBar = ov.signature[0] ?? 4;
  const devices = tracks.flatMap((t) => t.devices);
  const isInstrument = (d: { name: string; class: string }) => INSTRUMENTS.test(d.class) && !/Effect/.test(d.class);
  return {
    tempo: ov.tempo,
    tracks: tracks.length,
    instruments: devices.filter(isInstrument).length,
    effects: devices.filter((d) => !isInstrument(d)).length,
    plugins: devices.filter((d) => d.class === "PluginDevice").length,
    sessionClips: tracks.reduce((a, t) => a + Object.keys(t.clips ?? {}).length, 0),
    arrangementClips: arrClips.length,
    notes,
    distinctPitches: pitches.size,
    scenes: ov.scenes.filter((s) => s.name).length,
    sections: arr.locators.length,
    arrangementBars: Math.round((endBeat / beatsPerBar) * 10) / 10,
    arrangementSeconds: Math.round((endBeat * 60) / ov.tempo),
    locators: arr.locators.map((l) => l.name),
    automationEnvelopes: automation,
    sidechains,
    activeSends: sends,
    devicesByTrack: Object.fromEntries(tracks.map((t) => [t.name, t.devices.map((d) => d.name)])),
  };
}
