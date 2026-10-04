import { test, expect } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { liveBundle, defaultSet } from "../src/live/app";
import { screenshotLive, openSet } from "../spike/open-set";
import * as python from "../src/live/python";

test("persistent JSON worker survives errors and closes cleanly", async () => {
  expect(typeof python.PythonSession).toBe("function");
  const session = new python.PythonSession();
  try {
    await expect(session.request({ action: "not-supported" })).rejects.toThrow("unsupported developer action");
    await expect(session.request({ action: "open_set", path: "missing.als" })).rejects.toThrow("no Set at");
  } finally { session.close(); }
});

test("worker timeout drops its association and permits a fresh session", async () => {
  const session = new python.PythonSession();
  try {
    await expect(session.request({ action: "bundle" }, 1)).rejects.toThrow("outcome unknown");
    await expect(session.request({ action: "not-supported" })).rejects.toThrow("unsupported developer action");
  } finally { session.close(); }
});

test("openSet delegates missing files safely without shell interpolation", async () => {
  await expect(openSet('missing quote";file.als')).rejects.toThrow("no Set at");
});

test.skipIf(process.platform !== "win32")("Windows screenshots reject missing Live identity without a screen fallback", () => {
  const root = mkdtempSync(join(tmpdir(), "aae-capture-"));
  const old = process.env.AAE_LIVE_PATH;
  try {
    process.env.AAE_LIVE_PATH = join(root, "missing-live.exe");
    expect(() => screenshotLive(join(root, "never-created.png"))).toThrow("AAE_LIVE_PATH");
  } finally {
    if (old === undefined) delete process.env.AAE_LIVE_PATH; else process.env.AAE_LIVE_PATH = old;
    rmSync(root, { recursive: true, force: true });
  }
});

test("discovery uses shipped Live override and verifies built-in template", () => {
  const root = mkdtempSync(join(tmpdir(), "aae-devtools-"));
  const old = process.env.AAE_LIVE_PATH;
  try {
    const windows = process.platform === "win32";
    const exe = windows ? join(root, "Program", "Ableton Live 99 Test.exe") : join(root, "Ableton Live 99 Test.app");
    if (windows) { mkdirSync(join(root, "Program")); writeFileSync(exe, "fixture"); }
    else mkdirSync(exe);
    process.env.AAE_LIVE_PATH = exe;
    expect(liveBundle()).toBe(exe);
    expect(() => defaultSet()).toThrow("template is missing");
    const resources = windows ? join(root, "Resources") : join(exe, "Contents", "App-Resources");
    const template = join(resources, "Builtin", "Templates", "DefaultLiveSet.als");
    mkdirSync(join(resources, "Builtin", "Templates"), { recursive: true });
    writeFileSync(template, "fixture");
    expect(defaultSet()).toBe(template);
  } finally {
    if (old === undefined) delete process.env.AAE_LIVE_PATH; else process.env.AAE_LIVE_PATH = old;
    rmSync(root, { recursive: true, force: true });
  }
});
