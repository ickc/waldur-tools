"""Write the plotly bundle the browser extension loads.

The extension cannot fetch it from a CDN -- an MV3 extension may not load remote
script at all -- so the file has to sit on disk beside the page. It is written
here rather than committed for two reasons: five megabytes of somebody else's
minified code does not belong in this repository's history, and taking it from
`plotly.offline.get_plotlyjs()` pins it to the very bundle `waldur-tools viz`
inlines, so the two reports cannot drift onto different plotly versions.

That pins them to each other on one machine. What pins them across machines is
`pixi.lock` resolving the same plotly for every platform -- a solve that reaches
only some of the platforms would have this write a different five megabytes
depending on where it ran.

`plotly.min.js` carries only a one-line copyright and a bare "Licensed under the
MIT license"; the MIT text itself is not in it. Clause of that licence asks that
the notice travel with the code, and the release archive is a redistribution of
it, so the full terms are written out beside the bundle -- from plotly's own
packaged `LICENSE`, so the version cannot drift from the code it covers.

Run `pixi run web-vendor` once after cloning, and again after changing the
plotly pin.
"""

from __future__ import annotations

from importlib.metadata import distribution
from pathlib import Path

from plotly.offline import get_plotlyjs

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "web" / "vendor"
BUNDLE = VENDOR / "plotly.min.js"
LICENSE = VENDOR / "plotly.LICENSE"


def _plotly_license() -> str:
    """The MIT text plotly ships in its distribution metadata."""
    dist = distribution("plotly")
    for path in dist.files or []:
        if path.name.upper().startswith("LICENSE"):
            return path.read_text(encoding="utf-8")
    raise SystemExit("plotly ships no LICENSE file to copy into the archive")


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    source = get_plotlyjs()
    # `newline` rather than the platform default: these files are packed into
    # the release archive, which is meant to come out byte for byte the same
    # wherever it was built, and on Windows the default would rewrite every
    # line ending in five megabytes of JavaScript.
    BUNDLE.write_text(source, encoding="utf-8", newline="\n")
    LICENSE.write_text(_plotly_license(), encoding="utf-8", newline="\n")
    print(f"wrote {BUNDLE.relative_to(ROOT)} ({len(source) / 1e6:.1f} MB)")
    print(f"wrote {LICENSE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
