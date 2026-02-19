"""
EC2 analyser.

Checks for:
- Low CPU instances (CPU < threshold)     → right-size or schedule (idle ≠ not in use)
- Right-sizing opportunity (CPU 5–25%)      → downsize instance type
- Scheduler opportunity (non-prod 24/7)     → Instance Scheduler (~65% saving)
- Graviton/ARM migration (x86 families)     → t4g/m7g/c7g/r7g (~20-40% cheaper)
- Spot instance opportunity (non-prod)      → Spot Fleet / Spot with ASG
- gp2 EBS volumes (attached)               → migrate to gp3 (20% cheaper)
- Stopped instances (> N days)             → terminate
- Unattached EBS volumes                   → delete
- Unused Elastic IPs                       → release
- Old EBS snapshots                        → delete
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import ClientError

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_ec2_idle, estimate_ebs_volume, estimate_eip, estimate_snapshot

logger = logging.getLogger(__name__)

_CW_NAMESPACE = "AWS/EC2"

# ---------------------------------------------------------------------------
# Instance family mappings
# ---------------------------------------------------------------------------

# Graviton equivalents: x86 family → ARM family (same generation or better)
_GRAVITON_MAP: dict[str, str] = {
    "t3": "t4g", "t3a": "t4g",
    "m5": "m7g", "m5a": "m6g", "m5n": "m7g", "m6i": "m7g",
    "c5": "c7g", "c5a": "c6g", "c5n": "c7g", "c6i": "c7g",
    "r5": "r7g", "r5a": "r6g", "r5n": "r7g", "r6i": "r7g",
    "m4": "m7g", "c4": "c7g", "r4": "r7g",
}

# Graviton price discount vs equivalent x86 (conservative estimate)
_GRAVITON_SAVING_PCT = 0.25

# Down-size map: current size suffix → next smaller suffix within same family
_DOWNSIZE_SUFFIX: dict[str, str] = {
    "2xlarge": "xlarge",
    "xlarge": "large",
    "large": "medium",
    "medium": "small",
    "small": "micro",
}

# Non-prod keywords for scheduler/spot heuristic
_NON_PROD_KW = frozenset({"dev", "test", "staging", "qa", "sandbox", "demo", "nonprod", "non-prod", "uat"})

# Scheduler saving assumption: 10hrs/day weekdays only ≈ 30% of full-time hours
_SCHEDULER_SAVING_PCT = 0.65


def _get_tag(tags: list[dict], key: str, default: str = "") -> str:
    for tag in tags or []:
        if tag.get("Key") == key:
            return tag.get("Value", default)
    return default


def _days_since(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


def _is_non_prod(name: str, tags: list[dict]) -> bool:
    name_lower = name.lower()
    if any(kw in name_lower for kw in _NON_PROD_KW):
        return True
    for tag in tags:
        val = tag.get("Value", "").lower()
        key = tag.get("Key", "").lower()
        if key in ("environment", "env", "stage") and val in _NON_PROD_KW:
            return True
    return False


def _instance_family(instance_type: str) -> tuple[str, str]:
    """Return (family, size) e.g. 'm5.xlarge' -> ('m5', 'xlarge')."""
    parts = instance_type.split(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return instance_type, ""


def _suggest_downsize(instance_type: str) -> str | None:
    """Return a suggested smaller instance type, or None if already minimal."""
    family, size = _instance_family(instance_type)
    smaller_size = _DOWNSIZE_SUFFIX.get(size)
    if smaller_size:
        return f"{family}.{smaller_size}"
    return None


def _suggest_graviton(instance_type: str) -> str | None:
    """Return Graviton equivalent instance type, or None if no mapping exists."""
    family, size = _instance_family(instance_type)
    graviton_family = _GRAVITON_MAP.get(family)
    if graviton_family:
        return f"{graviton_family}.{size}"
    return None


class EC2Analyser(BaseAnalyser):
    SERVICE_NAME = "EC2"

    def _analyse_region(self, region: str) -> list[Finding]:
        findings: list[Finding] = []
        ec2 = self._client("ec2", region)
        cw = self._client("cloudwatch", region)

        findings.extend(self._check_instances(ec2, cw, region))
        findings.extend(self._check_ebs_volumes(ec2, region, cw))
        findings.extend(self._check_elastic_ips(ec2, region))
        findings.extend(self._check_snapshots(ec2, region))
        return findings

    # ------------------------------------------------------------------
    # Instance checks
    # ------------------------------------------------------------------
    def _check_instances(self, ec2, cw, region: str) -> list[Finding]:
        findings: list[Finding] = []
        reservations = paginate(ec2, "describe_instances", "Reservations")

        for reservation in reservations:
            for instance in reservation.get("Instances", []):
                instance_id = instance["InstanceId"]
                state = instance["State"]["Name"]
                tags = instance.get("Tags", [])
                name = _get_tag(tags, "Name", instance_id)
                instance_type = instance.get("InstanceType", "unknown")
                platform = (instance.get("Platform") or "linux").lower()
                is_non_prod = _is_non_prod(name, tags)

                if state == "stopped":
                    launch_time = instance.get("LaunchTime")
                    days_stopped = _days_since(launch_time) if launch_time else 0
                    if days_stopped >= self.config.ec2_stopped_days:
                        findings.append(Finding(
                            service="EC2",
                            region=region,
                            resource_id=instance_id,
                            resource_name=name,
                            issue=f"Instance stopped for ~{int(days_stopped)} days — EBS costs still accruing",
                            estimated_monthly_saving_usd=estimate_ec2_idle(instance_type, region),
                            severity=Severity.HIGH,
                            finding_type="stopped_instance",
                            details={"instance_type": instance_type, "days_stopped": int(days_stopped)},
                        ))
                    continue

                if state != "running":
                    continue

                avg_cpu = self._get_avg_cpu(cw, instance_id, days=14)

                # 1. Low CPU — suggest right-sizing or scheduler (do not imply termination; idle ≠ not in use)
                if avg_cpu is not None and avg_cpu < self.config.ec2_cpu_threshold_pct:
                    findings.append(Finding(
                        service="EC2",
                        region=region,
                        resource_id=instance_id,
                        resource_name=name,
                        issue=f"Low CPU utilisation (avg {avg_cpu:.1f}% over 14 days) — consider right-sizing or scheduler if not needed 24/7",
                        estimated_monthly_saving_usd=estimate_ec2_idle(instance_type, region),
                        severity=Severity.HIGH,
                        finding_type="idle_instance",
                        details={"instance_type": instance_type, "avg_cpu_pct": round(avg_cpu, 1)},
                    ))

                # 2. Right-sizing: CPU between threshold and 25% (underutilised but running)
                elif avg_cpu is not None and self.config.ec2_cpu_threshold_pct <= avg_cpu <= 25.0:
                    suggested = _suggest_downsize(instance_type)
                    if suggested:
                        current_cost = estimate_ec2_idle(instance_type, region)
                        suggested_cost = estimate_ec2_idle(suggested, region)
                        saving = round(current_cost - suggested_cost, 2)
                        if saving > 1:
                            findings.append(Finding(
                                service="EC2",
                                region=region,
                                resource_id=instance_id,
                                resource_name=name,
                                issue=(
                                    f"Right-sizing opportunity: avg CPU {avg_cpu:.1f}% — "
                                    f"downsize {instance_type} → {suggested}"
                                ),
                                estimated_monthly_saving_usd=saving,
                                severity=Severity.MEDIUM,
                                finding_type="ec2_rightsizing",
                                details={
                                    "instance_type": instance_type,
                                    "suggested_type": suggested,
                                    "avg_cpu_pct": round(avg_cpu, 1),
                                },
                            ))

                # 3. Scheduler: non-prod running 24/7
                if is_non_prod and avg_cpu is not None:
                    monthly_cost = estimate_ec2_idle(instance_type, region)
                    saving = round(monthly_cost * _SCHEDULER_SAVING_PCT, 2)
                    if saving > 5:
                        findings.append(Finding(
                            service="EC2",
                            region=region,
                            resource_id=instance_id,
                            resource_name=name,
                            issue=(
                                f"Non-production instance running 24/7 — "
                                f"AWS Instance Scheduler can save ~65% "
                                f"(10hrs/day weekdays only)"
                            ),
                            estimated_monthly_saving_usd=saving,
                            severity=Severity.MEDIUM,
                            finding_type="ec2_scheduler",
                            details={
                                "instance_type": instance_type,
                                "avg_cpu_pct": round(avg_cpu, 1) if avg_cpu else None,
                                "scheduler_saving_pct": 65,
                            },
                        ))

                # 4. Graviton migration (x86 families only)
                graviton_type = _suggest_graviton(instance_type)
                if graviton_type and "arm" not in platform:
                    monthly_cost = estimate_ec2_idle(instance_type, region)
                    saving = round(monthly_cost * _GRAVITON_SAVING_PCT, 2)
                    if saving > 2:
                        findings.append(Finding(
                            service="EC2",
                            region=region,
                            resource_id=instance_id,
                            resource_name=name,
                            issue=(
                                f"Graviton migration opportunity: {instance_type} → {graviton_type} "
                                f"(~25% cheaper, same or better performance)"
                            ),
                            estimated_monthly_saving_usd=saving,
                            severity=Severity.LOW,
                            finding_type="ec2_graviton",
                            details={
                                "instance_type": instance_type,
                                "suggested_type": graviton_type,
                                "saving_pct": 25,
                            },
                        ))

                # 5. Spot opportunity: non-prod instances
                if is_non_prod:
                    monthly_cost = estimate_ec2_idle(instance_type, region)
                    saving = round(monthly_cost * 0.70, 2)  # Spot typically 70-90% cheaper
                    if saving > 10:
                        findings.append(Finding(
                            service="EC2",
                            region=region,
                            resource_id=instance_id,
                            resource_name=name,
                            issue=(
                                f"Spot instance opportunity: non-prod {instance_type} on On-Demand — "
                                f"Spot instances save up to 70-90%"
                            ),
                            estimated_monthly_saving_usd=saving,
                            severity=Severity.LOW,
                            finding_type="ec2_spot_opportunity",
                            details={
                                "instance_type": instance_type,
                                "spot_saving_pct": 70,
                            },
                        ))

        return findings

    def _get_avg_cpu(self, cw, instance_id: str, days: int = 14) -> float | None:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        response = safe_call(
            cw.get_metric_statistics,
            Namespace=_CW_NAMESPACE,
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
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

    # ------------------------------------------------------------------
    # EBS volume checks
    # ------------------------------------------------------------------
    def _check_ebs_volumes(self, ec2, region: str, cw=None) -> list[Finding]:
        findings: list[Finding] = []
        volumes = paginate(ec2, "describe_volumes", "Volumes")

        for volume in volumes:
            volume_id = volume["VolumeId"]
            tags = volume.get("Tags", [])
            name = _get_tag(tags, "Name", volume_id)
            size_gb = volume.get("Size", 0)
            volume_type = volume.get("VolumeType", "gp2")
            state = volume.get("State")

            # Unattached volumes
            if state == "available":
                findings.append(Finding(
                    service="EC2",
                    region=region,
                    resource_id=volume_id,
                    resource_name=name,
                    issue=f"Unattached EBS volume ({size_gb} GB, {volume_type}) — not in use",
                    estimated_monthly_saving_usd=estimate_ebs_volume(size_gb, volume_type, region),
                    severity=Severity.MEDIUM,
                    finding_type="unattached_ebs",
                    details={"size_gb": size_gb, "volume_type": volume_type},
                ))

            # io1/io2 over-provisioned IOPS: compare provisioned IOPS vs consumed
            elif volume_type in ("io1", "io2") and cw is not None:
                provisioned_iops = volume.get("Iops", 0)
                if provisioned_iops > 100:
                    avg_consumed = self._get_avg_volume_iops(cw, volume_id)
                    if avg_consumed is not None and avg_consumed < provisioned_iops * 0.40:
                        # Using less than 40% of provisioned IOPS — significant over-provisioning
                        suggested_iops = max(int(avg_consumed * 1.5), 100)  # 50% headroom, min 100
                        # io1: $0.065/provisioned IOPS/month, io2: $0.065 for first 32K IOPS
                        price_per_iops = 0.065
                        saving = round((provisioned_iops - suggested_iops) * price_per_iops, 2)
                        if saving > 5:
                            findings.append(Finding(
                                service="EC2",
                                region=region,
                                resource_id=volume_id,
                                resource_name=name,
                                issue=(
                                    f"Over-provisioned {volume_type} volume: {provisioned_iops:,} IOPS provisioned "
                                    f"but avg consumed {avg_consumed:,.0f} IOPS ({avg_consumed/provisioned_iops*100:.0f}%) — "
                                    f"reduce to ~{suggested_iops:,} IOPS"
                                ),
                                estimated_monthly_saving_usd=saving,
                                severity=Severity.MEDIUM,
                                finding_type="ebs_overprovisioned_iops",
                                details={
                                    "volume_type": volume_type,
                                    "size_gb": size_gb,
                                    "provisioned_iops": provisioned_iops,
                                    "avg_consumed_iops": round(avg_consumed, 0),
                                    "suggested_iops": suggested_iops,
                                },
                            ))

            # gp2 → gp3 migration for in-use volumes
            elif volume_type == "gp2" and size_gb >= 10:
                gp2_cost = estimate_ebs_volume(size_gb, "gp2", region)
                gp3_cost = estimate_ebs_volume(size_gb, "gp3", region)
                saving = round(gp2_cost - gp3_cost, 2)
                if saving > 0.5:
                    findings.append(Finding(
                        service="EC2",
                        region=region,
                        resource_id=volume_id,
                        resource_name=name,
                        issue=(
                            f"gp2 → gp3 migration: {size_gb} GB EBS volume — "
                            f"gp3 is 20% cheaper with better baseline IOPS/throughput"
                        ),
                        estimated_monthly_saving_usd=saving,
                        severity=Severity.LOW,
                        finding_type="ebs_gp2_to_gp3",
                        details={"size_gb": size_gb, "volume_type": "gp2", "suggested_type": "gp3"},
                    ))

        return findings

    def _get_avg_volume_iops(self, cw, volume_id: str) -> float | None:
        """Return 14-day average consumed IOPS (read + write) for an EBS volume."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=14)
        period = 14 * 86400

        read_resp = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/EBS",
            MetricName="VolumeReadOps",
            Dimensions=[{"Name": "VolumeId", "Value": volume_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Sum"],
        ) or {}

        write_resp = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/EBS",
            MetricName="VolumeWriteOps",
            Dimensions=[{"Name": "VolumeId", "Value": volume_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Sum"],
        ) or {}

        read_dps = read_resp.get("Datapoints", [])
        write_dps = write_resp.get("Datapoints", [])

        if not read_dps and not write_dps:
            return None

        total_ops = sum(dp["Sum"] for dp in read_dps) + sum(dp["Sum"] for dp in write_dps)
        total_seconds = 14 * 86400
        return total_ops / total_seconds  # average IOPS over the period

    # ------------------------------------------------------------------
    # Elastic IP checks
    # ------------------------------------------------------------------
    def _check_elastic_ips(self, ec2, region: str) -> list[Finding]:
        findings: list[Finding] = []
        response = safe_call(ec2.describe_addresses) or {}

        for address in response.get("Addresses", []):
            if not address.get("InstanceId") and not address.get("NetworkInterfaceId"):
                allocation_id = address.get("AllocationId", address.get("PublicIp", "unknown"))
                public_ip = address.get("PublicIp", "")
                tags = address.get("Tags", [])
                name = _get_tag(tags, "Name", public_ip)

                findings.append(Finding(
                    service="EC2",
                    region=region,
                    resource_id=allocation_id,
                    resource_name=name or public_ip,
                    issue=f"Unused Elastic IP {public_ip} — $3.65/month while unassociated",
                    estimated_monthly_saving_usd=estimate_eip(),
                    severity=Severity.LOW,
                    finding_type="unused_eip",
                    details={"public_ip": public_ip},
                ))
        return findings

    # ------------------------------------------------------------------
    # Snapshot checks
    # ------------------------------------------------------------------
    def _check_snapshots(self, ec2, region: str) -> list[Finding]:
        findings: list[Finding] = []
        try:
            account_id = self.session.client("sts").get_caller_identity()["Account"]
        except Exception:
            account_id = "self"

        snapshots = paginate(ec2, "describe_snapshots", "Snapshots", OwnerIds=[account_id])

        for snapshot in snapshots:
            start_time = snapshot.get("StartTime")
            if not start_time:
                continue
            age_days = _days_since(start_time)
            if age_days >= self.config.ec2_snapshot_age_days:
                snapshot_id = snapshot["SnapshotId"]
                tags = snapshot.get("Tags", [])
                name = _get_tag(tags, "Name", snapshot_id)
                size_gb = snapshot.get("VolumeSize", 0)

                findings.append(Finding(
                    service="EC2",
                    region=region,
                    resource_id=snapshot_id,
                    resource_name=name,
                    issue=f"Old EBS snapshot ({int(age_days)} days old, {size_gb} GB)",
                    estimated_monthly_saving_usd=estimate_snapshot(size_gb, region),
                    severity=Severity.LOW,
                    finding_type="old_snapshot",
                    details={"age_days": int(age_days), "size_gb": size_gb},
                ))
        return findings
