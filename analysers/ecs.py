"""
ECS / Fargate analyser.

Checks for:
1. Over-provisioned Fargate tasks (low CPU and memory utilisation)
2. Idle ECS services (0 running tasks for extended period)
3. Fargate Spot opportunity (non-production on-demand services)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from models import Finding, Severity
from utils.cost_estimator import estimate_fargate_idle, estimate_fargate_rightsize

logger = logging.getLogger(__name__)

# Heuristic: non-production keywords in cluster/service names or tags
_NON_PROD_KEYWORDS = {"dev", "test", "staging", "qa", "sandbox", "demo", "uat", "nonprod", "non-prod"}


class ECSAnalyser(BaseAnalyser):
    SERVICE_NAME = "ECS/Fargate"

    def _analyse_region(self, region: str) -> list[Finding]:
        ecs = self._client("ecs", region)
        cw = self._client("cloudwatch", region)
        findings: list[Finding] = []

        # List all clusters
        try:
            cluster_arns: list[str] = []
            paginator = ecs.get_paginator("list_clusters")
            for page in paginator.paginate():
                cluster_arns.extend(page.get("clusterArns", []))
        except Exception as exc:
            self.logger.error("Failed to list ECS clusters in %s: %s", region, exc)
            return []

        if not cluster_arns:
            return []

        # Describe clusters to get names
        try:
            clusters_resp = ecs.describe_clusters(clusters=cluster_arns)
            clusters = {
                c["clusterArn"]: c["clusterName"]
                for c in clusters_resp.get("clusters", [])
            }
        except Exception as exc:
            self.logger.error("Failed to describe ECS clusters: %s", exc)
            return []

        self.logger.info("Found %d ECS clusters in %s", len(clusters), region)

        for cluster_arn, cluster_name in clusters.items():
            # List services in this cluster
            try:
                service_arns: list[str] = []
                svc_paginator = ecs.get_paginator("list_services")
                for page in svc_paginator.paginate(cluster=cluster_arn):
                    service_arns.extend(page.get("serviceArns", []))
            except Exception as exc:
                self.logger.debug("Failed to list services in %s: %s", cluster_name, exc)
                continue

            if not service_arns:
                continue

            # Describe services (batch of 10)
            for i in range(0, len(service_arns), 10):
                batch = service_arns[i:i + 10]
                try:
                    desc = ecs.describe_services(
                        cluster=cluster_arn, services=batch
                    )
                except Exception as exc:
                    self.logger.debug("Failed to describe services: %s", exc)
                    continue

                for svc in desc.get("services", []):
                    svc_findings = self._analyse_service(
                        ecs, cw, svc, cluster_name, region,
                    )
                    findings.extend(svc_findings)

        return findings

    def _analyse_service(
        self, ecs_client, cw_client, svc: dict, cluster_name: str, region: str
    ) -> list[Finding]:
        findings: list[Finding] = []
        svc_name = svc.get("serviceName", "unknown")
        running_count = svc.get("runningCount", 0)
        desired_count = svc.get("desiredCount", 0)
        launch_type = svc.get("launchType", "EC2")
        task_def_arn = svc.get("taskDefinition", "")

        display_name = f"{cluster_name}/{svc_name}"

        # Check 1: Idle service (0 running tasks)
        if running_count == 0 and desired_count == 0:
            # Check if it's been idle for a while using deployments
            findings.append(Finding(
                service=self.SERVICE_NAME,
                region=region,
                resource_id=svc.get("serviceArn", svc_name),
                resource_name=display_name,
                issue="Idle service (0 running tasks, desired count 0)",
                estimated_monthly_saving_usd=0.0,  # No active cost if 0 tasks
                severity=Severity.MEDIUM,
                finding_type="idle_ecs_service",
                details={
                    "cluster": cluster_name,
                    "launch_type": launch_type,
                    "running_count": running_count,
                },
            ))
            return findings

        # For Fargate services, check resource utilisation
        if launch_type != "FARGATE" and "FARGATE" not in str(svc.get("capacityProviderStrategy", [])):
            return findings  # EC2 launch type — skip Fargate-specific checks

        # Get task definition to know provisioned resources
        try:
            task_def = ecs_client.describe_task_definition(
                taskDefinition=task_def_arn
            )["taskDefinition"]
        except Exception:
            return findings

        # Fargate CPU and memory are set at the task level
        task_cpu = int(task_def.get("cpu", "256")) / 1024  # vCPU
        task_memory = int(task_def.get("memory", "512")) / 1024  # GB

        # Check 2: Over-provisioned Fargate tasks
        avg_cpu = self._get_avg_metric(
            cw_client, cluster_name, svc_name, "CPUUtilization"
        )
        avg_memory = self._get_avg_metric(
            cw_client, cluster_name, svc_name, "MemoryUtilization"
        )

        cpu_threshold = self.config.ecs_cpu_threshold_pct
        mem_threshold = self.config.ecs_memory_threshold_pct

        if avg_cpu > 0 and avg_cpu < cpu_threshold and avg_memory < mem_threshold:
            # Suggest right-sized values (2x average with minimum)
            target_cpu = max(self._nearest_fargate_cpu(task_cpu * avg_cpu / 100 * 2), 0.25)
            target_mem = max(self._nearest_fargate_memory(task_memory * avg_memory / 100 * 2, target_cpu), 0.5)

            savings_per_task = estimate_fargate_rightsize(
                task_cpu, task_memory, target_cpu, target_mem, region
            )
            total_savings = savings_per_task * max(running_count, 1)

            if total_savings > 0:
                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=svc.get("serviceArn", svc_name),
                    resource_name=display_name,
                    issue=(
                        f"Over-provisioned Fargate (CPU: {avg_cpu:.1f}%, Mem: {avg_memory:.1f}%) "
                        f"— {task_cpu} vCPU / {task_memory} GB could be {target_cpu} vCPU / {target_mem} GB"
                    ),
                    estimated_monthly_saving_usd=total_savings,
                    severity=Severity.MEDIUM,
                    finding_type="overprovisioned_fargate",
                    details={
                        "cluster": cluster_name,
                        "current_vcpu": task_cpu,
                        "current_memory_gb": task_memory,
                        "suggested_vcpu": target_cpu,
                        "suggested_memory_gb": target_mem,
                        "avg_cpu_pct": round(avg_cpu, 1),
                        "avg_memory_pct": round(avg_memory, 1),
                        "running_tasks": running_count,
                    },
                ))

        # Check 3: Graviton/arm64 opportunity for Fargate tasks on x86_64
        # Fargate on arm64 is ~20% cheaper; Fargate supports arm64 in most regions
        task_arch = task_def.get("runtimePlatform", {}).get("cpuArchitecture", "X86_64")
        if task_arch == "X86_64" and running_count > 0:
            current_cost = estimate_fargate_idle(task_cpu, task_memory, region) * running_count
            saving = round(current_cost * 0.20, 2)
            if saving > 2:
                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=svc.get("serviceArn", svc_name),
                    resource_name=display_name,
                    issue=(
                        "Graviton/arm64 opportunity: Fargate task on X86_64 — "
                        "switch to ARM64 architecture for ~20% lower Fargate compute cost"
                    ),
                    estimated_monthly_saving_usd=saving,
                    severity=Severity.LOW,
                    finding_type="fargate_graviton",
                    details={
                        "cluster": cluster_name,
                        "current_architecture": "X86_64",
                        "suggested_architecture": "ARM64",
                        "task_cpu": task_cpu,
                        "task_memory_gb": task_memory,
                        "running_tasks": running_count,
                        "saving_pct": 20,
                    },
                ))

        # Check 4: Fargate Spot opportunity for non-production
        if self._is_non_prod(cluster_name, svc_name, svc):
            # Check if currently using on-demand (no Spot capacity provider)
            cap_providers = svc.get("capacityProviderStrategy", [])
            using_spot = any(
                "SPOT" in cp.get("capacityProvider", "").upper()
                for cp in cap_providers
            )
            if not using_spot and running_count > 0:
                current_cost = estimate_fargate_idle(task_cpu, task_memory, region) * running_count
                spot_savings = current_cost * 0.50  # Fargate Spot typically 50-70% cheaper
                if spot_savings > 5:
                    findings.append(Finding(
                        service=self.SERVICE_NAME,
                        region=region,
                        resource_id=svc.get("serviceArn", svc_name),
                        resource_name=display_name,
                        issue="Non-production Fargate service on On-Demand — Fargate Spot could save ~50%",
                        estimated_monthly_saving_usd=round(spot_savings, 2),
                        severity=Severity.LOW,
                        finding_type="fargate_spot_opportunity",
                        details={
                            "cluster": cluster_name,
                            "running_tasks": running_count,
                            "current_monthly_cost": round(current_cost, 2),
                        },
                    ))

        return findings

    def _get_avg_metric(
        self, cw_client, cluster_name: str, service_name: str,
        metric_name: str, days: int = 7,
    ) -> float:
        """Get average ECS service metric from CloudWatch."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        try:
            response = cw_client.get_metric_statistics(
                Namespace="AWS/ECS",
                MetricName=metric_name,
                Dimensions=[
                    {"Name": "ClusterName", "Value": cluster_name},
                    {"Name": "ServiceName", "Value": service_name},
                ],
                StartTime=start,
                EndTime=end,
                Period=86400,
                Statistics=["Average"],
            )
            datapoints = response.get("Datapoints", [])
            if not datapoints:
                return 0.0
            return sum(dp["Average"] for dp in datapoints) / len(datapoints)
        except Exception as exc:
            self.logger.debug(
                "CloudWatch %s for %s/%s failed: %s",
                metric_name, cluster_name, service_name, exc,
            )
            return 0.0

    @staticmethod
    def _is_non_prod(cluster_name: str, service_name: str, svc: dict) -> bool:
        """Heuristic to detect non-production services."""
        combined = f"{cluster_name} {service_name}".lower()
        if any(kw in combined for kw in _NON_PROD_KEYWORDS):
            return True
        # Check tags
        for tag in svc.get("tags", []):
            val = str(tag.get("value", "")).lower()
            key = str(tag.get("key", "")).lower()
            if key in ("environment", "env", "stage"):
                if any(kw in val for kw in _NON_PROD_KEYWORDS):
                    return True
        return False

    @staticmethod
    def _nearest_fargate_cpu(vcpu: float) -> float:
        """Round up to the nearest valid Fargate vCPU value."""
        valid = [0.25, 0.5, 1, 2, 4, 8, 16]
        for v in valid:
            if vcpu <= v:
                return v
        return 16

    @staticmethod
    def _nearest_fargate_memory(gb: float, vcpu: float) -> float:
        """Round up to nearest valid Fargate memory for the given vCPU."""
        # Fargate memory depends on CPU; these are approximate ranges
        mem_options = {
            0.25: [0.5, 1, 2],
            0.5: [1, 2, 3, 4],
            1: [2, 3, 4, 5, 6, 7, 8],
            2: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
            4: [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30],
            8: [16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60],
            16: [32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120],
        }
        options = mem_options.get(vcpu, [gb])
        for m in options:
            if gb <= m:
                return float(m)
        return float(options[-1]) if options else gb
