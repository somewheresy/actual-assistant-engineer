"""Live 12 Windows uses native child panes for some modal dialogs."""
from test_windows_sets import Node, ui_for, windows_module


def test_native_child_pane_dialog_blocks_menu_automation():
    W = windows_module()
    pane = Node('Ableton Live 12 Trial', kind='Pane', children=[Node('', 'TitleBar'), Node('Start your free trial', 'Button')])
    pane.element_info.handle = 200
    main = Node('Untitled - Ableton Live 12 Trial', children=[pane])
    ui, _ = ui_for(W, [main])
    state = ui.snapshot()
    assert len(state.dialogs) == 1
    assert state.dialogs[0].buttons == ('Start your free trial',)
    import pytest
    with pytest.raises(W.UIError, match='no dialogs'):
        ui.menu('Save Live Set')
