from __future__ import annotations

import re

import pytest

from waldur_tools.config import MissingTokenError, Settings, resolve_token


def test_env_token_wins(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("from-file")
    monkeypatch.setenv("WALDUR_API_TOKEN", "from-env")
    monkeypatch.setenv("WALDUR_TOKEN_FILE", str(token_file))
    assert resolve_token() == "from-env"


def test_token_file_fallback(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("  from-file\n")
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.setenv("WALDUR_TOKEN_FILE", str(token_file))
    assert resolve_token() == "from-file"


def test_envrc_local_is_read_when_the_shell_did_not_source_it(monkeypatch, tmp_path):
    """What makes the documented workflow work on Windows -- see `_token_from_envrc`."""
    (tmp_path / ".envrc.local").write_text(
        "# a comment\n"
        "export WALDUR_API_URL=https://example.test\n"
        "export WALDUR_API_TOKEN=stale-token\n"
        "export WALDUR_API_TOKEN='fresh-token'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.delenv("WALDUR_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PIXI_PROJECT_ROOT", str(tmp_path))
    # The last assignment wins, as it would in the shell that usually reads it.
    assert resolve_token() == "fresh-token"


def test_token_file_beats_envrc_local(monkeypatch, tmp_path):
    (tmp_path / ".envrc.local").write_text("export WALDUR_API_TOKEN=from-envrc\n", encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("from-file")
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.setenv("WALDUR_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PIXI_PROJECT_ROOT", str(tmp_path))
    assert resolve_token() == "from-file"


def test_an_envrc_local_naming_no_token_is_not_a_token(monkeypatch, tmp_path):
    (tmp_path / ".envrc.local").write_text("export WALDUR_TIMEOUT=5\n", encoding="utf-8")
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.delenv("WALDUR_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PIXI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "platformdirs.user_config_dir", lambda *_args, **_kwargs: str(tmp_path / "nowhere")
    )
    with pytest.raises(MissingTokenError):
        resolve_token()


def test_missing_token_explains_itself(monkeypatch, tmp_path):
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.delenv("PIXI_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("WALDUR_TOKEN_FILE", str(tmp_path / "absent"))
    monkeypatch.setattr(
        "platformdirs.user_config_dir", lambda *_args, **_kwargs: str(tmp_path / "nowhere")
    )
    with pytest.raises(MissingTokenError, match=re.escape(".envrc.local")):
        resolve_token()


def test_repr_hides_the_token(settings):
    rendered = repr(settings)
    assert "secret-token" not in rendered
    assert "<redacted>" in rendered


def test_from_env_reads_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("WALDUR_API_TOKEN", "t")
    monkeypatch.setenv("WALDUR_CACHE_DIR", str(tmp_path / "snaps"))
    monkeypatch.setenv("WALDUR_API_URL", "https://example.test/")
    settings = Settings.from_env()
    assert settings.cache_dir == tmp_path / "snaps"
    assert settings.api_url == "https://example.test"
