"""Client for the Hermes control surface running inside Ableton Live."""

import importlib.util
import itertools
import secrets
import socket
import time
from pathlib import Path

# Do not import the Hermes package: its __init__ requires Live's embedded API.
_spec = importlib.util.spec_from_file_location(
    "_aae_transport", Path(__file__).parent / "live" / "Hermes" / "transport.py"
)
transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transport)
SOCK_PATH = transport.SOCK_PATH
ENDPOINT_PATH = transport.ENDPOINT_PATH
_ids = itertools.count(1)


class LiveUnavailable(Exception):
    pass


class LiveOutcomeUnknown(TimeoutError):
    """A request may have executed. Never automatically retry a mutation."""


def available():
    """Valid private discovery exists; use ping to establish Live is responsive."""
    try:
        transport.read_endpoint()
        return True
    except (OSError, ValueError):
        return False


def batch(ops, timeout=30.0, undo_step=True):
    """Run ops as one batch inside Live (one undo step) and return the response."""
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    deadline = time.monotonic() + timeout
    try:
        endpoint = transport.read_endpoint()
    except (OSError, ValueError) as e:
        raise LiveUnavailable(
            "Ableton Live has no valid private endpoint. Run `hermes assistant-engineer setup`, open Live, and choose "
            '"Hermes" under Settings > Tempo & MIDI > Control Surface; `hermes assistant-engineer status` checks it.'
        ) from e
    rid = next(_ids)
    request = {"id": rid, "ops": ops, "undo_step": undo_step}
    token = endpoint.get("token")
    payload = transport.encode_message(request, token)
    if len(payload) - 1 > transport.MAX_FRAME:
        raise ValueError("Live request exceeds frame limit; nothing sent")
    with socket.socket(socket.AF_INET if transport.WINDOWS else socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((endpoint["host"], endpoint["port"]) if transport.WINDOWS else endpoint["path"])
        except OSError as e:
            raise LiveUnavailable("cannot reach Live: %s" % e) from e
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LiveUnavailable("Live connection deadline expired; nothing sent")
        if token is not None:
            # Prove the peer owns the endpoint key before sending any ops. The
            # fresh challenge also rejects replayed handshake acknowledgements.
            nonce = secrets.token_hex(32)
            try:
                s.settimeout(remaining)
                s.sendall(transport.encode_message({"id": rid, "hello": nonce}, token))
                hello = b""
                while b"\n" not in hello:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Live handshake deadline expired")
                    s.settimeout(remaining)
                    chunk = s.recv(4097 - len(hello))
                    if not chunk:
                        raise ConnectionError("Live closed the handshake")
                    hello += chunk
                    if len(hello) > 4096:
                        raise ValueError("Live handshake exceeds frame limit")
                reply = transport.decode_message(hello.split(b"\n", 1)[0], token, "response")
                if reply.get("id") != rid or reply.get("ok") is not True or reply.get("hello") != nonce:
                    raise ValueError("invalid Live handshake")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Live handshake deadline expired")
            except (OSError, ValueError) as e:
                raise LiveUnavailable("Live authentication failed; no request sent") from e
        try:
            s.settimeout(remaining)
            s.sendall(payload)
            buf = b""
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Live request deadline expired")
                s.settimeout(remaining)
                chunk = s.recv(min(1 << 20, transport.MAX_FRAME + 1 - len(buf)))
                if not chunk:
                    raise ConnectionError("Live closed the connection")
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if len(line) > transport.MAX_FRAME:
                        raise ValueError("Live response exceeds frame limit")
                    msg = transport.decode_message(line, token, "response")
                    if msg.get("id") == rid:
                        return msg
                if len(buf) > transport.MAX_FRAME:
                    raise ValueError("Live response exceeds frame limit")
        except (OSError, ValueError) as e:
            # sendall can fail AFTER a partial or complete write. EOF, invalid
            # replies and receive timeouts are likewise not safe-to-retry failures.
            raise LiveOutcomeUnknown("Live request failed; outcome unknown, inspect before retrying") from e
