"""The optional local key must work without leaking into repository settings."""

import config


def test_project_setting_reads_ignored_file_and_prefers_environment(tmp_path, monkeypatch):
    local_env = tmp_path / ".env"
    local_env.write_text("OPENAI_API_KEY='local-test-key'\nOPENAI_MODEL=gpt-4.1-mini\n")
    monkeypatch.setattr(config, "LOCAL_ENV", local_env)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert config.project_setting("OPENAI_API_KEY") == "local-test-key"
    assert config.project_setting("OPENAI_MODEL") == "gpt-4.1-mini"

    monkeypatch.setenv("OPENAI_API_KEY", "shell-test-key")
    assert config.project_setting("OPENAI_API_KEY") == "shell-test-key"
