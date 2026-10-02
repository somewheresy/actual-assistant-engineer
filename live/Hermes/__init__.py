import importlib

from . import bridge, ops


def create_instance(c_instance):
    # Live caches modules across control-surface reselection; reload so new code takes effect.
    importlib.reload(ops)
    importlib.reload(bridge)
    return bridge.Hermes(c_instance)
