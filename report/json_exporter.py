"""
JSON export for the AWS Cost Optimisation tool.

Serialises a ScanResult to a structured JSON file using dataclasses.asdict(),
with custom handling for non-serialisable types (Enum, datetime, etc.).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any

from models import ScanResult

logger = logging.getLogger(__name__)


def _default_serialiser(obj: Any) -> Any:
    """JSON serialiser for types not handled by the default encoder."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def _scan_result_to_dict(result: ScanResult) -> dict:
    """Convert ScanResult to a clean, structured dict for JSON export."""
    return {
        "meta": {
            "account_id": result.account_id,
            "profile": result.profile,
            "scan_date": result.scan_date,
            "regions_scanned": result.regions_scanned,
            "months_of_history": result.months_of_history,
            "total_findings": len(result.findings),
            "total_potential_monthly_saving_usd": round(result.total_potential_saving, 2),
        },
        "forecast": dataclasses.asdict(result.forecast) if result.forecast else None,
        "cost_trends": [
            {
                "service": t.service,
                "total_spend_usd": round(t.total_spend, 2),
                "trend_pct": round(t.trend_pct, 1),
                "anomaly": t.anomaly,
                "anomaly_months": t.anomaly_months,
                "monthly_costs": {k: round(v, 2) for k, v in t.monthly_costs.items()},
            }
            for t in result.cost_trends
        ],
        "top_billed_services": result.top_billed_services,
        "uncovered_high_spend": result.uncovered_high_spend,
        "findings": [
            {
                "service": f.service,
                "region": f.region,
                "resource_id": f.resource_id,
                "resource_name": f.resource_name,
                "issue": f.issue,
                "finding_type": f.finding_type,
                "severity": f.severity.value,
                "estimated_monthly_saving_usd": round(f.estimated_monthly_saving_usd, 2),
                "details": f.details,
            }
            for f in sorted(result.findings, key=lambda x: x.estimated_monthly_saving_usd, reverse=True)
        ],
        "recommendations": [
            {
                "title": r.title,
                "description": r.description,
                "service": r.service,
                "severity": r.severity.value,
                "category": r.category,
                "implementation_effort": r.implementation_effort,
                "estimated_hours": r.estimated_hours,
                "risk_level": r.risk_level,
                "risk_notes": r.risk_notes,
                "total_monthly_saving_usd": round(r.total_saving, 2),
                "annualized_saving_usd": round(r.annualized_saving, 2),
                "roi_multiple": r.roi_multiple,
                "implementation_steps": r.implementation_steps,
                "finding_count": len(r.findings),
            }
            for r in result.recommendations
        ],
        "summary_by_severity": {
            sev.value: len(findings)
            for sev, findings in result.findings_by_severity.items()
        },
        "summary_by_service": {
            service: {
                "finding_count": len(findings),
                "total_saving_usd": round(sum(f.estimated_monthly_saving_usd for f in findings), 2),
            }
            for service, findings in result.findings_by_service.items()
        },
        "summary_by_category": _category_summary(result),
    }


def _category_summary(result: ScanResult) -> dict:
    """Summarise recommendations by category."""
    categories: dict[str, dict] = {}
    for rec in result.recommendations:
        cat = rec.category
        if cat not in categories:
            categories[cat] = {"count": 0, "total_monthly_saving_usd": 0.0, "total_hours": 0.0}
        categories[cat]["count"] += 1
        categories[cat]["total_monthly_saving_usd"] += rec.total_saving
        categories[cat]["total_hours"] += rec.estimated_hours
    # Round values
    for cat in categories:
        categories[cat]["total_monthly_saving_usd"] = round(
            categories[cat]["total_monthly_saving_usd"], 2
        )
        categories[cat]["total_hours"] = round(categories[cat]["total_hours"], 1)
    return categories


def export_json(result: ScanResult, output_path: str) -> None:
    """Serialise a ScanResult to a JSON file at output_path."""
    logger.info("Writing JSON export to %s", output_path)
    data = _scan_result_to_dict(result)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=_default_serialiser, ensure_ascii=False)
    logger.info("JSON export complete: %s", output_path)
