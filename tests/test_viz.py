from __future__ import annotations

import re
from datetime import date

import polars as pl
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
    customers,
):
    snap = Snapshot.create(tmp_path, "test")
    snap.write("users", to_frame(users))
    snap.write("customers", to_frame(customers))
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
    assert "@import" not in markup
    # Fragment links are fine -- they resolve inside the page and touch no
    # network. Anything else is a fetch waiting to fail on the laptop this gets
    # emailed to, which is the whole risk this test exists for.
    targets = re.findall(r'href="([^"]*)"', markup)
    assert all(target.startswith("#") for target in targets), targets
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
        "Where the usage comes from",
        "Which projects are alive",
        "How concentrated we are",
        "Are more people using it?",
    ):
        assert heading in page, heading


def test_figures_offer_absolute_and_relative(page):
    """Both scales ship, because no single axis can carry them at once."""
    assert "% of share" in page
    assert "% of the month" in page
    # A project's usage against its own award, which is the only comparison
    # that means anything across a 150-fold spread in award size.
    assert "% of own allocation" in page


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


def test_the_report_does_not_claim_fair_share_decides_anything(page):
    """Isambard 3 runs every priority weight at zero; the queue is FIFO.

    The page used to explain months over 100% as fair-share letting us borrow
    idle capacity. Nothing on the machine works that way, and the wrong reason
    for a real number is worse than no reason at all.
    """
    assert "fair-share lets" not in page
    assert "borrowed capacity nobody else" not in page
    assert "every priority weight" in page
    assert "GrpTRESMins" in page


def test_the_dropped_figures_stay_dropped(page):
    """The quota clears monthly, so a running shortfall is not a real balance."""
    assert "The gap, compounded" not in page
    assert "Unconverted to date" not in page
    assert "Nodes, monthly average" not in page
    assert "Busiest day, users" not in page


def test_totals_axis_is_logarithmic_and_still_draws_a_zero(snapshot):
    """Five orders of magnitude, and the never-ran projects are the point."""
    frame = reports.monthly(snapshot, customer="UKRI")
    figure = viz.figure_totals_by_project(frame)
    assert figure.layout.xaxis.type == "log"
    # A log axis cannot place a zero, so zeroes are pinned at the floor and
    # relabelled; the hover still reports the true value from `customdata`.
    assert min(figure.data[0].x) >= viz.FLOOR_NODE_HOURS
    assert "0" in figure.layout.xaxis.ticktext


def test_job_figures_are_omitted_without_a_slurm_capture(page):
    """Everything else has to render from a snapshot alone."""
    assert "How big are the jobs?" not in page
    assert "What the queue charges for size" not in page
    # The portal-only fallback takes their place.
    assert "Demand, and what it cost to wait" in page


def test_job_figures_appear_once_a_capture_is_supplied(snapshot, job_records):
    page = viz.render(snapshot, customer="UKRI", jobs=job_records)
    assert "How big are the jobs?" in page
    assert "What the queue charges for size" in page
    assert "How long jobs waited" in page
    # And the fallback stands down rather than doubling up on the same subject.
    assert "Demand, and what it cost to wait" not in page


def test_job_sizes_are_shares_of_their_own_total(job_records):
    """A count of jobs and a sum of node hours share no axis in raw units."""
    figure = viz.figure_job_sizes(job_records)
    for trace in figure.data:
        assert sum(trace.y) == pytest.approx(100.0)


def test_wait_bands_price_the_request_not_the_run(job_records):
    """The scheduler only ever sees what the batch script asked for."""
    figure = viz.figure_wait_by_shape(job_records)
    assert figure.layout.xaxis.title.text == "Nodes requested"
    labels = [button.label for button in figure.layout.updatemenus[0].buttons]
    assert labels == ["By nodes", "By node hours asked"]


def test_projects_endpoint_is_optional(tmp_path, allocations, user_usage_rows):
    """An older snapshot without `projects` still renders the other figures."""
    snap = Snapshot.create(tmp_path, "thin")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write("openportal-allocation-user-usage", to_frame(user_usage_rows))
    snap.write_meta({})
    assert viz.load_projects(snap, "UKRI").is_empty()
    assert viz.projects_existing(snap, [date(2026, 1, 1)], "UKRI").is_empty()


def test_committed_counts_only_projects_whose_award_covers_the_month(snapshot):
    """Awards that never overlapped were never committed at the same time."""
    from datetime import date as _date

    allocation = reports.allocations(snapshot, customer="UKRI")
    horizon = reports.as_of(snapshot)
    # Project A runs 2026-01 to 2026-08; Project B starts 2026-06.
    before = viz.committed(allocation, [_date(2025, 12, 1)], horizon)[0]
    early = viz.committed(allocation, [_date(2026, 2, 1)], horizon)[0]
    both = viz.committed(allocation, [_date(2026, 6, 1)], horizon)[0]
    assert before == 0.0
    assert early == pytest.approx(30000 / 8)
    assert both > early


def test_committed_survives_a_snapshot_with_no_awards():
    assert viz.committed(pl.DataFrame(), [date(2026, 1, 1)], date(2026, 7, 1)) == [0.0]


def test_the_headline_draws_awarded_next_to_entitled(snapshot):
    """Two different ceilings, and confusing them is the point of drawing both."""
    totals = reports.monthly_totals(snapshot, customer="UKRI")
    allocation = reports.allocations(snapshot, customer="UKRI")
    awarded = viz.committed(allocation, totals["month"].to_list(), reports.as_of(snapshot))
    figure = viz.figure_share(totals, 384, 0.10, awarded)
    names = [trace.name for trace in figure.data]
    assert names[0] == "Used"
    assert "Awarded to projects" in names
    # Every button has to rewrite every trace, or a view silently keeps stale y.
    for button in figure.layout.updatemenus[0].buttons:
        assert len(button.args[0]["y"]) == len(figure.data)


def test_the_headline_still_renders_without_awards(snapshot):
    totals = reports.monthly_totals(snapshot, customer="UKRI")
    figure = viz.figure_share(totals, 384, 0.10, [0.0] * totals.height)
    assert "Awarded to projects" not in [trace.name for trace in figure.data]
    for button in figure.layout.updatemenus[0].buttons:
        assert len(button.args[0]["y"]) == len(figure.data)


def test_the_headline_omits_the_awards_argument_entirely(snapshot):
    """The call README documents, and the one nothing else here makes.

    Passing an explicit list of zeros is not the same code path as leaving the
    argument out: the default used to become an empty list, which the strict
    zip against a full month column rejected outright.
    """
    totals = reports.monthly_totals(snapshot, customer="UKRI")
    figure = viz.figure_share(totals, nodes=384, share=0.10)
    assert "Awarded to projects" not in [trace.name for trace in figure.data]
    for button in figure.layout.updatemenus[0].buttons:
        assert len(button.args[0]["y"]) == len(figure.data)


def test_the_headline_rejects_an_awards_list_of_the_wrong_length(snapshot):
    """The strict zip still has to catch a caller who does pass one, wrongly."""
    totals = reports.monthly_totals(snapshot, customer="UKRI")
    with pytest.raises(ValueError):
        viz.figure_share(totals, 384, 0.10, [0.0] * (totals.height + 1))


def test_the_headline_rejects_an_empty_awards_list(snapshot):
    """An accidental ``[]`` is the wrong length too, and must not read as the default.

    Only ``None`` means "no awards". Treating any falsy value as the default
    would let a caller who built an empty list by mistake past the one check
    that would have told them.
    """
    totals = reports.monthly_totals(snapshot, customer="UKRI")
    assert totals.height > 0
    with pytest.raises(ValueError):
        viz.figure_share(totals, 384, 0.10, [])


def test_credit_position_reads_the_customer_level_fields(snapshot):
    """The only organisation-level quantities the portal carries.

    Easy to miss, and they change the reading of the whole report: an
    organisation can hold plenty of credit and still show low utilisation,
    because unallocated credit reaches no project and runs no job.
    """
    held, unallocated = viz.credit_position(snapshot, "UKRI")
    assert held == pytest.approx(50000.0)
    assert unallocated == pytest.approx(20000.0)


def test_credit_position_is_zero_when_the_endpoint_is_absent(tmp_path, allocations):
    snap = Snapshot.create(tmp_path, "thin")
    snap.write("openportal-allocations", to_frame(allocations))
    snap.write_meta({})
    assert viz.credit_position(snap, "UKRI") == (0.0, 0.0)


def test_the_report_does_not_call_the_share_unaffordable(page):
    """We hold far more credit than a month of the share costs.

    An earlier draft argued the headline could not pass 100% because the
    credit was not there. It is there -- it has not been handed to a project.
    """
    assert "unaffordable" not in page
    assert "never been allocated to a project" in page
    assert "Credit never allocated" in page


def test_the_share_is_stated_in_nodes_not_credits(page):
    """Guards a variable-shadowing bug that printed the customer's credit
    balance where the node count belongs. Both are floats and both are large,
    so nothing else catches it.

    384 nodes at 10% is the fixture's own configuration, not observed data."""
    assert "38.4 nodes, held for every hour of every month" in page
    assert "of the 38.4 nodes we hold" in page


def test_the_awarded_percentage_agrees_between_prose_and_tile(snapshot):
    """Both must read the latest *complete* month, or they differ by a point."""
    page = viz.render(snapshot, customer="UKRI")
    prose = re.search(r"amounts to about (\d+)% of the share", page)
    tile = re.search(r'Awarded to projects</div><div class="value">(\d+)%', page)
    assert prose and tile
    assert prose.group(1) == tile.group(1)
