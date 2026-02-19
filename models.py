"""
Data models for the AWS Cost Optimisation tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class Finding:
    """Represents a single wasteful or misconfigured resource."""

    service: str
    """AWS service name, e.g. 'EC2', 'RDS'."""

    region: str
    """AWS region where the resource lives, e.g. 'us-east-1'."""

    resource_id: str
    """Unique resource identifier (instance ID, bucket name, etc.)."""

    resource_name: str
    """Human-friendly name (tag Name or same as resource_id if untagged)."""

    issue: str
    """Short description of the problem, e.g. 'Idle instance (CPU < 5%)'."""

    estimated_monthly_saving_usd: float
    """Estimated monthly cost saving in USD if the issue is resolved."""

    severity: Severity
    """Severity level of the finding."""

    details: dict[str, Any] = field(default_factory=dict)
    """Additional metadata (metrics, tag values, last-used dates, etc.)."""

    finding_type: str = ""
    """Machine-readable finding type, e.g. 'idle_instance', 'unattached_ebs'."""

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)


@dataclass
class CostTrend:
    """6-month spend trend for a single AWS service."""

    service: str
    """AWS service name as returned by Cost Explorer."""

    monthly_costs: dict[str, float]
    """Mapping of 'YYYY-MM' → USD spend for each month."""

    total_spend: float
    """Sum of all monthly costs in the period."""

    trend_pct: float
    """Percentage change from the first month to the last month.
    Positive = growing spend, negative = declining spend."""

    anomaly: bool
    """True if any month's spend exceeded mean + 2×stddev."""

    anomaly_months: list[str] = field(default_factory=list)
    """List of 'YYYY-MM' months flagged as anomalous."""


@dataclass
class CostForecast:
    """Next-month cost forecast from Cost Explorer."""

    mean_usd: float
    """Forecasted mean spend for the next month."""

    lower_bound_usd: float
    """Lower bound of the forecast confidence interval."""

    upper_bound_usd: float
    """Upper bound of the forecast confidence interval."""


@dataclass
class Recommendation:
    """A prioritised, actionable recommendation backed by one or more findings."""

    title: str
    """Short title, e.g. 'Terminate idle EC2 instances'."""

    description: str
    """Full explanation including context from Cost Explorer trends."""

    service: str
    """AWS service this recommendation applies to."""

    findings: list[Finding] = field(default_factory=list)
    """The findings that back this recommendation."""

    total_saving: float = 0.0
    """Sum of estimated monthly savings across all backing findings."""

    severity: Severity = Severity.MEDIUM
    """Overall priority of the recommendation."""

    category: str = "strategic"
    """One of 'quick_win', 'strategic', or 'long_term'."""

    implementation_effort: str = "medium"
    """One of 'low', 'medium', or 'high'."""

    estimated_hours: float = 0.0
    """Rough implementation hours for this recommendation."""

    risk_level: str = "low"
    """One of 'low', 'medium', or 'high'."""

    risk_notes: str = ""
    """What could go wrong if this recommendation is implemented carelessly."""

    annualized_saving: float = 0.0
    """total_saving * 12."""

    roi_multiple: float = 0.0
    """annualized_saving / estimated_cost_to_implement."""

    implementation_steps: list[str] = field(default_factory=list)
    """Concrete action items for implementing this recommendation."""

    def __post_init__(self) -> None:
        if self.findings and self.total_saving == 0.0:
            self.total_saving = sum(f.estimated_monthly_saving_usd for f in self.findings)
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)
        if self.annualized_saving == 0.0 and self.total_saving > 0:
            self.annualized_saving = self.total_saving * 12
        if self.roi_multiple == 0.0 and self.estimated_hours > 0:
            impl_cost = self.estimated_hours * 150  # $150/hr engineering cost
            self.roi_multiple = round(self.annualized_saving / impl_cost, 1) if impl_cost > 0 else 0.0


@dataclass
class ScanResult:
    """Aggregated output of a full account scan."""

    account_id: str
    profile: str
    scan_date: str
    regions_scanned: list[str]
    months_of_history: int

    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    cost_trends: list[CostTrend] = field(default_factory=list)
    forecast: CostForecast | None = None

    top_billed_services: list[dict] = field(default_factory=list)
    """Top billed services from Cost Explorer, with coverage flags."""

    uncovered_high_spend: list[dict] = field(default_factory=list)
    """High-spend services without detailed resource-level analysis."""

    @property
    def total_potential_saving(self) -> float:
        return sum(f.estimated_monthly_saving_usd for f in self.findings)

    @property
    def findings_by_severity(self) -> dict[Severity, list[Finding]]:
        result: dict[Severity, list[Finding]] = {s: [] for s in Severity}
        for finding in self.findings:
            result[finding.severity].append(finding)
        return result

    @property
    def findings_by_service(self) -> dict[str, list[Finding]]:
        result: dict[str, list[Finding]] = {}
        for finding in self.findings:
            result.setdefault(finding.service, []).append(finding)
        return result
