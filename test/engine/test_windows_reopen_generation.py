from test_windows_sets import S, windows_sets
from types import SimpleNamespace


def test_manual_same_name_set_switch_invalidates_recorded_path(windows_sets, monkeypatch, tmp_path):
    W, ui, main, windows = windows_sets
    main.name = 'Song - Ableton Live 12 Suite'
    path = tmp_path/'Song.als'; path.write_bytes(b'saved')
    S._windows_opened = (ui.snapshot().identity, 'Song', str(path), ('old', 1))
    monkeypatch.setattr(S, '_endpoint_identity', lambda: ('new', 2))
    assert S._current_path() is None


def test_same_set_reopen_accepts_rotated_endpoint_without_observing_disconnect(windows_sets, monkeypatch, tmp_path):
    W, ui, main, windows = windows_sets
    main.name='Song - Ableton Live 12 Suite'
    path=tmp_path/'Song.als';path.write_bytes(b'saved')
    S._windows_opened=(ui.snapshot().identity,'Song',str(path))
    ids=iter([('old',1),('new',2)])
    monkeypatch.setattr(S,'_endpoint_identity',lambda: next(ids,('new',2)),raising=False)
    monkeypatch.setattr(S.live_app,'bundle',lambda:tmp_path/'Live.exe')
    monkeypatch.setattr(S.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(poll=lambda:0))
    monkeypatch.setattr(S.live_client,'available',lambda:True)
    monkeypatch.setattr(S.live_client,'batch',lambda *a,**kw:{'ok':True,'results':[{'tracks':4}]})
    result=S._open_windows(str(path),'cancel',timeout=0.1)
    assert result['path']==str(path)
