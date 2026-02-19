"""
Boto3 session factory and AWS utility helpers.
"""

from __future__ import annotations

import logging
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# Production-grade retry configuration: adaptive mode handles throttling
# automatically with exponential backoff and a token-bucket rate limiter.
_RETRY_CONFIG = BotoConfig(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=30,
)


def create_session(profile: str) -> boto3.Session:
    """Create a boto3 Session using the specified named profile."""
    try:
        session = boto3.Session(profile_name=profile)
        # Eagerly validate credentials exist
        session.get_credentials().get_frozen_credentials()
        return session
    except Exception as exc:
        raise RuntimeError(
            f"Failed to create AWS session for profile '{profile}'. "
            f"Ensure the profile exists in ~/.aws/credentials or ~/.aws/config. "
            f"Error: {exc}"
        ) from exc


def get_account_id(session: boto3.Session) -> str:
    """Return the AWS account ID for the current session."""
    sts = session.client("sts", config=_RETRY_CONFIG)
    identity = sts.get_caller_identity()
    return identity["Account"]


def get_enabled_regions(session: boto3.Session) -> list[str]:
    """
    Return all regions that are enabled for the account.
    Uses ec2.describe_regions() which only returns opted-in and default regions.
    """
    ec2 = session.client("ec2", region_name="us-east-1", config=_RETRY_CONFIG)
    try:
        response = ec2.describe_regions(Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}])
        regions = [r["RegionName"] for r in response["Regions"]]
        logger.info("Discovered %d enabled regions", len(regions))
        return sorted(regions)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("Could not discover regions automatically: %s. Falling back to us-east-1.", exc)
        return ["us-east-1"]


def get_client(session: boto3.Session, service: str, region: str):
    """Create a boto3 client with production retry configuration."""
    return session.client(service, region_name=region, config=_RETRY_CONFIG)


def get_resource(session: boto3.Session, service: str, region: str):
    """Create a boto3 resource with production retry configuration."""
    return session.resource(service, region_name=region, config=_RETRY_CONFIG)


def paginate(client, method: str, result_key: str, **kwargs) -> list:
    """
    Generic paginator helper. Iterates all pages of a paginated API call
    and returns a flat list of items from the specified result_key.
    """
    paginator = client.get_paginator(method)
    items = []
    for page in paginator.paginate(**kwargs):
        items.extend(page.get(result_key, []))
    return items


def safe_call(func, *args, default=None, log_errors: bool = True, **kwargs):
    """
    Call a boto3 API function, returning `default` on any AWS error.
    Useful for optional data fetches where a missing permission should not abort the scan.
    """
    try:
        return func(*args, **kwargs)
    except (BotoCoreError, ClientError) as exc:
        if log_errors:
            logger.debug("AWS API call failed (non-fatal): %s", exc)
        return default
