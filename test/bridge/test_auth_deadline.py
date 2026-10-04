"""Unauthenticated clients cannot retain all bridge slots by trickling bytes."""
import os
import pytest
from Hermes import bridge
from test_transport import make_surface, connect_peer, close_surface


@pytest.mark.skipif(os.name != 'nt', reason='Windows authentication')
def test_absolute_auth_deadline_reclaims_slots_before_accept(tmp_path, ctx, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])
    surface = make_surface(tmp_path / 'private', ctx)
    peers = []
    try:
        for _ in range(bridge.MAX_CLIENTS):
            p = connect_peer(surface)
            peers.append(p)
        surface._poll()
        clock[0] += 0.9
        for p in peers:
            p.sendall(b' ')
        surface._poll()
        clock[0] += 1.1
        good = connect_peer(surface)
        peers.append(good)
        good.sendall(bridge.transport.encode_message({'id': 5, 'ops': [{'op':'ping'}]}, surface._listener.token))
        surface._poll()
        result = bridge.transport.decode_message(good.recv(65536), surface._listener.token, 'response')
        assert result['ok']
        assert len(surface._clients) == 1
    finally:
        for p in peers:
            p.close()
        close_surface(surface)
