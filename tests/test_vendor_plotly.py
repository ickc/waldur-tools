"""That the licence vendored beside the bundle is plotly's own, in full.

`plotly.min.js` names the MIT licence in a one-line header and does not carry
its text. The release archive redistributes that bundle, so `web-vendor` writes
the terms out beside it -- and the failure that matters is a stub: a file that
says "MIT license" and nothing else licenses nothing. These pin the substance
of what gets written, and where it is read from.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    """Import the script by path: `scripts/` is not a package and not installed."""
    spec = importlib.util.spec_from_file_location(
        "vendor_plotly", ROOT / "scripts" / "vendor_plotly.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePath:
    """Enough of `importlib.metadata.PackagePath` for the search to walk it."""

    def __init__(self, path: str, text: str = ""):
        self.parts = tuple(path.split("/"))
        self.name = self.parts[-1]
        self._text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


class FakeDistribution:
    def __init__(self, files):
        self.files = files


def test_the_vendored_licence_is_the_whole_mit_text():
    """Not the one-line notice the bundle already carries."""
    text = load()._plotly_license()
    assert "MIT License" in text
    assert "Permission is hereby granted, free of charge" in text
    assert "WITHOUT WARRANTY OF ANY KIND" in text
    assert "Plotly" in text


def test_the_licence_comes_from_the_metadata_not_the_package_data(monkeypatch):
    """plotly ships other projects' notices as package data; those are not it."""
    module = load()
    monkeypatch.setattr(
        module,
        "distribution",
        lambda name: FakeDistribution(
            [
                FakePath("plotly/labextension/static/LICENSE", "somebody else's terms"),
                FakePath("plotly-9.9.9.dist-info/licenses/LICENSE.txt", "plotly's own terms"),
            ]
        ),
    )
    assert module._plotly_license() == "plotly's own terms"


def test_a_distribution_with_no_licence_stops_the_vendoring(monkeypatch):
    module = load()
    monkeypatch.setattr(
        module,
        "distribution",
        lambda name: FakeDistribution([FakePath("plotly-9.9.9.dist-info/METADATA", "")]),
    )
    with pytest.raises(SystemExit, match="ships no LICENSE file"):
        module._plotly_license()
