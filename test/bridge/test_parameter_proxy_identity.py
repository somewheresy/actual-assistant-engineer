"""Live may return distinct Python proxies for the same DeviceParameter."""
import copy
from Hermes import ops_automation as A


def test_track_volume_units_do_not_depend_on_python_proxy_identity(ctx, monkeypatch):
    original = A.resolve_target
    monkeypatch.setattr(A, 'resolve_target', lambda track, target: copy.copy(original(track, target)))
    result = A._param_info(ctx, 0, 'volume')
    assert result['is_volume'] is True
    assert A._param_info(ctx, 0, 'pan')['is_volume'] is False
