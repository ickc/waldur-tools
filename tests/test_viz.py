from __future__ import annotations

import re
from datetime import date

import pytest

from waldur_tools import reports, viz
from waldur_tools.cache import Snapshot
from waldur_tools.frames import to_frame


@pytest.fixture
def snapshot(
    tmp_path,
    allocations,
    associations,
    accounting_summary,
    usage_reports,
    user_usage_rows,
    users,
    projects,
):
    snap = Snapshot.create(tmp_path, "test")
    snap.write("users", to_frame(users))
    snap.write("projects", to_frame(projects))
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-associations", to_frame(associations))
    snap.write("openportal-accounting-summary", to_frame(accounting_summary))
    snap.write("openportal-project-usage-reports", to_frame(usage_reports))
    snap.write("openportal-allocation-user-usage", to_frame(user_usage_rows))
    snap.write_meta({})
    return snap


#: Rendering inlines a four-megabyte plotly bundle, so the page is built once
#: and every assertion below reads that one copy. The fixtures are constants.
_cache: dict[str, str] = {}


@pytest.fixture
def page(snapshot):
    if "page" not in _cache:
        _cache["page"] = viz.render(snapshot, customer="UKRI")
    return _cache["page"]


def markup_only(page: str) -> str:
    """The page with every ``<script>`` body blanked out.

    The plotly bundle contains every URL plotly knows about, so searching the
    raw string for ``http`` finds those and says nothing at all about what this
    page would load.
    """
    return re.sub(r"<script>.*?</script>", "<script></script>", page, flags=re.S)


def test_report_is_self_contained(page):
    """The point of the format: it opens with no network and no server.

    A CDN reference would work on the machine that built it and fail on the
    laptop it gets emailed to, which is exactly the audience.
    """
    markup = markup_only(page)
    assert 'src="' not in markup
    assert "href=" not in markup
    assert "@import" not in markup
    assert "Plotly.newPlot" in page
    assert page.startswith("<!doctype html>")


def test_report_states_its_assumptions(page):
    """The node-hour reading and the scope are not derivable from the figures."""
    assert "384" in page
    assert "Node hours are assumed" in page
    assert "UKRI" in page


def test_every_figure_is_present(page):
    for heading in (
        "Are we using our share?",
        "The gap, compounded",
        "Where the usage comes from",
        "Which projects are alive",
        "How concentrated we are",
        "Are more people using it?",
    ):
        assert heading in page, heading


def test_figures_offer_absolute_and_relative(page):
    """Both scales ship, because no single axis can carry them at once."""
    assert "% of share" in page
    assert "Nodes, monthly average" in page
    assert "% of the month" in page


def test_table_views_exist(page):
    """The relief for the three light-mode series colours below 3:1 contrast."""
    assert page.count("Table view") >= 3


def test_no_figure_declares_a_second_axis(page):
    """A dual-axis chart invents a correlation; the buttons exist to avoid one."""
    assert '"yaxis2"' not in page


def test_dark_mode_has_a_step_for_every_colour_drawn():
    """A hex with no dark step silently leaves a light mark on a dark surface."""
    swap = viz._swap_map()
    for pale, dark in viz.SERIES:
        assert pale in swap or pale == dark, pale
    assert viz.CHROME["surface"][0] in swap
    assert len(viz.RAMP_LIGHT) == len(viz.RAMP_DARK)


def test_ranked_folds_the_tail_rather_than_inventing_hues(snapshot):
    """Past seven series, colour stops telling projects apart."""
    frame = reports.monthly(snapshot, customer="UKRI")
    banded = viz._ranked(frame, keep=1)
    assert "Other projects" in set(banded["band"].to_list())
    # Folding must not lose node hours on the way.
    assert banded["node_hours"].sum() == pytest.approx(frame["node_hours"].sum())


def test_render_explains_an_empty_result(snapshot):
    with pytest.raises(ValueError, match="No monthly usage rows"):
        viz.render(snapshot, customer="No Such Organisation")


def test_projects_existing_counts_only_ours_by_creation_date(snapshot):
    """The denominator behind 'n of m projects ran something'."""
    months = [date(2025, 6, 1), date(2026, 6, 1)]
    frame = viz.projects_existing(snapshot, months, customer="UKRI")
    assert frame["projects"].to_list() == [1, 2]


def test_projects_endpoint_is_optional(tmp_path, allocations, user_usage_rows):
    """An older snapshot without `projects` still renders the other figures."""
    snap = Snapshot.create(tmp_path, "thin")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-allocation-user-usage", to_frame(user_usage_rows))
    snap.write_meta({})
    assert viz.load_projects(snap, "UKRI").is_empty()
    assert viz.projects_existing(snap, [date(2026, 1, 1)], "UKRI").is_empty()
