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

Run `pixi run web-vendor` once after cloning, and again after changing the
plotly pin.
"""

from __future__ import annotations

from pathlib import Path

from plotly.offline import get_plotlyjs

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "web" / "vendor" / "plotly.min.js"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    source = get_plotlyjs()
    # `newline` rather than the platform default: this file is packed into the
    # release archive, which is meant to come out byte for byte the same
    # wherever it was built, and on Windows the default would rewrite every
    # line ending in five megabytes of JavaScript.
    TARGET.write_text(source, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(source) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
