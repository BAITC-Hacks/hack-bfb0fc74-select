"""Keep automated tests isolated from a developer's real API credentials."""

import pytest

import config


@pytest.fixture(autouse=True)
def ignore_project_local_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOCAL_ENV", tmp_path / ".env")
