"""
NAT Gateway analyser.

Checks for:
1. Idle NAT Gateways (minimal throughput over 14 days)
2. High data-processing NAT Gateways (candidates for VPC endpoints)
3. Multiple NAT Gateways in the same AZ (consolidation opportunity)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from models import Finding, Severity
from utils.cost_estimator import estimate_nat_gateway_idle, estimate_nat_gateway_data

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1_073_741_824


class NATGatewayAnalyser(BaseAnalyser):
    SERVICE_NAME = "NAT Gateway"

    def _analyse_region(self, region: str) -> list[Finding]:
        ec2 = self._client("ec2", region)
        cw = self._client("cloudwatch", region)
        findings: list[Finding] = []

        # Fetch all active NAT Gateways
        try:
            paginator = ec2.get_paginator("describe_nat_gateways")
            nat_gateways = []
            for page in paginator.paginate(
                Filters=[{"Name": "state", "Values": ["available"]}]
            ):
                nat_gateways.extend(page.get("NatGateways", []))
        except Exception as exc:
            self.logger.error("Failed to list NAT Gateways in %s: %s", region, exc)
            return []

        if not nat_gateways:
            return []

        self.logger.info("Found %d NAT Gateways in %s", len(nat_gateways), region)

        # Track NAT GWs per AZ for consolidation check
        az_map: dict[str, list[str]] = defaultdict(list)

        for ngw in nat_gateways:
            ngw_id = ngw["NatGatewayId"]
            subnet_id = ngw.get("SubnetId", "")
            name = self._get_name(ngw)
            az = ngw.get("SubnetId", "unknown")

            # Try to get the actual AZ from subnet info
            try:
                subnet_resp = ec2.describe_subnets(SubnetIds=[subnet_id])
                if subnet_resp.get("Subnets"):
                    az = subnet_resp["Subnets"][0].get("AvailabilityZone", subnet_id)
            except Exception:
                pass

            az_map[az].append(ngw_id)

            # Get 14-day throughput
            total_bytes = self._get_metric_sum(
                cw, ngw_id, "BytesOutToDestination", days=14
            ) + self._get_metric_sum(
                cw, ngw_id, "BytesOutToSource", days=14
            )
            total_gb_14d = total_bytes / _BYTES_PER_GB

            # Get monthly throughput estimate (extrapolate from 14 days)
            monthly_gb = (total_bytes / _BYTES_PER_GB) * (30 / 14)

            # Check 1: Idle NAT Gateway
            if total_gb_14d < self.config.nat_gw_idle_gb_threshold:
                savings = estimate_nat_gateway_idle(region)
                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=ngw_id,
                    resource_name=name,
                    issue=f"Idle NAT Gateway ({total_gb_14d:.2f} GB throughput in 14 days)",
                    estimated_monthly_saving_usd=savings,
                    severity=Severity.HIGH,
                    finding_type="idle_nat_gateway",
                    details={
                        "throughput_14d_gb": round(total_gb_14d, 2),
                        "subnet_id": subnet_id,
                        "az": az,
                    },
                ))
                continue  # Skip high-traffic check for idle gateways

            # Check 2: High data-processing (suggest VPC endpoints)
            if monthly_gb > self.config.nat_gw_high_traffic_gb_month:
                savings = estimate_nat_gateway_data(monthly_gb, region)
                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=ngw_id,
                    resource_name=name,
                    issue=f"High traffic NAT Gateway (~{monthly_gb:,.0f} GB/month) — consider VPC endpoints",
                    estimated_monthly_saving_usd=savings,
                    severity=Severity.MEDIUM,
                    finding_type="high_traffic_nat_gateway",
                    details={
                        "estimated_monthly_gb": round(monthly_gb, 1),
                        "monthly_processing_cost": round(monthly_gb * 0.045, 2),
                        "subnet_id": subnet_id,
                        "az": az,
                    },
                ))

        # Check 3: Multiple NAT GWs in same AZ
        for az, ngw_ids in az_map.items():
            if len(ngw_ids) > 1:
                # Savings from removing the extra NAT GWs (hourly charge only)
                extra = len(ngw_ids) - 1
                savings = estimate_nat_gateway_idle(region) * extra
                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=", ".join(ngw_ids),
                    resource_name=f"{len(ngw_ids)} NAT GWs in {az}",
                    issue=f"Multiple NAT Gateways ({len(ngw_ids)}) in same AZ — consolidation opportunity",
                    estimated_monthly_saving_usd=savings,
                    severity=Severity.LOW,
                    finding_type="duplicate_nat_gateway",
                    details={"az": az, "nat_gateway_ids": ngw_ids, "extra_count": extra},
                ))

        return findings

    def _get_metric_sum(
        self, cw_client, ngw_id: str, metric_name: str, days: int = 14
    ) -> float:
        """Sum a CloudWatch metric for a NAT Gateway over the given number of days."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        try:
            response = cw_client.get_metric_statistics(
                Namespace="AWS/NATGateway",
                MetricName=metric_name,
                Dimensions=[{"Name": "NatGatewayId", "Value": ngw_id}],
                StartTime=start,
                EndTime=end,
                Period=86400,  # 1-day granularity
                Statistics=["Sum"],
            )
            return sum(dp["Sum"] for dp in response.get("Datapoints", []))
        except Exception as exc:
            self.logger.debug("CloudWatch metric %s for %s failed: %s", metric_name, ngw_id, exc)
            return 0.0

    @staticmethod
    def _get_name(ngw: dict) -> str:
        for tag in ngw.get("Tags", []):
            if tag["Key"] == "Name":
                return tag["Value"]
        return ngw["NatGatewayId"]
