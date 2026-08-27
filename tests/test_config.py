from __future__ import annotations

import re
import stat
import sys

import pytest

from waldur_tools.config import (
    PRIVATE_FILE,
    InsecureApiUrlError,
    MissingTokenError,
    Settings,
    origin_of,
    resolve_token,
    restrict,
)

# Every token file below is written by hand in the test, so it inherits the
# runner's umask and would warn. The warning is asserted on in its own test.
pytestmark = pytest.mark.filterwarnings("ignore:.*readable by other users.*")

#: `chmod` on Windows toggles the read-only bit and can express nothing else,
#: so the modes below are a POSIX claim and are skipped rather than relaxed.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="chmod is a no-op on Windows")


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


# -- permissions -------------------------------------------------------------


@posix_only
def test_restrict_narrows_a_file_to_its_owner(tmp_path):
    target = tmp_path / "snapshot.parquet"
    target.write_text("x")
    target.chmod(0o644)
    assert restrict(target) is target
    assert stat.S_IMODE(target.stat().st_mode) == PRIVATE_FILE


@posix_only
def test_restrict_fixes_a_file_that_already_existed(tmp_path):
    """A `umask` can only narrow a file being created; this has to narrow both."""
    target = tmp_path / "utilisation.html"
    target.write_text("old")
    target.chmod(0o666)
    target.write_text("new")
    restrict(target)
    assert stat.S_IMODE(target.stat().st_mode) == PRIVATE_FILE


def test_restrict_does_not_raise_on_a_path_it_cannot_change(tmp_path):
    """The write has already succeeded; losing it over the mode would be worse."""
    assert restrict(tmp_path / "not-there") == tmp_path / "not-there"


@posix_only
def test_a_world_readable_token_file_is_reported_rather_than_changed(monkeypatch, tmp_path):
    """It is not ours to chmod -- we did not write it -- so it is said out loud."""
    token_file = tmp_path / "token"
    token_file.write_text("from-file")
    token_file.chmod(0o644)
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.setenv("WALDUR_TOKEN_FILE", str(token_file))

    with pytest.warns(UserWarning, match="readable by other users"):
        assert resolve_token() == "from-file"
    # Reported, not corrected: the file stays exactly as its owner left it.
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o644


@posix_only
def test_a_private_token_file_says_nothing(monkeypatch, tmp_path, recwarn):
    token_file = tmp_path / "token"
    token_file.write_text("from-file")
    token_file.chmod(0o600)
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.setenv("WALDUR_TOKEN_FILE", str(token_file))

    assert resolve_token() == "from-file"
    assert not [w for w in recwarn if "readable by other users" in str(w.message)]


@posix_only
def test_an_envrc_local_anyone_can_read_is_reported_too(monkeypatch, tmp_path):
    """The documented workflow's own file, and the one nothing can enforce."""
    envrc = tmp_path / ".envrc.local"
    envrc.write_text("export WALDUR_API_TOKEN=from-envrc\n", encoding="utf-8")
    envrc.chmod(0o644)
    monkeypatch.delenv("WALDUR_API_TOKEN", raising=False)
    monkeypatch.delenv("WALDUR_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PIXI_PROJECT_ROOT", str(tmp_path))

    with pytest.warns(UserWarning, match=re.escape("chmod 600")):
        assert resolve_token() == "from-envrc"


# -- where the token may go --------------------------------------------------


def test_origin_ignores_everything_a_token_is_not_scoped_to():
    """Path and query decide nothing about who receives the Authorization header."""
    assert origin_of("https://Portal.Example.Test/api/users/?page=2") == (
        "https://portal.example.test"
    )
    assert origin_of("https://portal.example.test:8443/api/") == (
        "https://portal.example.test:8443"
    )
    # A different port is a different origin, as it is everywhere else.
    assert origin_of("https://portal.example.test") != origin_of("https://portal.example.test:8443")


def test_the_scheme_s_own_port_is_the_same_origin_as_leaving_it_out():
    """`https://host:443` is `https://host` -- a redirect may spell either."""
    assert origin_of("https://portal.example.test:443/api/") == "https://portal.example.test"
    assert origin_of("http://localhost:80/api/") == "http://localhost"
    assert origin_of("https://portal.example.test:80/") == "https://portal.example.test:80"


def test_credentials_in_the_url_are_not_part_of_the_origin():
    """Two spellings of one deployment must not read as two deployments."""
    assert origin_of("https://someone:secret@portal.example.test/api/") == (
        "https://portal.example.test"
    )
    assert origin_of("https://someone@portal.example.test/api/") == (
        origin_of("https://portal.example.test/api/")
    )


def test_an_ipv6_host_keeps_its_brackets():
    """`hostname` strips them; an origin without them is not a URL you can rejoin."""
    assert origin_of("https://[::1]:8443/api/") == "https://[::1]:8443"
    assert origin_of("https://[::1]:443/api/") == "https://[::1]"


def test_plain_http_to_a_remote_portal_is_refused(tmp_path):
    """The token is a bearer credential; http:// puts it on the wire in clear."""
    with pytest.raises(InsecureApiUrlError, match="plain HTTP"):
        Settings(api_url="http://portal.example.test", token="t", cache_dir=tmp_path)


def test_plain_http_to_this_machine_is_allowed(tmp_path):
    """Refusing it would leave nothing to develop or test against but production."""
    settings = Settings(api_url="http://localhost:8000", token="t", cache_dir=tmp_path)
    assert settings.origin == "http://localhost:8000"


def test_something_that_is_not_a_url_is_refused_before_the_first_request(tmp_path):
    with pytest.raises(InsecureApiUrlError, match="http\\(s\\) URL"):
        Settings(api_url="portal.example.test", token="t", cache_dir=tmp_path)
