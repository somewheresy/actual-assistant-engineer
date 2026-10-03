import { fileURLToPath } from "node:url";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";

/** Keep live_sets' verified identity/path association in one private process. */
export class PythonSession {
  private child?: ChildProcessWithoutNullStreams;
  private pending: { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }[] = [];
  request<T = any>(request: Record<string, unknown>, timeoutMs = 90_000): Promise<T> {
    if (!this.child) {
      const [exe, ...args] = pythonCommand();
      const child = this.child = spawn(exe!, [...args, developerScript, "serve"], {
        stdio: "pipe", windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      });
      // Idle workers must not hold Bun open; EOF closes Python when the parent exits.
      child.unref();
      for (const stream of [child.stdin, child.stdout, child.stderr]) (stream as any).unref?.();
      child.stderr.resume();
      child.stdin.on("error", error => { if (this.child === child) this.fail(error); });
      createInterface({ input: child.stdout }).on("line", line => {
        if (this.child !== child) return;
        const pending = this.pending.shift();
        if (!pending) return;
        clearTimeout(pending.timer);
        try {
          const response = JSON.parse(line);
          if (!response.ok) pending.reject(new Error(response.error));
          else pending.resolve(response.result);
        } catch { pending.reject(new Error("invalid developer Python response")); this.close(); }
      });
      child.on("error", error => this.fail(error));
      child.on("exit", () => this.fail(new Error("developer Python exited; Set association lost")));
    }
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error("developer Python timed out; outcome unknown; Set association lost")), timeoutMs);
      this.pending.push({ resolve, reject, timer });
      this.child!.stdin.write(JSON.stringify(request) + "\n");
    });
  }
  private fail(error: Error) {
    const child = this.child;
    this.child = undefined;
    child?.removeAllListeners();
    child?.stdin.destroy();
    child?.stdout.destroy();
    child?.stderr.destroy();
    child?.kill();
    for (const p of this.pending.splice(0)) { clearTimeout(p.timer); p.reject(error); }
  }
  close() { this.fail(new Error("developer Python session closed; Set association lost")); }
}

export const developerScript = fileURLToPath(new URL("../../scripts/developer.py", import.meta.url));
export function pythonCommand(): string[] {
  // An override is one executable, never shell text. uv also works without python3 on PATH.
  return process.env.AAE_PYTHON ? [process.env.AAE_PYTHON] : ["uv", "run", "--no-project", "--python", "3.11", "python"];
}

export function pythonJson<T>(request: Record<string, unknown>): T {
  const result = Bun.spawnSync([...pythonCommand(), developerScript, "json"], {
    stdin: Buffer.from(JSON.stringify(request)), env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  let response;
  try { response = JSON.parse(result.stdout.toString()); }
  catch { throw new Error(`developer Python failed: ${result.stderr.toString().trim()}`); }
  if (!response.ok || result.exitCode !== 0) throw new Error(response.error || "developer Python failed");
  return response.result as T;
}
