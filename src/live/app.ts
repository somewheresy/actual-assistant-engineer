// Locate the installed Ableton Live: the running edition, else the newest installed.
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";

export function liveBundle(): string {
  const ps = Bun.spawnSync(["ps", "-axo", "comm="]).stdout.toString();
  const running = ps.split("\n").map((l) => /^(.*?\/Ableton Live[^/]*\.app)\/Contents\/MacOS\/Live$/.exec(l.trim())?.[1]).find(Boolean);
  if (running) return running;
  const apps = ["/Applications", `${homedir()}/Applications`]
    .filter(existsSync)
    .flatMap((d) => readdirSync(d).filter((n) => /^Ableton Live.*\.app$/.test(n)).map((n) => `${d}/${n}`))
    .sort();
  if (!apps.length) throw new Error("no Ableton Live app found");
  return apps.at(-1)!;
}

export const defaultSet = () => `${liveBundle()}/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als`;
