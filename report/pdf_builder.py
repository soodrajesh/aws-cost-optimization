"""
PDF report builder using ReportLab.

Produces an executive-style PDF with:
1. Cover page
2. Executive summary
3. Spend trends (Cost Explorer charts)
4. Service breakdown table
5. Per-service findings sections
6. Commitment-based savings analysis
"""

from __future__ import annotations

import logging
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from models import Recommendation, ScanResult, Severity
from report import charts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour constants
# ---------------------------------------------------------------------------
_BRAND_BLUE = colors.HexColor("#1E40AF")
_BRAND_LIGHT_BLUE = colors.HexColor("#DBEAFE")
_SEVERITY_COLOURS = {
    Severity.HIGH: colors.HexColor("#DC2626"),
    Severity.MEDIUM: colors.HexColor("#D97706"),
    Severity.LOW: colors.HexColor("#16A34A"),
    Severity.INFO: colors.HexColor("#6B7280"),
}
_TABLE_HEADER_BG = colors.HexColor("#1E3A5F")
_TABLE_ALT_ROW = colors.HexColor("#F0F4FF")
_LIGHT_GREY = colors.HexColor("#F3F4F6")
_BORDER_GREY = colors.HexColor("#D1D5DB")

PAGE_W, PAGE_H = A4


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------
def _styles():
    base = getSampleStyleSheet()
    custom = {
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base["Title"],
            fontSize=32,
            textColor=colors.white,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=base["Normal"],
            fontSize=14,
            textColor=colors.HexColor("#BFDBFE"),
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta",
            parent=base["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#93C5FD"),
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontSize=18,
            textColor=_BRAND_BLUE,
            spaceBefore=14,
            spaceAfter=6,
            borderPad=4,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=13,
            textColor=_BRAND_BLUE,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontSize=9.5,
            leading=14,
            spaceAfter=4,
        ),
        "body_small": ParagraphStyle(
            "body_small",
            parent=base["Normal"],
            fontSize=8.5,
            leading=12,
        ),
        "table_header": ParagraphStyle(
            "table_header",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.white,
            fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "table_cell": ParagraphStyle(
            "table_cell",
            parent=base["Normal"],
            fontSize=8.5,
            leading=11,
            wordWrap="CJK",
        ),
        "table_cell_right": ParagraphStyle(
            "table_cell_right",
            parent=base["Normal"],
            fontSize=8.5,
            leading=11,
            alignment=TA_RIGHT,
        ),
        "callout": ParagraphStyle(
            "callout",
            parent=base["Normal"],
            fontSize=9.5,
            leading=14,
            leftIndent=12,
            rightIndent=12,
            spaceBefore=6,
            spaceAfter=6,
            backColor=_BRAND_LIGHT_BLUE,
            borderColor=_BRAND_BLUE,
            borderWidth=1,
            borderPad=8,
            borderRadius=4,
        ),
        "recommendation_title": ParagraphStyle(
            "recommendation_title",
            parent=base["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=_BRAND_BLUE,
            spaceBefore=8,
            spaceAfter=2,
        ),
    }
    return {**{k: base[k] for k in base.byName}, **custom}


# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------
class _CoverCanvas:
    """
    Draws the entire cover page directly on the canvas.
    Using canvas.drawString() avoids all ReportLab Paragraph leading/overlap issues.
    The ScanResult is attached to the doc object before build() is called.
    """

    def __init__(self, canvas, doc):
        canvas.saveState()

        # ── Background ──────────────────────────────────────────────────────
        canvas.setFillColor(_BRAND_BLUE)
        canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        # Top band
        canvas.setFillColor(colors.HexColor("#2563EB"))
        canvas.rect(0, PAGE_H - 3.5 * cm, PAGE_W, 3.5 * cm, fill=1, stroke=0)

        # Thin accent line
        canvas.setFillColor(colors.HexColor("#60A5FA"))
        canvas.rect(0, PAGE_H - 3.5 * cm - 3, PAGE_W, 3, fill=1, stroke=0)

        # Bottom strip
        canvas.setFillColor(colors.HexColor("#1E3A8A"))
        canvas.rect(0, 0, PAGE_W, 1.5 * cm, fill=1, stroke=0)

        # ── Text (all drawn directly — no Paragraph flowables) ───────────────
        cx = PAGE_W / 2  # horizontal centre

        # Title
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 30)
        canvas.drawCentredString(cx, PAGE_H * 0.72, "AWS Cost Optimisation")

        # Subtitle
        canvas.setFillColor(colors.HexColor("#BFDBFE"))
        canvas.setFont("Helvetica", 15)
        canvas.drawCentredString(cx, PAGE_H * 0.68, "Executive Report")

        # Divider line
        canvas.setStrokeColor(colors.HexColor("#3B82F6"))
        canvas.setLineWidth(1)
        canvas.line(PAGE_W * 0.2, PAGE_H * 0.645, PAGE_W * 0.8, PAGE_H * 0.645)

        # Meta info
        result = getattr(doc, "_cover_result", None)
        canvas.setFillColor(colors.HexColor("#93C5FD"))
        canvas.setFont("Helvetica", 10)
        meta_y = PAGE_H * 0.60
        line_gap = 0.55 * cm
        if result:
            for line in [
                f"Account: {result.account_id}",
                f"Profile:  {result.profile}",
                f"Date:  {result.scan_date}",
                f"Regions scanned:  {len(result.regions_scanned)}",
            ]:
                canvas.drawCentredString(cx, meta_y, line)
                meta_y -= line_gap

        # Savings amount
        saving = result.total_potential_saving if result else 0
        canvas.setFillColor(colors.HexColor("#34D399"))
        canvas.setFont("Helvetica-Bold", 48)
        canvas.drawCentredString(cx, PAGE_H * 0.38, f"${saving:,.0f}")

        # Savings label
        canvas.setFillColor(colors.HexColor("#A7F3D0"))
        canvas.setFont("Helvetica", 13)
        canvas.drawCentredString(cx, PAGE_H * 0.33, "estimated monthly savings identified")

        canvas.restoreState()


class _ContentCanvas:
    """Draws header/footer on content pages."""

    def __init__(self, canvas, doc):
        canvas.saveState()
        # Header bar
        canvas.setFillColor(_BRAND_BLUE)
        canvas.rect(0, PAGE_H - 1.2 * cm, PAGE_W, 1.2 * cm, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(colors.white)
        canvas.drawString(1.5 * cm, PAGE_H - 0.85 * cm, "AWS Cost Optimisation Report")
        canvas.drawRightString(PAGE_W - 1.5 * cm, PAGE_H - 0.85 * cm, doc.title or "")

        # Footer
        canvas.setFillColor(_BORDER_GREY)
        canvas.rect(0, 0, PAGE_W, 0.8 * cm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(1.5 * cm, 0.25 * cm, f"Generated: {date.today().isoformat()}")
        canvas.drawCentredString(PAGE_W / 2, 0.25 * cm, "Confidential — Internal Use Only")
        canvas.drawRightString(PAGE_W - 1.5 * cm, 0.25 * cm, f"Page {doc.page}")
        canvas.restoreState()


def _build_doc(output_path: str, account_id: str) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=2.0 * cm,
        bottomMargin=1.5 * cm,
        title=f"AWS Cost Report — {account_id}",
        author="AWS Cost Optimisation Tool",
    )

    # Cover frame: centred horizontally with generous side padding so text
    # doesn't run edge-to-edge on the blue background.
    cover_frame = Frame(
        2.5 * cm, 0,
        PAGE_W - 5 * cm, PAGE_H,
        id="cover",
        showBoundary=0,
    )
    content_frame = Frame(
        1.5 * cm,
        1.2 * cm,
        PAGE_W - 3 * cm,
        PAGE_H - 3.2 * cm,
        id="content",
        showBoundary=0,
    )

    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=lambda c, d: _CoverCanvas(c, d)),
        PageTemplate(id="Content", frames=[content_frame], onPage=lambda c, d: _ContentCanvas(c, d)),
    ])
    return doc


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------
def _cover_page(result: ScanResult, s: dict, doc: BaseDocTemplate) -> list:
    # Attach result to doc so _CoverCanvas can access it during the onPage callback.
    doc._cover_result = result
    # All cover content is drawn directly in _CoverCanvas — no Paragraph flowables needed.
    # NextPageTemplate("Cover") applies to the current page (triggered by the first content).
    # NextPageTemplate("Content") must come just before PageBreak so the *next* page is white.
    # A minimal Spacer forces the Cover template to be activated before we switch away.
    return [
        NextPageTemplate("Cover"),
        Spacer(1, 1),           # triggers the Cover template on this page
        NextPageTemplate("Content"),
        PageBreak(),            # ends the cover page; next page uses Content
    ]


def _executive_summary(result: ScanResult, s: dict) -> list:
    # Template already set to Content by _cover_page; no need to set it again here.
    elements = [Paragraph("Executive Summary", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))

    total_spend = sum(t.total_spend for t in result.cost_trends)
    months = result.months_of_history or 6
    monthly_avg = total_spend / months if months else 0
    total_saving = result.total_potential_saving
    findings_count = len(result.findings)
    by_sev = result.findings_by_severity
    quick_wins = [r for r in result.recommendations if r.category == "quick_win" and r.total_saving > 0]
    qw_saving = sum(r.total_saving for r in quick_wins)

    # Dynamic narrative paragraph
    narrative = (
        f"This report covers <b>{len(result.regions_scanned)} AWS regions</b> scanned on "
        f"{result.scan_date}, analysing {len(result.findings)} findings across "
        f"{len(result.findings_by_service)} services. "
        f"Your account spent <b>${total_spend:,.0f}</b> over the last {months} months "
        f"(${monthly_avg:,.0f}/month average). "
        f"We identified <b>${total_saving:,.0f}/month</b> in potential savings — "
        f"equivalent to <b>${total_saving * 12:,.0f}/year</b>. "
    )
    if quick_wins:
        narrative += (
            f"<b>{len(quick_wins)} quick-win actions</b> can save "
            f"<b>${qw_saving:,.0f}/month</b> with low effort and low risk."
        )
    elements.append(Paragraph(narrative, s["body"]))
    elements.append(Spacer(1, 0.3 * cm))

    # Summary stat boxes
    stats = [
        ("Total Spend (6 months)", f"${total_spend:,.0f}"),
        ("Potential Monthly Savings", f"${total_saving:,.0f}"),
        ("Total Findings", str(findings_count)),
        ("High Severity", str(len(by_sev.get(Severity.HIGH, [])))),
    ]
    stat_data = [[Paragraph(label, s["table_header"]) for label, _ in stats],
                 [Paragraph(value, ParagraphStyle("stat_val", fontSize=16, fontName="Helvetica-Bold",
                                                   alignment=TA_CENTER, textColor=_BRAND_BLUE))
                  for _, value in stats]]
    stat_table = Table(stat_data, colWidths=[(PAGE_W - 3 * cm) / 4] * 4, rowHeights=[22, 30])
    stat_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
        ("BACKGROUND", (0, 1), (-1, 1), _BRAND_LIGHT_BLUE),
        ("BOX", (0, 0), (-1, -1), 1, _BORDER_GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _BORDER_GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(stat_table)
    elements.append(Spacer(1, 0.3 * cm))

    # Quick wins callout
    if quick_wins:
        elements.append(Paragraph(
            f"<b>Quick Wins Available:</b> {len(quick_wins)} low-effort actions can save "
            f"<font color='#16A34A'><b>${qw_saving:,.0f}/month</b></font> — "
            "implementable within days with minimal risk.",
            s["callout"],
        ))

    # Forecast
    if result.forecast:
        fc = result.forecast
        elements.append(Paragraph(
            f"<b>Next Month Forecast:</b> ${fc.mean_usd:,.0f} "
            f"(range: ${fc.lower_bound_usd:,.0f} – ${fc.upper_bound_usd:,.0f})",
            s["callout"],
        ))

    # Current vs optimized bar chart
    try:
        opt_buf = charts.current_vs_optimized_bar(result)
        elements.append(Image(opt_buf, width=PAGE_W - 3 * cm, height=5 * cm))
        elements.append(Spacer(1, 0.2 * cm))
    except Exception as exc:
        logger.warning("Could not render current vs optimized chart: %s", exc)

    # Charts side by side
    try:
        pie_buf = charts.savings_pie_chart(result)
        sev_buf = charts.findings_by_severity_bar(result)
        chart_table = Table(
            [[Image(pie_buf, width=8.5 * cm, height=6 * cm),
              Image(sev_buf, width=8.5 * cm, height=6 * cm)]],
            colWidths=[9 * cm, 9 * cm],
        )
        chart_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(chart_table)
    except Exception as exc:
        logger.warning("Could not render summary charts: %s", exc)

    # Top 3 recommendations (only those with positive savings)
    recs_with_savings = [r for r in result.recommendations if r.total_saving > 0]
    if recs_with_savings:
        elements.append(Spacer(1, 0.3 * cm))
        elements.append(Paragraph("Top Recommendations", s["h2"]))
        top3 = sorted(recs_with_savings, key=lambda r: r.total_saving, reverse=True)[:3]
        for i, rec in enumerate(top3, 1):
            cat_badge = {"quick_win": "[QW]", "strategic": "[ST]", "long_term": "[LT]"}.get(rec.category, "")
            elements.append(Paragraph(
                f"<b>{i}. {cat_badge} [{rec.severity.value}] {rec.title}</b> — "
                f"<font color='#16A34A'>${rec.total_saving:,.0f}/month</font>",
                s["recommendation_title"],
            ))
            elements.append(Paragraph(rec.description[:200], s["body_small"]))

    elements.append(PageBreak())
    return elements


def _spend_trends_section(result: ScanResult, s: dict) -> list:
    elements = [Paragraph("AWS Spend Trends", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))

    if not result.cost_trends:
        elements.append(Paragraph(
            "Cost Explorer data could not be retrieved. Ensure the AWS profile has "
            "ce:GetCostAndUsage permission.", s["body"]
        ))
        elements.append(PageBreak())
        return elements

    months = result.months_of_history
    elements.append(Paragraph(
        f"The following charts show your AWS spend over the last {months} months, "
        "sourced from AWS Cost Explorer.", s["body"]
    ))

    try:
        line_buf = charts.total_spend_line_chart(result.cost_trends)
        elements.append(Image(line_buf, width=PAGE_W - 3 * cm, height=7 * cm))
        elements.append(Spacer(1, 0.3 * cm))
    except Exception as exc:
        logger.warning("Could not render line chart: %s", exc)

    try:
        bar_buf = charts.stacked_bar_by_service(result.cost_trends)
        elements.append(Image(bar_buf, width=PAGE_W - 3 * cm, height=8 * cm))
        elements.append(Spacer(1, 0.3 * cm))
    except Exception as exc:
        logger.warning("Could not render stacked bar chart: %s", exc)

    try:
        sparklines_buf = charts.service_trend_sparklines(result.cost_trends)
        elements.append(Image(sparklines_buf, width=PAGE_W - 3 * cm, height=10 * cm))
    except Exception as exc:
        logger.warning("Could not render sparklines: %s", exc)

    elements.append(PageBreak())
    return elements


def _service_breakdown_table(result: ScanResult, s: dict) -> list:
    elements = [Paragraph("Service Cost Breakdown", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))

    if not result.cost_trends:
        elements.append(PageBreak())
        return elements

    elements.append(Paragraph(
        "Services marked with ⚠ had month-over-month spend growth exceeding 20%.", s["body"]
    ))

    col_widths = [6.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm]
    header = [Paragraph(h, s["table_header"]) for h in
              ["Service", "Total Spend", "Trend", "Anomaly", "Findings"]]
    rows = [header]

    findings_by_service = result.findings_by_service

    for trend in result.cost_trends[:30]:  # cap at 30 rows
        service_findings = findings_by_service.get(trend.service, [])
        trend_str = f"{trend.trend_pct:+.1f}%"
        flag = " ⚠" if trend.trend_pct > 20 else ""
        anomaly_str = "Yes" if trend.anomaly else "No"

        trend_colour = "#DC2626" if trend.trend_pct > 20 else ("#16A34A" if trend.trend_pct < -5 else "#374151")

        rows.append([
            Paragraph(f"{trend.service[:40]}{flag}", s["table_cell"]),
            Paragraph(f"${trend.total_spend:,.0f}", s["table_cell_right"]),
            Paragraph(f"<font color='{trend_colour}'>{trend_str}</font>", s["table_cell_right"]),
            Paragraph(anomaly_str, s["table_cell"]),
            Paragraph(str(len(service_findings)), s["table_cell_right"]),
        ])

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(_default_table_style(len(rows)))
    elements.append(table)
    elements.append(PageBreak())
    return elements


def _severity_font(colour, label: str) -> str:
    """Return a ReportLab XML string with the severity label coloured appropriately."""
    hex_colour = colour.hexval() if hasattr(colour, "hexval") else "#374151"
    return f"<font color='{hex_colour}'><b>{label}</b></font>"


def _savings_roadmap(result: ScanResult, s: dict) -> list:
    """Savings roadmap: priority matrix table, ROI summary, and cumulative projection chart."""
    elements = [Paragraph("Savings Roadmap", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))
    elements.append(Paragraph(
        "Recommendations are categorised by implementation effort and risk. "
        "Quick Wins can be actioned immediately; Strategic items require planning; "
        "Long-term items involve architectural changes.",
        s["body"],
    ))

    # Priority matrix table
    cat_order = ["quick_win", "strategic", "long_term"]
    cat_labels = {"quick_win": "Quick Win", "strategic": "Strategic", "long_term": "Long-term"}
    cat_colours_rl = {
        "quick_win": colors.HexColor("#D1FAE5"),
        "strategic": colors.HexColor("#DBEAFE"),
        "long_term": colors.HexColor("#EDE9FE"),
    }

    matrix_header = [Paragraph(h, s["table_header"]) for h in
                     ["Category", "Items", "Monthly Savings", "Annual Savings", "Est. Hours", "ROI"]]
    matrix_rows = [matrix_header]
    matrix_row_colours = []

    for cat in cat_order:
        recs = [r for r in result.recommendations if r.category == cat and r.total_saving > 0]
        if not recs:
            continue
        monthly = sum(r.total_saving for r in recs)
        annual = monthly * 12
        hours = sum(r.estimated_hours for r in recs)
        impl_cost = hours * 150
        roi = round(annual / impl_cost, 1) if impl_cost > 0 else 0.0
        matrix_rows.append([
            Paragraph(f"<b>{cat_labels[cat]}</b>", s["table_cell"]),
            Paragraph(str(len(recs)), s["table_cell_right"]),
            Paragraph(f"${monthly:,.0f}", s["table_cell_right"]),
            Paragraph(f"${annual:,.0f}", s["table_cell_right"]),
            Paragraph(f"{hours:.0f}h", s["table_cell_right"]),
            Paragraph(f"{roi:.1f}x", s["table_cell_right"]),
        ])
        matrix_row_colours.append(cat_colours_rl[cat])

    col_widths = [3.5 * cm, 1.5 * cm, 3 * cm, 3 * cm, 2.5 * cm, 2.5 * cm]
    matrix_table = Table(matrix_rows, colWidths=col_widths, repeatRows=1)
    matrix_table.setStyle(_default_table_style(len(matrix_rows)))
    # Highlight each category row with its matrix colour (overrides the
    # generic alternating row shading applied above).
    for row_idx, colour in enumerate(matrix_row_colours, start=1):
        matrix_table.setStyle(TableStyle([("BACKGROUND", (0, row_idx), (-1, row_idx), colour)]))
    elements.append(matrix_table)
    elements.append(Spacer(1, 0.4 * cm))

    # Projection chart + priority matrix side by side
    try:
        proj_buf = charts.savings_roadmap_projection(result)
        pm_buf = charts.priority_matrix_quadrant(result)
        chart_table = Table(
            [[Image(proj_buf, width=9 * cm, height=6 * cm),
              Image(pm_buf, width=9 * cm, height=6 * cm)]],
            colWidths=[9.5 * cm, 9.5 * cm],
        )
        chart_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(chart_table)
    except Exception as exc:
        logger.warning("Could not render roadmap charts: %s", exc)

    elements.append(PageBreak())
    return elements


def _coverage_gap_analysis(result: ScanResult, s: dict) -> list:
    """Coverage gap analysis: high-spend services without detailed analysis."""
    elements = [Paragraph("Coverage Gap Analysis", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))

    # Coverage heatmap
    try:
        heatmap_buf = charts.coverage_heatmap(result)
        elements.append(Image(heatmap_buf, width=PAGE_W - 3 * cm, height=max(4 * cm, min(10 * cm, len(result.top_billed_services) * 0.5 * cm))))
        elements.append(Spacer(1, 0.3 * cm))
    except Exception as exc:
        logger.warning("Could not render coverage heatmap: %s", exc)

    if result.uncovered_high_spend:
        elements.append(Paragraph(
            f"The following {len(result.uncovered_high_spend)} high-spend services "
            "do not have a dedicated resource-level analyser. "
            "Consider investigating these manually or adding custom analysis.",
            s["body"],
        ))

        col_widths = [6 * cm, 2.5 * cm, 2.5 * cm, 5.5 * cm]
        header = [Paragraph(h, s["table_header"]) for h in
                  ["Service", "Avg/Month", "Trend", "Recommendation"]]
        rows = [header]

        for svc in result.uncovered_high_spend[:20]:
            trend_str = f"{svc['trend_pct']:+.1f}%"
            trend_colour = "#DC2626" if svc["trend_pct"] > 20 else "#374151"
            rows.append([
                Paragraph(svc["service"][:50], s["table_cell"]),
                Paragraph(f"${svc['monthly_avg']:,.0f}", s["table_cell_right"]),
                Paragraph(f"<font color='{trend_colour}'>{trend_str}</font>", s["table_cell_right"]),
                Paragraph(
                    "Review Savings Plans / Reserved Instances" if svc["trend_pct"] > 0
                    else "Monitor for further decline",
                    s["table_cell"],
                ),
            ])

        table = Table(rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(_default_table_style(len(rows)))
        elements.append(table)
    else:
        elements.append(Paragraph(
            "All high-spend services (>$100/month) have detailed analyser coverage.", s["body"]
        ))

    elements.append(PageBreak())
    return elements


def _commitment_savings_analysis(result: ScanResult, s: dict) -> list:
    """Commitment-based savings: RI/Savings Plans candidates from stable high-spend services."""
    import statistics as stats_lib

    elements = [Paragraph("Commitment-Based Savings Analysis", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))
    elements.append(Paragraph(
        "Services with stable, high spend are strong candidates for Reserved Instances (RI) "
        "or Savings Plans, which can reduce on-demand costs by 30–72%. "
        "Stability is measured by coefficient of variation (CV < 0.3 = stable).",
        s["body"],
    ))

    candidates = []
    for trend in result.cost_trends:
        if trend.total_spend < (500 * (result.months_of_history or 6) / 6):
            continue
        costs = list(trend.monthly_costs.values())
        if len(costs) < 2:
            continue
        mean = stats_lib.mean(costs)
        if mean == 0:
            continue
        try:
            cv = stats_lib.stdev(costs) / mean
        except stats_lib.StatisticsError:
            cv = 0.0
        if cv < 0.3:
            monthly_avg = trend.total_spend / max(result.months_of_history or 6, 1)
            candidates.append({
                "service": trend.service,
                "monthly_avg": monthly_avg,
                "cv": cv,
                "conservative_saving": monthly_avg * 0.30,
                "aggressive_saving": monthly_avg * 0.50,
            })

    if not candidates:
        elements.append(Paragraph(
            "No services met the stability threshold for RI/Savings Plans analysis "
            "(requires >$500/month with CV < 0.3).", s["body"]
        ))
        elements.append(PageBreak())
        return elements

    total_conservative = sum(c["conservative_saving"] for c in candidates)
    total_aggressive = sum(c["aggressive_saving"] for c in candidates)
    elements.append(Paragraph(
        f"<b>{len(candidates)} services</b> are strong RI/Savings Plans candidates. "
        f"Conservative estimate (30% saving): <font color='#16A34A'><b>${total_conservative:,.0f}/month</b></font>. "
        f"Aggressive estimate (50% saving): <font color='#16A34A'><b>${total_aggressive:,.0f}/month</b></font>.",
        s["callout"],
    ))
    elements.append(Spacer(1, 0.3 * cm))

    col_widths = [6 * cm, 2.5 * cm, 2 * cm, 3 * cm, 3 * cm]
    header = [Paragraph(h, s["table_header"]) for h in
              ["Service", "Avg/Month", "CV", "30% Saving", "50% Saving"]]
    rows = [header]

    for c in sorted(candidates, key=lambda x: -x["monthly_avg"]):
        rows.append([
            Paragraph(c["service"][:45], s["table_cell"]),
            Paragraph(f"${c['monthly_avg']:,.0f}", s["table_cell_right"]),
            Paragraph(f"{c['cv']:.2f}", s["table_cell_right"]),
            Paragraph(f"${c['conservative_saving']:,.0f}", s["table_cell_right"]),
            Paragraph(f"${c['aggressive_saving']:,.0f}", s["table_cell_right"]),
        ])

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(_default_table_style(len(rows)))
    elements.append(table)
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(
        "<b>Recommended actions:</b> (1) Use AWS Cost Explorer Savings Plans recommendations. "
        "(2) Start with a 1-year, no-upfront Compute Savings Plan for flexibility. "
        "(3) For RDS, evaluate RDS Reserved Instances per engine and region.",
        s["body_small"],
    ))
    elements.append(PageBreak())
    return elements


def _per_service_sections(result: ScanResult, s: dict) -> list:
    elements = [Paragraph("Findings & Recommendations", s["h1"])]
    elements.append(HRFlowable(width="100%", thickness=1, color=_BRAND_BLUE, spaceAfter=8))

    # Group recommendations by service
    recs_by_service: dict[str, list[Recommendation]] = {}
    for rec in result.recommendations:
        recs_by_service.setdefault(rec.service, []).append(rec)

    # Group trends by service name for cross-referencing
    trends_by_service = {t.service: t for t in result.cost_trends}

    services_with_findings = sorted(result.findings_by_service.keys())

    for service in services_with_findings:
        findings = result.findings_by_service[service]
        if not findings:
            continue

        elements.append(Paragraph(f"{service}", s["h2"]))

        # Cross-reference with Cost Explorer trend
        # Try to match service name (CE uses full names like "Amazon EC2")
        trend = _find_trend(trends_by_service, service)
        if trend:
            trend_note = (
                f"Cost Explorer: ${trend.total_spend:,.0f} total spend over "
                f"{result.months_of_history} months, trend {trend.trend_pct:+.1f}%."
            )
            if trend.anomaly:
                trend_note += f" Anomalous spend detected in: {', '.join(trend.anomaly_months)}."
            elements.append(Paragraph(trend_note, s["callout"]))

        # Recommendations for this service (only show those with positive savings)
        for rec in (r for r in recs_by_service.get(service, []) if r.total_saving > 0):
            cat_badge = {"quick_win": "Quick Win", "strategic": "Strategic", "long_term": "Long-term"}.get(rec.category, rec.category)
            cat_colour = {"quick_win": "#16A34A", "strategic": "#2563EB", "long_term": "#7C3AED"}.get(rec.category, "#374151")
            elements.append(Paragraph(
                f"<b>[{rec.severity.value}]</b> "
                f"<font color='{cat_colour}'>[{cat_badge}]</font> "
                f"<b>{rec.title}</b> — "
                f"<font color='#16A34A'>${rec.total_saving:,.0f}/month</font> "
                f"| Effort: {rec.implementation_effort} | ~{rec.estimated_hours:.0f}h",
                s["recommendation_title"],
            ))
            elements.append(Paragraph(rec.description, s["body_small"]))

            # Risk callout for medium/high risk
            if rec.risk_level in ("medium", "high") and rec.risk_notes:
                risk_bg = colors.HexColor("#FEF3C7") if rec.risk_level == "medium" else colors.HexColor("#FEE2E2")
                risk_style = ParagraphStyle(
                    "risk_callout",
                    parent=s["body_small"],
                    backColor=risk_bg,
                    borderColor=colors.HexColor("#D97706") if rec.risk_level == "medium" else colors.HexColor("#DC2626"),
                    borderWidth=1,
                    borderPad=6,
                    leftIndent=8,
                    rightIndent=8,
                    spaceBefore=3,
                    spaceAfter=3,
                )
                elements.append(Paragraph(f"<b>Risk ({rec.risk_level.upper()}):</b> {rec.risk_notes}", risk_style))

            # Implementation steps
            if rec.implementation_steps:
                elements.append(Paragraph("<b>Implementation steps:</b>", s["body_small"]))
                for i, step in enumerate(rec.implementation_steps[:6], 1):
                    elements.append(Paragraph(f"  {i}. {step}", s["body_small"]))

            elements.append(Spacer(1, 0.3 * cm))

        # Findings table (Issue column allows full text to wrap; no truncation)
        col_widths = [3.5 * cm, 6 * cm, 2.5 * cm, 2.5 * cm, 3.5 * cm]
        header = [Paragraph(h, s["table_header"]) for h in
                  ["Resource", "Issue", "Region", "Severity", "Est. Saving/mo"]]
        rows = [header]

        for i, finding in enumerate(sorted(findings, key=lambda f: f.severity.value)):
            sev_col = _SEVERITY_COLOURS.get(finding.severity, colors.grey)
            rows.append([
                Paragraph(finding.resource_name[:40], s["table_cell"]),
                Paragraph(finding.issue, s["table_cell"]),
                Paragraph(finding.region, s["table_cell"]),
                Paragraph(
                    _severity_font(sev_col, finding.severity.value),
                    s["table_cell"],
                ),
                Paragraph(
                    f"${finding.estimated_monthly_saving_usd:,.2f}" if finding.estimated_monthly_saving_usd > 0 else "—",
                    s["table_cell_right"],
                ),
            ])

        table = Table(rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(_default_table_style(len(rows)))
        elements.append(table)
        elements.append(Spacer(1, 0.5 * cm))

    elements.append(PageBreak())
    return elements


# ---------------------------------------------------------------------------
# Table style helper
# ---------------------------------------------------------------------------
def _default_table_style(num_rows: int) -> TableStyle:
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER_GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, _BORDER_GREY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _TABLE_ALT_ROW]),
    ]
    return TableStyle(style_cmds)


# ---------------------------------------------------------------------------
# Trend matching helper
# ---------------------------------------------------------------------------
_SERVICE_ALIASES: dict[str, list[str]] = {
    "EC2": ["Amazon EC2", "EC2 - Other", "Amazon Elastic Compute Cloud"],
    "RDS": ["Amazon RDS Service", "Amazon Relational Database Service"],
    "S3": ["Amazon Simple Storage Service", "Amazon S3"],
    "Lambda": ["AWS Lambda"],
    "ELB": ["Amazon Elastic Load Balancing", "AWS Elastic Load Balancing"],
    "CloudWatch": ["Amazon CloudWatch", "AmazonCloudWatch"],
    "IAM": ["AWS Identity and Access Management", "AWS IAM"],
}


def _find_trend(trends_by_service: dict, service: str):
    """Find a CostTrend for a given short service name using alias matching."""
    # Direct match
    if service in trends_by_service:
        return trends_by_service[service]
    # Alias match
    for alias in _SERVICE_ALIASES.get(service, []):
        if alias in trends_by_service:
            return trends_by_service[alias]
    # Partial match
    service_lower = service.lower()
    for key in trends_by_service:
        if service_lower in key.lower():
            return trends_by_service[key]
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_pdf(result: ScanResult, output_path: str) -> None:
    """Assemble and write the full PDF report to output_path."""
    logger.info("Building PDF report: %s", output_path)
    s = _styles()
    doc = _build_doc(output_path, result.account_id)

    story = []
    story.extend(_cover_page(result, s, doc))
    story.extend(_executive_summary(result, s))
    story.extend(_savings_roadmap(result, s))
    story.extend(_spend_trends_section(result, s))
    story.extend(_service_breakdown_table(result, s))
    story.extend(_coverage_gap_analysis(result, s))
    story.extend(_per_service_sections(result, s))
    story.extend(_commitment_savings_analysis(result, s))

    doc.build(story)
    logger.info("PDF report written to %s", output_path)
