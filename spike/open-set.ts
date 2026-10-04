// File operations use the shipped targeted UI, never global keystrokes.
import { existsSync } from "node:fs";
import { LiveClient } from "../src/live/client";
import { PythonSession, pythonJson } from "../src/live/python";
const sets = new PythonSession();
export async function openSet(path: string, onUnsaved: "dont-save" | "cancel" = "cancel", timeoutMs = 60_000) {
  const start = performance.now();
  const result = await sets.request<{ answered: string[] }>({ action: "open_set", path,
    on_unsaved: onUnsaved === "dont-save" ? "discard" : "cancel" }, timeoutMs);
  const live = await new LiveClient().connect();
  try {
    const info = await live.run<{ ok: boolean; tracks: number }>({ op: "info" });
    return { live, info, dialog: result.answered.join("\n") || undefined, ms: performance.now() - start };
  } catch (error) { live.close(); throw error; }
}
/** Requires a file opened by this process; unknown titles are never guessed. */
export async function saveSetAs(dir: string, name: string) {
  return (await sets.request<{ path: string }>({ action: "save_as", directory: dir, name })).path;
}
export function closeSetSession() { sets.close(); }
/** Capture only Live's own window. Windows returns a new .bmp path, even for a .png request. */
export function screenshotLive(path: string) {
  if (process.platform === "win32") {
    const bmp = /\.bmp$/i.test(path) ? path : path.replace(/\.[^./\\]+$/, "") + ".bmp";
    return pythonJson<string>({ action: "screenshot", path: bmp });
  }
  if (process.platform !== "darwin") throw new Error("Live target-window capture is unsupported on this platform; no whole-screen fallback");
  const id = Bun.spawnSync(["./bin/window-id", "Live"]).stdout.toString().trim();
  if (!id) return undefined;
  const result = Bun.spawnSync(["screencapture", "-x", "-o", "-l", id, path]);
  return result.exitCode === 0 && existsSync(path) ? path : undefined;
}
