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


from jamak import models as _models  # noqa: E402

HOST_SPECS = list(_models.SPECS)  # the list this machine really uses (checked in test_mac.py)


@pytest.fixture(autouse=True)
def _windows_model_list(monkeypatch):
    """Tests are written against the Windows model list (CUDA, Qwen3-ASR, faster-whisper ids), so they
    give the same result on a Mac runner. Mac paths are tested on purpose in test_mac.py."""
    import jamak.installer as ins
    from jamak import hw

    win = _models.specs_for("win")
    monkeypatch.setattr(hw, "IS_MAC", False)
    monkeypatch.setattr(_models, "SPECS", win)
    monkeypatch.setattr(_models, "BY_ID", {s.id: s for s in win})
    monkeypatch.setattr(_models, "STT_IDS", [s.id for s in win if s.kind == "stt"])
    monkeypatch.setattr(_models, "LLM_IDS", [s.id for s in win if s.kind == "llm"])
    comps = [ins._component(s) for s in win]
    monkeypatch.setattr(ins, "COMPONENTS", comps)
    monkeypatch.setattr(ins, "_BY_ID", {c.id: c for c in comps})


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
