"""
ElastiCache analyser.

Checks for:
1. Idle clusters (zero connections over 14 days)
2. Over-sized nodes (low CPU and memory utilisation)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from analysers.base import BaseAnalyser
from models import Finding, Severity
from utils.cost_estimator import estimate_elasticache_idle, estimate_elasticache_downsize

logger = logging.getLogger(__name__)


class ElastiCacheAnalyser(BaseAnalyser):
    SERVICE_NAME = "ElastiCache"

    def _analyse_region(self, region: str) -> list[Finding]:
        elasticache = self._client("elasticache", region)
        cw = self._client("cloudwatch", region)
        findings: list[Finding] = []

        # Fetch all cache clusters
        try:
            clusters = []
            paginator = elasticache.get_paginator("describe_cache_clusters")
            for page in paginator.paginate(ShowCacheNodeInfo=True):
                clusters.extend(page.get("CacheClusters", []))
        except Exception as exc:
            self.logger.error("Failed to list ElastiCache clusters in %s: %s", region, exc)
            return []

        if not clusters:
            return []

        self.logger.info("Found %d ElastiCache clusters in %s", len(clusters), region)

        for cluster in clusters:
            cluster_id = cluster["CacheClusterId"]
            node_type = cluster.get("CacheNodeType", "unknown")
            engine = cluster.get("Engine", "redis")
            num_nodes = cluster.get("NumCacheNodes", 1)
            status = cluster.get("CacheClusterStatus", "")

            if status != "available":
                continue

            # Check 1: Idle cluster (zero connections)
            avg_connections = self._get_avg_metric(
                cw, cluster_id, "CurrConnections",
                days=self.config.elasticache_idle_days,
            )

            if avg_connections < 1.0:
                savings = estimate_elasticache_idle(node_type, num_nodes, region)
                findings.append(Finding(
                    service=self.SERVICE_NAME,
                    region=region,
                    resource_id=cluster_id,
                    resource_name=cluster_id,
                    issue=f"Idle cluster (avg {avg_connections:.1f} connections over {self.config.elasticache_idle_days} days)",
                    estimated_monthly_saving_usd=savings,
                    severity=Severity.HIGH,
                    finding_type="idle_elasticache",
                    details={
                        "node_type": node_type,
                        "engine": engine,
                        "num_nodes": num_nodes,
                        "avg_connections": round(avg_connections, 2),
                    },
                ))
                continue  # Skip over-sizing check for idle clusters

            # Check 2: Over-sized nodes (low CPU AND low memory)
            avg_cpu = self._get_avg_metric(
                cw, cluster_id, "EngineCPUUtilization",
                days=self.config.elasticache_idle_days,
            )
            avg_memory = self._get_avg_metric(
                cw, cluster_id, "DatabaseMemoryUsagePercentage",
                days=self.config.elasticache_idle_days,
            )

            cpu_threshold = self.config.elasticache_cpu_threshold_pct
            mem_threshold = self.config.elasticache_memory_threshold_pct

            if avg_cpu < cpu_threshold and avg_memory < mem_threshold and avg_cpu >= 0:
                savings = estimate_elasticache_downsize(node_type, num_nodes, region)
                if savings > 0:
                    findings.append(Finding(
                        service=self.SERVICE_NAME,
                        region=region,
                        resource_id=cluster_id,
                        resource_name=cluster_id,
                        issue=(
                            f"Over-sized (CPU: {avg_cpu:.1f}%, Memory: {avg_memory:.1f}%) "
                            f"— consider downsizing from {node_type}"
                        ),
                        estimated_monthly_saving_usd=savings,
                        severity=Severity.MEDIUM,
                        finding_type="oversized_elasticache",
                        details={
                            "node_type": node_type,
                            "engine": engine,
                            "num_nodes": num_nodes,
                            "avg_cpu_pct": round(avg_cpu, 1),
                            "avg_memory_pct": round(avg_memory, 1),
                        },
                    ))

        return findings

    def _get_avg_metric(
        self, cw_client, cluster_id: str, metric_name: str, days: int = 14
    ) -> float:
        """Get the average of an ElastiCache CloudWatch metric."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        try:
            response = cw_client.get_metric_statistics(
                Namespace="AWS/ElastiCache",
                MetricName=metric_name,
                Dimensions=[{"Name": "CacheClusterId", "Value": cluster_id}],
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
                "CloudWatch %s for %s failed: %s", metric_name, cluster_id, exc
            )
            return 0.0
