"""
Centralised service registry.

Maps internal service keys to display names, Cost Explorer service names,
analyser classes, and metadata.  This is the single source of truth for
service name matching across the entire tool.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Registry: one entry per analysable AWS service
# ---------------------------------------------------------------------------
SERVICE_REGISTRY: dict[str, dict[str, Any]] = {
    "ec2": {
        "display_name": "EC2",
        "ce_names": [
            "Amazon Elastic Compute Cloud - Compute",
            "Amazon EC2",
            "EC2 - Other",
            "Amazon Elastic Compute Cloud",
        ],
        "is_global": False,
    },
    "rds": {
        "display_name": "RDS",
        "ce_names": [
            "Amazon Relational Database Service",
            "Amazon RDS Service",
        ],
        "is_global": False,
    },
    "s3": {
        "display_name": "S3",
        "ce_names": [
            "Amazon Simple Storage Service",
            "Amazon S3",
        ],
        "is_global": True,
    },
    "lambda": {
        "display_name": "Lambda",
        "ce_names": [
            "AWS Lambda",
        ],
        "is_global": False,
    },
    "elb": {
        "display_name": "ELB",
        "ce_names": [
            "Amazon Elastic Load Balancing",
            "AWS Elastic Load Balancing",
        ],
        "is_global": False,
    },
    "cloudwatch": {
        "display_name": "CloudWatch",
        "ce_names": [
            "Amazon CloudWatch",
            "AmazonCloudWatch",
        ],
        "is_global": False,
    },
    "nat_gateway": {
        "display_name": "NAT Gateway",
        "ce_names": [
            "EC2 - Other",
            "Amazon EC2",
            "Amazon Virtual Private Cloud",
        ],
        "is_global": False,
    },
    "dynamodb": {
        "display_name": "DynamoDB",
        "ce_names": [
            "Amazon DynamoDB",
        ],
        "is_global": False,
    },
    "elasticache": {
        "display_name": "ElastiCache",
        "ce_names": [
            "Amazon ElastiCache",
        ],
        "is_global": False,
    },
    "ecs": {
        "display_name": "ECS/Fargate",
        "ce_names": [
            "Amazon Elastic Container Service",
            "Amazon ECS",
        ],
        "is_global": False,
    },
    "ecr": {
        "display_name": "ECR",
        "ce_names": [
            "Amazon EC2 Container Registry (ECR)",
            "Amazon ECR",
        ],
        "is_global": False,
    },
    "data_transfer": {
        "display_name": "Data Transfer",
        "ce_names": [
            "AWS Data Transfer",
        ],
        "is_global": True,  # CE-driven, no regional enumeration
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_display_name(service_key: str) -> str:
    """Return a human-friendly name for a service key."""
    entry = SERVICE_REGISTRY.get(service_key)
    return entry["display_name"] if entry else service_key


def is_global_service(service_key: str) -> bool:
    """Return True if the service should only be scanned once (not per-region)."""
    entry = SERVICE_REGISTRY.get(service_key)
    return entry.get("is_global", False) if entry else False


def ce_name_to_service_key(ce_name: str) -> str | None:
    """
    Map a Cost Explorer service name to an internal service key.
    Returns None if no match is found.
    """
    for key, entry in SERVICE_REGISTRY.items():
        if ce_name in entry["ce_names"]:
            return key
    # Fallback: case-insensitive partial match
    ce_lower = ce_name.lower()
    for key, entry in SERVICE_REGISTRY.items():
        for alias in entry["ce_names"]:
            if ce_lower in alias.lower() or alias.lower() in ce_lower:
                return key
    return None


def get_service_aliases() -> dict[str, list[str]]:
    """
    Return a mapping of display names to CE service names,
    compatible with the old _SERVICE_ALIASES dict in pdf_builder.py.
    """
    return {
        entry["display_name"]: entry["ce_names"]
        for entry in SERVICE_REGISTRY.values()
    }


def get_all_ce_names() -> set[str]:
    """Return the set of all known Cost Explorer service names."""
    names: set[str] = set()
    for entry in SERVICE_REGISTRY.values():
        names.update(entry["ce_names"])
    return names
