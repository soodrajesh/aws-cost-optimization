"""
Unit tests for models.py.
"""

from __future__ import annotations

import pytest

from models import Finding, Recommendation, ScanResult, Severity


class TestFinding:
    def test_severity_coercion_from_string(self):
        f = Finding(
            service="EC2", region="us-east-1", resource_id="i-001",
            resource_name="test", issue="idle", estimated_monthly_saving_usd=10.0,
            severity="HIGH",
        )
        assert f.severity == Severity.HIGH

    def test_severity_enum_passthrough(self):
        f = Finding(
            service="EC2", region="us-east-1", resource_id="i-001",
            resource_name="test", issue="idle", estimated_monthly_saving_usd=10.0,
            severity=Severity.MEDIUM,
        )
        assert f.severity == Severity.MEDIUM

    def test_default_finding_type(self):
        f = Finding(
            service="S3", region="us-east-1", resource_id="bucket",
            resource_name="bucket", issue="no lifecycle", estimated_monthly_saving_usd=5.0,
            severity=Severity.LOW,
        )
        assert f.finding_type == ""

    def test_details_defaults_to_empty_dict(self):
        f = Finding(
            service="IAM", region="global", resource_id="role-arn",
            resource_name="OldRole", issue="unused", estimated_monthly_saving_usd=0.0,
            severity=Severity.INFO,
        )
        assert f.details == {}


class TestRecommendation:
    def test_total_saving_auto_computed(self, sample_findings):
        ec2_findings = [f for f in sample_findings if f.service == "EC2"]
        rec = Recommendation(
            title="Test", description="desc", service="EC2",
            findings=ec2_findings,
        )
        expected = sum(f.estimated_monthly_saving_usd for f in ec2_findings)
        assert rec.total_saving == pytest.approx(expected)

    def test_annualized_saving_computed(self):
        rec = Recommendation(
            title="Test", description="desc", service="EC2",
            total_saving=100.0,
        )
        assert rec.annualized_saving == pytest.approx(1200.0)

    def test_roi_multiple_computed(self):
        rec = Recommendation(
            title="Test", description="desc", service="EC2",
            total_saving=100.0,
            estimated_hours=4.0,
        )
        # annualized = 1200, impl_cost = 4 * 150 = 600, roi = 2.0
        assert rec.roi_multiple == pytest.approx(2.0)

    def test_severity_coercion(self):
        rec = Recommendation(title="T", description="D", service="EC2", severity="LOW")
        assert rec.severity == Severity.LOW


class TestScanResult:
    def test_total_potential_saving(self, sample_scan_result):
        expected = sum(f.estimated_monthly_saving_usd for f in sample_scan_result.findings)
        assert sample_scan_result.total_potential_saving == pytest.approx(expected)

    def test_findings_by_severity(self, sample_scan_result):
        by_sev = sample_scan_result.findings_by_severity
        assert Severity.HIGH in by_sev
        assert Severity.MEDIUM in by_sev
        assert Severity.INFO in by_sev
        high_findings = by_sev[Severity.HIGH]
        assert all(f.severity == Severity.HIGH for f in high_findings)

    def test_findings_by_service(self, sample_scan_result):
        by_svc = sample_scan_result.findings_by_service
        assert "EC2" in by_svc
        assert "RDS" in by_svc
        assert "S3" in by_svc
        assert all(f.service == "EC2" for f in by_svc["EC2"])

    def test_empty_findings(self):
        result = ScanResult(
            account_id="123", profile="default", scan_date="2026-01-01",
            regions_scanned=["us-east-1"], months_of_history=6,
        )
        assert result.total_potential_saving == 0.0
        assert result.findings_by_service == {}
