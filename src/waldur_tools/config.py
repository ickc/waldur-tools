"""Runtime configuration, resolved from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

DEFAULT_API_URL = "https://portal-api.isambard.ac.uk"

_TOKEN_HELP = """\
No Waldur API token found.

Set it in .envrc.local at the repository root. The file is gitignored, and pixi
sources it on every command via scripts/activate.sh:

    echo 'export WALDUR_API_TOKEN=<your token>' > .envrc.local

Alternatively point WALDUR_TOKEN_FILE at a file containing just the token, or
place it in ~/.config/waldur/token. The token is issued by the portal under
your own account menu.

Note that portal tokens are short-lived -- hours, not days -- so a token that
worked this morning will not work this afternoon. Rewriting .envrc.local is
part of the routine; the next command picks the new value up immediately.\
"""


class MissingTokenError(RuntimeError):
    """Raised when no API token can be resolved."""

    def __init__(self) -> None:
        super().__init__(_TOKEN_HELP)


def _read_token_file(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def resolve_token() -> str:
    """Find the API token, preferring the environment over on-disk files.

    Order: ``WALDUR_API_TOKEN``, then ``WALDUR_TOKEN_FILE``, then
    ``~/.config/waldur/token``.
    """
    token = os.environ.get("WALDUR_API_TOKEN", "").strip()
    if token:
        return token

    candidates = []
    if env_file := os.environ.get("WALDUR_TOKEN_FILE"):
        candidates.append(Path(env_file).expanduser())
    candidates.append(Path(platformdirs.user_config_dir("waldur")) / "token")

    for candidate in candidates:
        if found := _read_token_file(candidate):
            return found

    raise MissingTokenError


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the client and cache need to run."""

    api_url: str
    token: str
    cache_dir: Path
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> Settings:
        cache_dir = os.environ.get("WALDUR_CACHE_DIR")
        return cls(
            api_url=os.environ.get("WALDUR_API_URL", DEFAULT_API_URL).rstrip("/"),
            token=resolve_token(),
            cache_dir=(
                Path(cache_dir).expanduser()
                if cache_dir
                else Path(platformdirs.user_cache_dir("waldur-tools"))
            ),
            timeout=float(os.environ.get("WALDUR_TIMEOUT", "60")),
        )

    def __repr__(self) -> str:
        # Keep the token out of tracebacks and logs.
        return (
            f"Settings(api_url={self.api_url!r}, token=<redacted>, "
            f"cache_dir={self.cache_dir!r}, timeout={self.timeout!r})"
        )
