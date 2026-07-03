"""
AWS Cost Optimisation Tool — CLI entry point.

Usage:
    python main.py [options]

Run `python main.py --help` for full usage.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

import colorama
from colorama import Fore, Style
from tqdm import tqdm

from aws_client import create_session, get_account_id, get_enabled_regions
from config import Config, SUPPORTED_SERVICES_LIST
from models import Finding, Recommendation, ScanResult, Severity
from analysers.cost_explorer import CostExplorerAnalyser
from analysers.ec2 import EC2Analyser
from analysers.rds import RDSAnalyser
from analysers.s3 import S3Analyser
from analysers.lambda_ import LambdaAnalyser
from analysers.elb import ELBAnalyser
from analysers.cloudwatch import CloudWatchAnalyser
from analysers.nat_gateway import NATGatewayAnalyser
from analysers.dynamodb import DynamoDBAnalyser
from analysers.elasticache import ElastiCacheAnalyser
from analysers.ecs import ECSAnalyser
from analysers.ecr import ECRAnalyser
from analysers.data_transfer import DataTransferAnalyser
from report.pdf_builder import build_pdf
from utils.recommendation_metadata import EFFORT_MAP, RECOMMENDATION_TYPE_NAMES
from utils.service_registry import is_global_service

colorama.init(autoreset=True)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aws-cost-optimisation",
        description="Scan your AWS account for cost waste and generate an executive PDF report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --profile my-profile --output /tmp/report.pdf
  python main.py --regions us-east-1 eu-west-1 --services ec2 rds
  python main.py --min-saving 10 --months 3
  python main.py --format pdf json
        """,
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS named profile from ~/.aws/credentials (optional; if omitted, uses default credential chain: env vars, instance profile, etc.)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output PDF path (default: aws-cost-report-YYYY-MM-DD.pdf)",
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        default=None,
        help="Regions to scan (default: all enabled regions)",
    )
    parser.add_argument(
        "--services",
        nargs="+",
        default=None,
        choices=SUPPORTED_SERVICES_LIST,
        help="Services to analyse (default: all)",
    )
    parser.add_argument(
        "--min-saving",
        type=float,
        default=0.0,
        dest="min_saving",
        help="Minimum estimated monthly saving (USD) to include a finding (default: 0)",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="Months of Cost Explorer history to retrieve (default: 6)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        dest="max_workers",
        help="Max parallel threads for region scanning (default: 10)",
    )
    parser.add_argument(
        "--format",
        nargs="+",
        default=["pdf"],
        choices=["pdf", "json"],
        dest="output_formats",
        help="Output formats (default: pdf)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO-level console output",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------
def build_recommendations(
    findings: list[Finding],
    cost_trends: list,
    config: Config,
) -> list[Recommendation]:
    """
    Generate prioritised recommendations by combining resource-level findings
    with Cost Explorer spend trends.  Recommendations are categorised as
    quick_win, strategic, or long_term with ROI calculations.
    """
    from utils.service_registry import get_service_aliases

    recommendations: list[Recommendation] = []
    service_aliases = get_service_aliases()
    trends_by_service = {t.service: t for t in cost_trends}

    # Group findings by service
    by_service: dict[str, list[Finding]] = {}
    for f in findings:
        by_service.setdefault(f.service, []).append(f)

    for service, service_findings in by_service.items():
        if not service_findings:
            continue

        total_saving = sum(f.estimated_monthly_saving_usd for f in service_findings)

        # Determine base severity from findings
        severities = [f.severity for f in service_findings]
        if Severity.HIGH in severities:
            base_severity = Severity.HIGH
        elif Severity.MEDIUM in severities:
            base_severity = Severity.MEDIUM
        else:
            base_severity = Severity.LOW

        # Cross-reference with Cost Explorer trend
        trend = _find_trend(trends_by_service, service, service_aliases)
        trend_context = ""
        if trend:
            trend_context = (
                f" Cost Explorer shows ${trend.total_spend:,.0f} total spend over the analysis period "
                f"with a {trend.trend_pct:+.1f}% trend."
            )
            if trend.trend_pct > 20 and base_severity == Severity.MEDIUM:
                base_severity = Severity.HIGH
            if trend.anomaly:
                trend_context += f" Spend anomalies detected in: {', '.join(trend.anomaly_months)}."

        # Determine category from finding types
        finding_types = [f.finding_type for f in service_findings if f.finding_type]
        effort_data = _aggregate_effort(finding_types)

        # Build title and description
        title = f"Optimise {service} costs ({len(service_findings)} findings)"
        steps_all: list[str] = []

        for ft in set(finding_types):
            meta = EFFORT_MAP.get(ft, {})
            if meta.get("steps"):
                steps_all.extend(meta["steps"])

        # Use a more specific title for single-type findings when available
        if len(set(finding_types)) == 1 and finding_types:
            ft = finding_types[0]
            title = RECOMMENDATION_TYPE_NAMES.get(ft, title)

        description = (
            f"{len(service_findings)} {service} cost finding(s) identified, "
            f"with an estimated ${total_saving:,.0f}/month in savings."
            + trend_context
        )

        recommendations.append(Recommendation(
            title=title,
            description=description,
            service=service,
            findings=service_findings,
            total_saving=total_saving,
            severity=base_severity,
            category=effort_data["category"],
            implementation_effort=effort_data["effort"],
            estimated_hours=effort_data["hours"],
            risk_level=effort_data["risk"],
            risk_notes=effort_data["risk_notes"],
            implementation_steps=steps_all[:8],  # Cap at 8 steps
        ))

    # Also recommend Savings Plans / RI for high-spend services without findings
    services_with_findings = set(by_service.keys())
    for trend in cost_trends:
        if trend.total_spend < 100:
            continue
        matched = _match_service(trend.service, services_with_findings, service_aliases)
        if not matched and trend.trend_pct > 10:
            sp_meta = EFFORT_MAP.get("savings_plan", {})
            saving_estimate = trend.total_spend / max(config.months, 1) * 0.30
            recommendations.append(Recommendation(
                title=f"Consider Savings Plans / Reserved capacity for {trend.service[:40]}",
                description=(
                    f"{trend.service} has ${trend.total_spend:,.0f} total spend over the analysis period "
                    f"with a {trend.trend_pct:+.1f}% growth trend, but no specific resource waste was identified. "
                    "Consider purchasing Savings Plans or Reserved Instances to reduce on-demand costs by 30-72%."
                ),
                service=trend.service,
                findings=[],
                total_saving=saving_estimate,
                severity=Severity.MEDIUM,
                category=sp_meta.get("category", "strategic"),
                implementation_effort=sp_meta.get("effort", "medium"),
                estimated_hours=sp_meta.get("hours", 8),
                risk_level=sp_meta.get("risk", "medium"),
                risk_notes=sp_meta.get("risk_notes", ""),
                implementation_steps=sp_meta.get("steps", []),
            ))

    # Sort by total saving descending
    recommendations.sort(key=lambda r: r.total_saving, reverse=True)
    return recommendations


def _aggregate_effort(finding_types: list[str]) -> dict:
    """Determine overall effort/category from a mix of finding types."""
    if not finding_types:
        return {
            "category": "strategic", "effort": "medium",
            "hours": 2, "risk": "low", "risk_notes": "",
        }

    categories = []
    efforts = []
    hours = 0.0
    risks = []
    risk_notes_parts = []

    for ft in set(finding_types):
        meta = EFFORT_MAP.get(ft, {})
        categories.append(meta.get("category", "strategic"))
        efforts.append(meta.get("effort", "medium"))
        hours += meta.get("hours", 1)
        risks.append(meta.get("risk", "low"))
        rn = meta.get("risk_notes", "")
        if rn:
            risk_notes_parts.append(rn)

    # Overall category: if any long_term -> long_term, elif any strategic -> strategic
    if "long_term" in categories:
        cat = "long_term"
    elif "strategic" in categories:
        cat = "strategic"
    else:
        cat = "quick_win"

    # Overall effort
    effort_rank = {"low": 0, "medium": 1, "high": 2}
    max_effort = max(efforts, key=lambda e: effort_rank.get(e, 1))

    # Overall risk
    max_risk = max(risks, key=lambda r: effort_rank.get(r, 0))

    return {
        "category": cat,
        "effort": max_effort,
        "hours": round(hours, 1),
        "risk": max_risk,
        "risk_notes": " ".join(risk_notes_parts[:3]),
    }


def _find_trend(trends_by_service, service, service_aliases):
    """Find a CostTrend for a service using alias matching."""
    if service in trends_by_service:
        return trends_by_service[service]
    for display_name, aliases in service_aliases.items():
        if service == display_name:
            for alias in aliases:
                if alias in trends_by_service:
                    return trends_by_service[alias]
    # Partial match
    service_lower = service.lower()
    for key in trends_by_service:
        if service_lower in key.lower():
            return trends_by_service[key]
    return None


def _match_service(ce_name, services_with_findings, service_aliases):
    """Check if a CE service name matches any service that has findings."""
    for display_name, aliases in service_aliases.items():
        if ce_name in aliases or ce_name == display_name:
            if display_name in services_with_findings:
                return True
    return False


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def run(config: Config) -> ScanResult:
    print(f"\n{Fore.CYAN}{'='*60}")
    print("  AWS Cost Optimisation Tool")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    # Create session and get account metadata
    if config.profile:
        print(f"{Fore.YELLOW}Connecting to AWS (profile: {config.profile})...{Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}Connecting to AWS (default credential chain)...{Style.RESET_ALL}")
    session = create_session(config.profile)
    account_id = get_account_id(session)
    print(f"{Fore.GREEN}✓ Connected — Account: {account_id}{Style.RESET_ALL}")

    # Initialise live pricing
    print(f"{Fore.YELLOW}Initialising live pricing lookups...{Style.RESET_ALL}")
    from utils.cost_estimator import init_pricing
    init_pricing(session)
    print(f"{Fore.GREEN}✓ Pricing client ready{Style.RESET_ALL}")

    # Discover regions
    if config.regions:
        regions = config.regions
        print(f"{Fore.GREEN}✓ Using specified regions: {', '.join(regions)}{Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}Discovering enabled regions...{Style.RESET_ALL}")
        regions = get_enabled_regions(session)
        print(f"{Fore.GREEN}✓ Found {len(regions)} enabled regions{Style.RESET_ALL}")

    # Step 1: Cost Explorer (always runs, not region-scoped)
    print(f"\n{Fore.CYAN}[1/4] Fetching Cost Explorer data ({config.months} months)...{Style.RESET_ALL}")
    ce_analyser = CostExplorerAnalyser(session, config)
    cost_trends = ce_analyser.get_trends()
    forecast = ce_analyser.get_forecast()

    total_spend = 0.0
    if cost_trends:
        total_spend = sum(t.total_spend for t in cost_trends)
        print(f"{Fore.GREEN}✓ Retrieved trends for {len(cost_trends)} services "
              f"(${total_spend:,.0f} total spend){Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}⚠ No Cost Explorer data retrieved (check ce:GetCostAndUsage permission){Style.RESET_ALL}")

    # Step 2: Service Discovery — identify top-billed services and coverage gaps
    print(f"\n{Fore.CYAN}[2/4] Analysing billing data for service discovery...{Style.RESET_ALL}")
    top_billed = ce_analyser.get_top_billed_services(cost_trends)
    uncovered = [s for s in top_billed if not s["has_analyser"] and s["monthly_avg"] > 100]

    if top_billed:
        print(f"{Fore.GREEN}✓ Identified {len(top_billed)} services above $50/month{Style.RESET_ALL}")
    if uncovered:
        print(f"{Fore.YELLOW}⚠ {len(uncovered)} high-spend service(s) lack detailed analysis:{Style.RESET_ALL}")
        for svc in uncovered[:5]:
            print(f"    {Fore.YELLOW}• {svc['service']}: ${svc['monthly_avg']:,.0f}/month avg{Style.RESET_ALL}")

    # Fetch data transfer breakdown for the Data Transfer analyser
    dt_breakdown = ce_analyser.get_data_transfer_breakdown()

    # Step 3: Resource analysers
    print(f"\n{Fore.CYAN}[3/4] Scanning resources across {len(regions)} regions...{Style.RESET_ALL}")

    analyser_map = {
        "ec2": EC2Analyser,
        "rds": RDSAnalyser,
        "s3": S3Analyser,
        "lambda": LambdaAnalyser,
        "elb": ELBAnalyser,
        "cloudwatch": CloudWatchAnalyser,
        "nat_gateway": NATGatewayAnalyser,
        "dynamodb": DynamoDBAnalyser,
        "elasticache": ElastiCacheAnalyser,
        "ecs": ECSAnalyser,
        "ecr": ECRAnalyser,
        "data_transfer": DataTransferAnalyser,
    }

    active_services = config.effective_services()
    all_findings: list[Finding] = []

    with tqdm(total=len(active_services), desc="Services", unit="service", ncols=70) as pbar:
        for service_key in active_services:
            analyser_cls = analyser_map.get(service_key)
            if analyser_cls is None:
                pbar.update(1)
                continue

            analyser = analyser_cls(session, config)

            # Inject CE data for the Data Transfer analyser
            if service_key == "data_transfer" and hasattr(analyser, "set_cost_explorer_data"):
                analyser.set_cost_explorer_data(dt_breakdown)

            # Global services scan once with us-east-1
            if is_global_service(service_key):
                scan_regions = ["us-east-1"]
            else:
                scan_regions = regions

            try:
                findings = analyser.analyse(scan_regions)
                if config.min_saving > 0:
                    findings = [f for f in findings if f.estimated_monthly_saving_usd >= config.min_saving]
                all_findings.extend(findings)
                pbar.set_postfix({"last": service_key, "findings": len(all_findings)})
            except Exception as exc:
                logger.error("Analyser %s failed: %s", service_key, exc)
            finally:
                pbar.update(1)

    # Only include findings with positive estimated savings (report shows $ value only)
    all_findings = [f for f in all_findings if f.estimated_monthly_saving_usd > 0]

    # Deduplicate by (service, region, resource_id): keep the finding with highest saving per resource
    seen: dict[tuple[str, str, str], Finding] = {}
    for f in all_findings:
        key = (f.service, f.region, f.resource_id)
        if key not in seen or f.estimated_monthly_saving_usd > seen[key].estimated_monthly_saving_usd:
            seen[key] = f
    all_findings = list(seen.values())

    print(f"{Fore.GREEN}✓ Found {len(all_findings)} findings{Style.RESET_ALL}")

    # Step 4: Build recommendations
    print(f"\n{Fore.CYAN}[4/4] Building recommendations...{Style.RESET_ALL}")
    recommendations = build_recommendations(all_findings, cost_trends, config)
    print(f"{Fore.GREEN}✓ Generated {len(recommendations)} recommendations{Style.RESET_ALL}")

    return ScanResult(
        account_id=account_id,
        profile=config.profile or "default credential chain",
        scan_date=date.today().isoformat(),
        regions_scanned=regions,
        months_of_history=config.months,
        findings=all_findings,
        recommendations=recommendations,
        cost_trends=cost_trends,
        forecast=forecast,
        top_billed_services=top_billed,
        uncovered_high_spend=uncovered,
    )


def print_summary(result: ScanResult) -> None:
    """Print a coloured summary to the terminal."""
    print(f"\n{Fore.CYAN}{'='*60}")
    print("  SCAN SUMMARY")
    print(f"{'='*60}{Style.RESET_ALL}")
    print(f"  Account:          {result.account_id}")
    print(f"  Regions scanned:  {len(result.regions_scanned)}")
    print(f"  Total findings:   {len(result.findings)}")

    by_sev = result.findings_by_severity
    print(f"  {Fore.RED}HIGH:   {len(by_sev.get(Severity.HIGH, []))}{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}MEDIUM: {len(by_sev.get(Severity.MEDIUM, []))}{Style.RESET_ALL}")
    print(f"  {Fore.GREEN}LOW:    {len(by_sev.get(Severity.LOW, []))}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}INFO:   {len(by_sev.get(Severity.INFO, []))}{Style.RESET_ALL}")
    print(f"\n  {Fore.GREEN}Estimated monthly savings: ${result.total_potential_saving:,.2f}{Style.RESET_ALL}")

    if result.forecast:
        fc = result.forecast
        print(f"  Next month forecast: ${fc.mean_usd:,.0f} "
              f"(${fc.lower_bound_usd:,.0f} – ${fc.upper_bound_usd:,.0f})")

    # Show recommendations by category
    if result.recommendations:
        quick_wins = [r for r in result.recommendations if r.category == "quick_win"]
        strategic = [r for r in result.recommendations if r.category == "strategic"]
        long_term = [r for r in result.recommendations if r.category == "long_term"]

        qw_saving = sum(r.total_saving for r in quick_wins)
        st_saving = sum(r.total_saving for r in strategic)
        lt_saving = sum(r.total_saving for r in long_term)

        print("\n  Savings by category:")
        print(f"  {Fore.GREEN}  Quick Wins:   ${qw_saving:,.0f}/month ({len(quick_wins)} items){Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}  Strategic:    ${st_saving:,.0f}/month ({len(strategic)} items){Style.RESET_ALL}")
        print(f"  {Fore.CYAN}  Long-term:    ${lt_saving:,.0f}/month ({len(long_term)} items){Style.RESET_ALL}")

        print("\n  Top 3 recommendations:")
        for i, rec in enumerate(result.recommendations[:3], 1):
            sev_colour = {
                Severity.HIGH: Fore.RED,
                Severity.MEDIUM: Fore.YELLOW,
                Severity.LOW: Fore.GREEN,
                Severity.INFO: Fore.CYAN,
            }.get(rec.severity, "")
            cat_label = {"quick_win": "QW", "strategic": "ST", "long_term": "LT"}.get(rec.category, "")
            print(f"  {i}. {sev_colour}[{rec.severity.value}][{cat_label}]{Style.RESET_ALL} "
                  f"{rec.title} — ${rec.total_saving:,.0f}/month")

    if result.uncovered_high_spend:
        print(f"\n  {Fore.YELLOW}Coverage gaps ({len(result.uncovered_high_spend)} high-spend services without detailed analysis):{Style.RESET_ALL}")
        for svc in result.uncovered_high_spend[:3]:
            print(f"    • {svc['service']}: ${svc['monthly_avg']:,.0f}/month")


def main() -> None:
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    config = Config(
        profile=args.profile,
        regions=args.regions,
        services=args.services,
        output_path=args.output or f"aws-cost-report-{date.today().isoformat()}.pdf",
        output_formats=args.output_formats,
        min_saving=args.min_saving,
        months=args.months,
        max_workers=args.max_workers,
    )

    # Validate configuration
    errors = config.validate()
    if errors:
        for err in errors:
            print(f"{Fore.RED}ERROR: {err}{Style.RESET_ALL}")
        sys.exit(1)

    try:
        result = run(config)
        print_summary(result)

        # Generate outputs
        if "pdf" in config.output_formats:
            print(f"\n{Fore.CYAN}Generating PDF report...{Style.RESET_ALL}")
            build_pdf(result, config.output_path)
            print(f"{Fore.GREEN}✓ Report saved to: {config.output_path}{Style.RESET_ALL}")

        if "json" in config.output_formats:
            json_path = config.output_path.replace(".pdf", ".json")
            if json_path == config.output_path:
                json_path = f"aws-cost-report-{date.today().isoformat()}.json"
            print(f"\n{Fore.CYAN}Generating JSON export...{Style.RESET_ALL}")
            from report.json_exporter import export_json
            export_json(result, json_path)
            print(f"{Fore.GREEN}✓ JSON export saved to: {json_path}{Style.RESET_ALL}")

        print()

    except RuntimeError as exc:
        print(f"\n{Fore.RED}ERROR: {exc}{Style.RESET_ALL}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Scan interrupted by user.{Style.RESET_ALL}")
        sys.exit(0)


if __name__ == "__main__":
    main()
