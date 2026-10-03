// Open a .als in Live and wait until the Hermes bridge is serving the new Set.
// Live asks whether to save the current Set first; `onUnsaved` decides the answer.
import { existsSync } from "node:fs";
import { basename } from "node:path";
import { LiveClient, SOCK_PATH } from "../src/live/client";

const osa = (...lines: string[]) => {
  const r = Bun.spawnSync(["osascript", ...lines.flatMap((l) => ["-e", l])]);
  return r.stdout.toString().trim();
};
const liveWindows = () => osa('tell application "System Events" to get name of windows of process "Live"');

export async function openSet(path: string, onUnsaved: "dont-save" | "cancel" = "cancel", timeoutMs = 60_000) {
  const title = basename(path, ".als");
  const t0 = performance.now();
  Bun.spawnSync(["open", "-a", "Ableton Live 12 Suite", path]);
  let dialog: string | undefined;
  while (performance.now() - t0 < timeoutMs) {
    await Bun.sleep(250);
    const prompt = osa('tell application "System Events" to tell process "Live" to get value of static text 1 of group 1 of window 1');
    if (prompt.startsWith("This action will stop audio")) {
      osa('tell application "System Events" to tell process "Live" to click (first button of group 1 of window 1 whose description is "OK")');
      continue;
    }
    if (prompt.startsWith("Save changes")) {
      dialog = prompt;
      const button = onUnsaved === "dont-save" ? "Don" : "Cancel";
      osa(`tell application "System Events" to tell process "Live" to click (first button of group 1 of window 1 whose description starts with "${button}")`);
      if (onUnsaved === "cancel") throw new Error(`refusing to discard: ${prompt}`);
      continue;
    }
    if (!liveWindows().split(", ").includes(title) || !existsSync(SOCK_PATH)) continue;
    try {
      const live = await new LiveClient().connect();
      const info = await live.run<{ ok: boolean; tracks: number }>({ op: "info" });
      return { live, info, dialog, ms: performance.now() - t0 };
    } catch {
      // Socket present but the new control-surface instance isn't serving yet.
    }
  }
  throw new Error(`timed out opening ${path}`);
}
