"""
IAM analyser.

Checks for:
- IAM roles not used for > N days (or never used)
- IAM access keys older than N days (rotation hygiene + cost hygiene)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from analysers.base import BaseAnalyser
from aws_client import paginate, safe_call
from models import Finding, Severity
from utils.cost_estimator import estimate_iam_finding

logger = logging.getLogger(__name__)


def _days_since(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


class IAMAnalyser(BaseAnalyser):
    """
    IAM is a global service. Findings are reported with region='global'.
    """

    SERVICE_NAME = "IAM"

    def analyse(self, regions: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        try:
            findings.extend(self._check_roles())
            findings.extend(self._check_access_keys())
        except Exception as exc:
            logger.error("IAM analysis failed: %s", exc)
        return findings

    def _check_roles(self) -> list[Finding]:
        findings: list[Finding] = []
        iam = self._client("iam", "us-east-1")
        roles = paginate(iam, "list_roles", "Roles")

        for role in roles:
            role_name = role["RoleName"]
            role_arn = role["Arn"]

            # Skip AWS service-linked roles — they cannot be deleted
            if role_arn.startswith("arn:aws:iam::aws:") or ":aws-service-role/" in role_arn:
                continue

            role_detail = safe_call(iam.get_role, RoleName=role_name) or {}
            role_info = role_detail.get("Role", {})
            last_used_info = role_info.get("RoleLastUsed", {})
            last_used_date = last_used_info.get("LastUsedDate")

            if last_used_date is None:
                # Role has never been used
                create_date = role.get("CreateDate")
                days_old = _days_since(create_date) if create_date else 0

                if days_old >= self.config.iam_role_unused_days:
                    findings.append(Finding(
                        service="IAM",
                        region="global",
                        resource_id=role_arn,
                        resource_name=role_name,
                        issue=f"IAM role has never been used (created {int(days_old)} days ago)",
                        estimated_monthly_saving_usd=estimate_iam_finding(),
                        severity=Severity.INFO,
                        details={
                            "role_arn": role_arn,
                            "days_since_created": int(days_old),
                            "last_used": "never",
                        },
                    ))
            else:
                days_since_used = _days_since(last_used_date)
                if days_since_used >= self.config.iam_role_unused_days:
                    findings.append(Finding(
                        service="IAM",
                        region="global",
                        resource_id=role_arn,
                        resource_name=role_name,
                        issue=f"IAM role not used for {int(days_since_used)} days",
                        estimated_monthly_saving_usd=estimate_iam_finding(),
                        severity=Severity.INFO,
                        details={
                            "role_arn": role_arn,
                            "days_since_last_used": int(days_since_used),
                            "last_used_region": last_used_info.get("Region", "unknown"),
                        },
                    ))
        return findings

    def _check_access_keys(self) -> list[Finding]:
        findings: list[Finding] = []
        iam = self._client("iam", "us-east-1")
        users = paginate(iam, "list_users", "Users")

        for user in users:
            username = user["UserName"]
            keys = paginate(iam, "list_access_keys", "AccessKeyMetadata", UserName=username)

            for key in keys:
                key_id = key["AccessKeyId"]
                status = key.get("Status", "")
                create_date = key.get("CreateDate")
                if not create_date:
                    continue

                age_days = _days_since(create_date)

                if age_days >= self.config.iam_key_age_days:
                    # Check when the key was last used
                    last_used_info = safe_call(
                        iam.get_access_key_last_used,
                        AccessKeyId=key_id,
                    ) or {}
                    last_used = last_used_info.get("AccessKeyLastUsed", {})
                    last_used_date = last_used.get("LastUsedDate")
                    last_used_str = last_used_date.strftime("%Y-%m-%d") if last_used_date else "never"

                    severity = Severity.MEDIUM if status == "Active" else Severity.LOW
                    findings.append(Finding(
                        service="IAM",
                        region="global",
                        resource_id=key_id,
                        resource_name=f"{username}/{key_id}",
                        issue=(
                            f"Access key is {int(age_days)} days old "
                            f"(status: {status}, last used: {last_used_str})"
                        ),
                        estimated_monthly_saving_usd=estimate_iam_finding(),
                        severity=severity,
                        details={
                            "username": username,
                            "key_id": key_id,
                            "status": status,
                            "age_days": int(age_days),
                            "last_used": last_used_str,
                        },
                    ))
        return findings
