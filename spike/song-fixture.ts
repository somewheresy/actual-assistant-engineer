// Deterministic fixture song spec for the .als round-trip probe: A minor, 100 BPM,
// four sections across the Quick Start Song template's Drums / Bass / Keys tracks.
export type { Note, Section, Clip, SongSpec } from "../src/song/spec";
import type { Note, Section, SongSpec } from "../src/song/spec";

const KICK = 36, SNARE = 38, HAT = 42, OPEN_HAT = 46;
const PROG = [57, 53, 48, 55]; // Am F C G roots (A3 F3 C3 G3)
const TRIADS = [[0, 3, 7], [0, 4, 7], [0, 4, 7], [0, 4, 7]];

const drums = (bars: number, density: number): Note[] =>
  Array.from({ length: bars }, (_, b) => {
    const t = b * 4;
    const n: Note[] = [
      { pitch: KICK, start: t, duration: 0.25, velocity: 110 },
      { pitch: KICK, start: t + 2.5, duration: 0.25, velocity: 95 },
      { pitch: SNARE, start: t + 1, duration: 0.25, velocity: 105 },
      { pitch: SNARE, start: t + 3, duration: 0.25, velocity: 105 },
    ];
    for (let i = 0; i < 4 * density; i++) n.push({ pitch: i % (2 * density) === 2 * density - 1 && density > 1 ? OPEN_HAT : HAT, start: t + i / density, duration: 0.1, velocity: i % 2 ? 70 : 90 });
    return n;
  }).flat();

const bass = (bars: number, eighths: boolean): Note[] =>
  Array.from({ length: bars }, (_, b) => {
    const root = PROG[b % 4]! - 24;
    return eighths
      ? Array.from({ length: 8 }, (_, i) => ({ pitch: root + (i === 7 ? 7 : 0), start: b * 4 + i / 2, duration: 0.45, velocity: i % 2 ? 85 : 100 }))
      : [{ pitch: root, start: b * 4, duration: 3.8, velocity: 95 }];
  }).flat();

const keys = (bars: number, stabs: boolean): Note[] =>
  Array.from({ length: bars }, (_, b) => {
    const chord = TRIADS[b % 4]!.map((i) => PROG[b % 4]! + i);
    return stabs
      ? [0, 1.5, 3].flatMap((o) => chord.map((p) => ({ pitch: p, start: b * 4 + o, duration: 0.4, velocity: 90 })))
      : chord.map((p) => ({ pitch: p, start: b * 4, duration: 3.9, velocity: 75 }));
  }).flat();

const sections: Section[] = [
  { name: "Intro", bars: 4 },
  { name: "Verse", bars: 8 },
  { name: "Chorus", bars: 8 },
  { name: "Outro", bars: 4 },
];

export const fixtureSong: SongSpec = {
  title: "Hermes Fixture",
  tempo: 100,
  template: "quick-start-song",
  sections,
  parts: [
    { track: "Drums", clips: { Verse: { notes: drums(8, 2) }, Chorus: { notes: drums(8, 4) }, Outro: { notes: drums(4, 1) } } },
    { track: "Bass", clips: { Verse: { notes: bass(8, false) }, Chorus: { notes: bass(8, true) }, Outro: { notes: bass(4, false) } } },
    {
      track: "Keys",
      clips: {
        Intro: { notes: keys(4, false), envelopes: [{ target: "volume", points: [[0, -18], [16, 0]] }] },
        Verse: { notes: keys(8, false) },
        Chorus: { notes: keys(8, true) },
        Outro: { notes: keys(4, false) },
      },
    },
  ],
};
