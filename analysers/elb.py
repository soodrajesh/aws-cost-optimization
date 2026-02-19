"""
ELB (Elastic Load Balancer) analyser.

Checks for:
- Application/Network/Classic load balancers with no healthy targets
- ALBs averaging fewer than the configured requests/day threshold
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_elb

logger = logging.getLogger(__name__)


class ELBAnalyser(BaseAnalyser):
    SERVICE_NAME = "ELB"

    def _analyse_region(self, region: str) -> list[Finding]:
        findings: list[Finding] = []
        elbv2 = self._client("elbv2", region)
        cw = self._client("cloudwatch", region)

        findings.extend(self._check_alb_nlb(elbv2, cw, region))
        findings.extend(self._check_classic_elb(region))
        return findings

    def _check_alb_nlb(self, elbv2, cw, region: str) -> list[Finding]:
        findings: list[Finding] = []
        lbs = paginate(elbv2, "describe_load_balancers", "LoadBalancers")

        for lb in lbs:
            lb_arn = lb["LoadBalancerArn"]
            lb_name = lb["LoadBalancerName"]
            lb_type = lb.get("Type", "application")
            lb_dns = lb.get("DNSName", "")

            # Check target groups for healthy targets
            target_groups = safe_call(
                elbv2.describe_target_groups,
                LoadBalancerArn=lb_arn,
            ) or {}
            tgs = target_groups.get("TargetGroups", [])

            has_healthy_targets = False
            for tg in tgs:
                tg_arn = tg["TargetGroupArn"]
                health = safe_call(elbv2.describe_target_health, TargetGroupArn=tg_arn) or {}
                for desc in health.get("TargetHealthDescriptions", []):
                    if desc.get("TargetHealth", {}).get("State") == "healthy":
                        has_healthy_targets = True
                        break
                if has_healthy_targets:
                    break

            if not has_healthy_targets and tgs:
                findings.append(Finding(
                    service="ELB",
                    region=region,
                    resource_id=lb_arn,
                    resource_name=lb_name,
                    issue=f"{lb_type.upper()} load balancer has no healthy targets — delete and update DNS",
                    estimated_monthly_saving_usd=estimate_elb(lb_type),
                    severity=Severity.HIGH,
                    finding_type="no_targets",
                    details={
                        "lb_type": lb_type,
                        "dns_name": lb_dns,
                        "target_group_count": len(tgs),
                    },
                ))
                continue

            # For ALBs, also check request count
            if lb_type == "application":
                avg_requests_per_day = self._get_avg_requests(cw, lb_name)
                if (
                    avg_requests_per_day is not None
                    and avg_requests_per_day < self.config.elb_min_requests_per_day
                ):
                    findings.append(Finding(
                        service="ELB",
                        region=region,
                        resource_id=lb_arn,
                        resource_name=lb_name,
                        issue=(
                            f"ALB averaging {avg_requests_per_day:.1f} requests/day "
                            f"(threshold: {self.config.elb_min_requests_per_day}) — consolidate or remove"
                        ),
                        estimated_monthly_saving_usd=estimate_elb("application"),
                        severity=Severity.MEDIUM,
                        finding_type="low_traffic_elb",
                        details={
                            "lb_type": lb_type,
                            "avg_requests_per_day": round(avg_requests_per_day, 1),
                            "dns_name": lb_dns,
                        },
                    ))

        return findings

    def _get_avg_requests(self, cw, lb_name: str) -> float | None:
        """Return average daily request count for an ALB over the last 14 days."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=14)

        # ALB CloudWatch dimension uses the load balancer suffix from the ARN
        response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/ApplicationELB",
            MetricName="RequestCount",
            Dimensions=[{"Name": "LoadBalancer", "Value": lb_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Sum"],
        )
        if not response:
            return None
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return 0.0
        total = sum(dp["Sum"] for dp in datapoints)
        return total / len(datapoints)

    def _check_classic_elb(self, region: str) -> list[Finding]:
        """Check classic (v1) load balancers for empty instance lists."""
        findings: list[Finding] = []
        try:
            elb = self._client("elb", region)
            lbs = paginate(elb, "describe_load_balancers", "LoadBalancerDescriptions")
        except Exception as exc:
            logger.debug("Classic ELB check failed in %s: %s", region, exc)
            return findings

        for lb in lbs:
            lb_name = lb["LoadBalancerName"]
            instances = lb.get("Instances", [])
            if not instances:
                findings.append(Finding(
                    service="ELB",
                    region=region,
                    resource_id=lb_name,
                    resource_name=lb_name,
                    issue="Classic load balancer has no registered instances — migrate to ALB/NLB or delete",
                    estimated_monthly_saving_usd=estimate_elb("classic"),
                    severity=Severity.HIGH,
                    finding_type="no_targets",
                    details={"lb_type": "classic"},
                ))
        return findings
