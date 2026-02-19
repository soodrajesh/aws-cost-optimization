"""
CloudWatch analyser.

Checks for:
- Log groups with no retention policy (data stored indefinitely)
- Alarms stuck in INSUFFICIENT_DATA state for > N days
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_log_group, estimate_cloudwatch_alarm

logger = logging.getLogger(__name__)


def _days_since(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


class CloudWatchAnalyser(BaseAnalyser):
    SERVICE_NAME = "CloudWatch"

    def _analyse_region(self, region: str) -> list[Finding]:
        findings: list[Finding] = []
        logs = self._client("logs", region)
        cw = self._client("cloudwatch", region)

        findings.extend(self._check_log_groups(logs, region))
        findings.extend(self._check_alarms(cw, region))
        return findings

    def _check_log_groups(self, logs, region: str) -> list[Finding]:
        findings: list[Finding] = []
        log_groups = paginate(logs, "describe_log_groups", "logGroups")

        for lg in log_groups:
            name = lg["logGroupName"]
            retention_days = lg.get("retentionInDays")  # None means never expire

            if retention_days is None:
                # Estimate stored data size in GB from storedBytes
                stored_bytes = lg.get("storedBytes", 0)
                stored_gb = stored_bytes / (1024 ** 3)

                findings.append(Finding(
                    service="CloudWatch",
                    region=region,
                    resource_id=name,
                    resource_name=name,
                    issue=f"Log group has no retention policy ({stored_gb:.2f} GB stored indefinitely) — set 30–90 day retention",
                    estimated_monthly_saving_usd=estimate_log_group(stored_gb),
                    severity=Severity.MEDIUM,
                    finding_type="log_group_no_retention",
                    details={
                        "stored_gb": round(stored_gb, 3),
                        "retention_days": None,
                    },
                ))
        return findings

    def _check_alarms(self, cw, region: str) -> list[Finding]:
        """Flag alarms that have been in INSUFFICIENT_DATA state for too long."""
        findings: list[Finding] = []

        response = safe_call(
            cw.describe_alarms,
            StateValue="INSUFFICIENT_DATA",
        ) or {}

        alarms = response.get("MetricAlarms", [])

        for alarm in alarms:
            alarm_name = alarm["AlarmName"]
            state_updated = alarm.get("StateUpdatedTimestamp")
            if not state_updated:
                continue

            days_stale = _days_since(state_updated)
            if days_stale >= self.config.cloudwatch_alarm_stale_days:
                findings.append(Finding(
                    service="CloudWatch",
                    region=region,
                    resource_id=alarm_name,
                    resource_name=alarm_name,
                    issue=f"Alarm in INSUFFICIENT_DATA for {int(days_stale)} days — likely monitoring a deleted resource",
                    estimated_monthly_saving_usd=estimate_cloudwatch_alarm(),
                    severity=Severity.LOW,
                    finding_type="stale_alarm",
                    details={
                        "days_stale": int(days_stale),
                        "metric_name": alarm.get("MetricName", ""),
                        "namespace": alarm.get("Namespace", ""),
                        "state_reason": alarm.get("StateReason", ""),
                    },
                ))
        return findings
