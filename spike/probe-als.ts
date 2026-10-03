// Probe (c): verify a generated .als opened in Live matches its song spec exactly.
// Run after opening the Set produced by `python engine/als.py <spec> <out.als>`.
import { LiveClient } from "../src/live/client";
import { fixtureSong, type Note } from "./song-fixture";

type Overview = {
  ok: boolean;
  tempo: number;
  scenes: { name: string }[];
  tracks: { index: number; name: string; playing_slot: number; clips?: Record<string, { name: string; length: number }> }[];
};

const live = await new LiveClient().connect();
const ov = await live.run<Overview>({ op: "overview" });
const problems: string[] = [];
const check = (cond: boolean, msg: string) => cond || problems.push(msg);

check(ov.tempo === fixtureSong.tempo, `tempo ${ov.tempo} != ${fixtureSong.tempo}`);
check(
  JSON.stringify(ov.scenes.map((s) => s.name)) === JSON.stringify(fixtureSong.sections.map((s) => s.name)),
  `scenes ${ov.scenes.map((s) => s.name)}`,
);

const key = (n: Note) => `${n.pitch}@${n.start.toFixed(4)}+${n.duration.toFixed(4)}v${Math.round(n.velocity ?? 100)}`;
let clips = 0, notes = 0;
for (const part of fixtureSong.parts) {
  const track = ov.tracks.find((t) => t.name === part.track);
  if (!track) { problems.push(`missing track ${part.track}`); continue; }
  for (const [si, section] of fixtureSong.sections.entries()) {
    const want = part.clips[section.name];
    const have = track.clips?.[String(si)];
    if (!want) { check(!have, `${part.track}/${section.name}: unexpected clip`); continue; }
    if (!have) { problems.push(`${part.track}/${section.name}: missing clip`); continue; }
    clips++;
    check(have.length === section.bars * 4, `${part.track}/${section.name}: length ${have.length}`);
    const got = await live.run<{ ok: boolean; notes: Note[] }>({ op: "get_notes", track: track.index, slot: si, format: "objects" });
    const a = got.notes.map(key).sort(), b = want.notes.map(key).sort();
    notes += a.length;
    check(JSON.stringify(a) === JSON.stringify(b), `${part.track}/${section.name}: notes differ (${a.length} vs ${b.length})`);
    for (const env of want.envelopes ?? []) {
      const times = env.points.map(([t]) => t);
      const e = await live.run<{ ok: boolean; present: boolean; display?: string[] }>({ op: "clip_envelope", track: track.index, slot: si, target: env.target, times });
      // Compare through Live's own display strings, e.g. "-18.0 dB".
      const dB = e.display?.map((d) => parseFloat(d));
      const close = dB?.every((v, i) => Math.abs(v - env.points[i]![1]) < 0.15);
      check(e.present && !!close, `${part.track}/${section.name}: envelope ${env.target} ${JSON.stringify(e.display)}`);
    }
  }
}

// Play it: launch the Chorus scene and confirm Live reports playing clips.
const chorus = fixtureSong.sections.findIndex((s) => s.name === "Chorus");
await live.run({ op: "fire_scene", scene: chorus });
await Bun.sleep(2500);
const playing = await live.run<Overview>({ op: "overview", clips: false });
const playingParts = playing.tracks.filter((t) => t.playing_slot === chorus).map((t) => t.name);
check(playingParts.length === 3, `playing on chorus: ${playingParts}`);
check(playing.tempo === fixtureSong.tempo, `tempo after scene launch ${playing.tempo} != ${fixtureSong.tempo}`);
await live.run({ op: "transport", play: false });

console.log(`verified ${clips} clips, ${notes} notes, tempo, scenes, envelopes; playing: ${playingParts.join(", ")}`);
console.log(problems.length ? `PROBLEMS:\n- ${problems.join("\n- ")}` : "round trip exact");
live.close();
process.exit(problems.length ? 1 : 0);
