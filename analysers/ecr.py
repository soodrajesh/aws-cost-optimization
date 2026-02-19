"""
ECR (Elastic Container Registry) analyser.

Checks for:
- Repositories with no lifecycle policy      → old/untagged images accumulate at $0.10/GB/month
- Repositories with large image storage      → flag high-cost repos for cleanup
- Untagged images in repos without policy    → can be safely removed immediately

Trusted Advisor explicitly checks: "Amazon ECR Repository without lifecycle policy configured"
"""

from __future__ import annotations

import logging

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity

logger = logging.getLogger(__name__)

# ECR pricing: $0.10/GB/month for storage beyond the free tier
_ECR_PRICE_PER_GB = 0.10

# Only flag repos larger than this threshold to avoid noise
_MIN_SIZE_MB = 100


class ECRAnalyser(BaseAnalyser):
    SERVICE_NAME = "ECR"

    def _analyse_region(self, region: str) -> list[Finding]:
        findings: list[Finding] = []
        ecr = self._client("ecr", region)

        repositories = paginate(ecr, "describe_repositories", "repositories")
        if not repositories:
            return findings

        logger.info("Found %d ECR repositories in %s", len(repositories), region)

        for repo in repositories:
            repo_name = repo["repositoryName"]
            repo_arn = repo["repositoryArn"]

            # Check for lifecycle policy
            has_lifecycle = self._has_lifecycle_policy(ecr, repo_name)

            # Get repo size (sum all image sizes)
            images = safe_call(ecr.describe_images, repositoryName=repo_name) or {}
            image_list = images.get("imageDetails", [])

            total_size_bytes = sum(img.get("imageSizeInBytes", 0) for img in image_list)
            total_size_mb = total_size_bytes / (1024 ** 2)
            total_size_gb = total_size_bytes / (1024 ** 3)

            untagged_count = sum(1 for img in image_list if not img.get("imageTags"))

            if total_size_mb < _MIN_SIZE_MB:
                continue

            # 1. No lifecycle policy — images accumulate indefinitely
            if not has_lifecycle:
                monthly_cost = round(total_size_gb * _ECR_PRICE_PER_GB, 2)
                # Conservative saving estimate: a lifecycle policy typically reduces storage by 40-70%
                saving = round(monthly_cost * 0.50, 2)
                findings.append(Finding(
                    service="ECR",
                    region=region,
                    resource_id=repo_arn,
                    resource_name=repo_name,
                    issue=(
                        f"No lifecycle policy on ECR repo ({total_size_gb:.1f} GB, {len(image_list)} images) — "
                        f"old/untagged images accumulate at ${_ECR_PRICE_PER_GB}/GB/month. "
                        f"Add a policy to expire untagged images after 1 day and keep only last N tagged images."
                    ),
                    estimated_monthly_saving_usd=saving,
                    severity=Severity.MEDIUM,
                    finding_type="ecr_no_lifecycle",
                    details={
                        "image_count": len(image_list),
                        "untagged_image_count": untagged_count,
                        "total_size_gb": round(total_size_gb, 2),
                        "monthly_storage_cost": monthly_cost,
                    },
                ))

            # 2. Even with lifecycle policy, flag repos with many untagged images for immediate cleanup
            elif untagged_count >= 10:
                untagged_size_gb = sum(
                    img.get("imageSizeInBytes", 0)
                    for img in image_list
                    if not img.get("imageTags")
                ) / (1024 ** 3)
                saving = round(untagged_size_gb * _ECR_PRICE_PER_GB, 2)
                if saving > 0.10:
                    findings.append(Finding(
                        service="ECR",
                        region=region,
                        resource_id=repo_arn,
                        resource_name=repo_name,
                        issue=(
                            f"{untagged_count} untagged images in ECR repo "
                            f"({untagged_size_gb:.1f} GB) — run lifecycle policy or delete immediately"
                        ),
                        estimated_monthly_saving_usd=saving,
                        severity=Severity.LOW,
                        finding_type="ecr_untagged_images",
                        details={
                            "untagged_image_count": untagged_count,
                            "untagged_size_gb": round(untagged_size_gb, 2),
                            "total_image_count": len(image_list),
                        },
                    ))

        return findings

    def _has_lifecycle_policy(self, ecr, repo_name: str) -> bool:
        """Return True if the repository has a lifecycle policy configured."""
        try:
            response = safe_call(ecr.get_lifecycle_policy, repositoryName=repo_name)
            if response and response.get("lifecyclePolicyText"):
                return True
            return False
        except Exception:
            return False
