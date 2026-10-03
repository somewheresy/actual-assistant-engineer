"""Hermes control surface for Actual Assistant Engineer.

Runs inside Live's embedded Python. Serves newline-delimited JSON over a
Unix domain socket and executes each request's ops on Live's main thread
during the control surface update tick. Performance gestures arrive as MIDI
on channel 16 and are executed immediately in receive_midi.
"""

import importlib
import json
import os
import select
import socket
import time
import traceback

import Live
from _Framework.ControlSurface import ControlSurface

from . import ops

SOCK_DIR = os.path.expanduser("~/Library/Application Support/ActualAssistantEngineer")
SOCK_PATH = os.path.join(SOCK_DIR, "live.sock")
PROTOCOL = 1
PERF_CHANNEL = 15  # MIDI channel 16, zero-based
MAX_LINE = 64 * 1024 * 1024
SOCK_BUF = 4 * 1024 * 1024
PARTIAL_WAIT = 0.03  # max seconds per tick spent finishing a partially received request


class Client:
    def __init__(self, conn):
        self.conn = conn
        self.inbuf = b""
        self.outbuf = b""
        self.subscribed = False
        self.first_byte = None


class Hermes(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._clients = []
        self._server = None
        self._ticks = []
        self._perf = {"note": {}, "cc": {}}
        self._ctx = ops.Context(self)
        self._open_server()
        self._ctx.attach_listeners()
        self.log_message("Hermes: listening on %s" % SOCK_PATH)

    # --- socket server -------------------------------------------------

    def _open_server(self):
        os.makedirs(SOCK_DIR, mode=0o700, exist_ok=True)
        os.chmod(SOCK_DIR, 0o700)
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCK_PATH)
        os.chmod(SOCK_PATH, 0o600)
        server.listen(8)
        server.setblocking(False)
        self._server = server

    def _poll(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except (BlockingIOError, InterruptedError):
                break
            conn.setblocking(False)
            # The default ~8KB buffers would spread one large batch across several 100ms ticks.
            for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
                try:
                    conn.setsockopt(socket.SOL_SOCKET, opt, SOCK_BUF)
                except OSError:
                    pass
            self._clients.append(Client(conn))
        for client in list(self._clients):
            self._read(client)
            self._flush(client)

    def _read(self, client):
        deadline = time.perf_counter() + PARTIAL_WAIT
        while True:
            try:
                chunk = client.conn.recv(1 << 20)
            except (BlockingIOError, InterruptedError):
                # A request is mid-flight: the sender refills its small buffer within
                # microseconds, so wait briefly rather than a whole 100ms tick.
                pending = client.inbuf and not client.inbuf.endswith(b"\n")
                if pending and time.perf_counter() < deadline:
                    select.select([client.conn], [], [], 0.002)
                    continue
                break
            except OSError:
                self._drop(client)
                return
            if not chunk:
                self._drop(client)
                return
            if not client.inbuf:
                client.first_byte = time.perf_counter()
            client.inbuf += chunk
            if len(client.inbuf) > MAX_LINE:
                self._drop(client)
                return
        while b"\n" in client.inbuf:
            line, client.inbuf = client.inbuf.split(b"\n", 1)
            if line.strip():
                recv_ms = (time.perf_counter() - client.first_byte) * 1000 if client.first_byte else 0
                client.first_byte = time.perf_counter() if client.inbuf else None
                msg = self._handle(client, line)
                msg["recv_ms"] = round(recv_ms, 1)
                self._send(client, msg)

    def _handle(self, client, line):
        t0 = time.perf_counter()
        try:
            req = json.loads(line)
        except ValueError as e:
            return {"ok": False, "error": "bad json: %s" % e}
        rid = req.get("id")
        if req.get("subscribe"):
            client.subscribed = True
            return {"id": rid, "ok": True, "subscribed": True}
        if req.get("reload"):
            try:
                return dict(self._reload(), id=rid)
            except Exception as e:
                self.log_message("Hermes reload failed: %s" % traceback.format_exc())
                return {"id": rid, "ok": False, "error": "reload failed: %s: %s" % (type(e).__name__, e)}
        try:
            results, ok = self._ctx.run_batch(req.get("ops", []), req.get("undo_step", True))
        except Exception as e:
            # Every request gets an answer; a silent failure makes the caller wait out its timeout.
            self.log_message("Hermes batch failed: %s" % traceback.format_exc())
            results, ok = [{"ok": False, "error": "%s: %s" % (type(e).__name__, e)}], False
        return {
            "id": rid,
            "ok": ok,
            "results": results,
            "exec_ms": round((time.perf_counter() - t0) * 1000, 3),
        }

    def _reload(self):
        """Development: reload ops and bridge code in place, keeping the socket and clients."""
        import sys

        self._ctx.detach_listeners()
        package = importlib.reload(sys.modules[__package__])
        package.load_ops()
        module = importlib.reload(sys.modules[__package__ + ".bridge"])
        self.__class__ = module.Hermes
        self._ctx = module.ops.Context(self)
        self._ctx.attach_listeners()
        self.log_message("Hermes: reloaded")
        return {"ok": True, "reloaded": True}

    def _send(self, client, msg):
        client.outbuf += (json.dumps(msg, separators=(",", ":"), default=str) + "\n").encode()
        self._flush(client)

    def _flush(self, client):
        while client.outbuf:
            try:
                n = client.conn.send(client.outbuf)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self._drop(client)
                return
            client.outbuf = client.outbuf[n:]

    def _drop(self, client):
        try:
            client.conn.close()
        except OSError:
            pass
        if client in self._clients:
            self._clients.remove(client)

    def emit(self, event):
        """Push an event to subscribed clients (called from Live listeners)."""
        msg = dict(event, event=True, t=time.time())
        for client in list(self._clients):
            if client.subscribed:
                self._send(client, msg)

    # --- Live control surface hooks --------------------------------------

    def update_display(self):
        ControlSurface.update_display(self)
        now = time.perf_counter()
        self._ticks.append(now)
        if len(self._ticks) > 200:
            del self._ticks[:100]
        try:
            self._poll()
        except Exception:
            self.log_message("Hermes poll error: %s" % traceback.format_exc())

    def tick_stats(self):
        t = self._ticks
        gaps = [(b - a) * 1000 for a, b in zip(t, t[1:])]
        if not gaps:
            return {"samples": 0}
        gaps.sort()
        return {
            "samples": len(gaps),
            "median_ms": round(gaps[len(gaps) // 2], 2),
            "p95_ms": round(gaps[int(len(gaps) * 0.95) - 1], 2),
            "max_ms": round(gaps[-1], 2),
        }

    def build_midi_map(self, midi_map_handle):
        ControlSurface.build_midi_map(self, midi_map_handle)
        script = self._c_instance.handle()
        for n in range(128):
            Live.MidiMap.forward_midi_note(script, midi_map_handle, PERF_CHANNEL, n)
            Live.MidiMap.forward_midi_cc(script, midi_map_handle, PERF_CHANNEL, n)

    def receive_midi(self, midi_bytes):
        status = midi_bytes[0]
        if (status & 0x0F) == PERF_CHANNEL and len(midi_bytes) == 3:
            kind = status & 0xF0
            if kind == 0x90 and midi_bytes[2] > 0:
                self._ctx.perform(self._perf["note"].get(midi_bytes[1]), midi_bytes[2])
                return
            if kind == 0xB0:
                self._ctx.perform(self._perf["cc"].get(midi_bytes[1]), midi_bytes[2])
                return
            if kind in (0x80, 0x90):
                return
        ControlSurface.receive_midi(self, midi_bytes)

    def set_perf_bindings(self, bindings):
        self._perf = {
            "note": {int(k): v for k, v in bindings.get("note", {}).items()},
            "cc": {int(k): v for k, v in bindings.get("cc", {}).items()},
        }

    def disconnect(self):
        self._ctx.detach_listeners()
        for client in list(self._clients):
            self._drop(client)
        if self._server is not None:
            self._server.close()
            self._server = None
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
        ControlSurface.disconnect(self)
