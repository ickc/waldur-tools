"""Runtime configuration, resolved from the environment."""

from __future__ import annotations

import contextlib
import os
import stat
import warnings
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import platformdirs

DEFAULT_API_URL = "https://portal-api.isambard.ac.uk"

#: Mode for every file this package writes. Snapshots hold a whole
#: organisation's spend and project names, the generated report holds the same
#: figures rendered, and a token file holds the credential itself -- none of it
#: is ours to publish to everyone with an account on a shared login node, which
#: is what the usual 0644 does on a multi-user HPC filesystem.
PRIVATE_FILE = 0o600

#: The same for a directory this package creates. 0700 rather than 0600: a
#: directory needs the execute bit to be opened at all.
PRIVATE_DIR = 0o700

#: The bits that make a path readable by somebody other than its owner.
_OTHERS_READ = stat.S_IRWXG | stat.S_IRWXO


def restrict(path: Path, mode: int = PRIVATE_FILE) -> Path:
    """Narrow a path to its owner, and hand it back.

    Called after writing rather than before, because the mode has to be
    corrected on a file that already existed as well as set on a new one, and
    ``umask`` can only do the second. A ``chmod`` after the write does both.

    **Not a substitute for the directory being private.** Anything already
    holding a copy -- a file replaced in place, an editor's backup -- is
    untouched, and this only ever narrows the file it is handed.

    Failure is ignored. On Windows ``chmod`` cannot express this at all (it
    toggles the read-only bit and nothing else), and on a filesystem that
    refuses the call the write itself has already succeeded -- losing a
    finished snapshot over the permissions on it would be the worse outcome.
    Nothing here is the security boundary; the directory the file lands in is.
    """
    with contextlib.suppress(OSError):
        path.chmod(mode)
    return path


def _warn_if_readable(path: Path, what: str) -> None:
    """Say so, once, when a file holding the token is readable by anyone else.

    This cannot be enforced the way :func:`restrict` enforces our own output:
    these files are written by hand, by the person whose token it is, and
    silently changing the mode of a file we did not create -- one that may be
    deliberately shared, or may be a symlink into somewhere else -- is not ours
    to do. So it is said out loud and left to them.
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & _OTHERS_READ:
        warnings.warn(
            f"{path} is readable by other users (mode {stat.filemode(mode)}) and holds "
            f"{what}. On a shared login node that is the whole of the protection on it. "
            f"Fix with: chmod 600 {path}",
            stacklevel=3,
        )


_TOKEN_HELP = """\
No Waldur API token found.

Set it in .envrc.local at the repository root. The file is gitignored, and it
is read on every command -- pixi sources it through scripts/activate.sh, and on
Windows, where cmd.exe cannot source a shell file, this module reads the token
out of it directly:

    echo 'export WALDUR_API_TOKEN=<your token>' > .envrc.local
    chmod 600 .envrc.local

That chmod is not decoration on a shared login node: the default umask leaves
the file world-readable, and the token is the whole of your access to the
portal. Nothing here can set it for you -- the file is yours and is written by
hand -- so a command that finds it readable by others says so and carries on.

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
    if token:
        _warn_if_readable(path, "the API token")
    return token or None


def _token_from_envrc(path: Path) -> str | None:
    """The token out of a ``.envrc.local``, which is a shell file, not a token file.

    ``scripts/activate.sh`` sources that file, so on Unix the token is in the
    environment before this module runs and this never fires. cmd.exe cannot
    source a shell file, so on Windows this is what makes the documented
    workflow -- write the token into .envrc.local, run the next command -- work
    the same way there.

    Deliberately not a shell. It takes ``WALDUR_API_TOKEN=...`` off a line and
    ignores everything else, rather than interpreting a file that is allowed to
    contain arbitrary shell. The last assignment wins, as it would in a shell.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    found = None
    for line in lines:
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() == "WALDUR_API_TOKEN":
            found = value.strip().strip("\"'") or None
    if found:
        _warn_if_readable(path, "the API token")
    return found


def resolve_token() -> str:
    """Find the API token, preferring the environment over on-disk files.

    Order: ``WALDUR_API_TOKEN``, then ``WALDUR_TOKEN_FILE``, then the project's
    ``.envrc.local``, then ``~/.config/waldur/token``.
    """
    token = os.environ.get("WALDUR_API_TOKEN", "").strip()
    if token:
        return token

    env_file = os.environ.get("WALDUR_TOKEN_FILE")
    if env_file and (found := _read_token_file(Path(env_file).expanduser())):
        return found

    # Only the project pixi is running in, never a .envrc.local that happens to
    # be in whatever directory the command was typed from.
    root = os.environ.get("PIXI_PROJECT_ROOT")
    if root and (found := _token_from_envrc(Path(root) / ".envrc.local")):
        return found

    if found := _read_token_file(Path(platformdirs.user_config_dir("waldur")) / "token"):
        return found

    raise MissingTokenError


class InsecureApiUrlError(ValueError):
    """Raised when the configured API URL would put the token on the wire in clear."""


#: Hosts a plaintext ``http://`` API URL is allowed for. A Waldur running on the
#: same machine -- a developer's own, or a test double -- never leaves it, and
#: refusing that would make the package untestable against anything but the real
#: deployment. Everything else is a token crossing a network unencrypted.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


#: The port each scheme is already speaking on when the URL does not say.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> str:
    """``scheme://host[:port]``, lowercased -- the identity a token is scoped to.

    Two URLs share an origin when this matches. The path, the query and any
    credentials in the URL are deliberately not part of it: what decides
    whether the ``Authorization`` header may be attached is who is going to
    receive it, and that is the scheme, the host and the port.

    Built from ``hostname`` and the port rather than from ``netloc``, because
    ``netloc`` is the authority as written and not the origin it denotes. It
    keeps any ``user:password@`` -- which this is documented not to include and
    which would make one deployment look like two -- and it keeps a port that
    was only ever the scheme's default, so ``https://host:443`` and
    ``https://host`` would compare unequal and a redirect or a ``Link`` header
    that spelled one of them out would be refused. This is what the browser's
    ``URL.origin`` does, and what ``web/src/api.js`` gets for free from it.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # IPv6, which `hostname` hands back without its brackets.
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:  # Not a number, so not a port this could ever reach.
        return f"{scheme}://{parts.netloc.lower()}"
    if port is None or port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the client and cache need to run.

    ``api_url`` is the single origin this run may talk to. It is checked here
    rather than at each call site so there is one definition of "the portal",
    and :attr:`origin` is what every request is held to -- see
    :meth:`waldur_tools.client.WaldurClient._get`.
    """

    api_url: str
    token: str
    cache_dir: Path
    timeout: float = 60.0

    def __post_init__(self) -> None:
        parts = urlsplit(self.api_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise InsecureApiUrlError(
                f"WALDUR_API_URL must be an http(s) URL with a host; got {self.api_url!r}."
            )
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" and host not in LOOPBACK_HOSTS:
            raise InsecureApiUrlError(
                f"Refusing to send the API token to {self.api_url!r} over plain HTTP. "
                "The token is a bearer credential and http:// puts it on the wire in "
                "clear, readable by anything between here and the portal. Use https://, "
                "or point at a Waldur on this machine if you are testing."
            )

    @property
    def origin(self) -> str:
        """The one ``scheme://host[:port]`` this run's token is ever sent to."""
        return origin_of(self.api_url)

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
