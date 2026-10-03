"""Real OS transport tests; only the Live API is faked by conftest."""
import importlib.util
import json
import os
from pathlib import Path
import socket

import pytest

from Hermes import bridge


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Keep Unix socket paths below sockaddr_un's platform-dependent limit.
    return tmp_path_factory.mktemp("t")


def load_transport():
    path = Path(bridge.__file__).with_name("transport.py")
    assert path.exists(), "shared secure transport module is missing"
    spec = importlib.util.spec_from_file_location("aae_transport_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_surface(directory, ctx):
    surface = bridge.Hermes.__new__(bridge.Hermes)
    surface._clients = []
    surface._ctx = ctx
    surface.log_message = lambda *_: None
    surface._open_server(str(directory))
    return surface


def connect_peer(surface):
    listener = surface._listener
    peer = socket.socket(listener.socket.family, socket.SOCK_STREAM)
    peer.settimeout(1)
    peer.connect(listener.socket.getsockname())
    return peer


def exchange(surface, peer, request):
    # No token means an unauthenticated raw request, deliberately exercising rejection.
    token = request.pop("token", None)
    peer.sendall(bridge.transport.encode_message(request, token))
    surface._poll()
    return bridge.transport.decode_message(peer.recv(65536), surface._listener.token, "response")


def close_surface(surface):
    for client in list(surface._clients):
        surface._drop(client)
    surface._listener.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows capability transport")
@pytest.mark.parametrize("body", [{"ops": [{"op": "transport", "tempo": 99}]}, {"subscribe": True}, {"reload": True}])
def test_real_loopback_rejects_unauthenticated_before_dispatch(tmp_path, ctx, body):
    surface = make_surface(tmp_path / "private", ctx)
    try:
        with connect_peer(surface) as peer:
            result = exchange(surface, peer, dict(body, id=1))
            assert result.get("error") == "authentication required"
            assert not surface._clients[0].subscribed
            assert ctx.song.tempo != 99
            good = exchange(surface, peer, {"id": 2, "token": surface._listener.token, "subscribe": True})
            assert good["ok"] and surface._clients[0].subscribed
            bad = exchange(surface, peer, {"id": 3, "reload": True, "token": "00" * 32})
            assert bad["error"] == "authentication required"
    finally:
        close_surface(surface)


def load_client(directory, monkeypatch):
    path = Path(bridge.__file__).parents[2] / "live_client.py"
    spec = importlib.util.spec_from_file_location("aae_client_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "transport"), "client must share transport without importing Live"
    monkeypatch.setattr(module.transport, "DIRECTORY", str(directory))
    return module


def pump_call(surface, call):
    import concurrent.futures
    import time
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(call)
        until = time.monotonic() + 3
        while not result.done() and time.monotonic() < until:
            surface._poll()
            time.sleep(.001)
        return result.result(timeout=1)


def test_python_client_authenticated_batch_over_real_transport(tmp_path, ctx, monkeypatch):
    directory = tmp_path / "private"
    surface = make_surface(directory, ctx)
    try:
        client = load_client(directory, monkeypatch)
        assert client.available()
        result = pump_call(surface, lambda: client.batch([{"op": "transport", "tempo": 99}], timeout=1))
        assert result["ok"]
        assert result["results"][0]["ok"]
        assert ctx.song.tempo == 99
    finally:
        close_surface(surface)


@pytest.mark.parametrize("failure", ["eof", "malformed", "oversize", "trickle"])
def test_python_post_send_failures_report_unknown_outcome(tmp_path, monkeypatch, failure):
    import concurrent.futures
    import time
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    listener.socket.settimeout(2)
    client = load_client(directory, monkeypatch)
    monkeypatch.setattr(client.transport, "MAX_FRAME", 1024)

    def serve():
        conn, _ = listener.socket.accept()
        with conn:
            conn.settimeout(1)
            buf = b""
            while b"\n" not in buf:
                buf += conn.recv(4096)
            if failure == "malformed":
                conn.sendall(b"not json\n")
            elif failure == "oversize":
                conn.sendall(b"x" * 1025)
            elif failure == "trickle":
                for _ in range(30):
                    try:
                        conn.sendall(transport.encode_message({"event": True}, listener.token, "response"))
                    except OSError:
                        break
                    time.sleep(.01)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(serve)
            start = time.monotonic()
            with pytest.raises(Exception, match="outcome unknown.*inspect before retrying"):
                client.batch([{"op": "ping"}], timeout=.08)
            elapsed = time.monotonic() - start
            job.result(timeout=2)
            if failure == "trickle":
                assert elapsed < .25, "timeout must cover the entire operation, not each recv"
    finally:
        listener.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows authenticated response")
def test_stale_port_impersonator_cannot_learn_token_or_forge_response(tmp_path, monkeypatch):
    import concurrent.futures
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    listener.socket.settimeout(2)
    client = load_client(directory, monkeypatch)
    captured = []

    def impersonate():
        conn, _ = listener.socket.accept()
        with conn:
            raw = b""
            while b"\n" not in raw:
                raw += conn.recv(4096)
            captured.append(raw)
            req = json.loads(raw)
            body = json.loads(req["body"]) if "body" in req else req
            conn.sendall(json.dumps({"id": body["id"], "ok": True, "results": []}).encode() + b"\n")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(impersonate)
            with pytest.raises(client.LiveOutcomeUnknown):
                client.batch([{"op": "ping"}], timeout=1)
            job.result(timeout=2)
        assert listener.token.encode() not in captured[0], "capability must never travel on wire"
    finally:
        listener.close()


def test_python_partial_send_timeout_reports_unknown_outcome(tmp_path, monkeypatch):
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    listener.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    client = load_client(directory, monkeypatch)
    try:
        # Real connection, intentionally unread: enough bytes to fill OS send
        # buffers. This fails within sendall, not while awaiting a response.
        with pytest.raises(client.LiveOutcomeUnknown, match="outcome unknown"):
            client.batch([{"op": "ping", "padding": "x" * (2 * 1024 * 1024)}], timeout=.5)
    finally:
        listener.close()


@pytest.mark.parametrize("terminated", [False, True])
def test_bridge_rejects_oversized_input_frame(tmp_path, ctx, monkeypatch, terminated):
    surface = make_surface(tmp_path / "private", ctx)
    monkeypatch.setattr(bridge, "MAX_LINE", 128)
    try:
        with connect_peer(surface) as peer:
            peer.sendall(b"x" * 129 + (b"\n" if terminated else b""))
            surface._poll()
            assert not surface._clients
    finally:
        close_surface(surface)


def test_bridge_partial_input_is_nonblocking_and_idle_peers_expire(tmp_path, ctx, monkeypatch):
    import time
    surface = make_surface(tmp_path / "private", ctx)
    try:
        with connect_peer(surface) as peer:
            peer.sendall(b'{"body":')
            start = time.monotonic()
            surface._poll()
            assert time.monotonic() - start < .1
            assert surface._clients
            surface._clients[0].last_activity -= bridge.IDLE_TIMEOUT + 1
            surface._poll()
            assert not surface._clients
    finally:
        close_surface(surface)


def test_request_mac_cannot_be_used_as_response(tmp_path):
    transport = load_transport()
    token = __import__("secrets").token_hex(32)
    frame = transport.encode_message({"id": 1, "ops": []}, token)
    assert token.encode() not in frame
    with pytest.raises(transport.AuthenticationError):
        transport.decode_message(frame, token, "response")
    envelope = json.loads(frame)
    envelope["body"] = '{"id":1,"reload":true}'
    with pytest.raises(transport.AuthenticationError):
        transport.decode_message(json.dumps(envelope), token)


def test_bridge_bounds_outgoing_frames(tmp_path, ctx, monkeypatch):
    surface = make_surface(tmp_path / "private", ctx)
    monkeypatch.setattr(bridge, "MAX_LINE", 128)
    try:
        with connect_peer(surface) as peer:
            surface._poll()
            client = surface._clients[0]
            surface._send(client, {"huge": "x" * 129})
            assert client not in surface._clients
            assert peer.recv(1) == b""
    finally:
        close_surface(surface)


def test_bridge_bounds_client_count(tmp_path, ctx, monkeypatch):
    surface = make_surface(tmp_path / "private", ctx)
    monkeypatch.setattr(bridge, "MAX_CLIENTS", 2, raising=False)
    peers = []
    try:
        for _ in range(3):
            peers.append(connect_peer(surface))
            surface._poll()
        assert len(surface._clients) == 2
        assert peers[-1].recv(1) == b""
    finally:
        for peer in peers:
            peer.close()
        close_surface(surface)


def test_bridge_frame_limit_is_per_frame_not_per_recv(tmp_path, ctx, monkeypatch):
    surface = make_surface(tmp_path / "private", ctx)
    try:
        with connect_peer(surface) as peer:
            frame = bridge.transport.encode_message({"id": 1, "subscribe": True, "padding": "x" * 200}, surface._listener.token)
            monkeypatch.setattr(bridge, "MAX_LINE", len(frame) + 16)
            peer.sendall(frame + frame)
            surface._poll()
            buf = b""
            while buf.count(b"\n") < 2:
                part = peer.recv(4096)
                assert part, "coalesced valid frames must not be discarded"
                buf += part
            assert all(bridge.transport.decode_message(line, surface._listener.token, "response")["ok"] for line in buf.splitlines())
    finally:
        close_surface(surface)


@pytest.mark.skipif(os.name != "nt", reason="Windows endpoint schema")
@pytest.mark.parametrize("change", [
    {"host": "0.0.0.0"}, {"host": "localhost"}, {"host": "192.0.2.1"},
    {"port": True}, {"port": 0}, {"port": 65536}, {"port": "1234"},
    {"protocol": True}, {"protocol": 2}, {"transport": "unix"},
    {"token": "bad"}, {"token": None}, {"auth": "none"},
])
def test_reject_invalid_endpoints(tmp_path, change):
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    try:
        endpoint = transport.read_endpoint(str(directory))
        endpoint.update(change)
        Path(listener.path).write_text(json.dumps(endpoint))
        with pytest.raises(ValueError):
            transport.read_endpoint(str(directory))
    finally:
        listener.close()


@pytest.mark.skipif(os.name != "nt", reason="native Windows DACL")
@pytest.mark.parametrize("target", ["directory", "endpoint"])
def test_actual_windows_acl_rejects_other_users(tmp_path, target):
    import subprocess
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    try:
        path = str(directory) if target == "directory" else listener.path
        transport.validate_private(path, directory=target == "directory")
        subprocess.run(["icacls", path, "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
        with pytest.raises(PermissionError):
            transport.read_endpoint(str(directory))
    finally:
        listener.close()


def test_second_listener_cannot_steal_endpoint(tmp_path):
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    try:
        before = transport.read_endpoint(str(directory))
        with pytest.raises(OSError):
            transport.Listener(str(directory))
        assert transport.read_endpoint(str(directory)) == before
        with socket.socket(listener.socket.family, socket.SOCK_STREAM) as peer:
            peer.connect(listener.socket.getsockname())
    finally:
        listener.close()
    with_listener = transport.Listener(str(directory))
    with_listener.close()


@pytest.mark.skipif(os.name != "nt", reason="exclusive Windows TCP bind")
def test_exclusive_bind_prevents_port_hijack(tmp_path):
    transport = load_transport()
    listener = transport.Listener(str(tmp_path / "private"))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as attacker:
            attacker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                attacker.bind(listener.socket.getsockname())
    finally:
        listener.close()


def test_crashed_process_lock_and_endpoint_recover(tmp_path):
    import subprocess
    import sys
    transport = load_transport()
    directory = tmp_path / "private"
    script = (
        "import importlib.util,sys,os; "
        "s=importlib.util.spec_from_file_location('transport',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "listener=m.Listener(sys.argv[2]); print('ready',flush=True); "
        "sys.stdin.readline(); os._exit(0)"
    )
    child = subprocess.Popen([sys.executable, "-c", script, transport.__file__, str(directory)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        previous = transport.read_endpoint(str(directory))
        with pytest.raises(OSError):
            transport.Listener(str(directory))
        child.communicate("exit\n", timeout=3)
        assert child.returncode == 0
        recovered = transport.Listener(str(directory))
        try:
            current = transport.read_endpoint(str(directory))
            if os.name == "nt":
                assert previous["token"] != current["token"]
        finally:
            recovered.close()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


def test_cleanup_does_not_delete_replaced_endpoint(tmp_path):
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    path = Path(listener.path)
    replacement = directory / "replacement"
    replacement.write_text("not owned by listener")
    os.replace(replacement, path)
    listener.close()
    assert path.read_text() == "not owned by listener"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket compatibility")
def test_unix_does_not_unlink_active_legacy_listener(tmp_path):
    transport = load_transport()
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = str(directory / "live.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as legacy:
        legacy.bind(path)
        os.chmod(path, 0o600)
        legacy.listen(1)
        unexpected = None
        try:
            with pytest.raises(OSError, match="already"):
                unexpected = transport.Listener(str(directory))
        finally:
            if unexpected:
                unexpected.close()
        assert Path(path).exists()


@pytest.mark.skipif(os.name == "nt", reason="Unix ownership and mode")
def test_unix_rejects_public_directory(tmp_path):
    transport = load_transport()
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(PermissionError):
        transport.Listener(str(directory))


def test_real_listener_publishes_private_endpoint_and_cleans_up(tmp_path):
    transport = load_transport()
    directory = tmp_path / "private"
    listener = transport.Listener(str(directory))
    try:
        endpoint = transport.read_endpoint(str(directory))
        if os.name == "nt":
            assert endpoint["host"] == "127.0.0.1"
            assert 0 < endpoint["port"] < 65536
            assert len(endpoint["token"]) == 64
            assert listener.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE)
            peer = socket.create_connection((endpoint["host"], endpoint["port"]), timeout=1)
        else:
            assert (directory.stat().st_mode & 0o777) == 0o700
            assert (Path(endpoint["path"]).stat().st_mode & 0o777) == 0o600
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.connect(endpoint["path"])
        with peer:
            accepted, _ = listener.socket.accept()
            with accepted:
                peer.sendall(b"hello")
                assert accepted.recv(5) == b"hello"
    finally:
        listener.close()
    assert not (directory / ("live.endpoint.json" if os.name == "nt" else "live.sock")).exists()
