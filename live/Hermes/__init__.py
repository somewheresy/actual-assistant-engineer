import importlib

from . import bridge, ops

# Op modules beyond the core; each registers its ops into ops.OPS on import.
EXTENSIONS = ("ops_lom", "ops_mix", "ops_automation", "ops_review", "ops_devices")


def load_ops():
    """(Re)load the core and every extension so new code takes effect without restarting Live."""
    importlib.reload(ops)
    for name in EXTENSIONS:
        try:
            module = importlib.import_module("." + name, __name__)
        except ModuleNotFoundError:
            continue
        importlib.reload(module)


def create_instance(c_instance):
    # Live caches modules across control-surface reselection; reload so new code takes effect.
    load_ops()
    importlib.reload(bridge)
    return bridge.Hermes(c_instance)
