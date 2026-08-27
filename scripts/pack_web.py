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

# A fixed timestamp for every entry. Zip records the mtime of the file it read,
# so an archive built twice from the same source would otherwise differ in its
# bytes and could not be compared against one built somewhere else. 1980-01-01
# is the earliest the format can express.
EPOCH = (1980, 1, 1, 0, 0, 0)


def contents() -> list[Path]:
    """Every file to pack, sorted, so entry order does not depend on the disk."""
    found: list[Path] = []
    for name in CONTENTS:
        path = WEB / name
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(p for p in path.rglob("*") if p.is_file())
        else:
            raise SystemExit(f"{path.relative_to(ROOT)} is missing")
    return sorted(found)


def main() -> None:
    version = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))["version"]
    if not (WEB / "vendor" / "plotly.min.js").exists():
        raise SystemExit("web/vendor/plotly.min.js is missing -- run `pixi run web-vendor`")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"isambard-utilisation-{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in contents():
            entry = zipfile.ZipInfo(path.relative_to(WEB).as_posix(), date_time=EPOCH)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())

    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
