"""Real bridge integration peer for Bun tests; only Live's API is faked."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test" / "bridge"))
sys.path.insert(0, str(ROOT / "plugin" / "actual-assistant-engineer" / "live"))
import fake_live
fake_live.install()
import Hermes
from Hermes import bridge, ops

Hermes.load_ops()
surface = bridge.Hermes.__new__(bridge.Hermes)
surface._clients = []
song = fake_live.Song(tracks=2, scenes=4)
surface.song = lambda: song
surface._ctx = ops.Context(surface)
surface.log_message = lambda *_: None
surface._open_server(sys.argv[1])
print(surface._listener.path, flush=True)
try:
    mode = sys.argv[2] if len(sys.argv) > 2 else "bridge"
    if mode != "bridge":
        surface._server.settimeout(5)
        conn, _ = surface._server.accept()
        with conn:
            conn.settimeout(5)
            raw = b""
            if mode == "backpressure":
                raw = conn.recv(4096)
                time.sleep(.5)
            while b"\n" not in raw:
                try:
                    chunk = conn.recv(65536)
                except ConnectionResetError:
                    break  # Bun terminate() aborts the partially written socket.
                if not chunk:
                    break
                raw += chunk
            if mode == "backpressure":
                assert raw and b"\n" not in raw, "expected a real partial socket write"
            if mode == "malformed":
                conn.sendall(b"not-json\n")
            elif mode == "oversize":
                conn.sendall(b"x" * 2049)
            elif mode == "timeout":
                time.sleep(.3)
            elif mode == "forged":
                conn.sendall(b'{"id":1,"ok":true,"results":[]}\n')
            elif mode == "reflection":
                conn.sendall(raw)
            elif mode == "fragmented":
                request = bridge.transport.decode_message(raw, surface._listener.token)
                event = bridge.transport.encode_message({"event": True, "kind": "test"}, surface._listener.token, "response")
                reply = bridge.transport.encode_message({"id": request["id"], "ok": True, "results": [{"ok": True, "label": "Drüms 🎵"}]}, surface._listener.token, "response")
                wire = event + reply
                for i in range(0, len(wire), 3):
                    conn.sendall(wire[i:i+3])
        surface._server.setblocking(False)
    while True:
        surface._poll()
        time.sleep(.001)
finally:
    for client in list(surface._clients):
        surface._drop(client)
    surface._listener.close()
