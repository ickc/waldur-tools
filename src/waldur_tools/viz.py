"""A self-contained HTML report on how much of our share of Isambard 3 we use.

The question this exists to answer: Isambard 3 has 384 compute nodes, and the
GW4 partner share held here is 10% of them. Are we using it?

Everything is built from :func:`waldur_tools.reports.monthly_totals` and
:func:`waldur_tools.reports.monthly`, so the figures and the CSV you can export
from ``report monthly`` are the same numbers. The output is one HTML file with
plotly.js inlined -- no server, no CDN, no network at view time -- because the
audience for this is a committee, not a terminal.

**Design notes, so a later change does not undo them.** Series colours come from
a palette validated for colour-vision deficiency (worst adjacent pair ΔE 9.1
light / 8.4 dark, against a floor of 8); the slot order is the safety mechanism,
so assign slots in order and never generate a ninth. No figure has two y-axes:
where a figure could show more than one measure it offers *buttons* that swap
the single axis, which is also how absolute and relative views are toggled.
Every figure carries a table view, because three of the light-mode series
colours sit below 3:1 contrast and the table is their relief.
"""

from __future__ import annotations

import html
import json
import math
from datetime import date
from typing import TYPE_CHECKING, Any

import plotly.graph_objects as go
import polars as pl
from plotly.offline import get_plotlyjs

from . import reports
from .cache import SnapshotError, load
from .reports import DEFAULT_CUSTOMER, DEFAULT_SHARE, TOTAL_NODES

if TYPE_CHECKING:
    from .cache import Snapshot
    from .client import WaldurClient

# --------------------------------------------------------------------------
# Palette
#
# Light values first; the browser swaps to the dark step by hex lookup, which
# is why every colour used in a figure must appear in one of these tables.
# --------------------------------------------------------------------------

#: Categorical slots, in the order they must be assigned. Seven, not eight:
#: "Other" takes the neutral, so the eighth hue is never needed.
SERIES: tuple[tuple[str, str], ...] = (
    ("#2a78d6", "#3987e5"),  # blue
    ("#eb6834", "#d95926"),  # orange
    ("#1baf7a", "#199e70"),  # aqua
    ("#eda100", "#c98500"),  # yellow
    ("#e87ba4", "#d55181"),  # magenta
    ("#008300", "#008300"),  # green
    ("#4a3aa7", "#9085e9"),  # violet
)

#: Chrome. Marks that are not series: the "Other" bucket, reference lines, the
#: hatched partial month, gridlines and ink.
CHROME: dict[str, tuple[str, str]] = {
    "surface": ("#fcfcfb", "#1a1a19"),
    "plane": ("#f9f9f7", "#0d0d0d"),
    "ink": ("#0b0b0b", "#ffffff"),
    "ink_soft": ("#52514e", "#c3c2b7"),
    "muted": ("#898781", "#898781"),
    "grid": ("#e1e0d9", "#2c2c2a"),
    "axis": ("#c3c2b7", "#383835"),
    "other": ("#898781", "#898781"),
    "critical": ("#d03b3b", "#d03b3b"),
    "good": ("#0ca30c", "#0ca30c"),
}

#: Sequential ramp for the activity heatmap: one hue, low end nearest the
#: surface in each mode -- so light mode reads more-is-darker and dark mode
#: reads more-is-brighter, rather than flipping one into an unreadable copy.
RAMP_LIGHT = ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#0d366b"]
RAMP_DARK = ["#242423", "#104281", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#cde2fb"]

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def light(role: str) -> str:
    """The light-mode hex for a chrome role."""
    return CHROME[role][0]


def _swap_map() -> dict[str, str]:
    """Light hex -> dark hex, for the in-browser theme switch."""
    pairs = list(SERIES) + list(CHROME.values())
    return {pale: dark for pale, dark in pairs if pale != dark}


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


#: Shared layout. Recessive chrome, generous padding, hover on by default.
def _layout(**overrides: Any) -> go.Layout:
    base: dict[str, Any] = {
        "font": {"family": FONT, "size": 13, "color": light("ink_soft")},
        "paper_bgcolor": light("surface"),
        "plot_bgcolor": light("surface"),
        "margin": {"l": 70, "r": 30, "t": 60, "b": 110},
        "height": 460,
        "hovermode": "x unified",
        "hoverlabel": {"font": {"family": FONT}},
        # Below the axis, not above it: the top band already carries the title
        # and the view buttons, and a two-row legend up there collides with
        # both as soon as the project names get long.
        "legend": {
            "orientation": "h",
            "yanchor": "top",
            "y": -0.16,
            "x": 0,
            "font": {"color": light("ink_soft")},
        },
        "xaxis": {
            "showgrid": False,
            "linecolor": light("axis"),
            "tickcolor": light("axis"),
            "tickfont": {"color": light("muted")},
        },
        "yaxis": {
            "gridcolor": light("grid"),
            "zerolinecolor": light("axis"),
            "linecolor": light("axis"),
            "tickfont": {"color": light("muted")},
            "title": {"font": {"color": light("ink_soft")}},
        },
    }
    base.update(overrides)
    return go.Layout(**base)


def _buttons(labels: list[str], args: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """A single row of view-switching buttons above a figure.

    These are how a figure offers absolute *and* relative without a second
    y-axis: each button rewrites the one axis, so nothing is ever plotted
    against a scale it does not belong to.
    """
    return [
        {
            "type": "buttons",
            "direction": "right",
            "showactive": True,
            "x": 1.0,
            "xanchor": "right",
            "y": 1.03,
            "yanchor": "bottom",
            "pad": {"r": 0, "t": 0},
            "bgcolor": light("surface"),
            "bordercolor": light("axis"),
            "font": {"family": FONT, "size": 12, "color": light("ink_soft")},
            "buttons": [
                {"label": label, "method": "update", "args": arg}
                for label, arg in zip(labels, args, strict=True)
            ],
        }
    ]


def _months(frame: pl.DataFrame) -> list[str]:
    return [value.strftime("%b %Y") for value in frame["month"].to_list()]


def figure_share(totals: pl.DataFrame, nodes: int, share: float) -> go.Figure:
    """The headline: usage per month against what our share is worth.

    A column per month with a reference line at the entitlement, and buttons
    that restate the same comparison in node hours, as a percentage, or as an
    average node count -- the three units people ask for it in.
    """
    months = _months(totals)
    partial = totals["is_partial"].to_list()
    held = nodes * share

    # The in-progress month is hatched as well as annotated: a reader who
    # ignores the caption still sees that the last column is not comparable.
    pattern = ["/" if flag else "" for flag in partial]
    usage = totals["node_hours"].to_list()
    entitlement = totals["entitlement_node_hours"].to_list()
    percent = totals["pct_of_entitlement"].to_list()
    mean_nodes = totals["mean_nodes"].to_list()

    figure = go.Figure(
        [
            go.Bar(
                x=months,
                y=usage,
                name="Used",
                marker={"color": SERIES[0][0], "pattern": {"shape": pattern, "solidity": 0.6}},
                hovertemplate="%{y:,.0f} node hours<extra></extra>",
                meta={"slot": 0},
            ),
            go.Scatter(
                x=months,
                y=entitlement,
                name=f"Our share ({held:.1f} nodes, every hour)",
                mode="lines",
                line={"color": light("muted"), "width": 2},
                hovertemplate="%{y:,.0f} node hours<extra></extra>",
                meta={"chrome": "muted"},
            ),
        ]
    )

    hundred = [100.0] * len(months)
    held_line = [held] * len(months)
    figure.update_layout(
        _layout(
            title={
                "text": "Monthly node hours used, against our share of the machine",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Node hours", "font": {"color": light("ink_soft")}},
                "rangemode": "tozero",
            },
            updatemenus=_buttons(
                ["Node hours", "% of share", "Nodes, monthly average"],
                [
                    [
                        {
                            "y": [usage, entitlement],
                            "hovertemplate": "%{y:,.0f} node hours<extra></extra>",
                        },
                        {"yaxis.title.text": "Node hours"},
                    ],
                    [
                        {
                            "y": [percent, hundred],
                            "hovertemplate": "%{y:,.1f}% of our share<extra></extra>",
                        },
                        {"yaxis.title.text": "% of our monthly share"},
                    ],
                    [
                        {
                            "y": [mean_nodes, held_line],
                            "hovertemplate": "%{y:,.1f} nodes<extra></extra>",
                        },
                        {"yaxis.title.text": "Nodes, averaged over the month"},
                    ],
                ],
            ),
        )
    )
    return figure


def figure_cumulative(totals: pl.DataFrame) -> go.Figure:
    """Cumulative used against cumulative entitled: the gap, compounded.

    Month-by-month percentages bounce around; this is the same data as a
    running total, which is the form the "are we using our allocation?"
    conversation actually happens in. The in-progress month is excluded --
    a partial month would bend the actual line down against a full month of
    entitlement and invent a shortfall.
    """
    complete = totals.filter(~pl.col("is_partial"))
    running = complete.with_columns(
        used=pl.col("node_hours").cum_sum(),
        entitled=pl.col("entitlement_node_hours").cum_sum(),
    )
    months = _months(running)
    used = running["used"].to_list()
    entitled = running["entitled"].to_list()

    figure = go.Figure(
        [
            go.Scatter(
                x=months,
                y=entitled,
                name="Cumulative share",
                mode="lines",
                line={"color": light("muted"), "width": 2, "dash": "dash"},
                hovertemplate="%{y:,.0f} node hours entitled<extra></extra>",
                meta={"chrome": "muted"},
            ),
            go.Scatter(
                x=months,
                y=used,
                name="Cumulative used",
                mode="lines+markers",
                line={"color": SERIES[0][0], "width": 2},
                marker={"size": 8},
                hovertemplate="%{y:,.0f} node hours used<extra></extra>",
                meta={"slot": 0},
            ),
        ]
    )
    if months:
        shortfall = entitled[-1] - used[-1]
        figure.add_annotation(
            x=months[-1],
            y=max(used[-1], entitled[-1]),
            text=(
                f"{abs(shortfall):,.0f} node hours "
                f"{'unused' if shortfall > 0 else 'beyond our share'} to date"
            ),
            showarrow=False,
            yshift=18,
            xanchor="right",
            font={"size": 12},
        )
    figure.update_layout(
        _layout(
            title={
                "text": "Cumulative node hours: used against entitled, complete months only",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Node hours, cumulative", "font": {"color": light("ink_soft")}},
                "rangemode": "tozero",
            },
        )
    )
    return figure


def _ranked(per_project: pl.DataFrame, keep: int = 7) -> pl.DataFrame:
    """Collapse all but the ``keep`` largest projects into one "Other" row.

    Sixteen projects is past the point where hue can tell series apart, and
    generating more hues is the one thing a categorical palette must never do.
    The tail is not lost -- it is the "Other" band here, a row in the table
    view, and its own bar in the totals figure.
    """
    order = (
        per_project.group_by("project_name")
        .agg(total=pl.col("node_hours").sum())
        .sort("total", descending=True)
    )
    top = order.head(keep)["project_name"].to_list()
    return per_project.with_columns(
        band=pl.when(pl.col("project_name").is_in(top))
        .then(pl.col("project_name"))
        .otherwise(pl.lit("Other projects"))
    )


def figure_projects(per_project: pl.DataFrame, totals: pl.DataFrame) -> go.Figure:
    """Where the usage comes from: a stacked column per month, by project.

    Three views of one stack: node hours, each project against the whole
    organisation's share, and the month normalised to 100% -- which answers
    the different question of concentration, "was this month one project?".
    """
    banded = _ranked(per_project)
    months = totals["month"].to_list()
    labels = _months(totals)
    entitlement = dict(zip(months, totals["entitlement_node_hours"].to_list(), strict=True))
    month_total = dict(zip(months, totals["node_hours"].to_list(), strict=True))

    order = (
        banded.group_by("band")
        .agg(total=pl.col("node_hours").sum())
        .sort("total", descending=True)["band"]
        .to_list()
    )
    # "Other" is context, not a series, so it takes the neutral and sits last.
    order = [name for name in order if name != "Other projects"]
    if "Other projects" in banded["band"].to_list():
        order.append("Other projects")

    traces, absolute, relative, normalised = [], [], [], []
    for index, name in enumerate(order):
        rows = (
            banded.filter(pl.col("band") == name).group_by("month").agg(pl.col("node_hours").sum())
        )
        lookup = dict(zip(rows["month"].to_list(), rows["node_hours"].to_list(), strict=True))
        values = [lookup.get(month, 0.0) for month in months]
        absolute.append(values)
        relative.append([100 * v / entitlement[m] for v, m in zip(values, months, strict=True)])
        normalised.append(
            [
                100 * v / month_total[m] if month_total[m] else 0.0
                for v, m in zip(values, months, strict=True)
            ]
        )
        colour = light("other") if name == "Other projects" else SERIES[index][0]
        traces.append(
            go.Bar(
                x=labels,
                y=values,
                name=name,
                # A 2px surface gap separates the segments; no borders drawn.
                marker={"color": colour, "line": {"width": 2, "color": light("surface")}},
                hovertemplate="%{fullData.name}: %{y:,.0f}<extra></extra>",
                meta=({"chrome": "other"} if name == "Other projects" else {"slot": index}),
            )
        )

    figure = go.Figure(traces)
    figure.update_layout(
        _layout(
            barmode="stack",
            height=520,
            title={
                "text": "Which projects the usage came from, month by month",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Node hours", "font": {"color": light("ink_soft")}},
            },
            updatemenus=_buttons(
                ["Node hours", "% of share", "% of the month"],
                [
                    [
                        {
                            "y": absolute,
                            "hovertemplate": "%{fullData.name}: %{y:,.0f}<extra></extra>",
                        },
                        {"yaxis.title.text": "Node hours"},
                    ],
                    [
                        {
                            "y": relative,
                            "hovertemplate": "%{fullData.name}: %{y:,.1f}%<extra></extra>",
                        },
                        {"yaxis.title.text": "% of our monthly share"},
                    ],
                    [
                        {
                            "y": normalised,
                            "hovertemplate": "%{fullData.name}: %{y:,.1f}%<extra></extra>",
                        },
                        {"yaxis.title.text": "% of that month's usage"},
                    ],
                ],
            ),
        )
    )
    return figure


def figure_heatmap(per_project: pl.DataFrame, totals: pl.DataFrame) -> go.Figure:
    """Every project against every month, so dormancy is visible as blank space.

    Sequential rather than categorical: the question is magnitude, and this is
    the one figure that can carry all sixteen projects at once. Colour is on a
    log scale because a month of real work is three orders of magnitude above a
    test job, and a linear ramp would render everything but the peak as empty.
    """
    months = totals["month"].to_list()
    labels = _months(totals)
    order = (
        per_project.group_by("project_name")
        .agg(total=pl.col("node_hours").sum())
        .sort("total")["project_name"]
        .to_list()
    )

    z, text = [], []
    for name in order:
        rows = (
            per_project.filter(pl.col("project_name") == name)
            .group_by("month")
            .agg(pl.col("node_hours").sum())
        )
        lookup = dict(zip(rows["month"].to_list(), rows["node_hours"].to_list(), strict=True))
        values = [lookup.get(month) for month in months]
        z.append([None if v is None else math.log10(v + 1) for v in values])
        text.append(["no allocation" if v is None else f"{v:,.0f} node hours" for v in values])

    figure = go.Figure(
        go.Heatmap(
            x=labels,
            y=order,
            z=z,
            text=text,
            colorscale=[[i / (len(RAMP_LIGHT) - 1), c] for i, c in enumerate(RAMP_LIGHT)],
            hovertemplate="%{y}<br>%{x}: %{text}<extra></extra>",
            xgap=2,
            ygap=2,
            colorbar={
                "title": {"text": "Node hours", "font": {"color": light("ink_soft")}},
                "tickvals": [0, 1, 2, 3, 4, 5],
                "ticktext": ["0", "10", "100", "1k", "10k", "100k"],
                "tickfont": {"color": light("muted")},
                "outlinewidth": 0,
                "thickness": 12,
            },
            meta={"ramp": True},
        )
    )
    figure.update_layout(
        _layout(
            height=170 + 26 * len(order),
            hovermode="closest",
            margin={"l": 240, "r": 30, "t": 60, "b": 60},
            title={
                "text": "Project activity: node hours per project per month",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "showgrid": False,
            },
        )
    )
    return figure


def figure_totals_by_project(per_project: pl.DataFrame) -> go.Figure:
    """Lifetime node hours per project, ranked -- how concentrated we are.

    One measure, one hue: colouring these bars by size would double-encode the
    length they already show. The horizontal form is for the project names,
    which are too long to sit under columns.
    """
    ranked = (
        per_project.group_by("project_name").agg(total=pl.col("node_hours").sum()).sort("total")
    )
    grand = ranked["total"].sum() or 1.0
    values = ranked["total"].to_list()
    figure = go.Figure(
        go.Bar(
            x=values,
            y=ranked["project_name"].to_list(),
            orientation="h",
            marker={"color": SERIES[0][0]},
            text=[f"{v:,.0f}  ({100 * v / grand:.1f}%)" for v in values],
            textposition="outside",
            textfont={"color": light("ink_soft")},
            cliponaxis=False,
            hovertemplate="%{y}: %{x:,.0f} node hours<extra></extra>",
            meta={"slot": 0},
        )
    )
    figure.update_layout(
        _layout(
            height=170 + 26 * ranked.height,
            hovermode="closest",
            margin={"l": 240, "r": 120, "t": 60, "b": 60},
            title={
                "text": "Total node hours per project, all months",
                "font": {"size": 16, "color": light("ink")},
            },
            xaxis={
                "gridcolor": light("grid"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Node hours", "font": {"color": light("ink_soft")}},
            },
            yaxis={"linecolor": light("axis"), "tickfont": {"color": light("muted")}},
        )
    )
    return figure


def figure_engagement(totals: pl.DataFrame, existing: pl.DataFrame | None) -> go.Figure:
    """Projects and people actually running work, month by month.

    All three series are counts of the same kind of thing, so they share one
    axis honestly. The gap between "projects set up" and "projects that ran
    something" is the recruitment problem; the active-user line is whether the
    usage rests on more than one person.
    """
    labels = _months(totals)
    traces = []
    if existing is not None and not existing.is_empty():
        lookup = dict(zip(existing["month"].to_list(), existing["projects"].to_list(), strict=True))
        traces.append(
            go.Scatter(
                x=labels,
                y=[lookup.get(month, 0) for month in totals["month"].to_list()],
                name="Projects set up",
                mode="lines",
                line={"color": light("muted"), "width": 2},
                hovertemplate="%{y} projects exist<extra></extra>",
                meta={"chrome": "muted"},
            )
        )
    traces += [
        go.Scatter(
            x=labels,
            y=totals["active_projects"].to_list(),
            name="Projects that ran something",
            mode="lines+markers",
            line={"color": SERIES[0][0], "width": 2},
            marker={"size": 8},
            hovertemplate="%{y} active projects<extra></extra>",
            meta={"slot": 0},
        ),
        go.Scatter(
            x=labels,
            y=totals["active_users"].to_list(),
            name="People who ran something",
            mode="lines+markers",
            line={"color": SERIES[1][0], "width": 2},
            marker={"size": 8},
            hovertemplate="%{y} active users<extra></extra>",
            meta={"slot": 1},
        ),
    ]
    figure = go.Figure(traces)
    figure.update_layout(
        _layout(
            title={
                "text": "Engagement: projects set up, projects running, people running",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Count", "font": {"color": light("ink_soft")}},
                "rangemode": "tozero",
            },
        )
    )
    return figure


def figure_queue(monthly_queue: pl.DataFrame) -> go.Figure | None:
    """Jobs submitted and how long they waited -- demand, next to utilisation.

    Low utilisation with long waits is a scheduling or job-shape problem; low
    utilisation with short waits is a demand problem, and only one of those is
    fixed by tuning anything. Buttons swap the measure rather than adding an
    axis.
    """
    if monthly_queue.is_empty():
        return None
    labels = _months(monthly_queue)
    jobs = monthly_queue["num_jobs"].to_list()
    wait = monthly_queue["mean_wait_hours"].to_list()
    users = monthly_queue["busiest_day_users"].to_list()

    figure = go.Figure(
        go.Bar(
            x=labels,
            y=jobs,
            name="Jobs",
            marker={"color": SERIES[0][0]},
            hovertemplate="%{y:,.0f} jobs<extra></extra>",
            meta={"slot": 0},
        )
    )
    figure.update_layout(
        _layout(
            title={
                "text": "Demand: jobs submitted, and how long they waited",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Jobs submitted", "font": {"color": light("ink_soft")}},
                "rangemode": "tozero",
            },
            updatemenus=_buttons(
                ["Jobs", "Mean wait", "Busiest day, users"],
                [
                    [
                        {"y": [jobs], "hovertemplate": "%{y:,.0f} jobs<extra></extra>"},
                        {"yaxis.title.text": "Jobs submitted"},
                    ],
                    [
                        {
                            "y": [wait],
                            "hovertemplate": "%{y:,.1f} hours waited, mean<extra></extra>",
                        },
                        {"yaxis.title.text": "Mean queue wait (hours)"},
                    ],
                    [
                        {"y": [users], "hovertemplate": "%{y:,.0f} people<extra></extra>"},
                        {"yaxis.title.text": "People submitting, busiest day"},
                    ],
                ],
            ),
        )
    )
    return figure


# --------------------------------------------------------------------------
# Supporting series
#
# Two things the monthly reports do not carry, pulled from other endpoints.
# --------------------------------------------------------------------------


def projects_existing(
    source: Snapshot | WaldurClient,
    months: list[date],
    customer: str | None = DEFAULT_CUSTOMER,
) -> pl.DataFrame:
    """How many of our projects had been set up by each month.

    From ``projects.created``. This is the denominator behind "how many of the
    projects that existed ran something": without it the active-project count
    reads as a plateau rather than as a fraction.
    """
    frame = load_projects(source, customer)
    if frame.is_empty():
        return pl.DataFrame(schema={"month": pl.Date, "projects": pl.Int64})
    created = (
        frame.select(created=pl.col("created").str.slice(0, 10).str.to_date(strict=False))
        .drop_nulls()["created"]
        .to_list()
    )
    return pl.DataFrame(
        {
            "month": months,
            "projects": [sum(1 for day in created if day <= month) for month in months],
        },
        schema={"month": pl.Date, "projects": pl.Int64},
    )


def load_projects(
    source: Snapshot | WaldurClient, customer: str | None = DEFAULT_CUSTOMER
) -> pl.DataFrame:
    """Our organisation's projects, or an empty frame if we cannot get them.

    Empty rather than raising: ``projects`` only supplies the context line on
    one figure, and an older snapshot that predates it should still render the
    other six.
    """
    try:
        frame = load(source, ["projects"])["projects"]
    except SnapshotError:
        return pl.DataFrame()
    if frame.is_empty() or "created" not in frame.columns:
        return pl.DataFrame()
    if customer is not None and "customer_name" in frame.columns:
        frame = frame.filter(pl.col("customer_name") == customer)
    return frame


def people_with_access(source: Snapshot | WaldurClient, codes: list[str]) -> int:
    """How many distinct people hold an association on one of our projects.

    The denominator for "people who ran something": access granted but never
    exercised is the cheapest utilisation to recover, and it is invisible in
    the usage endpoint, which only knows about people who ran.
    """
    frame = reports.membership(source)
    if frame.is_empty():
        return 0
    return frame.filter(pl.col("project_code").is_in(codes))["unix_username"].n_unique()


def queue_monthly(source: Snapshot | WaldurClient, codes: list[str]) -> pl.DataFrame:
    """The daily queue report, rolled up to one row per month for our projects.

    ``mean_wait_hours`` is total wait over total jobs for the month, not the
    mean of the daily means -- a day with three jobs should not weigh as much
    as a day with three thousand.
    """
    daily = reports.queue(source)
    if daily.is_empty():
        return pl.DataFrame()
    ours = daily.with_columns(
        project_code=pl.col("project_identifier").str.split(".").list.first()
    ).filter(pl.col("project_code").is_in(codes))
    if ours.is_empty():
        return pl.DataFrame()
    return (
        ours.with_columns(month=pl.col("date").dt.truncate("1mo"))
        .group_by("month")
        .agg(
            num_jobs=pl.col("num_jobs").sum(),
            total_wait_seconds=pl.col("total_wait_seconds").sum(),
            busiest_day_users=pl.col("distinct_users").max(),
        )
        .with_columns(
            mean_wait_hours=pl.col("total_wait_seconds")
            / pl.col("num_jobs").replace(0, None)
            / 3600
        )
        .sort("month")
    )


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light; --surface:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b;
  --ink-soft:#52514e; --muted:#898781; --grid:#e1e0d9; --border:rgba(11,11,11,.10);
  --accent:#2a78d6; --good:#0ca30c; --critical:#d03b3b; }
:root[data-theme="dark"] { color-scheme: dark; --surface:#1a1a19; --plane:#0d0d0d;
  --ink:#fff; --ink-soft:#c3c2b7; --muted:#898781; --grid:#2c2c2a;
  --border:rgba(255,255,255,.10); --accent:#3987e5; }
* { box-sizing: border-box; }
body { margin:0; background:var(--plane); color:var(--ink-soft);
  font-family: system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.55;
  font-size:15px; }
.wrap { max-width:1080px; margin:0 auto; padding:32px 20px 80px; }
h1 { color:var(--ink); font-size:1.75rem; line-height:1.2; margin:0 0 6px; }
h2 { color:var(--ink); font-size:1.15rem; margin:0 0 6px; }
p { margin:0 0 12px; max-width:72ch; }
a { color:var(--accent); }
.sub { color:var(--muted); font-size:.9rem; }
header { display:flex; gap:16px; align-items:flex-start; justify-content:space-between;
  flex-wrap:wrap; margin-bottom:24px; }
button.theme { background:var(--surface); color:var(--ink-soft); border:1px solid var(--border);
  border-radius:8px; padding:7px 13px; font:inherit; font-size:.85rem; cursor:pointer; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px;
  margin:24px 0 36px; }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:16px 18px; }
.tile .label { font-size:.78rem; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); }
.tile .value { color:var(--ink); font-size:2rem; line-height:1.1; margin:6px 0 2px; }
.tile.hero .value { font-size:2.9rem; }
.tile .note { font-size:.83rem; color:var(--muted); }
section.fig { background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:20px 20px 12px; margin-bottom:24px; }
section.fig .plot { overflow-x:auto; }
details { margin-top:8px; border-top:1px solid var(--border); padding-top:8px; }
summary { cursor:pointer; font-size:.85rem; color:var(--muted); }
.tablewrap { overflow-x:auto; margin-top:10px; }
table { border-collapse:collapse; font-size:.85rem; font-variant-numeric:tabular-nums;
  width:100%; }
th,td { text-align:right; padding:5px 10px; border-bottom:1px solid var(--border);
  white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--ink); font-weight:600; }
.method { background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:20px; font-size:.9rem; }
.method li { margin-bottom:8px; max-width:80ch; }
@media (max-width:640px) { .tile.hero .value { font-size:2.2rem; } }
"""

_THEME_JS = """
const SWAP = __SWAP__;
const UNSWAP = Object.fromEntries(Object.entries(SWAP).map(([a, b]) => [b, a]));
const RAMPS = __RAMPS__;
const CHROME = __CHROME__;

function shade(hex, dark) { const m = dark ? SWAP : UNSWAP; return m[hex] || hex; }

function paint(dark) {
  const c = CHROME, i = dark ? 1 : 0;
  document.querySelectorAll('.plotly-graph-div').forEach(div => {
    if (!div.data) return;
    div.data.forEach((trace, index) => {
      const style = {};
      if (trace.meta && trace.meta.ramp) {
        style.colorscale = [RAMPS[dark ? 'dark' : 'light']];
        style['colorbar.tickfont.color'] = [c.muted[i]];
        style['colorbar.title.font.color'] = [c.ink_soft[i]];
      } else {
        if (trace.marker && typeof trace.marker.color === 'string')
          style['marker.color'] = [shade(trace.marker.color, dark)];
        if (trace.marker && trace.marker.line)
          style['marker.line.color'] = [c.surface[i]];
        if (trace.line && typeof trace.line.color === 'string')
          style['line.color'] = [shade(trace.line.color, dark)];
        if (trace.textfont) style['textfont.color'] = [c.ink_soft[i]];
      }
      if (Object.keys(style).length) Plotly.restyle(div, style, [index]);
    });
    Plotly.relayout(div, {
      'paper_bgcolor': c.surface[i], 'plot_bgcolor': c.surface[i],
      'font.color': c.ink_soft[i], 'title.font.color': c.ink[i],
      'legend.font.color': c.ink_soft[i],
      'xaxis.linecolor': c.axis[i], 'xaxis.tickcolor': c.axis[i],
      'xaxis.tickfont.color': c.muted[i], 'xaxis.gridcolor': c.grid[i],
      'xaxis.title.font.color': c.ink_soft[i],
      'yaxis.linecolor': c.axis[i], 'yaxis.tickcolor': c.axis[i],
      'yaxis.tickfont.color': c.muted[i], 'yaxis.gridcolor': c.grid[i],
      'yaxis.zerolinecolor': c.axis[i], 'yaxis.title.font.color': c.ink_soft[i],
      'updatemenus[0].bgcolor': c.surface[i], 'updatemenus[0].bordercolor': c.axis[i],
      'updatemenus[0].font.color': c.ink_soft[i]
    });
  });
}

function setTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  document.querySelector('.theme').textContent =
    mode === 'dark' ? 'Light mode' : 'Dark mode';
  paint(mode === 'dark');
  try { localStorage.setItem('waldur-viz-theme', mode); } catch (e) {}
}

(function () {
  let stored = null;
  try { stored = localStorage.getItem('waldur-viz-theme'); } catch (e) {}
  const initial = stored ||
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.querySelector('.theme').addEventListener('click', () =>
    setTheme(document.documentElement.getAttribute('data-theme') === 'dark'
      ? 'light' : 'dark'));
  setTheme(initial);
})();
"""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _table(frame: pl.DataFrame, headings: dict[str, str]) -> str:
    """A frame as an HTML table -- the WCAG-clean twin of the figure above it.

    Not an optional extra: several series colours sit below 3:1 contrast on the
    light surface, and the documented relief for that is a readable table.
    """
    columns = [column for column in headings if column in frame.columns]
    head = "".join(f"<th>{_esc(headings[column])}</th>" for column in columns)
    body = []
    for row in frame.select(columns).iter_rows(named=True):
        cells = []
        for column in columns:
            value = row[column]
            if value is None:
                cells.append("<td></td>")
            elif isinstance(value, float):
                cells.append(f"<td>{value:,.1f}</td>")
            elif isinstance(value, date):
                cells.append(f"<td>{value.strftime('%b %Y')}</td>")
            else:
                cells.append(f"<td>{_esc(value)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<details><summary>Table view</summary><div class="tablewrap"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody>"
        "</table></div></details>"
    )


def _tile(label: str, value: str, note: str, hero: bool = False) -> str:
    return (
        f'<div class="tile{" hero" if hero else ""}">'
        f'<div class="label">{_esc(label)}</div>'
        f'<div class="value">{_esc(value)}</div>'
        f'<div class="note">{_esc(note)}</div></div>'
    )


def _section(figure_html: str, heading: str, prose: str, table: str) -> str:
    return (
        f'<section class="fig"><h2>{_esc(heading)}</h2><p>{prose}</p>'
        f'<div class="plot">{figure_html}</div>{table}</section>'
    )


def render(
    source: Snapshot | WaldurClient,
    *,
    nodes: int = TOTAL_NODES,
    share: float = DEFAULT_SHARE,
    customer: str | None = DEFAULT_CUSTOMER,
) -> str:
    """Build the whole report as one HTML string.

    Deliberately a string rather than a file: the CLI writes it, but a notebook
    can hand it straight to ``IPython.display.HTML``.
    """
    from plotly.io import to_html

    totals = reports.monthly_totals(source, nodes=nodes, share=share, customer=customer)
    per_project = reports.monthly(source, nodes=nodes, share=share, customer=customer)
    if totals.is_empty():
        raise ValueError(
            "No monthly usage rows. Either the snapshot predates any usage, or "
            f"no project belongs to {customer!r} -- try customer=None."
        )

    months = totals["month"].to_list()
    complete = totals.filter(~pl.col("is_partial"))
    latest = complete.tail(1)
    held = nodes * share
    codes = per_project["project_code"].unique().to_list()

    existing = projects_existing(source, months, customer)
    queue = queue_monthly(source, codes)

    figures = [
        (
            figure_share(totals, nodes, share),
            "Are we using our share?",
            "Each column is a month's node hours; the line is what our share of the machine "
            "is worth over that month. Above the line we borrowed capacity nobody else "
            "claimed, which fair-share allows; below it we left our own share on the table. "
            "Use the buttons to read the same comparison as a percentage or as an average "
            "node count. The hatched column is the month the snapshot was taken in and is "
            "incomplete by construction.",
            _table(
                totals,
                {
                    "month": "Month",
                    "node_hours": "Node hours used",
                    "entitlement_node_hours": "Share worth",
                    "pct_of_entitlement": "% of share",
                    "mean_nodes": "Nodes (mean)",
                    "active_projects": "Active projects",
                    "active_users": "Active users",
                },
            ),
        ),
        (
            figure_cumulative(totals),
            "The gap, compounded",
            "Monthly percentages bounce; the running totals do not. The distance between "
            "the two lines is the share we have not converted into work since we started, "
            "and it is the number worth quoting when the allocation is reviewed.",
            _table(
                complete.with_columns(
                    used=pl.col("node_hours").cum_sum(),
                    entitled=pl.col("entitlement_node_hours").cum_sum(),
                ).with_columns(gap=pl.col("entitled") - pl.col("used")),
                {
                    "month": "Month",
                    "used": "Cumulative used",
                    "entitled": "Cumulative share",
                    "gap": "Unconverted",
                },
            ),
        ),
        (
            figure_projects(per_project, totals),
            "Where the usage comes from",
            "The seven largest projects by hue, the rest folded into a neutral band -- past "
            "seven, colour stops telling series apart, and the full list is in the table. "
            "The <em>% of the month</em> view answers a different question from the other "
            "two: how concentrated a month was, regardless of how big it was.",
            _table(
                per_project.group_by("project_name")
                .agg(
                    node_hours=pl.col("node_hours").sum(),
                    months_with_usage=pl.col("node_hours").filter(pl.col("node_hours") > 0).len(),
                )
                .sort("node_hours", descending=True),
                {
                    "project_name": "Project",
                    "node_hours": "Node hours, all months",
                    "months_with_usage": "Months with usage",
                },
            ),
        ),
        (
            figure_heatmap(per_project, totals),
            "Which projects are alive",
            "One cell per project per month, on a log colour scale so a test job and a "
            "production campaign are both visible. A row that stays at the background "
            "colour is a project that was set up and never used &mdash; capacity we hold "
            "and do not convert, and the most actionable thing on this page.",
            "",
        ),
        (
            figure_totals_by_project(per_project),
            "How concentrated we are",
            "If a couple of bars carry most of the total, our utilisation is one or two "
            "research groups deep, and a single person changing project would move the "
            "headline number more than any policy would.",
            "",
        ),
        (
            figure_engagement(totals, existing),
            "Are more people using it?",
            "Three counts on one axis. The gap between projects set up and projects that "
            "ran something is the onboarding gap; the people line is whether usage rests "
            "on more than a handful of individuals.",
            "",
        ),
    ]
    queue_figure = figure_queue(queue)
    if queue_figure is not None:
        figures.append(
            (
                queue_figure,
                "Demand, and what it cost to wait",
                "Utilisation that is low <em>and</em> quick to schedule is a demand problem; "
                "low utilisation with long waits is a job-shape or scheduling problem. Only "
                "one of those is fixed by tuning anything, so it is worth knowing which.",
                _table(
                    queue,
                    {
                        "month": "Month",
                        "num_jobs": "Jobs",
                        "mean_wait_hours": "Mean wait (h)",
                        "busiest_day_users": "Busiest day, users",
                    },
                ),
            )
        )

    # -- headline numbers ---------------------------------------------------
    tiles = []
    if not latest.is_empty():
        row = latest.row(0, named=True)
        tiles.append(
            _tile(
                f"{row['month'].strftime('%B %Y')} — share used",
                f"{row['pct_of_entitlement']:,.0f}%",
                f"{row['mean_nodes']:,.1f} of the {held:,.1f} nodes we hold",
                hero=True,
            )
        )
    if complete.height:
        # Hours over hours, not the mean of the monthly percentages: a quiet
        # month and a busy one are different sizes, and averaging their ratios
        # would weigh them the same.
        used_total = float(complete["node_hours"].sum())
        entitled_total = float(complete["entitlement_node_hours"].sum())
        mean_pct = 100 * used_total / entitled_total
        gap = entitled_total - used_total
        tiles += [
            _tile(
                "Since we started",
                f"{mean_pct:,.0f}%",
                f"over {complete.height} complete months",
            ),
            _tile(
                "Unconverted to date",
                f"{gap:,.0f}",
                "node hours of our share, never used"
                if gap > 0
                else "node hours borrowed beyond our share",
            ),
        ]
    if not latest.is_empty():
        row = latest.row(0, named=True)
        peak = existing["projects"].max() if not existing.is_empty() else None
        total_projects = int(peak) if isinstance(peak, int | float) else 0
        tiles += [
            _tile(
                "Projects running",
                f"{row['active_projects']}" + (f" / {total_projects}" if total_projects else ""),
                "ran something in the last complete month",
            ),
            _tile(
                "People running",
                f"{row['active_users']} / {people_with_access(source, codes)}",
                "of everyone with access ran something",
            ),
        ]

    swap = json.dumps(_swap_map())
    ramps = json.dumps(
        {
            "light": [[i / (len(RAMP_LIGHT) - 1), c] for i, c in enumerate(RAMP_LIGHT)],
            "dark": [[i / (len(RAMP_DARK) - 1), c] for i, c in enumerate(RAMP_DARK)],
        }
    )
    chrome = json.dumps({role: list(pair) for role, pair in CHROME.items()})
    theme_js = (
        _THEME_JS.replace("__SWAP__", swap)
        .replace("__RAMPS__", ramps)
        .replace("__CHROME__", chrome)
    )

    body = "".join(
        _section(
            to_html(
                figure,
                full_html=False,
                include_plotlyjs=False,
                config={"displaylogo": False, "responsive": True},
            ),
            heading,
            prose,
            table,
        )
        for figure, heading, prose, table in figures
    )

    stamp = getattr(source, "path", None)
    origin = f"snapshot {stamp.name}" if stamp is not None else "a live query"
    span = f"{months[0].strftime('%B %Y')} to {months[-1].strftime('%B %Y')}"

    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Isambard 3 utilisation — {_esc(customer or "all visible projects")}</title>
<style>{_CSS}</style>
<!-- The bundle must precede the figures: plotly emits a Plotly.newPlot call
     inline beside each div, and those run as the parser reaches them. -->
<script>{get_plotlyjs()}</script>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>Are we using our share of Isambard&nbsp;3?</h1>
    <p class="sub">{_esc(customer or "All visible projects")} · {_esc(span)} ·
      built from {_esc(origin)}</p>
  </div>
  <button class="theme" type="button">Dark mode</button>
</header>

<p>Isambard&nbsp;3 has {nodes} compute nodes and our share of it is
{share:.0%} — <strong>{held:,.1f} nodes, held for every hour of every month</strong>.
That is {held * 24:,.0f} node hours a day, and roughly
{held * 24 * 30:,.0f} a month. Every percentage on this page is usage measured
against that, so 100% means we ran on average exactly the nodes we hold, not
that we hit a ceiling: the share is an average entitlement, and SLURM fair-share
lets a busy month borrow capacity nobody else claimed.</p>

<div class="kpis">{"".join(tiles)}</div>

{body}

<section class="method">
<h2>How to read these numbers</h2>
<ul>
<li><strong>The source is one endpoint.</strong> Every figure comes from
<code>openportal-allocation-user-usage</code>, the only endpoint with a time
axis: one row per user, allocation and calendar month. Nothing is smoothed,
imputed or back-filled, and a month with no rows is a month with no usage.</li>
<li><strong>Node hours are assumed.</strong> The portal calls the field
<code>node_usage</code> and does not state a unit. This report reads it as node
hours, which is what makes {held:,.1f} nodes &times; 24 h &times; days the right comparison.
If it turns out to be node <em>days</em>, every percentage here is far too
small — but the shape of every curve is unchanged.</li>
<li><strong>Scope is {_esc(customer or "every project this token administers")}.</strong>
The portal also shows us separately funded UKRI and other, separately funded projects;
counting those in would inflate our own share.</li>
<li><strong>The last month is partial.</strong> It is hatched in the first
figure, and excluded from the cumulative figure and from every headline
average.</li>
<li><strong>Above 100% is real.</strong> The share is not a quota. Months over
the line are months we used capacity others had not claimed.</li>
<li><strong>Every figure has a table view</strong> under it, and the same
numbers come out of <code>waldur-tools report monthly -o monthly.csv</code>.</li>
</ul>
</section>
</div>
<script>{theme_js}</script>
</body>
</html>
"""
