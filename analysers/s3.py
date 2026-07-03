"""
S3 analyser.

Checks for:
- Large buckets with no lifecycle policy      → add lifecycle rules (transition + expiry)
- Large buckets without Intelligent-Tiering   → enable S3 Intelligent-Tiering
- Versioning enabled but no expiry rule       → old non-current versions accumulate indefinitely
- No AbortIncompleteMultipartUpload rule      → partial uploads stored at full STANDARD price
"""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from analysers.base import BaseAnalyser
from aws_client import safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_s3_storage

logger = logging.getLogger(__name__)

_STANDARD_PRICE_PER_GB = 0.023

# S3 Intelligent-Tiering saves ~45% on infrequent-access tier data.
# Conservatively assume 30% of data becomes infrequent over time.
_IT_SAVING_PCT = 0.30 * 0.45


class S3Analyser(BaseAnalyser):
    SERVICE_NAME = "S3"

    def analyse(self, regions: list[str]) -> list[Finding]:
        """S3 is a global service; we scan once regardless of region list."""
        findings: list[Finding] = []
        try:
            findings.extend(self._analyse_buckets())
        except Exception as exc:
            logger.error("S3 analysis failed: %s", exc)
        return findings

    def _analyse_buckets(self) -> list[Finding]:
        findings: list[Finding] = []
        s3 = self._client("s3", "us-east-1")
        cw = self._client("cloudwatch", "us-east-1")

        response = safe_call(s3.list_buckets) or {}
        buckets = response.get("Buckets", [])

        for bucket in buckets:
            bucket_name = bucket["Name"]
            try:
                findings.extend(self._check_bucket(s3, cw, bucket_name))
            except Exception as exc:
                logger.debug("Skipping bucket %s: %s", bucket_name, exc)

        return findings

    def _check_bucket(self, s3, cw, bucket_name: str) -> list[Finding]:
        findings: list[Finding] = []

        location = safe_call(s3.get_bucket_location, Bucket=bucket_name) or {}
        region = location.get("LocationConstraint") or "us-east-1"

        size_bytes = self._get_bucket_size(cw, bucket_name, region)
        size_gb = (size_bytes or 0) / (1024 ** 3)

        if size_gb < self.config.s3_min_size_gb:
            return findings

        has_lifecycle = self._has_lifecycle_policy(s3, bucket_name)
        has_intelligent_tiering = self._has_intelligent_tiering(s3, bucket_name)
        versioning_enabled = self._versioning_enabled(s3, bucket_name)
        has_version_expiry = self._has_version_expiry_rule(s3, bucket_name)
        has_mpu_abort = self._has_mpu_abort_rule(s3, bucket_name)

        # 1. No lifecycle policy — highest priority S3 recommendation
        if not has_lifecycle:
            findings.append(Finding(
                service="S3",
                region=region,
                resource_id=bucket_name,
                resource_name=bucket_name,
                issue=(
                    f"No lifecycle policy on {size_gb:.1f} GB bucket — "
                    f"add rules to transition to S3-IA (30 days), "
                    f"Glacier (90 days), and expire old versions"
                ),
                estimated_monthly_saving_usd=estimate_s3_storage(size_gb),
                severity=Severity.MEDIUM,
                finding_type="s3_no_lifecycle",
                details={"size_gb": round(size_gb, 2), "has_lifecycle_policy": False},
            ))

        # 2. S3 Intelligent-Tiering for large buckets (≥ 128 GB) without IT
        if size_gb >= 128 and not has_intelligent_tiering:
            it_saving = round(size_gb * _STANDARD_PRICE_PER_GB * _IT_SAVING_PCT, 2)
            if it_saving > 1:
                findings.append(Finding(
                    service="S3",
                    region=region,
                    resource_id=bucket_name,
                    resource_name=bucket_name,
                    issue=(
                        f"S3 Intelligent-Tiering not enabled on {size_gb:.1f} GB bucket — "
                        f"IT automatically moves objects to cheaper tiers with no retrieval fees"
                    ),
                    estimated_monthly_saving_usd=it_saving,
                    severity=Severity.LOW,
                    finding_type="s3_intelligent_tiering",
                    details={
                        "size_gb": round(size_gb, 2),
                        "has_intelligent_tiering": False,
                        "estimated_it_saving_pct": round(_IT_SAVING_PCT * 100, 1),
                    },
                ))

        # 3. No AbortIncompleteMultipartUpload rule — incomplete parts stored at Standard price
        # Trusted Advisor explicitly flags this; MPU parts can silently accumulate for weeks/months
        if not has_mpu_abort and size_gb >= self.config.s3_min_size_gb:
            # Nominal $1/month flag — actual cost depends on how many failed uploads exist
            findings.append(Finding(
                service="S3",
                region=region,
                resource_id=bucket_name,
                resource_name=bucket_name,
                issue=(
                    "No AbortIncompleteMultipartUpload lifecycle rule — incomplete multipart upload "
                    "parts are stored at Standard pricing indefinitely until explicitly aborted"
                ),
                estimated_monthly_saving_usd=1.0,  # Conservative; actual savings depend on failed uploads
                severity=Severity.LOW,
                finding_type="s3_mpu_no_abort",
                details={
                    "size_gb": round(size_gb, 2),
                    "has_mpu_abort_rule": False,
                    "action": "Add AbortIncompleteMultipartUpload rule with DaysAfterInitiation=7",
                },
            ))

        # 4. Versioning enabled but no expiry rule for non-current versions
        if versioning_enabled and not has_version_expiry:
            version_cost = round(size_gb * _STANDARD_PRICE_PER_GB * 0.25, 2)  # ~25% of bucket cost
            if version_cost > 1:
                findings.append(Finding(
                    service="S3",
                    region=region,
                    resource_id=bucket_name,
                    resource_name=bucket_name,
                    issue=(
                        f"Versioning enabled on {size_gb:.1f} GB bucket but no expiry rule — "
                        f"non-current versions accumulate indefinitely (typically 20-50% of storage cost)"
                    ),
                    estimated_monthly_saving_usd=version_cost,
                    severity=Severity.MEDIUM,
                    finding_type="s3_version_expiry",
                    details={
                        "size_gb": round(size_gb, 2),
                        "versioning": True,
                        "has_noncurrent_expiry": False,
                    },
                ))

        return findings

    def _get_bucket_size(self, cw, bucket_name: str, region: str) -> float | None:
        from datetime import datetime, timedelta, timezone
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=2)
        response = safe_call(
            cw.get_metric_statistics,
            Namespace="AWS/S3",
            MetricName="BucketSizeBytes",
            Dimensions=[
                {"Name": "BucketName", "Value": bucket_name},
                {"Name": "StorageType", "Value": "StandardStorage"},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Average"],
        )
        if not response:
            return None
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        return max(dp["Average"] for dp in datapoints)

    def _has_lifecycle_policy(self, s3, bucket_name: str) -> bool:
        try:
            response = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            return len(response.get("Rules", [])) > 0
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
                return False
            logger.debug("Could not check lifecycle for %s: %s", bucket_name, exc)
            return True  # assume policy exists to avoid false positives

    def _has_intelligent_tiering(self, s3, bucket_name: str) -> bool:
        """Return True if any lifecycle rule transitions objects to INTELLIGENT_TIERING."""
        try:
            response = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            for rule in response.get("Rules", []):
                if rule.get("Status") != "Enabled":
                    continue
                for transition in rule.get("Transitions", []):
                    if transition.get("StorageClass") == "INTELLIGENT_TIERING":
                        return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("NoSuchLifecycleConfiguration", "NoSuchBucket"):
                logger.debug("Could not check IT config for %s: %s", bucket_name, exc)
        return False

    def _versioning_enabled(self, s3, bucket_name: str) -> bool:
        try:
            response = safe_call(s3.get_bucket_versioning, Bucket=bucket_name) or {}
            return response.get("Status") == "Enabled"
        except Exception:
            return False

    def _has_mpu_abort_rule(self, s3, bucket_name: str) -> bool:
        """Return True if the bucket has an AbortIncompleteMultipartUpload lifecycle rule."""
        try:
            response = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            for rule in response.get("Rules", []):
                if rule.get("Status") != "Enabled":
                    continue
                if rule.get("AbortIncompleteMultipartUpload"):
                    return True
            return False
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
                return False
            return True  # assume rule exists to avoid false positives on access errors

    def _has_version_expiry_rule(self, s3, bucket_name: str) -> bool:
        """Return True if any lifecycle rule expires or transitions non-current versions."""
        try:
            response = s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            for rule in response.get("Rules", []):
                if rule.get("Status") != "Enabled":
                    continue
                if rule.get("NoncurrentVersionExpiration"):
                    return True
                if rule.get("NoncurrentVersionTransitions"):
                    return True
            return False
        except ClientError:
            return False
