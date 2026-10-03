import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "plugin" / "actual-assistant-engineer" / "live"))

import fake_live  # noqa: E402

fake_live.install()

import Hermes  # noqa: E402
from Hermes import ops  # noqa: E402

Hermes.load_ops()


@pytest.fixture
def song():
    return fake_live.Song(tracks=2, scenes=4)


@pytest.fixture
def ctx(song):
    return ops.Context(fake_live.FakeSurface(song))


@pytest.fixture
def run(ctx):
    """run(op_dicts...) -> (results, ok) through the real batch executor."""
    return lambda *batch, undo=True: ctx.run_batch(list(batch), undo)
