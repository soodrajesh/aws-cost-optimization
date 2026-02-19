"""
Configuration dataclass for the AWS Cost Optimisation tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# Single source of truth for --services CLI choices and analyser registration.
# IAM is excluded: not cost-related (no direct AWS charges).
SUPPORTED_SERVICES_LIST: list[str] = [
    "ec2", "rds", "s3", "lambda", "elb", "cloudwatch",
    "nat_gateway", "dynamodb", "elasticache", "ecs", "ecr", "data_transfer",
]


@dataclass
class Config:
    """Runtime configuration, populated from CLI arguments."""

    profile: Optional[str] = None
    """AWS named profile to use for authentication. None = default credential chain (env, instance profile, etc.)."""

    regions: Optional[list[str]] = None
    """Explicit list of regions to scan. None means auto-discover all enabled regions."""

    services: Optional[list[str]] = None
    """Explicit list of services to analyse. None means all supported services."""

    output_path: str = ""
    """Output path for the PDF report. Auto-generated if empty."""

    output_formats: list[str] = field(default_factory=lambda: ["pdf"])
    """Output formats to generate: pdf, json, or both."""

    min_saving: float = 0.0
    """Minimum estimated monthly saving (USD) for a finding to be included."""

    months: int = 6
    """Number of months of Cost Explorer history to retrieve."""

    max_workers: int = 10
    """Maximum number of threads for parallel region scanning."""

    # ----- EC2 thresholds -----
    ec2_cpu_threshold_pct: float = 5.0
    """EC2 instances with average CPU below this % are considered idle."""

    ec2_stopped_days: int = 7
    """EC2 instances stopped for more than this many days are flagged."""

    ec2_snapshot_age_days: int = 30
    """EC2 snapshots older than this many days are flagged."""

    # ----- RDS thresholds -----
    rds_connection_days: int = 7
    """RDS instances with zero connections for this many days are flagged."""

    rds_snapshot_age_days: int = 30
    """RDS snapshots older than this many days are flagged."""

    # ----- S3 thresholds -----
    s3_min_size_gb: float = 1.0
    """S3 buckets larger than this (GB) without a lifecycle policy are flagged."""

    # ----- Lambda thresholds -----
    lambda_idle_days: int = 30
    """Lambda functions with zero invocations for this many days are flagged."""

    lambda_memory_utilisation_pct: float = 20.0
    """Lambda functions using less than this % of provisioned memory are flagged."""

    # ----- ELB thresholds -----
    elb_min_requests_per_day: float = 10.0
    """ALBs averaging fewer requests/day than this are flagged."""

    # ----- CloudWatch thresholds -----
    cloudwatch_alarm_stale_days: int = 30
    """CloudWatch alarms in INSUFFICIENT_DATA for this many days are flagged."""

    # ----- NAT Gateway thresholds -----
    nat_gw_idle_gb_threshold: float = 1.0
    """NAT Gateways with less than this GB throughput in 14 days are idle."""

    nat_gw_high_traffic_gb_month: float = 100.0
    """NAT Gateways processing more than this GB/month trigger VPC endpoint recommendation."""

    # ----- DynamoDB thresholds -----
    dynamodb_utilisation_threshold_pct: float = 20.0
    """Provisioned DynamoDB tables using less than this % are over-provisioned."""

    dynamodb_idle_days: int = 14
    """DynamoDB tables with zero read/write units for this many days are idle."""

    # ----- ElastiCache thresholds -----
    elasticache_cpu_threshold_pct: float = 10.0
    """ElastiCache nodes with CPU below this % are candidates for downsizing."""

    elasticache_memory_threshold_pct: float = 30.0
    """ElastiCache nodes with memory usage below this % are candidates for downsizing."""

    elasticache_idle_days: int = 14
    """ElastiCache clusters with 0 connections for this many days are idle."""

    # ----- ECS/Fargate thresholds -----
    ecs_cpu_threshold_pct: float = 20.0
    """Fargate tasks with CPU below this % are over-provisioned."""

    ecs_memory_threshold_pct: float = 20.0
    """Fargate tasks with memory below this % are over-provisioned."""

    ecs_idle_days: int = 7
    """ECS services with 0 running tasks for this many days are idle."""

    # ----- Data Transfer thresholds -----
    data_transfer_min_monthly_usd: float = 100.0
    """Minimum monthly data transfer spend to flag for optimisation."""

    # Services supported by this tool (derived from SUPPORTED_SERVICES_LIST)
    SUPPORTED_SERVICES: list[str] = field(default_factory=lambda: list(SUPPORTED_SERVICES_LIST))

    def effective_services(self) -> list[str]:
        """Return the list of services to analyse, applying any --services filter."""
        if self.services:
            return [s.lower() for s in self.services if s.lower() in self.SUPPORTED_SERVICES]
        return list(self.SUPPORTED_SERVICES)

    def validate(self) -> list[str]:
        """Return a list of validation errors. Empty list means valid."""
        errors: list[str] = []
        if self.months < 1 or self.months > 12:
            errors.append("--months must be between 1 and 12")
        if self.min_saving < 0:
            errors.append("--min-saving must be non-negative")
        if self.max_workers < 1 or self.max_workers > 50:
            errors.append("--max-workers must be between 1 and 50")
        if self.regions:
            valid_pattern = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
            for r in self.regions:
                if not valid_pattern.match(r):
                    errors.append(f"Invalid region format: {r}")
        for fmt in self.output_formats:
            if fmt not in ("pdf", "json"):
                errors.append(f"Unsupported output format: {fmt}")
        return errors
