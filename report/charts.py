"""
Chart generators using matplotlib.

All functions return a BytesIO buffer containing a PNG image,
ready to be embedded into the PDF report via ReportLab.
"""

from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, safe for server/CLI use
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from models import CostTrend, ScanResult

# Consistent colour palette
_PALETTE = [
    "#2563EB",  # blue
    "#16A34A",  # green
    "#DC2626",  # red
    "#D97706",  # amber
    "#7C3AED",  # violet
    "#0891B2",  # cyan
    "#DB2777",  # pink
    "#65A30D",  # lime
    "#EA580C",  # orange
    "#6B7280",  # grey
]

_SEVERITY_COLOURS = {
    "HIGH": "#DC2626",
    "MEDIUM": "#D97706",
    "LOW": "#16A34A",
    "INFO": "#6B7280",
}


def _buf_from_fig(fig: plt.Figure, dpi: int = 150) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf


def total_spend_line_chart(trends: list[CostTrend]) -> io.BytesIO:
    """
    Line chart showing total monthly AWS spend over the trend period.
    """
    if not trends:
        return _empty_chart("No Cost Explorer data available")

    # Aggregate all services by month
    all_months: set[str] = set()
    for t in trends:
        all_months.update(t.monthly_costs.keys())
    sorted_months = sorted(all_months)

    totals = [sum(t.monthly_costs.get(m, 0) for t in trends) for m in sorted_months]

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(sorted_months, totals, marker="o", linewidth=2.5, color=_PALETTE[0], markersize=7)
    ax.fill_between(sorted_months, totals, alpha=0.12, color=_PALETTE[0])

    ax.set_title("Total Monthly AWS Spend", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Month")
    ax.set_ylabel("Cost (USD)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    return _buf_from_fig(fig)


def stacked_bar_by_service(trends: list[CostTrend], top_n: int = 8) -> io.BytesIO:
    """
    Stacked bar chart showing monthly spend broken down by top N services.
    """
    if not trends:
        return _empty_chart("No Cost Explorer data available")

    # Keep only top N services by total spend
    top_trends = sorted(trends, key=lambda t: t.total_spend, reverse=True)[:top_n]

    all_months: set[str] = set()
    for t in top_trends:
        all_months.update(t.monthly_costs.keys())
    sorted_months = sorted(all_months)

    df = pd.DataFrame(
        {t.service: [t.monthly_costs.get(m, 0) for m in sorted_months] for t in top_trends},
        index=sorted_months,
    )

    fig, ax = plt.subplots(figsize=(10, 5))
    colours = _PALETTE[: len(top_trends)]
    df.plot(kind="bar", stacked=True, ax=ax, color=colours, width=0.65)

    ax.set_title(f"Monthly Spend by Service (Top {top_n})", fontsize=14, fontweight="bold", pad=12)
    ax.set_xlabel("Month")
    ax.set_ylabel("Cost (USD)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.tick_params(axis="x", rotation=30)
    ax.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.8)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    return _buf_from_fig(fig)


def savings_pie_chart(result: ScanResult) -> io.BytesIO:
    """
    Pie chart showing estimated monthly savings broken down by service.
    """
    by_service = result.findings_by_service
    labels = []
    values = []

    for service, findings in sorted(by_service.items(), key=lambda x: -sum(f.estimated_monthly_saving_usd for f in x[1])):
        total = sum(f.estimated_monthly_saving_usd for f in findings)
        if total > 0:
            labels.append(service)
            values.append(total)

    if not values:
        return _empty_chart("No savings identified")

    fig, ax = plt.subplots(figsize=(7, 5))
    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        colors=_PALETTE[: len(labels)],
        startangle=140,
        pctdistance=0.82,
    )
    for at in autotexts:
        at.set_fontsize(9)

    ax.set_title("Potential Monthly Savings by Service", fontsize=13, fontweight="bold", pad=12)
    fig.tight_layout()
    return _buf_from_fig(fig)


def findings_by_severity_bar(result: ScanResult) -> io.BytesIO:
    """
    Horizontal bar chart showing finding counts by severity.
    """
    by_sev = result.findings_by_severity
    severities = ["HIGH", "MEDIUM", "LOW", "INFO"]
    counts = [len(by_sev.get(sev, [])) for sev in severities]  # type: ignore[call-overload]
    colours = [_SEVERITY_COLOURS[s] for s in severities]

    fig, ax = plt.subplots(figsize=(7, 3))
    bars = ax.barh(severities, counts, color=colours, height=0.5)
    ax.bar_label(bars, padding=4, fontsize=10)
    ax.set_title("Findings by Severity", fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel("Number of Findings")
    ax.invert_yaxis()
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    ax.set_xlim(0, max(counts) * 1.2 if max(counts) > 0 else 10)
    fig.tight_layout()
    return _buf_from_fig(fig)


def service_trend_sparklines(trends: list[CostTrend], top_n: int = 6) -> io.BytesIO:
    """
    Small multiples (sparklines) showing individual service spend trends.
    """
    top_trends = sorted(trends, key=lambda t: t.total_spend, reverse=True)[:top_n]
    if not top_trends:
        return _empty_chart("No trend data")

    cols = 3
    rows = (len(top_trends) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(12, rows * 2.5))
    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for idx, trend in enumerate(top_trends):
        ax = axes_flat[idx]
        sorted_months = sorted(trend.monthly_costs.keys())
        costs = [trend.monthly_costs[m] for m in sorted_months]
        colour = _PALETTE[idx % len(_PALETTE)]

        ax.plot(range(len(sorted_months)), costs, marker="o", color=colour, linewidth=2, markersize=5)
        ax.fill_between(range(len(sorted_months)), costs, alpha=0.15, color=colour)

        # Highlight anomaly months
        for i, month in enumerate(sorted_months):
            if month in trend.anomaly_months:
                ax.axvline(x=i, color="red", linestyle="--", alpha=0.5, linewidth=1)

        ax.set_title(
            f"{trend.service[:30]}\n${trend.total_spend:,.0f} total | {trend.trend_pct:+.0f}%",
            fontsize=8,
            fontweight="bold",
        )
        ax.set_xticks(range(len(sorted_months)))
        ax.set_xticklabels([m[5:] for m in sorted_months], fontsize=7, rotation=45)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.tick_params(axis="y", labelsize=7)
        ax.grid(axis="y", linestyle="--", alpha=0.3)

    # Hide unused subplots
    for idx in range(len(top_trends), len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.suptitle("Service Spend Trends (Top Services)", fontsize=12, fontweight="bold", y=1.01)
    fig.tight_layout()
    return _buf_from_fig(fig)


def current_vs_optimized_bar(result: ScanResult) -> io.BytesIO:
    """
    Horizontal waterfall bar showing current monthly spend vs optimized spend
    after applying all identified savings.
    """
    total_spend_monthly = sum(t.total_spend for t in result.cost_trends) / max(len(
        {m for t in result.cost_trends for m in t.monthly_costs}
    ), 1)
    total_saving = result.total_potential_saving
    optimized = max(total_spend_monthly - total_saving, 0)

    categories = ["Current Monthly Spend", "Identified Savings", "Optimized Monthly Spend"]
    values = [total_spend_monthly, total_saving, optimized]
    colours = [_PALETTE[0], _PALETTE[2], _PALETTE[1]]

    fig, ax = plt.subplots(figsize=(9, 3.5))
    bars = ax.barh(categories, values, color=colours, height=0.5)
    ax.bar_label(bars, labels=[f"${v:,.0f}" for v in values], padding=6, fontsize=10)
    ax.set_title("Current vs Optimized Monthly Spend", fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel("USD / Month")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.invert_yaxis()
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    ax.set_xlim(0, max(values) * 1.25)
    fig.tight_layout()
    return _buf_from_fig(fig)


def savings_roadmap_projection(result: ScanResult, months: int = 12) -> io.BytesIO:
    """
    Stacked area chart projecting cumulative savings over 12 months as
    quick wins, strategic, and long-term recommendations are implemented.
    """
    quick_win_saving = sum(r.total_saving for r in result.recommendations if r.category == "quick_win")
    strategic_saving = sum(r.total_saving for r in result.recommendations if r.category == "strategic")
    long_term_saving = sum(r.total_saving for r in result.recommendations if r.category == "long_term")

    # Assume phased implementation: quick wins by month 1, strategic by month 3, long-term by month 6
    month_range = list(range(1, months + 1))
    qw = [quick_win_saving * m for m in month_range]
    st = [strategic_saving * max(0, m - 2) for m in month_range]
    lt = [long_term_saving * max(0, m - 5) for m in month_range]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.stackplot(
        month_range,
        [qw, st, lt],
        labels=["Quick Wins (Month 1)", "Strategic (Month 3)", "Long-term (Month 6)"],
        colors=[_PALETTE[1], _PALETTE[0], _PALETTE[4]],
        alpha=0.8,
    )
    ax.set_title("Cumulative Savings Projection (12 Months)", fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel("Month")
    ax.set_ylabel("Cumulative Savings (USD)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.set_xticks(month_range)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.8)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    return _buf_from_fig(fig)


def priority_matrix_quadrant(result: ScanResult) -> io.BytesIO:
    """
    Scatter plot: Effort (x-axis) vs Monthly Savings (y-axis).
    Each point is a recommendation, sized by number of findings.
    """
    if not result.recommendations:
        return _empty_chart("No recommendations available")

    effort_map = {"low": 1, "medium": 2, "high": 3}
    cat_colours = {"quick_win": _PALETTE[1], "strategic": _PALETTE[0], "long_term": _PALETTE[4]}
    cat_labels = {"quick_win": "Quick Win", "strategic": "Strategic", "long_term": "Long-term"}

    fig, ax = plt.subplots(figsize=(9, 5))

    for cat in ["quick_win", "strategic", "long_term"]:
        recs = [r for r in result.recommendations if r.category == cat]
        if not recs:
            continue
        x = [effort_map.get(r.implementation_effort, 2) + (0.1 * i) for i, r in enumerate(recs)]
        y = [r.total_saving for r in recs]
        sizes = [max(50, min(500, len(r.findings) * 40)) for r in recs]
        ax.scatter(x, y, s=sizes, color=cat_colours[cat], alpha=0.75, label=cat_labels[cat], zorder=3)

        for xi, yi, rec in zip(x, y, recs):
            ax.annotate(
                rec.title[:25] + ("…" if len(rec.title) > 25 else ""),
                (xi, yi),
                fontsize=6.5,
                ha="left",
                va="bottom",
                xytext=(4, 4),
                textcoords="offset points",
            )

    ax.set_xticks([1, 2, 3])
    ax.set_xticklabels(["Low Effort", "Medium Effort", "High Effort"])
    ax.set_title("Priority Matrix: Effort vs Savings", fontsize=13, fontweight="bold", pad=10)
    ax.set_ylabel("Estimated Monthly Saving (USD)")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(linestyle="--", alpha=0.4)
    ax.set_xlim(0.5, 3.8)
    fig.tight_layout()
    return _buf_from_fig(fig)


def coverage_heatmap(result: ScanResult, top_n: int = 15) -> io.BytesIO:
    """
    Horizontal bar chart showing top billed services, coloured green if
    a detailed analyser covers them, red if there is a coverage gap.
    """
    services = result.top_billed_services[:top_n]
    if not services:
        return _empty_chart("No billing data available")

    names = [s["service"][:35] for s in services]
    values = [s["monthly_avg"] for s in services]
    colours = [_PALETTE[1] if s["has_analyser"] else _PALETTE[2] for s in services]

    fig, ax = plt.subplots(figsize=(10, max(3, len(names) * 0.45)))
    bars = ax.barh(names, values, color=colours, height=0.65)
    ax.bar_label(bars, labels=[f"${v:,.0f}/mo" for v in values], padding=4, fontsize=8)
    ax.set_title("Top Billed Services — Coverage Status", fontsize=13, fontweight="bold", pad=10)
    ax.set_xlabel("Avg Monthly Spend (USD)")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.invert_yaxis()
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    ax.set_xlim(0, max(values) * 1.3 if values else 100)

    # Legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=_PALETTE[1], label="Analyser coverage"),
        Patch(facecolor=_PALETTE[2], label="Coverage gap"),
    ]
    ax.legend(handles=legend_elements, loc="lower right", fontsize=9)
    fig.tight_layout()
    return _buf_from_fig(fig)


def _empty_chart(message: str) -> io.BytesIO:
    """Return a simple placeholder chart with a message."""
    fig, ax = plt.subplots(figsize=(6, 2))
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, color="grey")
    ax.axis("off")
    return _buf_from_fig(fig)
