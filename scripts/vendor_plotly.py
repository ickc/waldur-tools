"""Write the plotly bundle the browser extension loads.

The extension cannot fetch it from a CDN -- an MV3 extension may not load remote
script at all -- so the file has to sit on disk beside the page. It is written
here rather than committed for two reasons: five megabytes of somebody else's
minified code does not belong in this repository's history, and taking it from
`plotly.offline.get_plotlyjs()` pins it to the very bundle `waldur-tools viz`
inlines, so the two reports cannot drift onto different plotly versions.

Run `pixi run web-vendor` once after cloning, and again after changing the
plotly pin.
"""

from __future__ import annotations

from pathlib import Path

from plotly.offline import get_plotlyjs

TARGET = Path(__file__).resolve().parents[1] / "web" / "vendor" / "plotly.min.js"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    source = get_plotlyjs()
    TARGET.write_text(source, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(Path.cwd())} ({len(source) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
