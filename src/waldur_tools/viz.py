"""A self-contained HTML report on how much of our share of Isambard 3 we use.

The question this exists to answer: Isambard 3 has 384 compute nodes, and the
GW4 partner share held here is 10% of them. Are we using it?

Most of it is built from :func:`waldur_tools.reports.monthly_totals` and
:func:`waldur_tools.reports.monthly`, so the figures and the CSV you can export
from ``report monthly`` are the same numbers. The output is one HTML file with
plotly.js inlined -- no server, no CDN, no network at view time -- because the
audience for this is a committee, not a terminal.

**One source is not the portal.** The three job-shape figures read a
``sacct`` capture (see :mod:`waldur_tools.slurm`), because the portal records
daily job totals and nothing about an individual job. They are omitted when
that capture is absent, and nothing else depends on it.

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

import calendar
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

#: Sequential ramp for the quota heatmaps, with a hot tail. Unlike node hours,
#: a fill percentage is *bounded*: the whole decision runs from empty to full,
#: and the top of that range is the only part anyone acts on. So the ramp
#: leaves the blues around two thirds and finishes through amber into red,
#: which puts the quotas about to fail in a colour nothing else on the page
#: uses. Every hex is drawn from :data:`SERIES` or :data:`CHROME`, so the
#: light/dark swap needs no new pairs.
RAMP_FILL_LIGHT = ["#f0efec", "#d8e8f8", "#9ec5f4", "#6da7ec", "#eda100", "#eb6834", "#d03b3b"]
RAMP_FILL_DARK = ["#242423", "#104281", "#256abf", "#3987e5", "#eda100", "#eb6834", "#d03b3b"]

#: Violin body fill, light then dark. Translucent rather than a solid step,
#: so overlapping density lobes stay legible; it is kept out of :data:`SERIES`
#: because the hex-for-hex swap the theme switch performs cannot carry alpha.
VIOLIN_FILL = ("rgba(42,120,214,0.28)", "rgba(57,135,229,0.34)")

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

#: Where a zero is drawn on a logarithmic node-hour axis. A tenth of a node
#: hour is below anything the portal records as real work, so the bar reads as
#: "nothing" at a glance while still having a length to draw.
FLOOR_NODE_HOURS = 0.1


def light(role: str) -> str:
    """The light-mode hex for a chrome role."""
    return CHROME[role][0]


def _scale(ramp: list[str]) -> list[list[Any]]:
    """A list of hexes as an evenly stopped plotly colorscale."""
    return [[i / (len(ramp) - 1), colour] for i, colour in enumerate(ramp)]


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


def figure_share(
    totals: pl.DataFrame, nodes: int, share: float, awarded: list[float] | None = None
) -> go.Figure:
    """The headline: usage per month against what our share is worth.

    A column per month with a reference line at the entitlement, and buttons
    that restate the same comparison in node hours or as a percentage.

    ``awarded`` adds a second reference line: the node hours a month actually
    promised to projects (:func:`committed`). It is the more useful of the two
    lines, because the entitlement is an accounting figure nobody has committed
    to anyone, while this is the rate the credits behind the work were paced
    at. Usage running above it -- which it usually does here -- is projects
    spending their awards faster than the award period assumed, and that is
    only sustainable until the credits run out.

    There used to be a third button showing the same thing as an average node
    count. It was dropped as redundant: node count and percentage are the same
    number times a constant, so the two views drew identical shapes.
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
    # Omitting the awards means "no second line", not "a line of length zero":
    # pad to the month count so the strict zip below stays a real length check
    # on a list a caller did pass, rather than failing on the default. Only
    # ``None`` is the default -- an explicit ``[]`` is a caller passing a list of
    # the wrong length, and the zip is entitled to say so.
    if awarded is None:
        awarded = [0.0] * len(months)
    awarded_pct = [
        100 * value / limit if limit else 0.0
        for value, limit in zip(awarded, entitlement, strict=True)
    ]
    if any(awarded):
        figure.add_trace(
            go.Scatter(
                x=months,
                y=awarded,
                name="Awarded to projects",
                mode="lines",
                line={"color": SERIES[1][0], "width": 2, "dash": "dot"},
                hovertemplate="%{y:,.0f} node hours awarded<extra></extra>",
                meta={"slot": 1},
            )
        )

    absolute = [usage, entitlement] + ([awarded] if any(awarded) else [])
    relative = [percent, hundred] + ([awarded_pct] if any(awarded) else [])
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
                ["Node hours", "% of share"],
                [
                    [
                        {
                            "y": absolute,
                            "hovertemplate": "%{y:,.0f} node hours<extra></extra>",
                        },
                        {"yaxis.title.text": "Node hours"},
                    ],
                    [
                        {
                            "y": relative,
                            "hovertemplate": "%{y:,.1f}% of our share<extra></extra>",
                        },
                        {"yaxis.title.text": "% of our monthly share"},
                    ],
                ],
            ),
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


def figure_heatmap(
    per_project: pl.DataFrame, totals: pl.DataFrame, allocation: pl.DataFrame | None = None
) -> go.Figure:
    """Every project against every month, so dormancy is visible as blank space.

    Sequential rather than categorical: the question is magnitude, and this is
    the one figure that can carry all sixteen projects at once. Colour is on a
    log scale in both views, because a month of real work is three orders of
    magnitude above a test job and a linear ramp would render everything but
    the peak as empty.

    The second view divides each cell by that project's own
    ``mean_monthly_allocation`` (see :func:`waldur_tools.reports.allocations`),
    which is the only way to compare projects whose awards differ by orders of
    magnitude. It runs past 100% freely and is meant to: the award is a lump for
    a period, not a monthly ration, so a project doing a year's work in two
    months reads far above 100% for both and is behaving normally. Projects
    holding no credits at all have no denominator and stay blank.
    """
    months = totals["month"].to_list()
    labels = _months(totals)
    order = (
        per_project.group_by("project_name")
        .agg(total=pl.col("node_hours").sum())
        .sort("total")["project_name"]
        .to_list()
    )
    share: dict[str, float] = {}
    if allocation is not None and not allocation.is_empty():
        share = {
            name: value
            for name, value in zip(
                allocation["project_name"].to_list(),
                allocation["mean_monthly_allocation"].to_list(),
                strict=True,
            )
            if value
        }

    absolute, absolute_text, relative, relative_text = [], [], [], []
    for name in order:
        rows = (
            per_project.filter(pl.col("project_name") == name)
            .group_by("month")
            .agg(pl.col("node_hours").sum())
        )
        lookup = dict(zip(rows["month"].to_list(), rows["node_hours"].to_list(), strict=True))
        values = [lookup.get(month) for month in months]
        absolute.append([None if v is None else math.log10(v + 1) for v in values])
        absolute_text.append(
            ["no allocation" if v is None else f"{v:,.0f} node hours" for v in values]
        )

        rate = share.get(name)
        percents = [None if v is None or not rate else 100 * v / rate for v in values]
        # log10 of the percentage, so 100% sits at 2 and a project an order of
        # magnitude either side of its own rate is one step of colour away.
        relative.append([None if p is None else math.log10(p + 0.1) for p in percents])
        relative_text.append(
            [
                "no credits awarded"
                if rate is None
                else ("no usage" if p is None else f"{p:,.0f}% of its {rate:,.0f}/month")
                for p in percents
            ]
        )

    figure = go.Figure(
        go.Heatmap(
            x=labels,
            y=order,
            z=absolute,
            text=absolute_text,
            colorscale=_scale(RAMP_LIGHT),
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
            meta={"ramp": "activity"},
        )
    )
    buttons = (
        _buttons(
            ["Node hours", "% of own allocation"],
            [
                [
                    {
                        "z": [absolute],
                        "text": [absolute_text],
                        "colorbar.title.text": "Node hours",
                        "colorbar.tickvals": [[0, 1, 2, 3, 4, 5]],
                        "colorbar.ticktext": [["0", "10", "100", "1k", "10k", "100k"]],
                    },
                    {},
                ],
                [
                    {
                        "z": [relative],
                        "text": [relative_text],
                        "colorbar.title.text": "% of allocation",
                        "colorbar.tickvals": [[-1, 0, 1, 2, 3]],
                        "colorbar.ticktext": [["0", "1%", "10%", "100%", "1000%"]],
                    },
                    {},
                ],
            ],
        )
        if share
        else []
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
            updatemenus=buttons,
        )
    )
    return figure


#: Where the quota ramp tops out. A reading past this is drawn at the hot end
#: rather than stretching the scale for one over-quota cell -- the hover still
#: reports the true figure, and everything past full is equally actionable.
FILL_CEILING = 100.0

#: Colourbar ticks for the size views, in log10 bytes: 1 MiB, 1 GiB, 1 TiB.
_SIZE_TICKS = [math.log10(1024**power) for power in (2, 3, 4)]


def _storage_hover(row: dict[str, Any], stat: str) -> str:
    """One cell's tooltip: how full, in what, and on how much evidence.

    The size is spelled out on every cell rather than hidden behind a button.
    That is what lets the quota figures spend their buttons on the axes that
    change the picture -- the statistic, and for people the filesystem --
    instead of on an absolute view that would say the same thing in a colour.
    """
    fill = row[f"{stat}_fill_pct"]
    used = reports.humanise_bytes(row[f"{stat}_bytes"])
    days = calendar.monthrange(row["month"].year, row["month"].month)[1]
    seen = row["days_observed"]
    coverage = f"all {days} days" if seen >= days else f"{seen} of {days} days read"
    # Literal characters, not HTML entities: this is a plotly hover label
    # rather than page markup, and it would show the entity verbatim.
    if fill is None:
        return f"{used}, no limit reported · {coverage}"
    limit = reports.humanise_bytes(row["limit_bytes"])
    return f"{fill:,.1f}% full — {used} of {limit} · {coverage}"


def _storage_cells(
    lookup: dict[tuple[str, date], dict[str, Any]],
    order: list[str],
    months: list[date],
    stat: str,
    absolute: bool,
) -> tuple[list[list[float | None]], list[list[str]]]:
    """The z matrix and hover text for one view of a quota heatmap."""
    z: list[list[float | None]] = []
    text: list[list[str]] = []
    for key in order:
        values: list[float | None] = []
        labels: list[str] = []
        for month in months:
            row = lookup.get((key, month))
            raw = None if row is None else row[f"{stat}_bytes" if absolute else f"{stat}_fill_pct"]
            if row is None or raw is None:
                values.append(None)
                labels.append("no reading")
                continue
            values.append(math.log10(raw + 1) if absolute else min(raw, FILL_CEILING))
            labels.append(_storage_hover(row, stat))
        z.append(values)
        text.append(labels)
    return z, text


def _storage_heatmap(
    frame: pl.DataFrame,
    *,
    labels: dict[str, str],
    views: list[tuple[str, str, str, bool]],
    title: str,
    left_margin: int,
) -> go.Figure | None:
    """The shared body of both quota heatmaps.

    ``views`` is a flat list of ``(label, filesystem, stat, absolute)``. Flat
    rather than two button groups because plotly's groups do not compose: a
    second row of buttons would silently reset the first, and a control that
    undoes another control is worse than a longer row of honest ones.
    """
    if frame.is_empty():
        return None

    months = sorted(frame["month"].unique().to_list())
    # A month is short if even its best-observed quota missed days. Marked on
    # the axis, because a column standing on one reading is not comparable
    # with one standing on thirty and nothing else on the page would say so.
    days_seen = frame.group_by("month").agg(seen=pl.col("days_observed").max())
    coverage: dict[date, int] = dict(
        zip(days_seen["month"].to_list(), days_seen["seen"].to_list(), strict=True)
    )

    def short(month: date) -> bool:
        return coverage[month] < calendar.monthrange(month.year, month.month)[1]

    ticks = [f"{month.strftime('%b %Y')}{'*' if short(month) else ''}" for month in months]

    # Ascending, because plotly draws the first row at the bottom: the fullest
    # quota -- the one worth acting on -- ends up at the top of the figure.
    order = (
        frame.group_by("row_key")
        .agg(worst=pl.col("peak_fill_pct").max())
        .sort("worst", "row_key", descending=[False, True])["row_key"]
        .to_list()
    )

    matrices = []
    for _, filesystem, stat, absolute in views:
        slice_ = frame.filter(pl.col("filesystem") == filesystem)
        lookup = {(row["row_key"], row["month"]): row for row in slice_.iter_rows(named=True)}
        matrices.append(_storage_cells(lookup, order, months, stat, absolute))

    sizes = [
        value
        for _, _, stat, absolute in views
        if absolute
        for value in frame[f"{stat}_bytes"].drop_nulls().to_list()
    ]
    size_range = [math.log10(min(sizes) + 1), math.log10(max(sizes) + 1)] if sizes else [0, 1]

    def bar(absolute: bool) -> dict[str, Any]:
        if absolute:
            return {
                "title": "Size",
                "tickvals": _SIZE_TICKS,
                "ticktext": ["1 MB", "1 GB", "1 TB"],
                "zmin": size_range[0],
                "zmax": size_range[1],
            }
        return {
            "title": "% of quota",
            "tickvals": [0, 25, 50, 75, 100],
            "ticktext": ["0", "25%", "50%", "75%", "100%"],
            "zmin": 0,
            "zmax": FILL_CEILING,
        }

    first = bar(views[0][3])
    figure = go.Figure(
        go.Heatmap(
            x=ticks,
            y=[labels.get(key, key) for key in order],
            z=matrices[0][0],
            text=matrices[0][1],
            zmin=first["zmin"],
            zmax=first["zmax"],
            colorscale=_scale(RAMP_FILL_LIGHT),
            hovertemplate="%{y}<br>%{x}: %{text}<extra></extra>",
            xgap=2,
            ygap=2,
            colorbar={
                "title": {"text": first["title"], "font": {"color": light("ink_soft")}},
                "tickvals": first["tickvals"],
                "ticktext": first["ticktext"],
                "tickfont": {"color": light("muted")},
                "outlinewidth": 0,
                "thickness": 12,
            },
            meta={"ramp": "fill"},
        )
    )

    buttons = _buttons(
        [label for label, *_ in views],
        [
            [
                {
                    "z": [z],
                    "text": [text],
                    "zmin": [bar(absolute)["zmin"]],
                    "zmax": [bar(absolute)["zmax"]],
                    "colorbar.title.text": bar(absolute)["title"],
                    "colorbar.tickvals": [bar(absolute)["tickvals"]],
                    "colorbar.ticktext": [bar(absolute)["ticktext"]],
                },
                {},
            ]
            for (_, _, _, absolute), (z, text) in zip(views, matrices, strict=True)
        ],
    )

    figure.update_layout(
        _layout(
            height=170 + 26 * len(order),
            hovermode="closest",
            margin={"l": left_margin, "r": 30, "t": 60, "b": 60},
            title={"text": title, "font": {"size": 16, "color": light("ink")}},
            yaxis={
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "showgrid": False,
            },
            updatemenus=buttons,
        )
    )
    return figure


def figure_storage_projects(monthly: pl.DataFrame) -> go.Figure | None:
    """How full each project's shared storage got, month by month.

    The project quota is one filesystem, so the buttons spend themselves on the
    statistic and on an absolute view instead: a fill percentage answers "is
    this about to fail", and the size view answers "how much is actually
    there", which matters when every project carries the same limit and the
    percentages alone cannot tell a large project from a small one.
    """
    if monthly.is_empty():
        return None
    frame = monthly.filter(
        (pl.col("kind") == "project") & pl.col("filesystem").is_in(reports.PROJECT_FILESYSTEMS)
    ).with_columns(row_key=pl.col("project_code"))
    return _storage_heatmap(
        frame,
        labels={},
        views=[
            ("Peak", "projects", "peak", False),
            ("End", "projects", "end", False),
            ("Median", "projects", "median", False),
            ("Peak size", "projects", "peak", True),
            ("End size", "projects", "end", True),
            ("Median size", "projects", "median", True),
        ],
        title="Project storage: how full the shared quota got, per month",
        left_margin=140,
    )


def figure_storage_users(monthly: pl.DataFrame) -> go.Figure | None:
    """How full each person's home and scratch got, month by month.

    Two filesystems with limits two orders of magnitude apart, which is exactly
    why the colour is a percentage: on a size scale every home directory would
    read as empty next to a scratch quota, and it is the home directories that
    fill up. The buttons carry the filesystem here rather than an absolute
    view, since which disk is full is a different question from how full it is;
    sizes stay on every cell's tooltip.
    """
    if monthly.is_empty():
        return None
    frame = monthly.filter(
        (pl.col("kind") == "user") & pl.col("username").is_not_null()
    ).with_columns(row_key=pl.col("username") + " · " + pl.col("project_code"))
    if frame.is_empty():
        return None
    filesystems = sorted(frame["filesystem"].unique().to_list())
    views = [
        (f"{filesystem.capitalize()} {stat}", filesystem, stat, False)
        for filesystem in filesystems
        for stat in ("peak", "end", "median")
    ]
    return _storage_heatmap(
        frame,
        labels={},
        views=views,
        title="Personal storage: how full home and scratch got, per month",
        left_margin=220,
    )


#: How old the newest quota reading may be before the figures say so in words.
#: Long enough that a collector between runs is not accused of being dead, short
#: enough that a page is never quietly a season out of date. The browser
#: extension carries the same number.
STALE_DAYS = 45


def _storage_staleness(current: pl.DataFrame) -> str:
    """A warning sentence when the readings are old, and nothing when they are not.

    Storage figures lag their own collector rather than the snapshot, so a page
    pulled this morning can be showing a disk as it stood months ago. That is
    invisible on the heatmap -- the columns simply stop -- which makes it the
    one thing about these two figures worth saying in words.
    """
    newest = current["date"].max() if not current.is_empty() else None
    if not isinstance(newest, date):
        return ""
    read: date = newest
    days = (date.today() - read).days
    if days < STALE_DAYS:
        return ""
    return (
        f" <strong>These readings stop on {read:%-d %B %Y}</strong>, {days} days before "
        "this page was built: the collector behind them has not reported since. Read the "
        "columns as history rather than as the state of the disks now."
    )


def _storage_table(current: pl.DataFrame, columns: dict[str, str]) -> str:
    """The current-state table under a quota figure, with sizes as sizes.

    Byte counts are humanised into strings here rather than left as numbers,
    because :func:`_table` renders a float to one decimal place and a quota in
    bytes is fourteen digits of noise at that width.
    """
    if current.is_empty():
        return ""
    display = current.sort("fill_pct", descending=True, nulls_last=True).with_columns(
        pl.col("usage_bytes").map_elements(reports.humanise_bytes, return_dtype=pl.String),
        pl.col("limit_bytes").map_elements(reports.humanise_bytes, return_dtype=pl.String),
        pl.col("date").cast(pl.String),
    )
    return _table(display, columns)


def figure_totals_by_project(per_project: pl.DataFrame) -> go.Figure:
    """Lifetime node hours per project, ranked -- how concentrated we are.

    One measure, one hue: colouring these bars by size would double-encode the
    length they already show. The horizontal form is for the project names,
    which are too long to sit under columns.

    The axis is logarithmic, because the spread here is five orders of
    magnitude and on a linear axis every project below the top two is a stub of
    identical length. A log axis cannot draw a zero, so projects that never ran
    are pinned at :data:`FLOOR_NODE_HOURS` and labelled ``0`` -- they are the
    point of the figure and dropping them would be the worse distortion.
    """
    ranked = (
        per_project.group_by("project_name").agg(total=pl.col("node_hours").sum()).sort("total")
    )
    grand = ranked["total"].sum() or 1.0
    values = ranked["total"].to_list()
    plotted = [max(value, FLOOR_NODE_HOURS) for value in values]
    figure = go.Figure(
        go.Bar(
            x=plotted,
            y=ranked["project_name"].to_list(),
            orientation="h",
            marker={"color": SERIES[0][0]},
            text=[f"{v:,.0f}  ({100 * v / grand:.1f}%)" for v in values],
            textposition="outside",
            textfont={"color": light("ink_soft")},
            cliponaxis=False,
            customdata=values,
            hovertemplate="%{y}: %{customdata:,.1f} node hours<extra></extra>",
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
                "type": "log",
                "gridcolor": light("grid"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Node hours (log scale)", "font": {"color": light("ink_soft")}},
                "tickvals": [FLOOR_NODE_HOURS, 1, 10, 100, 1_000, 10_000, 100_000],
                "ticktext": ["0", "1", "10", "100", "1k", "10k", "100k"],
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
    """Jobs submitted and how long they waited, month by month.

    The fallback for a report built without a SLURM capture: these two series
    are all the portal can answer, since its usage reports stop at daily
    totals. With ``slurm-jobs.parquet`` present the three figures below replace
    this one and say considerably more.
    """
    if monthly_queue.is_empty():
        return None
    labels = _months(monthly_queue)
    jobs = monthly_queue["num_jobs"].to_list()
    wait = monthly_queue["mean_wait_hours"].to_list()

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
                ["Jobs", "Mean wait"],
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
                ],
            ),
        )
    )
    return figure


# --------------------------------------------------------------------------
# Job shape
#
# These three read `slurm-jobs.parquet` and are omitted when it is absent. They
# exist because the portal cannot answer any of them: it records how many jobs
# ran on a day and how long they waited in total, and nothing about what any
# one of them asked the scheduler for.
# --------------------------------------------------------------------------

#: Job size bands, in node hours consumed. Log-spaced, because job size on this
#: machine spans several orders of magnitude -- the median job costs seconds of
#: a single node, the largest hundreds of node hours -- and linear bands would
#: put almost every job in the first one.
SIZE_BANDS: tuple[tuple[float, str], ...] = (
    (0.01, "up to 0.01"),
    (0.1, "0.01 - 0.1"),
    (1.0, "0.1 - 1"),
    (10.0, "1 - 10"),
    (100.0, "10 - 100"),
    (float("inf"), "100 +"),
)

#: Requested-node bands. Not log-spaced but close to it, and cut where this
#: machine's users actually cut: single-node jobs are the great majority, so one
#: node gets a band to itself rather than being averaged in with two.
NODE_BANDS: tuple[tuple[float, str], ...] = (
    (1, "1 node"),
    (3, "2 - 3"),
    (7, "4 - 7"),
    (15, "8 - 15"),
    (31, "16 - 31"),
    (float("inf"), "32 +"),
)

#: Requested node-hour bands: nodes multiplied by the wall clock in the batch
#: script. This is the size of the hole the scheduler was asked to find.
ASK_BANDS: tuple[tuple[float, str], ...] = (
    (1.0, "up to 1"),
    (10.0, "1 - 10"),
    (100.0, "10 - 100"),
    (1_000.0, "100 - 1k"),
    (float("inf"), "1k +"),
)

#: The shortest wait the distribution figure will draw. A large share of jobs
#: start within a second of being submitted, and a logarithmic axis has no room
#: for zero; pinning them at a minute keeps that spike visible as a spike
#: instead of dropping the whole population on the floor.
FLOOR_WAIT_HOURS = 1 / 60


def _band(value: float | None, bands: tuple[tuple[float, str], ...]) -> str | None:
    if value is None:
        return None
    for edge, label in bands:
        if value <= edge:
            return label
    return bands[-1][1]


def _banded(jobs: pl.DataFrame, column: str, bands: tuple[tuple[float, str], ...]) -> pl.DataFrame:
    labels = [label for _, label in bands]
    return jobs.with_columns(
        band=pl.col(column)
        .map_elements(lambda value: _band(value, bands), return_dtype=pl.String)
        .cast(pl.Enum(labels))
    ).drop_nulls("band")


def figure_job_sizes(jobs: pl.DataFrame) -> go.Figure | None:
    """Where the jobs are, against where the node hours are.

    Both series are percentages of their own total, which is the whole point:
    plotted in their natural units a count of jobs and a sum of node hours
    share no axis, but as shares of the month they are directly comparable, and
    the gap between the two bars in a band is the answer. A machine whose job
    count sits in the smallest band while its node hours sit in the largest is
    being used by a few long runs and a great many one-node test jobs, and
    those two populations need different things.

    The slider moves through months; the first step is every month at once.
    """
    if jobs.is_empty():
        return None
    banded = _banded(jobs.filter(pl.col("node_hours").is_not_null()), "node_hours", SIZE_BANDS)
    if banded.is_empty():
        return None

    labels = [label for _, label in SIZE_BANDS]
    months = sorted(month for month in banded["month"].unique().to_list() if month is not None)

    def shares(frame: pl.DataFrame) -> tuple[list[float], list[float], int]:
        rolled = (
            frame.group_by("band")
            .agg(count=pl.len(), hours=pl.col("node_hours").sum())
            .sort("band")
        )
        lookup = {row["band"]: (row["count"], row["hours"]) for row in rolled.iter_rows(named=True)}
        total_jobs = sum(count for count, _ in lookup.values()) or 1
        total_hours = sum(hours for _, hours in lookup.values()) or 1.0
        counts = [100 * lookup.get(label, (0, 0.0))[0] / total_jobs for label in labels]
        hours = [100 * lookup.get(label, (0, 0.0))[1] / total_hours for label in labels]
        return counts, hours, int(total_jobs)

    frames = [("All months", banded)] + [
        (month.strftime("%b %Y"), banded.filter(pl.col("month") == month)) for month in months
    ]
    computed = [(name, *shares(frame)) for name, frame in frames]
    first = computed[0]

    figure = go.Figure(
        [
            go.Bar(
                x=labels,
                y=first[1],
                name="Share of jobs",
                marker={"color": SERIES[0][0]},
                hovertemplate="%{y:,.1f}% of jobs<extra></extra>",
                meta={"slot": 0},
            ),
            go.Bar(
                x=labels,
                y=first[2],
                name="Share of node hours",
                marker={"color": SERIES[1][0]},
                hovertemplate="%{y:,.1f}% of node hours<extra></extra>",
                meta={"slot": 1},
            ),
        ]
    )
    steps = [
        {
            "label": name,
            "method": "update",
            "args": [
                {"y": [counts, hours]},
                {"title.text": f"How big are the jobs? {name} — {total:,} jobs"},
            ],
        }
        for name, counts, hours, total in computed
    ]
    figure.update_layout(
        _layout(
            barmode="group",
            height=500,
            margin={"l": 70, "r": 30, "t": 60, "b": 150},
            title={
                "text": f"How big are the jobs? All months — {first[3]:,} jobs",
                "font": {"size": 16, "color": light("ink")},
            },
            xaxis={
                "showgrid": False,
                "linecolor": light("axis"),
                "tickcolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {
                    "text": "Node hours the job consumed",
                    "font": {"color": light("ink_soft")},
                },
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "% of the period's total", "font": {"color": light("ink_soft")}},
                "rangemode": "tozero",
            },
            legend={
                "orientation": "h",
                "yanchor": "top",
                "y": -0.30,
                "x": 0,
                "font": {"color": light("ink_soft")},
            },
            sliders=[
                {
                    "active": 0,
                    "steps": steps,
                    "x": 0,
                    "len": 1.0,
                    "pad": {"t": 70, "b": 10},
                    "currentvalue": {
                        "prefix": "Showing: ",
                        "font": {"color": light("ink_soft"), "size": 13},
                    },
                    "font": {"color": light("muted"), "size": 11},
                    "bgcolor": light("axis"),
                    "bordercolor": light("grid"),
                    "activebgcolor": SERIES[0][0],
                    "tickcolor": light("axis"),
                }
            ],
        )
    )
    return figure


def figure_wait_by_shape(jobs: pl.DataFrame) -> go.Figure | None:
    """What the queue charges for asking for more.

    Wait is plotted against what the job *requested* -- ``--nodes`` and
    ``--time`` from the batch script -- not against what it went on to use. The
    scheduler only ever sees the request: a job that reserves 24 hours and
    exits in one still had to wait for a 24-hour hole, and pricing the wait
    against the one hour it used would hide exactly the behaviour worth
    changing.

    Median and mean both appear because they disagree, and the disagreement is
    the finding: the mean is dragged up by a few jobs that waited weeks, so a
    band where the two are far apart is a band where the typical experience and
    the worst one are nothing alike.
    """
    if jobs.is_empty():
        return None
    waited = jobs.filter(pl.col("wait_seconds").is_not_null())
    if waited.is_empty():
        return None

    def summarise(column: str, bands: tuple[tuple[float, str], ...]) -> dict[str, list[Any]]:
        banded = _banded(waited.filter(pl.col(column).is_not_null()), column, bands)
        labels = [label for _, label in bands]
        rolled = (
            banded.group_by("band")
            .agg(
                median=pl.col("wait_seconds").median() / 3600,
                mean=pl.col("wait_seconds").mean() / 3600,
                count=pl.len(),
            )
            .sort("band")
        )
        lookup = {row["band"]: row for row in rolled.iter_rows(named=True)}
        return {
            "labels": labels,
            "median": [lookup.get(label, {}).get("median") for label in labels],
            "mean": [lookup.get(label, {}).get("mean") for label in labels],
            "count": [lookup.get(label, {}).get("count") or 0 for label in labels],
        }

    by_nodes = summarise("requested_nodes", NODE_BANDS)
    by_ask = summarise("requested_node_hours", ASK_BANDS)

    figure = go.Figure(
        [
            go.Bar(
                x=by_nodes["labels"],
                y=by_nodes["median"],
                name="Median wait",
                marker={"color": SERIES[0][0]},
                customdata=by_nodes["count"],
                hovertemplate="median %{y:,.2f} h over %{customdata:,} jobs<extra></extra>",
                meta={"slot": 0},
            ),
            go.Bar(
                x=by_nodes["labels"],
                y=by_nodes["mean"],
                name="Mean wait",
                marker={"color": SERIES[1][0]},
                customdata=by_nodes["count"],
                hovertemplate="mean %{y:,.2f} h over %{customdata:,} jobs<extra></extra>",
                meta={"slot": 1},
            ),
        ]
    )
    figure.update_layout(
        _layout(
            barmode="group",
            height=480,
            title={
                "text": "How long a job waits, by what it asked for",
                "font": {"size": 16, "color": light("ink")},
            },
            xaxis={
                "showgrid": False,
                "linecolor": light("axis"),
                "tickcolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Nodes requested", "font": {"color": light("ink_soft")}},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Queue wait (hours)", "font": {"color": light("ink_soft")}},
                "rangemode": "tozero",
            },
            updatemenus=_buttons(
                ["By nodes", "By node hours asked"],
                [
                    [
                        {
                            "x": [by_nodes["labels"], by_nodes["labels"]],
                            "y": [by_nodes["median"], by_nodes["mean"]],
                            "customdata": [by_nodes["count"], by_nodes["count"]],
                        },
                        {"xaxis.title.text": "Nodes requested"},
                    ],
                    [
                        {
                            "x": [by_ask["labels"], by_ask["labels"]],
                            "y": [by_ask["median"], by_ask["mean"]],
                            "customdata": [by_ask["count"], by_ask["count"]],
                        },
                        {"xaxis.title.text": "Node hours requested (nodes x wall clock asked for)"},
                    ],
                ],
            ),
        )
    )
    return figure


def figure_wait_distribution(jobs: pl.DataFrame) -> go.Figure | None:
    """The whole spread of queue waits in each month, not just its average.

    A mean wait hides the shape completely: the same 4-hour mean covers "every
    job waited about four hours" and "nine jobs in ten started at once and the
    tenth waited a fortnight", and those are different machines to be a user
    of. A violin per month shows which one it was.

    Drawn against log10 hours rather than on a log axis, because plotly fits
    the kernel density in the axis's own coordinates: on a linear fit the lobe
    covering four decades of fast jobs collapses to a line. The tick labels are
    written back in hours and days so nothing has to be read as an exponent.
    """
    if jobs.is_empty():
        return None
    waited = jobs.filter(pl.col("wait_seconds").is_not_null())
    if waited.is_empty():
        return None

    months = sorted(month for month in waited["month"].unique().to_list() if month is not None)
    traces = []
    for month in months:
        hours = [
            max(value / 3600, FLOOR_WAIT_HOURS)
            for value in waited.filter(pl.col("month") == month)["wait_seconds"].to_list()
        ]
        if not hours:
            continue
        traces.append(
            go.Violin(
                x=[month.strftime("%b %Y")] * len(hours),
                y=[math.log10(value) for value in hours],
                name=month.strftime("%b %Y"),
                showlegend=False,
                spanmode="hard",
                points=False,
                width=0.85,
                line={"color": SERIES[0][0], "width": 1},
                meta={"slot": 0, "fill": True},
                hoverinfo="skip",
            )
        )
    if not traces:
        return None

    figure = go.Figure(traces)
    figure.update_layout(
        _layout(
            height=480,
            hovermode="closest",
            title={
                "text": "How long jobs waited, month by month",
                "font": {"size": 16, "color": light("ink")},
            },
            yaxis={
                "gridcolor": light("grid"),
                "zerolinecolor": light("axis"),
                "linecolor": light("axis"),
                "tickfont": {"color": light("muted")},
                "title": {"text": "Queue wait", "font": {"color": light("ink_soft")}},
                "tickvals": [math.log10(FLOOR_WAIT_HOURS), -1, 0, 1, math.log10(24), 2, 3],
                "ticktext": ["1 min", "6 min", "1 h", "10 h", "1 day", "4 days", "6 weeks"],
            },
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


def committed(allocation: pl.DataFrame, months: list[date], horizon: date) -> list[float]:
    """The awarded rate in force in each month: node hours a month, promised.

    A project counts towards a month only while its award covers it, which is
    the whole reason this is not one number. Summing every project's
    ``mean_monthly_allocation`` regardless of dates treats awards that never
    overlapped as concurrent, and quietly inflates the total the moment one
    project's window closes. The two agree for as long as no award has expired
    yet, and that agreement is a coincidence rather than a licence to take the
    shortcut.

    Compared against the entitlement this answers a question the usage figures
    cannot: how much of our share has been *promised to anyone*. Usage cannot
    exceed that for long, whatever anyone runs, because the credits behind it
    are finite.
    """
    if allocation.is_empty() or "mean_monthly_allocation" not in allocation.columns:
        return [0.0] * len(months)
    starts = allocation["start_date"].to_list()
    ends = allocation["end_date"].to_list()
    rates = allocation["mean_monthly_allocation"].to_list()
    totals = []
    for month in months:
        total = 0.0
        for start, end, rate in zip(starts, ends, rates, strict=True):
            if rate is None or start is None:
                continue
            finish = end or horizon
            if start.replace(day=1) <= month <= finish.replace(day=1):
                total += rate
        totals.append(total)
    return totals


def credit_position(source: Snapshot | WaldurClient, customer: str | None) -> tuple[float, float]:
    """``(credit held, credit never allocated to a project)``, in node hours.

    Straight from ``customers``: ``customer_credit`` and
    ``customer_unallocated_credit``. Two fields that are easy to miss and that
    change the whole reading of this report, because they are the only
    organisation-level quantities the portal carries -- everything else here is
    per project or per month.

    The difference between them is credit the customer has handed down to
    projects, and it reconciles exactly: sum the ``limits.node`` of the live
    (``OK``-state) ``Isambard 3`` marketplace resources, add the credits of any
    project holding a balance with no provisioned resource, and you land on
    ``customer_credit - customer_unallocated_credit`` to the penny. That
    agreement is what makes the two fields trustworthy enough to quote; check it
    against your own snapshot rather than taking it on trust.

    So the allocated side is measured net of spend -- a project's limit *is* its
    remaining balance -- which makes ``customer_credit`` a remaining figure too,
    not a lifetime grant. An organisation can therefore hold a large unallocated
    balance while its utilisation looks poor: unallocated credit reaches no
    project, and a project is the only thing that can spend it.

    Returns ``(0.0, 0.0)`` when the endpoint is missing or the fields are null,
    which is what the legacy internal customer looks like.
    """
    try:
        frame = load(source, ["customers"])["customers"]
    except SnapshotError:
        return (0.0, 0.0)
    if frame.is_empty() or "customer_credit" not in frame.columns:
        return (0.0, 0.0)
    if customer is not None and "name" in frame.columns:
        frame = frame.filter(pl.col("name") == customer)
    if frame.is_empty():
        return (0.0, 0.0)
    numbers = frame.select(
        held=pl.col("customer_credit").cast(pl.String).cast(pl.Float64, strict=False).sum(),
        spare=pl.col("customer_unallocated_credit")
        .cast(pl.String)
        .cast(pl.Float64, strict=False)
        .sum(),
    ).row(0)
    return (float(numbers[0] or 0.0), float(numbers[1] or 0.0))


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
const FILL = __FILL__;

function shade(hex, dark) { const m = dark ? SWAP : UNSWAP; return m[hex] || hex; }

function paint(dark) {
  const c = CHROME, i = dark ? 1 : 0;
  document.querySelectorAll('.plotly-graph-div').forEach(div => {
    if (!div.data) return;
    div.data.forEach((trace, index) => {
      const style = {};
      if (trace.meta && trace.meta.ramp) {
        // Named rather than assumed: activity is one hue, the quota ramps end
        // in red, and repainting must not swap one figure's scale for another's.
        const ramp = RAMPS[trace.meta.ramp] || RAMPS.activity;
        style.colorscale = [ramp[dark ? 'dark' : 'light']];
        style['colorbar.tickfont.color'] = [c.muted[i]];
        style['colorbar.title.font.color'] = [c.ink_soft[i]];
      } else {
        if (trace.marker && typeof trace.marker.color === 'string')
          style['marker.color'] = [shade(trace.marker.color, dark)];
        if (trace.marker && trace.marker.line)
          style['marker.line.color'] = [c.surface[i]];
        if (trace.line && typeof trace.line.color === 'string')
          style['line.color'] = [shade(trace.line.color, dark)];
        if (trace.meta && trace.meta.fill) style['fillcolor'] = [FILL[i]];
        if (trace.textfont) style['textfont.color'] = [c.ink_soft[i]];
      }
      if (Object.keys(style).length) Plotly.restyle(div, style, [index]);
    });
    // Sliders carry their own chrome, and relayouting a path that does not
    // exist throws -- so this is added only for the figures that have one.
    if (div.layout && div.layout.sliders && div.layout.sliders.length) {
      Plotly.relayout(div, {
        'sliders[0].bgcolor': c.axis[i], 'sliders[0].bordercolor': c.grid[i],
        'sliders[0].tickcolor': c.axis[i], 'sliders[0].font.color': c.muted[i],
        'sliders[0].currentvalue.font.color': c.ink_soft[i]
      });
    }
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
    jobs: pl.DataFrame | None = None,
) -> str:
    """Build the whole report as one HTML string.

    Deliberately a string rather than a file: the CLI writes it, but a notebook
    can hand it straight to ``IPython.display.HTML``.

    ``jobs`` is an optional SLURM capture from
    :func:`waldur_tools.slurm.capture`. When it is supplied the report gains
    three figures on job shape and queue wait that the portal cannot answer;
    when it is not, everything else renders unchanged.
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
    allocation = reports.allocations(source, customer=customer)
    awarded = committed(allocation, months, reports.as_of(source))
    credit_held, unallocated = credit_position(source, customer)
    monthly_share = float(totals["entitlement_node_hours"][-1]) or 1.0
    unallocated_months = unallocated / monthly_share
    ours = jobs.filter(pl.col("project_code").is_in(codes)) if jobs is not None else None

    figures = [
        (
            figure_share(totals, nodes, share, awarded),
            "Are we using our share?",
            "Each column is a month's node hours. The grey line is what our share of the "
            "machine is worth; the dotted line is what has actually been <em>awarded</em> to "
            "projects, at the rate their award periods imply. The distance between the two "
            "lines is share nobody has been given, and no amount of running harder reaches "
            "it. Usage above the dotted line is projects spending their awards faster than "
            "the award period assumed. The hatched column is the month the snapshot was "
            "taken in and is incomplete by construction.",
            _table(
                totals,
                {
                    "month": "Month",
                    "node_hours": "Node hours used",
                    "entitlement_node_hours": "Share worth",
                    "pct_of_entitlement": "% of share",
                    "active_projects": "Active projects",
                    "active_users": "Active users",
                },
            ),
        ),
        (
            figure_projects(per_project, totals),
            "Where the usage comes from",
            "The seven largest projects by hue, the rest folded into a neutral band; the "
            "full list is in the table. The <em>% of the month</em> view answers a different "
            "question from the other two: how concentrated a month was, regardless of how "
            "big it was.",
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
            figure_heatmap(per_project, totals, allocation),
            "Which projects are alive",
            "One cell per project per month, on a log colour scale so a test job and a "
            "production campaign are both visible. A row that stays at the background "
            "colour is a project that was set up and never used &mdash; capacity we hold "
            "and do not convert, and the most actionable thing on this page. "
            "The <em>% of own allocation</em> view measures each project against its "
            "<strong>mean monthly allocation</strong>: the credits it holds, divided by the "
            "number of months between its start and end dates. It passes 100% freely, "
            "because an award is a lump for a period and not a monthly ration.",
            _table(
                allocation,
                {
                    "project_name": "Project",
                    "total_credits": "Node hours awarded",
                    "award_months": "Months",
                    "mean_monthly_allocation": "Mean monthly allocation",
                    "start_date": "From",
                    "end_date": "To",
                },
            ),
        ),
        (
            figure_totals_by_project(per_project),
            "How concentrated we are",
            "If a couple of bars carry most of the total, our utilisation is one or two "
            "research groups deep. The axis is logarithmic; a project that never ran is "
            "drawn at the far left and labelled zero.",
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

    # One parse of the storage endpoint, scoped to the same organisation as
    # every other figure on the page: the endpoint reports every project the
    # token can see, and a report headed by one customer's name must not mix
    # another's disks into its charts. Both views come off the same read, so
    # the table under a figure cannot describe a different pull from it.
    storage_samples = reports.storage_samples(source, customer=customer)
    storage_monthly = reports.storage_by_month(storage_samples)
    storage_now = reports.storage_now(storage_samples)
    project_quota = figure_storage_projects(storage_monthly)
    user_quota = figure_storage_users(storage_monthly)
    stale = _storage_staleness(storage_now)
    if project_quota is not None:
        figures.append(
            (
                project_quota,
                "How full the project disks are",
                "Node hours are a flow; disk is a level, so a month has to be summarised by "
                "picking a statistic rather than by adding one up. <em>Peak</em> is the "
                "fullest it got &mdash; the reading that decides whether writes failed, and "
                "the default. <em>End</em> is the level carried into the next month, and "
                "<em>median</em> is the typical level, unmoved by a single day's spike. "
                "Colour is the percentage of the quota rather than the size, because every "
                "project holds the same limit and only the fraction says who is in trouble; "
                "the <em>size</em> views and every tooltip give the bytes. A month marked "
                "<code>*</code> was not observed on every day." + stale,
                _storage_table(
                    storage_now.filter(pl.col("kind") == "project"),
                    {
                        "project_code": "Project",
                        "filesystem": "Filesystem",
                        "usage_bytes": "Used",
                        "limit_bytes": "Quota",
                        "fill_pct": "% full",
                        "date": "Last read",
                    },
                ),
            )
        )
    if user_quota is not None:
        figures.append(
            (
                user_quota,
                "How full people's own disks are",
                "The same reading, per person. Home is a hundredth the size of scratch, so "
                "the two are never comparable as sizes and the colour stays a percentage of "
                "whichever quota the buttons select. Home is where this bites: it is small, "
                "it is where people put things they meant to keep, and a full one stops a "
                "job as surely as a full scratch does. Rows are ordered by the fullest that "
                "quota ever got, so the people worth an email are at the top." + stale,
                _storage_table(
                    storage_now.filter(pl.col("kind") == "user"),
                    {
                        "username": "User",
                        "project_code": "Project",
                        "filesystem": "Filesystem",
                        "usage_bytes": "Used",
                        "limit_bytes": "Quota",
                        "fill_pct": "% full",
                        "date": "Last read",
                    },
                ),
            )
        )

    sizes = figure_job_sizes(ours) if ours is not None else None
    shape = figure_wait_by_shape(ours) if ours is not None else None
    spread = figure_wait_distribution(ours) if ours is not None else None
    if sizes is not None:
        figures.append(
            (
                sizes,
                "How big are the jobs?",
                "Both bars are shares of their own total, so a count of jobs and a sum of "
                "node hours can be read side by side. Where the two disagree, the machine "
                "is serving two different populations at once: many small jobs that cost "
                "almost nothing, and a few large ones that are the entire bill. "
                "The slider moves through months.",
                "",
            )
        )
    if shape is not None:
        figures.append(
            (
                shape,
                "What the queue charges for size",
                "Wait against what the job <em>asked</em> for &mdash; the <code>--nodes</code> "
                "and <code>--time</code> in the batch script, not what it went on to use. "
                "That is all the scheduler ever sees. Where the mean towers over the median, "
                "the typical wait and the worst wait are nothing alike.",
                "",
            )
        )
    if spread is not None:
        figures.append(
            (
                spread,
                "How long jobs waited",
                "One violin per month: the width at a height is how many jobs waited about "
                "that long. A mean cannot distinguish &ldquo;everyone waited four hours&rdquo; "
                "from &ldquo;nine in ten started at once and the tenth waited a fortnight&rdquo;, "
                "and those are different machines to be a user of.",
                "",
            )
        )
    queue_figure = figure_queue(queue) if ours is None else None
    if queue_figure is not None:
        figures.append(
            (
                queue_figure,
                "Demand, and what it cost to wait",
                "Utilisation that is low <em>and</em> quick to schedule is a demand problem; "
                "low utilisation with long waits is a job-shape or scheduling problem. "
                "Capture SLURM job records with <code>waldur-tools slurm-jobs</code> to "
                "replace this with the job-shape figures.",
                _table(
                    queue,
                    {
                        "month": "Month",
                        "num_jobs": "Jobs",
                        "mean_wait_hours": "Mean wait (h)",
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
        tiles.append(
            _tile(
                "Since we started",
                f"{mean_pct:,.0f}%",
                f"over {complete.height} complete months",
            )
        )
    if not latest.is_empty() and awarded:
        # Read off the latest complete month, not summed over every project
        # that has ever held an award: two projects whose windows never
        # overlapped were never committed at the same time.
        index = months.index(latest.row(0, named=True)["month"])
        rate = awarded[index]
        entitled_month = float(latest.row(0, named=True)["entitlement_node_hours"])
        if rate:
            tiles.append(
                _tile(
                    "Awarded to projects",
                    f"{100 * rate / entitled_month:,.0f}%",
                    f"of our share is promised to anyone — {rate:,.0f} node hours a month",
                )
            )
    if unallocated:
        tiles.append(
            _tile(
                "Credit never allocated",
                f"{unallocated:,.0f}",
                f"node hours held but assigned to no project — {unallocated_months:.1f} "
                "months of our share",
            )
        )
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
            "activity": {"light": _scale(RAMP_LIGHT), "dark": _scale(RAMP_DARK)},
            "fill": {"light": _scale(RAMP_FILL_LIGHT), "dark": _scale(RAMP_FILL_DARK)},
        }
    )
    chrome = json.dumps({role: list(pair) for role, pair in CHROME.items()})
    theme_js = (
        _THEME_JS.replace("__SWAP__", swap)
        .replace("__RAMPS__", ramps)
        .replace("__CHROME__", chrome)
        .replace("__FILL__", json.dumps(list(VIOLIN_FILL)))
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

    # The best month we have managed, which is the honest answer to "so how
    # close do we get?" and keeps the opening from promising more than the
    # figures deliver.
    peak = complete.sort("pct_of_entitlement", descending=True).head(1)
    best_month = ""
    if not peak.is_empty():
        row = peak.row(0, named=True)
        best_month = (
            f"The best month so far was {row['month'].strftime('%B %Y')}, at "
            f"{row['pct_of_entitlement']:,.0f}%."
        )
    # Read off the same month as the tile, not the last month in the frame:
    # that one is partial, and the two would disagree by a point on the page.
    awarded_pct = 0.0
    if awarded and not latest.is_empty():
        row = latest.row(0, named=True)
        awarded_pct = (
            100 * awarded[months.index(row["month"])] / float(row["entitlement_node_hours"])
        )

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
against that, so 100% would mean we ran, on average, exactly the nodes we
hold.</p>

<p>Nothing on the machine cuts us off. {best_month}
Nor is the constraint a shortage of credit: we hold {credit_held:,.0f} node hours of it and
<strong>{unallocated:,.0f} has never been allocated to a project</strong> —
{unallocated_months:.1f} months of our entire share, sitting unassigned. What has
reached projects amounts to about {awarded_pct:.0f}% of the share, the dotted
line on the first chart, and that is the ceiling everything below it works
under. <a href="#method">How to read these numbers</a> sets out what is and is
not enforced.</p>

<div class="kpis">{"".join(tiles)}</div>

{body}

<section class="method" id="method">
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
figure, and excluded from every headline average.</li>
<li><strong>Nothing enforces the {share:.0%}, and nothing needs to.</strong>
Isambard&nbsp;3 runs SLURM with every priority weight — fair share included —
set to zero, so jobs are scheduled first come, first served with backfill; no
account is held back for running over its share, and none is favoured for
running under it. There is no organisation-level account to cap either: our
projects hang directly off the root. The only enforced ceiling is
<em>per project</em> — a <code>GrpTRESMins</code> on each project's SLURM
account, which the portal sets from the credits that project has left. So what
bounds the figures on this page is not the scheduler and not a shortage of
credit: the organisation holds {credit_held:,.0f} node hours of credit, of which
<strong>{unallocated:,.0f} has never been allocated to any project</strong> —
{unallocated_months:.1f} months at 100% of our share, sitting unassigned. The
binding constraint is how much of it reaches a project, and then whether that
project has work to run.</li>
<li><strong>Two percentages, two denominators.</strong> Everything at page level
is measured against the organisation's share above.
The <em>% of own allocation</em> view on the project heatmap is measured against
each project's own award, and passes 100% routinely — a project may spend a
year of credits in a month, because nothing paces the spend. The two are not
comparable and are never drawn on the same axis.</li>
<li><strong>Mean monthly allocation is ours, not the portal's.</strong> The
relative view on the project heatmap divides a project's usage by its credits
spread evenly over the months between its start and end dates. The portal
grants credits as a lump for a period and never states a monthly figure, so
this is a construction; <code>waldur-tools report allocations</code> prints its
inputs.</li>
<li><strong>Job shape comes from SLURM, not the portal.</strong> The three
figures on job size and queue wait are built from <code>sacct</code> records
captured by <code>waldur-tools slurm-jobs</code>. The portal's usage reports
stop at daily totals and hold no record of an individual job, so what a job
requested exists only on the cluster.</li>
<li><strong>Every figure has a table view</strong> under it, and the same
numbers come out of <code>waldur-tools report monthly -o monthly.csv</code>.</li>
</ul>
</section>
</div>
<script>{theme_js}</script>
</body>
</html>
"""
