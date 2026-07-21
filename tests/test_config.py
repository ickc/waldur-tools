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


def test_missing_token_explains_itself(monkeypatch, tmp_path):
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
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
