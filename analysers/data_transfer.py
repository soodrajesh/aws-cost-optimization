"""
Data Transfer analyser.

Unlike other analysers, this one is driven entirely by Cost Explorer data
(not by enumerating resources). It identifies high data-transfer spend
patterns and recommends optimisation strategies.

Checks for:
1. High internet egress spend
2. High inter-region transfer spend
3. Cross-AZ transfer spend within the same region
"""

from __future__ import annotations

import logging

import boto3

from analysers.base import BaseAnalyser
from config import Config
from models import Finding, Severity

logger = logging.getLogger(__name__)


class DataTransferAnalyser(BaseAnalyser):
    SERVICE_NAME = "Data Transfer"

    def __init__(self, session: boto3.Session, config: Config) -> None:
        super().__init__(session, config)
        self._dt_breakdown: list[dict] = []

    def set_cost_explorer_data(self, dt_breakdown: list[dict]) -> None:
        """Inject data transfer breakdown from CostExplorerAnalyser."""
        self._dt_breakdown = dt_breakdown

    def analyse(self, regions: list[str]) -> list[Finding]:
        """
        Analyse data transfer costs using Cost Explorer usage-type breakdown.
        The `regions` parameter is not used (CE is global).
        """
        findings: list[Finding] = []

        if not self._dt_breakdown:
            self.logger.info("No data transfer breakdown data available")
            return findings

        min_threshold = self.config.data_transfer_min_monthly_usd

        # Categorise usage types
        internet_egress_total = 0.0
        inter_region_total = 0.0
        cross_az_total = 0.0
        internet_types: list[dict] = []
        inter_region_types: list[dict] = []
        cross_az_types: list[dict] = []

        for entry in self._dt_breakdown:
            usage_type = entry["usage_type"].lower()
            monthly_avg = entry["monthly_avg"]

            if "out-bytes" in usage_type or "dataout" in usage_type:
                internet_egress_total += monthly_avg
                internet_types.append(entry)
            elif "regional" in usage_type and "in-bytes" not in usage_type:
                cross_az_total += monthly_avg
                cross_az_types.append(entry)
            elif "interregion" in usage_type or "region" in usage_type:
                inter_region_total += monthly_avg
                inter_region_types.append(entry)

        # Check 1: High internet egress
        if internet_egress_total > min_threshold:
            savings = round(internet_egress_total * 0.25, 2)  # 25% reduction via CloudFront/optimization
            findings.append(Finding(
                service=self.SERVICE_NAME,
                region="global",
                resource_id="internet-egress",
                resource_name="Internet Data Transfer Out",
                issue=f"High internet egress (${internet_egress_total:,.0f}/month) — consider CloudFront or S3 Transfer Acceleration",
                estimated_monthly_saving_usd=savings,
                severity=Severity.MEDIUM,
                finding_type="high_internet_egress",
                details={
                    "monthly_spend": round(internet_egress_total, 2),
                    "usage_types": [t["usage_type"] for t in internet_types[:5]],
                },
            ))

        # Check 2: High inter-region transfer
        if inter_region_total > min_threshold:
            savings = round(inter_region_total * 0.30, 2)  # 30% reduction by co-locating
            findings.append(Finding(
                service=self.SERVICE_NAME,
                region="global",
                resource_id="inter-region-transfer",
                resource_name="Inter-Region Data Transfer",
                issue=f"High inter-region transfer (${inter_region_total:,.0f}/month) — review cross-region architecture",
                estimated_monthly_saving_usd=savings,
                severity=Severity.MEDIUM,
                finding_type="high_inter_region_transfer",
                details={
                    "monthly_spend": round(inter_region_total, 2),
                    "usage_types": [t["usage_type"] for t in inter_region_types[:5]],
                },
            ))

        # Check 3: Cross-AZ transfer
        if cross_az_total > min_threshold:
            savings = round(cross_az_total * 0.20, 2)  # 20% reduction by better AZ placement
            findings.append(Finding(
                service=self.SERVICE_NAME,
                region="global",
                resource_id="cross-az-transfer",
                resource_name="Cross-AZ Data Transfer",
                issue=f"High cross-AZ transfer (${cross_az_total:,.0f}/month) — co-locate communicating resources",
                estimated_monthly_saving_usd=savings,
                severity=Severity.LOW,
                finding_type="high_cross_az_transfer",
                details={
                    "monthly_spend": round(cross_az_total, 2),
                    "usage_types": [t["usage_type"] for t in cross_az_types[:5]],
                },
            ))

        return findings
