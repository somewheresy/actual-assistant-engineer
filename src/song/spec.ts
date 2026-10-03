// Song spec: the fully expanded song that engine/als.py writes into a Live Set.
export type Note = { pitch: number; start: number; duration: number; velocity?: number };
export type Section = { name: string; bars: number; color?: number; tempo?: number };
// Envelope points are [beat, value]; volume values are dB.
export type Envelope = { target: string; points: [number, number][] };
export type Clip = { name?: string; notes: Note[]; envelopes?: Envelope[] };
export type Part = { track: string; name?: string; color?: number; clips: Record<string, Clip> };
export type SongSpec = { title: string; tempo: number; template: string; sections: Section[]; parts: Part[] };
