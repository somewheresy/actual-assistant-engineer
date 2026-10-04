import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { closeSync, lstatSync, openSync, readSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import type { Socket } from "bun";

export const SOCK_PATH = join(homedir(), "Library/Application Support/ActualAssistantEngineer/live.sock");
const WINDOWS = process.platform === "win32";
export const ENDPOINT_PATH = WINDOWS
  ? join(process.env.LOCALAPPDATA || join(homedir(), "AppData/Local"), "ActualAssistantEngineer/live.endpoint.json")
  : SOCK_PATH;
export const MAX_FRAME = 64 * 1024 * 1024;

/** Validate OS protection before reading the Windows capability. No shell or
 * user-controlled command interpolation; PowerShell ships with Windows. */
export function readEndpoint(path = ENDPOINT_PATH): { unix: string } | { hostname: string; port: number; token: string } {
  if (!isAbsolute(path)) throw new Error("Live endpoint path must be absolute");
  if (!WINDOWS) {
    for (const [p, directory] of [[dirname(path), true], [path, false]] as const) {
      const st = lstatSync(p);
      if (st.isSymbolicLink() || st.uid !== process.getuid!() || (st.mode & 0o077)
          || (directory ? !st.isDirectory() : !st.isSocket())) throw new Error("insecure Live Unix socket");
    }
    return { unix: path };
  }
  const script = String.raw`
    $ErrorActionPreference = 'Stop'
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($p in @((Split-Path -LiteralPath $env:AAE_ENDPOINT_PATH), $env:AAE_ENDPOINT_PATH)) {
      $item = Get-Item -LiteralPath $p -Force
      if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'reparse point' }
      $acl = Get-Acl -LiteralPath $p
      $sd = [System.Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
      if (!$acl.AreAccessRulesProtected -or $sd.Owner.Value -ne $sid -or $sd.DiscretionaryAcl.Count -ne 1) { throw 'insecure ACL' }
      $ace = $sd.DiscretionaryAcl[0]
      if ($ace.SecurityIdentifier.Value -ne $sid -or $ace.AceQualifier -ne 'AccessAllowed' -or [int]$ace.AceFlags -ne 0 -or $ace.AccessMask -ne 2032127) { throw 'insecure ACE' }
    }
  `;
  try {
    execFileSync(join(process.env.SystemRoot || "C:/Windows", "System32/WindowsPowerShell/v1.0/powershell.exe"),
      ["-NoProfile", "-NonInteractive", "-Command", script],
      { env: { ...process.env, AAE_ENDPOINT_PATH: resolve(path) }, timeout: 5000, windowsHide: true, stdio: "pipe" });
    if (!lstatSync(dirname(path)).isDirectory() || !lstatSync(path).isFile()) throw new Error("invalid endpoint type");
    const fd = openSync(path, "r");
    let text: string;
    try {
      const buf = Buffer.alloc(4097);
      const n = readSync(fd, buf, 0, buf.length, 0);
      if (n > 4096) throw new Error("endpoint too large");
      text = buf.subarray(0, n).toString("utf8");
    } finally { closeSync(fd); }
    const e = JSON.parse(text);
    if (e?.protocol !== 1 || e.transport !== "tcp" || e.auth !== "hmac-sha256" || e.host !== "127.0.0.1"
        || !Number.isInteger(e.port) || e.port < 1 || e.port > 65535
        || typeof e.token !== "string" || !/^[0-9a-f]{64}$/.test(e.token)) throw new Error("invalid endpoint");
    return { hostname: "127.0.0.1", port: e.port, token: e.token };
  } catch {
    // Never include endpoint contents or a child-process exception in errors.
    throw new Error("invalid or insecure Live endpoint");
  }
}

export type Op = { op: string } & Record<string, unknown>;
export type OpResult = { ok: boolean; error?: string } & Record<string, unknown>;
export type BatchResponse = { id: number; ok: boolean; results: OpResult[]; exec_ms: number };
export type LiveEvent = { event: true; kind: string; t: number } & Record<string, unknown>;

type Pending = { resolve: (r: BatchResponse) => void; reject: (e: Error) => void; timer: Timer; handshake: boolean };

/** Connection to the Hermes control surface running inside Live. */
export class LiveClient {
  private socket?: Socket<undefined>;
  private buf = Buffer.alloc(0);
  private outbuf = Buffer.alloc(0);
  private decoder = new TextDecoder("utf-8", { fatal: true });
  private generation = 0;
  private connecting = false;
  private nextId = 1;
  private token?: string;
  private pending = new Map<number, Pending>();
  onEvent?: (e: LiveEvent) => void;
  onClose?: () => void;

  constructor(private path = ENDPOINT_PATH, private maxFrame = MAX_FRAME) {
    if (!Number.isInteger(maxFrame) || maxFrame < 1 || maxFrame > MAX_FRAME) throw new Error("invalid frame limit");
  }

  async connect(): Promise<this> {
    if (this.socket || this.connecting) throw new Error("already connected or connecting");
    this.connecting = true;
    const generation = ++this.generation;
    try {
      const endpoint = readEndpoint(this.path);
      this.token = "token" in endpoint ? endpoint.token : undefined;
      const handlers = {
        data: (_s: Socket<undefined>, chunk: Buffer) => { if (generation === this.generation) this.receive(chunk); },
        drain: () => { if (generation === this.generation) this.flush(); },
        close: () => { if (generation === this.generation) this.closed("Live connection closed"); },
        error: () => { if (generation === this.generation) this.closed("Live connection failed"); },
      };
      const socket = await ("unix" in endpoint
        ? Bun.connect({ unix: endpoint.unix, socket: handlers })
        : Bun.connect({ hostname: endpoint.hostname, port: endpoint.port, socket: handlers }));
      if (generation !== this.generation) { socket.terminate(); throw new Error("Live disconnected before request; nothing sent"); }
      this.socket = socket;
      if (this.token) {
        const nonce = randomBytes(32).toString("hex");
        const reply = await this.request({ hello: nonce }, 1000, true) as BatchResponse & { hello?: string };
        if (reply.ok !== true || reply.hello !== nonce) throw new Error("invalid Live handshake; no request sent");
      }
      return this;
    } catch (error) {
      if (generation === this.generation) this.closed("Live authentication failed; no request sent");
      throw error;
    } finally { this.connecting = false; }
  }

  /** Run ops as one batch (one undo step) and return per-op results. */
  batch(ops: Op[], { timeoutMs = 10_000, undoStep = true } = {}): Promise<BatchResponse> {
    return this.request({ ops, undo_step: undoStep }, timeoutMs);
  }

  async run<T extends OpResult = OpResult>(op: Op, timeoutMs?: number): Promise<T> {
    const res = await this.batch([op], { timeoutMs, undoStep: false });
    const r = res.results[0];
    if (!res.ok || !r) throw new Error(`${op.op}: ${r?.error ?? "failed"}`);
    return r as T;
  }

  /** Development: hot-reload the control surface's Python code. */
  reload(): Promise<BatchResponse> {
    return this.request({ reload: true }, 5_000);
  }

  subscribe(): Promise<BatchResponse> {
    return this.request({ subscribe: true }, 5_000);
  }

  close() {
    this.closed("Live connection closed");
  }

  private request(body: Record<string, unknown>, timeoutMs: number, handshake = false): Promise<BatchResponse> {
    const socket = this.socket;
    if (!socket || (this.connecting && !handshake)) return Promise.reject(new Error("not connected"));
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return Promise.reject(new Error("timeout must be positive; nothing sent"));
    const id = this.nextId++;
    let frame: Buffer;
    try {
      const text = JSON.stringify({ id, ...body });
      const envelope = this.token ? JSON.stringify({ body: text, mac: this.mac(text, "request") }) : text;
      frame = Buffer.from(envelope + "\n");
      if (frame.length > this.maxFrame + 1 || this.outbuf.length + frame.length > this.maxFrame + 1) {
        return Promise.reject(new Error("Live request exceeds frame limit; nothing sent"));
      }
    } catch { return Promise.reject(new Error("invalid Live request; nothing sent")); }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        // Discard unsent bytes; never finish a timed-out request on a later
        // drain callback or replay it on reconnect. Sent mutations may execute.
        this.closed(`request ${id} timed out after ${timeoutMs}ms`);
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, handshake });
      this.outbuf = Buffer.concat([this.outbuf, frame]);
      this.flush();
    });
  }

  // Bun sockets don't buffer: write() may accept only part of the data.
  private flush() {
    if (!this.socket || this.outbuf.length === 0) return;
    try {
      const n = this.socket.write(this.outbuf);
      if (n < 0) this.closed("Live write failed");
      else if (n > 0) this.outbuf = this.outbuf.subarray(n);
    } catch { this.closed("Live write failed"); }
  }

  private receive(chunk: Uint8Array) {
    this.buf = Buffer.concat([this.buf, chunk]);
    try {
      let nl: number;
      while ((nl = this.buf.indexOf(10)) >= 0) {
        if (nl > this.maxFrame) throw new Error("frame limit");
        const line = this.buf.subarray(0, nl);
        this.buf = this.buf.subarray(nl + 1);
        if (!line.length) continue;
        let msg = JSON.parse(this.decoder.decode(line));
        if (this.token) {
          if (typeof msg?.body !== "string" || typeof msg?.mac !== "string" || !/^[0-9a-f]{64}$/.test(msg.mac)
              || !timingSafeEqual(Buffer.from(msg.mac, "hex"), Buffer.from(this.mac(msg.body, "response"), "hex"))) throw new Error("authentication");
          msg = JSON.parse(msg.body);
        }
        if (!msg || typeof msg !== "object" || Array.isArray(msg)) throw new Error("invalid response");
        if (msg.event) { if (!this.connecting) this.onEvent?.(msg); continue; }
        const p = this.pending.get(msg.id);
        if (!p) continue;
        clearTimeout(p.timer);
        this.pending.delete(msg.id);
        p.resolve(msg);
      }
      if (this.buf.length > this.maxFrame) throw new Error("frame limit");
    } catch { this.closed("invalid, oversized or unauthenticated Live response"); }
  }

  private mac(body: string, direction: "request" | "response"): string {
    return createHmac("sha256", Buffer.from(this.token!, "hex")).update(`aae-v1/${direction}\n${body}`, "utf8").digest("hex");
  }

  private closed(reason: string) {
    const socket = this.socket;
    this.socket = undefined;
    ++this.generation; // Ignore late callbacks from a terminated connection.
    this.buf = Buffer.alloc(0);
    this.outbuf = Buffer.alloc(0);
    this.token = undefined;
    socket?.terminate();
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(new Error(p.handshake ? `${reason}; no request sent` : `${reason}; outcome unknown, inspect before retrying`));
    }
    this.pending.clear();
    this.onClose?.();
  }
}
