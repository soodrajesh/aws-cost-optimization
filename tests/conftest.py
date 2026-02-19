"""
Shared pytest fixtures for the AWS Cost Optimisation test suite.
"""

from __future__ import annotations

import sys
import os

# Ensure the project root is on the path so imports work without installation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from models import (
    CostForecast,
    CostTrend,
    Finding,
    Recommendation,
    ScanResult,
    Severity,
)
from config import Config
from utils.pricing import reset_pricing_client


@pytest.fixture(autouse=True)
def reset_pricing():
    """Reset the pricing singleton before each test to avoid cross-test pollution."""
    reset_pricing_client()
    yield
    reset_pricing_client()


@pytest.fixture
def sample_finding() -> Finding:
    return Finding(
        service="EC2",
        region="us-east-1",
        resource_id="i-0123456789abcdef0",
        resource_name="my-idle-server",
        issue="Idle instance (avg CPU 1.2% over 14 days)",
        estimated_monthly_saving_usd=70.08,
        severity=Severity.HIGH,
        finding_type="idle_instance",
        details={"instance_type": "m5.large", "avg_cpu_pct": 1.2},
    )


@pytest.fixture
def sample_findings() -> list[Finding]:
    return [
        Finding(
            service="EC2",
            region="us-east-1",
            resource_id="i-001",
            resource_name="idle-server-1",
            issue="Idle instance",
            estimated_monthly_saving_usd=70.08,
            severity=Severity.HIGH,
            finding_type="idle_instance",
        ),
        Finding(
            service="EC2",
            region="us-east-1",
            resource_id="vol-001",
            resource_name="orphan-volume",
            issue="Unattached EBS volume (100 GB, gp2)",
            estimated_monthly_saving_usd=10.00,
            severity=Severity.MEDIUM,
            finding_type="unattached_ebs",
        ),
        Finding(
            service="RDS",
            region="eu-west-1",
            resource_id="mydb",
            resource_name="mydb",
            issue="Idle RDS instance (0 connections over 7 days)",
            estimated_monthly_saving_usd=124.10,
            severity=Severity.HIGH,
            finding_type="idle_rds",
        ),
        Finding(
            service="S3",
            region="us-east-1",
            resource_id="my-big-bucket",
            resource_name="my-big-bucket",
            issue="Bucket (500 GB) has no lifecycle policy",
            estimated_monthly_saving_usd=3.45,
            severity=Severity.MEDIUM,
            finding_type="no_lifecycle",
        ),
        Finding(
            service="IAM",
            region="global",
            resource_id="arn:aws:iam::123456789012:role/OldRole",
            resource_name="OldRole",
            issue="IAM role not used for 120 days",
            estimated_monthly_saving_usd=0.0,
            severity=Severity.INFO,
            finding_type="unused_role",
        ),
    ]


@pytest.fixture
def sample_cost_trends() -> list[CostTrend]:
    return [
        CostTrend(
            service="Amazon Elastic Compute Cloud - Compute",
            monthly_costs={
                "2025-08": 800.0, "2025-09": 850.0, "2025-10": 900.0,
                "2025-11": 950.0, "2025-12": 1000.0, "2026-01": 1100.0,
            },
            total_spend=5600.0,
            trend_pct=37.5,
            anomaly=False,
            anomaly_months=[],
        ),
        CostTrend(
            service="Amazon Relational Database Service",
            monthly_costs={
                "2025-08": 300.0, "2025-09": 310.0, "2025-10": 295.0,
                "2025-11": 305.0, "2025-12": 300.0, "2026-01": 310.0,
            },
            total_spend=1820.0,
            trend_pct=3.3,
            anomaly=False,
            anomaly_months=[],
        ),
        CostTrend(
            service="Amazon Simple Storage Service",
            monthly_costs={
                "2025-08": 50.0, "2025-09": 55.0, "2025-10": 60.0,
                "2025-11": 70.0, "2025-12": 200.0, "2026-01": 75.0,
            },
            total_spend=510.0,
            trend_pct=50.0,
            anomaly=True,
            anomaly_months=["2025-12"],
        ),
    ]


@pytest.fixture
def sample_recommendations(sample_findings) -> list[Recommendation]:
    ec2_findings = [f for f in sample_findings if f.service == "EC2"]
    rds_findings = [f for f in sample_findings if f.service == "RDS"]
    return [
        Recommendation(
            title="Terminate or stop idle EC2 instances",
            description="2 EC2 findings identified.",
            service="EC2",
            findings=ec2_findings,
            severity=Severity.HIGH,
            category="quick_win",
            implementation_effort="low",
            estimated_hours=1.5,
            risk_level="low",
            risk_notes="",
            implementation_steps=[
                "Review instance metrics",
                "Terminate instance via CLI",
            ],
        ),
        Recommendation(
            title="Stop or delete idle RDS instances",
            description="1 RDS finding identified.",
            service="RDS",
            findings=rds_findings,
            severity=Severity.HIGH,
            category="strategic",
            implementation_effort="medium",
            estimated_hours=2.0,
            risk_level="medium",
            risk_notes="Verify no applications depend on the database.",
            implementation_steps=[
                "Confirm zero connections",
                "Take a final snapshot",
                "Stop or delete the instance",
            ],
        ),
    ]


@pytest.fixture
def sample_scan_result(sample_findings, sample_recommendations, sample_cost_trends) -> ScanResult:
    return ScanResult(
        account_id="123456789012",
        profile="test-profile",
        scan_date="2026-02-18",
        regions_scanned=["us-east-1", "eu-west-1"],
        months_of_history=6,
        findings=sample_findings,
        recommendations=sample_recommendations,
        cost_trends=sample_cost_trends,
        forecast=CostForecast(mean_usd=1500.0, lower_bound_usd=1400.0, upper_bound_usd=1600.0),
        top_billed_services=[
            {"service": "Amazon Elastic Compute Cloud - Compute", "monthly_avg": 933.0,
             "total_spend": 5600.0, "trend_pct": 37.5, "has_analyser": True, "service_key": "ec2"},
            {"service": "Amazon Kinesis", "monthly_avg": 250.0,
             "total_spend": 1500.0, "trend_pct": 15.0, "has_analyser": False, "service_key": None},
        ],
        uncovered_high_spend=[
            {"service": "Amazon Kinesis", "monthly_avg": 250.0,
             "total_spend": 1500.0, "trend_pct": 15.0, "has_analyser": False, "service_key": None},
        ],
    )


@pytest.fixture
def default_config() -> Config:
    return Config(profile="test-profile", months=6)
