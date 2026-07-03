"""
RDS analyser.

Checks for:
- Idle DB instances (0 connections)           → stop or delete
- Right-sizing (low CPU + low connections)    → downsize instance class
- Scheduler opportunity (non-prod 24/7)       → stop/start scheduler (~65% saving)
- Multi-AZ on non-production instances        → disable Multi-AZ
- Aurora Serverless migration opportunity     → small MySQL/PG → Aurora Serverless v2
- gp2 → gp3 storage migration                → 20% cheaper storage
- Old RDS snapshots                           → delete
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_rds_idle, estimate_rds_snapshot

logger = logging.getLogger(__name__)

_NON_PROD_KW = frozenset({"dev", "test", "staging", "qa", "sandbox", "demo", "nonprod", "non-prod", "uat"})
_SCHEDULER_SAVING_PCT = 0.65

# DB classes small enough to be good Aurora Serverless v2 candidates
_AURORA_SERVERLESS_CANDIDATE_CLASSES = {
    "db.t3.micro", "db.t3.small", "db.t3.medium",
    "db.t4g.micro", "db.t4g.small", "db.t4g.medium",
    "db.t2.micro", "db.t2.small", "db.t2.medium",
}
_AURORA_SERVERLESS_ENGINES = {"mysql", "postgres", "aurora-mysql", "aurora-postgresql"}

# RDS instance class downsize map (same engine family)
_DOWNSIZE_MAP: dict[str, str] = {
    "db.m5.4xlarge": "db.m5.2xlarge",
    "db.m5.2xlarge": "db.m5.xlarge",
    "db.m5.xlarge": "db.m5.large",
    "db.m5.large": "db.t3.large",
    "db.m6g.4xlarge": "db.m6g.2xlarge",
    "db.m6g.2xlarge": "db.m6g.xlarge",
    "db.m6g.xlarge": "db.m6g.large",
    "db.m6g.large": "db.t4g.large",
    "db.r5.4xlarge": "db.r5.2xlarge",
    "db.r5.2xlarge": "db.r5.xlarge",
    "db.r5.xlarge": "db.r5.large",
    "db.r6g.4xlarge": "db.r6g.2xlarge",
    "db.r6g.2xlarge": "db.r6g.xlarge",
    "db.r6g.xlarge": "db.r6g.large",
    "db.t3.large": "db.t3.medium",
    "db.t3.medium": "db.t3.small",
    "db.t3.small": "db.t3.micro",
    "db.t4g.large": "db.t4g.medium",
    "db.t4g.medium": "db.t4g.small",
    "db.t4g.small": "db.t4g.micro",
}


def _days_since(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


def _is_non_prod(identifier: str, tags: list[dict]) -> bool:
    identifier_lower = identifier.lower()
    if any(kw in identifier_lower for kw in _NON_PROD_KW):
        return True
    for tag in tags:
        val = tag.get("Value", "").lower()
        key = tag.get("Key", "").lower()
        if key in ("environment", "env", "stage") and val in _NON_PROD_KW:
            return True
    return False


class RDSAnalyser(BaseAnalyser):
    SERVICE_NAME = "RDS"

    def _analyse_region(self, region: str) -> list[Finding]:
        findings: list[Finding] = []
        rds = self._client("rds", region)
        cw = self._client("cloudwatch", region)

        findings.extend(self._check_instances(rds, cw, region))
        findings.extend(self._check_snapshots(rds, region))
        return findings

    def _check_instances(self, rds, cw, region: str) -> list[Finding]:
        findings: list[Finding] = []
        instances = paginate(rds, "describe_db_instances", "DBInstances")

        for db in instances:
            db_id = db["DBInstanceIdentifier"]
            db_class = db.get("DBInstanceClass", "unknown")
            engine = db.get("Engine", "unknown").lower()
            multi_az = db.get("MultiAZ", False)
            storage_type = db.get("StorageType", "gp2")
            allocated_storage = db.get("AllocatedStorage", 0)
            status = db.get("DBInstanceStatus", "")

            if status != "available":
                continue

            arn = db.get("DBInstanceArn", "")
            tags_response = safe_call(rds.list_tags_for_resource, ResourceName=arn) or {}
            tags = tags_response.get("TagList", [])
            is_non_prod = _is_non_prod(db_id, tags)

            avg_connections = self._get_metric_avg(cw, db_id, "DatabaseConnections",
                                                    self.config.rds_connection_days)
            avg_cpu = self._get_metric_avg(cw, db_id, "CPUUtilization", 14)

            # 1. Idle instance → stop or delete
            if avg_connections is not None and avg_connections == 0:
                findings.append(Finding(
                    service="RDS",
                    region=region,
                    resource_id=db_id,
                    resource_name=db_id,
                    issue=(
                        f"Idle RDS instance (0 connections over {self.config.rds_connection_days} days) — "
                        f"stop (saves ~50%) or delete"
                    ),
                    estimated_monthly_saving_usd=estimate_rds_idle(db_class, region),
                    severity=Severity.HIGH,
                    finding_type="idle_rds",
                    details={"db_class": db_class, "engine": engine, "multi_az": multi_az},
                ))
                continue  # Skip further checks for idle instances

            # 2. Right-sizing: low CPU and low connections
            low_cpu = avg_cpu is not None and avg_cpu < 15.0
            low_conn = avg_connections is not None and avg_connections < 10
            if low_cpu and low_conn:
                suggested = _DOWNSIZE_MAP.get(db_class)
                if suggested:
                    current_cost = estimate_rds_idle(db_class, region)
                    suggested_cost = estimate_rds_idle(suggested, region)
                    saving = round(current_cost - suggested_cost, 2)
                    if saving > 5:
                        findings.append(Finding(
                            service="RDS",
                            region=region,
                            resource_id=db_id,
                            resource_name=db_id,
                            issue=(
                                f"Right-sizing opportunity: {db_class} at {avg_cpu:.1f}% CPU, "
                                f"{avg_connections:.0f} avg connections — "
                                f"downsize to {suggested}"
                            ),
                            estimated_monthly_saving_usd=saving,
                            severity=Severity.MEDIUM,
                            finding_type="rds_rightsizing",
                            details={
                                "db_class": db_class,
                                "suggested_class": suggested,
                                "avg_cpu_pct": round(avg_cpu, 1),
                                "avg_connections": round(avg_connections, 1),
                            },
                        ))

            # 3. Scheduler: non-prod running 24/7
            if is_non_prod and avg_connections is not None:
                monthly_cost = estimate_rds_idle(db_class, region)
                saving = round(monthly_cost * _SCHEDULER_SAVING_PCT, 2)
                if saving > 5:
                    findings.append(Finding(
                        service="RDS",
                        region=region,
                        resource_id=db_id,
                        resource_name=db_id,
                        issue=(
                            "Non-production RDS running 24/7 — "
                            "AWS Instance Scheduler can stop it evenings/weekends (~65% saving). "
                            "RDS stops automatically after 7 days and can be restarted on demand."
                        ),
                        estimated_monthly_saving_usd=saving,
                        severity=Severity.MEDIUM,
                        finding_type="rds_scheduler",
                        details={
                            "db_class": db_class,
                            "engine": engine,
                            "scheduler_saving_pct": 65,
                        },
                    ))

            # 4. Multi-AZ on non-production
            if multi_az and is_non_prod:
                findings.append(Finding(
                    service="RDS",
                    region=region,
                    resource_id=db_id,
                    resource_name=db_id,
                    issue="Multi-AZ enabled on apparent non-production instance — disable to halve instance cost",
                    estimated_monthly_saving_usd=round(estimate_rds_idle(db_class, region) * 0.5, 2),
                    severity=Severity.MEDIUM,
                    finding_type="multi_az_non_prod",
                    details={"db_class": db_class, "engine": engine},
                ))

            # 5. Aurora Serverless migration (small MySQL/PostgreSQL instances)
            if (db_class in _AURORA_SERVERLESS_CANDIDATE_CLASSES
                    and engine in _AURORA_SERVERLESS_ENGINES
                    and "aurora" not in engine):
                monthly_cost = estimate_rds_idle(db_class, region)
                # Aurora Serverless v2 scales to 0 ACU during idle; conservatively 40% saving for variable workloads
                saving = round(monthly_cost * 0.40, 2)
                if saving > 3:
                    findings.append(Finding(
                        service="RDS",
                        region=region,
                        resource_id=db_id,
                        resource_name=db_id,
                        issue=(
                            f"Aurora Serverless v2 opportunity: {db_class} {engine} — "
                            f"Aurora Serverless scales to zero ACU during idle periods, "
                            f"typically 30-60% cheaper for variable workloads"
                        ),
                        estimated_monthly_saving_usd=saving,
                        severity=Severity.LOW,
                        finding_type="rds_aurora_serverless",
                        details={"db_class": db_class, "engine": engine, "saving_pct": 40},
                    ))

            # 6. gp2 → gp3 storage migration
            if storage_type == "gp2" and allocated_storage >= 20:
                # gp3 is 20% cheaper than gp2 for RDS storage
                gp2_monthly = allocated_storage * 0.115  # $0.115/GB for RDS gp2
                gp3_monthly = allocated_storage * 0.092  # $0.092/GB for RDS gp3 (20% cheaper)
                saving = round(gp2_monthly - gp3_monthly, 2)
                if saving > 1:
                    findings.append(Finding(
                        service="RDS",
                        region=region,
                        resource_id=db_id,
                        resource_name=db_id,
                        issue=(
                            f"RDS storage gp2 → gp3: {allocated_storage} GB storage — "
                            f"gp3 is 20% cheaper with 3x more baseline IOPS and 125MB/s throughput"
                        ),
                        estimated_monthly_saving_usd=saving,
                        severity=Severity.LOW,
                        finding_type="rds_gp2_to_gp3",
                        details={
                            "storage_type": "gp2",
                            "suggested_type": "gp3",
                            "allocated_storage_gb": allocated_storage,
                        },
                    ))

        return findings

    def _get_metric_avg(self, cw, db_id: str, metric: str, days: int) -> float | None:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/RDS",
            MetricName=metric,
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=days * 86400,
            Statistics=["Average"],
        )
        if not response:
            return None
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        return datapoints[0]["Average"]

    def _check_snapshots(self, rds, region: str) -> list[Finding]:
        findings: list[Finding] = []
        snapshots = paginate(rds, "describe_db_snapshots", "DBSnapshots", SnapshotType="manual")

        for snap in snapshots:
            create_time = snap.get("SnapshotCreateTime")
            if not create_time:
                continue
            age_days = _days_since(create_time)
            if age_days >= self.config.rds_snapshot_age_days:
                snap_id = snap["DBSnapshotIdentifier"]
                size_gb = snap.get("AllocatedStorage", 0)
                findings.append(Finding(
                    service="RDS",
                    region=region,
                    resource_id=snap_id,
                    resource_name=snap_id,
                    issue=f"Old RDS snapshot ({int(age_days)} days old, {size_gb} GB)",
                    estimated_monthly_saving_usd=estimate_rds_snapshot(size_gb),
                    severity=Severity.LOW,
                    finding_type="old_rds_snapshot",
                    details={
                        "age_days": int(age_days),
                        "size_gb": size_gb,
                        "engine": snap.get("Engine", ""),
                    },
                ))
        return findings
