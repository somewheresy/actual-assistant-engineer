import { test, expect } from "bun:test";
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { once } from "node:events";
import { LiveClient, readEndpoint } from "../src/live/client";

test.skipIf(process.platform !== "win32")("large first batch survives 100ms Live display ticks", async () => {
  const server = await peer("paced");
  const client = new LiveClient(server.endpoint);
  try {
    await client.connect();
    const result = await client.batch([{ op: "set_identity", set_id: "x".repeat(48 * 1024 * 1024) }], { timeoutMs: 15000 });
    expect(result.ok).toBe(true);
    expect((result.results[0]?.set_id as string).length).toBe(48 * 1024 * 1024);
  } finally { client.close(); await server.stop(); }
}, 20000);

async function peer(mode = "bridge") {
  const root = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "aae-transport-"));
  const directory = join(root, "private");
  const child = spawn(process.env.AAE_TEST_PYTHON || "python", ["test/transport/peer.py", directory, mode], { stdio: ["pipe", "pipe", "pipe"] });
  const endpoint = await new Promise<string>((resolve, reject) => {
    let text = "", errors = "";
    const timer = setTimeout(() => reject(new Error("peer startup timed out")), 5000);
    child.stderr.on("data", b => { errors += b; });
    child.stdout.on("data", b => {
      text += b;
      if (text.includes("\n")) { clearTimeout(timer); resolve(text.split("\n")[0]!.trim()); }
    });
    child.once("exit", () => { clearTimeout(timer); reject(new Error(`peer exited: ${errors}`)); });
  });
  return {
    endpoint,
    async capture() {
      const path = join(root, "capture.json");
      const deadline = Date.now() + 2000;
      while (!existsSync(path) && Date.now() < deadline) await Bun.sleep(10);
      return JSON.parse(readFileSync(path, "utf8"));
    },
    async stop() { const exited = once(child, "exit"); child.kill(); await exited; rmSync(root, { recursive: true, force: true }); },
  };
}

test("TypeScript client authenticates actual Python bridge batch, subscribe and reload", async () => {
  const server = await peer();
  const client = new LiveClient(server.endpoint);
  try {
    await client.connect();
    expect((await client.batch([{ op: "ping" }])).ok).toBe(true);
    expect((await client.subscribe()).ok).toBe(true);
    await client.run({ op: "transport", tempo: 99 });
    expect((await client.run({ op: "transport" })).tempo).toBe(99);
    expect((await client.reload()).ok).toBe(true);
    expect((await client.batch([{ op: "ping" }])).ok).toBe(true);
  } finally { client.close(); await server.stop(); }
}, 15000);

for (const mode of ["eof", "malformed", "oversize", "timeout", "forged", "reflection"]) {
  test.skipIf(process.platform !== "win32" && ["forged", "reflection"].includes(mode))(`TypeScript ${mode}: unknown outcome, clean reconnect, no replay`, async () => {
    const server = await peer(mode);
    const client = new LiveClient(server.endpoint, 2048);
    try {
      await client.connect();
      await expect(client.batch([{ op: "transport", tempo: 99 }], { timeoutMs: 100 })).rejects.toThrow(/outcome unknown.*inspect before retrying/);
      await expect(client.batch([{ op: "ping" }])).rejects.toThrow(/not connected/);
      await client.connect();
      expect((await client.batch([{ op: "ping" }])).ok).toBe(true);
    } finally { client.close(); await server.stop(); }
  }, 15000);
}

for (const mode of ["forged", "reflection", "wrong_nonce", "eof", "timeout"]) {
  test.skipIf(process.platform !== "win32")(`failed ${mode} handshake sends no mutation and allows reconnect`, async () => {
    const server = await peer(`handshake_${mode}`);
    const client = new LiveClient(server.endpoint);
    try {
      const connecting = client.connect();
      await expect(client.batch([{ op: "transport", tempo: 99 }])).rejects.toThrow("not connected");
      await expect(connecting).rejects.toThrow(/no request sent/);
      const captured = await server.capture();
      expect(Object.keys(captured.hello).sort()).toEqual(["hello", "id"]);
      expect(captured.extra).toBe(0);
      expect(captured.token_leaked).toBe(false);
      await client.connect();
      expect((await client.batch([{ op: "ping" }])).ok).toBe(true);
    } finally { client.close(); await server.stop(); }
  }, 15000);
}

test.skipIf(process.platform !== "win32")("Windows endpoint validation rejects remote hosts, invalid schema, ACL grants and junctions", async () => {
  const server = await peer();
  try {
    const original = readFileSync(server.endpoint, "utf8");
    for (const change of [
      { host: "0.0.0.0" }, { host: "localhost" }, { host: "192.0.2.1" },
      { port: true }, { port: 0 }, { port: 65536 }, { port: "1234" },
      { protocol: true }, { protocol: 2 }, { transport: "unix" },
      { token: "bad" }, { token: null }, { auth: "none" },
    ]) {
      writeFileSync(server.endpoint, JSON.stringify({ ...JSON.parse(original), ...change }));
      expect(() => readEndpoint(server.endpoint)).toThrow(/invalid or insecure/);
    }
    writeFileSync(server.endpoint, original);
    const alias = join(dirname(dirname(server.endpoint)), "alias");
    symlinkSync(dirname(server.endpoint), alias, "junction");
    expect(() => readEndpoint(join(alias, "live.endpoint.json"))).toThrow(/invalid or insecure/);
    execFileSync("icacls", [server.endpoint, "/grant", "*S-1-1-0:(R)"], { stdio: "pipe" });
    expect(() => readEndpoint(server.endpoint)).toThrow(/invalid or insecure/);
  } finally { await server.stop(); }
}, 30000);

test("TypeScript handles fragmented UTF-8 signed replies and coalesced events", async () => {
  const server = await peer("fragmented");
  const client = new LiveClient(server.endpoint);
  const events: unknown[] = [];
  client.onEvent = e => events.push(e);
  try {
    await client.connect();
    const result = await client.batch([{ op: "ping" }]);
    expect(result.results[0]?.label).toBe("Drüms 🎵");
    expect(events.length).toBe(1);
  } finally { client.close(); await server.stop(); }
}, 15000);

test("TypeScript discards a real backpressured partial write on timeout and reconnect", async () => {
  const server = await peer("backpressure");
  const client = new LiveClient(server.endpoint);
  try {
    await client.connect();
    await expect(client.batch([{ op: "set_identity", set_id: "x".repeat(8 * 1024 * 1024) }], { timeoutMs: 50 }))
      .rejects.toThrow(/outcome unknown.*inspect before retrying/);
    await client.connect();
    expect((await client.batch([{ op: "ping" }])).ok).toBe(true);
  } finally { client.close(); await server.stop(); }
}, 15000);

test("TypeScript refuses oversized requests before writing", async () => {
  const server = await peer();
  const client = new LiveClient(server.endpoint, 1024);
  try {
    await client.connect();
    await expect(client.batch([{ op: "ping", large: "x".repeat(1024) }])).rejects.toThrow(/frame limit.*nothing sent/);
    expect((await client.batch([{ op: "ping" }])).ok).toBe(true);
  } finally { client.close(); await server.stop(); }
}, 15000);
