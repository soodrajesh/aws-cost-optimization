"""
Unit tests for utils/cost_estimator.py.

Tests that each estimate function returns a positive float and that
the fallback values are used when the pricing client is not initialised.
"""

from __future__ import annotations

import pytest

from utils.cost_estimator import (
    estimate_cloudwatch_alarm,
    estimate_ebs_volume,
    estimate_ec2_idle,
    estimate_eip,
    estimate_elb,
    estimate_iam_finding,
    estimate_lambda_idle,
    estimate_log_group,
    estimate_nat_gateway_idle,
    estimate_nat_gateway_data,
    estimate_rds_idle,
    estimate_rds_snapshot,
    estimate_s3_storage,
    estimate_snapshot,
)


class TestEC2Estimators:
    def test_known_instance_type(self):
        saving = estimate_ec2_idle("m5.large")
        assert saving > 0
        assert saving == pytest.approx(0.096 * 730, rel=0.05)

    def test_unknown_instance_type_uses_default(self):
        saving = estimate_ec2_idle("x9.superlarge")
        assert saving > 0

    def test_ebs_volume_gp2(self):
        saving = estimate_ebs_volume(100, "gp2")
        assert saving == pytest.approx(10.0, rel=0.05)

    def test_ebs_volume_gp3_cheaper(self):
        gp2 = estimate_ebs_volume(100, "gp2")
        gp3 = estimate_ebs_volume(100, "gp3")
        assert gp3 < gp2

    def test_snapshot_cost(self):
        saving = estimate_snapshot(200)
        assert saving == pytest.approx(10.0, rel=0.05)

    def test_eip_fixed_cost(self):
        saving = estimate_eip()
        assert saving > 3.0  # at least $3/month


class TestRDSEstimators:
    def test_known_db_class(self):
        saving = estimate_rds_idle("db.m5.large")
        assert saving > 0

    def test_unknown_db_class_uses_default(self):
        saving = estimate_rds_idle("db.x99.huge")
        assert saving > 0

    def test_rds_snapshot(self):
        saving = estimate_rds_snapshot(100)
        assert saving == pytest.approx(9.5, rel=0.05)


class TestS3Estimators:
    def test_s3_storage_positive(self):
        saving = estimate_s3_storage(1000)
        assert saving > 0

    def test_larger_bucket_higher_saving(self):
        small = estimate_s3_storage(100)
        large = estimate_s3_storage(10000)
        assert large > small


class TestOtherEstimators:
    def test_lambda_idle_returns_nominal(self):
        saving = estimate_lambda_idle(128)
        assert saving >= 0

    def test_elb_application(self):
        saving = estimate_elb("application")
        assert saving > 0

    def test_elb_classic_more_expensive(self):
        app = estimate_elb("application")
        classic = estimate_elb("classic")
        assert classic >= app

    def test_log_group_zero_gb(self):
        saving = estimate_log_group(0)
        assert saving == 0.0

    def test_log_group_positive(self):
        saving = estimate_log_group(100)
        assert saving > 0

    def test_cloudwatch_alarm(self):
        saving = estimate_cloudwatch_alarm()
        assert saving > 0

    def test_iam_finding_zero(self):
        saving = estimate_iam_finding()
        assert saving == 0.0

    def test_nat_gateway_idle_positive(self):
        saving = estimate_nat_gateway_idle()
        assert saving > 0

    def test_nat_gateway_data_with_traffic(self):
        no_traffic = estimate_nat_gateway_data(0.0)
        with_traffic = estimate_nat_gateway_data(100.0)
        assert with_traffic > no_traffic
