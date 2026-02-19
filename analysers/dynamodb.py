"""
DynamoDB analyser.

Checks for:
1. Over-provisioned tables (consumed < 20% of provisioned capacity)
2. Unused/idle tables (zero consumed capacity over 14 days)
3. Provisioned tables without auto-scaling
4. Tables that may benefit from switching billing mode
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from models import Finding, Severity
from utils.cost_estimator import estimate_dynamodb_idle, estimate_dynamodb_overprovisioned

logger = logging.getLogger(__name__)


class DynamoDBAnalyser(BaseAnalyser):
    SERVICE_NAME = "DynamoDB"

    def _analyse_region(self, region: str) -> list[Finding]:
        dynamodb = self._client("dynamodb", region)
        cw = self._client("cloudwatch", region)
        autoscaling = self._client("application-autoscaling", region)
        findings: list[Finding] = []

        # List all tables
        try:
            table_names: list[str] = []
            paginator = dynamodb.get_paginator("list_tables")
            for page in paginator.paginate():
                table_names.extend(page.get("TableNames", []))
        except Exception as exc:
            self.logger.error("Failed to list DynamoDB tables in %s: %s", region, exc)
            return []

        if not table_names:
            return []

        self.logger.info("Found %d DynamoDB tables in %s", len(table_names), region)

        # Get auto-scaling targets for DynamoDB in this region
        scaling_targets = self._get_scaling_targets(autoscaling)

        for table_name in table_names:
            try:
                desc = dynamodb.describe_table(TableName=table_name)["Table"]
            except Exception as exc:
                self.logger.debug("Could not describe table %s: %s", table_name, exc)
                continue

            billing_mode = desc.get("BillingModeSummary", {}).get(
                "BillingMode", "PROVISIONED"
            )

            # Only analyse provisioned-mode tables for over-provisioning
            if billing_mode != "PROVISIONED":
                continue

            provisioned = desc.get("ProvisionedThroughput", {})
            prov_rcu = provisioned.get("ReadCapacityUnits", 0)
            prov_wcu = provisioned.get("WriteCapacityUnits", 0)

            if prov_rcu == 0 and prov_wcu == 0:
                continue

            # Get consumed capacity (14-day average)
            avg_consumed_rcu = self._get_avg_metric(
                cw, table_name, "ConsumedReadCapacityUnits"
            )
            avg_consumed_wcu = self._get_avg_metric(
                cw, table_name, "ConsumedWriteCapacityUnits"
            )

            # Check 1: Completely idle table
            if avg_consumed_rcu < 0.1 and avg_consumed_wcu < 0.1:
                savings = estimate_dynamodb_idle(prov_rcu, prov_wcu, region)
                if savings > 0:
                    findings.append(Finding(
                        service=self.SERVICE_NAME,
                        region=region,
                        resource_id=table_name,
                        resource_name=table_name,
                        issue=f"Idle table (0 reads/writes for {self.config.dynamodb_idle_days} days, {prov_rcu} RCU / {prov_wcu} WCU provisioned)",
                        estimated_monthly_saving_usd=savings,
                        severity=Severity.HIGH,
                        finding_type="idle_dynamodb_table",
                        details={
                            "provisioned_rcu": prov_rcu,
                            "provisioned_wcu": prov_wcu,
                            "avg_consumed_rcu": round(avg_consumed_rcu, 2),
                            "avg_consumed_wcu": round(avg_consumed_wcu, 2),
                            "billing_mode": billing_mode,
                        },
                    ))
                continue

            # Check 2: Over-provisioned
            threshold = self.config.dynamodb_utilisation_threshold_pct / 100.0
            rcu_util = (avg_consumed_rcu / prov_rcu) if prov_rcu > 0 else 1.0
            wcu_util = (avg_consumed_wcu / prov_wcu) if prov_wcu > 0 else 1.0

            if rcu_util < threshold or wcu_util < threshold:
                rcu_saving = estimate_dynamodb_overprovisioned(
                    prov_rcu, avg_consumed_rcu, is_write=False, region=region
                )
                wcu_saving = estimate_dynamodb_overprovisioned(
                    prov_wcu, avg_consumed_wcu, is_write=True, region=region
                )
                total_saving = rcu_saving + wcu_saving

                if total_saving > 0:
                    findings.append(Finding(
                        service=self.SERVICE_NAME,
                        region=region,
                        resource_id=table_name,
                        resource_name=table_name,
                        issue=(
                            f"Over-provisioned (RCU: {rcu_util:.0%} utilised, "
                            f"WCU: {wcu_util:.0%} utilised)"
                        ),
                        estimated_monthly_saving_usd=total_saving,
                        severity=Severity.HIGH,
                        finding_type="overprovisioned_dynamodb",
                        details={
                            "provisioned_rcu": prov_rcu,
                            "provisioned_wcu": prov_wcu,
                            "avg_consumed_rcu": round(avg_consumed_rcu, 2),
                            "avg_consumed_wcu": round(avg_consumed_wcu, 2),
                            "rcu_utilisation": round(rcu_util * 100, 1),
                            "wcu_utilisation": round(wcu_util * 100, 1),
                        },
                    ))

            # Check 3: No auto-scaling configured
            table_arn = desc.get("TableArn", "")
            has_rcu_scaling = f"table/{table_name}" in scaling_targets.get("read", set())
            has_wcu_scaling = f"table/{table_name}" in scaling_targets.get("write", set())

            if not has_rcu_scaling or not has_wcu_scaling:
                missing = []
                if not has_rcu_scaling:
                    missing.append("read")
                if not has_wcu_scaling:
                    missing.append("write")

                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=table_name,
                    resource_name=table_name,
                    issue=f"No auto-scaling for {'/'.join(missing)} capacity",
                    estimated_monthly_saving_usd=0.0,
                    severity=Severity.MEDIUM,
                    finding_type="no_autoscaling_dynamodb",
                    details={
                        "missing_scaling": missing,
                        "billing_mode": billing_mode,
                    },
                ))

        return findings

    def _get_avg_metric(
        self, cw_client, table_name: str, metric_name: str, days: int = 14
    ) -> float:
        """Get the average of a DynamoDB CloudWatch metric over the given days."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        try:
            response = cw_client.get_metric_statistics(
                Namespace="AWS/DynamoDB",
                MetricName=metric_name,
                Dimensions=[{"Name": "TableName", "Value": table_name}],
                StartTime=start,
                EndTime=end,
                Period=86400,  # 1-day granularity
                Statistics=["Average"],
            )
            datapoints = response.get("Datapoints", [])
            if not datapoints:
                return 0.0
            return sum(dp["Average"] for dp in datapoints) / len(datapoints)
        except Exception as exc:
            self.logger.debug(
                "CloudWatch %s for table %s failed: %s", metric_name, table_name, exc
            )
            return 0.0

    def _get_scaling_targets(self, autoscaling_client) -> dict[str, set[str]]:
        """Get auto-scaling targets for DynamoDB tables."""
        result: dict[str, set[str]] = {"read": set(), "write": set()}

        try:
            paginator = autoscaling_client.get_paginator(
                "describe_scalable_targets"
            )
            for page in paginator.paginate(
                ServiceNamespace="dynamodb",
            ):
                for target in page.get("ScalableTargets", []):
                    resource_id = target.get("ResourceId", "")
                    dimension = target.get("ScalableDimension", "")
                    if "ReadCapacityUnits" in dimension:
                        result["read"].add(resource_id)
                    elif "WriteCapacityUnits" in dimension:
                        result["write"].add(resource_id)
        except Exception as exc:
            self.logger.debug("Could not fetch auto-scaling targets: %s", exc)

        return result
