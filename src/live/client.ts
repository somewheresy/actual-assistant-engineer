import { homedir } from "node:os";
import { join } from "node:path";
import type { Socket } from "bun";

export const SOCK_PATH = join(homedir(), "Library/Application Support/ActualAssistantEngineer/live.sock");

export type Op = { op: string } & Record<string, unknown>;
export type OpResult = { ok: boolean; error?: string } & Record<string, unknown>;
export type BatchResponse = { id: number; ok: boolean; results: OpResult[]; exec_ms: number };
export type LiveEvent = { event: true; kind: string; t: number } & Record<string, unknown>;

type Pending = { resolve: (r: BatchResponse) => void; reject: (e: Error) => void; timer: Timer };

/** Connection to the Hermes control surface running inside Live. */
export class LiveClient {
  private socket?: Socket<undefined>;
  private buf = "";
  private nextId = 1;
  private pending = new Map<number, Pending>();
  onEvent?: (e: LiveEvent) => void;
  onClose?: () => void;

  constructor(private path = SOCK_PATH) {}

  async connect(): Promise<this> {
    this.socket = await Bun.connect({
      unix: this.path,
      socket: {
        data: (_s, chunk) => this.receive(chunk.toString()),
        close: () => this.closed(new Error("Live connection closed")),
        error: (_s, err) => this.closed(err),
      },
    });
    return this;
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

  subscribe(): Promise<BatchResponse> {
    return this.request({ subscribe: true }, 5_000);
  }

  close() {
    this.socket?.end();
  }

  private request(body: Record<string, unknown>, timeoutMs: number): Promise<BatchResponse> {
    const socket = this.socket;
    if (!socket) return Promise.reject(new Error("not connected"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        // The batch may still execute in Live; callers must reconcile before retrying.
        reject(new Error(`request ${id} timed out after ${timeoutMs}ms (outcome unknown)`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      socket.write(JSON.stringify({ id, ...body }) + "\n");
    });
  }

  private receive(chunk: string) {
    this.buf += chunk;
    let nl: number;
    while ((nl = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, nl);
      this.buf = this.buf.slice(nl + 1);
      if (!line) continue;
      const msg = JSON.parse(line);
      if (msg.event) {
        this.onEvent?.(msg);
        continue;
      }
      const p = this.pending.get(msg.id);
      if (!p) continue;
      clearTimeout(p.timer);
      this.pending.delete(msg.id);
      p.resolve(msg);
    }
  }

  private closed(err: Error) {
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();
    this.socket = undefined;
    this.onClose?.();
  }
}
