"""Pack the browser extension into the zip that ships as a release asset.

The extension has no build step, but it does have a generated file:
`web/vendor/plotly.min.js` is written by `pixi run web-vendor` and is not
committed, so a pack that ran on its own would produce an extension that cannot
draw anything. The `web-pack` task depends on `web-vendor` for that reason, and
this refuses to write an archive without it rather than shipping a broken one.

`manifest.json` carries the version and is the only place it is written down.
The archive is named from it, and `.github/workflows/release.yml` refuses a tag
that says something else -- otherwise the asset's name and the extension's own
reported version drift apart, and the reader is the one who finds out.

Paths inside the archive are relative to `web/`, because Chrome requires
`manifest.json` at the root of what it loads, and are written with forward
slashes on every platform -- the zip format says so, and Chrome would read a
backslash as part of the name rather than as a directory.

`LICENSE` is the exception to that: it lives at the repository root, not under
`web/`, and is copied to the root of the archive. The zip is how most people
who use the extension will ever receive this code -- they load the unpacked
folder and never see the repository -- so the terms have to travel with it.
The same reasoning covers `web/vendor/plotly.LICENSE`, plotly's own MIT terms,
which `web-vendor` writes beside the bundle and which pack in with the rest of
`vendor/`.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = ROOT / "dist"

# What Chrome loads, plus the README, which is documentation and contains no
# data. Everything else in `web/` is developer scaffolding: the tests, their
# golden fixtures, and the `package.json` that exists only to tell node that
# `src/` is ES modules.
CONTENTS = ["manifest.json", "README.md", "src", "vendor"]

# Files taken from the repository root rather than from `web/`, and written to
# the root of the archive under their own names. Only the licence: the archive
# is a redistribution of this source, and clause 1 of the BSD-3 terms it ships
# under asks that redistributions carry the notice.
ROOT_CONTENTS = ["LICENSE"]

# A fixed timestamp for every entry. Zip records the mtime of the file it read,
# so an archive built twice from the same source would otherwise differ in its
# bytes and could not be compared against one built somewhere else. 1980-01-01
# is the earliest the format can express.
EPOCH = (1980, 1, 1, 0, 0, 0)


def contents(web: Path, root: Path) -> list[tuple[Path, str]]:
    """Every file to pack as ``(source, name inside the archive)``.

    Sorted on the archive name rather than on the source path, because the two
    no longer agree -- `LICENSE` comes from outside `web/` -- and it is the
    order inside the archive that has to be reproducible.

    Both roots are arguments rather than defaults reading the module globals, so
    that a test can point this at a tree it built. A default would be bound once
    at import and go on naming the real `web/` however the globals were changed
    afterwards, which is a quiet way for a test to pass against the wrong tree.
    """
    found: list[tuple[Path, str]] = []
    for name in CONTENTS:
        path = web / name
        if path.is_file():
            found.append((path, path.relative_to(web).as_posix()))
        elif path.is_dir():
            found.extend((p, p.relative_to(web).as_posix()) for p in path.rglob("*") if p.is_file())
        else:
            raise SystemExit(f"{_shown(path, root)} is missing")
    for name in ROOT_CONTENTS:
        path = root / name
        if not path.is_file():
            raise SystemExit(f"{_shown(path, root)} is missing")
        found.append((path, name))
    return sorted(found, key=lambda pair: pair[1])


def _shown(path: Path, root: Path) -> str:
    """A path to name in an error, relative to the repository where it can be."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main() -> None:
    version = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))["version"]
    for generated in ("plotly.min.js", "plotly.LICENSE"):
        if not (WEB / "vendor" / generated).exists():
            raise SystemExit(f"web/vendor/{generated} is missing -- run `pixi run web-vendor`")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"isambard-utilisation-{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in contents(WEB, ROOT):
            entry = zipfile.ZipInfo(name, date_time=EPOCH)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            # ZipInfo takes this from whatever host it is constructed on -- 0
            # on Windows, 3 (unix) everywhere else -- and writes it into every
            # central-directory entry, so the same source would otherwise pack
            # into two different archives. 3 is also what makes the permission
            # bits set above mean anything to a reader.
            entry.create_system = 3
            archive.writestr(entry, path.read_bytes())

    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
