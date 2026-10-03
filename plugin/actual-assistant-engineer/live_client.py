"""Client for the Hermes control surface running inside Ableton Live."""

import itertools
import json
import os
import socket

SOCK_PATH = os.path.expanduser("~/Library/Application Support/ActualAssistantEngineer/live.sock")
_ids = itertools.count(1)


class LiveUnavailable(Exception):
    pass


def available():
    return os.path.exists(SOCK_PATH)


def batch(ops, timeout=30.0, undo_step=True):
    """Run ops as one batch inside Live (one undo step) and return the response."""
    if not available():
        raise LiveUnavailable(
            "Ableton Live isn't connected. Ask the producer to run `hermes aae setup` once, open Live, and choose "
            '"Hermes" under Settings > Tempo & MIDI > Control Surface; `hermes aae status` checks it.'
        )
    rid = next(_ids)
    payload = (json.dumps({"id": rid, "ops": ops, "undo_step": undo_step}) + "\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(SOCK_PATH)
        except OSError as e:
            raise LiveUnavailable("cannot reach Live: %s" % e)
        s.sendall(payload)
        buf = b""
        while True:
            try:
                chunk = s.recv(1 << 20)
            except socket.timeout:
                # The batch may still have run; callers must inspect before retrying.
                raise TimeoutError("Live did not answer within %ss; outcome unknown, inspect before retrying" % timeout)
            if not chunk:
                raise LiveUnavailable("Live closed the connection")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("id") == rid:
                    return msg
