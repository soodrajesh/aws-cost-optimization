"""
Cost estimation helpers.

These functions return approximate monthly USD savings for each type of finding.
When a PricingClient session is available, prices are fetched live from the
AWS Price List API.  Otherwise, hardcoded fallback prices are used.

All prices should be treated as indicative — actual savings depend on the
specific instance type, region, and usage patterns.
"""

from __future__ import annotations

import logging

import boto3

logger = logging.getLogger(__name__)

_HOURS_PER_MONTH = 730

# Lazy reference to the PricingClient (set once at scan start)
_pricing = None


def init_pricing(session: boto3.Session) -> None:
    """Initialise the module-level PricingClient singleton."""
    global _pricing
    from utils.pricing import get_pricing_client
    _pricing = get_pricing_client(session)


def _pc():
    """Return the PricingClient, or None if not initialised."""
    return _pricing


# ---------------------------------------------------------------------------
# EC2
# ---------------------------------------------------------------------------
def estimate_ec2_idle(instance_type: str, region: str = "us-east-1") -> float:
    """Estimated monthly saving from terminating/stopping an idle EC2 instance."""
    pc = _pc()
    if pc:
        hourly = pc.ec2_hourly(instance_type, region)
    else:
        from utils.pricing import _FALLBACK_EC2_HOURLY, _FALLBACK_EC2_DEFAULT
        hourly = _FALLBACK_EC2_HOURLY.get(instance_type, _FALLBACK_EC2_DEFAULT)
    return round(hourly * _HOURS_PER_MONTH, 2)


def estimate_ebs_volume(size_gb: float, volume_type: str = "gp2", region: str = "us-east-1") -> float:
    """Estimated monthly saving from deleting an unattached EBS volume."""
    pc = _pc()
    if pc:
        price = pc.ebs_per_gb(volume_type, region)
    else:
        from utils.pricing import _FALLBACK_EBS_PRICES
        price = _FALLBACK_EBS_PRICES.get(volume_type, 0.10)
    return round(size_gb * price, 2)


def estimate_snapshot(size_gb: float, region: str = "us-east-1") -> float:
    """Estimated monthly saving from deleting an old EBS snapshot."""
    pc = _pc()
    price = pc.snapshot_per_gb(region) if pc else 0.05
    return round(size_gb * price, 2)


def estimate_eip() -> float:
    """Estimated monthly saving from releasing an unused Elastic IP."""
    pc = _pc()
    return pc.eip_monthly() if pc else 3.65


# ---------------------------------------------------------------------------
# RDS
# ---------------------------------------------------------------------------
def estimate_rds_idle(db_class: str, region: str = "us-east-1") -> float:
    """Estimated monthly saving from stopping/deleting an idle RDS instance."""
    pc = _pc()
    if pc:
        hourly = pc.rds_hourly(db_class, region=region)
    else:
        from utils.pricing import _FALLBACK_RDS_HOURLY, _FALLBACK_RDS_DEFAULT
        hourly = _FALLBACK_RDS_HOURLY.get(db_class, _FALLBACK_RDS_DEFAULT)
    return round(hourly * _HOURS_PER_MONTH, 2)


def estimate_rds_snapshot(size_gb: float) -> float:
    """Estimated monthly saving from deleting an old RDS snapshot."""
    pc = _pc()
    price = pc.rds_snapshot_per_gb() if pc else 0.095
    return round(size_gb * price, 2)


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------
def estimate_s3_storage(size_gb: float) -> float:
    """Estimated monthly saving from adding a lifecycle policy (moving to IA)."""
    pc = _pc()
    if pc:
        std = pc.s3_standard_per_gb()
        ia = pc.s3_ia_per_gb()
    else:
        std = 0.023
        ia = 0.0125
    saving_per_gb = std - ia
    return round(size_gb * 0.30 * saving_per_gb, 2)  # 30% of data moves to IA


# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------
def estimate_lambda_idle(memory_mb: float) -> float:
    """Estimated monthly saving from removing an idle Lambda function.
    Returns a nominal $1 since idle functions have near-zero direct cost."""
    return 1.0


# ---------------------------------------------------------------------------
# ELB
# ---------------------------------------------------------------------------
def estimate_elb(lb_type: str = "application", region: str = "us-east-1") -> float:
    """Estimated monthly saving from deleting an unused load balancer."""
    pc = _pc()
    if pc:
        hourly = pc.elb_hourly(lb_type, region)
    else:
        from utils.pricing import _FALLBACK_ELB_HOURLY
        hourly = _FALLBACK_ELB_HOURLY.get(lb_type, 0.0225)
    return round(hourly * _HOURS_PER_MONTH, 2)


# ---------------------------------------------------------------------------
# CloudWatch
# ---------------------------------------------------------------------------
def estimate_log_group(stored_gb: float) -> float:
    """Estimated monthly saving from setting a retention policy on a log group."""
    pc = _pc()
    price = pc.cw_log_storage_per_gb() if pc else 0.03
    return round(stored_gb * price * 0.5, 2)  # 50% of data could be expired


def estimate_cloudwatch_alarm() -> float:
    """Estimated monthly saving from deleting an orphaned CloudWatch alarm."""
    pc = _pc()
    return pc.cw_alarm_monthly() if pc else 0.10


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
def estimate_iam_finding() -> float:
    """IAM findings are security/hygiene — no direct cost saving."""
    return 0.0


# ---------------------------------------------------------------------------
# NAT Gateway
# ---------------------------------------------------------------------------
def estimate_nat_gateway_idle(region: str = "us-east-1") -> float:
    """Estimated monthly saving from deleting an idle NAT Gateway (hourly charge)."""
    pc = _pc()
    if pc:
        hourly = pc.nat_gateway_hourly(region)
    else:
        hourly = 0.045
    return round(hourly * _HOURS_PER_MONTH, 2)


def estimate_nat_gateway_data(gb_per_month: float, region: str = "us-east-1") -> float:
    """Estimated monthly saving from reducing NAT Gateway data processing via VPC endpoints.
    Conservatively assume 40% of traffic could use VPC endpoints."""
    pc = _pc()
    if pc:
        per_gb = pc.nat_gateway_per_gb(region)
    else:
        per_gb = 0.045
    return round(gb_per_month * 0.40 * per_gb, 2)


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------
def estimate_dynamodb_idle(
    provisioned_rcu: float, provisioned_wcu: float, region: str = "us-east-1"
) -> float:
    """Estimated monthly saving from deleting/stopping an idle DynamoDB table."""
    # Provisioned pricing: $0.00065 per RCU/hr, $0.00065 per WCU/hr (us-east-1)
    rcu_monthly = provisioned_rcu * 0.00065 * _HOURS_PER_MONTH
    wcu_monthly = provisioned_wcu * 0.00065 * _HOURS_PER_MONTH
    return round(rcu_monthly + wcu_monthly, 2)


def estimate_dynamodb_overprovisioned(
    provisioned: float, consumed: float, is_write: bool = False, region: str = "us-east-1"
) -> float:
    """Estimated monthly saving from right-sizing a provisioned DynamoDB table.
    Suggests reducing to 2x consumed capacity (with headroom)."""
    if consumed <= 0:
        target = 5  # minimum 5 capacity units
    else:
        target = max(consumed * 2, 5)  # 2x headroom
    if target >= provisioned:
        return 0.0
    reduction = provisioned - target
    price_per_unit_hr = 0.00065
    return round(reduction * price_per_unit_hr * _HOURS_PER_MONTH, 2)


# ---------------------------------------------------------------------------
# ElastiCache
# ---------------------------------------------------------------------------
def estimate_elasticache_idle(node_type: str, num_nodes: int = 1, region: str = "us-east-1") -> float:
    """Estimated monthly saving from deleting an idle ElastiCache cluster."""
    pc = _pc()
    if pc:
        hourly = pc.elasticache_hourly(node_type, region)
    else:
        from utils.pricing import _FALLBACK_ELASTICACHE_HOURLY, _FALLBACK_ELASTICACHE_DEFAULT
        hourly = _FALLBACK_ELASTICACHE_HOURLY.get(node_type, _FALLBACK_ELASTICACHE_DEFAULT)
    return round(hourly * _HOURS_PER_MONTH * num_nodes, 2)


def estimate_elasticache_downsize(
    current_type: str, num_nodes: int = 1, region: str = "us-east-1"
) -> float:
    """Estimated monthly saving from downsizing ElastiCache nodes.
    Conservatively estimates 40% savings from downsizing one tier."""
    full_cost = estimate_elasticache_idle(current_type, num_nodes, region)
    return round(full_cost * 0.40, 2)


# ---------------------------------------------------------------------------
# ECS / Fargate
# ---------------------------------------------------------------------------
def estimate_fargate_idle(vcpu: float, memory_gb: float, region: str = "us-east-1") -> float:
    """Estimated monthly saving from removing an idle Fargate service."""
    pc = _pc()
    if pc:
        vcpu_hr = pc.fargate_vcpu_hr(region)
        gb_hr = pc.fargate_gb_hr(region)
    else:
        vcpu_hr = 0.04048
        gb_hr = 0.004445
    monthly = (vcpu * vcpu_hr + memory_gb * gb_hr) * _HOURS_PER_MONTH
    return round(monthly, 2)


def estimate_fargate_rightsize(
    current_vcpu: float, current_mem_gb: float,
    target_vcpu: float, target_mem_gb: float,
    region: str = "us-east-1",
) -> float:
    """Estimated monthly saving from right-sizing a Fargate task definition."""
    current = estimate_fargate_idle(current_vcpu, current_mem_gb, region)
    target = estimate_fargate_idle(target_vcpu, target_mem_gb, region)
    return round(max(current - target, 0), 2)


# ---------------------------------------------------------------------------
# Data Transfer
# ---------------------------------------------------------------------------
def estimate_data_transfer(monthly_gb: float, region: str = "us-east-1") -> float:
    """Estimated monthly cost of data transfer out to internet."""
    pc = _pc()
    per_gb = pc.data_transfer_per_gb(region) if pc else 0.09
    return round(monthly_gb * per_gb, 2)
