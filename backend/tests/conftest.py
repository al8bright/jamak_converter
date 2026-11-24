from __future__ import annotations

import os
from pathlib import Path

import pytest

DATA = Path(__file__).parent / ".data"


def pytest_configure(config):
    config.addinivalue_line("markers", "gpu: needs CUDA + models (slow); run with -m gpu")


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="GPU/model test; run with: uv run pytest -m gpu")
    for item in items:
        if "gpu" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def jamak_home(tmp_path, monkeypatch):
    """Isolated runtime folder (jobs, cache) for a test."""
    home = tmp_path / "home"
    monkeypatch.setenv("JAMAK_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def dialogue_video() -> tuple[Path, Path]:
    from tests.fixtures_gen import synth

    return synth(DATA)


@pytest.fixture(scope="session")
def real_home() -> Path:
    """The real runtime folder (llama-server, models) for GPU tests."""
    from jamak.config import paths

    os.environ.pop("JAMAK_HOME", None)
    return paths.home
