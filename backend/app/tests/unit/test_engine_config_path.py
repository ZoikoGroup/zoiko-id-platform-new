"""The face-engine config files are found the way every other config file is: through ZID_PLATFORM_CONFIG, which the deployed image sets to /app/config.

Before this, only `ZID_SELF_RUN_CONFIG` and `ZID_ENGINE_REGISTER` were honoured, so in the image (where the package sits under site-packages) the files resolved to a
folder that does not exist and the engine readiness answer failed. A change of environment is read at import, like the other loaders, so each case reloads the module.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from zid_platform.modules.verification.infrastructure import engine_register, self_run_config


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(engine_register)  # back to the environment the rest of the suite expects
    importlib.reload(self_run_config)


def _tree(root: Path) -> Path:
    folder = root / "self-run-verification"
    folder.mkdir(parents=True)
    (folder / "self-run.yaml").write_text("mode: LAB_ONLY\n", encoding="utf-8")
    (folder / "engine-register.yaml").write_text("version: t\ncomponents: []\n", encoding="utf-8")
    return root


def test_the_config_root_environment_finds_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path / "cfg")
    monkeypatch.delenv("ZID_SELF_RUN_CONFIG", raising=False)
    monkeypatch.delenv("ZID_ENGINE_REGISTER", raising=False)
    monkeypatch.setenv("ZID_PLATFORM_CONFIG", str(root))
    importlib.reload(engine_register)
    importlib.reload(self_run_config)
    assert self_run_config.raw() == {"mode": "LAB_ONLY"}
    assert engine_register.register()["version"] == "t"


def test_a_named_file_still_wins_over_the_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path / "cfg")
    other = tmp_path / "other.yaml"
    other.write_text("mode: DISABLED\n", encoding="utf-8")
    monkeypatch.setenv("ZID_PLATFORM_CONFIG", str(root))
    monkeypatch.setenv("ZID_SELF_RUN_CONFIG", str(other))
    importlib.reload(self_run_config)
    assert self_run_config.raw() == {"mode": "DISABLED"}


def test_without_any_environment_the_repository_config_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ZID_PLATFORM_CONFIG", "ZID_SELF_RUN_CONFIG", "ZID_ENGINE_REGISTER"):
        monkeypatch.delenv(name, raising=False)
    importlib.reload(engine_register)
    importlib.reload(self_run_config)
    assert self_run_config.FILE.name == "self-run.yaml" and self_run_config.FILE.exists()
    assert engine_register.FILE.name == "engine-register.yaml" and engine_register.FILE.exists()
