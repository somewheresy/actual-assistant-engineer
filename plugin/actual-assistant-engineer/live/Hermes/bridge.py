"""Hermes control surface for Actual Assistant Engineer.

Runs inside Live's embedded Python. Serves newline-delimited JSON over a
private Unix socket or authenticated Windows loopback and executes ops on Live's main thread
during the control surface update tick. Performance gestures arrive as MIDI
on channel 16 and are executed immediately in receive_midi.
"""

import importlib
import socket
import time
import traceback

import Live
from _Framework.ControlSurface import ControlSurface

from . import ops, transport

SOCK_DIR = transport.DIRECTORY
SOCK_PATH = transport.SOCK_PATH
PROTOCOL = 1
PERF_CHANNEL = 15  # MIDI channel 16, zero-based
MAX_LINE = transport.MAX_FRAME
MAX_CLIENTS = 8
MAX_REQUESTS_PER_TICK = 8
SOCK_BUF = 4 * 1024 * 1024
IDLE_TIMEOUT = 30.0


class Client:
    def __init__(self, conn):
        self.conn = conn
        self.inbuf = b""
        self.outbuf = b""
        self.subscribed = False
        self.first_byte = None
        self.last_activity = time.monotonic()
        self.accepted_at = self.last_activity
        self.authenticated = False


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
        self.log_message("Hermes: listening via %s" % self._listener.path)

    # --- socket server -------------------------------------------------

    def _open_server(self, directory=None):
        self._listener = transport.Listener(directory)
        self._server = self._listener.socket

    def _poll(self):
        # Expire unauthenticated sockets before admission, even if they trickle
        # bytes. Local processes without the key must not retain all eight slots.
        for client in list(self._clients):
            if transport.WINDOWS and not client.authenticated and time.monotonic() - client.accepted_at > 1.0:
                self._drop(client)
        for _ in range(MAX_CLIENTS):
            try:
                conn, address = self._server.accept()
            except (BlockingIOError, InterruptedError):
                break
            if len(self._clients) >= MAX_CLIENTS or (transport.WINDOWS and address[0] != "127.0.0.1"):
                conn.close()
                continue
            conn.setblocking(False)
            # The default ~8KB buffers would spread one large batch across several 100ms ticks.
            for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
                try:
                    conn.setsockopt(socket.SOL_SOCKET, opt, SOCK_BUF)
                except OSError:
                    pass
            self._clients.append(Client(conn))
        for client in list(self._clients):
            if (not client.subscribed or client.inbuf) and time.monotonic() - client.last_activity > IDLE_TIMEOUT:
                self._drop(client)
                continue
            self._read(client)
            if client in self._clients:
                self._flush(client)

    def _read(self, client):
        # Bounded nonblocking work. Never wait on a network peer in Live's UI
        # thread; fragmented large batches continue on the next display tick.
        received = 0
        handled = 0
        while handled < MAX_REQUESTS_PER_TICK and client in self._clients:
            if b"\n" in client.inbuf:
                line, client.inbuf = client.inbuf.split(b"\n", 1)
                if len(line) > MAX_LINE:
                    self._drop(client)
                    return
                handled += 1
                if line.strip():
                    recv_ms = (time.perf_counter() - client.first_byte) * 1000 if client.first_byte else 0
                    client.first_byte = time.perf_counter() if client.inbuf else None
                    msg = self._handle(client, line)
                    msg["recv_ms"] = round(recv_ms, 1)
                    self._send(client, msg)
                continue
            if len(client.inbuf) > MAX_LINE:
                self._drop(client)
                return
            if received >= SOCK_BUF:
                return
            try:
                chunk = client.conn.recv(min(1 << 20, MAX_LINE + 1 - len(client.inbuf)))
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self._drop(client)
                return
            if not chunk:
                self._drop(client)
                return
            if not client.inbuf:
                client.first_byte = time.perf_counter()
            received += len(chunk)
            client.last_activity = time.monotonic()
            client.inbuf += chunk

    def _handle(self, client, line):
        t0 = time.perf_counter()
        try:
            req = transport.decode_message(line, self._listener.token)
        except transport.AuthenticationError:
            return {"ok": False, "error": "authentication required"}
        except ValueError as e:
            return {"ok": False, "error": "bad json: %s" % e}
        if not isinstance(req, dict):
            return {"ok": False, "error": "request must be an object"}
        client.authenticated = True
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
        frame = transport.encode_message(msg, self._listener.token, "response")
        if len(frame) > MAX_LINE + 1 or len(client.outbuf) + len(frame) > MAX_LINE + 1:
            self._drop(client)
            return
        client.outbuf += frame
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
            if n == 0:
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
        self._listener.close()
        self._server = None
        ControlSurface.disconnect(self)
