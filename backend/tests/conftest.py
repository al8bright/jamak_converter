from __future__ import annotations

import os
import sys
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


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch):
    """On a Mac, tests must not write API keys into the login Keychain: use a dict instead."""
    if sys.platform != "darwin":
        return
    from jamak.core import secrets

    items: dict[tuple[str, str], str] = {}

    class Memory:
        def set_password(self, service, user, value):
            items[(service, user)] = value

        def get_password(self, service, user):
            return items.get((service, user))

        def delete_password(self, service, user):
            items.pop((service, user))

    monkeypatch.setattr(secrets, "_keychain", Memory)


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
